"""Rule unit tests: window boundaries (PLAN §9.2), single-fact flips (§9.4) and known traps."""
from tests.helpers import (
    WORKFLOW, action_codes, assessment_of, docs_of, failing_gates, mutate, run, run_key,
)


def _set_date(d: dict, field: str, value: str):
    d["fields"][field] = value
    d["date"] = value


# ---------------------------------------------------------------- boundaries

def test_pri2_window_boundary():
    def mi(day):
        def fn(data):
            for d in docs_of(data, "referral") + [assessment_of(data)]:
                d["fields"]["material_increase_date"] = day
        return fn
    assert run(mutate("P01", mi("2026-09-17"))).priority.status == "PRI-2"
    assert run(mutate("P01", mi("2026-09-16"))).priority.status == "PRI-3"


def test_benefits_window_boundary():
    def rd(day):
        return lambda data: _set_date(docs_of(data, "benefits_response")[0], "response_date", day)
    assert run(mutate("P01", rd("2026-09-01"))).coverage.status == "verified"
    s = run(mutate("P01", rd("2026-08-31"))).coverage
    assert (s.status, s.reason_code) == ("pending", "COV_PENDING_STALE")


def test_benefits_after_eval_date_ignored_A8():
    s = run(mutate("P01", lambda d: _set_date(docs_of(d, "benefits_response")[0],
                                               "response_date", "2026-10-02"))).coverage
    assert (s.status, s.reason_code) == ("pending", "COV_PENDING_NONE")


def test_resting_ecg_window_boundary():
    def td(day):
        return lambda data: _set_date(docs_of(data, "resting_ecg_report")[0], "test_date", day)
    assert run(mutate("P01", td("2026-07-03"))).criteria["QUAL-2"].status == "S"
    assert run(mutate("P01", td("2026-07-02"))).criteria["QUAL-2"].status == "I"
    assert run(mutate("P05", td("2026-07-03"))).criteria["HMO-1"].status == "S"
    assert run(mutate("P05", td("2026-07-02"))).criteria["HMO-1"].status == "I"


def test_exercise_ecg_window_boundary():
    def td(day):
        return lambda data: _set_date(docs_of(data, "exercise_ecg_report")[0], "test_date", day)
    s = run(mutate("P05", td("2026-04-04"))).criteria["QUAL-2"]
    assert (s.status, s.detail["supported_by"]) == ("S", ["B"])
    assert run(mutate("P05", td("2026-04-03"))).criteria["QUAL-2"].status == "I"


def test_prior_stress_echo_window_boundary():
    def pd(day):
        return lambda data: assessment_of(data)["fields"]["prior_stress_echo"].update(date=day)
    assert run(mutate("P07", pd("2026-04-03"))).criteria["REPEAT-1"].status == "S"
    assert run(mutate("P07", pd("2026-04-04"))).criteria["REPEAT-1"].status == "NM"


# ---------------------------------------------------------------- single-fact flips

def test_flip_p05_resting_ecg_current_makes_pa_ready():
    s = run(mutate("P05", lambda d: _set_date(docs_of(d, "resting_ecg_report")[0],
                                               "test_date", "2026-07-10")))
    assert s.criteria["HMO-1"].status == "S" and s.criteria["EVIDENCE"].status == "S"
    assert s.pa_prep_ready                         # clinical hold never blocks PA prep
    assert s.priority.status == "PRI-2" and "G-CLIN" in failing_gates(s)


def test_flip_p07_interval_change_with_rationale_supports_repeat():
    def fn(d):
        a = assessment_of(d)["fields"]
        a.update(interval_change_after_prior=True, repeat_rationale="new exertional symptoms")
    assert run(mutate("P07", fn)).criteria["REPEAT-1"].status == "S"


def test_flip_p07_interval_change_without_rationale_is_insufficient():
    fn = lambda d: assessment_of(d)["fields"].update(interval_change_after_prior=True)  # noqa: E731
    assert run(mutate("P07", fn)).criteria["REPEAT-1"].status == "I"


def test_prior_stress_echo_not_stated_is_insufficient():
    fn = lambda d: assessment_of(d)["fields"].update(prior_stress_echo={"stated": False, "date": None})  # noqa: E731
    assert run(mutate("P01", fn)).criteria["REPEAT-1"].status == "I"


# ---------------------------------------------------------------- QUAL-2 traps

def test_one_failed_branch_never_implies_the_other():
    # P10 without the exercise-ECG history: A failed, B unknown -> insufficient, not "not met"
    fn = lambda d: assessment_of(d)["fields"].update(exercise_ecg_history={"stated": False, "none_since": None})  # noqa: E731
    s = run(mutate("P10", fn)).criteria["QUAL-2"]
    assert s.status == "I"
    assert s.detail["branches"] == {"A": "failed", "B": "unknown"}


def test_raw_tracing_ignored():
    s = run_key("P10")
    assert s.criteria["QUAL-2"].detail["branches"]["A"] == "failed"
    no_trace = run(mutate("P10", lambda d: d["documents"].pop()))
    assert no_trace == s                         # removing the raw tracing changes nothing


def test_exercise_ecg_never_satisfies_hmo1():
    assert run_key("P05").criteria["HMO-1"].status == "I"


# ---------------------------------------------------------------- DOC-1 / QUAL-1

def test_consult_referral_is_not_an_order():
    s = run_key("P09").criteria["DOC-1"]
    assert (s.status, s.reason_code) == ("I", "EVID_MISSING_ORDER")


