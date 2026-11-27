"""Clinical priority PRI-1…4 (PLAN §7.2). Independent of payer, coverage, PA and identity:
it only ever sees signed referral / clinician_note documents."""
from __future__ import annotations

from datetime import date

from app.models import ClinicianNoteFields, Document, ExtractionFile, RuleResult
from app.policy import Policy
from app.rules.common import in_window, of_type, ref

LABELS = {"PRI-1": "Urgent", "PRI-2": "Expedited", "PRI-3": "Routine", "PRI-4": "Unable to determine"}
RANK = {"PRI-1": 1, "PRI-2": 2, "PRI-3": 3}  # lower = more urgent
CODES = {"PRI-1": "CLIN_URGENT", "PRI-2": "CLIN_EXPEDITED", "PRI-3": None, "PRI-4": "CLIN_UNDETERMINED"}


def _addresses_timing(d: Document) -> bool:
    f = d.fields
    return bool(f.urgent_review_requested or f.expedited_review_requested
                or f.material_increase_date or f.symptom_trajectory != "not_stated" or f.timing_text)


def _tier(d: Document, policy: Policy) -> str:
    f = d.fields
    if f.urgent_review_requested and f.urgent_reason:
        return "PRI-1"
    if f.expedited_review_requested or in_window(
            f.material_increase_date, policy.window_start("pri2_material_increase"), policy.eval_date):
        return "PRI-2"
    return "PRI-3"


def _why(d: Document, tier: str, policy: Policy) -> str:
    f, win = d.fields, f"[{policy.window_start('pri2_material_increase')}, {policy.eval_date}]"
    if tier == "PRI-1":
        return f"urgent review requested ({f.urgent_reason})"
    if tier == "PRI-2":
        return ("expedited review requested" if f.expedited_review_requested
                else f"material increase on {f.material_increase_date} is within {win}")
    if f.material_increase_date:
        return (f"material increase on {f.material_increase_date} is outside the 14-day window {win}; "
                "no urgent/expedited request")
    return "no urgent/expedited request and no material increase inside the 14-day window"


def _traj(d: Document) -> str:
    return d.fields.symptom_trajectory


def _result(tier: str, text: str, ef: ExtractionFile, sources: list[Document]) -> RuleResult:
    return RuleResult(
        rule_id=tier, status=tier, reason_code=CODES[tier], reason_text=text,
        evidence_refs=[ref(ef, d, "timing_text") for d in sources],
        detail={"label": LABELS[tier], "sources": [d.doc_id for d in sources]},
    )


def evaluate_priority(ef: ExtractionFile, docs: list[Document], policy: Policy) -> RuleResult:
    cands = [d for d in of_type(docs, "referral", "clinician_note") if _addresses_timing(d)]
    if not cands:
        return _result("PRI-4", "No signed referral or clinician note addresses symptom timing, "
                                "severity or a review request.", ef, [])
    key = lambda d: d.date or date.min  # noqa: E731
    newest_date = max(key(d) for d in cands)
    newest = sorted([d for d in cands if key(d) == newest_date], key=lambda d: d.pages[0])
    older = [d for d in cands if key(d) < newest_date]

    if len(newest) > 1:
        tiers = {_tier(d, policy) for d in newest}
        trajs = {_traj(d) for d in newest} - {"not_stated"}
        if len(tiers) > 1 or len(trajs) > 1:
            return _result("PRI-4", f"Signed sources dated {newest_date} disagree on timing/severity "
                                    f"(tiers {sorted(tiers)}, trajectories {sorted(trajs)}). "
                                    "Clinical triage must clarify.", ef, newest)
        tier = tiers.pop()
        return _result(tier, f"{LABELS[tier]}: {len(newest)} consistent signed sources dated {newest_date}: "
                             f"{_why(newest[0], tier, policy)}.", ef, newest)

    n = newest[0]
    n_tier = _tier(n, policy)
    # A3 (Fix 1): no field can document a deliberate downgrade, so an older candidate with a
    # higher tier than the newest one is always unresolved, never silently overridden.
    higher = [o for o in older if RANK[_tier(o, policy)] < RANK[n_tier]]
    if higher:
        listing = "; ".join(f"older {o.doc_type} ({o.date}) is {_tier(o, policy)} "
                            f"{LABELS[_tier(o, policy)]}" for o in higher)
        return _result("PRI-4", f"Newest signed source ({n.doc_type}, {n.date}) is {n_tier} "
                                f"{LABELS[n_tier]}, but {listing}. No document records a deliberate "
                                "downgrade, so this is unresolved (A3). Clinical triage must clarify.",
                       ef, [n, *higher])
    documents_change = n.fields.material_increase_date is not None
    for o in older:
        if (_traj(n) != "not_stated" and _traj(o) != "not_stated" and _traj(n) != _traj(o)
                and not documents_change):
            return _result("PRI-4", f"Newer note ({n.date}) contradicts older {o.doc_type} ({o.date}) "
                                    "on symptom trajectory without documenting a material change (A3).",
                           ef, [n, o])
    tier = n_tier
    why = _why(n, tier, policy)
    note = " Overrides older timing because the newer note documents a material change." \
        if older and documents_change and any(_traj(o) not in ("not_stated", _traj(n)) for o in older) else ""
    return _result(tier, f"{LABELS[tier]}: newest signed source ({n.doc_type}, {n.date}): {why}.{note}",
                   ef, [n])


def is_assessment(d: Document) -> bool:
    return isinstance(d.fields, ClinicianNoteFields) and d.fields.is_assessment
