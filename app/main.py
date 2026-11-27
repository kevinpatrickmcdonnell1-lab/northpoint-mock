"""FastAPI + Jinja2 + HTMX UI (PLAN §8). Plain server-rendered pages, one CSS file."""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import date
from pathlib import Path
from typing import Optional
from urllib.parse import quote, quote_plus, urlencode

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from app import fact_review as fr
from app import handoffs, metrics, presenter
from app.engine import evaluate
from app.metrics import business_days
from app.pdfs import PAGES, TITLES
from app.rules.common import why_unused
from app.service import ALLOWED_ROLES, DEFAULT_DB, DEFAULT_OUTBOX, Service
from app.policy import load_config

HERE = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("NORTHPOINT_DB", DEFAULT_DB))

# Fail fast (Fix 5): a bad policy/workflow YAML raises ConfigError when the app is imported,
# i.e. at `uvicorn app.main:app` startup, not on the first request.
load_config()

app = FastAPI(title="Northpoint stress-echo readiness mock (SYNTHETIC)")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")

# Display words (brief R1). Internal codes stay S / NM / I / NA.
STATUS_WORDS = {"S": "Supported", "NM": "Not met", "I": "Insufficient or conflicting", "NA": "Not applicable"}
OWNER_TABS = [("all", "All", None), ("triage", "Clinical Triage", "Clinical Triage RN"),
              ("review", "Clinical Review", "Clinical Review RN"), ("intake", "Intake", "Intake Coordinator"),
              ("auth", "Auth", "Auth Specialist"), ("ready", "Ready for scheduling", None),
              ("facts", "Fact review", None)]


def page_url(file: str, page: int, case: Optional[str] = None, q: Optional[str] = None,
             field: Optional[str] = None) -> str:
    """Page-viewer link. Carries the case key so the back link never guesses it (Fix 9)."""
    params = {k: v for k, v in (("case", case), ("q", q), ("field", None if q else field)) if v}
    return f"/page/{quote(file)}/{page}" + (f"?{urlencode(params)}" if params else "")


templates.env.globals.update(STATUS_WORDS=STATUS_WORDS, TITLES=TITLES, quote_plus=quote_plus,
                             page_url=page_url, LAYERS=presenter.LAYERS, PILOT=presenter.PILOT)

CONCURRENT_WRITE = ("Not recorded: another write landed at the same moment, so nothing was saved. "
                    "Press the button once more.")


def svc() -> Service:
    if not hasattr(app.state, "svc"):
        app.state.svc = Service(DB_PATH)
        app.state.svc.seed(evaluate=False)   # a fresh DB starts as "packets arrived, engine not run"
    return app.state.svc


def go(url: str, msg: Optional[str] = None, at: Optional[str] = None) -> RedirectResponse:
    """303 back to a page with a flash message; `at` names the section to scroll to."""
    for k, v in (("msg", msg), ("at", at)):
        if v:
            url += ("&" if "?" in url else "?") + f"{k}=" + quote_plus(v)
    return RedirectResponse(url, status_code=303)


def corpus_stats(service: Service) -> dict:
    """Layer-1 numbers across every loaded packet (pages → typed documents → cited quotes)."""
    efs = [service.store.case(k) for k in service.store.keys()]
    return {
        "packets": len(efs),
        "pages": sum(len({p for d in ef.documents for p in d.pages}) for ef in efs),
        "documents": sum(len(ef.documents) for ef in efs),
        "quotes": sum(len(d.quotes) for ef in efs for d in ef.documents),
        "corrections": sum(len(ef.corrections) for ef in efs),
        "reviews": len(service.store.reviews()),
        "runtime_corrections": sum(r.action == "correct" for r in service.store.reviews()),
        "methods": sorted({ef.extraction_method for ef in efs}),
    }


def _short(v) -> str:
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, list):
        return ", ".join(map(str, v)) or "none"
    return str(v)


