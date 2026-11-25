"""Phase 5 walkthrough through the HTTP UI (P01, P03, P04, P05, P08)."""
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client(svc):
    app.state.svc = svc
    with TestClient(app) as c:
        yield c
    del app.state.svc


def test_queue_and_tabs(client):
    r = client.get("/")
    assert r.status_code == 200 and "SYNTHETIC – MOCK DATA" in r.text
    assert all(f"P{i:02d}" in r.text for i in range(1, 11))
    assert "SC-CARD-1.1" in r.text and "2026-10-01" in r.text
    ready = client.get("/?owner=ready").text
    assert "P02" in ready and "P01" not in ready
    triage = client.get("/?owner=triage").text
    assert "P03" in triage and "P08" in triage and "P02" not in triage
    assert "Re-evaluated all 10" in client.post("/rerun").text


def test_p01_walkthrough(client):
    r = client.post("/case/P01/handoff/schedule")
    assert "BLOCKED" in r.text and "G-PA-APPROVED" in r.text
    assert "Sent pa_request_packet" in client.post("/case/P01/handoff/pa_packet").text
    r = client.post("/case/P01/simulate", data={"type": "PAYER_PA_DECISION", "decision": "approved",
                                                 "actor_role": "Auth Specialist", "note": "mock"})
    assert "SIMULATED event EV-0001" in r.text and "SIMULATED – not from PDF" in r.text
    r = client.post("/case/P01/handoff/schedule")
    assert "Sent stress_echo_ready_to_schedule" in r.text
    assert "already recorded" in client.post("/case/P01/handoff/schedule").text.lower()
    out = client.get("/outbox/HO-P01-SCHED-1").json()
    assert out["simulated_dependencies"][0]["simulated"] is True
    assert "HO-P01-SCHED-1" in client.get("/outbox").text


def test_p03_urgent(client):
    r = client.get("/case/P03")
    assert "PRI-1" in r.text and "Notify cardiology clinical triage owner now" in r.text
    assert "Run E&amp;B (refresh stale response)" in r.text
    assert "Sent urgent_triage_notification" in client.post("/case/P03/handoff/triage").text
    r = client.post("/case/P03/simulate", data={"type": "CLINICAL_REVIEW_DECISION",
                                                 "decision": "reviewed_hold",
                                                 "actor_role": "Clinical Triage RN", "note": "seen"})
    assert "reviewed and placed a hold" in r.text


def test_p04_identity(client):
    r = client.get("/case/P04")
    assert "1951-03-14" in r.text and "1951-04-13" in r.text and "Never auto-selected" in r.text
    r = client.post("/case/P04/simulate", data={"type": "IDENTITY_RESOLUTION",
                                                 "actor_role": "Intake Coordinator",
                                                 "confirmed_dob": "1951-03-14"})
    assert "Not recorded" in r.text                          # note + Epic ref required
    client.post("/case/P04/simulate", data={
        "type": "IDENTITY_RESOLUTION", "actor_role": "Intake Coordinator",
        "confirmed_dob": "1951-03-14", "confirmed_member_id": "SCM-302177",
        "epic_reference": "EPIC-REG-77", "note": "Confirmed in Epic"})
    r = client.post("/case/P04/simulate", data={
        "type": "BENEFITS_RESPONSE", "actor_role": "Insurance Benefits Manager (mock)",
        "response_date": "2026-10-01", "payer": "Summit Crest",
        "plan_product_raw": "Summit Crest Medicare Advantage", "member_id": "SCM-302177", "active": "true"})
    assert "Prepare PA submission packet" in r.text


def test_p05_and_page_viewer(client):
    r = client.get("/case/P05")
    assert "HMO-1" in r.text and "Request current resting ECG report" in r.text
    q = "Clinician interpretation: equivocal"
    r = client.get("/page/P05_packet.pdf/7", params={"q": q})
    assert r.status_code == 200 and "<mark>" in r.text and "Found on this page" in r.text
    assert client.get("/pages/P05_packet.pdf/7.png").headers["content-type"] == "image/png"
    assert client.get("/page/..%2Fsecret/1").status_code == 404


def test_p08_cleared(client):
    assert "Unable to determine" in client.get("/case/P08").text
    client.post("/case/P08/simulate", data={"type": "CLINICAL_REVIEW_DECISION",
                                             "decision": "cleared_for_scheduling",
                                             "actor_role": "Clinical Triage RN"})
    assert "Sent stress_echo_ready_to_schedule" in client.post("/case/P08/handoff/schedule").text


def test_unsigned_and_raw_tracing_labelled(client):
    assert "unsigned, not used as evidence" in client.get("/case/P07").text
    assert "present, not used (raw tracing)" in client.get("/case/P10").text


def test_metrics_page(client):
    r = client.get("/metrics")
    assert "10-case mock illustration, not a forecast" in r.text and "Gate leaks" in r.text
