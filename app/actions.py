"""Reason codes → next action → owner role → Tennr product (PLAN §7.5).

Clinical escalation actions are generated whatever the admin gates say.
"""
from __future__ import annotations

from app.models import NextAction, RuleResult, SimEvent
from app.policy import Workflow
from app.rules.common import latest

TRIAGE, REVIEW, INTAKE, AUTH, SCHED = (
    "Clinical Triage RN", "Clinical Review RN", "Intake Coordinator", "Auth Specialist", "Scheduling (Epic)")
ROUTING, IOPS, IBM, QRE, PAM, COMMS = (
    "Intake Operations routing", "Intake Operations", "Insurance Benefits Manager",
    "Qualification Review Engine", "Prior Authorization Manager", "Communications Coordinator")

CATALOG: dict[str, tuple[str, str, str]] = {
    "CLIN_URGENT": ("Notify cardiology clinical triage owner now. Hold scheduling until reviewed", TRIAGE, ROUTING),
    "CLIN_EXPEDITED": ("Place in clinical review queue, expedited", REVIEW, ROUTING),
    "CLIN_UNDETERMINED": ("Ask triage owner to clarify timing/severity (conflicting sources shown)", TRIAGE, ROUTING),
    "CLIN_ROUTINE_REVIEW": ("Routine clinical sign-off before scheduling (flag A1 on)", REVIEW, ROUTING),
    "CLIN_REVIEWED_HOLD": ("Triage reviewed and placed a hold (SIMULATED). Scheduling held until cleared", TRIAGE, ROUTING),
    "CLIN_NOT_CLEARED": ("Not cleared for scheduling (SIMULATED). Follow up with ordering clinician", REVIEW, ROUTING),
    "ID_CONFLICT": ("Verify in Epic; both values and pages shown", INTAKE, IOPS),
    "ID_PENDING": ("Obtain missing identity evidence", INTAKE, IOPS),
    "COV_PENDING_NONE": ("Run E&B", INTAKE, IBM),
    "COV_PENDING_STALE": ("Run E&B (refresh stale response)", INTAKE, IBM),
    "COV_PENDING_PRODUCT": ("Confirm exact plan product", INTAKE, IBM),
    "PA_UNKNOWN": ("Confirm exact plan product", INTAKE, IBM),
    "COV_CONFLICT": ("Reconcile member/product mismatch", INTAKE, IBM),
    "COV_INACTIVE": ("Obtain current coverage from patient", INTAKE, COMMS),
    "EVID_MISSING_ORDER": ("Request signed stress echo order from ordering clinician", INTAKE, f"{COMMS} (fax)"),
    "EVID_MISSING_REPORT": ("Request specific report", INTAKE, f"{COMMS} (fax)"),
    "EVID_HMO1": ("Request current resting ECG report (HMO-1)", INTAKE, f"{COMMS} (fax)"),
    "EVID_NOT_MET": ("Do not prepare PA. Review with ordering clinician (not met is not a payer denial)", AUTH, QRE),
    "PA_READY": ("Prepare PA submission packet", AUTH, PAM),
    "PA_AWAITING": ("Track payer decision", AUTH, PAM),
    "PA_DENIED": ("Payer denied (SIMULATED). Review next steps with ordering clinician", AUTH, PAM),
    "SCHED_READY": ("Send scheduling handoff", SCHED, "handoff"),
}


def _act(code: str, detail: str | None = None) -> NextAction:
    action, owner, product = CATALOG[code]
    return NextAction(reason_code=code, action=action, owner_role=owner, tennr_product=product,
                      detail=detail)


def build_actions(*, priority: RuleResult, identity: RuleResult, coverage: RuleResult,
                  plan: RuleResult, pa_requirement: str, criteria: dict[str, RuleResult],
                  events: list[SimEvent], workflow: Workflow, pa_prep_ready: bool,
                  sendable: bool) -> list[NextAction]:
    out: list[NextAction] = []

    # 1. Clinical (never waits on admin work)
    clin = latest(events, "CLINICAL_REVIEW_DECISION")
    if clin is None:
        if priority.reason_code:
            out.append(_act(priority.reason_code, priority.reason_text))
        elif workflow.routine_requires_clinical_clearance:
            out.append(_act("CLIN_ROUTINE_REVIEW"))
    elif clin.decision == "reviewed_hold":
        out.append(_act("CLIN_REVIEWED_HOLD", clin.note))
    elif clin.decision == "not_cleared":
        out.append(_act("CLIN_NOT_CLEARED", clin.note))

    # 2. Identity
    if identity.reason_code:
        out.append(_act(identity.reason_code, identity.reason_text))

    # 3. Coverage and plan product
    if coverage.reason_code:
        out.append(_act(coverage.reason_code, coverage.reason_text))
    if pa_requirement == "unknown" and coverage.reason_code != "COV_PENDING_PRODUCT":
        out.append(_act("PA_UNKNOWN", plan.reason_text))

    # 4. Evidence — only rules that gate for this plan (PPO: A2 advisory rules excluded)
    gating = (workflow.no_pa_evidence_gate if pa_requirement == "not_required"
              else ["DOC-1", "QUAL-1", "QUAL-2", "REPEAT-1", "HMO-1"])
    rules = {k: criteria[k] for k in gating if criteria[k].status != "NA"}
    if any(r.status == "NM" for r in rules.values()):
        nm = [k for k, r in rules.items() if r.status == "NM"]
        out.append(_act("EVID_NOT_MET", f"Not met: {', '.join(nm)}"))
    else:
        missing: dict[str, list[str]] = {}
        no_order = criteria["DOC-1"].reason_code == "EVID_MISSING_ORDER"
        for k, r in rules.items():
            if r.status != "I" or not r.reason_code:
                continue
            if no_order and k != "DOC-1" and r.reason_code == "EVID_MISSING_ORDER":
                continue  # one order request covers DOC-1 and QUAL-1
            missing.setdefault(r.reason_code, []).append(f"{k}: {r.detail.get('missing', r.reason_text)}")
        for code, items in missing.items():
            out.append(_act(code, "; ".join(items)))

    # 5. PA lifecycle
    if pa_prep_ready:
        pa = latest(events, "PAYER_PA_DECISION")
        if pa is None:
            out.append(_act("PA_READY"))
        elif pa.decision == "pended":
            out.append(_act("PA_AWAITING", pa.note))
        elif pa.decision == "denied":
            out.append(_act("PA_DENIED", pa.note))

    # 6. Scheduling
    if sendable:
        out.append(_act("SCHED_READY"))
    return out
