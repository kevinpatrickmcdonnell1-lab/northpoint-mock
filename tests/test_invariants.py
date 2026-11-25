"""PLAN §9.3 invariants and CLAUDE.md non-negotiables."""
import json
import re
import sqlite3
from pathlib import Path

import pytest

from app.engine import state_hash
from app.handoffs import send
from app.models import find_derived_keys
from tests.helpers import KEYS, POLICY, WORKFLOW, docs_of, facts, mutate, run, run_key

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"


def _all_results(s):
    return [s.identity, s.coverage, s.plan, *s.criteria.values()]


# -------------------------------------------------------------- gates / handoffs

def test_no_scheduling_handoff_with_failing_gate(svc):
    for k in KEYS:
        r = send(svc, k, "schedule")
        assert r["result"] == ("sent" if k == "P02" else "blocked"), k
    sched = [o for o in svc.store.outbox() if o["work_item"]["type"] == "stress_echo_ready_to_schedule"]
    assert [o["patient_key"] for o in sched] == ["P02"]
    for o in sched:
        assert len(o["gates_checked"]) == 6 and all(g["status"] == "pass" for g in o["gates_checked"])
    assert len(svc.store.audit_rows(kind="handoff_blocked")) == 9      # every block is recorded


def test_no_pa_approval_without_event():
    for k in KEYS:
        s = run_key(k)
        g = next(g for g in s.gates if g.gate == "G-PA-APPROVED")
        assert g.passed == (s.pa_requirement == "not_required"), k


def test_engine_never_writes_events(svc):
    before = svc.store.event_count()
    svc.evaluate_all()
    for k in KEYS:
        svc.state(k)
    assert svc.store.event_count() == before == 0
    src = (APP / "engine.py").read_text() + "".join(p.read_text() for p in (APP / "rules").glob("*.py"))
    src += (APP / "gates.py").read_text() + (APP / "actions.py").read_text()
    for banned in ("import sqlite3", "app.store", "app.service", "SimEvent(", "open("):
        assert banned not in src, banned


def test_engine_has_no_patient_specific_branches_or_expected_outcomes():
    names = {d.identifiers.name for k in KEYS for d in facts(k).documents if d.identifiers.name}
    for f in list(APP.rglob("*.py")):
        if f.name == "pdfs.py":
            continue
        src = f.read_text()
        assert not re.search(r"[\"']P(0[1-9]|10)[\"']", src), f"{f.name} references a patient key"
        assert not re.search(r"NP-(PKT|1\d{5})", src), f"{f.name} references a packet/MRN"
        assert not any(n in src for n in names), f"{f.name} references a patient name"
        assert "tests" not in re.findall(r"from (\w+)", src), f"{f.name} imports from tests"


# -------------------------------------------------------------- facts only

def test_extraction_files_contain_no_derived_keys():
    for f in sorted((ROOT / "data" / "extractions").rglob("*.json")):
        assert find_derived_keys(json.loads(f.read_text())) == [], f.name


def test_source_evidence_is_immutable(svc):
    before = {k: svc.store.case_json(k) for k in KEYS}
    svc.simulate("P04", "IDENTITY_RESOLUTION", "confirmed", "Intake Coordinator",
                 note="Confirmed in Epic", confirmed={"dob": "1951-03-14"}, epic_reference="EPIC-1")
    assert {k: svc.store.case_json(k) for k in KEYS} == before
    for table, sql in [("cases", "UPDATE cases SET packet_id='x'"),
                       ("sim_events", "UPDATE sim_events SET type='x'"),
                       ("sim_events", "DELETE FROM sim_events"),
                       ("audit_log", "DELETE FROM audit_log")]:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            svc.store.db.execute(sql)


# -------------------------------------------------------------- mutation tests

