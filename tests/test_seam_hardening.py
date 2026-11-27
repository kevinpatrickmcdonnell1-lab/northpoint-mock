"""Seam hardening (fix/seam-hardening): one reproducing test group per fix.

Each test here was written first and confirmed failing before its fix.
"""
import pytest
from pydantic import ValidationError

from app.handoffs import send
from app.models import SimEvent
from tests.helpers import docs_of, mutate, run


# --------------------------------------------------------------------------- #
# Fix 1. Older urgent/expedited request silently downgraded (A3)               #
# --------------------------------------------------------------------------- #

def _p03_older_urgent(data):
    ref = next(d for d in docs_of(data, "referral") if d["date"] == "2026-09-15")
    ref["fields"].update(urgent_review_requested=True, urgent_reason="new_symptoms_at_rest")
    note = next(d for d in docs_of(data, "clinician_note") if d["date"] == "2026-09-29")
    note["fields"].update(urgent_review_requested=False, urgent_reason=None, material_increase_date=None)


def test_fix1_older_higher_tier_is_pri4_not_downgrade():
    s = run(mutate("P03", _p03_older_urgent))
    assert s.priority.status == "PRI-4"
    assert s.priority.reason_code == "CLIN_UNDETERMINED"
    text = s.priority.reason_text
    # names both documents and their tiers
    assert "2026-09-15" in text and "2026-09-29" in text
    assert "PRI-1" in text and "PRI-3" in text
    assert {r.doc_id for r in s.priority.evidence_refs} >= {"P03-D1", "P03-D7"}


def test_fix1_older_expedited_also_unresolved():
    def fn(data):
        _p03_older_urgent(data)
        ref = next(d for d in docs_of(data, "referral") if d["date"] == "2026-09-15")
        ref["fields"].update(urgent_review_requested=False, urgent_reason=None,
                             expedited_review_requested=True)
    s = run(mutate("P03", fn))
    assert s.priority.status == "PRI-4" and "PRI-2" in s.priority.reason_text


# --------------------------------------------------------------------------- #
# Fix 2. PA decision accepted with no PA submission                            #
# --------------------------------------------------------------------------- #

def _p04_resolve(svc):
    svc.simulate("P04", "IDENTITY_RESOLUTION", "confirmed", "Intake Coordinator", note="Epic",
                 confirmed={"dob": "1951-03-14", "member_id": "SCM-302177"}, epic_reference="EPIC-1")
    svc.simulate("P04", "BENEFITS_RESPONSE", "received", "Insurance Benefits Manager (mock)",
                 benefits={"response_date": "2026-10-01", "payer": "Summit Crest",
                           "plan_product_raw": "Summit Crest Medicare Advantage",
                           "member_id": "SCM-302177", "coverage_active_on_eval_date": True})


def test_fix2_pa_decision_rejected_without_pa_packet(svc):
    with pytest.raises(ValueError, match="PA request packet"):
        svc.simulate("P04", "PAYER_PA_DECISION", "approved", "Auth Specialist")
    assert svc.store.event_count() == 0
    _p04_resolve(svc)
    s = svc.state("P04")
    assert not s.scheduling_sendable and "G-PA-APPROVED" in {g.gate for g in s.gates if not g.passed}
    assert svc.store.outbox_count("P04", "pa_request_packet") == 0


def test_fix2_pa_decision_records_pa_handoff_id(svc):
    _p04_resolve(svc)
    pa = send(svc, "P04", "pa_packet")
    assert pa["result"] == "sent"
    ev = svc.simulate("P04", "PAYER_PA_DECISION", "approved", "Auth Specialist")
    assert ev.pa_handoff_id == pa["handoff_id"]
    assert svc.store.events("P04")[-1].pa_handoff_id == pa["handoff_id"]
    assert svc.state("P04").scheduling_sendable


def test_fix2_pa_handoff_id_only_on_pa_decisions():
    from datetime import datetime
    with pytest.raises(ValidationError):
        SimEvent(event_id="EV-X", patient_key="P01", type="CLINICAL_REVIEW_DECISION",
                 decision="reviewed_hold", actor_role="Clinical Triage RN",
                 event_time=datetime(2026, 10, 1, 9), recorded_at=datetime(2026, 11, 27, 9),
                 pa_handoff_id="HO-P01-PA-1")


def test_fix2_ui_shows_rejection_not_500(svc):
    from fastapi.testclient import TestClient
    from app.main import app
    app.state.svc = svc
    try:
        with TestClient(app) as c:
            r = c.post("/case/P04/simulate", data={"type": "PAYER_PA_DECISION", "decision": "approved",
                                                    "actor_role": "Auth Specialist"})
        assert r.status_code == 200
        assert "Not recorded" in r.text and "PA request packet" in r.text
    finally:
        del app.state.svc
