"""Human review of extracted facts: confirmations, corrections and fact checks.

Three pieces, all outside the engine:

1. `FactReview` - a person confirms that a fact matches its page, or corrects it. A
   correction must quote the page text that supports the new value (found on that page), give
   a reason and name a role. Reviews are append-only rows; the extraction stored in `cases`
   never changes (invariant 4).
2. `apply_reviews()` - builds the *reviewed* view of an extraction: the source facts with
   each correction laid over them. The engine evaluates that view, so a correction changes
   results only through the same rules as every other fact.
3. `check_facts()` - cheap checks that look for facts whose quote doesn't back them (the
   R01 signer quote that cited a date is the motivating case). A flag only routes a fact to
   a person. It never changes a value and the engine never sees it.
"""
from __future__ import annotations

import copy
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, ValidationError

from app.models import (FIELD_MODELS, ClinicianNoteFields, ExtractionFile, PriorityFields,
                        RestingEcgFields, ExerciseEcgFields, Strict)
from app.pdfs import PAGES, _norm

# Doc-level facts a reviewer may correct, besides the doc type's own `fields`.
DOC_LEVEL = ("date", "signed", "signer")
# Identifier disagreements are resolved by IDENTITY_RESOLUTION on the case page (invariant 7),
# never by editing what a page says.
IDENTIFIER_FIELDS = ("name", "dob", "mrn", "packet_id")

REVIEW_ROLES = ["Intake Coordinator", "Auth Specialist", "Clinical Review RN",
                "Clinical Triage RN", "Cardiologist"]
CLINICAL_ROLES = ["Clinical Review RN", "Clinical Triage RN", "Cardiologist"]
# Facts that set clinical priority or describe clinical findings: only clinical roles may
# confirm or correct them. Admin facts (dates, signer, coverage, procedure name) any role may.
CLINICAL_FIELDS = frozenset(
    set(PriorityFields.model_fields) - {"timing_text"}
    | (set(ClinicianNoteFields.model_fields) - set(PriorityFields.model_fields))
    | set(RestingEcgFields.model_fields) | set(ExerciseEcgFields.model_fields)
    | {"indication", "indication_type"}
) - {"test_date"}

# Text fields an extractor copies from the page (not classifies), so the value should appear in
# its own quote. Classified fields (enums, booleans) are paraphrases by design.
COPIED_TEXT_FIELDS = ("procedure", "timing_text", "plan_product_raw", "member_id")


class FactReview(Strict):
    review_id: str
    patient_key: str
    doc_id: str
    field: str
    action: Literal["confirm", "correct"]
    value: Any = None                    # correct only: the corrected value
    quote_text: Optional[str] = None     # correct only: page text that supports the value
    quote_page: Optional[int] = None
    reason: Optional[str] = None
    actor_role: str
    actor_name: str
    recorded_at: datetime


class Flag(BaseModel):
    model_config = ConfigDict(frozen=True)
    doc_id: str
    field: str
    code: Literal["QUOTE_NOT_ON_PAGE", "SIGNER_NAME_NOT_IN_QUOTE", "DATE_NOT_IN_QUOTE",
                  "VALUE_NOT_IN_QUOTE"]
    page: int
    message: str


# --------------------------------------------------------------------------- what can be reviewed

def correctable_fields(doc_type: str) -> list[str]:
    return list(DOC_LEVEL) + list(FIELD_MODELS[doc_type].model_fields)


def current_value(doc, field: str) -> Any:
    if field in DOC_LEVEL:
        return getattr(doc, field)
    v = getattr(doc.fields, field, None)
    return v.model_dump(mode="json") if isinstance(v, BaseModel) else v


def is_clinical(field: str) -> bool:
    return field in CLINICAL_FIELDS


def parse_value(raw: str) -> list[Any]:
    """Candidate values for a form string, tried in order: empty -> null, the plain string
    (str / date / enum fields), then JSON (booleans, lists, nested objects)."""
    s = raw.strip()
    if s == "" or s.lower() == "null":
        return [None]
    out: list[Any] = [s]
    try:
        out.append(json.loads(s))
    except ValueError:
        pass
    return out


# --------------------------------------------------------------------------- reviewed view

def _set(doc: dict, field: str, value: Any) -> None:
    if field in DOC_LEVEL:
        doc[field] = value
    else:
        doc["fields"][field] = value


def apply_reviews(ef: ExtractionFile, reviews: list[FactReview]) -> ExtractionFile:
    """The source facts with every correction laid over them, in the order recorded. A
    correction replaces that field's value and its quotes (the reviewer's quote becomes the
    evidence). Confirmations change nothing. The source `ef` is not modified."""
    corrections = [r for r in reviews if r.action == "correct" and r.patient_key == ef.patient_key]
    if not corrections:
        return ef
    data = copy.deepcopy(ef.model_dump(mode="json"))
    docs = {d["doc_id"]: d for d in data["documents"]}
    for r in corrections:
        d = docs[r.doc_id]
        _set(d, r.field, r.value)
        d["quotes"] = [q for q in d["quotes"] if q["field"] != r.field]
        d["quotes"].append({"field": r.field, "page": r.quote_page, "text": r.quote_text})
    return ExtractionFile.model_validate(data)


# --------------------------------------------------------------------------- fact checks

_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september",
     "october", "november", "december"], 1)}
_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_US = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_LONG = re.compile(r"\b(" + "|".join(_MONTHS) + r")\s+(\d{1,2})(?:,?\s+(\d{4}))?\b", re.I)