ADMIN_MUTATIONS = {
    "swap_product": lambda d: [x["fields"].update(plan_product_raw="Harborstone Health Commercial PPO")
                               for x in d["documents"] if x["doc_type"] in
                               ("registration_face_sheet", "insurance_card", "benefits_response")],
    "coverage_inactive": lambda d: [x["fields"].update(coverage_active_on_eval_date=False)
                                    for x in docs_of(d, "benefits_response")],
    "drop_benefits": lambda d: d.update(documents=[x for x in d["documents"]
                                                   if x["doc_type"] != "benefits_response"]),
    "drop_raw_tracings": lambda d: d.update(documents=[x for x in d["documents"]
                                                       if x["doc_type"] != "ecg_tracing_raw"]),
    "member_mismatch": lambda d: [x["fields"].update(member_id="ZZ-0")
                                  for x in docs_of(d, "benefits_response")],
}


@pytest.mark.parametrize("key", KEYS)
@pytest.mark.parametrize("mut", list(ADMIN_MUTATIONS))
def test_priority_unchanged_by_payer_coverage_or_tracings(key, mut):
    assert run(mutate(key, ADMIN_MUTATIONS[mut])).priority == run_key(key).priority


@pytest.mark.parametrize("key", KEYS)
def test_unsigned_docs_change_nothing(key):
    stripped = mutate(key, lambda d: d.update(documents=[
        x for x in d["documents"]
        if x["signed"] or x["doc_type"] in ("registration_face_sheet", "insurance_card", "benefits_response")]))
    assert run(stripped) == run_key(key)


def test_delete_p07_page_8_identical():
    assert run(mutate("P07", lambda d: d["documents"].pop())) == run_key("P07")


@pytest.mark.parametrize("key", ["P02", "P08"])
def test_no_pa_plan_with_pending_coverage_never_sendable(key):
    from tests.helpers import event
    clear = [event(key, "CLINICAL_REVIEW_DECISION", "cleared_for_scheduling", "Clinical Triage RN")]
    ef = mutate(key, ADMIN_MUTATIONS["drop_benefits"])
    s = run(ef, clear)
    assert s.coverage.status == "pending" and not s.scheduling_sendable


# -------------------------------------------------------------- results quality

def _check_results(s):
    for r in _all_results(s):
        if r.status in ("S", "verified", "resolved"):
            assert r.evidence_refs, f"{s.patient_key} {r.rule_id} S without evidence"
        elif r.status != "NA":
            assert r.reason_code, f"{s.patient_key} {r.rule_id} {r.status} without reason code"
    if s.priority.status == "PRI-3":
        assert s.priority.evidence_refs
    else:
        assert s.priority.reason_code


@pytest.mark.parametrize("key", KEYS)
def test_every_result_has_reason_or_evidence(key):
    _check_results(run_key(key))


def test_reason_codes_survive_transitions(svc):
    svc.simulate("P04", "IDENTITY_RESOLUTION", "confirmed", "Intake Coordinator", note="Epic",
                 confirmed={"dob": "1951-03-14", "member_id": "SCM-302177"}, epic_reference="EPIC-1")
    svc.simulate("P01", "PAYER_PA_DECISION", "approved", "Auth Specialist")
    for k in KEYS:
        _check_results(svc.state(k))


def test_evidence_refs_point_at_real_pages():
    for k in KEYS:
        ef = facts(k)
        pages = {p for d in ef.documents for p in d.pages}
        for r in [*_all_results(run_key(k)), run_key(k).priority]:
            for e in r.evidence_refs:
                assert e.file == ef.source_file and e.page in pages


@pytest.mark.parametrize("key", KEYS)
def test_deterministic(key):
    assert state_hash(run_key(key)) == state_hash(run_key(key))


def test_ui_words_not_met_vs_denial():
    for k in ("P07", "P10"):
        s = run_key(k)
        assert "not a payer denial" in s.criteria["EVIDENCE"].reason_text
    assert "not an approval" in run_key("P01").criteria["EVIDENCE"].reason_text
