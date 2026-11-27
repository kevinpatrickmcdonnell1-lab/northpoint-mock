"""Seam hardening (fix/seam-hardening): one reproducing test group per fix.

Each test here was written first and confirmed failing before its fix.
"""
from tests.helpers import docs_of, mutate, run


# --------------------------------------------------------------------------- #
# Fix 1. Older urgent/expedited request silently downgraded (A3)               #
# --------------------------------------------------------------------------- #

def _p03_older_urgent(data):
    ref = next(d for d in docs_of(data, "referral") if d["date"] == "2026-09-15")
    ref["fields"].update(urgent_review_requested=True, urgent_reason="new_symptoms_at_rest")
    note = next(d for d in docs_of(data, "clinician_note") if d["date"] == "2026-09-29")
    note["fields"].update(urgent_review_requested=False, urgent_reason=None, material_increase_date=None)


def test_fix1_older_higher_tier_is_pri4_not_downgrade():
    s = run(mutate("P03", _p03_older_urgent))
    assert s.priority.status == "PRI-4"
    assert s.priority.reason_code == "CLIN_UNDETERMINED"
    text = s.priority.reason_text
    # names both documents and their tiers
    assert "2026-09-15" in text and "2026-09-29" in text
    assert "PRI-1" in text and "PRI-3" in text
    assert {r.doc_id for r in s.priority.evidence_refs} >= {"P03-D1", "P03-D7"}


def test_fix1_older_expedited_also_unresolved():
    def fn(data):
        _p03_older_urgent(data)
        ref = next(d for d in docs_of(data, "referral") if d["date"] == "2026-09-15")
        ref["fields"].update(urgent_review_requested=False, urgent_reason=None,
                             expedited_review_requested=True)
    s = run(mutate("P03", fn))
    assert s.priority.status == "PRI-4" and "PRI-2" in s.priority.reason_text
