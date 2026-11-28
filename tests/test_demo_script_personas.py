"""docs/demo_script_personas.md, real set (R01–R10): the persona walkthrough, step by step.

Drives the app over HTTP with the "Acting as" cookie, in script order, so every work list,
flash message, event ID and handoff ID matches what the presenter sees after Reset → Run all.
Then checks that the script states each of them.
"""
import re
from pathlib import Path
from urllib.parse import unquote_plus

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.metrics import compute
from app.pdfs import extractions_dir, real_ready
from app.service import Service

pytestmark = pytest.mark.skipif(not real_ready(), reason="real PDFs not in data/packets")

SCRIPT = Path(__file__).resolve().parent.parent / "docs" / "demo_script_personas.md"


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    d = tmp_path_factory.mktemp("personas")
    s = Service(db_path=d / "demo.db", outbox_dir=d / "outbox", extractions_dir=extractions_dir("real"))
    s.seed(evaluate=False)                                   # what Reset leaves behind
    app.state.svc = s
    with TestClient(app) as c:
        yield s, c
    del app.state.svc
    s.store.close()


def flash(r):
    m = re.search(r"msg=([^&]*)", r.headers.get("location", ""))
    return unquote_plus(m.group(1)) if m else ""


def act(c, key):
    c.post("/persona", data={"persona": key}, follow_redirects=False)


def post(c, url, data=None):
    return flash(c.post(url, data=data or {}, follow_redirects=False))


def handoff(c, key, kind):
    return post(c, f"/case/{key}/handoff/{kind}", {"back": "/work"})


def work(c):
    html = c.get("/work").text
    active, _, waiting = html.partition('id="waiting"')
    pat = r'data-work="(R\d+):([A-Za-z_0-9]+)"'
    return re.findall(pat, active), re.findall(pat, waiting)


def clinical_items(items):
    return [k for k, code in items if code.startswith("CLIN_")]


def failing(s, key):
    return [g.gate for g in s.state(key).gates if not g.passed]


# The tests below are ordered: they ARE the script.

def test_0_run_and_lists(run):
    s, c = run
    assert "Re-evaluated all 10 cases" in post(c, "/rerun", {"back": "/"})
    assert compute(s)["counts"] == {"held": 8, "ready for PA prep / awaiting payer": 2}
    act(c, "triage")
    assert clinical_items(work(c)[0]) == ["R08", "R07"]
    act(c, "review")
    assert clinical_items(work(c)[0]) == ["R03", "R05", "R06", "R10"]
    act(c, "auth")
    assert work(c)[0] == [("R07", "PA_READY"), ("R02", "PA_READY"), ("R04", "EVID_NOT_MET"),
                          ("R09", "EVID_NOT_MET")]
    act(c, "scheduler")
    assert work(c) == ([], [])
    act(c, "intake")
    r01 = [code for k, code in work(c)[0] if k == "R01"]
    assert r01 == ["COV_PENDING_NONE", "EVID_MISSING_ORDER", "EVID_MISSING_REPORT", "ID_CONFLICT"]


def test_1_ops_is_read_only(run):
    _, c = run
    act(c, "ops")
    assert c.post("/persona", data={"persona": "ops"}, follow_redirects=False).headers["location"] == "/metrics"
    assert "This role is read-only" in c.get("/work").text


def test_2_triage_r07(run):
    s, c = run
    act(c, "triage")
    panel = c.get("/work/R07/CLIN_URGENT").text
    assert "Payer, coverage and PA are not shown here" in panel
    assert "Summit Crest" not in panel and "Harborstone" not in panel
    assert "2 consistent signed sources dated 2026-09-30" in s.state("R07").priority.reason_text
    assert handoff(c, "R07", "triage").startswith("R07: Sent urgent_triage_notification: HO-R07-TRIAGE-1")


