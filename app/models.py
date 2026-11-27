"""Pydantic v2 models.

Three groups, kept strictly apart (CLAUDE.md invariants 2-4):
  1. Extraction facts   - what a page says. extra="forbid"; derived keys are rejected.
  2. Simulated events   - created only by explicit UI action; simulated is always True.
  3. Derived state      - rule results, gates, actions, CaseState. Never persisted as truth.
"""
from __future__ import annotations

from datetime import date, datetime

Date = date  # alias: fields named `date` shadow the type inside a class body
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, SerializeAsAny, model_validator

# Keys that would smuggle a derived outcome into an extraction file (invariant 2).
# extra="forbid" already rejects them; this list makes the intent explicit and is
# also used by check_extractions.py / test_invariants.py to scan raw JSON.
DERIVED_KEYS = frozenset({
    "priority", "tier", "status", "verified", "pa_required", "ready", "sendable",
    "outcome", "result", "decision", "approved", "cleared", "coverage_status",
    "identity_status", "evidence_status", "expected",
})


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------- #
# 1. Extraction facts                                                          #
# --------------------------------------------------------------------------- #

DocType = Literal[
    "referral", "stress_echo_order", "clinician_note", "registration_face_sheet",
    "insurance_card", "benefits_response", "resting_ecg_report",
    "exercise_ecg_report", "ecg_tracing_raw", "other",
]
UrgentReason = Literal["new_symptoms_at_rest", "high_risk_finding", "other_immediate_concern"]
Trajectory = Literal["new", "worsening", "stable", "improving", "not_stated"]
ComplaintType = Literal[
    "exertional_chest_discomfort", "exertional_dyspnea", "nonexertional", "other", "not_stated"
]
IschemiaConcern = Literal["explicit_yes", "explicit_no", "not_stated"]


class FieldsBase(Strict):
    """Base for per-doc-type `fields`. Unlisted facts default to false / not_stated / null."""


class PriorityFields(FieldsBase):
    urgent_review_requested: bool = False
    urgent_reason: Optional[UrgentReason] = None
    expedited_review_requested: bool = False
    material_increase_date: Optional[date] = None
    symptom_trajectory: Trajectory = "not_stated"
    timing_text: Optional[str] = None


class ReferralFields(PriorityFields):
    referral_id: Optional[str] = None
    service_requested: Literal["stress_echo", "consult"]


class StressEchoOrderFields(FieldsBase):
    procedure: Optional[str] = None
    indication: Optional[str] = None
    # What the indication text describes, classified at extraction time like a note's
    # complaint_type ("chest pressure with uphill walking" -> exertional_chest_discomfort).
    indication_type: "ComplaintType" = "not_stated"


class PriorStressEcho(Strict):
    stated: bool = False
    date: Optional[Date] = None  # stated + null date means "none"


class ExerciseEcgHistory(Strict):
    stated: bool = False
    none_since: Optional[date] = None


class ClinicianNoteFields(PriorityFields):
    is_assessment: bool = False
    assessment_complete: bool = False
    symptom_course_documented: bool = False
    relevant_history_documented: bool = False
    complaint_type: ComplaintType = "not_stated"
    ischemia_concern: IschemiaConcern = "not_stated"
    prior_stress_echo: PriorStressEcho = PriorStressEcho()
    interval_change_after_prior: Optional[bool] = None
    repeat_rationale: Optional[str] = None
    tests_mentioned_without_report: list[str] = Field(default_factory=list)
    exercise_ecg_history: ExerciseEcgHistory = ExerciseEcgHistory()


class CoverageDocFields(FieldsBase):
    payer: Optional[str] = None
    plan_product_raw: Optional[str] = None
    member_id: Optional[str] = None


class RegistrationFields(CoverageDocFields):
    pass


class InsuranceCardFields(CoverageDocFields):
    pass


class BenefitsResponseFields(CoverageDocFields):
    response_date: date
    coverage_active_on_eval_date: Optional[bool] = None
    product_end_date: Optional[date] = None


