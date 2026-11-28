"""Personas: who is using the app, what lands in their work list, and what they may record.

Display and permission layer only. The engine never reads this (invariant 1). With no persona
chosen the app runs in presenter mode (every control visible, role picked per form), which is
how the demo has always worked. With a persona chosen, every write is attributed to that
persona's role and name by the server, whatever the form says.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.actions import AUTH, INTAKE, REVIEW, SCHED, TRIAGE

COOKIE = "np_persona"


@dataclass(frozen=True)
class Persona:
    key: str
    role: str
    name: str                      # fictional (SYNTHETIC)
    blurb: str
    work_roles: tuple[str, ...] = ()        # action owner roles whose items land in "My work"
    clinical_only: bool = False             # only CLIN_* items (cardiologist)
    fact_flags: Optional[str] = None        # "clinical" | "admin" | None: which fact flags to show
    landing: str = "/work"
    read_only: bool = False
    clinical: bool = False                  # case page hides plan/coverage detail (invariant 5)


PERSONAS: dict[str, Persona] = {p.key: p for p in [
    Persona("triage", TRIAGE, "Mara Quill, RN",
            "Urgent and undetermined clinical priority. Never waits on insurance.",
            work_roles=(TRIAGE,), fact_flags="clinical", clinical=True),
    Persona("review", REVIEW, "Theo Lund, RN",
            "Expedited and routine clinical review before scheduling.",
            work_roles=(REVIEW,), fact_flags="clinical", clinical=True),
    Persona("cardiologist", "Cardiologist", "Dr. Ines Varga",
            "Clinical decisions on escalated orders.",
            work_roles=(TRIAGE, REVIEW), clinical_only=True, fact_flags="clinical", clinical=True),
    Persona("intake", INTAKE, "Jo Rivera",
            "Identity, coverage and missing documents: the exceptions the packet couldn't settle.",
            work_roles=(INTAKE,), fact_flags="admin"),
    Persona("auth", AUTH, "Sam Okoro",
            "PA packets, payer decisions, and not-met orders back to the clinician.",
            work_roles=(AUTH,)),
    Persona("scheduler", "Scheduler", "Lee Hart",
            "Orders that pass every gate, and recalls.",
            work_roles=(SCHED,)),
    Persona("ops", "Operations Manager", "Ana Petrov",
            "Bottlenecks by owner, site and reason. Read-only.",
            landing="/metrics", read_only=True),
    Persona("auditor", "QA Auditor", "Rui Castell",
            "Every fact, decision and handoff, with who did it. Read-only.",
            landing="/", read_only=True),
]}

# Which personas' roles may record each simulated event type. BENEFITS_RESPONSE is entered by
# Intake but attributed to the mock Insurance Benefits Manager (the system that returns it).
EVENT_ROLES: dict[str, tuple[str, ...]] = {
    "PAYER_PA_DECISION": (AUTH,),
    "CLINICAL_REVIEW_DECISION": (TRIAGE, REVIEW, "Cardiologist"),
    "IDENTITY_RESOLUTION": (INTAKE,),
    "BENEFITS_RESPONSE": (INTAKE,),
}
EVENT_ACTOR_ROLE = {"BENEFITS_RESPONSE": "Insurance Benefits Manager (mock)"}

HANDOFF_ROLES: dict[str, tuple[str, ...]] = {
    "schedule": ("Scheduler", INTAKE),
    "pa_packet": (AUTH,),
    "missing_info": (INTAKE,),
    "triage": (INTAKE, TRIAGE),
    "identity_task": (INTAKE,),
    "clinician_review": (AUTH,),
}

# The one-click step for a work item, where a handoff does the job.
PRIMARY_HANDOFF = {
    "CLIN_URGENT": ("triage", "Notify triage"),
    "ID_CONFLICT": ("identity_task", "Create identity task"),
    "ID_PENDING": ("identity_task", "Create identity task"),
    "EVID_MISSING_ORDER": ("missing_info", "Send request"),
    "EVID_MISSING_REPORT": ("missing_info", "Send request"),
    "EVID_HMO1": ("missing_info", "Send request"),
    "COV_INACTIVE": ("missing_info", "Send request"),
    "PA_READY": ("pa_packet", "Prepare PA packet"),
    "EVID_NOT_MET": ("clinician_review", "Send for clinician review"),
    "PA_DENIED": ("clinician_review", "Send for clinician review"),
    "SCHED_READY": ("schedule", "Send to scheduling"),
}

# Work order: urgent first, then undetermined (it may be urgent), expedited, routine.
PRIORITY_ORDER = {"PRI-1": 0, "PRI-4": 1, "PRI-2": 2, "PRI-3": 3}


def get(key: Optional[str]) -> Optional[Persona]:
    return PERSONAS.get(key or "")


def label(p: Persona) -> str:
    return f"{p.name} ({p.role})"


def may_record(p: Optional[Persona], event_type: str) -> bool:
    return p is None or (not p.read_only and p.role in EVENT_ROLES.get(event_type, ()))


def may_send(p: Optional[Persona], kind: str) -> bool:
    return p is None or (not p.read_only and p.role in HANDOFF_ROLES.get(kind, ()))


def owns(p: Persona, action) -> bool:
    if action.owner_role not in p.work_roles:
        return False
    return not p.clinical_only or action.reason_code.startswith("CLIN_")
