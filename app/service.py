"""Application service: wires store + engine + simulated events + audit.

The engine stays pure; this layer does the I/O. Only explicit calls to `simulate()`
(from a labelled UI control or a test) ever create events.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from app.engine import evaluate, state_hash
from app.models import BenefitsResponseFields, CaseState, SimEvent
from app.pdfs import extractions_dir as active_extractions_dir, load_extractions
from app.policy import POLICY_PATH, WORKFLOW_PATH, Policy, Workflow, load_config
from app.store import Store

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "northpoint.db"
DEFAULT_OUTBOX = ROOT / "outbox"

ALLOWED_ROLES = {
    "PAYER_PA_DECISION": ["Auth Specialist"],
    "CLINICAL_REVIEW_DECISION": ["Clinical Triage RN", "Clinical Review RN", "Cardiologist"],
    "IDENTITY_RESOLUTION": ["Intake Coordinator"],
    "BENEFITS_RESPONSE": ["Insurance Benefits Manager (mock)"],
}


def wall_clock() -> str:
    return datetime.now().isoformat(timespec="seconds")


def summarize(s: CaseState) -> dict[str, Any]:
    return {
        "priority": s.priority.status, "identity": s.identity.status,
        "coverage": s.coverage.status, "plan": s.plan.detail.get("plan_id"),
        "pa_requirement": s.pa_requirement,
        "criteria": {k: v.status for k, v in s.criteria.items()},
        "failing_gates": [g.gate for g in s.gates if not g.passed],
        "actions": [a.reason_code for a in s.actions],
        # R6: the audit records who owns the next step, not only the reason codes.
        "next_actions": [{"reason_code": a.reason_code, "owner_role": a.owner_role,
                          "tennr_product": a.tennr_product} for a in s.actions],
        "next_owner": s.actions[0].owner_role if s.actions else None,
        "pa_prep_ready": s.pa_prep_ready, "scheduling_sendable": s.scheduling_sendable,
        "admin_ready": s.admin_ready,
        "evidence_refs": {k: [f"{r.file} p.{r.page}" for r in v.evidence_refs]
                          for k, v in s.criteria.items()},
        "simulated_event_ids": s.simulated_event_ids,
    }


def diff(prev: Optional[dict], cur: dict) -> dict:
    if prev is None:
        return {"initial": True}
    out = {}
    for k in cur:
        if k == "evidence_refs":
            continue
        if prev.get(k) != cur[k]:
            out[k] = {"from": prev.get(k), "to": cur[k]}
    return out


class Service:
    def __init__(self, db_path: Path | str = DEFAULT_DB, outbox_dir: Path = DEFAULT_OUTBOX,
                 policy: Optional[Policy] = None, workflow: Optional[Workflow] = None,
                 extractions_dir: Path | None = None,
                 policy_path: Path = POLICY_PATH, workflow_path: Path = WORKFLOW_PATH):
        # Validate config first, so a bad YAML fails at startup with a ConfigError (Fix 5).
        loaded_policy, loaded_workflow, self.config_sha256 = load_config(policy_path, workflow_path)
        self.store = Store(db_path)
        self.outbox_dir = Path(outbox_dir)
        self.policy = policy or loaded_policy
        self.workflow = workflow or loaded_workflow
        if policy or workflow:
            self.config_sha256 = "in-memory override (not from the YAML files)"
        self.extractions_dir = extractions_dir or active_extractions_dir()

    # ---- setup ----------------------------------------------------------------
    def seed(self) -> None:
        if self.store.keys():
            return
        for key, ef in load_extractions(self.extractions_dir).items():
            self.store.load_case(ef, wall_clock())
        self.evaluate_all(actor="system (seed)")

    # ---- evaluation -----------------------------------------------------------
    def state(self, key: str) -> CaseState:
        return evaluate(self.store.case(key), self.store.events(key), self.policy, self.workflow)

    def states(self) -> list[CaseState]:
        events = self.store.events()
        return [evaluate(self.store.case(k), events, self.policy, self.workflow) for k in self.store.keys()]

    def evaluate_and_record(self, key: str, actor: str = "system", kind: str = "evaluation") -> CaseState:
        s = self.state(key)
        cur = summarize(s)
        prev = self.store.last_evaluation(key)
        changes = diff(prev, cur)
        detail = {"policy_version": s.policy_version, "config_sha256": self.config_sha256,
                  "eval_date": str(s.eval_date),
                  "state_hash": state_hash(s), **cur, "diff": changes}
        self.store.audit(key, wall_clock(), actor, "evaluation", detail)
        if kind == "state_change" and changes:
            self.store.audit(key, wall_clock(), actor, "state_change", {"diff": changes})
        return s

    def evaluate_all(self, actor: str = "system") -> list[CaseState]:
        return [self.evaluate_and_record(k, actor) for k in self.store.keys()]

    # ---- simulated events (explicit action only) ------------------------------
    def next_event_time(self) -> datetime:
        start = datetime.fromisoformat(self.workflow.sim_clock_start)
        return start + timedelta(minutes=30 * self.store.event_count())

    def simulate(self, key: str, type_: str, decision: str, actor_role: str,
                 actor_name: Optional[str] = None, note: Optional[str] = None,
                 confirmed: Optional[dict[str, str]] = None, epic_reference: Optional[str] = None,
                 benefits: Optional[dict] = None) -> SimEvent:
        if actor_role not in ALLOWED_ROLES.get(type_, []):
            raise ValueError(f"{actor_role} cannot record {type_} (allowed: {ALLOWED_ROLES.get(type_)})")
        self.store.case(key)  # must exist
        pa_handoff_id = None
        if type_ == "PAYER_PA_DECISION":
            # Fix 2: a payer can only decide on a PA request that was actually submitted.
            packets = [o for o in self.store.outbox(key) if o["work_item"]["type"] == "pa_request_packet"]
            if not packets:
                raise ValueError(f"No PA request packet has been sent for {key}, so there is no "
                                 "payer decision to record. Prepare the PA packet first.")
            pa_handoff_id = packets[-1]["handoff_id"]
        ev = SimEvent(
            event_id=f"EV-{self.store.event_count() + 1:04d}", patient_key=key, type=type_,
            decision=decision, actor_role=actor_role, actor_name=actor_name or None,
            event_time=self.next_event_time(), recorded_at=datetime.now().replace(microsecond=0),
            note=note or None, confirmed=confirmed or None, epic_reference=epic_reference or None,
            benefits=BenefitsResponseFields.model_validate(benefits) if benefits else None,
            pa_handoff_id=pa_handoff_id,
        )
        self.store.add_event(ev)
        self.store.audit(key, wall_clock(), actor_role, "sim_event",
                         {"simulated": True, **ev.model_dump(mode="json")})
        self.evaluate_and_record(key, actor=f"system (after {ev.event_id})", kind="state_change")
        # Fix 4: a case that loses readiness after a scheduling handoff is recalled (service
        # layer, not the engine: the engine stays pure and never records anything).
        from app.handoffs import recall_if_needed
        recall_if_needed(self, key)
        return ev
