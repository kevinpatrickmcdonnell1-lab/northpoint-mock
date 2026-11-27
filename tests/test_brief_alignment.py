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
