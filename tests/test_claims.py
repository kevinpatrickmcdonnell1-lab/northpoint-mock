"""Claiming work items: one person works an item at a time, and every claim is auditable."""
import re
import sqlite3

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


def test_claim_shows_on_the_shared_list(client, svc):
    act_as(client, "triage")
    r = client.post("/work/P08/CLIN_UNDETERMINED/claim")
    assert "Claimed" in r.text
    act_as(client, "cardiologist")
    row = re.search(r'<tr data-work="P08:CLIN_UNDETERMINED".*?</tr>', client.get("/work").text, re.S).group(0)
    assert "Claimed by Mara Quill, RN" in row


def test_others_cannot_act_on_a_claimed_item(client, svc):
    act_as(client, "triage")
    client.post("/work/P08/CLIN_UNDETERMINED/claim")
    act_as(client, "cardiologist")
    page = client.get("/work/P08/CLIN_UNDETERMINED").text
    assert "Claimed by Mara Quill, RN" in page and 'value="decide"' not in page
    r = client.post("/work/P08/CLIN_UNDETERMINED", data={"op": "decide", "decision": "reviewed_hold",
                                                          "note": "x"})
    assert "Not recorded" in r.text and "claimed by Mara Quill" in r.text and svc.store.events() == []


def test_take_over_needs_a_reason_and_is_recorded(client, svc):
    act_as(client, "triage")
    client.post("/work/P08/CLIN_UNDETERMINED/claim")
    act_as(client, "cardiologist")
    r = client.post("/work/P08/CLIN_UNDETERMINED/claim", data={"takeover": "1"})
    assert "Not recorded" in r.text and svc.claim_holder("P08", "CLIN_UNDETERMINED")["actor_name"] == "Mara Quill, RN"
    client.post("/work/P08/CLIN_UNDETERMINED/claim", data={"takeover": "1", "note": "Mara off shift"})
    h = svc.claim_holder("P08", "CLIN_UNDETERMINED")
    assert (h["actor_name"], h["action"], h["note"]) == ("Dr. Ines Varga", "takeover", "Mara off shift")
    r = client.post("/work/P08/CLIN_UNDETERMINED", data={"op": "decide", "decision": "reviewed_hold",
                                                          "note": "spoke to PCP"})
    assert svc.store.events("P08")[0].actor_name == "Dr. Ines Varga"


def test_release_and_the_holder_still_acts(client, svc):
    act_as(client, "triage")
    client.post("/work/P08/CLIN_UNDETERMINED/claim")
    client.post("/work/P08/CLIN_UNDETERMINED/release")
    assert svc.claim_holder("P08", "CLIN_UNDETERMINED") is None
    client.post("/work/P08/CLIN_UNDETERMINED/claim")
    client.post("/work/P08/CLIN_UNDETERMINED", data={"op": "decide", "decision": "reviewed_hold", "note": "ok"})
    assert svc.store.events("P08")[0].actor_name == "Mara Quill, RN"


def test_only_the_holder_can_release(client, svc):
    act_as(client, "triage")
    client.post("/work/P08/CLIN_UNDETERMINED/claim")
    act_as(client, "cardiologist")
    r = client.post("/work/P08/CLIN_UNDETERMINED/release")
    assert "Not recorded" in r.text and svc.claim_holder("P08", "CLIN_UNDETERMINED") is not None


def test_cannot_claim_what_is_not_yours(client, svc):
    act_as(client, "auth")
    r = client.post("/work/P08/CLIN_UNDETERMINED/claim")
    assert "not on your list" in r.text and svc.claim_holder("P08", "CLIN_UNDETERMINED") is None


def test_next_item_skips_items_claimed_by_others(client, svc):
    act_as(client, "triage")
    client.post("/work/P08/CLIN_UNDETERMINED/claim")
    act_as(client, "cardiologist")
    r = client.post("/work/P03/CLIN_URGENT", data={"op": "decide", "decision": "reviewed_hold",
                                                  "note": "seen"}, follow_redirects=False)
    assert "/work/P08/" not in r.headers["location"]


def test_claims_are_append_only_and_on_the_timeline(client, svc):
    act_as(client, "triage")
    client.post("/work/P08/CLIN_UNDETERMINED/claim")
    client.post("/work/P08/CLIN_UNDETERMINED/release")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        svc.store.db.execute("DELETE FROM claims")
    kinds = re.findall(r'data-kind="([a-z_]+)"', client.get("/case/P08/timeline").text)
    assert kinds.count("claim") == 1 and kinds.count("release") == 1
    assert kinds.index("claim") < kinds.index("release")


def test_presenter_cannot_claim(client):
    r = client.post("/work/P08/CLIN_UNDETERMINED/claim", follow_redirects=False)
    assert r.headers["location"].startswith("/case/P08")
