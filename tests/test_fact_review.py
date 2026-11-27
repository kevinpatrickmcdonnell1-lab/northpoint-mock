"""Human review of extracted facts (fact checks, confirmations, evidence-backed corrections).

Motivating case: the R01 stress echo order's `signer` quote cited "09/20/2026" (the date
signed), was signed off in the bulk review, and was only caught by eye on 2026-11-27.
"""
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import handoffs
from app.engine import evaluate
from app.fact_review import apply_reviews, check_facts, dates_in, open_flags
from app.main import app
from app.pdfs import MOCK_EXTRACTIONS, REAL_EXTRACTIONS, load_extractions
from app.service import Service
from tests.helpers import FACTS, POLICY, WORKFLOW, mutate

REAL = load_extractions(REAL_EXTRACTIONS) if any(REAL_EXTRACTIONS.glob("*.json")) else {}
needs_real = pytest.mark.skipif(not REAL, reason="real packets not present")

ORDER_QUOTE = "Procedure: Outpatient stress echocardiogram"   # P02-D2, p.2


def reintroduce_signer_bug(data: dict) -> None:
    """Put back the 2026-11-27 bug: R01's order signer quote cites only the date signed."""
    d = next(x for x in data["documents"] if x["doc_id"] == "R01-D3")
    d["quotes"] = [q for q in d["quotes"] if q["field"] not in ("signer", "date")]
    d["quotes"].append({"field": "signer", "page": 2, "text": "09/20/2026"})


def mutate_real(key, fn):
    data = REAL[key].model_dump(mode="json")
    fn(data)
    return type(REAL[key]).model_validate(data)


@pytest.fixture
def buggy_svc(tmp_path):
    """A service over the real packets with the R01 signer bug put back."""
    ext = tmp_path / "ext"
    ext.mkdir()
    for k, ef in REAL.items():
        data = ef.model_dump(mode="json")
        if k == "R01":
            reintroduce_signer_bug(data)
        (ext / f"{k}.json").write_text(json.dumps(data))
    s = Service(db_path=tmp_path / "t.db", outbox_dir=tmp_path / "out", extractions_dir=ext)
    s.seed()
    yield s
    s.store.close()


def correct_p02_signed(svc, **over):
    kw = dict(key="P02", doc_id="P02-D2", field="signed", action="correct", value="false",
              quote_page=2, quote_text=ORDER_QUOTE, reason="No signature on the order page",
              actor_role="Intake Coordinator", actor_name="J. Test")
    kw.update(over)
    return svc.review_fact(**kw)


# ---------------------------------------------------------------- fact checks

@needs_real
def test_check_catches_the_signer_bug_that_bulk_review_missed():
    flags = check_facts(mutate_real("R01", reintroduce_signer_bug))
    hit = [f for f in flags if f.code == "SIGNER_NAME_NOT_IN_QUOTE"]
    assert [(f.doc_id, f.field) for f in hit] == [("R01-D3", "signer")]
    assert "Rohan Devlin" in hit[0].message and "09/20/2026" in hit[0].message


def test_mock_packets_raise_no_flags():
    assert {k: check_facts(ef) for k, ef in FACTS.items() if check_facts(ef)} == {}


@needs_real
def test_real_packets_flag_only_weak_copied_text_quotes():
    """The 11 quotes on the real packets that cite a heading or fragment instead of the
    value (same class as the signer bug). Left in the data for in-app review."""
    flags = [f for ef in REAL.values() for f in check_facts(ef)]
    assert {f.code for f in flags} == {"VALUE_NOT_IN_QUOTE"}
    assert len(flags) == 11
    assert sum(f.field == "procedure" for f in flags) == 9


def test_date_quote_mismatch_flagged_and_spelled_dates_parse():
    def bad(d):
        doc = next(x for x in d["documents"] if x["doc_id"] == "P02-D5")
        doc["fields"]["response_date"] = "2026-09-24"
    flags = check_facts(mutate("P02", bad))
    assert [(f.code, f.field) for f in flags] == [("DATE_NOT_IN_QUOTE", "response_date")]
    assert dates_in("Today, September 30, he reports") == [(None, 9, 30)]
    assert dates_in("on 09/20/2026 and 2026-09-21") == [(2026, 9, 21), (2026, 9, 20)]