def format_diff(diff: dict) -> list[dict]:
    """Readable rows for an evaluation diff (what a simulated event changed)."""
    labels = {"identity": "Identity", "coverage": "Coverage", "priority": "Priority", "plan": "Plan",
              "pa_requirement": "PA requirement", "failing_gates": "Holds", "actions": "Next actions",
              "pa_prep_ready": "PA prep ready", "scheduling_sendable": "Scheduling sendable",
              "admin_ready": "Admin readiness"}
    rows = []
    for k, ch in diff.items():
        if k in ("simulated_event_ids", "initial"):
            continue
        if k == "criteria":
            for rule, to in (ch["to"] or {}).items():
                frm = (ch["from"] or {}).get(rule)
                if frm != to:
                    rows.append({"label": rule, "from": frm, "to": to})
            continue
        rows.append({"label": labels.get(k, k), "from": _short(ch["from"]), "to": _short(ch["to"])})
    return rows


def last_change(service: Service, key: str) -> Optional[dict]:
    for a in service.store.audit_rows(key, "evaluation"):
        d = a["detail"].get("diff") or {}
        if d and not d.get("initial"):
            return {"at": a["at"], "actor": a["actor"], "rows": format_diff(d),
                    "policy_version": a["detail"].get("policy_version")}
    return None


def patient_name(ef) -> str:
    reg = next((d for d in ef.documents if d.doc_type == "registration_face_sheet"), None)
    src = reg or next(d for d in ef.documents if d.identifiers.name)
    return src.identifiers.name or ef.patient_key


def procedure(ef) -> str:
    o = next((d for d in ef.documents if d.doc_type == "stress_echo_order" and d.signed), None)
    if o:
        return o.fields.procedure or "stress echo"
    if any(d.doc_type == "referral" and d.fields.service_requested == "consult" for d in ef.documents):
        return "— no order (consult referral; out of scope)"
    return "— no signed order"


def row(s, service: Service) -> dict:
    ef = service.case(s.patient_key)
    sent = handoffs.active_scheduling_handoff(service, s.patient_key) is not None
    return {
        "s": s, "name": patient_name(ef), "packet": ef.packet_id, "procedure": procedure(ef),
        "plan": s.plan.detail.get("product") or "unresolved",
        "holds": [g.gate for g in s.gates if not g.passed],
        "next": s.actions[0] if s.actions else None,
        "age": business_days(ef.received_at, service.policy.eval_date), "sent": sent,
        "site": ef.ordering_site or "(unknown)",
        "pages": len({p for d in ef.documents for p in d.pages}), "docs": len(ef.documents),
        "flags": service.open_flags(s.patient_key),
    }


# ---------------------------------------------------------------- queue

@app.get("/")
def queue(request: Request, owner: str = "all", site: str = "all", msg: Optional[str] = None):
    service = svc()
    rows = [row(s, service) for s in service.states()]
    sites = sorted({r["site"] for r in rows})
    if site != "all":
        rows = [r for r in rows if r["site"] == site]
    m = metrics.compute(service)
    kpis = {"sendable": m["counts"].get("sendable to scheduling", 0),
            "pa_ready": m["counts"].get("ready for PA prep / awaiting payer", 0),
            "held": m["counts"].get("held", 0), "handed": m["counts"].get("handed to scheduling", 0),
            "gate_leaks": m["gate_leaks"], "owners": len(m["owners"]),
            "actions": sum(v for _, v in m["owners"]), "n": m["n"],
            "holds": sum(len([g for g in r["s"].gates if not g.passed]) for r in rows),
            "clinical": sum(1 for r in rows if any(a.reason_code.startswith("CLIN_") for a in r["s"].actions))}
    corpus = corpus_stats(service)
    tab_counts = {k: (sum(r["s"].scheduling_sendable for r in rows) if k == "ready" else
                      sum(bool(r["flags"]) for r in rows) if k == "facts" else
                      sum(any(a.owner_role == role_ for a in r["s"].actions) for r in rows) if role_ else len(rows))
                  for k, _, role_ in OWNER_TABS}
    role = dict((k, r) for k, _, r in OWNER_TABS).get(owner)
    if owner == "ready":
        rows = [r for r in rows if r["s"].scheduling_sendable]
    elif owner == "facts":
        rows = [r for r in rows if r["flags"]]
    elif role:
        rows = [r for r in rows if any(a.owner_role == role for a in r["s"].actions)]
    return templates.TemplateResponse(request, "queue.html", {
        "rows": rows, "owner": owner, "tabs": OWNER_TABS, "site": site, "sites": sites, "policy": service.policy, "msg": msg,
        "workflow": service.workflow, "kpis": kpis, "corpus": corpus, "tab_counts": tab_counts,
        "engine_run": service.engine_has_run(),
        "arrivals": [{"key": k, "ef": service.store.case(k), "name": patient_name(service.store.case(k))}
                     for k in service.store.keys()]})


