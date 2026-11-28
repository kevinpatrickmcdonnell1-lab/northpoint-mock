"""Personas step 3: Auth Specialist and Scheduler panels, and the auditor's case timeline."""
import json
import re

import pytest
from fastapi.testclient import TestClient

from app import handoffs
from app.main import app
from app.policy import load_workflow
from app.service import Service
from app.pdfs import MOCK_EXTRACTIONS


@pytest.fixture
def client(svc):
    app.state.svc = svc
    with TestClient(app) as c:
        yield c
    del app.state.svc


def act_as(c, key):
    c.post("/persona", data={"persona": key})


def evidence(html):
    return html.split('id="evidence"')[1].split("</section>")[0]


# ---------------------------------------------------------------- auth

def test_pa_ready_panel_previews_packet_then_sends(client, svc):
    act_as(client, "auth")
    assert 'href="/work/P01/PA_READY"' in client.get("/work").text
    page = client.get("/work/P01/PA_READY").text
    ev = evidence(page)
    for rule in ("DOC-1", "QUAL-1", "QUAL-2", "REPEAT-1"):
        assert rule in ev
    assert "Supported is not an approval" in page and "PLAN-SCMA" in ev
    assert "Indication: exertional chest d" in ev                       # the quotes go with it
    client.post("/work/P01/PA_READY", data={"op": "pa_packet"})
    sent = [o for o in svc.store.outbox("P01") if o["work_item"]["type"] == "pa_request_packet"]
    assert len(sent) == 1
    assert svc.store.audit_rows("P01", "handoff_sent")[0]["actor"] == "Sam Okoro (Auth Specialist)"
    # The engine doesn't read the outbox: the item stays PA_READY, now waiting on the payer.
    waiting = client.get("/work").text.split('id="waiting"')[1]
    assert 'data-work="P01:PA_READY"' in waiting and "HO-P01-PA-1" in waiting
    assert 'value="payer"' in client.get("/work/P01/PA_READY").text   # panel now records the decision


def test_payer_decision_needs_reference_and_records(client, svc):
    act_as(client, "auth")
    client.post("/work/P01/PA_READY", data={"op": "pa_packet"})
    r = client.post("/work/P01/PA_READY", data={"op": "payer", "decision": "pended"})
    assert "Not recorded" in r.text and "reference" in r.text and svc.store.events("P01") == []
    client.post("/work/P01/PA_READY", data={"op": "payer", "decision": "pended",
                                            "note": "Portal ref SC-778: clinicals requested"})
    assert 'data-work="P01:PA_AWAITING"' in client.get("/work").text.split('id="waiting"')[0]
    client.post("/work/P01/PA_AWAITING", data={"op": "payer", "decision": "approved",
                                               "note": "Portal ref SC-778"})
    ev = svc.store.events("P01")[-1]
    assert (ev.type, ev.decision, ev.actor_name, ev.pa_handoff_id) == (
        "PAYER_PA_DECISION", "approved", "Sam Okoro", "HO-P01-PA-1")


def test_payer_decision_before_packet_refused(client, svc):
    act_as(client, "auth")
    r = client.post("/work/P01/PA_READY", data={"op": "payer", "decision": "approved", "note": "x"})
    assert "Not recorded" in r.text and "PA packet" in r.text and svc.store.events() == []


def test_not_met_panel_says_not_a_denial_and_sends_clinician_review(client, svc):
    act_as(client, "auth")
    page = client.get("/work/P07/EVID_NOT_MET").text
    ev = evidence(page)
    assert "REPEAT-1" in ev and "Prior stress echo 2026-06-11" in ev
    assert "not a payer denial" in page.lower()
    assert "DOC-1" not in ev                                            # only what is not met
    client.post("/work/P07/EVID_NOT_MET", data={"op": "clinician_review"})
    sent = [o for o in svc.store.outbox("P07") if o["work_item"]["type"] == "clinician_review_request"]
    assert len(sent) == 1
    item = sent[0]["work_item"]
    assert list(item["not_met"]) == ["REPEAT-1"] and "not a payer denial" in item["statement"].lower()
    waiting = client.get("/work").text.split('id="waiting"')[1]
    assert 'data-work="P07:EVID_NOT_MET"' in waiting


