"""DOC-1, QUAL-1, QUAL-2, REPEAT-1, HMO-1 and the common evidence test (PLAN §7.2).

"Not met" (NM) is not a denial; "supported" (S) is not an approval.
"""
from __future__ import annotations

import re
from typing import Optional

from app import rule_ids
from app.models import Document, ExtractionFile, RuleResult
from app.policy import Policy
from app.rules.common import in_window, of_type, ref
from app.rules.priority import is_assessment

QUAL_A_OK = {"abnormal", "nondiagnostic", "nonspecific_st_t"}
QUAL_B_FAIL = {"normal", "negative", "positive"}

# Order-indication text that matches the assessment's complaint type (assumption B9).
_CHEST = r"chest (discomfort|pain|pressure|tightness)"
_DYSP = r"(dyspnea|shortness of breath|\bsob\b)"
INDICATION_MATCH = {
    "exertional_chest_discomfort": re.compile(rf"exertion\w*.*{_CHEST}|{_CHEST}.*exertion", re.I),
    "exertional_dyspnea": re.compile(rf"exertion\w*.*{_DYSP}|{_DYSP}.*exertion", re.I),
}


def _r(rule, status, text, refs=(), code=None, **detail) -> RuleResult:
    return RuleResult(rule_id=rule, status=status, reason_code=code, reason_text=text,
                      evidence_refs=list(refs), detail=detail)


def latest_assessment(docs: list[Document]) -> Optional[Document]:
    a = [d for d in of_type(docs, "clinician_note") if is_assessment(d)]
    return max(a, key=lambda d: (d.date, d.pages[0])) if a else None


def _orders(docs):
    return [d for d in of_type(docs, "stress_echo_order")
            if d.fields.procedure and d.fields.indication]


def doc1(ef, docs) -> RuleResult:
    orders, a = _orders(docs), latest_assessment(docs)
    assess_ok = bool(a and a.date and a.fields.symptom_course_documented
                     and a.fields.relevant_history_documented)
    if orders and assess_ok:
        return _r("DOC-1", "S", f"Signed order ({orders[0].date}) names procedure and indication; "
                                f"signed assessment ({a.date}) documents symptom course and history.",
                  [ref(ef, orders[0], "indication"), ref(ef, a, "relevant_history_documented")])
    if not orders:
        partial = [d for d in of_type(docs, "stress_echo_order") if d.fields.procedure]
        if partial:
            return _r("DOC-1", "I", f"Signed order ({partial[0].date}) names the procedure but no "
                                    "indication. A consult-referral reason is not the order's indication.",
                      [ref(ef, partial[0], "procedure")], code="EVID_MISSING_ORDER",
                      missing="corrected signed stress echo order naming the indication")
        consult = any(d.fields.service_requested == "consult" for d in of_type(docs, "referral"))
        extra = " The packet holds a cardiology consult referral, which is not a stress echo order." \
            if consult else ""
        return _r("DOC-1", "I", "No signed stress echo order naming procedure and indication." + extra,
                  code="EVID_MISSING_ORDER", missing="signed stress echo order")
    return _r("DOC-1", "I", "No signed, dated clinician assessment documenting symptom course and "
                            "relevant history.", code="EVID_MISSING_REPORT",
              missing="signed clinician assessment")


def qual1(ef, docs) -> RuleResult:
    a, orders = latest_assessment(docs), _orders(docs)
    if a is None:
        return _r("QUAL-1", "I", "No signed clinician assessment.", code="EVID_MISSING_REPORT",
                  missing="signed clinician assessment")
    f = a.fields
    if f.ischemia_concern == "explicit_yes":
        return _r("QUAL-1", "S", "Assessment states concern for ischemia explicitly.",
                  [ref(ef, a, "ischemia_concern")])
    pattern = INDICATION_MATCH.get(f.complaint_type)

    def matches(o) -> bool:
        if f.complaint_type not in INDICATION_MATCH:
            return False
        if o.fields.indication_type != "not_stated":
            return o.fields.indication_type == f.complaint_type
        return bool(pattern.search(o.fields.indication))
    match = next((o for o in orders if matches(o)), None)
    if match:
        return _r("QUAL-1", "S", f"Assessment complaint ({f.complaint_type.replace('_', ' ')}) matches "
                                 f"order indication '{match.fields.indication}'.",
                  [ref(ef, a, "complaint_type"), ref(ef, match, "indication")])
    if a.fields.assessment_complete and f.complaint_type == "nonexertional":
        return _r("QUAL-1", "NM", "Complete assessment documents a nonexertional complaint and does "
                                  "not state ischemia concern. Not met (this is not a payer denial).",
                  [ref(ef, a, "complaint_type")], code="EVID_NOT_MET")
    if not orders:
        return _r("QUAL-1", "I", "Cannot match complaint to an order indication: no signed stress echo "
                                 "order.", code="EVID_MISSING_ORDER", missing="signed stress echo order")
    return _r("QUAL-1", "I", "Assessment complaint does not clearly match the order indication.",
              code="EVID_MISSING_REPORT", missing="assessment clarifying the exertional complaint")


