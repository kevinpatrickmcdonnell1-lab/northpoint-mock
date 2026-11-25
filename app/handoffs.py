"""Handoff stubs (PLAN §8.4) → outbox table + outbox/*.json. Idempotent; scheduling is hard-gated."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from app.engine import gate_snapshot_hash
from app.models import CaseState
from app.service import Service, wall_clock

KINDS = {
    "schedule": ("stress_echo_ready_to_schedule", "Epic Cadence / scheduling WQ (MOCK STUB)", "SCHED"),
    "pa_packet": ("pa_request_packet", "Payer PA portal (MOCK STUB)", "PA"),
    "identity_task": ("identity_verification_task", "Epic intake WQ (MOCK STUB)", "ID"),
    "triage": ("urgent_triage_notification", "Epic In Basket (MOCK STUB)", "TRIAGE"),
    "missing_info": ("missing_info_request", "Fax/Comms (MOCK STUB)", "INFO"),
}
PA_PREP_GATES = ("G-ID", "G-COV", "G-PAREQ", "G-EVID")
MISSING_CODES = {"EVID_MISSING_ORDER", "EVID_MISSING_REPORT", "EVID_HMO1", "COV_INACTIVE"}
CLIN_CODES = {"CLIN_URGENT", "CLIN_EXPEDITED", "CLIN_UNDETERMINED", "CLIN_ROUTINE_REVIEW"}


def _gates(s: CaseState, names=None) -> list[dict]:
    return [{"gate": g.gate, "status": "pass" if g.passed else "blocked", "basis": g.basis}
            for g in s.gates if names is None or g.gate in names]


def _evidence(s: CaseState) -> list[dict]:
    return [{"rule": k, "file": r.file, "page": r.page, "quote": r.quote}
            for k, v in s.criteria.items() if k != "EVIDENCE" for r in v.evidence_refs]


def send(svc: Service, key: str, kind: str, actor: str = "UI user") -> dict[str, Any]:
    work_type, receiver, tag = KINDS[kind]
    s = svc.state(key)
    ef = svc.store.case(key)
    events = svc.store.events(key)
    svc.store.audit(key, wall_clock(), actor, "handoff_attempt", {"kind": work_type})

    def blocked(gates: list[str], why: str) -> dict:
        detail = {"kind": work_type, "failing_gates": gates, "reason": why}
        svc.store.audit(key, wall_clock(), actor, "handoff_blocked", detail)
        return {"result": "blocked", **detail}

    item: dict[str, Any] = {"type": work_type, "packet_id": ef.packet_id,
                            "mrn": next((d.identifiers.mrn for d in ef.documents if d.identifiers.mrn), None)}
    gates_checked: list[dict] = []
    if kind == "schedule":
        failing = [g.gate for g in s.gates if not g.passed]
        if failing or not s.scheduling_sendable:
            return blocked(failing, "Scheduling handoff requires all six gates to pass.")
        gates_checked = _gates(s)
        item.update(procedure="Outpatient stress echocardiogram",
                    clinical_priority=s.priority.detail["label"].lower(),
                    plan=s.plan.detail.get("plan_id"))
    elif kind == "pa_packet":
        failing = [g.gate for g in s.gates if g.gate in PA_PREP_GATES and not g.passed]
        if s.pa_requirement != "required" or failing:
            return blocked(failing or ["PA not required"], "PA packet requires PA-prep gates to pass.")
        gates_checked = _gates(s, PA_PREP_GATES)
        item.update(plan=s.plan.detail.get("plan_id"), procedure="Outpatient stress echocardiogram",
                    criteria={k: v.status for k, v in s.criteria.items()},
                    attachments=sorted({(r["file"], r["page"]) for r in _evidence(s)}))
    elif kind == "identity_task":
        if s.identity.status == "verified":
            return {"result": "not_applicable", "reason": "Identity already verified."}
        item.update(status=s.identity.status, reason=s.identity.reason_text,
                    values={f: e["values"] for f, e in s.identity.detail["fields"].items()
                            if e["status"] in ("conflicting", "resolved")})
    elif kind == "triage":
        clin = [a for a in s.actions if a.reason_code in CLIN_CODES]
        if not clin:
            return {"result": "not_applicable", "reason": "No clinical escalation for this case."}
        item.update(clinical_priority=s.priority.status, label=s.priority.detail["label"],
                    reason=s.priority.reason_text,
                    sources=[{"file": r.file, "page": r.page, "quote": r.quote}
                             for r in s.priority.evidence_refs],
                    note="Clinical routing is never gated by admin status.")
    elif kind == "missing_info":
        req = [a for a in s.actions if a.reason_code in MISSING_CODES]
        if not req:
            return {"result": "not_applicable", "reason": "No missing-information request open."}
        item.update(requests=[{"reason_code": a.reason_code, "document": a.detail or a.action}
                              for a in req])

    snap = gate_snapshot_hash(s) if kind in ("schedule", "pa_packet") else hashlib.sha256(
        json.dumps(item, sort_keys=True, default=str).encode()).hexdigest()[:16]
    idem = f"{ef.packet_id}:{kind}:{snap}"
    existing = svc.store.outbox_by_key(idem)
    if existing:
        return {"result": "already_recorded", "handoff_id": existing["handoff_id"], "payload": existing}

    n = svc.store.outbox_count(key, work_type) + 1
    payload = {
        "handoff_id": f"HO-{key}-{tag}-{n}", "idempotency_key": idem, "patient_key": key,
        "receiving_system": receiver, "work_item": item, "gates_checked": gates_checked,
        "evidence_refs": _evidence(s) if kind in ("schedule", "pa_packet") else [],
        "simulated_dependencies": [{"event_id": e.event_id, "type": e.type, "decision": e.decision,
                                    "simulated": True} for e in events],
        "policy_version": s.policy_version, "eval_date": str(s.eval_date), "status": "recorded",
        "label": "SYNTHETIC – MOCK DATA",
    }
    # Defense in depth: never record a scheduling handoff with a failing gate.
    if kind == "schedule":
        assert all(g["status"] == "pass" for g in payload["gates_checked"]) and len(gates_checked) == 6
    at = wall_clock()
    svc.store.outbox_add(payload, at)
    svc.outbox_dir.mkdir(parents=True, exist_ok=True)
    (svc.outbox_dir / f"{payload['handoff_id']}.json").write_text(json.dumps(payload, indent=2, default=str))
    svc.store.audit(key, at, actor, "handoff_sent", {"handoff_id": payload["handoff_id"],
                                                     "kind": work_type, "idempotency_key": idem})
    d = deliver(svc, payload["handoff_id"], actor="system (first attempt)")
    return {"result": "sent", "handoff_id": payload["handoff_id"], "payload": payload,
            "delivery": d["status"]}


# --------------------------------------------------------------------------- #
# Stretch: delivery to (mock) downstream systems with retry and dead-letter.   #
# A handoff is recorded once (idempotent); delivery attempts are separate rows. #
# Receiver outages are SIMULATED and created only by an explicit UI action.    #
# --------------------------------------------------------------------------- #

def _payload(svc: Service, handoff_id: str) -> dict:
    for o in svc.store.outbox():
        if o["handoff_id"] == handoff_id:
            return o
    raise KeyError(handoff_id)


def delivery_status(svc: Service, handoff_id: str) -> dict[str, Any]:
    rows = svc.store.deliveries(handoff_id)
    last_requeue = max((i for i, r in enumerate(rows) if r["result"] == "requeued"), default=-1)
    current = rows[last_requeue + 1:]
    failed = sum(r["result"] == "failed" for r in current)
    if any(r["result"] == "delivered" for r in current):
        status = "delivered"
    elif any(r["result"] == "dead_letter" for r in current):
        status = "dead_letter"
    elif failed:
        status = "retrying"
    else:
        status = "pending"
    return {"status": status, "attempts": len([r for r in rows if r["result"] in ("delivered", "failed")]),
            "failed_this_cycle": failed, "rows": rows,
            "last_error": next((r["error"] for r in reversed(rows) if r["error"]), None)}


def deliver(svc: Service, handoff_id: str, actor: str = "UI user") -> dict[str, Any]:
    """One delivery attempt. Never re-records or re-gates the handoff; it only transports it."""
    payload = _payload(svc, handoff_id)
    st = delivery_status(svc, handoff_id)
    if st["status"] in ("delivered", "dead_letter"):
        return {"result": "no_op", **st}
    receiver = payload["receiving_system"]
    attempt = st["attempts"] + 1
    at = wall_clock()
    key = payload["patient_key"]
    if svc.store.outages().get(receiver):
        err = f"{receiver} unavailable (SIMULATED outage)"
        svc.store.add_delivery(handoff_id, attempt, at, "failed", err)
        svc.store.audit(key, at, actor, "delivery_attempt",
                        {"handoff_id": handoff_id, "attempt": attempt, "result": "failed", "error": err})
        if st["failed_this_cycle"] + 1 >= svc.workflow.delivery_max_attempts:
            svc.store.add_delivery(handoff_id, attempt, at, "dead_letter", err)
            svc.store.audit(key, at, "system", "dead_letter",
                            {"handoff_id": handoff_id, "after_attempts": st["failed_this_cycle"] + 1,
                             "owner": "Integration support (named owner on each side)"})
    else:
        svc.store.add_delivery(handoff_id, attempt, at, "delivered")
        svc.store.audit(key, at, actor, "delivery_attempt",
                        {"handoff_id": handoff_id, "attempt": attempt, "result": "delivered"})
    return {"result": "attempted", **delivery_status(svc, handoff_id)}


def requeue(svc: Service, handoff_id: str, actor: str = "UI user") -> dict[str, Any]:
    """Replay a dead-lettered handoff (same payload, same idempotency key)."""
    st = delivery_status(svc, handoff_id)
    if st["status"] != "dead_letter":
        return {"result": "no_op", **st}
    at = wall_clock()
    svc.store.add_delivery(handoff_id, st["attempts"], at, "requeued")
    svc.store.audit(_payload(svc, handoff_id)["patient_key"], at, actor, "delivery_attempt",
                    {"handoff_id": handoff_id, "result": "requeued"})
    return deliver(svc, handoff_id, actor)


def retry_pending(svc: Service, actor: str = "system (retry sweep)") -> list[dict]:
    out = []
    for o in svc.store.outbox():
        if delivery_status(svc, o["handoff_id"])["status"] in ("pending", "retrying"):
            out.append({"handoff_id": o["handoff_id"], **deliver(svc, o["handoff_id"], actor)})
    return out


def set_outage(svc: Service, receiving_system: str, down: bool, actor: str = "UI user") -> None:
    if receiving_system not in RECEIVERS:
        raise ValueError(receiving_system)
    at = wall_clock()
    svc.store.set_outage(receiving_system, down, at, "SIMULATED")
    svc.store.audit(None, at, actor, "outage",
                    {"receiving_system": receiving_system, "down": down, "simulated": True})


RECEIVERS = [v[1] for v in KINDS.values()]
