"""Action panels: one focused screen per work item (a case + one reason code).

Each panel shows only the evidence that reason needs and the one decision or request that
settles it, then moves the persona to their next item. Presentation only: every write goes
through the existing audited paths (Service.simulate, handoffs.send) with the persona's
attribution, and the engine never reads anything here.
"""
from __future__ import annotations

from typing import Any, Optional

from app import handoffs
from app.models import CaseState, ExtractionFile

COVERAGE_CODES = {"COV_PENDING_NONE", "COV_PENDING_STALE", "COV_PENDING_PRODUCT", "COV_CONFLICT", "PA_UNKNOWN"}
COVERAGE_DOCS = ("registration_face_sheet", "insurance_card", "benefits_response")
PRIORITY_FIELDS = ("urgent_review_requested", "urgent_reason", "expedited_review_requested",
                   "material_increase_date", "symptom_trajectory", "complaint_type", "timing_text")
DECISIONS = [("cleared_for_scheduling", "Cleared for scheduling"),
             ("reviewed_hold", "Reviewed: hold (clinical follow-up first)"),
             ("not_cleared", "Not cleared (back to ordering clinician)")]
IDENTITY_FIELDS = ("name", "dob", "member_id")   # what IDENTITY_RESOLUTION can confirm


def kind(code: str) -> Optional[str]:
    if code.startswith("CLIN_"):
        return "clinical"
    if code.startswith("ID_"):
        return "identity"
    if code in COVERAGE_CODES:
        return "coverage"
    if code in handoffs.MISSING_CODES:
        return "request"
    if code in ("PA_READY", "PA_AWAITING"):
        return "pa"
    if code in handoffs.REVIEW_CODES:
        return "not_met"
    if code == "SCHED_READY":
        return "schedule"
    return None


# What each panel kind may record (the form's `op`).
OPS = {"clinical": {"decide"}, "identity": {"identity"}, "coverage": {"benefits"},
       "request": {"request"}, "pa": {"pa_packet", "payer"}, "not_met": {"clinician_review"},
       "schedule": {"schedule"}}
PAYER_DECISIONS = [("approved", "Approved"), ("pended", "Pended (payer asked for more)"),
                   ("denied", "Denied")]


def requests_sent(svc, key: str) -> list[dict]:
    work_type = handoffs.KINDS["missing_info"][0]
    return [o for o in svc.store.outbox(key) if o["work_item"]["type"] == work_type]


def _sent(svc, key: str, handoff_kind: str) -> list[dict]:
    work_type = handoffs.KINDS[handoff_kind][0]
    return [o for o in svc.store.outbox(key) if o["work_item"]["type"] == work_type]


def waiting_on(svc, key: str, code: str) -> Optional[str]:
    """The handoff an item is waiting on, if the persona's part is done: a request that named
    it, a PA packet with no payer decision yet, a clinician review, or an active scheduling
    handoff. The engine never reads the outbox, so the reason code itself stays the same."""
    k = kind(code)
    if k == "request":
        for o in reversed(requests_sent(svc, key)):
            if any(r["reason_code"] == code for r in o["work_item"].get("requests", [])):
                return o["handoff_id"]
    elif k == "not_met":
        for o in reversed(_sent(svc, key, "clinician_review")):
            if code in o["work_item"].get("reason_codes", []):
                return o["handoff_id"]
    elif code == "PA_READY":
        sent = _sent(svc, key, "pa_packet")
        return sent[-1]["handoff_id"] if sent else None
    elif k == "schedule":
        active = handoffs.active_scheduling_handoff(svc, key)
        return active["handoff_id"] if active else None
    return None


def _doc_page(ef: ExtractionFile, doc) -> dict[str, Any]:
    return {"file": ef.source_file, "page": doc.pages[0]}


def clinical(s: CaseState, ef: ExtractionFile, svc) -> dict[str, Any]:
    docs = {d.doc_id: d for d in ef.documents}
    sources = []
    for doc_id in s.priority.detail.get("sources", []):
        d = docs.get(doc_id)
        if d is None:
            continue
        facts = {f: ("yes" if v is True else v) for f in PRIORITY_FIELDS
                 if (v := getattr(d.fields, f, None)) not in (None, False, "not_stated")}
        sources.append({"d": d, **_doc_page(ef, d), "facts": facts,
                        "quotes": [q for q in d.quotes if q.field in PRIORITY_FIELDS or q.field == "signer"]})
    triage_type = handoffs.KINDS["triage"][0]
    return {"sources": sources, "decisions": DECISIONS,
            "notified": [o for o in svc.store.outbox(s.patient_key) if o["work_item"]["type"] == triage_type]}


