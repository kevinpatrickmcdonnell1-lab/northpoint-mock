"""UI rework (branch ui/tennr-demo): three-layer case page, value lens, page facts, one-click-one-write."""
import re
import threading

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client(svc):
    app.state.svc = svc
    with TestClient(app) as c:
        yield c
    del app.state.svc


def test_queue_shows_kpis_layers_and_brand(client):
    html = client.get("/").text
    assert "Northpoint" in html and "Tennr" in html and "SYNTHETIC – MOCK DATA" in html
    for label in ("Sendable to scheduling", "Ready for PA prep", "Gate leaks (must be 0)",
                  "Document extraction", "Policy engine", "Case state &amp; audit"):
        assert label in html
    # mock baseline: 1 sendable, 1 PA-ready, 8 held
    kpis = re.findall(r'<div class="n">(\d+)</div><div class="l">([^<]+)</div>', html)
    assert ("1", "Sendable to scheduling") in kpis and ("1", "Ready for PA prep") in kpis
    assert ("8", "Held · each with an owner") in kpis


def test_case_page_has_three_layers_and_value_lens(client):
    html = client.get("/case/P04").text
    for anchor in ('id="layer-1"', 'id="layer-2"', 'id="layer-3"', 'id="changed"', 'id="handoffs"',
                   'id="simulate"'):
        assert anchor in html
    assert html.count('class="lens"') == 3                       # one value-lens callout per layer
    assert "What Northpoint measures" in html and "Question for Intake" in html
    assert "Identifiers as each page states them" in html and "disagree" in html
    assert "Never auto-selected" in html
    assert "Baseline state" in html                              # no events yet


def test_what_changed_card_after_event(client):
    r = client.post("/case/P04/simulate", data={
        "type": "IDENTITY_RESOLUTION", "actor_role": "Intake Coordinator", "confirmed_dob": "1951-03-14",
        "confirmed_member_id": "SCM-302177", "epic_reference": "EPIC-REG-77", "note": "Confirmed in Epic"})
    assert "What the last event changed" in r.text
    assert re.search(r"<b>Identity</b></td><td><span class=\"from\">conflicting</span> → "
                     r"<span class=\"to\">verified</span>", r.text)
    assert 'data-to="changed"' in r.text                         # page scrolls to the change


def test_page_viewer_lists_facts_on_that_page(client):
    r = client.get("/page/P05_packet.pdf/7", params={"q": "Clinician interpretation: equivocal", "case": "P05"})
    assert "Facts extracted from this page" in r.text and 'class="q cited"' in r.text
    assert re.findall(r'href="(/case/[^"]+)"', r.text) == ["/case/P05"]


def test_concurrent_clicks_record_one_handoff_and_never_500(client, svc):
    results = []

    def click():
        results.append(client.post("/case/P03/handoff/triage").status_code)

    threads = [threading.Thread(target=click) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [200] * 6
    assert svc.store.outbox_count("P03", "urgent_triage_notification") == 1


def test_concurrent_simulate_events_get_unique_ids(client, svc):
    def click():
        client.post("/case/P03/simulate", data={"type": "CLINICAL_REVIEW_DECISION",
                                                 "decision": "reviewed_hold", "actor_role": "Clinical Triage RN"})

    threads = [threading.Thread(target=click) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    ids = [e.event_id for e in svc.store.events("P03")]
    assert len(ids) == 5 and len(set(ids)) == 5


# ------------------------------------------------------------------ reset = packets arrived, engine not run

@pytest.fixture
def fresh(tmp_path, monkeypatch):
    """A client whose app was just reset: packets loaded, engine not yet run."""
    import app.main as m
    from app.pdfs import MOCK_EXTRACTIONS
    from app.service import Service
    monkeypatch.setattr(m, "DB_PATH", tmp_path / "fresh.db")
    monkeypatch.setattr(m, "DEFAULT_OUTBOX", tmp_path / "outbox")
    s = Service(db_path=tmp_path / "fresh.db", outbox_dir=tmp_path / "outbox", extractions_dir=MOCK_EXTRACTIONS)
    s.seed()
    s.simulate("P08", "CLINICAL_REVIEW_DECISION", "cleared_for_scheduling", "Clinical Triage RN")
    app.state.svc = s
    monkeypatch.setattr(m, "active_extractions_dir", lambda: MOCK_EXTRACTIONS, raising=False)
    with TestClient(app) as c:
        yield c
    if hasattr(app.state, "svc"):
        app.state.svc.store.close()
        del app.state.svc


def test_reset_leaves_packets_loaded_but_not_evaluated(fresh):
    r = fresh.post("/reset")
    assert r.status_code == 200 and "Demo reset" in r.text
    svc = app.state.svc
    assert svc.store.event_count() == 0 and svc.store.outbox() == []
    assert svc.store.audit_rows(kind="evaluation") == []          # engine has not run
    assert not svc.engine_has_run()
    html = r.text
    assert "Run all 10 through engine" in html and "Awaiting engine" in html
    assert all(f"P{i:02d}" in html for i in range(1, 11))       # the packets are there
    for word in ("PRI-1", "PRI-3", "conflicting", "Insufficient", "G-PA-APPROVED"):
        assert word not in html                                   # but no engine results yet


def test_case_before_run_shows_extraction_only(fresh):
    fresh.post("/reset")
    html = fresh.get("/case/P04").text
    assert 'id="layer-1"' in html and "Identifiers as each page states them" in html
    assert "Not yet run through the engine" in html
    assert "PRI-3" not in html and "Never auto-selected" not in html and 'id="simulate"' not in html


def test_run_after_reset_populates_and_audits(fresh):
    fresh.post("/reset")
    r = fresh.post("/rerun")
    assert "Re-evaluated all 10" in r.text and "PRI-1" in r.text
    assert app.state.svc.engine_has_run()
    assert len(app.state.svc.store.audit_rows(kind="evaluation")) == 10
    assert "Re-run all 10 through engine" in r.text