def test_3_auth_pa(run):
    s, c = run
    act(c, "auth")
    assert post(c, "/work/R07/PA_READY", {"op": "pa_packet"}).startswith("HO-R07-PA-1:")
    assert post(c, "/work/R02/PA_READY", {"op": "pa_packet"}).startswith("HO-R02-PA-1:")
    assert post(c, "/work/R02/PA_READY", {"op": "payer", "decision": "approved", "note": ""}) == \
        "Not recorded: a payer decision needs the payer reference (portal or fax reference) in the note."
    assert post(c, "/work/R02/PA_READY", {"op": "payer", "decision": "approved",
                                         "note": "SCMA portal ref PA-55120"}) == \
        "EV-0001: payer approved recorded for R02 by Sam Okoro (Auth Specialist)."
    assert [a.reason_code for a in s.state("R02").actions] == ["SCHED_READY"]


def test_4_scheduler_r02_and_guardrails(run):
    s, c = run
    act(c, "scheduler")
    assert work(c)[0] == [("R02", "SCHED_READY")]
    assert post(c, "/work/R02/SCHED_READY", {"op": "schedule"}).startswith("HO-R02-SCHED-1:")
    assert handoff(c, "R02", "schedule") == "R02: Already recorded as HO-R02-SCHED-1 (idempotency key unchanged)."
    r = c.get("/work/R01/ID_CONFLICT", follow_redirects=False)
    assert "R01 ID_CONFLICT is not on your list (Scheduler)" in flash(r)
    assert post(c, "/case/R01/handoff/schedule") == \
        "BLOCKED stress_echo_ready_to_schedule: failing gates G-ID, G-COV, G-EVID, G-PA-APPROVED. " \
        "Attempt recorded in the audit trail."


def test_5_intake_r01(run):
    s, c = run
    act(c, "intake")
    assert handoff(c, "R01", "identity_task").startswith("R01: Sent identity_verification_task: HO-R01-ID-1")
    assert post(c, "/work/R01/ID_CONFLICT/claim") == "Claimed: R01 ID_CONFLICT by Jo Rivera (Intake Coordinator)."
    panel = c.get("/work/R01/ID_CONFLICT").text
    assert "Nothing is preselected" in panel and "1958-06-12" in panel and "1958-06-21" in panel
    assert "IDENTITY_RESOLUTION needs confirmed values, epic_reference and a note" in post(
        c, "/work/R01/ID_CONFLICT", {"op": "identity", "confirmed_dob": "1958-06-12",
                                     "epic_reference": "EPIC-REG-R01"})
    assert post(c, "/work/R01/ID_CONFLICT", {
        "op": "identity", "confirmed_dob": "1958-06-12", "epic_reference": "EPIC-REG-R01",
        "note": "Confirmed DOB 06/12/1958 in Epic and with patient; face sheet and card transposed"}) == \
        "EV-0002: identity confirmed for R01 by Jo Rivera (Intake Coordinator)."
    st = s.state("R01")
    assert st.identity.status == "verified" and failing(s, "R01") == ["G-COV", "G-EVID", "G-PA-APPROVED"]

    assert post(c, "/work/R01/COV_PENDING_NONE", {
        "op": "benefits", "response_date": "2026-10-01", "payer": "Summit Crest",
        "plan_product_raw": "Summit Crest Medicare Advantage", "member_id": "SCMA-SYN-71043",
        "active": "true", "note": "E&B rerun with confirmed DOB"}) == \
        "EV-0003: E&B response logged for R01 by Jo Rivera (Intake Coordinator)."
    assert s.state("R01").coverage.status == "verified" and failing(s, "R01") == ["G-EVID", "G-PA-APPROVED"]

    assert post(c, "/work/R01/EVID_MISSING_ORDER", {"op": "request"}).startswith("HO-R01-INFO-1:")
    reqs = [o for o in s.store.outbox("R01") if o["handoff_id"] == "HO-R01-INFO-1"][0]["work_item"]["requests"]
    assert [r["document"] for r in reqs] == ["DOC-1: corrected signed stress echo order naming the indication",
                                             "REPEAT-1: prior stress echo history in the assessment"]
    active, waiting = work(c)
    assert not [k for k, _ in active if k == "R01"]
    assert [code for k, code in waiting if k == "R01"] == ["EVID_MISSING_ORDER", "EVID_MISSING_REPORT"]


