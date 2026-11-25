"""PLAN §6.4.3: golden table for the REAL packets, derived by hand from the real pages
BEFORE the engine runs on them. Never copy the mock table.

- No real extractions yet -> skipped.
- Real extractions present but no hand-derived table -> FAILS, by design.
- Table format: tests/golden_real.example.yaml -> copy to tests/golden_real.yaml.
"""
from pathlib import Path

import pytest
import yaml

from app.engine import evaluate
from app.pdfs import REAL_EXTRACTIONS, load_extractions
from tests.helpers import POLICY, WORKFLOW

TABLE = Path(__file__).with_name("golden_real.yaml")
REAL = load_extractions(REAL_EXTRACTIONS) if any(REAL_EXTRACTIONS.glob("*.json")) else {}

pytestmark = pytest.mark.skipif(not REAL, reason="real PDFs not supplied yet (PLAN §6.4)")


def test_hand_derived_table_exists_before_engine_runs():
    assert TABLE.exists(), ("Derive tests/golden_real.yaml by hand from the real pages first "
                            "(PLAN §6.4.3); copy the format of golden_real.example.yaml.")
    table = yaml.safe_load(TABLE.read_text())
    assert set(table) == set(REAL), f"table keys {sorted(table)} != real packets {sorted(REAL)}"


def _rows():
    if not (REAL and TABLE.exists()):
        return []
    return sorted(yaml.safe_load(TABLE.read_text()).items())


@pytest.mark.parametrize("key,want", _rows())
def test_real_golden_row(key, want):
    s = evaluate(REAL[key], [], POLICY, WORKFLOW)
    got = {
        "priority": s.priority.status, "identity": s.identity.status,
        "coverage": s.coverage.status, "pa": s.pa_requirement,
        **{k: v.status for k, v in s.criteria.items()},
        "actions": [a.reason_code for a in s.actions],
    }
    # Each mismatch: record in docs/build_log.md as extraction error, rule misread or code bug.
    assert {k: got[k] for k in want} == want
