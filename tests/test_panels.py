"""Personas step 2: action panels for Clinical Triage and Intake (one item, one decision, next)."""
import re
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client(svc):
    app.state.svc = svc
    with TestClient(app) as c:
        yield c
    del app.state.svc


def act_as(c, key):
    c.post("/persona", data={"persona": key})


def location(r):
    u = urlparse(r.headers["location"])
    return u.path, {k: v[0] for k, v in parse_qs(u.query).items()}


def preselected(html):
    """A bare `checked` attribute on an input (not the word inside a placeholder)."""
    return re.search(r"<input[^>]*\schecked(?=[\s>=/])", html) is not None


def section(html, name):
    return html.split(f'id="{name}"')[1].split("</section>")[0]


# ---------------------------------------------------------------- clinical triage

def test_triage_panel_shows_only_clinical_evidence_and_no_default_decision(client):
    act_as(client, "triage")
    page = client.get("/work/P03/CLIN_URGENT").text
    assert "Urgent review" in page and "New episodes of chest pain at rest" in page
    assert "/pages/P03_packet.pdf/7.png" in page                      # the source page itself
    assert "Harborstone" not in page and "COV_PENDING" not in page    # invariant 5, on screen
    panel = section(page, "decide")
    assert panel.count('type="radio"') == 3 and not preselected(panel)


def test_undetermined_shows_both_sources_side_by_side(client):
    act_as(client, "triage")
    page = client.get("/work/P08/CLIN_UNDETERMINED").text
    assert "/pages/P08_packet.pdf/1.png" in page and "/pages/P08_packet.pdf/6.png" in page
    assert "stable" in page and "worsening" in page


def test_clinical_decision_needs_a_note_then_records_and_moves_on(client, svc):
    act_as(client, "triage")
    r = client.post("/work/P03/CLIN_URGENT", data={"op": "decide", "decision": "reviewed_hold"},
                    follow_redirects=False)
    path, q = location(r)
    assert path == "/work/P03/CLIN_URGENT" and q["msg"].startswith("Not recorded") and "note" in q["msg"]
    assert svc.store.events("P03") == []
    r = client.post("/work/P03/CLIN_URGENT", data={"op": "decide", "decision": "reviewed_hold",
                                                   "note": "Called patient; ED advice given"},
                    follow_redirects=False)
    path, q = location(r)
    ev = svc.store.events("P03")[0]
    assert (ev.type, ev.decision, ev.actor_role, ev.actor_name) == (
        "CLINICAL_REVIEW_DECISION", "reviewed_hold", "Clinical Triage RN", "Mara Quill, RN")
    assert path == "/work/P08/CLIN_UNDETERMINED"      # next case, not the same one again
    assert "EV-0001" in q["msg"]


def test_decision_without_choice_refused(client, svc):
    act_as(client, "triage")
    r = client.post("/work/P08/CLIN_UNDETERMINED", data={"op": "decide", "note": "x"})
    assert "Not recorded" in r.text and svc.store.events() == []


# ---------------------------------------------------------------- intake: identity

def test_identity_panel_shows_each_value_with_pages_and_never_preselects(client):
    act_as(client, "intake")
    page = client.get("/work/P04/ID_CONFLICT").text
    for v in ("1951-03-14", "1951-04-13", "SCM-302177", "SCM-302171"):
        assert v in page
    assert "/pages/P04_packet.pdf/3.png" in page
    assert not preselected(section(page, "decide"))                    # invariant 7