def test_6_r05_clear_send_recall(run):
    s, c = run
    act(c, "review")
    assert post(c, "/work/R05/CLIN_EXPEDITED", {"op": "decide", "decision": "cleared_for_scheduling",
                                               "note": "Reviewed note and ECG"}) == \
        "EV-0004: R05 cleared for scheduling recorded by Theo Lund, RN (Clinical Review RN)."
    act(c, "scheduler")
    assert post(c, "/work/R05/SCHED_READY", {"op": "schedule"}).startswith("HO-R05-SCHED-1:")
    act(c, "cardiologist")
    assert "R05" not in clinical_items(work(c)[0])         # cleared cases leave the clinical list
    msg = post(c, "/case/R05/simulate", {"type": "CLINICAL_REVIEW_DECISION", "decision": "not_cleared",
                                         "note": "Wants a resting echo first"})
    assert msg.startswith("SIMULATED event EV-0005 recorded (CLINICAL_REVIEW_DECISION not_cleared)")
    assert "scheduling handoff recalled automatically (HO-R05-RECALL-1)" in msg
    act(c, "scheduler")
    page = c.get("/work").text
    assert "Release the slot" in page and "HO-R05-RECALL-1" in page and "HO-R05-SCHED-1" in page


def test_7_auth_not_met(run):
    s, c = run
    act(c, "auth")
    assert post(c, "/work/R04/EVID_NOT_MET", {"op": "clinician_review"}).startswith("HO-R04-REVIEW-1:")
    wi = [o for o in s.store.outbox("R04") if o["handoff_id"] == "HO-R04-REVIEW-1"][0]["work_item"]
    assert "It is not a payer denial." in wi["statement"] and list(wi["not_met"]) == ["QUAL-1"]


def test_8_auditor_timeline(run):
    _, c = run
    act(c, "auditor")
    page = c.get("/case/R01/timeline").text
    for s in ("Lee Hart (Scheduler)", "HO-R01-ID-1", "Jo Rivera (Intake Coordinator)", "EV-0002",
              "IDENTITY_RESOLUTION", "face sheet and card transposed", "conflicting → verified"):
        assert s in page


def test_9_metrics_at_end(run):
    s, _ = run
    m = compute(s)
    assert m["counts"] == {"held": 8, "handed to scheduling": 1, "ready for PA prep / awaiting payer": 1}
    assert m["gate_leaks"] == 0 and m["scheduling_recalls"] == 1
    assert m["blocked_attempts"] == 1 and m["triage_notifications"] == 1


@pytest.mark.parametrize("stated", [
    "R08, R07", "R03, R05, R06, R10", "0 sendable", "2 ready for PA prep", "8 held",
    "HO-R07-TRIAGE-1", "HO-R07-PA-1", "HO-R02-PA-1", "EV-0001", "HO-R02-SCHED-1",
    "already recorded", "is not on your list", "G-ID, G-COV, G-EVID, G-PA-APPROVED",
    "1958-06-12", "1958-06-21", "HO-R01-ID-1", "EV-0002", "G-COV, G-EVID, G-PA-APPROVED",
    "SCMA-SYN-71043", "EV-0003", "G-EVID, G-PA-APPROVED", "HO-R01-INFO-1",
    "corrected signed stress echo order naming the indication",
    "EV-0004", "HO-R05-SCHED-1", "EV-0005", "HO-R05-RECALL-1", "Release the slot",
    "HO-R04-REVIEW-1", "not a payer denial",
    "1 handed to scheduling", "1 ready for PA prep", "0 gate leaks", "1 scheduling recall",
    "1 blocked attempt", "1 urgent triage notification",
])
def test_10_script_states_what_the_run_shows(stated):
    assert stated in SCRIPT.read_text(), f"demo_script_personas.md does not state {stated!r}"
