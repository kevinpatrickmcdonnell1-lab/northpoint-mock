"""FastAPI + Jinja2 + HTMX UI (PLAN §8). Plain server-rendered pages, one CSS file."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from app import handoffs, metrics
from app.engine import evaluate
from app.metrics import business_days
from app.pdfs import PAGES, TITLES
from app.rules.common import why_unused
from app.service import ALLOWED_ROLES, DEFAULT_DB, DEFAULT_OUTBOX, Service

HERE = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("NORTHPOINT_DB", DEFAULT_DB))

app = FastAPI(title="Northpoint stress-echo readiness mock (SYNTHETIC)")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")

STATUS_WORDS = {"S": "Supported", "NM": "Not met", "I": "Insufficient", "NA": "Not applicable"}
OWNER_TABS = [("all", "All", None), ("triage", "Clinical Triage", "Clinical Triage RN"),
              ("review", "Clinical Review", "Clinical Review RN"), ("intake", "Intake", "Intake Coordinator"),
              ("auth", "Auth", "Auth Specialist"), ("ready", "Ready for scheduling", None)]
templates.env.globals.update(STATUS_WORDS=STATUS_WORDS, TITLES=TITLES, quote_plus=quote_plus)


def svc() -> Service:
    if not hasattr(app.state, "svc"):
        app.state.svc = Service(DB_PATH)
        app.state.svc.seed()
    return app.state.svc


def go(url: str, msg: Optional[str] = None) -> RedirectResponse:
    if msg:
        url += ("&" if "?" in url else "?") + "msg=" + quote_plus(msg)
    return RedirectResponse(url, status_code=303)


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
    ef = service.store.case(s.patient_key)
    sent = service.store.outbox_count(s.patient_key, "stress_echo_ready_to_schedule") > 0
    return {
        "s": s, "name": patient_name(ef), "packet": ef.packet_id, "procedure": procedure(ef),
        "plan": s.plan.detail.get("product") or "unresolved",
        "holds": [g.gate for g in s.gates if not g.passed],
        "next": s.actions[0] if s.actions else None,
        "age": business_days(ef.received_at, service.policy.eval_date), "sent": sent,
        "site": ef.ordering_site or "(unknown)",
    }


# ---------------------------------------------------------------- queue

@app.get("/")
def queue(request: Request, owner: str = "all", site: str = "all", msg: Optional[str] = None):
    service = svc()
    rows = [row(s, service) for s in service.states()]
    sites = sorted({r["site"] for r in rows})
    if site != "all":
        rows = [r for r in rows if r["site"] == site]
    role = dict((k, r) for k, _, r in OWNER_TABS).get(owner)
    if owner == "ready":
        rows = [r for r in rows if r["s"].scheduling_sendable]
    elif role:
        rows = [r for r in rows if any(a.owner_role == role for a in r["s"].actions)]
    return templates.TemplateResponse(request, "queue.html", {
        "rows": rows, "owner": owner, "tabs": OWNER_TABS, "site": site, "sites": sites, "policy": service.policy, "msg": msg,
        "workflow": service.workflow})


@app.post("/rerun")
def rerun():
    states = svc().evaluate_all(actor="UI: re-run all through engine")
    return go("/", f"Re-evaluated all {len(states)} cases through one engine; audit entries written.")


@app.post("/reset")
def reset():
    service = svc()
    service.store.close()
    for p in (DB_PATH, Path(str(DB_PATH) + "-wal"), Path(str(DB_PATH) + "-shm")):
        p.unlink(missing_ok=True)
    for f in DEFAULT_OUTBOX.glob("*.json"):
        f.unlink()
    del app.state.svc
    svc()
    return go("/", "Demo reset: source facts reloaded, simulated events, audit and outbox cleared.")


# ---------------------------------------------------------------- case detail

@app.get("/case/{key}")
def case(request: Request, key: str, msg: Optional[str] = None):
    service = svc()
    try:
        ef = service.store.case(key)
    except KeyError:
        raise HTTPException(404)
    events = service.store.events(key)
    s = evaluate(ef, events, service.policy, service.workflow)
    docs = [{"d": d, "unused": why_unused(d, service.policy)} for d in ef.documents]
    return templates.TemplateResponse(request, "case.html", {
        "s": s, "ef": ef, "name": patient_name(ef), "procedure": procedure(ef), "docs": docs,
        "events": events, "audit": service.store.audit_rows(key)[:60],
        "outbox": service.store.outbox(key), "roles": ALLOWED_ROLES, "msg": msg,
        "policy": service.policy, "products": [p.product for p in service.policy.plans.values()],
        "age": business_days(ef.received_at, service.policy.eval_date)})


@app.post("/case/{key}/simulate")
async def simulate(key: str, request: Request):
    form = dict((await request.form()).items())
    t = form.get("type", "")
    kw: dict = {"actor_role": form.get("actor_role", ""), "actor_name": form.get("actor_name"),
                "note": form.get("note")}
    try:
        if t == "IDENTITY_RESOLUTION":
            confirmed = {f: form[f"confirmed_{f}"].strip() for f in ("name", "dob", "member_id")
                         if form.get(f"confirmed_{f}", "").strip()}
            ev = svc().simulate(key, t, "confirmed", confirmed=confirmed,
                                epic_reference=form.get("epic_reference"), **kw)
        elif t == "BENEFITS_RESPONSE":
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
        return go(f"/case/{key}", f"Not recorded: {msg}")
    return go(f"/case/{key}", f"SIMULATED event {ev.event_id} recorded ({ev.type} {ev.decision}); "
                              "case re-evaluated and audited.")


@app.post("/case/{key}/handoff/{kind}")
def handoff(key: str, kind: str):
    if kind not in handoffs.KINDS:
        raise HTTPException(404)
    r = handoffs.send(svc(), key, kind)
    label = handoffs.KINDS[kind][0]
    msgs = {
        "sent": f"Sent {label}: {r.get('handoff_id')} recorded in outbox (MOCK STUB); "
                f"delivery {r.get('delivery')}.",
        "already_recorded": f"Already recorded as {r.get('handoff_id')} (idempotency key unchanged).",
        "blocked": f"BLOCKED {label}: failing gates {', '.join(r.get('failing_gates', []))}. "
                   "Attempt recorded in the audit trail.",
        "not_applicable": f"Not applicable: {r.get('reason')}",
    }
    return go(f"/case/{key}", msgs[r["result"]])


# ---------------------------------------------------------------- pages, outbox, metrics

@app.get("/page/{file}/{page}")
def page_viewer(request: Request, file: str, page: int, q: Optional[str] = None):
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
    key = file.split("_")[0]
    return templates.TemplateResponse(request, "page.html", {
        "file": file, "page": page, "q": q, "text": text, "parts": parts, "key": key})


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
    return templates.TemplateResponse(request, "metrics.html", {"m": metrics.compute(svc())})