def qual2(ef, docs, policy: Policy) -> RuleResult:
    a_start, b_start, end = (policy.window_start("resting_ecg"),
                             policy.window_start("exercise_ecg"), policy.eval_date)
    rest = [d for d in of_type(docs, "resting_ecg_report") if d.fields.clinician_interpreted]
    rest_in = [d for d in rest if in_window(d.fields.test_date, a_start, end)]
    a_ok = [d for d in rest_in if d.fields.interpretation in QUAL_A_OK]
    if a_ok:
        a_status, a_ref = "supported", ref(ef, a_ok[0], "interpretation")
    elif any(d.fields.interpretation == "normal" for d in rest_in):
        a_status, a_ref = "failed", ref(ef, rest_in[0], "interpretation")
    else:
        a_status, a_ref = "unknown", None

    ex = [d for d in of_type(docs, "exercise_ecg_report") if d.fields.clinician_interpreted]
    ex_in = [d for d in ex if in_window(d.fields.test_date, b_start, end)]
    b_ok = [d for d in ex_in if d.fields.complete and d.fields.interpretation == "equivocal"]
    a = latest_assessment(docs)
    hist = a.fields.exercise_ecg_history if a else None
    if b_ok:
        b_status, b_ref = "supported", ref(ef, b_ok[0], "interpretation")
    elif any(d.fields.interpretation in QUAL_B_FAIL for d in ex_in):
        b_status, b_ref = "failed", ref(ef, ex_in[0], "interpretation")
    elif hist and hist.stated and hist.none_since and hist.none_since <= b_start:
        b_status, b_ref = "failed", ref(ef, a, "exercise_ecg_history")
    else:
        b_status, b_ref = "unknown", None

    branches = {"A": a_status, "B": b_status}
    supported_by = [k for k, v in branches.items() if v == "supported"]
    refs = [r for r in (a_ref, b_ref) if r]
    desc = (f"A (resting ECG ≥ {a_start}): {a_status}; B (exercise ECG ≥ {b_start}): {b_status}.")
    if supported_by:
        return _r("QUAL-2", "S", desc, refs, branches=branches, supported_by=supported_by)
    if a_status == "failed" and b_status == "failed":
        return _r("QUAL-2", "NM", desc + " Both branches failed. Not met (this is not a payer denial).",
                  refs, code="EVID_NOT_MET", branches=branches, supported_by=[])
    mentioned = a.fields.tests_mentioned_without_report if a else []
    need = []
    if a_status == "unknown":
        m = " (mentioned in note, report not attached)" if any("resting" in t.lower() for t in mentioned) else ""
        need.append(f"clinician-interpreted resting ECG report dated ≥ {a_start}{m}")
    if b_status == "unknown":
        need.append(f"exercise ECG report dated ≥ {b_start}")
    return _r("QUAL-2", "I", desc + " Insufficient: neither branch supported and not both failed.",
              refs, code="EVID_MISSING_REPORT", branches=branches, supported_by=[],
              missing=need[0] if need else "qualifying ECG evidence")


def _prior_stress_echo_source(docs) -> Optional[Document]:
    """The assessment's own statement wins; otherwise the newest signed clinician note
    (e.g. a dated prior-test-history addendum) that states the history."""
    a = latest_assessment(docs)
    if a and a.fields.prior_stress_echo.stated:
        return a
    notes = [d for d in of_type(docs, "clinician_note") if d.fields.prior_stress_echo.stated]
    return max(notes, key=lambda d: (d.date, d.pages[0])) if notes else a


