"""Hard gates (PLAN §7.3). Every gate is always computed, so every simultaneous hold is shown."""
from __future__ import annotations

from app.models import GateResult, RuleResult, SimEvent
from app.policy import Workflow
from app.rules.common import latest

BOTH = ["pa_prep", "scheduling"]


def evaluate_gates(*, priority: RuleResult, identity: RuleResult, coverage: RuleResult,
                   pa_requirement: str, criteria: dict[str, RuleResult],
                   events: list[SimEvent], workflow: Workflow) -> list[GateResult]:
    gates = [
        GateResult(gate="G-ID", passed=identity.status == "verified",
                   basis=f"ID-1 {identity.status}", blocks=BOTH),
        GateResult(gate="G-COV", passed=coverage.status == "verified",
                   basis=f"COV-1 {coverage.status}", blocks=BOTH),
        GateResult(gate="G-PAREQ", passed=pa_requirement != "unknown",
                   basis=f"PA requirement {pa_requirement.replace('_', ' ')}", blocks=BOTH),
    ]

    if pa_requirement == "not_required":
        rules = workflow.no_pa_evidence_gate
        ok = all(criteria[r].status == "S" for r in rules)
        basis = (f"No-PA plan: {', '.join(rules)} must be supported (A2); "
                 + ", ".join(f"{r} {criteria[r].status}" for r in rules))
    else:
        ok = criteria["EVIDENCE"].status == "S"
        basis = f"Common evidence test {criteria['EVIDENCE'].status}"
    gates.append(GateResult(gate="G-EVID", passed=ok, basis=basis, blocks=BOTH))

    clin = latest(events, "CLINICAL_REVIEW_DECISION")
    if clin is not None and clin.decision == "cleared_for_scheduling":
        g_clin = (True, f"Cleared for scheduling by {clin.actor_role} (SIMULATED {clin.event_id})")
    elif clin is not None:
        g_clin = (False, f"{clin.actor_role} recorded '{clin.decision}' (SIMULATED {clin.event_id})")
    elif priority.status == "PRI-3" and not workflow.routine_requires_clinical_clearance:
        g_clin = (True, "Routine (PRI-3); no clinical clearance required (A1)")
    else:
        g_clin = (False, f"{priority.status} {priority.detail['label']}: needs clinical clearance")
    gates.append(GateResult(gate="G-CLIN", passed=g_clin[0], basis=g_clin[1], blocks=["scheduling"]))

    pa = latest(events, "PAYER_PA_DECISION")
    if pa_requirement == "not_required":
        g_pa = (True, "PA not required for this plan")
    elif pa is not None and pa.decision == "approved":
        g_pa = (True, f"Payer approved (SIMULATED {pa.event_id})")
    elif pa is not None:
        g_pa = (False, f"Payer decision '{pa.decision}' (SIMULATED {pa.event_id})")
    elif pa_requirement == "unknown":
        g_pa = (False, "PA requirement unknown")
    else:
        g_pa = (False, "PA required; no payer approval recorded")
    gates.append(GateResult(gate="G-PA-APPROVED", passed=g_pa[0], basis=g_pa[1], blocks=["scheduling"]))
    return gates


def passed(gates: list[GateResult], *names: str) -> bool:
    by = {g.gate: g.passed for g in gates}
    return all(by[n] for n in names)
