"""docs/demo_script.md, real set (R01–R10): every number and status the script states.

Runs the scripted sequence in script order through the service layer, so event and
handoff IDs match what the presenter will see after pressing Reset.
"""
import pytest

from app.handoffs import send
from app.metrics import compute
from app.pdfs import extractions_dir, real_ready
from app.service import Service

pytestmark = pytest.mark.skipif(not real_ready(), reason="real PDFs not in data/packets")


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    d = tmp_path_factory.mktemp("demo")
    s = Service(db_path=d / "demo.db", outbox_dir=d / "outbox", extractions_dir=extractions_dir("real"))
    s.seed()
    yield s
    s.store.close()


def failing(s):
    return [g.gate for g in s.gates if not g.passed]


def codes(s):
    return [a.reason_code for a in s.actions]


def owners_tab(svc, role):
    return sorted(s.patient_key for s in svc.states() if any(a.owner_role == role for a in s.actions))


# The tests below are ordered: they ARE the script, step by step.

def test_1_baseline(demo):
    assert compute(demo)["counts"] == {"held": 8, "ready for PA prep / awaiting payer": 2}
    states = {s.patient_key: s for s in demo.states()}
    assert sorted(k for k, s in states.items() if s.pa_prep_ready) == ["R02", "R07"]
    assert [k for k, s in states.items() if s.scheduling_sendable] == []
    assert all(s.actions for s in states.values())                  # every case has an owner
    assert owners_tab(demo, "Clinical Triage RN") == ["R07", "R08"]
    assert owners_tab(demo, "Clinical Review RN") == ["R03", "R05", "R06", "R10"]


def test_2_r07_urgent_and_pa_ready(demo):
    s = demo.state("R07")
    assert s.priority.status == "PRI-1" and s.pa_prep_ready
    assert codes(s) == ["CLIN_URGENT", "PA_READY"]
    assert failing(s) == ["G-CLIN", "G-PA-APPROVED"]
    assert s.priority.evidence_refs[0].page == 2
    assert send(demo, "R07", "triage")["handoff_id"] == "HO-R07-TRIAGE-1"
    assert send(demo, "R07", "pa_packet")["handoff_id"] == "HO-R07-PA-1"   # clinical hold never blocks PA prep


def test_3_r01_identity_conflict(demo):
    s = demo.state("R01")
    assert s.identity.status == "conflicting"
    assert "dob: 1958-06-12 (Patient_01_Tessa_Lornwell.pdf p.2, 4, 5) vs 1958-06-21 " \
           "(Patient_01_Tessa_Lornwell.pdf p.3, 9)" in s.identity.reason_text
    assert s.coverage.status == "pending" and s.criteria["DOC-1"].status == "I"
    assert "names the procedure but no indication" in s.criteria["DOC-1"].reason_text
    assert codes(s) == ["ID_CONFLICT", "COV_PENDING_NONE", "EVID_MISSING_ORDER", "EVID_MISSING_REPORT"]
    assert failing(s) == ["G-ID", "G-COV", "G-EVID", "G-PA-APPROVED"]
    assert send(demo, "R01", "identity_task")["handoff_id"] == "HO-R01-ID-1"


def test_4_r02_pa_path(demo):
    r = send(demo, "R02", "schedule")
    assert r["result"] == "blocked" and r["failing_gates"] == ["G-PA-APPROVED"]
    with pytest.raises(ValueError, match="No PA request packet has been sent for R02"):
        demo.simulate("R02", "PAYER_PA_DECISION", "approved", "Auth Specialist")
    assert send(demo, "R02", "pa_packet")["handoff_id"] == "HO-R02-PA-1"
    ev = demo.simulate("R02", "PAYER_PA_DECISION", "approved", "Auth Specialist", note="mock payer ref")
    assert ev.event_id == "EV-0001" and ev.pa_handoff_id == "HO-R02-PA-1"
    s = demo.state("R02")
    assert s.scheduling_sendable and codes(s) == ["SCHED_READY"]
    sent = send(demo, "R02", "schedule")
    assert sent["result"] == "sent" and sent["handoff_id"] == "HO-R02-SCHED-1"
    assert all(g["status"] == "pass" for g in sent["payload"]["gates_checked"])
    assert send(demo, "R02", "schedule")["result"] == "already_recorded"


def test_5_r05_recall(demo):
    s = demo.state("R05")
    assert s.priority.status == "PRI-2" and s.pa_requirement == "not_required"
    assert failing(s) == ["G-CLIN"] and codes(s) == ["CLIN_EXPEDITED"]
    r = send(demo, "R05", "schedule")
    assert r["result"] == "blocked" and r["failing_gates"] == ["G-CLIN"]

    ev = demo.simulate("R05", "CLINICAL_REVIEW_DECISION", "cleared_for_scheduling", "Clinical Review RN",
                       note="Expedited review done")
    assert ev.event_id == "EV-0002" and demo.state("R05").scheduling_sendable
    assert send(demo, "R05", "schedule")["handoff_id"] == "HO-R05-SCHED-1"

    ev = demo.simulate("R05", "CLINICAL_REVIEW_DECISION", "not_cleared", "Cardiologist", note="Hold")
    assert ev.event_id == "EV-0003"
    s = demo.state("R05")
    assert not s.scheduling_sendable and failing(s) == ["G-CLIN"] and codes(s) == ["CLIN_NOT_CLEARED"]
    recalls = [o for o in demo.store.outbox("R05") if o["work_item"]["type"] == "scheduling_recall"]
    assert [(o["handoff_id"], o["work_item"]["original_handoff_id"], o["work_item"]["failing_gates"],
             o["work_item"]["reason_codes"]) for o in recalls] == [
        ("HO-R05-RECALL-1", "HO-R05-SCHED-1", ["G-CLIN"], ["CLIN_NOT_CLEARED"])]
    r = send(demo, "R05", "schedule")
    assert r["result"] == "blocked" and r["failing_gates"] == ["G-CLIN"]