@app.post("/rerun")
def rerun(back: str = Form("/")):
    with svc().lock:
        states = svc().evaluate_all(actor="UI: re-run all through engine")
    back = back if back.startswith("/case/") else "/"
    return go(back, f"Re-evaluated all {len(states)} cases through one engine; audit entries written.")


@app.post("/reset")
def reset():
    """Back to the demo's starting point: the packets have arrived (extracted facts loaded),
    nothing has been simulated, sent or audited, and the engine has not run yet."""
    old = svc()
    with old.lock:
        old.store.close()
        for p in (DB_PATH, Path(str(DB_PATH) + "-wal"), Path(str(DB_PATH) + "-shm")):
            p.unlink(missing_ok=True)
        for f in old.outbox_dir.glob("*.json"):
            f.unlink()
        new = Service(DB_PATH, outbox_dir=old.outbox_dir, extractions_dir=old.extractions_dir)
        new.seed(evaluate=False)
        app.state.svc = new
    return go("/", "Demo reset: packets reloaded; simulated events, handoffs and audit cleared. "
                   "The engine has not run yet.")


# ---------------------------------------------------------------- case detail

@app.get("/case/{key}")
def case(request: Request, key: str, msg: Optional[str] = None, form: Optional[str] = None,
         at: Optional[str] = None):
    service = svc()
    try:
        ef = service.case(key)
    except KeyError:
        raise HTTPException(404)
    events = service.store.events(key)
    s = evaluate(ef, events, service.policy, service.workflow)
    docs = [{"d": d, "unused": why_unused(d, service.policy)} for d in ef.documents]
    qmap: dict = {}
    for d in ef.documents:
        for qt in d.quotes:
            qmap.setdefault(f"{d.doc_id}|{qt.field}", qt.text)
    clin = [a for a in s.actions if a.reason_code.startswith("CLIN_")]
    admin = [a for a in s.actions if not a.reason_code.startswith("CLIN_")]
    return templates.TemplateResponse(request, "case.html", {
        "s": s, "ef": ef, "name": patient_name(ef), "procedure": procedure(ef), "docs": docs,
        "events": events, "audit": service.store.audit_rows(key)[:60],
        "outbox": service.store.outbox(key), "roles": ALLOWED_ROLES, "msg": msg,
        "recalls": [o for o in service.store.outbox(key) if o["work_item"]["type"] == handoffs.RECALL_TYPE],
        "active_sched": handoffs.active_scheduling_handoff(service, key),
        "policy": service.policy, "products": [p.product for p in service.policy.plans.values()],
        "age": business_days(ef.received_at, service.policy.eval_date),
        "config_sha256": service.config_sha256, "form": form, "at": at, "qmap": qmap,
        "pages_total": len({p for d in ef.documents for p in d.pages}),
        "quotes_total": sum(len(d.quotes) for d in ef.documents),
        "clin_actions": clin, "admin_actions": admin, "change": last_change(service, key),
        "gate_map": {g.gate: g for g in s.gates},
        "flags": service.open_flags(key), "reviews": service.store.reviews(key),
        "engine_run": service.engine_has_run()})


