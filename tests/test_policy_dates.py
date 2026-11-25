"""Window starts derived from config must equal the dates printed in the rules (PLAN §7.1)."""
from datetime import date
from pathlib import Path

from tests.helpers import POLICY


def test_eval_date():
    assert POLICY.eval_date == date(2026, 10, 1)


def test_window_starts_match_rules_pdf():
    assert POLICY.window_start("benefits_response") == date(2026, 9, 1)
    assert POLICY.window_start("pri2_material_increase") == date(2026, 9, 17)
    assert POLICY.window_start("resting_ecg") == date(2026, 7, 3)
    assert POLICY.window_start("exercise_ecg") == date(2026, 4, 4)
    assert POLICY.window_start("prior_stress_echo") == date(2026, 4, 4)


def test_no_wall_clock_in_rule_code():
    """Invariant 10: no date.today()/datetime.now() in the engine."""
    app = Path(__file__).resolve().parent.parent / "app"
    files = list((app / "rules").glob("*.py")) + [app / n for n in ("engine.py", "gates.py", "actions.py")]
    for f in files:
        src = f.read_text()
        for bad in ("date.today(", "datetime.now(", "datetime.today(", "time.time("):
            assert bad not in src, f"{f.name} uses {bad}"
