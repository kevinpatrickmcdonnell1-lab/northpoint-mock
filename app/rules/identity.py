"""ID-1 identity (PLAN §7.2). Each identifier type is compared only with itself."""
from __future__ import annotations

import re

from app.models import Document, ExtractionFile, RuleResult, SimEvent
from app.policy import Policy
from app.rules.common import event_ref, id_ref, of_type

MEMBER_SOURCES = {"registration_face_sheet", "insurance_card", "benefits_response"}
FIELDS = ("name", "dob", "mrn", "packet_id", "member_id")


def normalize_name(name: str) -> tuple[str, ...]:
    """Ignore case, whitespace, punctuation and single-letter tokens (middle initials).
    Token order and every other difference (nicknames) still count (A5)."""
    tokens = re.sub(r"[^\w\s]", " ", name.lower()).split()
    return tuple(t for t in tokens if len(t) > 1)


def _norm(field: str, value: str) -> object:
    return normalize_name(value) if field == "name" else str(value).strip().upper()


def evaluate_identity(ef: ExtractionFile, docs: list[Document], events: list[SimEvent],
                      policy: Policy) -> RuleResult:
    resolutions: dict[str, SimEvent] = {}
    for ev in events:
        if ev.type == "IDENTITY_RESOLUTION":
            for f in ev.confirmed or {}:
                resolutions[f] = ev            # latest confirmation per field wins

    fields: dict[str, dict] = {}
    for field in FIELDS:
        values = []
        for d in docs:
            if field == "member_id" and d.doc_type not in MEMBER_SOURCES:
                continue
            v = getattr(d.identifiers, field)
            if v is None:
                continue
            values.append({"value": str(v), "file": ef.source_file, "page": d.pages[0],
                           "doc_id": d.doc_id, "doc_type": d.doc_type})
        distinct = {_norm(field, v["value"]) for v in values}
        entry: dict = {"values": values}
        if len(distinct) > 1:
            entry["status"] = "conflicting"
            ev = resolutions.get(field)
            if ev is not None:
                entry.update(status="resolved", confirmed=ev.confirmed[field],
                             event_id=ev.event_id, epic_reference=ev.epic_reference,
                             simulated=True)
        else:
            entry["status"] = "consistent" if values else "absent"
        fields[field] = entry

    conflicts = [f for f, e in fields.items() if e["status"] == "conflicting"]
    has_registration = bool(of_type(docs, "registration_face_sheet"))
    name_types = {v["doc_type"] for v in fields["name"]["values"]}
    dob_types = {v["doc_type"] for v in fields["dob"]["values"]}
    detail = {"fields": fields}

    if conflicts:
        refs = [id_ref(ef, next(d for d in docs if d.doc_id == v["doc_id"]), f, v["value"])
                for f in conflicts for v in fields[f]["values"]]
        def group(f):
            by: dict[str, list[str]] = {}
            for v in fields[f]["values"]:
                pages = by.setdefault(v["value"], [])
                if str(v["page"]) not in pages:
                    pages.append(str(v["page"]))
            return " vs ".join(f"{val} ({ef.source_file} p.{', '.join(pages)})" for val, pages in by.items())
        listing = "; ".join(f"{f}: {group(f)}" for f in conflicts)
        return RuleResult(rule_id="ID-1", status="conflicting", reason_code="ID_CONFLICT",
                          reason_text=f"Conflicting identifiers, not auto-selected: {listing}. "
                                      "Verify in Epic.", evidence_refs=refs, detail=detail)
    if not has_registration or len(name_types) < 2 or len(dob_types) < 2:
        missing = [] if has_registration else ["registration face sheet"]
        if len(name_types) < 2:
            missing.append("name on a second source type")
        if len(dob_types) < 2:
            missing.append("DOB on a second source type")
        return RuleResult(rule_id="ID-1", status="pending", reason_code="ID_PENDING",
                          reason_text="Missing identity evidence: " + ", ".join(missing) + ".",
                          detail=detail)

    reg = of_type(docs, "registration_face_sheet")[0]
    refs = [id_ref(ef, reg, f, str(getattr(reg.identifiers, f)))
            for f in ("name", "dob") if getattr(reg.identifiers, f)]
    other = next((d for d in docs if d.doc_type != "registration_face_sheet" and d.identifiers.dob), None)
    if other:
        refs.append(id_ref(ef, other, "dob", str(other.identifiers.dob)))
    refs += [event_ref(resolutions[f]) for f, e in fields.items() if e["status"] == "resolved"]
    resolved = [f for f, e in fields.items() if e["status"] == "resolved"]
    text = ("Name, DOB and every same-type identifier agree across "
            f"{len(name_types | dob_types)} source types (packet-internal; Epic is the authority).")
    if resolved:
        text += f" Conflicts on {', '.join(resolved)} resolved by Intake in Epic (SIMULATED)."
    return RuleResult(rule_id="ID-1", status="verified", reason_text=text, evidence_refs=refs,
                      detail=detail)
