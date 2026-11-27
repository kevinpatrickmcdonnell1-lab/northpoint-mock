"""Personas: sign-in, server-side attribution, permissions and the My Work list.

Presenter mode (no persona) is the old behaviour and is covered by the rest of the suite.
"""
import re

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.pdfs import REAL_EXTRACTIONS
from app.personas import COOKIE
from app.service import Service


@pytest.fixture
def client(svc):
    app.state.svc = svc
    with TestClient(app) as c:
        yield c
    del app.state.svc


def act_as(c, key):
    r = c.post("/persona", data={"persona": key}, follow_redirects=False)
    assert r.status_code == 303
    return r


def work_keys(html):
    return re.findall(r'data-work="(P\d+):([A-Z_0-9]+)"', html)


def test_sign_in_sets_cookie_and_lands_on_work(client):
    r = act_as(client, "intake")
    assert r.headers["location"] == "/work" and COOKIE in r.headers["set-cookie"]
    page = client.get("/work").text
    assert "Jo Rivera" in page and "Intake Coordinator" in page
    r = act_as(client, "ops")
    assert r.headers["location"] == "/metrics"


def test_clearing_persona_returns_to_presenter_mode(client):
    act_as(client, "intake")
    r = client.post("/persona", data={"persona": ""}, follow_redirects=False)
    assert r.headers["location"] == "/"
    assert "Presenter (all roles)" in client.get("/").text


def test_intake_work_list_is_only_intake_items_with_other_holds_read_only(client):
    act_as(client, "intake")
    page = client.get("/work").text
    items = work_keys(page)
    assert {k for k, _ in items} == {"P03", "P04", "P05", "P06", "P09"}
    assert all(not code.startswith(("CLIN_", "PA_", "SCHED_", "EVID_NOT")) for _, code in items)
    assert items[0][0] == "P03"                     # urgent case's admin work first
    assert "Also held by Clinical Triage RN" in page


def test_triage_list_orders_urgent_then_undetermined_and_hides_insurance(client):
    act_as(client, "triage")
    page = client.get("/work").text
    assert work_keys(page) == [("P03", "CLIN_URGENT"), ("P08", "CLIN_UNDETERMINED")]
    assert "COV_PENDING_STALE" not in page and "Harborstone" not in page


def test_cardiologist_sees_every_clinical_item_and_nothing_else(client):
    act_as(client, "cardiologist")
    assert {k for k, _ in work_keys(client.get("/work").text)} == {"P03", "P05", "P06", "P08"}


def test_auth_and_scheduler_lists(client):
    act_as(client, "auth")
    # all routine, so oldest first: P10 (13 bd), P07 (11), P01 (9)
    assert work_keys(client.get("/work").text) == [("P10", "EVID_NOT_MET"), ("P07", "EVID_NOT_MET"),
                                                   ("P01", "PA_READY")]
    act_as(client, "scheduler")
    assert work_keys(client.get("/work").text) == [("P02", "SCHED_READY")]


def test_server_attributes_events_to_the_persona_whatever_the_form_says(client, svc):
    act_as(client, "intake")
    r = client.post("/case/P04/simulate", data={
        "type": "IDENTITY_RESOLUTION", "actor_role": "Cardiologist", "actor_name": "Someone else",
        "confirmed_dob": "1965-03-14", "epic_reference": "EPIC-REG-1", "note": "checked"})
    assert "SIMULATED event" in r.text
    ev = svc.store.events("P04")[0]
    assert (ev.actor_role, ev.actor_name) == ("Intake Coordinator", "Jo Rivera")


def test_benefits_entered_by_intake_attributed_to_mock_ibm(client, svc):
    act_as(client, "intake")
    client.post("/case/P03/simulate", data={"type": "BENEFITS_RESPONSE", "response_date": "2026-09-30",
                                            "active": "true"})
    ev = svc.store.events("P03")[0]
    assert (ev.actor_role, ev.actor_name) == ("Insurance Benefits Manager (mock)", "Jo Rivera")


