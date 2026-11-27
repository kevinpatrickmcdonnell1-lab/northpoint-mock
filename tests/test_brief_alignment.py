"""Brief alignment (fix/brief-alignment): one reproducing test group per brief fix.

Each test here was written first and confirmed failing before its fix.
"""
import json

import pytest

from app.handoffs import send
from app.pdfs import REAL_EXTRACTIONS, real_ready
from app.service import Service

needs_real = pytest.mark.skipif(not real_ready(), reason="real PDFs not in data/packets")
PA_CODES = {"PA_READY", "PA_AWAITING", "PA_DENIED"}


def _real_service(tmp_path, mutate=None):
    """A service over the real set; `mutate(key, data)` may change one packet's facts first."""
    src = tmp_path / "extractions"
    src.mkdir()
    for f in sorted(REAL_EXTRACTIONS.glob("*.json")):
        data = json.loads(f.read_text())
        if mutate:
            mutate(f.stem, data)
        (src / f.name).write_text(json.dumps(data))
    s = Service(db_path=tmp_path / "t.db", outbox_dir=tmp_path / "outbox", extractions_dir=src)
    s.seed()
    return s


# --------------------------------------------------------------------------- #
# Brief fix 1. No-PA plans must still gate on evidence (R5)                    #
# --------------------------------------------------------------------------- #

def _r05_nonexertional(key, data):
    if key == "R05":
        a = next(d for d in data["documents"]
                 if d["doc_type"] == "clinician_note" and d["fields"].get("is_assessment"))
        a["fields"]["complaint_type"] = "nonexertional"


@needs_real
def test_brief1_no_pa_case_not_met_on_qual1_is_never_sendable(tmp_path):
    s = _real_service(tmp_path, _r05_nonexertional)
    st = s.state("R05")
    assert st.pa_requirement == "not_required"
    assert st.criteria["QUAL-1"].status == "NM"
    # clear the clinical gate so the only remaining hold is the evidence gate
    s.simulate("R05", "CLINICAL_REVIEW_DECISION", "cleared_for_scheduling", "Clinical Review RN")
    st = s.state("R05")
    assert [g.gate for g in st.gates if not g.passed] == ["G-EVID"]
    codes = [a.reason_code for a in st.actions]
    assert "EVID_NOT_MET" in codes and not PA_CODES & set(codes)
    assert not st.scheduling_sendable and not st.pa_prep_ready
    r = send(s, "R05", "schedule")
    assert r["result"] == "blocked" and r["failing_gates"] == ["G-EVID"]
    blocked = s.store.audit_rows("R05", "handoff_blocked")
    assert blocked and blocked[0]["detail"]["failing_gates"] == ["G-EVID"]
    assert s.store.outbox_count("R05", "stress_echo_ready_to_schedule") == 0
    s.store.close()


@needs_real
def test_brief1_unmodified_r05_still_supported_on_every_gating_rule(tmp_path):
    s = _real_service(tmp_path)
    st = s.state("R05")
    assert all(st.criteria[r].status == "S" for r in ("DOC-1", "QUAL-1", "QUAL-2", "REPEAT-1"))
    assert next(g for g in st.gates if g.gate == "G-EVID").passed
    s.store.close()


@needs_real
def test_brief1_case_page_ppo_note(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import app
    s = _real_service(tmp_path)
    app.state.svc = s
    try:
        with TestClient(app) as c:
            html = c.get("/case/R05").text
        assert ("No-PA plan: the PA step is skipped; identity, coverage, evidence and clinical checks "
                "still apply.") in html
        assert "shown for visibility" not in html
    finally:
        del app.state.svc
        s.store.close()


# --------------------------------------------------------------------------- #
# Brief fix 2. The audit must record the next owner (R6)                       #
# --------------------------------------------------------------------------- #

import re  # noqa: E402


def test_brief2_every_seeded_evaluation_records_next_owner(svc):
    for key in svc.store.keys():
        ev = svc.store.audit_rows(key, "evaluation")[0]["detail"]
        st = svc.state(key)
        expected = st.actions[0].owner_role if st.actions else None
        assert ev["next_owner"] == expected, key
        assert ev["next_actions"] == [{"reason_code": a.reason_code, "owner_role": a.owner_role,
                                       "tennr_product": a.tennr_product} for a in st.actions]
        assert ev["actions"] == [a.reason_code for a in st.actions]       # old list kept


def test_brief2_evaluations_still_carry_file_and_page_refs(svc):
    for key in svc.store.keys():
        ev = svc.store.audit_rows(key, "evaluation")[0]["detail"]
        refs = [r for v in ev["evidence_refs"].values() for r in v]
        assert refs, key
        assert all(re.fullmatch(r".+\.pdf p\.\d+", r) for r in refs), refs


def test_brief2_owner_change_shows_in_diff(svc):
    # P03: urgent -> first owner Clinical Triage RN. Triage clears it -> owner moves to Intake.
    before = svc.store.audit_rows("P03", "evaluation")[0]["detail"]["next_owner"]
    assert before == "Clinical Triage RN"
    svc.simulate("P03", "CLINICAL_REVIEW_DECISION", "cleared_for_scheduling", "Clinical Triage RN")
    ev = svc.store.audit_rows("P03", "evaluation")[0]["detail"]
    assert ev["next_owner"] == "Intake Coordinator"
    assert ev["diff"]["next_owner"] == {"from": "Clinical Triage RN", "to": "Intake Coordinator"}
    assert svc.store.audit_rows("P03", "state_change")[0]["detail"]["diff"]["next_owner"]["to"] == \
        "Intake Coordinator"


@needs_real
def test_brief2_r01_identity_resolution_records_next_owner(tmp_path):
    s = _real_service(tmp_path)
    before = s.store.audit_rows("R01", "evaluation")[0]["detail"]["next_owner"]
    s.simulate("R01", "IDENTITY_RESOLUTION", "confirmed", "Intake Coordinator",
               note="Confirmed DOB in Epic registration", confirmed={"dob": "1958-06-12"},
               epic_reference="EPIC-REG-R01")
    ev = s.store.audit_rows("R01", "evaluation")[0]["detail"]
    st = s.state("R01")
    assert st.identity.status == "verified"
    assert ev["next_owner"] == st.actions[0].owner_role
    if ev["next_owner"] != before:
        assert ev["diff"]["next_owner"] == {"from": before, "to": ev["next_owner"]}
    else:
        assert "next_owner" not in ev["diff"]
    assert ev["diff"]["actions"]["from"][0] == "ID_CONFLICT"                  # the owner's task changed
    s.store.close()


def test_brief2_case_page_audit_rows_show_next_owner(svc):
    from fastapi.testclient import TestClient
    from app.main import app
    app.state.svc = svc
    try:
        with TestClient(app) as c:
            html = c.get("/case/P03").text
        assert "next owner: Clinical Triage RN" in html
    finally:
        del app.state.svc
