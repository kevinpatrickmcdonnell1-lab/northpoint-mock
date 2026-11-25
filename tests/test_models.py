"""Phase 0: models validate the PLAN §5.1 sample and reject derived fields."""
import copy

import pytest
from pydantic import ValidationError

from app.models import (
    ClinicianNoteFields, ExtractionFile, SimEvent, StressEchoOrderFields, find_derived_keys,
)
from app.policy import load_policy, load_workflow

SAMPLE = {
    "packet_id": "NP-PKT-0001",
    "patient_key": "P01",
    "source_file": "P01_packet.pdf",
    "received_at": "2026-09-18",
    "received_channel": "fax",
    "extraction_method": "mock_authored",
    "documents": [{
        "doc_id": "P01-D2",
        "doc_type": "stress_echo_order",
        "pages": [2],
        "date": "2026-09-18",
        "signed": True,
        "signer": "R. Osei, MD",
        "identifiers": {"name": "Martin Alder", "dob": "1954-02-11", "mrn": "NP-100231",
                        "packet_id": "NP-PKT-0001", "member_id": None},
        "fields": {"procedure": "Outpatient stress echocardiogram",
                   "indication": "exertional chest discomfort"},
        "quotes": [{"field": "indication", "page": 2,
                    "text": "Indication: exertional chest discomfort"}],
    }],
    "corrections": [{"doc_id": "P01-D2", "field": "indication", "extracted": "x",
                     "corrected": "y", "page": 2, "reason": "OCR misread 8 as 3",
                     "by": "Kevin", "at": "2026-11-26"}],
}


def test_sample_validates_and_types_fields():
    ef = ExtractionFile.model_validate(SAMPLE)
    assert isinstance(ef.documents[0].fields, StressEchoOrderFields)
    # round-trips without losing per-type fields
    assert ExtractionFile.model_validate(ef.model_dump(mode="json")) == ef


@pytest.mark.parametrize("where", ["top", "doc", "fields", "identifiers"])
@pytest.mark.parametrize("key", ["priority", "status", "verified", "pa_required", "ready"])
def test_derived_keys_rejected(where, key):
    data = copy.deepcopy(SAMPLE)
    target = {"top": data, "doc": data["documents"][0],
              "fields": data["documents"][0]["fields"],
              "identifiers": data["documents"][0]["identifiers"]}[where]
    target[key] = "anything"
    assert find_derived_keys(data)
    with pytest.raises(ValidationError):
        ExtractionFile.model_validate(data)


def test_fields_checked_against_doc_type():
    data = copy.deepcopy(SAMPLE)
    data["documents"][0]["fields"]["member_id"] = "SCM-1"   # not an order field
    with pytest.raises(ValidationError):
        ExtractionFile.model_validate(data)


def test_quote_must_cite_own_page():
    data = copy.deepcopy(SAMPLE)
    data["documents"][0]["quotes"][0]["page"] = 5
    with pytest.raises(ValidationError):
        ExtractionFile.model_validate(data)


def test_clinician_note_defaults_are_not_stated():
    f = ClinicianNoteFields()
    assert f.complaint_type == "not_stated" and f.ischemia_concern == "not_stated"
    assert f.prior_stress_echo.stated is False and f.urgent_review_requested is False


def test_sim_event_always_simulated_and_shaped():
    base = dict(event_id="EV-1", patient_key="P01", type="PAYER_PA_DECISION",
                decision="approved", actor_role="Auth Specialist",
                event_time="2026-10-01T09:00", recorded_at="2026-11-24T10:00")
    assert SimEvent.model_validate(base).simulated is True
    with pytest.raises(ValidationError):
        SimEvent.model_validate({**base, "simulated": False})
    with pytest.raises(ValidationError):
        SimEvent.model_validate({**base, "decision": "cleared_for_scheduling"})
    with pytest.raises(ValidationError):  # identity resolution needs values, Epic ref and note
        SimEvent.model_validate({**base, "type": "IDENTITY_RESOLUTION", "decision": "confirmed"})


def test_configs_load():
    p, w = load_policy(), load_workflow()
    assert p.policy_version == "SC-CARD-1.1" and str(p.eval_date) == "2026-10-01"
    assert p.resolve_product("Summit Crest Medicare Advantage") == "PLAN-SCMA"
    assert p.resolve_product("Harborstone Health Commercial") is None
    assert p.plans["PLAN-HSPPO"].pa == "not_required"
    assert w.routine_requires_clinical_clearance is False and w.no_pa_evidence_gate == ["DOC-1"]


def test_date_named_fields_accept_dates():
    # Regression (build_log 2026-11-24): a field named `date` shadowed the type.
    f = ClinicianNoteFields(prior_stress_echo={"stated": True, "date": "2026-06-11"})
    assert str(f.prior_stress_echo.date) == "2026-06-11"