@app.post("/case/{key}/simulate")
async def simulate(key: str, request: Request):
    form = dict((await request.form()).items())
    with svc().lock:
        return _simulate(key, form)


def _simulate(key: str, form: dict):
    t = form.get("type", "")
    recalls_before = {o["handoff_id"] for o in svc().store.outbox(key)
                      if o["work_item"]["type"] == handoffs.RECALL_TYPE}
    kw: dict = {"actor_role": form.get("actor_role", ""), "actor_name": form.get("actor_name"),
                "note": form.get("note")}
    try:
        if t == "IDENTITY_RESOLUTION":
            confirmed = {f: form[f"confirmed_{f}"].strip() for f in ("name", "dob", "member_id")
                         if form.get(f"confirmed_{f}", "").strip()}
            ev = svc().simulate(key, t, "confirmed", confirmed=confirmed,
                                epic_reference=form.get("epic_reference"), **kw)
        elif t == "BENEFITS_RESPONSE":
            eval_date = svc().policy.eval_date
            try:
                rd = date.fromisoformat((form.get("response_date") or "").strip())
            except ValueError:
                rd = None
            if rd is not None and rd > eval_date:
                return go(f"/case/{key}?form=benefits", f"Not recorded: response date {rd} is after the "
                          f"evaluation date {eval_date}, so the engine would ignore it (A8). "
                          f"Use a date on or before {eval_date}.", at="sim-benefits")
            b = {"response_date": form.get("response_date"), "payer": form.get("payer") or None,
                 "plan_product_raw": form.get("plan_product_raw") or None,
                 "member_id": form.get("member_id") or None,
                 "coverage_active_on_eval_date": {"true": True, "false": False}.get(form.get("active", "")),
                 "product_end_date": form.get("product_end_date") or None}
            ev = svc().simulate(key, t, "received", benefits=b, **kw)
        else:
            ev = svc().simulate(key, t, form.get("decision", ""), **kw)
    except (ValidationError, ValueError) as e:
        msg = e.errors()[0]["msg"] if isinstance(e, ValidationError) else str(e)
        return go(f"/case/{key}", f"Not recorded: {msg}", at="simulate")
    except sqlite3.IntegrityError:
        return go(f"/case/{key}", CONCURRENT_WRITE, at="simulate")
    new_recalls = [o["handoff_id"] for o in svc().store.outbox(key)
                   if o["work_item"]["type"] == handoffs.RECALL_TYPE and o["handoff_id"] not in recalls_before]
    recalled = (f" Case is no longer sendable: scheduling handoff recalled automatically "
                f"({', '.join(new_recalls)}).") if new_recalls else ""
    return go(f"/case/{key}", f"SIMULATED event {ev.event_id} recorded ({ev.type} {ev.decision}); "
                              f"case re-evaluated and audited.{recalled}", at="changed")


@app.post("/case/{key}/handoff/{kind}")
def handoff(key: str, kind: str):
    if kind not in handoffs.KINDS:
        raise HTTPException(404)
    try:
        with svc().lock:
            r = handoffs.send(svc(), key, kind)
    except sqlite3.IntegrityError:
        return go(f"/case/{key}", CONCURRENT_WRITE, at="handoffs")
    label = handoffs.KINDS[kind][0]
    msgs = {
        "sent": f"Sent {label}: {r.get('handoff_id')} recorded in outbox (MOCK STUB); "
                f"delivery {r.get('delivery')}.",
        "already_recorded": f"Already recorded as {r.get('handoff_id')} (idempotency key unchanged).",
        "already_sent": f"Already sent to scheduling as {r.get('handoff_id')}; nothing recorded. "
                        "A re-send is allowed only after a scheduling recall.",
        "blocked": f"BLOCKED {label}: failing gates {', '.join(r.get('failing_gates', []))}. "
                   "Attempt recorded in the audit trail.",
        "not_applicable": f"Not applicable: {r.get('reason')}",
    }
    return go(f"/case/{key}", msgs[r["result"]], at="handoffs")