class RestingEcgFields(FieldsBase):
    test_date: date
    clinician_interpreted: bool
    interpretation: Literal["normal", "abnormal", "nondiagnostic", "nonspecific_st_t", "other"]
    complete: bool = True


class ExerciseEcgFields(FieldsBase):
    test_date: date
    clinician_interpreted: bool
    interpretation: Literal["equivocal", "normal", "negative", "positive", "other"]
    complete: bool


class EcgTracingRawFields(FieldsBase):
    """Never used by any rule (invariant 5). Shown as 'present, not used'."""
    test_date: Optional[date] = None
    machine_statement: Optional[str] = None


class OtherFields(FieldsBase):
    text: Optional[str] = None


FIELD_MODELS: dict[str, type[FieldsBase]] = {
    "referral": ReferralFields,
    "stress_echo_order": StressEchoOrderFields,
    "clinician_note": ClinicianNoteFields,
    "registration_face_sheet": RegistrationFields,
    "insurance_card": InsuranceCardFields,
    "benefits_response": BenefitsResponseFields,
    "resting_ecg_report": RestingEcgFields,
    "exercise_ecg_report": ExerciseEcgFields,
    "ecg_tracing_raw": EcgTracingRawFields,
    "other": OtherFields,
}


class Identifiers(Strict):
    name: Optional[str] = None
    dob: Optional[date] = None
    mrn: Optional[str] = None
    packet_id: Optional[str] = None
    member_id: Optional[str] = None


class Quote(Strict):
    field: str
    page: int
    text: str


