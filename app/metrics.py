"""Metrics panel: a 10-case mock illustration of how pilot KPIs would be instrumented. Not a forecast."""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from statistics import median
from typing import Any

from app.service import Service


def business_days(start: date, end: date) -> int:
    """Mon–Fri days after `start` up to and including `end`; no holiday calendar (A7)."""
    if end <= start:
        return 0
    n, d = 0, start
    while d < end:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def compute(svc: Service) -> dict[str, Any]:
    states = svc.states()
    eval_date = svc.policy.eval_date
    outbox = svc.store.outbox()
    sched = [o for o in outbox if o["work_item"]["type"] == "stress_echo_ready_to_schedule"]
    from app.handoffs import RECALL_TYPE, active_scheduling_handoff
    # Only an active (not recalled) scheduling handoff counts as "handed to scheduling".
    sent_keys = {k for k in svc.store.keys() if active_scheduling_handoff(svc, k)}
    recalls = [o for o in outbox if o["work_item"]["type"] == RECALL_TYPE]

    counts = Counter()
    for s in states:
        if s.patient_key in sent_keys:
            counts["handed to scheduling"] += 1
        elif s.scheduling_sendable:
            counts["sendable to scheduling"] += 1
        elif s.pa_prep_ready:
            counts["ready for PA prep / awaiting payer"] += 1
        else:
            counts["held"] += 1

    holds = Counter(a.reason_code for s in states for a in s.actions
                    if a.reason_code not in ("SCHED_READY",))
    owners = Counter(a.owner_role for s in states for a in s.actions)

    aging, ready_times = Counter(), []
    rows = []
    for s in states:
        ef = svc.store.case(s.patient_key)
        evs = svc.store.events(s.patient_key)
        age = business_days(ef.received_at, eval_date)
        if s.admin_ready:
            ready_on = max([eval_date] + [e.event_time.date() for e in evs])
            bd = business_days(ef.received_at, ready_on)
            ready_times.append(bd)
            rows.append((s.patient_key, ef.received_at, ready_on, bd))
        else:
            bucket = "0–2" if age <= 2 else "3–5" if age <= 5 else "6–10" if age <= 10 else ">10"
            aging[bucket] += 1

    from app.handoffs import delivery_status
    statuses = Counter(delivery_status(svc, o["handoff_id"])["status"] for o in outbox)
    by_site: dict[str, Counter] = {}
    for s in states:
        site = svc.store.case(s.patient_key).ordering_site or "(unknown)"
        c = by_site.setdefault(site, Counter())
        c["cases"] += 1
        c["held"] += int(not s.scheduling_sendable and not s.pa_prep_ready
                         and s.patient_key not in sent_keys)
        c["open PA/doc gaps"] += sum(a.reason_code.startswith(("EVID_", "COV_", "PA_UNKNOWN"))
                                     for a in s.actions)

    leaks = sum(1 for o in sched if any(g["status"] != "pass" for g in o["gates_checked"]))
    blocked = len(svc.store.audit_rows(kind="handoff_blocked"))
    triage = [o for o in outbox if o["work_item"]["type"] == "urgent_triage_notification"]
    return {
        "counts": dict(counts), "holds": holds.most_common(), "owners": owners.most_common(),
        "aging": {b: aging.get(b, 0) for b in ("0–2", "3–5", "6–10", ">10")},
        "ready_rows": rows, "median_ready_bd": median(ready_times) if ready_times else None,
        "gate_leaks": leaks, "blocked_attempts": blocked, "triage_notifications": len(triage),
        "scheduling_recalls": len(recalls),
        "eval_date": eval_date, "n": len(states),
        "deliveries": {k: statuses.get(k, 0) for k in ("delivered", "retrying", "pending", "dead_letter")},
        "by_site": {k: dict(v) for k, v in sorted(by_site.items())},
    }