# ---------------------------------------------------------------- pages, outbox, metrics

def case_for_page(service: Service, file: str, page: int, case: Optional[str]) -> Optional[str]:
    """The case a page belongs to: the ?case= key if it cites this file, else the extraction
    whose documents cite this file and page (combined PDFs included). Never parsed from the name."""
    keys = service.store.keys()
    if case in keys and service.store.case(case).source_file == file:
        return case
    for k in keys:
        ef = service.store.case(k)
        if ef.source_file == file and any(page in d.pages for d in ef.documents):
            return k
    return None


@app.get("/page/{file}/{page}")
def page_viewer(request: Request, file: str, page: int, q: Optional[str] = None,
                field: Optional[str] = None, case: Optional[str] = None, msg: Optional[str] = None):
    txt = PAGES / file / f"{page}.txt"
    if "/" in file or ".." in file or not txt.exists():
        raise HTTPException(404, "Page not ingested. Run `make ingest`.")
    text = txt.read_text()
    parts = None
    if q:
        norm = " ".join(text.split())
        i = norm.find(" ".join(q.split()))
        if i >= 0:
            j = i + len(" ".join(q.split()))
            parts = (norm[:i], norm[i:j], norm[j:])
    key = case_for_page(svc(), file, page, case)
    here = []
    if key:
        ef = svc().case(key)
        reviews = svc().store.reviews(key)
        flags = svc().flags(key)
        open_ = svc().open_flags(key)
        for d in ef.documents:
            if page in d.pages:
                here.append({"d": d, "unused": why_unused(d, svc().policy),
                             "quotes": [x for x in d.quotes if x.page == page],
                             "rows": review_rows(d, page, flags, open_, reviews)})
    norm_q = " ".join(q.split()) if q else None
    return templates.TemplateResponse(request, "page.html", {
        "file": file, "page": page, "q": q, "text": text, "parts": parts, "key": key, "field": field,
        "here": here, "norm_q": norm_q, "msg": msg, "review_roles": fr.REVIEW_ROLES,
        "clinical_roles": fr.CLINICAL_ROLES})


def _form_value(v) -> str:
    """A fact value as the correction box shows it (and parse_value reads it back)."""
    if v is None:
        return ""
    if isinstance(v, (bool, list, dict)):
        return json.dumps(v)
    return str(v)


def review_rows(d, page: int, flags, open_, reviews) -> list[dict]:
    """One row per fact a reviewer can check on this page: every field quoted here, plus any
    flagged field of this document, plus its date and signer."""
    order = [x.field for x in d.quotes if x.page == page] + [f.field for f in flags if f.doc_id == d.doc_id]
    order += [f for f in ("date", "signer") if f not in order and fr.current_value(d, f) is not None]
    rows = []
    for f in dict.fromkeys(order):
        correctable = f in fr.correctable_fields(d.doc_type)
        rows.append({
            "field": f, "quotes": [x for x in d.quotes if x.field == f],
            "value": fr.current_value(d, f) if correctable else None,
            "form_value": _form_value(fr.current_value(d, f)) if correctable else "",
            "correctable": correctable, "identifier": f in fr.IDENTIFIER_FIELDS,
            "clinical": fr.is_clinical(f),
            "flags": [x for x in open_ if x.doc_id == d.doc_id and x.field == f],
            "last": fr.latest_review(reviews, d.doc_id, f),
        })
    return rows


