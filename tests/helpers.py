"""Test helpers: load facts, mutate one fact, build simulated events, run the engine."""
from __future__ import annotations

import copy
from datetime import datetime
from typing import Callable

from app.models import ExtractionFile, SimEvent
from app.pdfs import MOCK_EXTRACTIONS, load_extractions
from app.policy import load_policy, load_workflow

POLICY = load_policy()
WORKFLOW = load_workflow()
FACTS = load_extractions(MOCK_EXTRACTIONS)   # mock regression suite
KEYS = sorted(FACTS)


def facts(key: str) -> ExtractionFile:
    return FACTS[key]


def mutate(key: str, fn: Callable[[dict], None]) -> ExtractionFile:
    data = copy.deepcopy(FACTS[key].model_dump(mode="json"))
    fn(data)
    return ExtractionFile.model_validate(data)


def docs_of(data: dict, doc_type: str) -> list[dict]:
    return [d for d in data["documents"] if d["doc_type"] == doc_type]


def assessment_of(data: dict) -> dict:
    return [d for d in docs_of(data, "clinician_note") if d["fields"].get("is_assessment")][0]


_n = 0


def event(key: str, type_: str, decision: str, role: str, **extra) -> SimEvent:
    global _n
    _n += 1
    return SimEvent(event_id=f"EV-T{_n:04d}", patient_key=key, type=type_, decision=decision,
                    actor_role=role, event_time=datetime(2026, 10, 1, 9, 0),
                    recorded_at=datetime(2026, 11, 24, 12, 0), **extra)


def run(ef: ExtractionFile, events=(), workflow=WORKFLOW):
    from app.engine import evaluate
    return evaluate(ef, list(events), POLICY, workflow)


def run_key(key: str, events=(), workflow=WORKFLOW):
    return run(facts(key), events, workflow)


def failing_gates(state) -> set[str]:
    return {g.gate for g in state.gates if not g.passed}


def action_codes(state) -> list[str]:
    return [a.reason_code for a in state.actions]