def test_6_r06_hmo1(demo):
    s = demo.state("R06")
    assert s.plan.detail["plan_id"] == "PLAN-HSHMO" and s.priority.status == "PRI-2"
    assert s.criteria["QUAL-2"].status == "S" and s.criteria["QUAL-2"].detail["branches"] == {
        "A": "unknown", "B": "supported"}
    assert s.criteria["HMO-1"].status == "I" and s.criteria["EVIDENCE"].status == "I"
    assert codes(s) == ["CLIN_EXPEDITED", "EVID_HMO1"]
    r = send(demo, "R06", "missing_info")
    assert r["handoff_id"] == "HO-R06-INFO-1"
    assert r["payload"]["work_item"]["requests"] == [
        {"reason_code": "EVID_HMO1", "document": "HMO-1: current resting ECG report (dated ≥ 2026-07-03)"}]


def test_7_r04_r09_not_met(demo):
    r04, r09 = demo.state("R04"), demo.state("R09")
    assert r04.criteria["QUAL-1"].status == "NM" and r04.criteria["QUAL-2"].status == "I"
    assert r04.criteria["QUAL-2"].detail["branches"] == {"A": "failed", "B": "unknown"}
    assert r09.criteria["REPEAT-1"].status == "NM" and "2026-08-15" in r09.criteria["REPEAT-1"].reason_text
    for s in (r04, r09):
        assert s.criteria["EVIDENCE"].status == "NM" and codes(s) == ["EVID_NOT_MET"]
        assert "not a payer denial" in s.criteria["EVIDENCE"].reason_text
        r = send(demo, s.patient_key, "pa_packet")
        assert r["result"] == "blocked" and r["failing_gates"] == ["G-EVID"]


def test_8_r10_inactive(demo):
    s = demo.state("R10")
    assert s.coverage.status == "inactive"
    assert s.coverage.reason_text == ("Benefits response dated 2026-09-30: coverage not active on "
                                      "2026-10-01 (ended 2026-09-15).")
    assert s.coverage.evidence_refs[0].page == 7
    assert codes(s) == ["CLIN_EXPEDITED", "COV_INACTIVE"]
    assert send(demo, "R10", "missing_info")["handoff_id"] == "HO-R10-INFO-1"


def test_9_metrics_at_end(demo):
    m = compute(demo)
    assert m["counts"] == {"held": 8, "handed to scheduling": 1, "ready for PA prep / awaiting payer": 1}
    assert m["gate_leaks"] == 0 and m["scheduling_recalls"] == 1
    assert m["blocked_attempts"] == 5 and m["triage_notifications"] == 1


# ------------------------------------------------------------------ the document itself

def _script():
    from pathlib import Path
    text = (Path(__file__).resolve().parent.parent / "docs" / "demo_script.md").read_text()
    main, _, appendix = text.partition("## Appendix")
    return main, appendix


def test_10_script_walkthrough_is_the_real_set():
    import re
    main, appendix = _script()
    assert not re.search(r"\bP(0[1-9]|10)\b", main), "mock keys in the main walkthrough"
    assert "P01" in appendix and "NORTHPOINT_DATASET=mock" in appendix   # mock kept as appendix
    assert "1 sendable, 1 PA-ready" not in main


@pytest.mark.parametrize("stated", [
    "0 sendable", "2 ready for PA prep (R02, R07)", "8 held",
    "R07, R08", "R03, R05, R06, R10",
    "HO-R07-TRIAGE-1", "HO-R07-PA-1",
    "1958-06-12 (p.2, 4, 5) vs 1958-06-21 (p.3, 9)", "HO-R01-ID-1",
    "G-PA-APPROVED", "No PA request packet has been sent for R02", "HO-R02-PA-1", "EV-0001",
    "HO-R02-SCHED-1",
    "EV-0002", "HO-R05-SCHED-1", "EV-0003", "HO-R05-RECALL-1", "CLIN_NOT_CLEARED",
    "current resting ECG report (dated ≥ 2026-07-03)", "HO-R06-INFO-1",
    "2026-08-15", "G-EVID",
    "ended 2026-09-15", "HO-R10-INFO-1",
    "1 handed to scheduling", "1 ready for PA prep", "0 gate leaks", "1 scheduling recall",
    "5 blocked attempts",
])
def test_11_script_states_what_the_run_shows(stated):
    main, _ = _script()
    assert stated in main, f"demo_script.md does not state {stated!r}"


def test_12_recall_flash_in_ui(tmp_path):
    """Section 5's on-screen beat: the not_cleared flash says the handoff was recalled."""
    from fastapi.testclient import TestClient
    from app.main import app
    s = Service(db_path=tmp_path / "ui.db", outbox_dir=tmp_path / "o", extractions_dir=extractions_dir("real"))
    s.seed()
    app.state.svc = s
    try:
        with TestClient(app) as c:
            c.post("/case/R05/simulate", data={"type": "CLINICAL_REVIEW_DECISION",
                                               "decision": "cleared_for_scheduling",
                                               "actor_role": "Clinical Review RN"})
            c.post("/case/R05/handoff/schedule")
            r = c.post("/case/R05/simulate", data={"type": "CLINICAL_REVIEW_DECISION",
                                                   "decision": "not_cleared", "actor_role": "Cardiologist"})
        assert "scheduling handoff recalled automatically (HO-R05-RECALL-1)" in r.text
        assert "RECALLED" in r.text
    finally:
        del app.state.svc
        s.store.close()