def test_quote_not_on_page_flagged():
    def bad(d):
        d["documents"][1]["quotes"][1]["text"] = "Indication: angina at rest"
    flags = check_facts(mutate("P02", bad))
    assert [(f.code, f.doc_id) for f in flags] == [("QUOTE_NOT_ON_PAGE", "P02-D2")]


# ---------------------------------------------------------------- corrections

def test_correction_changes_results_through_the_rules_and_source_stays_put(svc):
    before_json = svc.store.case_json("P02")
    assert svc.state("P02").scheduling_sendable
    r = correct_p02_signed(svc)
    assert r.review_id == "FR-0001" and r.value is False
    s = svc.state("P02")
    assert not s.scheduling_sendable
    # same result as running the engine on the reviewed facts directly
    assert s == evaluate(apply_reviews(FACTS["P02"], [r]), [], POLICY, WORKFLOW)
    assert svc.store.case_json("P02") == before_json            # invariant 4
    assert svc.store.case("P02").documents[1].signed is True
    audit = svc.store.audit_rows("P02", "correction")[0]["detail"]
    assert audit["action"] == "correct" and audit["before"] is True and audit["after"] is False
    assert svc.store.audit_rows("P02", "state_change")           # diff recorded


def test_correction_quote_replaces_that_fields_evidence(svc):
    correct_p02_signed(svc)
    d = svc.case("P02").documents[1]
    assert [(q.field, q.text) for q in d.quotes if q.field == "signed"] == [("signed", ORDER_QUOTE)]
    assert len([q for q in d.quotes if q.field == "procedure"]) == 1   # other fields untouched


@pytest.mark.parametrize("over, msg", [
    ({"quote_text": "Signed by nobody at all"}, "not found on p.2"),
    ({"quote_page": 3}, "not one of this document's pages"),
    ({"quote_text": ""}, "needs the page text"),
    ({"reason": ""}, "needs a reason"),
    ({"actor_name": " "}, "needs the reviewer's name"),
    ({"actor_role": "Scheduler"}, "cannot review facts"),
    ({"field": "dob"}, "Identity resolution"),
    ({"field": "favourite_colour"}, "not a fact on"),
    ({"field": "indication", "value": "chest pain"}, "clinical fact"),
    ({"doc_id": "P02-D9"}, "No document P02-D9"),
])
def test_bad_corrections_are_refused_and_record_nothing(svc, over, msg):
    with pytest.raises(ValueError, match=msg):
        correct_p02_signed(svc, **over)
    assert svc.store.reviews("P02") == [] and svc.store.audit_rows("P02", "correction") == []


def test_invalid_value_refused(svc):
    with pytest.raises(ValueError, match="not a valid value for interpretation"):
        svc.review_fact("P02", "P02-D7", "interpretation", "correct", value="sort of fine",
                        quote_page=7, quote_text="Clinician interpretation: nondiagnostic",
                        reason="x", actor_role="Cardiologist", actor_name="Dr T")


def test_clinical_role_may_correct_clinical_fact(svc):
    r = svc.review_fact("P02", "P02-D2", "indication", "correct", value="exertional dyspnea",
                        quote_page=2, quote_text="Indication: exertional dyspnea", reason="trim",
                        actor_role="Clinical Review RN", actor_name="RN T")
    assert r.value == "exertional dyspnea"


def test_correction_after_handoff_recalls_scheduling(svc):
    assert handoffs.send(svc, "P02", "schedule")["result"] == "sent"
    correct_p02_signed(svc)
    assert any(o["work_item"]["type"] == handoffs.RECALL_TYPE for o in svc.store.outbox("P02"))


