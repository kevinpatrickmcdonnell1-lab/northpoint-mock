"""PLAN §6.3 golden table, derived by hand from the rules. Written BEFORE the engine.

Expected outcomes live only here (CLAUDE.md invariant 1). If the engine disagrees,
report whether the cause is extraction, rule reading or code. Never edit to match.
"""
import pytest

from tests.helpers import KEYS, action_codes, failing_gates, run_key

# priority, identity, (coverage, coverage reason), pa_req,
# DOC-1, QUAL-1, (QUAL-2, branches supporting), REPEAT-1, HMO-1, evidence, actions (ordered)
GOLDEN = {
    "P01": ("PRI-3", "verified", ("verified", None), "required",
            "S", "S", ("S", ["A"]), "S", "NA", "S", ["PA_READY"]),
    "P02": ("PRI-3", "verified", ("verified", None), "not_required",
            "S", "S", ("S", ["A"]), "S", "NA", "S", ["SCHED_READY"]),
    "P03": ("PRI-1", "verified", ("pending", "COV_PENDING_STALE"), "required",
            "S", "S", ("I", []), "S", "NA", "I",
            ["CLIN_URGENT", "COV_PENDING_STALE", "EVID_MISSING_REPORT"]),
    "P04": ("PRI-3", "conflicting", ("conflicting", "COV_CONFLICT"), "required",
            "S", "S", ("S", ["A"]), "S", "NA", "S", ["ID_CONFLICT", "COV_CONFLICT"]),
    "P05": ("PRI-2", "verified", ("verified", None), "required",
            "S", "S", ("S", ["B"]), "S", "I", "I", ["CLIN_EXPEDITED", "EVID_HMO1"]),
    "P06": ("PRI-2", "verified", ("inactive", "COV_INACTIVE"), "required",
            "S", "S", ("S", ["A"]), "S", "NA", "S", ["CLIN_EXPEDITED", "COV_INACTIVE"]),
    "P07": ("PRI-3", "verified", ("verified", None), "required",
            "S", "S", ("S", ["A"]), "NM", "NA", "NM", ["EVID_NOT_MET"]),
    "P08": ("PRI-4", "verified", ("verified", None), "not_required",
            "S", "S", ("S", ["A"]), "S", "NA", "S", ["CLIN_UNDETERMINED"]),
    "P09": ("PRI-3", "verified", ("pending", "COV_PENDING_PRODUCT"), "unknown",
            "I", "I", ("S", ["A"]), "S", "NA", "I", ["COV_PENDING_PRODUCT", "EVID_MISSING_ORDER"]),
    "P10": ("PRI-3", "verified", ("verified", None), "required",
            "S", "NM", ("NM", []), "S", "NA", "NM", ["EVID_NOT_MET"]),
}

OWNERS = {
    "PA_READY": "Auth Specialist", "SCHED_READY": "Scheduling (Epic)",
    "CLIN_URGENT": "Clinical Triage RN", "CLIN_EXPEDITED": "Clinical Review RN",
    "CLIN_UNDETERMINED": "Clinical Triage RN", "ID_CONFLICT": "Intake Coordinator",
    "COV_PENDING_STALE": "Intake Coordinator", "COV_PENDING_PRODUCT": "Intake Coordinator",
    "COV_CONFLICT": "Intake Coordinator", "COV_INACTIVE": "Intake Coordinator",
    "EVID_MISSING_REPORT": "Intake Coordinator", "EVID_MISSING_ORDER": "Intake Coordinator",
    "EVID_HMO1": "Intake Coordinator", "EVID_NOT_MET": "Auth Specialist",
}


def test_all_ten_present():
    assert KEYS == [f"P{i:02d}" for i in range(1, 11)]


@pytest.mark.parametrize("key", list(GOLDEN))
def test_golden_row(key):
    (pri, ident, (cov, cov_code), pa, doc1, q1, (q2, q2_by), rep, hmo, evid, actions) = GOLDEN[key]
    s = run_key(key)
    got = {
        "priority": s.priority.status, "identity": s.identity.status,
        "coverage": (s.coverage.status, s.coverage.reason_code), "pa": s.pa_requirement,
        "DOC-1": s.criteria["DOC-1"].status, "QUAL-1": s.criteria["QUAL-1"].status,
        "QUAL-2": (s.criteria["QUAL-2"].status, s.criteria["QUAL-2"].detail["supported_by"]),
        "REPEAT-1": s.criteria["REPEAT-1"].status, "HMO-1": s.criteria["HMO-1"].status,
        "evidence": s.criteria["EVIDENCE"].status, "actions": action_codes(s),
    }
    want = {
        "priority": pri, "identity": ident, "coverage": (cov, cov_code), "pa": pa,
        "DOC-1": doc1, "QUAL-1": q1, "QUAL-2": (q2, q2_by), "REPEAT-1": rep, "HMO-1": hmo,
        "evidence": evid, "actions": actions,
    }
    assert got == want
    for a in s.actions:
        assert a.owner_role == OWNERS[a.reason_code], a


def test_baseline_counts():
    states = [run_key(k) for k in GOLDEN]
    sendable = [s.patient_key for s in states if s.scheduling_sendable]
    pa_ready = [s.patient_key for s in states if s.pa_prep_ready]
    held = [s.patient_key for s in states if not s.scheduling_sendable and not s.pa_prep_ready]
    assert sendable == ["P02"]
    assert pa_ready == ["P01"]
    assert len(held) == 8
    for s in states:  # every case has a reason and an owner
        assert s.actions and all(a.owner_role for a in s.actions)


def test_all_simultaneous_holds_shown():
    assert failing_gates(run_key("P03")) == {"G-COV", "G-EVID", "G-CLIN", "G-PA-APPROVED"}
    assert failing_gates(run_key("P04")) == {"G-ID", "G-COV", "G-PA-APPROVED"}
    assert failing_gates(run_key("P09")) == {"G-COV", "G-PAREQ", "G-EVID", "G-PA-APPROVED"}
    assert failing_gates(run_key("P01")) == {"G-PA-APPROVED"}
    assert failing_gates(run_key("P08")) == {"G-CLIN"}


def test_priority_sources():
    p03 = run_key("P03").priority
    assert [(r.file, r.page) for r in p03.evidence_refs] == [("P03_packet.pdf", 7)]
    p05 = run_key("P05").priority
    assert [(r.file, r.page) for r in p05.evidence_refs] == [("P05_packet.pdf", 6)]
    p08 = run_key("P08").priority   # both conflicting sources shown
    assert sorted(r.page for r in p08.evidence_refs) == [1, 6]


def test_identity_conflict_shows_every_value_with_source():
    s = run_key("P04").identity
    dob = s.detail["fields"]["dob"]
    assert dob["status"] == "conflicting"
    assert {v["value"] for v in dob["values"]} == {"1951-03-14", "1951-04-13"}
    assert all(v["file"] == "P04_packet.pdf" and v["page"] for v in dob["values"])
    member = s.detail["fields"]["member_id"]
    assert {v["value"] for v in member["values"]} == {"SCM-302177", "SCM-302171"}
    assert "selected" not in dob and "selected" not in member   # never auto-select