@app.post("/case/{key}/fact-review")
async def fact_review(key: str, request: Request):
    form = dict((await request.form()).items())
    back_file, back_page = form.get("back_file", ""), form.get("back_page", "")
    try:
        page = int(form.get("quote_page") or back_page or 0)
    except ValueError:
        page = 0
    with svc().lock:
        try:
            ef = svc().store.case(key)
        except KeyError:
            raise HTTPException(404)
        back = page_url(back_file or ef.source_file, int(back_page or page or 1), key)
        recalls_before = {o["handoff_id"] for o in svc().store.outbox(key)
                          if o["work_item"]["type"] == handoffs.RECALL_TYPE}
        try:
            r = svc().review_fact(
                key, form.get("doc_id", ""), form.get("field", ""), form.get("action", ""),
                actor_role=form.get("actor_role", ""), actor_name=form.get("actor_name", ""),
                reason=form.get("reason"), value=form.get("value"),
                quote_text=form.get("quote_text"), quote_page=page or None)
        except ValueError as e:
            return go(back, f"Not recorded: {e}")
        except sqlite3.IntegrityError:
            return go(back, CONCURRENT_WRITE)
        new_recalls = [o["handoff_id"] for o in svc().store.outbox(key)
                       if o["work_item"]["type"] == handoffs.RECALL_TYPE and o["handoff_id"] not in recalls_before]
    if r.action == "confirm":
        return go(back, f"{r.review_id}: {r.field} on {r.doc_id} confirmed by {r.actor_name} "
                        f"({r.actor_role}). No fact changed.")
    recalled = (f" Case is no longer sendable: scheduling handoff recalled automatically "
                f"({', '.join(new_recalls)}).") if new_recalls else ""
    return go(back, f"{r.review_id}: {r.field} on {r.doc_id} corrected to {_short(r.value)} by "
                    f"{r.actor_name} ({r.actor_role}). The source extraction is unchanged; the case "
                    f"was re-evaluated on the corrected facts and audited.{recalled}")


@app.get("/pages/{file}/{page}.png")
def page_png(file: str, page: int):
    p = PAGES / file / f"{page}.png"
    if "/" in file or ".." in file or not p.exists():
        raise HTTPException(404)
    return FileResponse(p)


@app.get("/outbox")
def outbox(request: Request, msg: Optional[str] = None):
    service = svc()
    items = [{**o, "delivery": handoffs.delivery_status(service, o["handoff_id"])}
             for o in service.store.outbox()]
    return templates.TemplateResponse(request, "outbox.html", {
        "items": items, "msg": msg, "receivers": handoffs.RECEIVERS,
        "outages": service.store.outages(), "max_attempts": service.workflow.delivery_max_attempts})


@app.post("/outbox/outage")
def outage(receiving_system: str = Form(...), down: str = Form(...)):
    try:
        handoffs.set_outage(svc(), receiving_system, down == "true")
    except ValueError:
        raise HTTPException(400)
    state = "DOWN" if down == "true" else "back up"
    return go("/outbox", f"SIMULATED: {receiving_system} is {state}. Audited.")


@app.post("/outbox/retry")
def retry_all():
    r = handoffs.retry_pending(svc())
    return go("/outbox", f"Retry sweep: {len(r)} handoff(s) attempted.")


@app.post("/outbox/{handoff_id}/retry")
def retry_one(handoff_id: str):
    r = handoffs.deliver(svc(), handoff_id)
    return go("/outbox", f"{handoff_id}: {r['status']} after {r['attempts']} attempt(s).")


@app.post("/outbox/{handoff_id}/requeue")
def requeue_one(handoff_id: str):
    r = handoffs.requeue(svc(), handoff_id)
    return go("/outbox", f"{handoff_id} requeued from dead letter: now {r['status']}.")


@app.get("/outbox/{handoff_id}")
def outbox_item(handoff_id: str):
    for o in svc().store.outbox():
        if o["handoff_id"] == handoff_id:
            return JSONResponse(o)
    raise HTTPException(404)


@app.get("/metrics")
def metrics_page(request: Request):
    service = svc()
    return templates.TemplateResponse(request, "metrics.html", {
        "m": metrics.compute(service), "corpus": corpus_stats(service),
        "engine_run": service.engine_has_run()})