def test_reviews_are_append_only(svc):
    correct_p02_signed(svc)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        svc.store.db.execute("UPDATE fact_reviews SET review_json='{}'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        svc.store.db.execute("DELETE FROM fact_reviews")


# ---------------------------------------------------------------- flags + confirmations

@needs_real
def test_confirm_on_a_flagged_fact_needs_a_note_then_closes_the_flag(buggy_svc):
    assert [(f.doc_id, f.field) for f in buggy_svc.open_flags("R01")] == [("R01-D3", "signer")]
    with pytest.raises(ValueError, match="flagged"):
        buggy_svc.review_fact("R01", "R01-D3", "signer", "confirm",
                              actor_role="Intake Coordinator", actor_name="J. Test")
    buggy_svc.review_fact("R01", "R01-D3", "signer", "confirm", reason="Signature block is right above",
                          actor_role="Intake Coordinator", actor_name="J. Test")
    assert buggy_svc.open_flags("R01") == []
    assert buggy_svc.case("R01") == buggy_svc.store.case("R01")    # confirm changes no fact


@needs_real
def test_fixing_the_quote_clears_the_flag_without_changing_the_value(buggy_svc):
    before = buggy_svc.state("R01")
    buggy_svc.review_fact("R01", "R01-D3", "signer", "correct", value="Rohan Devlin, MD",
                          quote_page=2, quote_text="Electronically signed by Rohan Devlin, MD",
                          reason="Quote cited the date, not the signature",
                          actor_role="Intake Coordinator", actor_name="J. Test")
    assert check_facts(buggy_svc.case("R01")) == []
    assert buggy_svc.state("R01").gates == before.gates


def test_flags_never_reach_the_engine(svc):
    """Open flags route work to people; with no corrections the result is the source result."""
    for k in svc.store.keys():
        assert svc.state(k) == evaluate(FACTS[k], [], POLICY, WORKFLOW)


def test_open_flags_closed_by_any_review():
    from app.fact_review import FactReview, Flag
    from datetime import datetime
    f = Flag(doc_id="D", field="signer", code="SIGNER_NAME_NOT_IN_QUOTE", page=1, message="m")
    r = FactReview(review_id="FR-1", patient_key="X", doc_id="D", field="signer", action="confirm",
                   actor_role="Intake Coordinator", actor_name="n", recorded_at=datetime(2026, 1, 1))
    assert open_flags([f], []) == [f] and open_flags([f], [r]) == []


# ---------------------------------------------------------------- HTTP

@pytest.fixture
def client(svc):
    app.state.svc = svc
    with TestClient(app) as c:
        yield c
    del app.state.svc


def test_http_review_flow(client):
    page = client.get("/page/P02_packet.pdf/2?case=P02").text
    assert 'action="/case/P02/fact-review"' in page and "Confirm" in page
    r = client.post("/case/P02/fact-review", data={
        "doc_id": "P02-D2", "field": "signed", "action": "correct", "value": "false",
        "quote_page": "2", "quote_text": ORDER_QUOTE, "reason": "No signature",
        "actor_role": "Intake Coordinator", "actor_name": "J. Test"})
    assert r.status_code == 200 and "FR-0001" in r.text
    assert "corrected by J. Test" in client.get("/page/P02_packet.pdf/2?case=P02").text
    bad = client.post("/case/P02/fact-review", data={
        "doc_id": "P02-D2", "field": "signed", "action": "correct", "value": "false",
        "quote_page": "2", "quote_text": "nope", "reason": "x",
        "actor_role": "Intake Coordinator", "actor_name": "J. Test"})
    assert bad.status_code == 200 and "Not recorded" in bad.text and "not found on p.2" in bad.text


def test_handoff_payload_lists_applied_corrections(svc):
    svc.review_fact("P02", "P02-D2", "indication", "correct", value="exertional dyspnea",
                    quote_page=2, quote_text="Indication: exertional dyspnea", reason="trim",
                    actor_role="Clinical Review RN", actor_name="RN T")
    r = handoffs.send(svc, "P02", "schedule")
    assert r["result"] == "sent"
    assert [c["review_id"] for c in r["payload"]["fact_corrections"]] == ["FR-0001"]


@needs_real
def test_queue_fact_review_tab(buggy_svc):
    app.state.svc = buggy_svc
    try:
        with TestClient(app) as c:
            buggy_svc.evaluate_all()
            t = c.get("/?owner=facts").text
            assert "R01" in t and "fact flag" in t
    finally:
        del app.state.svc
