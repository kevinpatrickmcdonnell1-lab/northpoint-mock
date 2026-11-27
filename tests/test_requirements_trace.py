"""docs/requirements_trace.md must stay true: every test it cites is collected by pytest,
and every brief requirement R1–R10 has a row with at least one test."""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "requirements_trace.md"
TEST_ID = re.compile(r"`(tests/test_[a-z0-9_]+\.py::test_[A-Za-z0-9_]+)`")


def _rows() -> dict[str, list[str]]:
    rows = {}
    for line in DOC.read_text().splitlines():
        m = re.match(r"\|\s*\*{0,2}(R\d+)\*{0,2}\b", line)
        if m:
            rows[m.group(1)] = TEST_ID.findall(line)
    return rows


def _collected() -> set[str]:
    out = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
                         cwd=ROOT, capture_output=True, text=True).stdout
    return {re.sub(r"\[.*\]$", "", ln.strip()) for ln in out.splitlines() if "::" in ln}


def test_every_requirement_has_a_row_with_tests():
    rows = _rows()
    assert sorted(rows, key=lambda r: int(r[1:])) == [f"R{i}" for i in range(1, 11)]
    for req, tests in rows.items():
        assert tests, f"{req} cites no test"


def test_every_cited_test_is_collected_by_pytest():
    collected = _collected()
    cited = {t for tests in _rows().values() for t in tests}
    missing = sorted(cited - collected)
    assert not missing, f"cited but not collected: {missing}"
