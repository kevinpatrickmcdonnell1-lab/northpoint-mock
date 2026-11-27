"""Shared helpers for rules. Pure functions: no I/O, no clock."""
from __future__ import annotations

from datetime import date
from typing import Optional

from app.models import Document, EvidenceRef, ExtractionFile, SimEvent
from app.policy import Policy

SIGNATURE_REQUIRED = {"referral", "stress_echo_order", "clinician_note",
                      "resting_ecg_report", "exercise_ecg_report"}
ADMIN_TYPES = {"registration_face_sheet", "insurance_card", "benefits_response"}
NEVER_USED = {"ecg_tracing_raw", "other"}

ID_LABELS = {"name": "Patient", "dob": "DOB", "mrn": "MRN",
             "packet_id": "Packet ID", "member_id": "Member ID"}


def usable_docs(ef: ExtractionFile, policy: Policy) -> list[Document]:
    """Documents any rule may see (PLAN §7.2):
    - never ecg_tracing_raw / other;
    - clinical documents only when signed (admin records carry no clinician signature);
    - only documents dated on or before the evaluation date."""
    out = []
    for d in ef.documents:
        if d.doc_type in NEVER_USED:
            continue
        if d.doc_type in SIGNATURE_REQUIRED and not d.signed:
            continue
        if d.date is not None and d.date > policy.eval_date:
            continue
        out.append(d)
    return out


def why_unused(d: Document, policy: Policy) -> Optional[str]:
    """UI label for documents excluded from every rule (None when used)."""
    if d.doc_type in NEVER_USED:
        return "present, not used (raw tracing)" if d.doc_type == "ecg_tracing_raw" else "present, not used"
    if d.doc_type in SIGNATURE_REQUIRED and not d.signed:
        return "unsigned, not used as evidence"
    if d.date is not None and d.date > policy.eval_date:
        return "dated after evaluation date, not used"
    return None


def of_type(docs: list[Document], *types: str) -> list[Document]:
    return [d for d in docs if d.doc_type in types]


def ref(ef: ExtractionFile, doc: Document, field: Optional[str] = None) -> EvidenceRef:
    """Cite `field` on `doc`. Only a quote recorded FOR that field is used (Fix 8): with none,
    the ref points at the document's first page with no quote, never at another field's quote."""
    quote = next((q for q in doc.quotes if q.field == field), None) if field else None
    return EvidenceRef(file=ef.source_file, page=quote.page if quote else doc.pages[0],
                       quote=quote.text if quote else None, doc_id=doc.doc_id, field=field)


def id_ref(ef: ExtractionFile, doc: Document, field: str, value: str) -> EvidenceRef:
    """Identifier lines are printed as '<Label>: <value>' on every page."""
    return EvidenceRef(file=ef.source_file, page=doc.pages[0],
                       quote=f"{ID_LABELS[field]}: {value}", doc_id=doc.doc_id)


def event_ref(ev: SimEvent) -> EvidenceRef:
    return EvidenceRef(file="SIMULATED – not from PDF", page=0,
                       quote=f"{ev.type} {ev.decision} by {ev.actor_role}", event_id=ev.event_id)


def latest(events: list[SimEvent], type_: str) -> Optional[SimEvent]:
    evs = [e for e in events if e.type == type_]
    return evs[-1] if evs else None


def in_window(d: Optional[date], start: date, end: date) -> bool:
    return d is not None and start <= d <= end
