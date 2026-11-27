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


# --------------------------------------------------------------------------- #
# Fix 5. Config mistakes fail open                                             #
# --------------------------------------------------------------------------- #

import yaml  # noqa: E402

from app import policy as policy_mod  # noqa: E402


def _yaml_variant(tmp_path, src, name, fn):
    data = yaml.safe_load(src.read_text())
    fn(data)
    p = tmp_path / name
    p.write_text(yaml.safe_dump(data, sort_keys=False))
    return p


def _policy_variant(tmp_path, fn):
    return _yaml_variant(tmp_path, policy_mod.POLICY_PATH, "policy.yaml", fn)


def _workflow_variant(tmp_path, fn):
    return _yaml_variant(tmp_path, policy_mod.WORKFLOW_PATH, "workflow.yaml", fn)


def test_fix5_unknown_plan_extra_rule_rejected(tmp_path):
    p = _policy_variant(tmp_path, lambda d: d["plans"]["PLAN-HSHMO"].update(extra=["HMO1"]))
    with pytest.raises(Exception, match=r"PLAN-HSHMO.*HMO1"):
        policy_mod.load_policy(p)


def test_fix5_empty_no_pa_evidence_gate_rejected(tmp_path):
    p = _workflow_variant(tmp_path, lambda d: d.update(no_pa_evidence_gate=[]))
    with pytest.raises(Exception, match="no_pa_evidence_gate"):
        policy_mod.load_workflow(p)


def test_fix5_unknown_no_pa_evidence_gate_rule_rejected(tmp_path):
    p = _workflow_variant(tmp_path, lambda d: d.update(no_pa_evidence_gate=["DOC1"]))
    with pytest.raises(Exception, match=r"no_pa_evidence_gate.*DOC1"):
        policy_mod.load_workflow(p)


@pytest.mark.parametrize("bad", [-90, 0, 1.5, True, "90"])
def test_fix5_windows_must_be_positive_integers(tmp_path, bad):
    p = _policy_variant(tmp_path, lambda d: d["windows"].update(resting_ecg=bad))
    with pytest.raises(Exception, match="resting_ecg"):
        policy_mod.load_policy(p)


def test_fix5_config_errors_are_config_errors(tmp_path):
    p = _workflow_variant(tmp_path, lambda d: d.update(no_pa_evidence_gate=[]))
    with pytest.raises(policy_mod.ConfigError) as e:
        policy_mod.load_workflow(p)
    assert "workflow.yaml" in str(e.value)


def test_fix5_one_rule_id_registry():
    from app import rule_ids
    from app.rules import evidence
    assert set(rule_ids.EVIDENCE_RULES) <= set(rule_ids.ALL_RULE_IDS)
    assert set(rule_ids.PLAN_EXTRA_RULES) <= set(rule_ids.EVIDENCE_RULES)
    assert list(evidence.evaluate_evidence.__globals__["rule_ids"].EVIDENCE_RULES) == \
        list(rule_ids.EVIDENCE_RULES)


def test_fix5_config_hash_in_audit_and_changes_with_file(tmp_path):
    from app.pdfs import MOCK_EXTRACTIONS
    from app.service import Service
    pol = tmp_path / "p.yaml"
    wf = tmp_path / "w.yaml"
    pol.write_bytes(policy_mod.POLICY_PATH.read_bytes())
    wf.write_bytes(policy_mod.WORKFLOW_PATH.read_bytes())

    def audit_hash(db):
        s = Service(db_path=tmp_path / db, outbox_dir=tmp_path / "o", extractions_dir=MOCK_EXTRACTIONS,
                    policy_path=pol, workflow_path=wf)
        s.seed()
        h = s.store.audit_rows("P01", "evaluation")[0]["detail"]["config_sha256"]
        assert h == s.config_sha256 and len(h) == 64
        s.store.close()
        return h

    h1 = audit_hash("a.db")
    assert audit_hash("b.db") == h1                     # same bytes, same hash
    wf.write_bytes(wf.read_bytes() + b"\n# comment only\n")
    assert audit_hash("c.db") != h1                     # any byte change shows


def test_fix5_config_hash_in_case_footer(svc):
    from fastapi.testclient import TestClient
    from app.main import app
    app.state.svc = svc
    try:
        with TestClient(app) as c:
            page = c.get("/case/P01").text
        assert svc.config_sha256 in page
    finally:
        del app.state.svc


# --------------------------------------------------------------------------- #
# Fix 6. Confirmed identity value not checked against the packet               #
# --------------------------------------------------------------------------- #

from tests.helpers import event, run_key  # noqa: E402


def _confirm(key, **confirmed):
    return event(key, "IDENTITY_RESOLUTION", "confirmed", "Intake Coordinator", note="Epic",
                 confirmed=confirmed, epic_reference="EPIC-REG-9")


def test_fix6_confirmed_value_not_in_packet_is_flagged():
    s = run_key("P04", [_confirm("P04", dob="1999-09-09", member_id="SCM-302177")])
    assert s.identity.status == "verified"                     # still accepted: Epic is the authority
    assert "confirmed value 1999-09-09 not found in packet (Epic ref EPIC-REG-9)" in s.identity.reason_text
    flags = s.identity.detail["confirmed_not_in_packet"]
    assert [(f["field"], f["value"]) for f in flags] == [("dob", "1999-09-09")]


