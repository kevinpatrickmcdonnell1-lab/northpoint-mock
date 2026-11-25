"""CaseState = evaluate(facts, simulated_events, policy, workflow).

Pure: no I/O, no clock, no patient-specific branches, never creates events.
"""
from __future__ import annotations

import hashlib
import json

from app.actions import build_actions
from app.gates import evaluate_gates, passed
from app.models import CaseState, ExtractionFile, SimEvent
from app.policy import Policy, Workflow
from app.rules.common import usable_docs
from app.rules.coverage import evaluate_coverage
from app.rules.evidence import evaluate_evidence
from app.rules.identity import evaluate_identity
from app.rules.plan import evaluate_plan
from app.rules.priority import evaluate_priority


def evaluate(ef: ExtractionFile, events: list[SimEvent], policy: Policy, workflow: Workflow) -> CaseState:
    evs = sorted((e for e in events if e.patient_key == ef.patient_key),
                 key=lambda e: (e.event_time, e.event_id))
    docs = usable_docs(ef, policy)

    priority = evaluate_priority(ef, docs, policy)          # sees no admin inputs
    identity = evaluate_identity(ef, docs, evs, policy)
    plan, pa_requirement = evaluate_plan(ef, docs, evs, policy)
    coverage = evaluate_coverage(ef, docs, evs, policy, plan)
    criteria = evaluate_evidence(ef, docs, policy, plan.detail.get("extra", []))

    gates = evaluate_gates(priority=priority, identity=identity, coverage=coverage,
                           pa_requirement=pa_requirement, criteria=criteria, events=evs,
                           workflow=workflow)
    pa_prep_ready = pa_requirement == "required" and passed(gates, "G-ID", "G-COV", "G-PAREQ", "G-EVID")
    sendable = all(g.passed for g in gates)
    admin_ready = passed(gates, "G-ID", "G-COV", "G-EVID", "G-PA-APPROVED")
    actions = build_actions(priority=priority, identity=identity, coverage=coverage, plan=plan,
                            pa_requirement=pa_requirement, criteria=criteria, events=evs,
                            workflow=workflow, pa_prep_ready=pa_prep_ready, sendable=sendable)
    return CaseState(
        patient_key=ef.patient_key, packet_id=ef.packet_id, policy_version=policy.policy_version,
        eval_date=policy.eval_date, priority=priority, identity=identity, plan=plan,
        coverage=coverage, pa_requirement=pa_requirement, criteria=criteria, gates=gates,
        pa_prep_ready=pa_prep_ready, scheduling_sendable=sendable, admin_ready=admin_ready,
        actions=actions, simulated_event_ids=[e.event_id for e in evs],
    )


def state_hash(state: CaseState) -> str:
    return hashlib.sha256(json.dumps(state.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()


def gate_snapshot_hash(state: CaseState) -> str:
    snap = {"gates": [g.model_dump() for g in state.gates], "events": state.simulated_event_ids}
    return hashlib.sha256(json.dumps(snap, sort_keys=True).encode()).hexdigest()[:16]