def test_unsigned_order_does_not_count():
    s = run(mutate("P01", lambda d: docs_of(d, "stress_echo_order")[0].update(signed=False, signer=None)))
    assert s.criteria["DOC-1"].status == "I"


def test_ischemia_concern_alone_supports_qual1():
    fn = lambda d: docs_of(d, "stress_echo_order")[0]["fields"].update(indication="evaluate")  # noqa: E731
    assert run(mutate("P03", fn)).criteria["QUAL-1"].status == "S"
    assert run(mutate("P01", fn)).criteria["QUAL-1"].status == "I"


# ---------------------------------------------------------------- priority

def test_newer_note_without_documented_change_is_pri4_A3():
    # P05's newer note contradicts the older referral; without the documented change -> PRI-4
    fn = lambda d: assessment_of(d)["fields"].update(material_increase_date=None)  # noqa: E731
    assert run(mutate("P05", fn)).priority.status == "PRI-4"


def test_no_candidate_is_pri4():
    def fn(d):
        d["documents"] = [x for x in d["documents"] if x["doc_type"] not in ("referral", "clinician_note")]
    assert run(mutate("P01", fn)).priority.status == "PRI-4"


def test_priority_independent_of_payer_and_coverage():
    base = run_key("P03").priority
    def fn(d):
        for x in d["documents"]:
            if x["doc_type"] in ("registration_face_sheet", "insurance_card", "benefits_response"):
                x["fields"]["plan_product_raw"] = "Harborstone Health Commercial PPO"
        docs_of(d, "benefits_response")[0]["fields"]["coverage_active_on_eval_date"] = False
    assert run(mutate("P03", fn)).priority == base


# ---------------------------------------------------------------- identity

def test_name_normalization_A5():
    def nm(name):
        return lambda d: docs_of(d, "registration_face_sheet")[0]["identifiers"].update(name=name)
    assert run(mutate("P01", nm("ALDER, Martin J."))).identity.status == "conflicting"  # reordered
    assert run(mutate("P01", nm("martin  j. alder"))).identity.status == "verified"   # middle initial
    assert run(mutate("P01", nm("Marty Alder"))).identity.status == "conflicting"     # nickname


def test_identity_pending_without_registration():
    fn = lambda d: d.update(documents=[x for x in d["documents"] if x["doc_type"] != "registration_face_sheet"])  # noqa: E731
    s = run(mutate("P01", fn)).identity
    assert (s.status, s.reason_code) == ("pending", "ID_PENDING")


def test_identifier_types_never_compared_across():
    # An MRN that happens to equal the member ID is not a conflict and not a match.
    fn = lambda d: [x["identifiers"].update(mrn="SCM-448812") for x in d["documents"] if x["identifiers"].get("mrn")]  # noqa: E731
    assert run(mutate("P01", fn)).identity.status == "verified"


# ---------------------------------------------------------------- plan / coverage

def test_product_never_inferred_from_member_prefix():
    s = run_key("P09")
    assert s.plan.status == "unknown" and s.pa_requirement == "unknown"


def test_benefits_for_different_plan_is_conflicting():
    fn = lambda d: docs_of(d, "benefits_response")[0]["fields"].update(plan_product_raw="Harborstone Health Commercial HMO")  # noqa: E731
    s = run(mutate("P01", fn)).coverage
    assert (s.status, s.reason_code) == ("conflicting", "COV_CONFLICT")


def test_product_end_date_before_eval_is_inactive():
    fn = lambda d: docs_of(d, "benefits_response")[0]["fields"].update(product_end_date="2026-09-30")  # noqa: E731
    assert run(mutate("P01", fn)).coverage.status == "inactive"


def test_two_same_day_responses_that_disagree_conflict():
    def fn(d):
        b = dict(docs_of(d, "benefits_response")[0])
        b = {**b, "doc_id": "P01-D99", "fields": {**b["fields"], "coverage_active_on_eval_date": False}}
        d["documents"].append(b)
    assert run(mutate("P01", fn)).coverage.status == "conflicting"


# ---------------------------------------------------------------- gates / flags

def test_no_pa_plan_with_pending_coverage_never_sendable():
    fn = lambda d: d.update(documents=[x for x in d["documents"] if x["doc_type"] != "benefits_response"])  # noqa: E731
    s = run(mutate("P02", fn))
    assert not s.scheduling_sendable and "G-COV" in failing_gates(s)


def test_flag_A1_routine_requires_clearance():
    wf = WORKFLOW.model_copy(update={"routine_requires_clinical_clearance": True})
    s = run_key("P02", workflow=wf)
    assert not s.scheduling_sendable and failing_gates(s) == {"G-CLIN"}
    assert action_codes(s) == ["CLIN_ROUTINE_REVIEW"]


def test_flag_A2_ppo_evidence_gate_configurable():
    fn = lambda d: assessment_of(d)["fields"].update(prior_stress_echo={"stated": True, "date": "2026-08-01"}, interval_change_after_prior=False)  # noqa: E731
    ef = mutate("P02", fn)
    assert run(ef).scheduling_sendable                       # REPEAT-1 is advisory for PPO
    wf = WORKFLOW.model_copy(update={"no_pa_evidence_gate": ["DOC-1", "REPEAT-1"]})
    assert not run(ef, workflow=wf).scheduling_sendable