def identity(s: CaseState, ef: ExtractionFile) -> dict[str, Any]:
    fields = []
    for f, e in s.identity.detail.get("fields", {}).items():
        if f not in IDENTITY_FIELDS or e.get("status") == "consistent" or not e.get("values"):
            continue
        groups: dict[str, list] = {}
        for v in e["values"]:
            groups.setdefault(str(v["value"]), []).append(v)
        fields.append({"field": f, "status": e.get("status"), "groups": groups})
    return {"fields": fields}


def coverage(s: CaseState, ef: ExtractionFile) -> dict[str, Any]:
    rows = []
    for d in ef.documents:
        if d.doc_type not in COVERAGE_DOCS:
            continue
        f = d.fields
        rows.append({"d": d, **_doc_page(ef, d), "payer": f.payer, "product": f.plan_product_raw,
                     "member_id": f.member_id, "response_date": getattr(f, "response_date", None),
                     "active": getattr(f, "coverage_active_on_eval_date", None)})
    return {"rows": rows}


def request(s: CaseState, ef: ExtractionFile, svc) -> dict[str, Any]:
    signed = [d for d in ef.documents if d.signed and d.signer
              and d.doc_type in ("stress_echo_order", "referral", "clinician_note")]
    newest = max(signed, key=lambda d: (d.date is not None, d.date), default=None)
    reqs = [a for a in s.actions if a.reason_code in handoffs.MISSING_CODES]
    to = []
    if any(a.reason_code != "COV_INACTIVE" for a in reqs):
        to.append(f"Ordering clinician: {newest.signer}" if newest else "Ordering clinician (no signer found)")
    if any(a.reason_code == "COV_INACTIVE" for a in reqs):
        to.append("Patient (current coverage)")
    return {"requests": reqs, "to": to, "sent": requests_sent(svc, s.patient_key)}


def criteria(s: CaseState, only: Optional[str] = None) -> list[dict[str, Any]]:
    return [{"rule": k, "r": v} for k, v in s.criteria.items()
            if k != "EVIDENCE" and v.status != "NA" and (only is None or v.status == only)]


def pa(s: CaseState, svc) -> dict[str, Any]:
    events = [e for e in svc.store.events(s.patient_key) if e.type == "PAYER_PA_DECISION"]
    return {"criteria": criteria(s), "packets": _sent(svc, s.patient_key, "pa_packet"),
            "payer_events": events, "decisions": PAYER_DECISIONS}


def not_met(s: CaseState, svc) -> dict[str, Any]:
    denial = next((e for e in reversed(svc.store.events(s.patient_key))
                   if e.type == "PAYER_PA_DECISION" and e.decision == "denied"), None)
    return {"criteria": criteria(s, "NM"), "denial": denial, "statement": handoffs.NOT_A_DENIAL,
            "sent": _sent(svc, s.patient_key, "clinician_review")}


def schedule(s: CaseState, svc) -> dict[str, Any]:
    return {"gates": s.gates, "events": svc.store.events(s.patient_key),
            "corrections": [r for r in svc.store.reviews(s.patient_key) if r.action == "correct"],
            "active": handoffs.active_scheduling_handoff(svc, s.patient_key)}


def build(code: str, s: CaseState, ef: ExtractionFile, svc) -> dict[str, Any]:
    k = kind(code)
    ctx: dict[str, Any] = {"kind": k}
    if k == "clinical":
        ctx.update(clinical(s, ef, svc))
    elif k == "identity":
        ctx.update(identity(s, ef))
    elif k == "coverage":
        ctx.update(coverage(s, ef))
    elif k == "request":
        ctx.update(request(s, ef, svc))
    elif k == "pa":
        ctx.update(pa(s, svc))
    elif k == "not_met":
        ctx.update(not_met(s, svc))
    elif k == "schedule":
        ctx.update(schedule(s, svc))
    return ctx
