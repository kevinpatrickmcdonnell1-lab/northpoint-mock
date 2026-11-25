"""Plan product resolution (PLAN §7.2). Exact-product aliases only; never from member-ID prefix."""
from __future__ import annotations

from typing import Literal

from app.models import Document, ExtractionFile, RuleResult, SimEvent
from app.policy import Policy
from app.rules.common import event_ref, of_type, ref

PRODUCT_SOURCES = ("registration_face_sheet", "insurance_card", "benefits_response")


def evaluate_plan(ef: ExtractionFile, docs: list[Document], events: list[SimEvent],
                  policy: Policy) -> tuple[RuleResult, Literal["required", "not_required", "unknown"]]:
    values = []
    for d in of_type(docs, *PRODUCT_SOURCES):
        raw = d.fields.plan_product_raw
        values.append({"raw": raw, "plan_id": policy.resolve_product(raw), "doc_type": d.doc_type,
                       "ref": ref(ef, d, "plan_product_raw")})
    for ev in events:
        if ev.type == "BENEFITS_RESPONSE" and ev.benefits.response_date <= policy.eval_date:
            raw = ev.benefits.plan_product_raw
            values.append({"raw": raw, "plan_id": policy.resolve_product(raw),
                           "doc_type": "benefits_response (SIMULATED)", "ref": event_ref(ev)})

    detail = {"values": [{k: v for k, v in x.items() if k != "ref"} for x in values]}
    plan_ids = {x["plan_id"] for x in values}
    if values and None not in plan_ids and len(plan_ids) == 1:
        plan_id = plan_ids.pop()
        plan = policy.plans[plan_id]
        detail.update(plan_id=plan_id, product=plan.product, extra=plan.extra)
        return RuleResult(rule_id="PLAN", status="resolved",
                          reason_text=f"Every product value maps to {plan_id} ({plan.product}); "
                                      f"PA {plan.pa.replace('_', ' ')} per {policy.policy_version}.",
                          evidence_refs=[x["ref"] for x in values], detail=detail), plan.pa

    unmatched = [x["raw"] or "(blank)" for x in values if x["plan_id"] is None]
    if unmatched:
        why = "product not matched to an exact plan: " + ", ".join(f"'{u}'" for u in unmatched)
    elif values:
        why = "sources map to different plans: " + ", ".join(sorted(p for p in plan_ids if p))
    else:
        why = "no plan product found on registration, card or benefits response"
    return RuleResult(rule_id="PLAN", status="unknown", reason_code="PA_UNKNOWN",
                      reason_text=f"Plan product unresolved ({why}). PA requirement unknown.",
                      detail=detail), "unknown"
