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


# --------------------------------------------------------------------------- #
# Fix 3. Same order can be sent to scheduling twice                            #
# --------------------------------------------------------------------------- #

SCHED = "stress_echo_ready_to_schedule"


def test_fix3_no_second_scheduling_handoff_after_unrelated_event(svc):
    first = send(svc, "P02", "schedule")
    assert first["result"] == "sent" and first["handoff_id"] == "HO-P02-SCHED-1"
    svc.simulate("P02", "CLINICAL_REVIEW_DECISION", "cleared_for_scheduling", "Clinical Triage RN")
    assert svc.state("P02").scheduling_sendable
    again = send(svc, "P02", "schedule")
    assert again["result"] == "already_sent" and again["handoff_id"] == "HO-P02-SCHED-1"
    assert svc.store.outbox_count("P02", SCHED) == 1
    assert not (svc.outbox_dir / "HO-P02-SCHED-2.json").exists()


def test_fix3_pa_packet_behaviour_unchanged(svc):
    # A PA packet re-sent after a new simulated event is still a new packet (B17).
    assert send(svc, "P01", "pa_packet")["handoff_id"] == "HO-P01-PA-1"
    assert send(svc, "P01", "pa_packet")["result"] == "already_recorded"
    svc.simulate("P01", "CLINICAL_REVIEW_DECISION", "reviewed_hold", "Clinical Triage RN")
    assert send(svc, "P01", "pa_packet")["handoff_id"] == "HO-P01-PA-2"


def test_fix3_ui_message(svc):
    from fastapi.testclient import TestClient
    from app.main import app
    app.state.svc = svc
    try:
        with TestClient(app) as c:
            assert "Sent stress_echo" in c.post("/case/P02/handoff/schedule").text
            svc.simulate("P02", "CLINICAL_REVIEW_DECISION", "cleared_for_scheduling", "Clinical Triage RN")
            r = c.post("/case/P02/handoff/schedule")
        assert r.status_code == 200 and "Already sent to scheduling as HO-P02-SCHED-1" in r.text
    finally:
        del app.state.svc


# --------------------------------------------------------------------------- #
# Fix 4. No recall when a case loses readiness after a scheduling handoff      #
# --------------------------------------------------------------------------- #

RECALL = "scheduling_recall"


def _recalls(svc, key):
    return [o for o in svc.store.outbox(key) if o["work_item"]["type"] == RECALL]


def test_fix4_recall_full_loop(svc):
    assert send(svc, "P02", "schedule")["handoff_id"] == "HO-P02-SCHED-1"
    assert _recalls(svc, "P02") == []

    svc.simulate("P02", "CLINICAL_REVIEW_DECISION", "not_cleared", "Cardiologist", note="hold")
    assert not svc.state("P02").scheduling_sendable
    recalls = _recalls(svc, "P02")
    assert len(recalls) == 1
    wi = recalls[0]["work_item"]
    assert recalls[0]["handoff_id"] == "HO-P02-RECALL-1"
    assert wi["original_handoff_id"] == "HO-P02-SCHED-1"
    assert wi["failing_gates"] == ["G-CLIN"] and "CLIN_NOT_CLEARED" in wi["reason_codes"]
    assert recalls[0]["receiving_system"].startswith("Epic Cadence")
    assert (svc.outbox_dir / "HO-P02-RECALL-1.json").exists()
    audit = [a for a in svc.store.audit_rows("P02", "handoff_sent") if a["detail"]["kind"] == RECALL]
    assert len(audit) == 1 and audit[0]["detail"]["original_handoff_id"] == "HO-P02-SCHED-1"

    # Unrelated event while still not sendable: no further recall.
    svc.simulate("P02", "IDENTITY_RESOLUTION", "confirmed", "Intake Coordinator", note="Epic check",
                 confirmed={"dob": "1968-07-30"}, epic_reference="EPIC-REG-12")
    assert not svc.state("P02").scheduling_sendable
    assert len(_recalls(svc, "P02")) == 1

    # Cleared again: sendable, still one recall, and a second scheduling handoff is allowed.
    svc.simulate("P02", "CLINICAL_REVIEW_DECISION", "cleared_for_scheduling", "Cardiologist")
    assert svc.state("P02").scheduling_sendable and len(_recalls(svc, "P02")) == 1
    again = send(svc, "P02", "schedule")
    assert again["result"] == "sent" and again["handoff_id"] == "HO-P02-SCHED-2"
    assert svc.store.outbox_count("P02", SCHED) == 2


def test_fix4_no_recall_without_scheduling_handoff(svc):
    svc.simulate("P02", "CLINICAL_REVIEW_DECISION", "not_cleared", "Cardiologist")
    assert _recalls(svc, "P02") == []


def test_fix4_case_page_and_outbox_show_recall(svc):
    from fastapi.testclient import TestClient
    from app.main import app
    send(svc, "P02", "schedule")
    svc.simulate("P02", "CLINICAL_REVIEW_DECISION", "not_cleared", "Cardiologist")
    app.state.svc = svc
    try:
        with TestClient(app) as c:
            page = c.get("/case/P02").text
            out = c.get("/outbox").text
        assert "RECALLED" in page and "HO-P02-RECALL-1" in page and "HO-P02-SCHED-1" in page
        assert "HO-P02-RECALL-1" in out and "recalls HO-P02-SCHED-1" in out
    finally:
        del app.state.svc