def repeat1(ef, docs, policy: Policy) -> RuleResult:
    a = _prior_stress_echo_source(docs)
    start = policy.window_start("prior_stress_echo")
    if a is None or not a.fields.prior_stress_echo.stated:
        return _r("REPEAT-1", "I", "Assessment does not state prior stress echo history.",
                  code="EVID_MISSING_REPORT", missing="prior stress echo history in the assessment")
    p = a.fields.prior_stress_echo
    r = [ref(ef, a, "prior_stress_echo")]
    if p.date is None:
        return _r("REPEAT-1", "S", "Assessment states no prior stress echo.", r)
    if p.date < start:
        return _r("REPEAT-1", "S", f"Prior stress echo {p.date} is before the window start {start}.", r)
    if a.fields.interval_change_after_prior is True and a.fields.repeat_rationale:
        return _r("REPEAT-1", "S", f"Prior stress echo {p.date} within window; interval change and "
                                   f"repeat rationale documented.", r)
    if a.fields.interval_change_after_prior is False:
        return _r("REPEAT-1", "NM", f"Prior stress echo {p.date} is within the window (≥ {start}) and "
                                    "the assessment documents no interval change. Not met "
                                    "(this is not a payer denial).", r, code="EVID_NOT_MET")
    return _r("REPEAT-1", "I", f"Prior stress echo {p.date} within window; interval change and repeat "
                               "rationale not both documented.", r, code="EVID_MISSING_REPORT",
              missing="interval change and repeat rationale since prior stress echo")


def hmo1(ef, docs, policy: Policy, plan_extra: list[str]) -> RuleResult:
    if rule_ids.HMO_1 not in plan_extra:
        return _r("HMO-1", "NA", "Not applicable: HMO-1 applies to PLAN-HSHMO only.")
    start = policy.window_start("resting_ecg")
    ok = [d for d in of_type(docs, "resting_ecg_report")
          if d.fields.clinician_interpreted and d.fields.complete
          and in_window(d.fields.test_date, start, policy.eval_date)]
    if ok:
        return _r("HMO-1", "S", f"Complete clinician-interpreted resting ECG dated {ok[0].fields.test_date} "
                                f"(≥ {start}).", [ref(ef, ok[0], "test_date")])
    older = [d.fields.test_date for d in of_type(docs, "resting_ecg_report")]
    note = f" Only out-of-window report(s): {', '.join(map(str, older))}." if older else ""
    return _r("HMO-1", "I", f"HMO-1 needs a complete clinician-interpreted resting ECG dated ≥ {start}; "
                            f"an exercise ECG does not satisfy it.{note} (A4)", code="EVID_HMO1",
              missing=f"current resting ECG report (dated ≥ {start})")


def common(results: dict[str, RuleResult]) -> RuleResult:
    applicable = {k: v for k, v in results.items() if v.status != "NA"}
    statuses = {v.status for v in applicable.values()}
    names = ", ".join(applicable)
    if "NM" in statuses:
        nm = [k for k, v in applicable.items() if v.status == "NM"]
        return _r("EVIDENCE", "NM", f"Not met on {', '.join(nm)}. Do not prepare PA; this is not a "
                                    "payer denial.", code="EVID_NOT_MET", rules=list(applicable))
    if "I" in statuses:
        i = [k for k, v in applicable.items() if v.status == "I"]
        return _r("EVIDENCE", "I", f"Insufficient on {', '.join(i)}.", code="EVID_INSUFFICIENT",
                  rules=list(applicable))
    refs = [r for v in applicable.values() for r in v.evidence_refs]
    return _r("EVIDENCE", "S", f"Supported on {names}. Supported is not an approval.", refs,
              rules=list(applicable))


def evaluate_evidence(ef: ExtractionFile, docs: list[Document], policy: Policy,
                      plan_extra: list[str]) -> dict[str, RuleResult]:
    r = rule_ids
    out = {r.DOC_1: doc1(ef, docs), r.QUAL_1: qual1(ef, docs), r.QUAL_2: qual2(ef, docs, policy),
           r.REPEAT_1: repeat1(ef, docs, policy), r.HMO_1: hmo1(ef, docs, policy, plan_extra)}
    assert tuple(out) == r.EVIDENCE_RULES, "evidence rules out of sync with app/rule_ids.py"
    out[r.EVIDENCE] = common(out)
    return out