def test_fix6_confirmed_value_on_a_page_is_not_flagged():
    s = run_key("P04", [_confirm("P04", dob="1951-03-14", member_id="SCM-302177")])
    assert s.identity.status == "verified"
    assert "not found in packet" not in s.identity.reason_text
    assert s.identity.detail["confirmed_not_in_packet"] == []


def test_fix6_confirmation_of_undisputed_field_also_checked():
    s = run_key("P02", [_confirm("P02", dob="1970-01-01")])
    assert "confirmed value 1970-01-01 not found in packet" in s.identity.reason_text


def test_fix6_case_page_warning(svc):
    from fastapi.testclient import TestClient
    from app.main import app
    svc.simulate("P04", "IDENTITY_RESOLUTION", "confirmed", "Intake Coordinator", note="Epic",
                 confirmed={"dob": "1999-09-09", "member_id": "SCM-302177"}, epic_reference="EPIC-REG-9")
    app.state.svc = svc
    try:
        with TestClient(app) as c:
            page = c.get("/case/P04").text
        assert "id-warning" in page and "1999-09-09" in page and "not found in packet" in page
    finally:
        del app.state.svc


# --------------------------------------------------------------------------- #
# Fix 7. Responses dropped silently by the evaluation-date filter (A8)          #
# --------------------------------------------------------------------------- #

LATE = {"response_date": "2026-10-02", "payer": "Summit Crest",
        "plan_product_raw": "Summit Crest Medicare Advantage", "member_id": "SCM-302177",
        "coverage_active_on_eval_date": True}


def test_fix7_simulated_late_response_named_in_reason(svc):
    ev = svc.simulate("P04", "BENEFITS_RESPONSE", "received", "Insurance Benefits Manager (mock)",
                      benefits=LATE)
    s = svc.state("P04")
    assert s.coverage.status == "conflicting"
    assert "1 response dated 2026-10-02 ignored (after evaluation date 2026-10-01, A8)" in s.coverage.reason_text
    assert ev.event_id in s.coverage.reason_text
    assert s.coverage.detail["ignored_after_eval_date"][0]["response_date"] == "2026-10-02"


def test_fix7_in_packet_late_response_named_in_reason():
    def fn(data):
        b = docs_of(data, "benefits_response")[0]
        b["date"] = "2026-10-02"
        b["fields"]["response_date"] = "2026-10-02"
    s = run(mutate("P04", fn))
    assert s.coverage.status == "pending"
    assert "1 response dated 2026-10-02 ignored (after evaluation date 2026-10-01, A8)" in s.coverage.reason_text
    assert "P04_packet.pdf p.5" in s.coverage.reason_text


def test_fix7_no_ignored_text_when_nothing_ignored():
    assert "ignored" not in run_key("P04").coverage.reason_text


def test_fix7_simulate_form_rejects_late_response(svc):
    from fastapi.testclient import TestClient
    from app.main import app
    app.state.svc = svc
    try:
        with TestClient(app) as c:
            r = c.post("/case/P04/simulate", data={
                "type": "BENEFITS_RESPONSE", "actor_role": "Insurance Benefits Manager (mock)",
                "response_date": "2026-10-02", "payer": "Summit Crest",
                "plan_product_raw": "Summit Crest Medicare Advantage", "member_id": "SCM-302177",
                "active": "true"})
        assert r.status_code == 200 and svc.store.event_count() == 0
        assert "Not recorded" in r.text and "after the evaluation date 2026-10-01" in r.text
        assert 'class="flash flash-bad small form-error"' in r.text     # shown inline, at the form
    finally:
        del app.state.svc


# --------------------------------------------------------------------------- #
# Fix 8. Evidence links fall back to an unrelated quote                        #
# --------------------------------------------------------------------------- #

import subprocess  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

from tests.helpers import facts  # noqa: E402


def _doc_without_quote_for(field):
    for k in ("P01", "P02", "P03", "P05"):
        for d in facts(k).documents:
            if d.quotes and field not in {q.field for q in d.quotes} and hasattr(d.fields, field):
                return facts(k), d
    raise AssertionError(f"no doc lacks a {field} quote")


def test_fix8_ref_without_field_quote_has_no_quote():
    from app.rules.common import ref
    ef, d = _doc_without_quote_for("symptom_trajectory")
    r = ref(ef, d, "symptom_trajectory")
    assert r.quote is None and r.page == d.pages[0] and r.field == "symptom_trajectory"


def test_fix8_ref_with_field_quote_unchanged():
    from app.rules.common import ref
    ef = facts("P01")
    d = next(x for x in ef.documents if x.doc_type == "stress_echo_order")
    q = next(q for q in d.quotes if q.field == "indication")
    r = ref(ef, d, "indication")
    assert r.quote == q.text and r.page == q.page


def test_fix8_page_viewer_says_no_quote(svc):
    from fastapi.testclient import TestClient
    from app.main import app
    app.state.svc = svc
    try:
        with TestClient(app) as c:
            r = c.get("/page/P01_packet.pdf/6?field=complaint_type&case=P01")
        assert r.status_code == 200
        assert "No quote recorded for complaint_type; showing the page" in r.text
        assert "<mark>" not in r.text
    finally:
        del app.state.svc


def test_fix8_check_extractions_warns_but_passes():
    root = Path(__file__).resolve().parent.parent
    p = subprocess.run([sys.executable, "scripts/check_extractions.py"], cwd=root,
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    warns = [ln for ln in p.stdout.splitlines() if ln.startswith("WARNING") and "no quote of its own" in ln]
    assert warns, p.stdout
    assert "ERROR" not in p.stdout