def test_clinician_review_not_applicable_without_not_met(svc):
    assert handoffs.send(svc, "P02", "clinician_review")["result"] == "not_applicable"


def test_receivers_listed_once():
    assert len(handoffs.RECEIVERS) == len(set(handoffs.RECEIVERS))


# ---------------------------------------------------------------- scheduler

def test_schedule_panel_shows_every_gate_then_sends_and_waits(client, svc):
    act_as(client, "scheduler")
    page = client.get("/work/P02/SCHED_READY").text
    ev = evidence(page)
    assert all(g in ev for g in ("G-ID", "G-COV", "G-PAREQ", "G-EVID", "G-CLIN", "G-PA-APPROVED"))
    assert "Routine" in ev
    client.post("/work/P02/SCHED_READY", data={"op": "schedule"})
    assert handoffs.active_scheduling_handoff(svc, "P02")["handoff_id"] == "HO-P02-SCHED-1"
    waiting = client.get("/work").text.split('id="waiting"')[1]
    assert 'data-work="P02:SCHED_READY"' in waiting and "HO-P02-SCHED-1" in waiting


def test_scheduler_sees_recalls(client, svc):
    handoffs.send(svc, "P02", "schedule")
    svc.review_fact("P02", "P02-D2", "signed", "correct", value="false", quote_page=2,
                    quote_text="Procedure: Outpatient stress echocardiogram", reason="unsigned",
                    actor_role="Intake Coordinator", actor_name="J")
    act_as(client, "scheduler")
    rec = client.get("/work").text.split('id="recalls"')[1]
    assert "HO-P02-RECALL-1" in rec and "HO-P02-SCHED-1" in rec


# ---------------------------------------------------------------- auditor timeline

def test_timeline_tells_the_story_in_order_with_actors(client, svc):
    act_as(client, "auth")
    client.post("/work/P01/PA_READY", data={"op": "pa_packet"})
    client.post("/work/P01/PA_READY", data={"op": "payer", "decision": "approved", "note": "ref 1"})
    act_as(client, "auditor")
    page = client.get("/case/P01/timeline").text
    kinds = re.findall(r'data-kind="([a-z_]+)"', page)
    assert kinds[0] == "loaded" and "evaluation" in kinds
    assert kinds.index("handoff_sent") < kinds.index("sim_event") < kinds.index("state_change")
    assert "Sam Okoro (Auth Specialist)" in page and "SIMULATED" in page
    assert "<form" not in page.split("<main>")[1].split("</main>")[0]   # read-only


def test_timeline_json_export_matches_audit(client, svc):
    handoffs.send(svc, "P02", "schedule")
    data = client.get("/case/P02/timeline.json").json()
    assert data["patient_key"] == "P02"
    assert len(data["entries"]) == len(svc.store.audit_rows("P02")) + 1     # + loaded
    assert data["replay"]["matches"] is True


def test_replay_flags_a_state_that_no_longer_matches_the_audit(tmp_path):
    db = tmp_path / "t.db"
    s = Service(db_path=db, outbox_dir=tmp_path / "o", extractions_dir=MOCK_EXTRACTIONS)
    s.seed()
    s.store.close()
    wf = load_workflow().model_copy(update={"routine_requires_clinical_clearance": True})
    s2 = Service(db_path=db, outbox_dir=tmp_path / "o", extractions_dir=MOCK_EXTRACTIONS, workflow=wf)
    app.state.svc = s2
    try:
        with TestClient(app) as c:
            r = c.get("/case/P02/timeline.json").json()
            assert r["replay"]["matches"] is False
            assert "does not match" in c.get("/case/P02/timeline").text
    finally:
        del app.state.svc
        s2.store.close()


def test_case_page_links_to_timeline(client):
    assert 'href="/case/P02/timeline"' in client.get("/case/P02").text