def test_persona_cannot_record_another_roles_decision(client, svc):
    act_as(client, "intake")
    r = client.post("/case/P03/simulate", data={"type": "CLINICAL_REVIEW_DECISION",
                                                 "decision": "cleared_for_scheduling"})
    assert "Not recorded" in r.text and "Intake Coordinator" in r.text
    assert svc.store.events("P03") == []


def test_handoff_refused_for_wrong_role_is_audited_and_records_nothing(client, svc):
    act_as(client, "auth")
    r = client.post("/case/P02/handoff/schedule")
    assert "Not recorded" in r.text
    assert svc.store.outbox("P02") == []
    blocked = svc.store.audit_rows("P02", "handoff_blocked")[0]
    assert blocked["actor"] == "Sam Okoro (Auth Specialist)" and "role" in blocked["detail"]["reason"]


def test_handoff_attributed_and_back_to_work(client, svc):
    act_as(client, "scheduler")
    r = client.post("/case/P02/handoff/schedule", data={"back": "/work"}, follow_redirects=False)
    from urllib.parse import parse_qs, urlparse
    loc = urlparse(r.headers["location"])
    assert loc.path == "/work" and parse_qs(loc.query)["msg"][0].startswith("P02: Sent")
    sent = svc.store.audit_rows("P02", "handoff_sent")[0]
    assert sent["actor"] == "Lee Hart (Scheduler)"


def test_read_only_personas_cannot_write(client, svc):
    act_as(client, "ops")
    assert "Not recorded" in client.post("/case/P02/handoff/schedule").text
    assert "Not recorded" in client.post("/case/P01/simulate", data={
        "type": "PAYER_PA_DECISION", "decision": "approved"}).text
    assert svc.store.outbox() == [] and svc.store.events() == []


def test_fact_review_attributed_to_persona(client, svc):
    act_as(client, "intake")
    client.post("/case/P02/fact-review", data={
        "doc_id": "P02-D2", "field": "procedure", "action": "confirm",
        "actor_role": "Cardiologist", "actor_name": "Spoof"})
    r = svc.store.reviews("P02")[0]
    assert (r.actor_role, r.actor_name) == ("Intake Coordinator", "Jo Rivera")
    act_as(client, "scheduler")
    out = client.post("/case/P02/fact-review", data={"doc_id": "P02-D2", "field": "procedure",
                                                     "action": "confirm"})
    assert "cannot review facts" in out.text and len(svc.store.reviews("P02")) == 1


def test_persona_forms_drop_role_pickers(client):
    act_as(client, "review")
    page = client.get("/case/P05").text
    assert "Recording as Theo Lund, RN (Clinical Review RN)" in page
    assert 'name="actor_role"' not in page.split('id="simulate"')[1].split("</section>")[0]


def test_work_before_engine_runs(tmp_path):
    from app.pdfs import MOCK_EXTRACTIONS
    s = Service(db_path=tmp_path / "t.db", outbox_dir=tmp_path / "o", extractions_dir=MOCK_EXTRACTIONS)
    s.seed(evaluate=False)
    app.state.svc = s
    try:
        with TestClient(app) as c:
            act_as(c, "intake")
            assert "engine has not run" in c.get("/work").text.lower()
    finally:
        del app.state.svc
        s.store.close()


@pytest.mark.skipif(not any(REAL_EXTRACTIONS.glob("*.json")), reason="real packets not present")
def test_fact_flags_routed_by_kind(tmp_path):
    s = Service(db_path=tmp_path / "t.db", outbox_dir=tmp_path / "o", extractions_dir=REAL_EXTRACTIONS)
    s.seed()
    app.state.svc = s
    try:
        with TestClient(app) as c:
            act_as(c, "intake")
            assert "R02:FACT_procedure" in c.get("/work").text.replace('data-work="', "")
            act_as(c, "triage")
            assert "FACT_procedure" not in c.get("/work").text
    finally:
        del app.state.svc
        s.store.close()
