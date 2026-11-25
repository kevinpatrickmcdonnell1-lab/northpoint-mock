"""Author the ten mock extraction files (PLAN §6.2) as document FACTS only.

This script writes data/extractions/mock/P01.json … P10.json. It contains no expected
outcomes; those live only in tests/. Every quote is text that make_mock_packets.py
prints on the cited page, so check_extractions.py can verify it.
SYNTHETIC – MOCK DATA. All names and IDs are fictional.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.models import ExtractionFile  # noqa: E402

OUT = ROOT / "data" / "extractions" / "mock"

CLINICAL = {"referral", "stress_echo_order", "clinician_note"}
ID_FIELDS = {
    "referral": ("name", "dob", "mrn", "packet_id"),
    "stress_echo_order": ("name", "dob", "mrn", "packet_id"),
    "clinician_note": ("name", "dob", "mrn", "packet_id"),
    "registration_face_sheet": ("name", "dob", "mrn", "member_id"),
    "insurance_card": ("name", "member_id"),
    "benefits_response": ("name", "dob", "member_id"),
    "resting_ecg_report": ("name", "dob", "mrn"),
    "exercise_ecg_report": ("name", "dob", "mrn"),
    "ecg_tracing_raw": ("name", "dob"),
    "other": ("name",),
}


class Packet:
    def __init__(self, key, n, ids, received_at, channel, site):
        self.key, self.ids = key, {**ids, "packet_id": f"NP-PKT-{n:04d}"}
        self.data = {
            "packet_id": self.ids["packet_id"], "patient_key": key,
            "source_file": f"{key}_packet.pdf", "received_at": received_at,
            "received_channel": channel, "ordering_site": site,
            "extraction_method": "mock_authored",
            "documents": [], "corrections": [],
        }

    def doc(self, doc_type, date, fields, quotes, signer=None, signed=None, ids=None):
        page = len(self.data["documents"]) + 1
        if signed is None:
            signed = signer is not None
        merged = {**self.ids, **(ids or {})}
        self.data["documents"].append({
            "doc_id": f"{self.key}-D{page}", "doc_type": doc_type, "pages": [page],
            "date": date, "signed": signed, "signer": signer,
            "identifiers": {k: merged.get(k) for k in ID_FIELDS[doc_type]},
            "fields": fields,
            "quotes": [{"field": f, "page": page, "text": t} for f, t in quotes],
        })
        return self

    # ---- helpers for repeated admin documents ---------------------------------
    def registration(self, date, payer, product, member=None, ids=None):
        member = member or self.ids["member_id"]
        return self.doc("registration_face_sheet", date,
                        {"payer": payer, "plan_product_raw": product, "member_id": member},
                        [("plan_product_raw", f"Plan product: {product}"),
                         ("member_id", f"Member ID: {member}")], ids=ids)

    def card(self, date, payer, product, member=None):
        member = member or self.ids["member_id"]
        return self.doc("insurance_card", date,
                        {"payer": payer, "plan_product_raw": product, "member_id": member},
                        [("plan_product_raw", f"Plan product: {product}"),
                         ("member_id", f"Member ID: {member}")])

    def benefits(self, date, payer, product, active=True, end=None, member=None, extra_quote=None):
        member = member or self.ids["member_id"]
        q = [("response_date", f"Eligibility & benefits response date: {date}"),
             ("plan_product_raw", f"Plan product: {product}"),
             ("member_id", f"Member ID: {member}"),
             ("coverage_active_on_eval_date",
              f"Coverage on 2026-10-01: {'ACTIVE' if active else 'NOT ACTIVE'}")]
        if extra_quote:
            q.append(extra_quote)
        return self.doc("benefits_response", date,
                        {"response_date": date, "payer": payer, "plan_product_raw": product,
                         "member_id": member, "coverage_active_on_eval_date": active,
                         "product_end_date": end}, q, ids={"member_id": member})

    def order(self, date, signer, indication):
        return self.doc("stress_echo_order", date,
                        {"procedure": "Outpatient stress echocardiogram", "indication": indication},
                        [("procedure", "Procedure: Outpatient stress echocardiogram"),
                         ("indication", f"Indication: {indication}")], signer=signer)

    def resting_ecg(self, date, signer, interp, text):
        return self.doc("resting_ecg_report", date,
                        {"test_date": date, "clinician_interpreted": True,
                         "interpretation": interp, "complete": True},
                        [("test_date", f"Resting 12-lead ECG performed {date}"),
                         ("interpretation", f"Clinician interpretation: {text}")], signer=signer)

    def save(self):
        ef = ExtractionFile.model_validate(self.data)   # schema check before writing
        (OUT / f"{self.key}.json").write_text(json.dumps(ef.model_dump(mode="json"), indent=2) + "\n")


def assessment(**kw):
    base = {"is_assessment": True, "assessment_complete": True,
            "symptom_course_documented": True, "relevant_history_documented": True}
    return {**base, **kw}


NO_PRIOR = {"stated": True, "date": None}


def build() -> list[Packet]:
    ps = []

    # P01 — Martin Alder · SCMA
    p = Packet("P01", 1, {"name": "Martin Alder", "dob": "1954-02-11", "mrn": "NP-100231",
                          "member_id": "SCM-448812"}, "2026-09-18", "fax", "Northpoint Main")
    osei = "R. Osei, MD"
    p.doc("referral", "2026-09-18",
          {"referral_id": "REF-0001", "service_requested": "stress_echo",
           "symptom_trajectory": "worsening", "material_increase_date": "2026-09-10",
           "timing_text": "Exertional chest tightness for ~6 weeks, increased around 2026-09-10."},
          [("service_requested", "Service requested: outpatient stress echocardiogram"),
           ("timing_text", "Exertional chest tightness for ~6 weeks, increased around 2026-09-10."),
           ("urgent_review_requested", "No urgent or expedited review requested.")], signer=osei)
    p.order("2026-09-18", osei, "exertional chest discomfort")
    p.registration("2026-09-18", "Summit Crest", "Summit Crest Medicare Advantage")
    p.card("2026-09-18", "Summit Crest", "Summit Crest Medicare Advantage")
    p.benefits("2026-09-24", "Summit Crest", "Summit Crest Medicare Advantage")
    p.doc("clinician_note", "2026-09-18",
          assessment(complaint_type="exertional_chest_discomfort", prior_stress_echo=NO_PRIOR,
                     symptom_trajectory="worsening", material_increase_date="2026-09-10",
                     timing_text="Exertional chest discomfort for ~6 weeks; increased around 2026-09-10."),
          [("complaint_type", "Exertional chest discomfort for ~6 weeks; increased around 2026-09-10."),
           ("relevant_history_documented", "History: hypertension, hyperlipidemia."),
           ("prior_stress_echo", "No stress echocardiogram in the past 12 months.")], signer=osei)
    p.resting_ecg("2026-09-10", osei, "nonspecific_st_t", "nonspecific ST-T changes")
    ps.append(p)

    # P02 — Julia Brennan · Harborstone PPO
    p = Packet("P02", 2, {"name": "Julia Brennan", "dob": "1968-07-30", "mrn": "NP-100412",
                          "member_id": "HHP-20931177"}, "2026-09-22", "epic_wq", "Lakeview Clinic")
    v = "M. Varga, MD"
    p.doc("referral", "2026-09-22",
          {"referral_id": "REF-0002", "service_requested": "stress_echo",
           "symptom_trajectory": "stable",
           "timing_text": "Exertional dyspnea, gradual onset over 2 months."},
          [("service_requested", "Service requested: outpatient stress echocardiogram"),
           ("timing_text", "Exertional dyspnea, gradual onset over 2 months.")], signer=v)
    p.order("2026-09-22", v, "exertional dyspnea")
    p.registration("2026-09-22", "Harborstone Health", "Harborstone Health Commercial PPO")
    p.card("2026-09-22", "Harborstone Health", "Harborstone Health Commercial PPO")
    p.benefits("2026-09-25", "Harborstone Health", "Harborstone Health Commercial PPO")
    p.doc("clinician_note", "2026-09-22",
          assessment(complaint_type="exertional_dyspnea", symptom_trajectory="stable",
                     timing_text="Exertional dyspnea, gradual onset over 2 months.",
                     prior_stress_echo={"stated": True, "date": "2025-11-03"}),
          [("complaint_type", "Exertional dyspnea, gradual onset over 2 months."),
           ("relevant_history_documented", "History: type 2 diabetes."),
           ("prior_stress_echo", "Prior stress echocardiogram: 2025-11-03.")], signer=v)
    p.resting_ecg("2026-09-15", v, "nondiagnostic", "nondiagnostic")
    ps.append(p)

    # P03 — Rosa Castellano · LCH
    p = Packet("P03", 3, {"name": "Rosa Castellano", "dob": "1959-12-05", "mrn": "NP-100518",
                          "member_id": "LCH-7731-0042"}, "2026-09-15", "fax", "Northpoint Main")
    b = "T. Brandt, MD"
    p.doc("referral", "2026-09-15",
          {"referral_id": "REF-0003", "service_requested": "stress_echo",
           "timing_text": "Exertional chest pressure for 1 month. Routine."},
          [("service_requested", "Service requested: outpatient stress echocardiogram"),
           ("timing_text", "Exertional chest pressure for 1 month. Routine.")], signer=b)
    p.order("2026-09-15", b, "exertional chest pressure; concern for possible coronary ischemia")
    p.registration("2026-09-15", "Lakeshore Community Health", "LCH Managed Medicaid")
    p.card("2026-09-15", "Lakeshore Community Health", "LCH Managed Medicaid")
    p.benefits("2026-08-15", "Lakeshore Community Health", "Lakeshore Community Health Managed Medicaid")
    p.doc("clinician_note", "2026-09-15",
          assessment(complaint_type="exertional_chest_discomfort", ischemia_concern="explicit_yes",
                     tests_mentioned_without_report=["resting ECG"], prior_stress_echo=NO_PRIOR,
                     timing_text="Exertional chest pressure for 1 month."),
          [("complaint_type", "Exertional chest pressure for 1 month."),
           ("ischemia_concern", "Concern for possible coronary ischemia."),
           ("tests_mentioned_without_report", "Resting ECG reviewed in clinic."),
           ("prior_stress_echo", "No prior stress echocardiogram.")], signer=b)
    p.doc("clinician_note", "2026-09-29",
          {"urgent_review_requested": True, "urgent_reason": "new_symptoms_at_rest",
           "symptom_trajectory": "worsening", "material_increase_date": "2026-09-27",
           "timing_text": "New episodes of chest pain at rest since 2026-09-27."},
          [("timing_text", "New episodes of chest pain at rest since 2026-09-27."),
           ("urgent_review_requested", "Requesting urgent cardiology review.")],
          signer="A. Lindqvist, MD")
    ps.append(p)

    # P04 — Patrick Doyle · SCMA · identity conflict
    p = Packet("P04", 4, {"name": "Patrick Doyle", "dob": "1951-03-14", "mrn": "NP-100877",
                          "member_id": "SCM-302177"}, "2026-09-21", "fax", "Riverside")
    k = "J. Kowalski, MD"
    p.doc("referral", "2026-09-21",
          {"referral_id": "REF-0004", "service_requested": "stress_echo",
           "timing_text": "Routine stress echocardiogram requested."},
          [("service_requested", "Service requested: outpatient stress echocardiogram"),
           ("timing_text", "Routine stress echocardiogram requested.")], signer=k)
    p.order("2026-09-21", k, "exertional dyspnea")
    p.registration("2026-09-21", "Summit Crest", "Summit Crest Medicare Advantage",
                   ids={"dob": "1951-04-13"})
    p.card("2026-09-21", "Summit Crest", "Summit Crest Medicare Advantage")
    p.benefits("2026-09-23", "Summit Crest", "Summit Crest Medicare Advantage", member="SCM-302171")
    p.data["documents"][-1]["identifiers"]["dob"] = None   # benefits response shows no DOB
    p.doc("clinician_note", "2026-09-21",
          assessment(complaint_type="exertional_dyspnea", prior_stress_echo=NO_PRIOR,
                     timing_text="Exertional dyspnea."),
          [("complaint_type", "Exertional dyspnea."),
           ("relevant_history_documented", "History: COPD, former smoker."),
           ("prior_stress_echo", "No prior stress echocardiogram.")], signer=k)
    p.resting_ecg("2026-08-20", k, "abnormal", "abnormal")
    ps.append(p)

    # P05 — Grace Eze · Harborstone HMO
    p = Packet("P05", 5, {"name": "Grace Eze", "dob": "1972-05-19", "mrn": "NP-101004",
                          "member_id": "HHH-55102938"}, "2026-09-26", "epic_wq", "Lakeview Clinic")
    n = "C. Nwosu, MD"
    p.doc("referral", "2026-09-08",
          {"referral_id": "REF-0005", "service_requested": "stress_echo",
           "symptom_trajectory": "stable", "timing_text": "Routine; symptoms stable."},
          [("service_requested", "Service requested: outpatient stress echocardiogram"),
           ("timing_text", "Routine; symptoms stable.")], signer=n)
    p.order("2026-09-26", n, "exertional chest discomfort")
    p.registration("2026-09-26", "Harborstone Health", "Harborstone Health Commercial HMO")
    p.card("2026-09-26", "Harborstone Health", "Harborstone Health Commercial HMO")
    p.benefits("2026-09-28", "Harborstone Health", "Harborstone Health Commercial HMO")
    p.doc("clinician_note", "2026-09-26",
          assessment(complaint_type="exertional_chest_discomfort", prior_stress_echo=NO_PRIOR,
                     symptom_trajectory="worsening", material_increase_date="2026-09-22",
                     timing_text="Material increase in frequency since 2026-09-22."),
          [("complaint_type", "Exertional chest discomfort."),
           ("material_increase_date", "Material increase in frequency since 2026-09-22."),
           ("relevant_history_documented", "History: hyperlipidemia, family history of CAD."),
           ("prior_stress_echo", "No prior stress echocardiogram.")], signer=n)
    p.doc("exercise_ecg_report", "2026-06-20",
          {"test_date": "2026-06-20", "clinician_interpreted": True,
           "interpretation": "equivocal", "complete": True},
          [("test_date", "Exercise treadmill ECG performed 2026-06-20"),
           ("interpretation", "Clinician interpretation: equivocal")], signer=n)
    p.resting_ecg("2026-05-02", n, "normal", "normal")
    ps.append(p)

    # P06 — Sami Farouk · LCH · inactive
    p = Packet("P06", 6, {"name": "Sami Farouk", "dob": "1980-10-02", "mrn": "NP-101122",
                          "member_id": "LCH-9012-3345"}, "2026-09-24", "fax", "Riverside")
    h = "D. Haddad, MD"
    p.doc("referral", "2026-09-20", {"referral_id": "REF-0006", "service_requested": "stress_echo"},
          [("service_requested", "Service requested: outpatient stress echocardiogram")], signer=h)
    p.order("2026-09-24", h, "exertional dyspnea")
    p.registration("2026-09-24", "Lakeshore Community Health", "LCH Managed Medicaid")
    p.card("2026-09-24", "Lakeshore Community Health", "LCH Managed Medicaid")
    p.benefits("2026-09-26", "Lakeshore Community Health", "LCH Managed Medicaid",
               active=False, end="2026-09-15",
               extra_quote=("product_end_date", "Coverage terminated effective 2026-09-15."))
    p.doc("clinician_note", "2026-09-24",
          assessment(complaint_type="exertional_dyspnea", prior_stress_echo=NO_PRIOR,
                     symptom_trajectory="worsening", expedited_review_requested=True,
                     timing_text="Progressive exertional dyspnea over 3 months."),
          [("complaint_type", "Progressive exertional dyspnea over 3 months."),
           ("expedited_review_requested", "Requesting expedited cardiology review."),
           ("relevant_history_documented", "History: asthma."),
           ("prior_stress_echo", "No prior stress echocardiogram.")], signer=h)
    p.resting_ecg("2026-09-24", h, "nonspecific_st_t", "nonspecific ST-T changes")
    ps.append(p)

    # P07 — Ellen Gallagher · SCMA · repeat not met; unsigned draft
    p = Packet("P07", 7, {"name": "Ellen Gallagher", "dob": "1949-01-27", "mrn": "NP-101309",
                          "member_id": "SCM-118420"}, "2026-09-16", "fax", "Northpoint Main")
    r = "P. Rinaldi, MD"
    p.doc("referral", "2026-09-16", {"referral_id": "REF-0007", "service_requested": "stress_echo"},
          [("service_requested", "Service requested: outpatient stress echocardiogram")], signer=r)
    p.order("2026-09-16", r, "exertional chest discomfort")
    p.registration("2026-09-16", "Summit Crest", "Summit Crest Medicare Advantage")
    p.card("2026-09-16", "Summit Crest", "Summit Crest Medicare Advantage")
    p.benefits("2026-09-19", "Summit Crest", "Summit Crest Medicare Advantage")
    p.doc("clinician_note", "2026-09-16",
          assessment(complaint_type="exertional_chest_discomfort", symptom_trajectory="stable",
                     timing_text="Exertional chest discomfort, stable.",
                     prior_stress_echo={"stated": True, "date": "2026-06-11"},
                     interval_change_after_prior=False),
          [("complaint_type", "Exertional chest discomfort, stable."),
           ("relevant_history_documented", "History: hypertension."),
           ("prior_stress_echo", "Stress echocardiogram performed 2026-06-11; "
                                 "no interval change in symptoms since that study.")], signer=r)
    p.resting_ecg("2026-09-05", r, "nonspecific_st_t", "nonspecific ST-T changes")
    p.doc("clinician_note", "2026-09-30",
          {"symptom_trajectory": "worsening", "timing_text": "pt reports more SOB?"},
          [("timing_text", "pt reports more SOB?")], signer=None, signed=False)
    ps.append(p)

    # P08 — Daniel Hughes · PPO · same-day conflicting timing
    p = Packet("P08", 8, {"name": "Daniel Hughes", "dob": "1963-08-08", "mrn": "NP-101455",
                          "member_id": "HHP-77120345"}, "2026-09-25", "fax", "Riverside")
    p.doc("referral", "2026-09-25",
          {"referral_id": "REF-0008", "service_requested": "stress_echo",
           "symptom_trajectory": "worsening", "material_increase_date": "2026-09-18",
           "timing_text": "Exertional chest discomfort, worsening over the past week."},
          [("service_requested", "Service requested: outpatient stress echocardiogram"),
           ("timing_text", "Exertional chest discomfort, worsening over the past week.")],
          signer="L. Okafor, MD (PCP)")
    p.order("2026-09-25", "L. Okafor, MD (PCP)", "exertional chest discomfort")
    p.registration("2026-09-25", "Harborstone Health", "Harborstone Health Commercial PPO")
    p.card("2026-09-25", "Harborstone Health", "Harborstone Health Commercial PPO")
    p.benefits("2026-09-26", "Harborstone Health", "Harborstone Health Commercial PPO")
    p.doc("clinician_note", "2026-09-25",
          assessment(complaint_type="exertional_chest_discomfort", symptom_trajectory="stable",
                     prior_stress_echo=NO_PRIOR,
                     timing_text="Exertional chest discomfort, stable pattern for 4 months, no recent change."),
          [("complaint_type", "Exertional chest discomfort, stable pattern for 4 months, no recent change."),
           ("relevant_history_documented", "History: hypertension, obesity."),
           ("prior_stress_echo", "No prior stress echocardiogram.")],
          signer="S. Patel, MD (cardiology intake)")
    p.resting_ecg("2026-09-20", "S. Patel, MD (cardiology intake)", "abnormal", "abnormal")
    ps.append(p)

    # P09 — Lucia Ibarra · Harborstone product unclear · consult referral, no order
    p = Packet("P09", 9, {"name": "Lucia Ibarra", "dob": "1975-03-03", "mrn": "NP-101610",
                          "member_id": "HH-6610392"}, "2026-09-19", "fax", "Lakeview Clinic")
    g = "E. Grant, MD (PCP)"
    p.doc("referral", "2026-09-19",
          {"referral_id": "REF-0009", "service_requested": "consult",
           "timing_text": "Exertional dyspnea for 3 months. Routine."},
          [("service_requested", "Service requested: cardiology consult"),
           ("timing_text", "Exertional dyspnea for 3 months. Routine.")], signer=g)
    p.registration("2026-09-19", "Harborstone Health", "Harborstone Health Commercial")
    p.card("2026-09-19", "Harborstone Health", "Harborstone Health")
    p.benefits("2026-09-21", "Harborstone Health", "Harborstone Health Commercial")
    p.doc("clinician_note", "2026-09-19",
          assessment(complaint_type="exertional_dyspnea", prior_stress_echo=NO_PRIOR,
                     timing_text="Exertional dyspnea for 3 months."),
          [("complaint_type", "Exertional dyspnea for 3 months."),
           ("relevant_history_documented", "History: anemia, resolved."),
           ("prior_stress_echo", "No prior stress echocardiogram.")], signer=g)
    p.resting_ecg("2026-09-12", g, "abnormal", "abnormal")
    ps.append(p)

    # P10 — Karl Jensen · LCH · not met; raw tracing trap
    p = Packet("P10", 10, {"name": "Karl Jensen", "dob": "1957-11-21", "mrn": "NP-101777",
                           "member_id": "LCH-4420-1189"}, "2026-09-14", "epic_wq", "Northpoint Main")
    s = "H. Sato, MD"
    txt = "Intermittent sharp chest pain, worse with deep breathing and lying flat, not related to exertion."
    p.doc("referral", "2026-09-14",
          {"referral_id": "REF-0010", "service_requested": "stress_echo", "timing_text": txt},
          [("service_requested", "Service requested: outpatient stress echocardiogram"),
           ("timing_text", txt)], signer=s)
    p.order("2026-09-14", s, "chest pain")
    p.registration("2026-09-14", "Lakeshore Community Health", "LCH Managed Medicaid")
    p.card("2026-09-14", "Lakeshore Community Health", "LCH Managed Medicaid")
    p.benefits("2026-09-17", "Lakeshore Community Health", "LCH Managed Medicaid")
    p.doc("clinician_note", "2026-09-14",
          assessment(complaint_type="nonexertional", ischemia_concern="explicit_no",
                     prior_stress_echo=NO_PRIOR, timing_text=txt,
                     exercise_ecg_history={"stated": True, "none_since": "2025-10-01"}),
          [("complaint_type", txt),
           ("ischemia_concern", "Low concern for ischemia."),
           ("exercise_ecg_history", "No exercise ECG in the past 12 months."),
           ("prior_stress_echo", "No prior stress echocardiogram.")], signer=s)
    p.resting_ecg("2026-08-30", s, "normal", "normal")
    p.doc("ecg_tracing_raw", "2026-08-30",
          {"test_date": "2026-08-30",
           "machine_statement": "ABNORMAL ECG – possible ST depression – unconfirmed"},
          [("machine_statement", "ABNORMAL ECG – possible ST depression – unconfirmed")],
          signed=False)
    ps.append(p)
    return ps


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for p in build():
        p.save()
    print(f"wrote {len(build())} extraction files to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
