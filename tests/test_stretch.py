"""Stretch (PLAN §10): simulated downstream failure → retry/dead-letter; per-site field."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.handoffs import RECEIVERS, deliver, delivery_status, requeue, retry_pending, send, set_outage
from app.main import app
from app.metrics import compute
from tests.helpers import KEYS, mutate, run, run_key

SCHED = "Epic Cadence / scheduling WQ (MOCK STUB)"


def test_normal_send_is_delivered(svc):
    r = send(svc, "P02", "schedule")
    assert r["delivery"] == "delivered"
    assert delivery_status(svc, r["handoff_id"])["attempts"] == 1


def test_outage_retry_then_recover(svc):
    set_outage(svc, SCHED, True)
    r = send(svc, "P02", "schedule")
    hid = r["handoff_id"]
    assert r["result"] == "sent" and r["delivery"] == "retrying"     # recorded once, not delivered
    set_outage(svc, SCHED, False)
    assert deliver(svc, hid)["status"] == "delivered"
    assert delivery_status(svc, hid)["attempts"] == 2
    assert deliver(svc, hid)["result"] == "no_op"                    # never delivered twice


def test_dead_letter_after_max_attempts_then_requeue(svc):
    set_outage(svc, SCHED, True)
    hid = send(svc, "P02", "schedule")["handoff_id"]
    for _ in range(svc.workflow.delivery_max_attempts - 1):
        deliver(svc, hid)
    st = delivery_status(svc, hid)
    assert st["status"] == "dead_letter" and st["attempts"] == 3
    assert svc.store.audit_rows("P02", "dead_letter")
    assert deliver(svc, hid)["result"] == "no_op"                    # dead letters wait for a human
    assert retry_pending(svc) == []
    set_outage(svc, SCHED, False)
    assert requeue(svc, hid)["status"] == "delivered"
    # Retries never duplicate the handoff or bypass the gates
    sched = [o for o in svc.store.outbox() if o["work_item"]["type"] == "stress_echo_ready_to_schedule"]
    assert len(sched) == 1 and all(g["status"] == "pass" for g in sched[0]["gates_checked"])


def test_outage_does_not_affect_other_receivers_or_gates(svc):
    set_outage(svc, SCHED, True)
    assert send(svc, "P03", "triage")["delivery"] == "delivered"
    assert send(svc, "P01", "schedule")["result"] == "blocked"        # gates still checked first
    m = compute(svc)
    assert m["gate_leaks"] == 0 and m["deliveries"]["delivered"] == 1


def test_outage_rows_are_simulated_and_append_only(svc):
    set_outage(svc, RECEIVERS[0], True)
    send(svc, "P03", "triage")              # creates a delivery row (an UPDATE on 0 rows fires no trigger)
    assert svc.store.deliveries()
    assert svc.store.audit_rows(kind="outage")[0]["detail"]["simulated"] is True
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        svc.store.db.execute("DELETE FROM receiver_outages")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        svc.store.db.execute("UPDATE deliveries SET result='delivered'")


@pytest.mark.parametrize("key", KEYS)
def test_site_never_changes_a_rule_result(key):
    assert run(mutate(key, lambda d: d.update(ordering_site="Somewhere Else"))) == run_key(key)


def test_site_metrics_and_filter(svc):
    m = compute(svc)
    assert sum(c["cases"] for c in m["by_site"].values()) == 10
    assert set(m["by_site"]) == {"Lakeview Clinic", "Northpoint Main", "Riverside"}
    app.state.svc = svc
    try:
        with TestClient(app) as c:
            html = c.get("/", params={"site": "Riverside"}).text
            assert "P04" in html and "P06" in html and "P08" in html and "P01</strong>" not in html
            set_outage(svc, SCHED, True)
            c.post("/case/P02/handoff/schedule")
            out = c.get("/outbox").text
            assert "DOWN (simulated)" in out and "retrying" in out
            assert "By ordering site" in c.get("/metrics").text
    finally:
        del app.state.svc