def dates_in(text: str) -> list[tuple[Optional[int], int, int]]:
    """(year or None, month, day) for every date written in the text."""
    out: list[tuple[Optional[int], int, int]] = []
    out += [(int(y), int(m), int(d)) for y, m, d in _ISO.findall(text)]
    out += [(int(y), int(m), int(d)) for m, d, y in _US.findall(text)]
    out += [(int(y) if y else None, _MONTHS[mo.lower()], int(d)) for mo, d, y in _LONG.findall(text)]
    return out


def _date_matches(value: date, found: tuple[Optional[int], int, int]) -> bool:
    y, m, d = found
    return (m, d) == (value.month, value.day) and y in (None, value.year)


def signer_surname(signer: Optional[str]) -> Optional[str]:
    """'Rohan Devlin, MD' -> 'Devlin'. None when the signer isn't written as 'Name, credential'
    (e.g. 'final clinician interpretation (per report)')."""
    if not signer or "," not in signer:
        return None
    parts = signer.split(",")[0].split()
    return parts[-1] if parts else None


def page_text(file: str, page: int, pages_root: Path = PAGES) -> Optional[str]:
    p = pages_root / file / f"{page}.txt"
    return p.read_text() if p.exists() else None


def quote_on_page(text: str, file: str, page: int, pages_root: Path = PAGES) -> bool:
    t = page_text(file, page, pages_root)
    return t is not None and _norm(text) in _norm(t)


def check_facts(ef: ExtractionFile, pages_root: Path = PAGES) -> list[Flag]:
    flags: list[Flag] = []
    for d in ef.documents:
        by_field: dict[str, list] = {}
        for q in d.quotes:
            by_field.setdefault(q.field, []).append(q)
            if page_text(ef.source_file, q.page, pages_root) is not None and \
                    not quote_on_page(q.text, ef.source_file, q.page, pages_root):
                flags.append(Flag(doc_id=d.doc_id, field=q.field, code="QUOTE_NOT_ON_PAGE",
                                  page=q.page, message=f"Quote not found on p.{q.page}: “{q.text}”"))
        # The signer's name should be in the quote that backs the signature.
        name = signer_surname(d.signer)
        sq = by_field.get("signer", [])
        if name and sq and not any(name.lower() in q.text.lower() for q in sq):
            flags.append(Flag(doc_id=d.doc_id, field="signer", code="SIGNER_NAME_NOT_IN_QUOTE",
                              page=sq[0].page,
                              message=f"Signer is “{d.signer}” but the signature quote "
                                      f"(“{sq[0].text}”) doesn't name them."))
        # A date fact whose quote states a different date.
        dated = {"date": d.date} | {f: getattr(d.fields, f) for f in type(d.fields).model_fields
                                    if isinstance(getattr(d.fields, f), date)}
        for f, v in dated.items():
            for q in by_field.get(f, []):
                found = dates_in(q.text)
                if v is not None and found and not any(_date_matches(v, x) for x in found):
                    flags.append(Flag(doc_id=d.doc_id, field=f, code="DATE_NOT_IN_QUOTE", page=q.page,
                                      message=f"{f} is {v} but its quote states another date: “{q.text}”"))
        # Copied text should appear in its own quote.
        for f in COPIED_TEXT_FIELDS:
            v = getattr(d.fields, f, None)
            qs = by_field.get(f, [])
            if isinstance(v, str) and v and qs and \
                    not any(_norm(v).lower() in _norm(q.text).lower() for q in qs):
                flags.append(Flag(doc_id=d.doc_id, field=f, code="VALUE_NOT_IN_QUOTE", page=qs[0].page,
                                  message=f"{f} is “{v}” but its quote (“{qs[0].text}”) doesn't contain it."))
    return flags


def open_flags(flags: list[Flag], reviews: list[FactReview]) -> list[Flag]:
    """A flag stays open until a person has reviewed that fact (confirmed or corrected it).
    A correction that fixes the quote also makes the flag disappear when checks re-run."""
    reviewed = {(r.doc_id, r.field) for r in reviews}
    return [f for f in flags if (f.doc_id, f.field) not in reviewed]


def latest_review(reviews: list[FactReview], doc_id: str, field: str) -> Optional[FactReview]:
    hits = [r for r in reviews if r.doc_id == doc_id and r.field == field]
    return hits[-1] if hits else None


def validate_correction(ef_reviewed: ExtractionFile, doc_id: str, field: str, raw: str,
                        quote_page: int, quote_text: str) -> Any:
    """Return the parsed value if the corrected extraction validates, else raise ValueError."""
    last: Optional[Exception] = None
    for candidate in parse_value(raw):
        probe = FactReview(review_id="probe", patient_key=ef_reviewed.patient_key, doc_id=doc_id,
                           field=field, action="correct", value=candidate, quote_text=quote_text,
                           quote_page=quote_page, actor_role="probe", actor_name="probe",
                           recorded_at=datetime(2000, 1, 1))
        try:
            fixed = apply_reviews(ef_reviewed, [probe])
        except ValidationError as e:
            last = e
            continue
        return current_value(next(d for d in fixed.documents if d.doc_id == doc_id), field)
    msg = last.errors()[0]["msg"] if isinstance(last, ValidationError) else str(last)
    raise ValueError(f"“{raw}” is not a valid value for {field}: {msg}")