class Document(Strict):
    doc_id: str
    doc_type: DocType
    pages: list[int] = Field(min_length=1)
    date: Optional[Date] = None
    signed: bool = False
    signer: Optional[str] = None
    identifiers: Identifiers = Identifiers()
    fields: SerializeAsAny[FieldsBase]
    quotes: list[Quote] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _parse_fields_by_type(cls, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(data.get("fields", {}), dict):
            model = FIELD_MODELS.get(data.get("doc_type"))
            if model is not None:
                data = {**data, "fields": model.model_validate(data.get("fields", {}))}
        return data

    @model_validator(mode="after")
    def _quotes_on_own_pages(self) -> "Document":
        for q in self.quotes:
            if q.page not in self.pages:
                raise ValueError(f"{self.doc_id}: quote cites page {q.page}, not in {self.pages}")
        return self


class Correction(Strict):
    doc_id: str
    field: str
    extracted: Any
    corrected: Any
    page: int
    reason: str
    by: str
    at: date


class ExtractionFile(Strict):
    packet_id: str
    patient_key: str
    source_file: str
    received_at: date
    received_channel: Literal["fax", "epic_wq", "not_stated"]
    ordering_site: Optional[str] = None   # intake metadata (Stretch: site variance); never used by rules
    extraction_method: Literal["mock_authored", "llm_draft_unverified", "llm_draft_human_verified",
                               "manual"]
    documents: list[Document]
    corrections: list[Correction] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_doc_ids(self) -> "ExtractionFile":
        ids = [d.doc_id for d in self.documents]
        if len(ids) != len(set(ids)):
            raise ValueError(f"{self.patient_key}: duplicate doc_id")
        return self


def find_derived_keys(obj: Any, path: str = "") -> list[str]:
    """Scan raw (pre-validation) JSON for forbidden derived keys at any depth."""
    hits: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else k
            if k in DERIVED_KEYS:
                hits.append(p)
            hits.extend(find_derived_keys(v, p))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            hits.extend(find_derived_keys(v, f"{path}[{i}]"))
    return hits


# --------------------------------------------------------------------------- #
# 2. Simulated events (separate, append-only; engine never creates them)       #
# --------------------------------------------------------------------------- #

EventType = Literal[
    "PAYER_PA_DECISION", "CLINICAL_REVIEW_DECISION", "IDENTITY_RESOLUTION", "BENEFITS_RESPONSE"
]
_DECISIONS: dict[str, set[Optional[str]]] = {
    "PAYER_PA_DECISION": {"approved", "denied", "pended"},
    "CLINICAL_REVIEW_DECISION": {"cleared_for_scheduling", "not_cleared", "reviewed_hold"},
    "IDENTITY_RESOLUTION": {"confirmed"},
    "BENEFITS_RESPONSE": {"received"},
}


class SimEvent(Strict):
    event_id: str
    patient_key: str
    type: EventType
    decision: str
    actor_role: str
    actor_name: Optional[str] = None
    event_time: datetime          # simulated business clock
    recorded_at: datetime         # wall clock
    note: Optional[str] = None
    simulated: Literal[True] = True
    # IDENTITY_RESOLUTION: field -> human-confirmed value, plus Epic reference
    confirmed: Optional[dict[str, str]] = None
    epic_reference: Optional[str] = None
    # BENEFITS_RESPONSE: same schema as a benefits_response document's fields
    benefits: Optional[BenefitsResponseFields] = None
    # PAYER_PA_DECISION: the pa_request_packet handoff this decision answers (Fix 2)
    pa_handoff_id: Optional[str] = None

    @model_validator(mode="after")
    def _shape_by_type(self) -> "SimEvent":
        if self.decision not in _DECISIONS[self.type]:
            raise ValueError(f"{self.type}: invalid decision {self.decision!r}")
        if self.pa_handoff_id is not None and self.type != "PAYER_PA_DECISION":
            raise ValueError("pa_handoff_id is only valid on PAYER_PA_DECISION")
        if self.type == "IDENTITY_RESOLUTION":
            if not self.confirmed or not self.epic_reference or not self.note:
                raise ValueError("IDENTITY_RESOLUTION needs confirmed values, epic_reference and a note")
            bad = set(self.confirmed) - {"name", "dob", "mrn", "packet_id", "member_id"}
            if bad:
                raise ValueError(f"IDENTITY_RESOLUTION: unknown identifier fields {bad}")
        if self.type == "BENEFITS_RESPONSE" and self.benefits is None:
            raise ValueError("BENEFITS_RESPONSE needs a benefits payload")
        return self


# --------------------------------------------------------------------------- #
# 3. Derived state (output of evaluate(); never stored as truth)               #
# --------------------------------------------------------------------------- #

RuleStatus = Literal["S", "NM", "I", "NA"]  # supported | not met | insufficient | not applicable


class EvidenceRef(Strict):
    file: str
    page: int
    quote: Optional[str] = None
    doc_id: Optional[str] = None
    event_id: Optional[str] = None   # set when the basis is a SIMULATED event, not a PDF


class RuleResult(Strict):
    rule_id: str
    status: str                       # rule-specific vocabulary (S/NM/I, verified/pending/…)
    reason_code: Optional[str] = None
    reason_text: str
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    detail: dict[str, Any] = Field(default_factory=dict)


class GateResult(Strict):
    gate: Literal["G-ID", "G-COV", "G-PAREQ", "G-EVID", "G-CLIN", "G-PA-APPROVED"]
    passed: bool
    basis: str
    blocks: list[Literal["pa_prep", "scheduling"]]


class NextAction(Strict):
    reason_code: str
    action: str
    owner_role: str
    tennr_product: str
    detail: Optional[str] = None


class CaseState(Strict):
    patient_key: str
    packet_id: str
    policy_version: str
    eval_date: date
    priority: RuleResult
    identity: RuleResult
    plan: RuleResult
    coverage: RuleResult
    pa_requirement: Literal["required", "not_required", "unknown"]
    criteria: dict[str, RuleResult]   # DOC-1, QUAL-1, QUAL-2, REPEAT-1, HMO-1, EVIDENCE
    gates: list[GateResult]
    pa_prep_ready: bool
    scheduling_sendable: bool
    admin_ready: bool
    actions: list[NextAction]
    simulated_event_ids: list[str] = Field(default_factory=list)
