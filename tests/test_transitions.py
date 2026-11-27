"""PLAN §6.3 scripted transitions, each driven by an explicit, labelled simulated event."""
import json

import pytest
from pydantic import ValidationError

from app.handoffs import send
from app.metrics import business_days, compute


def gates(s):
    return {g.gate for g in s.gates if not g.passed}


def test_p01_blocked_then_pa_then_approval_then_sent(svc):
    r = send(svc, "P01", "schedule")
    assert r["result"] == "blocked" and r["failing_gates"] == ["G-PA-APPROVED"]
    assert svc.store.audit_rows("P01", "handoff_blocked")[0]["detail"]["failing_gates"] == ["G-PA-APPROVED"]

    pa = send(svc, "P01", "pa_packet")
    assert pa["result"] == "sent"
    assert {"rule": "QUAL-2", "file": "P01_packet.pdf", "page": 7} in [
        {k: e[k] for k in ("rule", "file", "page")} for e in pa["payload"]["evidence_refs"]]

    ev = svc.simulate("P01", "PAYER_PA_DECISION", "approved", "Auth Specialist", note="mock payer")
    s = svc.state("P01")
    assert s.scheduling_sendable and [a.reason_code for a in s.actions] == ["SCHED_READY"]

    sent = send(svc, "P01", "schedule")
    assert sent["result"] == "sent"
    p = sent["payload"]
    assert p["work_item"]["type"] == "stress_echo_ready_to_schedule"
    assert p["work_item"]["clinical_priority"] == "routine" and p["work_item"]["plan"] == "PLAN-SCMA"
    assert p["simulated_dependencies"] == [{"event_id": ev.event_id, "type": "PAYER_PA_DECISION",
                                            "decision": "approved", "simulated": True}]
    assert (svc.outbox_dir / f"{p['handoff_id']}.json").exists()
    again = send(svc, "P01", "schedule")
    assert again["result"] == "already_recorded" and again["handoff_id"] == p["handoff_id"]


def test_p04_identity_then_benefits(svc):
    assert send(svc, "P04", "identity_task")["result"] == "sent"
    with pytest.raises(ValidationError):   # note and Epic reference are required
        svc.simulate("P04", "IDENTITY_RESOLUTION", "confirmed", "Intake Coordinator",
                     confirmed={"dob": "1951-03-14"})
    svc.simulate("P04", "IDENTITY_RESOLUTION", "confirmed", "Intake Coordinator",
                 note="Confirmed DOB and member ID in Epic registration",
                 confirmed={"dob": "1951-03-14", "member_id": "SCM-302177"}, epic_reference="EPIC-REG-77")
    s = svc.state("P04")
    assert s.identity.status == "verified"
    assert s.coverage.status == "conflicting"
    dob = s.identity.detail["fields"]["dob"]       # original conflicting evidence stays visible
    assert dob["status"] == "resolved" and {v["value"] for v in dob["values"]} == {"1951-03-14", "1951-04-13"}

    svc.simulate("P04", "BENEFITS_RESPONSE", "received", "Insurance Benefits Manager (mock)",
                 benefits={"response_date": "2026-10-01", "payer": "Summit Crest",
                           "plan_product_raw": "Summit Crest Medicare Advantage",
                           "member_id": "SCM-302177", "coverage_active_on_eval_date": True})
    s = svc.state("P04")
    assert s.coverage.status == "verified" and "SIMULATED" in s.coverage.reason_text
    assert s.pa_prep_ready and [a.reason_code for a in s.actions] == ["PA_READY"]


def test_p08_cleared_then_sendable(svc):
    assert gates(svc.state("P08")) == {"G-CLIN"}
    svc.simulate("P08", "CLINICAL_REVIEW_DECISION", "cleared_for_scheduling", "Clinical Triage RN",
                 note="Timing clarified with PCP")
    s = svc.state("P08")
    assert s.scheduling_sendable and s.priority.status == "PRI-4"   # priority itself unchanged
    assert send(svc, "P08", "schedule")["result"] == "sent"


def test_p03_triage_acts_while_admin_holds_remain(svc):
    t = send(svc, "P03", "triage")          # never gated by admin
    assert t["result"] == "sent" and t["payload"]["work_item"]["clinical_priority"] == "PRI-1"
    svc.simulate("P03", "CLINICAL_REVIEW_DECISION", "reviewed_hold", "Clinical Triage RN",
                 note="Reviewed; hold")
    s = svc.state("P03")
    assert gates(s) == {"G-COV", "G-EVID", "G-CLIN", "G-PA-APPROVED"}
    assert [a.reason_code for a in s.actions] == ["CLIN_REVIEWED_HOLD", "COV_PENDING_STALE",
                                                  "EVID_MISSING_REPORT"]
    assert send(svc, "P03", "missing_info")["result"] == "sent"


def test_wrong_role_cannot_record_event(svc):
    send(svc, "P01", "pa_packet")          # a PA decision needs a submitted packet
    with pytest.raises(ValueError):
        svc.simulate("P01", "PAYER_PA_DECISION", "approved", "Intake Coordinator")
    assert svc.store.event_count() == 0


def test_audit_records_evaluation_with_policy_and_diff(svc):
    svc.simulate("P08", "CLINICAL_REVIEW_DECISION", "cleared_for_scheduling", "Clinical Triage RN")
    ev = svc.store.audit_rows("P08", "evaluation")[0]["detail"]
    assert ev["policy_version"] == "SC-CARD-1.1" and ev["eval_date"] == "2026-10-01"
    assert ev["diff"]["scheduling_sendable"] == {"from": False, "to": True}
    assert svc.store.audit_rows("P08", "sim_event")[0]["detail"]["simulated"] is True
    assert svc.store.audit_rows("P08", "state_change")


def test_metrics(svc):
    m = compute(svc)
    assert m["counts"] == {"sendable to scheduling": 1, "ready for PA prep / awaiting payer": 1, "held": 8}
    assert m["gate_leaks"] == 0
    for k in ("P01", "P02", "P03"):
        send(svc, k, "schedule")
    m = compute(svc)
    assert m["gate_leaks"] == 0 and m["blocked_attempts"] == 2
    assert m["counts"]["handed to scheduling"] == 1


def test_business_days_A7():
    from datetime import date
    assert business_days(date(2026, 9, 25), date(2026, 10, 1)) == 4   # Fri -> Thu
    assert business_days(date(2026, 10, 1), date(2026, 10, 1)) == 0


def test_outbox_json_matches_table(svc):
    r = send(svc, "P02", "schedule")
    on_disk = json.loads((svc.outbox_dir / f"{r['handoff_id']}.json").read_text())
    assert on_disk == svc.store.outbox("P02")[0]
