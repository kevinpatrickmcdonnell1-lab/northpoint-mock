"""COV-1 coverage (PLAN §7.2). Only a dated benefits response inside the window can verify."""
from __future__ import annotations

from typing import Optional

from app.models import BenefitsResponseFields, Document, EvidenceRef, ExtractionFile, RuleResult, SimEvent
from app.policy import Policy, normalize_product
from app.rules.common import event_ref, of_type, ref


def _responses(ef, docs, events, policy) -> list[tuple[BenefitsResponseFields, EvidenceRef, bool]]:
    out = []
    for d in of_type(docs, "benefits_response"):
        out.append((d.fields, ref(ef, d, "response_date"), False))
    for ev in events:
        if ev.type == "BENEFITS_RESPONSE":
            out.append((ev.benefits, event_ref(ev), True))
    # A8: responses dated after the evaluation date are ignored.
    return [r for r in out if r[0].response_date <= policy.eval_date]


def _ignored_after_eval(ef, events, policy) -> list[dict]:
    """Fix 7: every benefits response A8 ignores (in-packet or simulated), so none vanishes silently.
    In-packet documents dated after eval_date are already dropped by usable_docs(), so this
    reads ef.documents directly."""
    out = []
    for d in of_type(ef.documents, "benefits_response"):
        if d.fields.response_date > policy.eval_date or (d.date and d.date > policy.eval_date):
            r = ref(ef, d, "response_date")
            out.append({"response_date": str(d.fields.response_date), "source": f"{r.file} p.{r.page}"})
    for ev in events:
        if ev.type == "BENEFITS_RESPONSE" and ev.benefits.response_date > policy.eval_date:
            out.append({"response_date": str(ev.benefits.response_date),
                        "source": f"SIMULATED {ev.event_id}", "event_id": ev.event_id})
    return out


def _signature(f: BenefitsResponseFields) -> tuple:
    return ((f.member_id or "").upper(), normalize_product(f.plan_product_raw),
            f.coverage_active_on_eval_date, f.product_end_date)


def evaluate_coverage(ef: ExtractionFile, docs: list[Document], events: list[SimEvent],
                      policy: Policy, plan: RuleResult) -> RuleResult:
    start = policy.window_start("benefits_response")
    responses = _responses(ef, docs, events, policy)
    in_win = [r for r in responses if r[0].response_date >= start]

    ignored = _ignored_after_eval(ef, events, policy)
    suffix = ""
    if ignored:
        n = len(ignored)
        suffix = (f" {n} response{'s' if n > 1 else ''} dated "
                  f"{', '.join(i['response_date'] for i in ignored)} ignored (after evaluation date "
                  f"{policy.eval_date}, A8): {'; '.join(i['source'] for i in ignored)}.")

    def res(status, code, text, refs, **detail) -> RuleResult:
        return RuleResult(rule_id="COV-1", status=status, reason_code=code, reason_text=text + suffix,
                          evidence_refs=refs, detail={**detail, "ignored_after_eval_date": ignored})

    if not in_win:
        if responses:
            old = max(responses, key=lambda r: r[0].response_date)
            return res("pending", "COV_PENDING_STALE",
                       f"Newest benefits response is dated {old[0].response_date}, before the window "
                       f"start {start}. Card/face sheet alone cannot verify coverage.", [old[1]])
        return res("pending", "COV_PENDING_NONE",
                   f"No benefits response dated in [{start}, {policy.eval_date}]. "
                   "Card/face sheet alone cannot verify coverage.", [])

    newest_date = max(r[0].response_date for r in in_win)
    newest = [r for r in in_win if r[0].response_date == newest_date]
    if len({_signature(r[0]) for r in newest}) > 1:
        return res("conflicting", "COV_CONFLICT",
                   f"Benefits responses dated {newest_date} disagree.", [r[1] for r in newest])
    f, rref, simulated = newest[-1]
    sim = " (SIMULATED response)" if simulated else ""

    # Expected member ID: a human-confirmed value (SIMULATED) or the registration/card values.
    confirmed: Optional[str] = None
    for ev in events:
        if ev.type == "IDENTITY_RESOLUTION" and ev.confirmed and "member_id" in ev.confirmed:
            confirmed = ev.confirmed["member_id"]
    regcard = of_type(docs, "registration_face_sheet", "insurance_card")
    expected = {confirmed.upper()} if confirmed else {
        d.fields.member_id.upper() for d in regcard if d.fields.member_id}
    member = (f.member_id or "").upper()
    if not member or expected != {member}:
        refs = [rref] + [ref(ef, d, "member_id") for d in regcard]
        return res("conflicting", "COV_CONFLICT",
                   f"Benefits response{sim} member ID {f.member_id or '(none)'} does not match "
                   f"registration/card member ID {', '.join(sorted(expected)) or '(none)'}.", refs)

    r_plan = policy.resolve_product(f.plan_product_raw)
    other_plans = {policy.resolve_product(d.fields.plan_product_raw) for d in regcard} - {None}
    if r_plan and other_plans and other_plans != {r_plan}:
        return res("conflicting", "COV_CONFLICT",
                   f"Benefits response{sim} is for {r_plan}; registration/card show "
                   f"{', '.join(sorted(other_plans))}.", [rref])
    if r_plan is None or plan.status != "resolved":
        return res("pending", "COV_PENDING_PRODUCT",
                   f"Benefits response{sim} dated {f.response_date} does not name an exact plan "
                   f"product ('{f.plan_product_raw or ''}'). Confirm the product (A6).", [rref])
    if f.coverage_active_on_eval_date is False or (
            f.product_end_date is not None and f.product_end_date < policy.eval_date):
        end = f" (ended {f.product_end_date})" if f.product_end_date else ""
        return res("inactive", "COV_INACTIVE",
                   f"Benefits response{sim} dated {f.response_date}: coverage not active on "
                   f"{policy.eval_date}{end}.", [rref])
    if f.coverage_active_on_eval_date is not True:
        return res("pending", "COV_PENDING_NONE",
                   f"Benefits response{sim} does not state coverage status on {policy.eval_date}.", [rref])
    return res("verified", None,
               f"Benefits response{sim} dated {f.response_date} (in window from {start}): member "
               f"{f.member_id}, {r_plan}, active on {policy.eval_date}.", [rref])
