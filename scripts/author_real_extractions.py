"""Extraction facts for the ten REAL synthetic packets (PLAN §6.4.2).

Drafted by Claude from the supplied page text on 2026-11-25; reviewed against the pages and
confirmed by Kevin on 2026-11-25 ("verified", no corrections) -> `llm_draft_human_verified`.
Any later correction goes in `corrections[]`. Facts only: no outcomes.
Quotes are copied verbatim from the page text so check_extractions.py can verify them once
the PDFs are in data/packets/.
Writes data/extractions/R01.json … R10.json.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.models import ExtractionFile  # noqa: E402

OUT = ROOT / "data" / "extractions"
MERCER = "Rowan S. Mercer, MD (synthetic)"
ECG_FINAL = "final clinician interpretation (per report)"
NO_PRIOR = {"stated": True, "date": None}   # "no stress echocardiogram ... 2026-04-04 through 2026-10-01"


class Packet:
    def __init__(self, key, source, packet_id, received, channel="not_stated"):
        self.key = key
        self.data = {"packet_id": packet_id, "patient_key": key, "source_file": source,
                     "received_at": received, "received_channel": channel, "ordering_site": None,
                     "extraction_method": "llm_draft_human_verified", "documents": [], "corrections": []}

    def doc(self, doc_type, pages, date, fields, quotes, ids, signer=None, signed=None):
        n = len(self.data["documents"]) + 1
        self.data["documents"].append({
            "doc_id": f"{self.key}-D{n}", "doc_type": doc_type, "pages": pages, "date": date,
            "signed": bool(signer) if signed is None else signed, "signer": signer,
            "identifiers": ids, "fields": fields,
            "quotes": [{"field": f, "page": p, "text": t} for f, p, t in quotes]})

    def save(self):
        ef = ExtractionFile.model_validate(self.data)
        (OUT / f"{self.key}.json").write_text(json.dumps(ef.model_dump(mode="json"), indent=2) + "\n")


def standard(key, n, name, dob, product, member, received, *, signed_on, reason, timing,
             referral_prio, indication_type, note, ecg=None, exercise=None, page6_other=None,
             addendum=True, extra_pages=None, bene_active=True, bene_end=None, bene_status=None,
             note_title_pages=(4, 5)):
    """The common NRH-SYN layout: p1 transmission, p2 consult referral + separate order,
    p3 registration, p4-5 signed note, p6 reports/supplements, last page card + benefits."""
    src = f"Patient_{n:02d}_{name.replace(' ', '_')}.pdf"
    pkt = f"NRH-SYN-72{n:02d}"
    p = Packet(key, src, pkt, received)
    hdr = {"name": name, "dob": dob}
    last = 8 if extra_pages else 7

    p.doc("other", [1], received, {"text": "Referral Transmission Record"},
          [("text", 1, "Referral Transmission Record"), ("text", 1, f"Original referral received {received}")],
          {**hdr, "packet_id": pkt})
    p.doc("referral", [2], signed_on,
          {"service_requested": "consult", "timing_text": timing, **referral_prio},
          [("service_requested", 2, "New-patient cardiology consultation."),
           ("timing_text", 2, f"Requested timing: {timing}"),
           ("signer", 2, f"Electronically signed by {MERCER}")],
          {**hdr, "packet_id": pkt}, signer=MERCER)
    p.doc("stress_echo_order", [2], signed_on,
          {"procedure": "Outpatient stress echocardiogram", "indication": reason,
           "indication_type": indication_type},
          [("procedure", 2, "Separate diagnostic order"),
           ("indication", 2, f"Outpatient stress echocardiogram. Indication: {reason}"),
           ("signer", 2, f"Date signed {signed_on}")],
          {**hdr, "packet_id": pkt}, signer=MERCER)
    p.doc("registration_face_sheet", [3], None,
          {"payer": product.split(" Commercial")[0].split(" Medicare")[0].split(" Community")[0],
           "plan_product_raw": product, "member_id": member},
          [("plan_product_raw", 3, product), ("member_id", 3, member),
           ("mrn", 3, f"Medical record number {pkt}")],
          {**hdr, "mrn": pkt, "member_id": member})
    p.doc("clinician_note", list(note_title_pages), note["date"], note["fields"],
          [(f, pg, t) for f, pg, t in note["quotes"]], hdr, signer=MERCER)
    if ecg:
        p.doc("resting_ecg_report", [6], ecg["date"],
              {"test_date": ecg["date"], "clinician_interpreted": True,
               "interpretation": ecg["interp"], "complete": True},
              [("test_date", 6, f"Study date {ecg['date']}"), ("interpretation", 6, ecg["text"])],
              {**hdr, "mrn": pkt}, signer=ECG_FINAL)
    if exercise:
        p.doc("exercise_ecg_report", [6], exercise["date"],
              {"test_date": exercise["date"], "clinician_interpreted": True,
               "interpretation": "equivocal", "complete": True},
              [("test_date", 6, f"Study date {exercise['date']}"),
               ("interpretation", 6, "equivocal exercise ECG for the current symptom evaluation; "
                                     "complete report final.")],
              {**hdr, "mrn": pkt}, signer=ECG_FINAL)
    if page6_other:
        p.doc("other", [6], "2026-10-01", {"text": page6_other},
              [("text", 6, page6_other)], hdr)
    if addendum:
        p.doc("clinician_note", [6], "2026-10-01", {"prior_stress_echo": NO_PRIOR},
              [("prior_stress_echo", 6, "2026-04-04 through 2026-10-01"),
               ("signer", 6, f"Electronically signed by {MERCER}, 2026-10-01.")],
              hdr, signer=MERCER)
    for pg, fields, quotes in (extra_pages or []):
        p.doc("other", [pg], None, fields, quotes, hdr)
    p.doc("insurance_card", [last], None,
          {"payer": None, "plan_product_raw": product, "member_id": member},
          [("plan_product_raw", last, product), ("member_id", last, member),
           ("other", last, "SYNTHETIC CARD IMAGE - NOT VALID FOR BILLING")],
          {**hdr, "member_id": member})
    p.doc("benefits_response", [last], "2026-09-30",
          {"response_date": "2026-09-30", "payer": None, "plan_product_raw": product,
           "member_id": member, "coverage_active_on_eval_date": bene_active,
           "product_end_date": bene_end},
          [("response_date", last, "Response date 2026-09-30"),
           ("member_id", last, f"{member} / {product}"),
           ("coverage_active_on_eval_date", last,
            bene_status or "Active on 2026-10-01; exact product and member matched.")],
          {"name": None, "dob": None, "member_id": member})
    return p


def assessment(date, complaint, quotes, **kw):
    return {"date": date, "quotes": quotes, "fields": {
        "is_assessment": True, "assessment_complete": True, "symptom_course_documented": True,
        "relevant_history_documented": True, "complaint_type": complaint, **kw}}


def build() -> list[Packet]:
    ps = []

    # ---- R01 Tessa Lornwell: Westbridge fax layout (9 pages) --------------------------------
    p = Packet("R01", "Patient_01_Tessa_Lornwell.pdf", "NONE-STATED-R01", "2026-09-21", "fax")
    a = {"name": "Tessa Lornwell", "dob": "1958-06-12"}
    b = {"name": "Tessa Lornwell", "dob": "1958-06-21"}
    devlin = "Rohan Devlin, MD"
    p.doc("other", [1], "2026-09-21", {"text": "FAX COVER SHEET"},
          [("text", 1, "FAX COVER SHEET"), ("text", 1, "09/21/2026 09:06 ET")], a)
    p.doc("referral", [2], "2026-09-20",
          {"service_requested": "consult", "timing_text": "Next available after intake review"},
          [("service_requested", 2, "New-patient cardiology consult"),
           ("timing_text", 2, "Next available after intake review"),
           ("signer", 2, "Electronically signed by Rohan Devlin, MD")], a, signer=devlin)
    # DIAGNOSTIC ORDER names the test only; the reason sits under CONSULT REFERRAL.
    p.doc("stress_echo_order", [2], "2026-09-20",
          {"procedure": "Stress echocardiogram", "indication": None},
          [("procedure", 2, "DIAGNOSTIC ORDER"), ("procedure", 2, "Stress echocardiogram"),
           ("signer", 2, "09/20/2026")], a, signer=devlin)
    p.doc("registration_face_sheet", [3], "2026-09-21",
          {"payer": "Summit Crest", "plan_product_raw": "Summit Crest Medicare Advantage",
           "member_id": "SCMA-SYN-71043"},
          [("dob", 3, "06/21/1958"), ("plan_product_raw", 3, "Summit Crest Medicare Advantage"),
           ("member_id", 3, "SCMA-SYN-71043"), ("coverage", 3, "Pending current E&B check")],
          {**b, "mrn": "NRH-SYN-7005", "member_id": "SCMA-SYN-71043"})
    p.doc("resting_ecg_report", [4], "2026-09-18",
          {"test_date": "2026-09-18", "clinician_interpreted": True,
           "interpretation": "nonspecific_st_t", "complete": True},
          [("test_date", 4, "Test date 09/18/2026"),
           ("interpretation", 4, "Sinus rhythm with nonspecific ST-T changes."),
           ("clinician_interpreted", 4, "Final interpretation"),
           ("dob", 4, "DOB: 06/12/1958")], a, signer="ordering clinician (final interpretation reviewed)")
    p.doc("clinician_note", [5, 6, 7, 8], "2026-09-18",
          {"is_assessment": True, "assessment_complete": True, "symptom_course_documented": True,
           "relevant_history_documented": True, "complaint_type": "exertional_chest_discomfort",
           "symptom_trajectory": "worsening",
           "timing_text": "intermittent exertional chest discomfort over approximately six weeks"},
          [("complaint_type", 5, "intermittent exertional chest discomfort over approximately six weeks"),
           ("symptom_trajectory", 5, "More frequent over six weeks"),
           ("relevant_history_documented", 6, "Family history of coronary disease"),
           ("mrn", 5, "MRN NRH-SYN-7005"),
           ("signer", 8, "Rohan Devlin, MD 09/18/2026")],
          {**a, "mrn": "NRH-SYN-7005"}, signer=devlin)
    p.doc("insurance_card", [9], None,
          {"payer": "Summit Crest", "plan_product_raw": "Summit Crest Medicare Advantage",
           "member_id": "SCMA-SYN-71043"},
          [("plan_product_raw", 9, "Summit Crest Medicare Advantage"),
           ("member_id", 9, "SCMA-SYN-71043"), ("dob", 9, "06/21/1958")],
          {**b, "member_id": "SCMA-SYN-71043"})
    ps.append(p)

    # ---- R02 Ada Merrin -----------------------------------------------------------------------
    reason = ("Intermittent chest pressure with uphill walking for three months, unchanged over the "
              "last four weeks; resolves with rest and does not occur at rest.")
    ps.append(standard(
        "R02", 2, "Ada Merrin", "1971-04-16", "Summit Crest Medicare Advantage", "SCMA-SYN-8202",
        "2026-09-26", signed_on="2026-09-25", reason=reason,
        timing="Next available after intake review.", indication_type="exertional_chest_discomfort",
        referral_prio={"symptom_trajectory": "stable"},
        note=assessment("2026-09-25", "exertional_chest_discomfort", [
            ("complaint_type", 4, "The pattern has not changed over the last four weeks."),
            ("relevant_history_documented", 4, "Hypertension and dyslipidemia; no reported syncope."),
            ("timing_text", 5, "Routine cardiology review is"),
            ("signer", 5, "Electronically signed by Rowan S. Mercer, MD (synthetic), 2026-09-25.")],
            symptom_trajectory="stable", timing_text="three months; unchanged over the last four weeks"),
        ecg={"date": "2026-09-24", "interp": "nonspecific_st_t",
             "text": "sinus rhythm with nonspecific ST-T changes; report final."}))

    # ---- R03 Beren Solas ----------------------------------------------------------------------
    reason = ("Exertional chest pressure became more frequent on 2026-09-23 and now occurs during "
              "shorter walks; no rest episode documented.")
    ps.append(standard(
        "R03", 3, "Beren Solas", "1964-11-03", "Summit Crest Medicare Advantage", "SCMA-SYN-8203",
        "2026-09-29", signed_on="2026-09-28", reason=reason, timing="Expedited review requested.",
        indication_type="exertional_chest_discomfort",
        referral_prio={"expedited_review_requested": True, "symptom_trajectory": "worsening",
                       "material_increase_date": "2026-09-23"},
        note=assessment("2026-09-28", "exertional_chest_discomfort", [
            ("material_increase_date", 5, "Exertional chest pressure with a material symptom increase beginning 2026-09-23."),
            ("expedited_review_requested", 5, "I request expedited cardiology review."),
            ("tests_mentioned_without_report", 4, "The complete report was not sent with that record."),
            ("relevant_history_documented", 4, "Type 2 diabetes and treated hypertension; no reported syncope."),
            ("signer", 5, "Electronically signed by Rowan S. Mercer, MD (synthetic), 2026-09-28.")],
            expedited_review_requested=True, symptom_trajectory="worsening",
            material_increase_date="2026-09-23", timing_text="more frequent since 2026-09-23",
            tests_mentioned_without_report=["exercise ECG (outside, 2026-02-20)", "resting ECG"]),
        page6_other="Referral records correspondence"))

    # ---- R04 Cora Venn ------------------------------------------------------------------------
    reason = ("Brief discomfort after meals while seated for two months. The patient denies exertional "
              "chest discomfort or exertional dyspnea; no recent change.")
    ps.append(standard(
        "R04", 4, "Cora Venn", "1980-02-09", "Summit Crest Medicare Advantage", "SCMA-SYN-8204",
        "2026-09-27", signed_on="2026-09-26", reason=reason,
        timing="Next available after intake review.", indication_type="nonexertional",
        referral_prio={"symptom_trajectory": "stable"},
        note=assessment("2026-09-26", "nonexertional", [
            ("complaint_type", 5, "Nonexertional, meal-related discomfort while seated."),
            ("ischemia_concern", 5, "I have no concern for possible coronary"),
            ("relevant_history_documented", 4, "Reflux symptoms and treated hypothyroidism; no documented coronary disease."),
            ("signer", 5, "Electronically signed by Rowan S. Mercer, MD (synthetic), 2026-09-26.")],
            ischemia_concern="explicit_no", symptom_trajectory="stable",
            timing_text="about two months; no recent change"),
        ecg={"date": "2026-09-26", "interp": "normal",
             "text": "normal sinus rhythm, no abnormal or nondiagnostic finding; report final."}))

    # ---- R05 Dalia Kest (PPO) / R06 Elias Brinn (HMO) ------------------------------------------
    reason = "Exertional dyspnea on stairs has increased since 2026-09-24; no rest symptoms documented."
    for key, n, name, dob, product, member, hmo in [
            ("R05", 5, "Dalia Kest", "1974-07-22", "Harborstone Health Commercial PPO", "HSPPO-SYN-8205", False),
            ("R06", 6, "Elias Brinn", "1972-09-14", "Harborstone Health Commercial HMO", "HSHMO-SYN-8206", True)]:
        missing = ["resting ECG"] if hmo else []
        quotes = [
            ("material_increase_date", 5, "Exertional dyspnea with a material increase on 2026-09-24."),
            ("expedited_review_requested", 5, "I request expedited cardiology review."),
            ("relevant_history_documented", 4, "Treated hypertension and family history of coronary disease."),
            ("signer", 5, "Electronically signed by Rowan S. Mercer, MD (synthetic), 2026-09-28.")]
        if hmo:
            quotes.append(("tests_mentioned_without_report", 5,
                           "A resting ECG was requested, but its report is not in the"))
        ps.append(standard(
            key, n, name, dob, product, member, "2026-09-29", signed_on="2026-09-28", reason=reason,
            timing="Expedited review requested.", indication_type="exertional_dyspnea",
            referral_prio={"expedited_review_requested": True, "symptom_trajectory": "worsening",
                           "material_increase_date": "2026-09-24"},
            note=assessment("2026-09-28", "exertional_dyspnea", quotes,
                            expedited_review_requested=True, symptom_trajectory="worsening",
                            material_increase_date="2026-09-24",
                            timing_text="increased since 2026-09-24",
                            tests_mentioned_without_report=missing),
            exercise={"date": "2026-09-20"}))

    # ---- R07 Farah Quill ----------------------------------------------------------------------
    reason = ("Exertional chest discomfort has worsened over ten days; a new episode at rest occurred on "
              "2026-09-30, documented by the clinician.")
    ps.append(standard(
        "R07", 7, "Farah Quill", "1967-12-05", "Lakeshore Community Health Managed Medicaid",
        "LCH-SYN-8207", "2026-09-30", signed_on="2026-09-30", reason=reason,
        timing="Urgent clinical review requested.", indication_type="exertional_chest_discomfort",
        referral_prio={"urgent_review_requested": True, "urgent_reason": "new_symptoms_at_rest",
                       "symptom_trajectory": "worsening", "material_increase_date": "2026-09-30"},
        note=assessment("2026-09-30", "exertional_chest_discomfort", [
            ("urgent_review_requested", 5, "I explicitly"),
            ("urgent_reason", 5, "request urgent cardiology clinical review because of the new rest episode and the high-risk"),
            ("prior_stress_echo", 5, "No stress echocardiogram in the lookback period."),
            ("relevant_history_documented", 4, "Treated diabetes and dyslipidemia; no stress echo in the lookback period."),
            ("signer", 5, "Electronically signed by Rowan S. Mercer, MD (synthetic), 2026-09-30.")],
            urgent_review_requested=True, urgent_reason="new_symptoms_at_rest",
            symptom_trajectory="worsening", material_increase_date="2026-09-30",
            timing_text="worsened over ten days; new episode at rest 2026-09-30",
            prior_stress_echo=NO_PRIOR),
        ecg={"date": "2026-09-30", "interp": "abnormal",
             "text": "abnormal resting ECG with new horizontal ST depression compared with the 2026-08-20"}))

    # ---- R08 Gideon Hale ----------------------------------------------------------------------
    reason = "Stable exertional chest pressure, without a reported rest episode as of 2026-09-20."
    ps.append(standard(
        "R08", 8, "Gideon Hale", "1962-03-18", "Summit Crest Medicare Advantage", "SCMA-SYN-8208",
        "2026-09-20", signed_on="2026-09-20", reason=reason,
        timing="Next available after intake review on original referral.",
        indication_type="exertional_chest_discomfort",
        referral_prio={"symptom_trajectory": "stable"},
        note=assessment("2026-09-30", "exertional_chest_discomfort", [
            ("material_increase_date", 4, "Today, September 30, he reports a new episode of chest pressure at rest."),
            ("urgent_review_requested", 5, "I request urgent cardiology clinical"),
            ("timing_text", 5, "This request supersedes the routine timing on the"),
            ("tests_mentioned_without_report", 5, "A current resting ECG was ordered by our office on September 30."),
            ("relevant_history_documented", 4, "Treated hypertension and former tobacco use."),
            ("signer", 5, "Electronically signed by Rowan S. Mercer, MD (synthetic), 2026-09-30.")],
            urgent_review_requested=True, urgent_reason="new_symptoms_at_rest",
            symptom_trajectory="worsening", material_increase_date="2026-09-30",
            timing_text="new rest episode 2026-09-30; supersedes routine timing",
            tests_mentioned_without_report=["resting ECG"]),
        page6_other="Referral records correspondence"))

    # ---- R09 Hana Orrel (8 pages; prior stress echo report on p7) ------------------------------
    reason = ("Stable exertional dyspnea for four months, with no material change since the 2026-08-15 "
              "stress echocardiogram.")
    ps.append(standard(
        "R09", 9, "Hana Orrel", "1970-05-27", "Harborstone Health Commercial HMO", "HSHMO-SYN-8209",
        "2026-09-28", signed_on="2026-09-27", reason=reason,
        timing="Next available after intake review.", indication_type="exertional_dyspnea",
        referral_prio={"symptom_trajectory": "stable"},
        note=assessment("2026-09-27", "exertional_dyspnea", [
            ("prior_stress_echo", 5, "Stress echocardiogram performed 2026-08-15; no material symptom change after that test."),
            ("interval_change_after_prior", 5, "Symptoms remain unchanged since that equivalent study."),
            ("relevant_history_documented", 4, "Treated hypertension and asthma; no interval admission."),
            ("signer", 5, "Electronically signed by Rowan S. Mercer, MD (synthetic), 2026-09-27.")],
            symptom_trajectory="stable", timing_text="four months; no material change since 2026-08-15",
            prior_stress_echo={"stated": True, "date": "2026-08-15"},
            interval_change_after_prior=False),
        ecg={"date": "2026-09-25", "interp": "nonspecific_st_t", "text": "nonspecific ST-T changes, final."},
        addendum=False,
        extra_pages=[(7, {"text": "Prior Stress Echocardiogram Report (2026-08-15)"},
                      [("text", 7, "Prior Stress Echocardiogram Report"),
                       ("text", 7, "Study date: 2026-08-15.")])]))

    # ---- R10 Imani Voss -----------------------------------------------------------------------
    reason = "Exertional chest pressure increased on 2026-09-25; no rest episode documented."
    ps.append(standard(
        "R10", 10, "Imani Voss", "1978-08-31", "Lakeshore Community Health Managed Medicaid",
        "LCH-SYN-8210", "2026-09-30", signed_on="2026-09-29", reason=reason,
        timing="Expedited review requested.", indication_type="exertional_chest_discomfort",
        referral_prio={"expedited_review_requested": True, "symptom_trajectory": "worsening",
                       "material_increase_date": "2026-09-25"},
        note=assessment("2026-09-29", "exertional_chest_discomfort", [
            ("material_increase_date", 5, "Exertional chest pressure with an increase dated 2026-09-25."),
            ("expedited_review_requested", 5, "I request expedited cardiology"),
            ("relevant_history_documented", 4, "Treated diabetes and a first-degree family history of coronary disease."),
            ("signer", 5, "Electronically signed by Rowan S. Mercer, MD (synthetic), 2026-09-29.")],
            expedited_review_requested=True, symptom_trajectory="worsening",
            material_increase_date="2026-09-25", timing_text="increased on 2026-09-25"),
        ecg={"date": "2026-09-29", "interp": "nonspecific_st_t", "text": "nonspecific ST-T changes, final."},
        bene_active=False, bene_end="2026-09-15",
        bene_status="Coverage terminated 2026-09-15; inactive on 2026-10-01."))
    return ps


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    built = build()
    for p in built:
        p.save()
    print(f"wrote {len(built)} real extraction files to {OUT.relative_to(ROOT)} (llm_draft_human_verified)")


if __name__ == "__main__":
    main()
