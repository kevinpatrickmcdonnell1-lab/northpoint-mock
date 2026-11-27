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
from app import handoffs, metrics, panels, personas, presenter
from app.engine import evaluate
from app.metrics import business_days
from app.pdfs import PAGES, TITLES
from app.rules.common import why_unused
from app.service import ALLOWED_ROLES, DEFAULT_DB, DEFAULT_OUTBOX, Service, wall_clock
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


def persona_of(request: Request) -> Optional[personas.Persona]:
    """The persona this browser is acting as (cookie), or None for presenter mode."""
    return personas.get(request.cookies.get(personas.COOKIE))


def actor_label(request: Request, default: str) -> str:
    p = persona_of(request)
    return personas.label(p) if p else default


templates.env.globals.update(persona_of=persona_of, PERSONAS=personas.PERSONAS,
                             persona_label=personas.label, may_send=personas.may_send,
                             may_record=personas.may_record, STATUS_WORDS=STATUS_WORDS, TITLES=TITLES, quote_plus=quote_plus,
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
def rerun(request: Request, back: str = Form("/")):
    with svc().lock:
        states = svc().evaluate_all(actor=f"UI: re-run all through engine "
                                          f"({actor_label(request, 'presenter')})")
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
    p = persona_of(request)
    if p:
        # Server-side attribution: the persona decides who recorded this, never the form.
        t = form.get("type", "")
        if not personas.may_record(p, t):
            allowed = ", ".join(personas.EVENT_ROLES.get(t, ())) or "nobody"
            return go(f"/case/{key}", f"Not recorded: {p.role} can't record {t or 'this event'} "
                                      f"(recorded by: {allowed}).", at="simulate")
        form["actor_role"] = personas.EVENT_ACTOR_ROLE.get(t, p.role)
        form["actor_name"] = p.name
    with svc().lock:
        return _simulate(key, form)


def benefits_from_form(form: dict, eval_date: date) -> tuple[dict, Optional[str]]:
    """The E&B payload from a form, plus an error when the response date is after the
    evaluation date (the engine would ignore it, A8)."""
    try:
        rd = date.fromisoformat((form.get("response_date") or "").strip())
    except ValueError:
        rd = None
    if rd is not None and rd > eval_date:
        return {}, (f"response date {rd} is after the evaluation date {eval_date}, so the engine "
                    f"would ignore it (A8). Use a date on or before {eval_date}.")
    return {"response_date": form.get("response_date"), "payer": form.get("payer") or None,
            "plan_product_raw": form.get("plan_product_raw") or None,
            "member_id": form.get("member_id") or None,
            "coverage_active_on_eval_date": {"true": True, "false": False}.get(form.get("active", "")),
            "product_end_date": form.get("product_end_date") or None}, None


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
            b, late = benefits_from_form(form, svc().policy.eval_date)
            if late:
                return go(f"/case/{key}?form=benefits", f"Not recorded: {late}", at="sim-benefits")
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
async def handoff(key: str, kind: str, request: Request):
    if kind not in handoffs.KINDS:
        raise HTTPException(404)
    form = dict((await request.form()).items())
    back = form.get("back", "")
    back_url, at = (back, None) if back in ("/work",) else (f"/case/{key}", "handoffs")
    p = persona_of(request)
    label = handoffs.KINDS[kind][0]
    with svc().lock:
        try:
            svc().store.case(key)
        except KeyError:
            raise HTTPException(404)
        if not personas.may_send(p, kind):
            allowed = ", ".join(personas.HANDOFF_ROLES.get(kind, ())) or "nobody"
            reason = f"role {p.role} may not send {label} (sent by: {allowed})"
            svc().store.audit(key, wall_clock(), personas.label(p), "handoff_blocked",
                              {"kind": label, "failing_gates": [], "reason": reason})
            return go(back_url, f"Not recorded: {reason}. Attempt recorded in the audit trail.", at=at)
        try:
            r = handoffs.send(svc(), key, kind, actor=actor_label(request, "UI user"))
        except sqlite3.IntegrityError:
            return go(back_url, CONCURRENT_WRITE, at=at)
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
    msg = msgs[r["result"]]
    return go(back_url, f"{key}: {msg}" if back_url == "/work" else msg, at=at)


# ---------------------------------------------------------------- personas + my work

@app.post("/persona")
def choose_persona(persona: str = Form("")):
    p = personas.get(persona)
    r = RedirectResponse(p.landing if p else "/", status_code=303)
    if p:
        r.set_cookie(personas.COOKIE, p.key, httponly=True, samesite="lax")
    else:
        r.delete_cookie(personas.COOKIE)
    return r


def _anchor(code: str) -> str:
    """Where on the case page the persona acts on this item."""
    if code.startswith(("CLIN_", "ID_")) or code in ("PA_AWAITING", "PA_DENIED"):
        return "simulate"
    if code.startswith("COV_PENDING") or code in ("COV_CONFLICT", "PA_UNKNOWN"):
        return "sim-benefits"
    return "handoffs"


def work_items(service: Service, p: personas.Persona) -> list[dict]:
    """The persona's open items: one per (case, next action) they own, plus fact flags of the
    kind they review. Sorted urgent → undetermined → expedited → routine, then oldest first."""
    items = []
    for s in service.states():
        ef = service.case(s.patient_key)
        base = {"s": s, "key": s.patient_key, "name": patient_name(ef),
                "age": business_days(ef.received_at, service.policy.eval_date),
                "rank": personas.PRIORITY_ORDER.get(s.priority.status, 9)}
        mine = [a for a in s.actions if personas.owns(p, a)]
        others = sorted({a.owner_role for a in s.actions if not personas.owns(p, a)})
        for a in mine:
            code = a.reason_code
            items.append({**base, "code": code, "title": a.action, "detail": a.detail,
                          "owner": a.owner_role, "others": others,
                          "primary": personas.PRIMARY_HANDOFF.get(code),
                          "waiting": panels.waiting_on(service, s.patient_key, code),
                          "href": (f"/work/{s.patient_key}/{code}" if panels.kind(code)
                                   else f"/case/{s.patient_key}#{_anchor(code)}")})
        if p.fact_flags:
            for f in service.open_flags(s.patient_key):
                if fr.is_clinical(f.field) == (p.fact_flags == "clinical"):
                    items.append({**base, "code": f"FACT_{f.field}", "title": f"Check {f.field} on {f.doc_id}",
                                  "detail": f.message, "owner": p.role, "others": others, "primary": None,
                                  "waiting": None,
                                  "href": page_url(ef.source_file, f.page, s.patient_key) + f"#fact-{f.doc_id}-{f.field}"})
    return sorted(items, key=lambda i: (i["rank"], -i["age"], i["key"], i["code"]))


@app.get("/work")
def my_work(request: Request, msg: Optional[str] = None):
    p = persona_of(request)
    if p is None:
        return go("/", "Choose who you are (top right) to see your own work list.")
    service = svc()
    run = service.engine_has_run()
    items = work_items(service, p) if run and not p.read_only else []
    return templates.TemplateResponse(request, "work.html", {
        "p": p, "items": [i for i in items if not i["waiting"]],
        "waiting": [i for i in items if i["waiting"]], "msg": msg, "engine_run": run})


def _next_url(service: Service, p: personas.Persona, done: tuple[str, str]) -> str:
    """The persona's next active item, preferring another case over the one just handled."""
    items = [i for i in work_items(service, p) if not i["waiting"] and (i["key"], i["code"]) != done]
    other = [i for i in items if i["key"] != done[0]]
    nxt = (other or items or [None])[0]
    return nxt["href"] if nxt else "/work"


def _find_item(service: Service, p: personas.Persona, key: str, code: str) -> Optional[dict]:
    return next((i for i in work_items(service, p) if i["key"] == key and i["code"] == code), None)


@app.get("/work/{key}/{code}")
def work_panel(request: Request, key: str, code: str, msg: Optional[str] = None):
    p = persona_of(request)
    if p is None:
        return go(f"/case/{key}")
    service = svc()
    item = _find_item(service, p, key, code) if service.engine_has_run() else None
    if item is None:
        return go("/work", f"{key} {code} is not on your list ({p.role}): it may be done or owned by another role.")
    if not panels.kind(code):
        return go(item["href"])
    s, ef = item["s"], service.case(key)
    active = [i for i in work_items(service, p) if not i["waiting"]]
    pos = next((n for n, i in enumerate(active, 1) if (i["key"], i["code"]) == (key, code)), None)
    return templates.TemplateResponse(request, "panel.html", {
        "p": p, "i": item, "s": s, "ef": ef, "x": panels.build(code, s, ef, service),
        "pos": pos, "total": len(active), "msg": msg, "policy": service.policy,
        "skip": _next_url(service, p, (key, code)),
        "products": [pl.product for pl in service.policy.plans.values()]})


OPS = {"clinical": "decide", "identity": "identity", "coverage": "benefits", "request": "request"}


@app.post("/work/{key}/{code}")
async def work_act(request: Request, key: str, code: str):
    p = persona_of(request)
    if p is None:
        return go(f"/case/{key}", "Choose who you are (top right) to work from a panel.")
    form = dict((await request.form()).items())
    here = f"/work/{key}/{code}"
    service = svc()
    with service.lock:
        item = _find_item(service, p, key, code) if service.engine_has_run() else None
        if item is None:
            return go("/work", f"Not recorded: {key} {code} is not on your list ({p.role}).")
        k, op = panels.kind(code), form.get("op", "")
        if k is None or OPS[k] != op:
            return go(here, f"Not recorded: “{op or 'nothing'}” doesn't settle {code}.")
        note = (form.get("note") or "").strip()
        try:
            if op == "decide":
                decision = form.get("decision", "")
                if decision not in dict(panels.DECISIONS):
                    return go(here, "Not recorded: choose a decision.")
                if not note:
                    return go(here, "Not recorded: a clinical decision needs a note saying what you reviewed.")
                ev = service.simulate(key, "CLINICAL_REVIEW_DECISION", decision, actor_role=p.role,
                                      actor_name=p.name, note=note)
                done = f"{ev.event_id}: {key} {decision.replace('_', ' ')} recorded by {personas.label(p)}."
            elif op == "identity":
                fields = [f["field"] for f in panels.identity(item["s"], service.case(key))["fields"]]
                confirmed, missing = {}, []
                for f in fields:
                    v = form.get(f"confirmed_{f}", "")
                    v = (form.get(f"confirmed_{f}_other", "") if v == "__other" else v).strip()
                    (confirmed.__setitem__(f, v) if v else missing.append(f))
                if missing:
                    return go(here, f"Not recorded: confirm a value for {', '.join(missing)} "
                                    "(every disagreeing identifier must be settled).")
                ev = service.simulate(key, "IDENTITY_RESOLUTION", "confirmed", actor_role=p.role,
                                      actor_name=p.name, note=note or None, confirmed=confirmed,
                                      epic_reference=(form.get("epic_reference") or "").strip() or None)
                done = f"{ev.event_id}: identity confirmed for {key} by {personas.label(p)}."
            elif op == "benefits":
                b, late = benefits_from_form(form, service.policy.eval_date)
                if late:
                    return go(here, f"Not recorded: {late}")
                ev = service.simulate(key, "BENEFITS_RESPONSE", "received",
                                      actor_role=personas.EVENT_ACTOR_ROLE["BENEFITS_RESPONSE"],
                                      actor_name=p.name, note=note or None, benefits=b)
                done = f"{ev.event_id}: E&B response logged for {key} by {personas.label(p)}."
            else:  # request
                if not personas.may_send(p, "missing_info"):
                    return go(here, f"Not recorded: {p.role} doesn't send document requests.")
                r = handoffs.send(service, key, "missing_info", actor=personas.label(p))
                if r["result"] not in ("sent", "already_recorded"):
                    return go(here, f"Not recorded: {r.get('reason') or r['result']}")
                done = (f"{r['handoff_id']}: request for {key} sent by {personas.label(p)}."
                        if r["result"] == "sent" else f"Already sent as {r['handoff_id']}.")
        except (ValidationError, ValueError) as e:
            msg = e.errors()[0]["msg"] if isinstance(e, ValidationError) else str(e)
            return go(here, f"Not recorded: {msg}")
        except sqlite3.IntegrityError:
            return go(here, CONCURRENT_WRITE)
        nxt = _next_url(service, p, (key, code))
    return go(nxt, done + ("" if nxt != "/work" else " Your list is clear."))


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
    p = persona_of(request)
    if p:
        form["actor_role"], form["actor_name"] = p.role, p.name
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