def test_identity_needs_every_conflicting_field_then_records(client, svc):
    act_as(client, "intake")
    base = {"op": "identity", "epic_reference": "EPIC-REG-77", "note": "Checked Epic registration"}
    r = client.post("/work/P04/ID_CONFLICT", data={**base, "confirmed_dob": "1951-03-14"})
    assert "Not recorded" in r.text and "member_id" in r.text and svc.store.events() == []
    client.post("/work/P04/ID_CONFLICT", data={**base, "confirmed_dob": "1951-03-14",
                                               "confirmed_member_id": "__other",
                                               "confirmed_member_id_other": "SCM-302177"})
    ev = svc.store.events("P04")[0]
    assert ev.confirmed == {"dob": "1951-03-14", "member_id": "SCM-302177"}
    assert (ev.actor_role, ev.actor_name, ev.epic_reference) == ("Intake Coordinator", "Jo Rivera", "EPIC-REG-77")


# ---------------------------------------------------------------- intake: coverage

def test_coverage_panel_lists_each_coverage_document(client):
    act_as(client, "intake")
    page = client.get("/work/P09/COV_PENDING_PRODUCT").text
    assert "Harborstone Health Commercial" in page and "does not name an exact plan product" in page
    assert page.count('class="cov-row"') == 3


def test_coverage_panel_records_benefits_response(client, svc):
    act_as(client, "intake")
    client.post("/work/P09/COV_PENDING_PRODUCT", data={
        "op": "benefits", "response_date": "2026-09-30", "payer": "Harborstone Health",
        "plan_product_raw": "Harborstone Health Commercial PPO", "member_id": "HHC-1", "active": "true"})
    ev = svc.store.events("P09")[0]
    assert (ev.type, ev.actor_role, ev.actor_name) == ("BENEFITS_RESPONSE", "Insurance Benefits Manager (mock)", "Jo Rivera")


# ---------------------------------------------------------------- intake: requests

def test_request_panel_previews_then_sends_and_item_waits(client, svc):
    act_as(client, "intake")
    page = client.get("/work/P03/EVID_MISSING_REPORT").text
    assert "resting ECG" in page and "Ordering clinician: A. Lindqvist, MD" in page
    client.post("/work/P03/EVID_MISSING_REPORT", data={"op": "request"})
    sent = [o for o in svc.store.outbox("P03") if o["work_item"]["type"] == "missing_info_request"]
    assert len(sent) == 1
    work = client.get("/work").text
    active, waiting = work.split('id="waiting"')
    assert 'data-work="P03:EVID_MISSING_REPORT"' in waiting and 'data-work="P03:EVID_MISSING_REPORT"' not in active
    assert sent[0]["handoff_id"] in waiting
    assert "Sent" in client.get("/work/P03/EVID_MISSING_REPORT").text


# ---------------------------------------------------------------- access + navigation

def test_panel_only_for_the_owner(client):
    act_as(client, "triage")
    r = client.get("/work/P04/ID_CONFLICT", follow_redirects=False)
    path, q = location(r)
    assert path == "/work" and "not on your list" in q["msg"]
    assert client.post("/work/P04/ID_CONFLICT", data={"op": "identity"}, follow_redirects=False).status_code == 303


def test_presenter_mode_panel_goes_to_case(client):
    r = client.get("/work/P03/CLIN_URGENT", follow_redirects=False)
    assert location(r)[0] == "/case/P03"


def test_work_list_links_to_panels_and_case_for_the_rest(client):
    act_as(client, "intake")
    work = client.get("/work").text
    assert 'href="/work/P04/ID_CONFLICT"' in work and 'href="/work/P09/COV_PENDING_PRODUCT"' in work
    act_as(client, "auth")
    assert 'href="/work/P01/PA_READY"' in client.get("/work").text   # Auth panels arrived in step 3


def test_wrong_op_for_item_refused(client, svc):
    act_as(client, "intake")
    r = client.post("/work/P04/ID_CONFLICT", data={"op": "decide", "decision": "cleared_for_scheduling", "note": "x"})
    assert "Not recorded" in r.text and svc.store.events() == []


def test_panel_shows_position_in_list(client):
    act_as(client, "intake")
    page = client.get("/work/P04/ID_CONFLICT").text
    assert re.search(r"Item \d+ of \d+", page)
