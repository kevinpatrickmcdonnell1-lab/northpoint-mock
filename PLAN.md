# Northpoint × Tennr — Stress-Echo Readiness Mock: Build Plan

**Owner:** Kevin (SC candidate) · **Builder:** Claude Code · **Policy:** SC-CARD-1.1 · **Evaluation date:** 2026-10-01

This plan is the handoff spec for Claude Code. Read `CLAUDE.md` first; it holds the rules that must not break. Sections 1–2 cover the business story and the slides. Sections 3–10 are the build spec. Section 11 covers the demo and the debrief.

> **Status of inputs:** We have the Primer, the Demo brief, the Prospect Profile and the Payer Rules. **The ten synthetic patient PDFs have not been supplied yet.** Section 6 defines ten mock packets that cover every rule path. When the real PDFs arrive, follow Section 6.4 to swap them in. Don't reuse the mock expected-results table for real packets. Work it out again by hand from the real pages.

---

## 1. Value hypothesis (what the slides argue)

### 1.1 The problem, in Northpoint's own numbers (Prospect Profile, July–Sept 2026 averages)
- **600** new outpatient stress-echo orders per month. About **60%** arrive by fax; the rest come from Epic work queues.
- **Admin readiness** takes a median of **5 business days** from order receipt (target: 3). Readiness means identity resolved, coverage verified, required evidence adequate and PA approved where needed. Clinical clearance is **separate**.
- **Active admin handling** is an estimated **18 min per order** (target: toward 12). This is a staff estimate and needs validation.
- **Late readiness disruptions:** **48 of ~480** booked stress-echo appointments per month (**10%**) are cancelled or rescheduled within 2 business days of the slot because PA or documents are still unresolved. The target is 5%.
- **Qualitative pain:** staff reopen cases just to check status, nobody clearly owns missing-info follow-up, and **scheduling sometimes receives an order before it is administratively ready.**
- 8 intake coordinators and 3 centralized auth specialists. There is **no staffing-cut target**. The goal is to absorb growth.

### 1.2 Hypothesis (one sentence)
> For outpatient stress-echo orders, Tennr can turn incoming packets into a structured case and check identity, coverage, PA requirement and evidence against Northpoint's payer rules. Every exception goes to a **named owner** with a **reason code and page-level evidence**. **Scheduling only receives orders that have passed every gate.** That should cut late readiness disruptions and the repeat status checks that inflate handling time.

### 1.3 The staff work that changes
| Today (per profile) | With the workflow |
|---|---|
| Coordinator opens the fax and manually reconciles registration, card and notes | The packet arrives split by document type with fields extracted and page references kept. The coordinator only touches **exceptions**. |
| Coverage is checked against a card or an old response | COV-1 accepts only a dated benefits response for the exact product inside the window. Anything else is *pending*, *conflicting* or *inactive*, each with an owner. |
| PA need and evidence gaps are found late, sometimes after booking | PA requirement and each criterion are assessed up front. Missing reports trigger a Communications Coordinator request **before** scheduling. |
| Unclear owner, shared follow-up list, reopening to check status | One queue per owner role, a visible next action and an audit trail. Status is readable without reopening the case. |
| Scheduling sometimes gets not-ready orders | The scheduling handoff is a **hard gate**. A blocked attempt is recorded along with the gates that failed. |
| Clinical urgency is mixed into admin work | Clinical priority is routed **separately** to nursing triage and never waits on insurance or PA. |

### 1.4 What Northpoint could measure (bounded, not promised)
- **Primary:** late readiness disruption rate, split by cause (PA vs. documents). Baseline is 10%.
- **Primary:** median admin readiness time, plus an **aging** view for orders still waiting.
- **Secondary:** active handling minutes per order (time-study sample before and after), reopen/status-check touches, exception mix by reason code and owner.
- **Guardrails:** zero orders handed to scheduling with an open gate; time from receipt to urgent-triage notification; extraction correction rate.
- **Bounded examples from the profile (to test, not claim):** 6 fewer minutes × 600 orders ≈ **60 gross staff hours/month**. Moving 10% → 5% disruptions on 480 bookings ≈ **24 fewer disrupted appointments/month**. Neither figure is net capacity, recovered revenue or proof of causation. **Do not** use 480 ÷ 600 as a conversion rate.

### 1.5 Why stress echo first
The supplied payer rules cover only this service. The disruption metric is defined for it. 3 of the 4 plan products require PA. Consult referrals (2,000/month) are a later expansion and are **out of scope** for these rules.

### 1.6 Assumptions to state out loud (and test in discovery)
1. Most late disruptions come from PA or document gaps that could have been found at intake, rather than from payer turnaround alone. *Ask for the PA-vs-docs split and how reliably the reason codes are captured.*
2. Repeat status checks make up a meaningful share of the 18 minutes. *Ask how the estimate was produced.*
3. Routine (PRI-3) orders do **not** need a separate nursing sign-off before scheduling. *Ask; this is a config flag in the mock.*
4. Sites differ. *Ask which sites drive the 48 disruptions and whether work is centralized or local.*
5. Epic is the authority for identity. The mock's "verified" only means the packet is internally consistent. Production would match against Epic/EMPI.

### 1.7 Discovery questions by stakeholder
- **Operations:** Which sites and payers drive the 48? How is a "readiness-related" disruption coded today, and do you trust it? What happens to the slot? What would a pilot have to show you?
- **Intake:** Who owns missing-info follow-up today? What's on the shared follow-up list and how old is it? How do you learn a PA decision came back? How many times is a typical order touched?
- **IT & Integrations:** How is patient matching done (EMPI, ADT feed)? Which Epic work queues carry these orders? What interfaces exist (HL7 ADT/ORM/SIU, FHIR, Interconnect)? Fax server? Who supports an interface failure at 7 a.m.? Is write-back to a work queue or order status allowed?
- **Auth (if present):** What are the PA channels by payer (portal, fax, 278)? Typical turnaround? Most common pend reasons?
- **Clinical (via Ops):** How is the triage nurse notified of an urgent order today, and how fast?

---

## 2. Opening slides (5 slides, built separately from the app)
1. **Northpoint today:** the four numbers in 1.1 and the "scheduling gets not-ready orders" quote.
2. **Hypothesis and the staff work that changes:** 1.2 plus a condensed version of the 1.3 table.
3. **Where Tennr fits:** flow diagram (fax/Epic WQ → Intake Operations → Insurance Benefits Manager + Qualification Review Engine → routing → Prior Authorization Manager → Epic scheduling/work queue handoff; Communications Coordinator alongside). Epic stays the system of record.
4. **What we'd measure in a pilot:** 1.4 with the bounded examples and caveats on the slide.
5. **Assumptions we want to test today:** 1.6 framed as questions.

---

## 3. Architecture

```
 data/packets/*.pdf ──► ingest (page split, page text, page PNG) ──► data/pages/
                                     │
 data/extractions/*.json  ◄── prep-time extraction (manual or LLM-drafted, human-verified;
   (document FACTS only, with           corrections logged)
    file + page + quote)
                                     │
                                     ▼
            ┌──────────── evaluate(facts, sim_events, policy) ────────────┐
            │  rules/priority   PRI-1..4                                 │  pure functions,
            │  rules/identity   ID-1                                     │  no I/O, no clock
            │  rules/coverage   COV-1  + plan product resolution         │
            │  rules/evidence   DOC-1 QUAL-1 QUAL-2 REPEAT-1 HMO-1       │
            │  gates.py         G-* gates   → holds                      │
            │  actions.py       reason codes → next action + owner role  │
            └──────────────────────────────┬─────────────────────────────┘
                                           ▼
                              CaseState (derived, never stored as truth)
                                           │
     SQLite: cases, sim_events (append-only), audit_log (append-only), outbox
                                           │
     FastAPI + Jinja2 + HTMX: Queue · Case detail · Page viewer · Outbox · Metrics
                                           │
     Handoff stubs ──► outbox/*.json + outbox table
       (Epic scheduling, Epic intake WQ, Epic In Basket triage, Payer PA portal, Fax/SMS)
```

**Design choices to explain in the debrief**
- **State is derived, not stored.** `CaseState = evaluate(source_facts, simulated_events, policy)`. Source facts are immutable. Simulated events sit in a separate append-only table. Re-running the engine always gives the same answer, which is how we show all ten patients go through one implementation.
- **Rules are code and dates are config.** `policy/sc-card-1.1.yaml` holds the evaluation date, the windows and the plan matrix. A test checks that the derived window start dates match the dates printed in the rules PDF.
- **Every rule result carries evidence.** Each one has `{rule_id, status, reason_code, reason_text, evidence_refs[{file, page, quote}]}`. If evidence is absent, the reason text says what was looked for.
- **Stack:** Python 3.11+, FastAPI, Jinja2, HTMX, SQLite, pydantic v2, pytest, pypdf/pdfplumber, pdftoppm (poppler), reportlab (mock PDFs only). It runs locally with `make demo`. There's no hosting, no auth and no live model calls.

---

## 4. Repository layout

```
northpoint-mock/
  CLAUDE.md
  PLAN.md
  Makefile                      # setup, mock, ingest, test, demo, reset
  config/
    policy_sc_card_1_1.yaml     # dates, windows, plan matrix, product aliases
    workflow.yaml               # assumption flags (Section 7.4), owner roles
  data/
    packets/                    # P01..P10 PDFs (mock now; real later)
    pages/                      # ingest output: <file>/<page>.txt + .png
    extractions/                # P01.json .. P10.json  (facts only)
  scripts/
    make_mock_packets.py        # reportlab: renders mock PDFs FROM extraction JSON
    ingest.py                   # split pages, text, PNG; supports combined PDFs
    extract_draft.py            # OPTIONAL, real PDFs: LLM draft → human verify
    check_extractions.py        # schema + every quote found on its cited page
  app/
    models.py                   # pydantic: facts, rule results, CaseState, events
    policy.py                   # load + validate policy/workflow config
    rules/{priority,identity,coverage,plan,evidence}.py
    gates.py  actions.py  engine.py
    store.py                    # sqlite: cases, sim_events, audit_log, outbox
    handoffs.py                 # stubs + idempotency
    metrics.py
    main.py                     # FastAPI routes
    templates/  static/
  tests/
    test_policy_dates.py
    test_rules_priority.py test_rules_identity.py test_rules_coverage.py
    test_rules_evidence.py test_gates.py
    test_golden_ten.py          # Section 6.3 table
    test_invariants.py          # Section 9
  outbox/
  docs/
    assumptions.md              # every interpretation call, with rule ID
    build_log.md                # agent mistakes found + how verified
    demo_script.md
```

---

## 5. Data model

### 5.1 Extraction file (one per packet): **document facts only**
Pydantic models use `extra="forbid"`. **No derived fields** (no `priority`, `status`, `pa_required`, `verified`, `ready`). A test enforces this.

```jsonc
{
  "packet_id": "NP-PKT-0001",
  "patient_key": "P01",
  "source_file": "P01_packet.pdf",
  "received_at": "2026-09-18",          // order receipt; drives aging
  "received_channel": "fax",            // fax | epic_wq
  "extraction_method": "mock_authored", // mock_authored | llm_draft_human_verified | manual
  "documents": [
    {
      "doc_id": "P01-D2",
      "doc_type": "stress_echo_order",  // see 5.2
      "pages": [2],
      "date": "2026-09-18",
      "signed": true,
      "signer": "R. Osei, MD",
      "identifiers": {"name": "Martin Alder", "dob": "1954-02-11",
                       "mrn": "NP-100231", "packet_id": "NP-PKT-0001", "member_id": null},
      "fields": { "procedure": "Outpatient stress echocardiogram",
                  "indication": "exertional chest discomfort" },
      "quotes": [{"field": "indication", "page": 2,
                  "text": "Indication: exertional chest discomfort"}]
    }
  ],
  "corrections": [   // disclosed manual corrections (real PDFs)
    {"doc_id": "...", "field": "...", "extracted": "...", "corrected": "...",
     "page": 3, "reason": "OCR misread 8 as 3", "by": "Kevin", "at": "2026-11-26"}
  ]
}
```

### 5.2 Document types and their `fields`
| doc_type | fields |
|---|---|
| `referral` | `referral_id`, `service_requested` (`stress_echo`\|`consult`), `urgent_review_requested` (bool), `urgent_reason` (`new_symptoms_at_rest`\|`high_risk_finding`\|`other_immediate_concern`\|null), `expedited_review_requested` (bool), `material_increase_date` (date\|null), `symptom_trajectory` (`new`\|`worsening`\|`stable`\|`improving`\|`not_stated`), `timing_text` |
| `stress_echo_order` | `procedure`, `indication` |
| `clinician_note` | all the priority fields from `referral`, plus `is_assessment` (bool), `assessment_complete` (bool), `symptom_course_documented`, `relevant_history_documented`, `complaint_type` (`exertional_chest_discomfort`\|`exertional_dyspnea`\|`nonexertional`\|`other`\|`not_stated`), `ischemia_concern` (`explicit_yes`\|`explicit_no`\|`not_stated`), `prior_stress_echo` (`{stated: bool, date: date\|null}`, where stated + null date = "none"), `interval_change_after_prior` (bool\|null), `repeat_rationale` (str\|null), `tests_mentioned_without_report` ([str]), `exercise_ecg_history` (`{stated: bool, none_since: date\|null}`) |
| `registration_face_sheet` | `payer`, `plan_product_raw`, `member_id` |
| `insurance_card` | `payer`, `plan_product_raw`, `member_id` |
| `benefits_response` | `response_date`, `payer`, `plan_product_raw`, `member_id`, `coverage_active_on_eval_date` (bool\|null), `product_end_date` (date\|null) |
| `resting_ecg_report` | `test_date`, `clinician_interpreted` (bool), `interpretation` (`normal`\|`abnormal`\|`nondiagnostic`\|`nonspecific_st_t`\|`other`), `complete` (bool) |
| `exercise_ecg_report` | `test_date`, `clinician_interpreted`, `interpretation` (`equivocal`\|`normal`\|`negative`\|`positive`\|`other`), `complete` |
| `ecg_tracing_raw` | `test_date`, `machine_statement`. **Never used by any rule.** Shown as "present, not used". |
| `other` | free text; never used by rules |

`signed: false` documents are listed in the UI as "unsigned, not used as evidence" and are excluded from all rules.

### 5.3 Simulated events (separate table, append-only)
```
sim_event: event_id, patient_key, type, decision, actor_role, actor_name,
           event_time (simulated business clock, starts 2026-10-01T09:00),
           recorded_at (wall clock), note, simulated=TRUE (constant)
types:
  PAYER_PA_DECISION        decision: approved | denied | pended      actor: payer (mock) / recorded by Auth Specialist
  CLINICAL_REVIEW_DECISION decision: cleared_for_scheduling | not_cleared | reviewed_hold
                           actor: Cardiology Clinical Triage RN / Cardiologist
  IDENTITY_RESOLUTION      decision: confirmed value per field + Epic reference  actor: Intake Coordinator
  BENEFITS_RESPONSE        a new simulated E&B response (same schema as benefits_response) actor: Insurance Benefits Manager (mock)
```
The UI creates events only through clearly labelled **"Simulate…"** controls. The engine never creates events. The UI shows a `SIMULATED – not from PDF` badge next to anything derived from an event.

### 5.4 Audit log (append-only)
`audit_id, patient_key, at, actor (system|role), kind (evaluation|state_change|sim_event|handoff_attempt|handoff_sent|handoff_blocked|correction), detail_json`. Each evaluation records `policy_version`, the evaluation date, a summary of rule results with evidence refs, and a diff from the previous state.

---

## 6. Mock dataset (10 packets)

### 6.1 Design principle
Each packet is a set of **document facts**. The packets were chosen so that the ten together exercise every status of every rule, plus the known traps. Expected outcomes (6.3) were worked out **by hand from the rules**. They exist only as tests and must never be read by the engine. `make_mock_packets.py` renders each extraction JSON into a multi-page PDF (one document per page, with a header showing doc type, date, signer and "SYNTHETIC – MOCK DATA" in the footer), so ingest, page references and the page viewer work exactly as they will with real PDFs. Names, IDs and clinicians are invented.

### 6.2 Packet facts
Pages are in packet order. "Signed" means signed by the named clinician. All dates are 2026 unless shown otherwise. Unless a story says otherwise (P08), signed referral and clinician notes in the same packet must not contradict each other on timing or trajectory; set fields that aren't listed to `not_stated` or `false`. Each packet's identifiers (name, DOB, MRN, packet ID, member ID) are consistent on every page except where P04 says otherwise.

**P01 — Martin Alder** · DOB 1954-02-11 · MRN NP-100231 · Summit Crest Medicare Advantage · member SCM-448812 · received 09-18 (fax) · *Story: clean PA path; priority-window boundary*
1. Referral, signed 09-18, R. Osei MD: stress echo; "exertional chest tightness ~6 weeks, increased around **09-10**"; no urgent or expedited request; trajectory `worsening`, material_increase_date 09-10
2. Stress echo order, signed 09-18: indication "exertional chest discomfort"
3. Registration: SCMA "Summit Crest Medicare Advantage", SCM-448812, name/DOB/MRN match
4. Insurance card: same product and member ID
5. Benefits response, **09-24**: SCM-448812, Summit Crest Medicare Advantage, active on 10-01
6. Clinician assessment, signed 09-18: complete; exertional chest discomfort; HTN and hyperlipidemia; ischemia concern not stated; "No stress echocardiogram in the past 12 months"; trajectory `worsening`, material_increase_date 09-10
7. Resting ECG report 09-10, interpreted by R. Osei MD: **nonspecific ST-T changes**

**P02 — Julia Brennan** · DOB 1968-07-30 · MRN NP-100412 · Harborstone Health Commercial PPO · HHP-20931177 · received 09-22 (Epic WQ) · *Story: no-PA case that is genuinely ready*
1. Referral, signed 09-22: stress echo; exertional dyspnea, gradual over 2 months; trajectory `stable`
2. Order, signed 09-22: indication "exertional dyspnea"
3. Registration: "Harborstone Health Commercial PPO", HHP-20931177
4. Card: same
5. Benefits **09-25**: same member and product, active on 10-01
6. Assessment, signed 09-22: complete; exertional dyspnea; prior stress echo **2025-11-03** (outside the 180-day window)
7. Resting ECG 09-15, interpreted: **nondiagnostic**

**P03 — Rosa Castellano** · DOB 1959-12-05 · MRN NP-100518 · Lakeshore Community Health Managed Medicaid · LCH-7731-0042 · received 09-15 (fax) · *Story: urgent clinical escalation in parallel with admin gaps*
1. Referral, signed 09-15: stress echo; exertional chest pressure for 1 month; routine
2. Order, signed 09-15: indication "exertional chest pressure; concern for possible coronary ischemia"
3. Registration: LCH Managed Medicaid, LCH-7731-0042
4. Card: same
5. Benefits response dated **08-15**: active (stale, before 09-01)
6. Assessment, signed 09-15: complete; ischemia concern **explicit_yes**; "resting ECG reviewed in clinic" with **no report attached** (`tests_mentioned_without_report: ["resting ECG"]`); exercise ECG history not stated; "no prior stress echocardiogram"
7. Clinician note, signed **09-29**, A. Lindqvist MD: "new episodes of chest pain **at rest** since 09-27. **Requesting urgent cardiology review.**" (`urgent_review_requested: true`, `urgent_reason: new_symptoms_at_rest`)

**P04 — Patrick Doyle** · MRN NP-100877 · Summit Crest Medicare Advantage · received 09-21 (fax) · *Story: identity conflict; never auto-select*
1. Referral, signed 09-21: DOB **1951-03-14**, stress echo, routine
2. Order, signed 09-21: indication "exertional dyspnea"
3. Registration: DOB **1951-04-13**, member **SCM-302177**
4. Card: member **SCM-302177**
5. Benefits **09-23**: member **SCM-302171**, SCMA, active
6. Assessment, signed 09-21: DOB 1951-03-14; complete; exertional dyspnea; no prior stress echo
7. Resting ECG 08-20, interpreted: **abnormal**

**P05 — Grace Eze** · DOB 1972-05-19 · MRN NP-101004 · Harborstone Health Commercial HMO · HHH-55102938 · received 09-26 (Epic WQ) · *Story: HMO-1 trap; newer note overrides older timing*
1. Referral, signed **09-08**: stress echo; routine, ordinary timing; trajectory `stable` (the newer note below documents a material change, so it overrides; this is not a PRI-4 conflict)
2. Order, signed 09-26: indication "exertional chest discomfort"
3. Registration: "Harborstone Health Commercial HMO", HHH-55102938
4. Card: same
5. Benefits **09-28**: same, active
6. Assessment, signed **09-26**: complete; exertional chest discomfort; "**material increase in frequency since 09-22**"; no prior stress echo
7. Exercise ECG report **06-20**, interpreted, complete: **equivocal**
8. Resting ECG report **05-02**, interpreted: normal (**older than the 90-day window**)

**P06 — Sami Farouk** · DOB 1980-10-02 · MRN NP-101122 · LCH Managed Medicaid · LCH-9012-3345 · received 09-24 (fax) · *Story: coverage inactive; explicit expedited request*
1. Referral, signed 09-20: stress echo; routine
2. Order, signed 09-24: "exertional dyspnea"
3. Registration and 4. Card: LCH Managed Medicaid, LCH-9012-3345
5. Benefits **09-26**: "coverage **terminated effective 09-15**", `coverage_active_on_eval_date: false`
6. Assessment, signed 09-24: complete; progressive exertional dyspnea over 3 months; "**Requesting expedited cardiology review**"; no prior stress echo
7. Resting ECG 09-24, interpreted: **nonspecific ST-T**

**P07 — Ellen Gallagher** · DOB 1949-01-27 · MRN NP-101309 · SCMA · SCM-118420 · received 09-16 (fax) · *Story: REPEAT-1 not met (not a denial); unsigned-note trap*
1. Referral, signed 09-16: stress echo; routine
2. Order, signed 09-16: "exertional chest discomfort"
3–4. Registration and card: SCMA, SCM-118420
5. Benefits 09-19: active
6. Assessment, signed 09-16: complete; exertional chest discomfort, stable; "**stress echocardiogram performed 06-11; no interval change in symptoms since that study**"
7. Resting ECG 09-05, interpreted: **nonspecific ST-T**
8. Progress note **09-30, UNSIGNED draft**: "pt reports more SOB?" (must be ignored and shown as unsigned)

**P08 — Daniel Hughes** · DOB 1963-08-08 · MRN NP-101455 · Harborstone Health Commercial PPO · HHP-77120345 · received 09-25 (fax) · *Story: admin-ready but clinically undetermined*
1. Referral, signed **09-25** (PCP): "exertional chest discomfort, **worsening over the past week**" (trajectory `worsening`, material_increase_date 09-18)
2. Order, signed 09-25: "exertional chest discomfort"
3–4. Registration and card: PPO, HHP-77120345
5. Benefits 09-26: active
6. Cardiology intake note, signed **09-25**: complete; "exertional chest discomfort, **stable pattern for 4 months, no recent change**" (trajectory `stable`); no prior stress echo
7. Resting ECG 09-20, interpreted: **abnormal**

**P09 — Lucia Ibarra** · DOB 1975-03-03 · MRN NP-101610 · Harborstone (product unclear) · HH-6610392 · received 09-19 (fax) · *Story: unknown product + consult referral is not an order*
1. Referral, signed 09-19: **cardiology consult** (service `consult`); exertional dyspnea for 3 months; routine
2. Registration: "Harborstone Health Commercial" (**no PPO/HMO**), HH-6610392
3. Card: "Harborstone Health", HH-6610392, no product line
4. Benefits 09-21: HH-6610392, "Harborstone Health Commercial", active (product not specified)
5. Assessment, signed 09-19 (PCP): complete; exertional dyspnea; ischemia concern not stated; no prior stress echo
6. Resting ECG 09-12, interpreted: **abnormal**
*(No stress echo order in the packet.)*

**P10 — Karl Jensen** · DOB 1957-11-21 · MRN NP-101777 · LCH Managed Medicaid · LCH-4420-1189 · received 09-14 (Epic WQ) · *Story: "not met" vs "insufficient"; raw-tracing trap*
1. Referral, signed 09-14: stress echo; "intermittent sharp chest pain, worse with deep breathing and lying flat, not related to exertion"; routine
2. Order, signed 09-14: indication "chest pain"
3–4. Registration and card: LCH, LCH-4420-1189
5. Benefits 09-17: active
6. Assessment, signed 09-14: complete; `complaint_type: nonexertional`; ischemia concern **explicit_no**; exercise ECG history "**none in the past 12 months**" (`none_since: 2025-10-01`); no prior stress echo
7. Resting ECG report 08-30, clinician-interpreted: **normal**
8. Raw ECG tracing 08-30, machine statement "ABNORMAL ECG – possible ST depression – unconfirmed", no clinician interpretation (**must not count for QUAL-2 or priority**)

### 6.3 Expected results at baseline (golden tests, derived by hand)
S = supported, NM = not met, I = insufficient/conflicting, — = not applicable

| | Priority | Identity | Coverage | PA req | DOC-1 | QUAL-1 | QUAL-2 | REPEAT-1 | HMO-1 | Evidence | Next action → owner |
|---|---|---|---|---|---|---|---|---|---|---|---|
| P01 | Routine (PRI-3) | Verified | Verified | Required | S | S | S (A) | S | — | **S** | Prepare PA submission → Auth Specialist |
| P02 | Routine | Verified | Verified | Not required | S | S | S (A) | S | — | **S** (visibility) | Scheduling handoff **sendable** (no clinical gate for routine) |
| P03 | **Urgent (PRI-1)** | Verified | **Pending** (stale) | Required | S | S | **I** | S | — | **I** | ① Notify triage now → Clinical Triage RN ② Refresh E&B → Intake ③ Request resting ECG report → Intake via Comms Coordinator |
| P04 | Routine | **Conflicting** (DOB; member ID) | **Conflicting** (member mismatch) | Required | S | S | S (A) | S | — | **S** | Verify identity, both values and sources shown → Intake Coordinator. PA prep + scheduling held |
| P05 | **Expedited (PRI-2)** | Verified | Verified | Required | S | S | S (**B**) | S | **I** | **I** | ① Expedited clinical review → Clinical Review RN ② Request current resting ECG → Intake via Comms |
| P06 | **Expedited (PRI-2)** | Verified | **Inactive** | Required (LCH; recheck on new coverage) | S | S | S (A) | S | — | **S** | ① Expedited clinical review ② Obtain current coverage, patient outreach → Intake via Comms |
| P07 | Routine | Verified | Verified | Required | S | S | S (A) | **NM** | — | **NM** | Do not prepare PA. Review with ordering clinician → Auth Specialist ("not a payer denial") |
| P08 | **Unable to determine (PRI-4)** | Verified | Verified | Not required | S | S | S (A) | S | — | **S** | Clarify timing → Clinical Triage RN. Scheduling held |
| P09 | Routine | Verified | **Pending** (product unmatched) | **Unknown** | **I** (no order) | **I** | S (A) | S | — | **I** | ① Confirm exact product → Intake/Benefits ② Request signed stress echo order → Intake via Comms. (Consult routes to consult intake, out of scope) |
| P10 | Routine (raw tracing ignored) | Verified | Verified | Required | S | **NM** | **NM** | S | — | **NM** | Do not prepare PA. Review with ordering clinician → Auth Specialist |

Baseline: **1 sendable to scheduling, 1 ready for PA prep, 8 held**, every one with a reason and an owner. Present this as a property of the mock set, **not** as Northpoint's exception mix.

**Scripted transitions (each a labelled simulated event; covered by tests):**
- P01: `PAYER_PA_DECISION approved` → scheduling handoff becomes sendable → send. Before that event, a handoff attempt is **blocked** with `G-PA-APPROVED` and the block is audited.
- P04: `IDENTITY_RESOLUTION` (Intake confirms DOB 1951-03-14 and member SCM-302177 in Epic, note required) → identity verified; coverage stays conflicting → `BENEFITS_RESPONSE` (simulated, SCM-302177, active) → coverage verified → PA prep ready.
- P08: `CLINICAL_REVIEW_DECISION cleared_for_scheduling` → handoff sendable.
- P03: `CLINICAL_REVIEW_DECISION reviewed_hold` → shows triage acted while admin holds remain. No treatment decision is recorded.

### 6.4 Swapping in the real PDFs
1. Put the PDFs in `data/packets/`. If a single combined PDF holds several patients, `ingest.py` keeps **original file + original page number** and assigns page ranges per packet.
2. Draft the extractions, either by hand or with `extract_draft.py` (prep-time LLM draft, output marked `llm_draft_human_verified` only after review). Every quote must be found on its cited page (`check_extractions.py`). Log every manual correction in `corrections[]`.
3. **Work out a new 6.3 table by hand from the real pages before running the engine.** Then run the tests. Each mismatch is either an extraction error, a misread rule or a code bug. Record which in `docs/build_log.md`.
4. Mock packets move to `data/packets/mock/` and stay available as a regression suite.

---

## 7. Rule engine spec

### 7.1 Policy config (`policy_sc_card_1_1.yaml`)
```yaml
policy_version: SC-CARD-1.1
effective: {from: 2026-09-01, to: 2026-12-31}
eval_date: 2026-10-01
windows:            # days back from eval_date, inclusive start
  benefits_response: 30   # -> 2026-09-01
  pri2_material_increase: 14  # -> 2026-09-17 .. 2026-10-01
  resting_ecg: 90         # -> 2026-07-03
  exercise_ecg: 180       # -> 2026-04-04
  prior_stress_echo: 180  # -> 2026-04-04
plans:
  PLAN-SCMA:  {payer: Summit Crest, product: "Summit Crest Medicare Advantage", pa: required}
  PLAN-HSPPO: {payer: Harborstone Health, product: "Harborstone Health Commercial PPO", pa: not_required}
  PLAN-HSHMO: {payer: Harborstone Health, product: "Harborstone Health Commercial HMO", pa: required, extra: [HMO-1]}
  PLAN-LCH:   {payer: Lakeshore Community Health, product: "Lakeshore Community Health Managed Medicaid", pa: required}
product_aliases:    # exact-product strings only; anything else -> unresolved
  "summit crest medicare advantage": PLAN-SCMA
  "scma": PLAN-SCMA
  "harborstone health commercial ppo": PLAN-HSPPO
  "harborstone health commercial hmo": PLAN-HSHMO
  "lakeshore community health managed medicaid": PLAN-LCH
  "lch managed medicaid": PLAN-LCH
```
`test_policy_dates.py` checks that the computed window starts equal 2026-09-01, 2026-09-17, 2026-07-03 and 2026-04-04.

### 7.2 Rules
All rules see **only signed documents** (except where noted), only documents dated ≤ eval_date, and never `ecg_tracing_raw` or `other`.

**Clinical priority (PRI-1…4).** Evaluated independently of every admin rule.
- Candidate docs: signed `referral` or `clinician_note` that address symptom timing, severity or a review request. Ignore payer, coverage, ECG tracings, labs and records limited to test history or admin data.
- Select the newest-dated candidate(s).
- **PRI-4** if: no candidate exists; OR candidates share the newest date and their tiers or trajectories disagree; OR the newest candidate contradicts an older one on timing/severity **without documenting a material change** (see assumptions A3).
- Otherwise, using the newest candidate: **PRI-1** if `urgent_review_requested` with an `urgent_reason`. **PRI-2** if `expedited_review_requested` OR `material_increase_date` falls in [09-17, 10-01]. Else **PRI-3**.
- A newer note that documents a material change overrides older referral timing (P05).
- Output: tier, rule ID, source doc, file and page.

**ID-1 Identity.**
- Name + DOB compared across referral/order, registration, clinical notes and insurance evidence. MRN compared where present on more than one document. packet/referral ID compared likewise. member_id compared across registration, card and benefits. **Never compare across identifier types.**
- Name normalization: case, whitespace and punctuation are ignored, and a missing middle initial is not a conflict. Any other difference, nicknames included, is a **conflict** (A5).
- **Conflicting** if any same-type identifier disagrees. Output every value with its source page. **Pending** if registration is missing, or if name/DOB appear in fewer than two source types. Else **verified**.
- An `IDENTITY_RESOLUTION` event can only set the value a human confirmed. The original conflicting evidence remains visible.

**Plan product resolution.** Collect `plan_product_raw` from registration, card and benefits, normalize and map through the alias table. **Resolved** only if every present value maps to the same plan ID. Otherwise `unknown`. PA requirement comes from the plan matrix, or **unknown** if unresolved. Never infer a product from a member-ID prefix.

**COV-1 Coverage.**
- Pick benefits responses with response_date in [09-01, 10-01]. None in window but an older one exists → **pending** (stale). None at all → **pending** (card/face sheet only).
- For the newest in-window response: member_id ≠ registration/card member_id, or product maps to a different plan → **conflicting**. Product unmapped or unspecified → **pending** (A6). `coverage_active_on_eval_date == false` or `product_end_date < 10-01` → **inactive**. Active, matching member and exact product → **verified**.
- If two in-window responses disagree → **conflicting**.

**DOC-1.** Requires a signed `stress_echo_order` naming procedure **and** indication, **plus** a signed, dated clinician assessment with symptom course and relevant history. A consult referral never counts as the order. Result: **S** or **I** (never NM).

**QUAL-1.** **S** if the assessment has complaint_type exertional chest discomfort/dyspnea **and** a signed order whose indication matches that complaint, OR ischemia_concern == explicit_yes. **NM** if the assessment is complete, complaint_type == nonexertional and ischemia_concern ≠ explicit_yes. Else **I**.

**QUAL-2.** Evaluate each branch as `supported | failed | unknown`.
- **A (resting ECG):** a clinician-interpreted report with test_date ≥ 07-03 and interpretation in {abnormal, nondiagnostic, nonspecific_st_t} → supported. An in-window clinician-interpreted report that is normal, with none qualifying → failed. Otherwise (out-of-window report only, mention-without-report, or nothing) → unknown.
- **B (exercise ECG):** a complete clinician-interpreted report with test_date ≥ 04-04 and interpretation == equivocal → supported. An in-window report that is normal/negative/positive, or signed history stating none since a date ≤ 04-04 → failed. Else unknown.
- Overall: either branch supported → **S**. Both failed → **NM**. Else **I**.
- Never let a failed branch imply the other branch has failed.

**REPEAT-1.** Assessment `prior_stress_echo.stated == false` → **I**. Stated and none (or date < 04-04) → **S**. Date ≥ 04-04: interval_change_after_prior == true **and** repeat_rationale present → **S**. interval_change == false → **NM**. Else **I**.

**HMO-1 (PLAN-HSHMO only).** A complete clinician-interpreted resting ECG report dated ≥ 07-03 is present (any interpretation) → **S**. Otherwise → **I** (A4). This is checked regardless of QUAL-2.

**Common evidence test.** For PA-required plans: DOC-1 ∧ QUAL-1 ∧ QUAL-2 ∧ REPEAT-1 (∧ HMO-1 for HSHMO). Any NM → **NM**. Else any I → **I**. Else **S**. For PPO: compute and display it for visibility (see gate G-EVID).

### 7.3 Gates
| Gate | Passes when | Blocks |
|---|---|---|
| G-ID | identity == verified | PA prep, scheduling |
| G-COV | coverage == verified | PA prep, scheduling |
| G-PAREQ | PA requirement known | PA prep, scheduling |
| G-EVID | PA plans: common test == S. No-PA plans: DOC-1 == S (configurable, A2) | PA prep, scheduling |
| G-CLIN | priority PRI-3 (and `routine_requires_clinical_clearance: false`), OR a `CLINICAL_REVIEW_DECISION cleared_for_scheduling` event | scheduling **only**, never PA prep or admin work |
| G-PA-APPROVED | PA not required, OR a `PAYER_PA_DECISION approved` event | scheduling |

- **PA prep ready** = PA required ∧ G-ID ∧ G-COV ∧ G-PAREQ ∧ G-EVID.
- **Scheduling handoff sendable** = all six gates pass.
- **Admin readiness reached** (for the metric) = G-ID ∧ G-COV ∧ G-EVID ∧ G-PA-APPROVED. G-CLIN is excluded, because clinical clearance is separate.
- Show **all** failing gates at once. Clinical escalation actions are generated whatever the admin gates say.

### 7.4 Assumption flags (`workflow.yaml`), to be named during the demo
```yaml
routine_requires_clinical_clearance: false   # A1: ask Intake/Clinical
no_pa_evidence_gate: [DOC-1]                 # A2: QUAL/REPEAT shown as advisory for PPO
```
Plus interpretation calls recorded in `docs/assumptions.md`:
- **A3:** a newer note that contradicts older timing without documenting a change → PRI-4, not an override.
- **A4:** HMO-1 with only an out-of-window report → *insufficient* (the action is the same either way: request a current report).
- **A5:** name differences other than case/punctuation/middle initial → conflict for human review.
- **A6:** benefits response with an unspecified product → coverage *pending*, not conflicting.
- **A7:** business days = Mon–Fri, no holiday calendar.
- **A8:** a benefits response dated after the evaluation date is ignored.

### 7.5 Reason codes → next action → owner (with Tennr product)
| Reason code | Next action | Owner role | Tennr product |
|---|---|---|---|
| CLIN_URGENT | Notify cardiology clinical triage owner now. Hold scheduling until reviewed | Clinical Triage RN | Intake Operations routing |
| CLIN_EXPEDITED | Place in clinical review queue, expedited | Clinical Review RN | Intake Operations routing |
| CLIN_UNDETERMINED | Ask triage owner to clarify timing/severity (show conflicting sources) | Clinical Triage RN | Intake Operations routing |
| ID_CONFLICT | Verify in Epic; both values and pages shown | Intake Coordinator | Intake Operations |
| ID_PENDING | Obtain missing identity evidence | Intake Coordinator | Intake Operations |
| COV_PENDING_NONE / _STALE | Run E&B | Intake Coordinator | Insurance Benefits Manager |
| COV_PENDING_PRODUCT / PA_UNKNOWN | Confirm exact plan product | Intake Coordinator | Insurance Benefits Manager |
| COV_CONFLICT | Reconcile member/product mismatch | Intake Coordinator | Insurance Benefits Manager |
| COV_INACTIVE | Obtain current coverage from patient | Intake Coordinator | Communications Coordinator |
| EVID_MISSING_ORDER | Request signed stress echo order from ordering clinician | Intake Coordinator | Communications Coordinator (fax) |
| EVID_MISSING_REPORT / EVID_HMO1 | Request specific report (named in reason) | Intake Coordinator | Communications Coordinator (fax) |
| EVID_NOT_MET | Do **not** prepare PA. Review with ordering clinician (not a payer denial) | Auth Specialist | Qualification Review Engine |
| PA_READY | Prepare PA submission packet | Auth Specialist | Prior Authorization Manager |
| PA_AWAITING | Track payer decision | Auth Specialist | Prior Authorization Manager |
| SCHED_READY | Send scheduling handoff | Scheduling (Epic) | handoff |

---

## 8. App: screens and handoffs

### 8.1 Queue (`/`)
- Columns: priority badge · patient · packet · exact plan · procedure · PA req · identity · coverage · evidence · holds (count + chips) · next action · owner · age (business days since receipt).
- Owner filter tabs: All · Clinical Triage · Clinical Review · Intake · Auth · Ready for scheduling.
- Header: policy version, evaluation date, "Re-run all 10 through engine" button (re-evaluates and writes audit entries), and a reset-demo button.

### 8.2 Case detail (`/case/{key}`)
- Four **separate** status cards: Clinical priority (with source page), Identity, Coverage, PA requirement.
- Criteria table: rule ID · status · reason · evidence links (`P05_packet.pdf p.7` opens the page viewer with the quote highlighted).
- Gates panel: each gate pass/blocked, with the reason.
- Next actions: ordered list, owner role, Tennr product tag.
- Documents list: type, date, signed/unsigned, pages, with "not used" labels for unsigned docs and raw tracings.
- Simulate panel (amber, labelled **SIMULATED – not from source PDFs**): role picker + decision + note → event.
- Handoff panel: "Send to scheduling" (always clickable; if blocked, it records a `handoff_blocked` audit entry and shows the failing gates), "Prepare PA packet", "Send missing-info request", "Notify triage".
- Audit trail, newest first.

### 8.3 Page viewer
Page PNG (rendered at ingest) with the cited quote shown beside it, plus file + page header.

### 8.4 Handoff stubs (`handoffs.py`), written to the `outbox` table and `outbox/*.json`
| Work item | Receiving system (stub) | Gates checked |
|---|---|---|
| `stress_echo_ready_to_schedule` | Epic Cadence / scheduling WQ (mock) | all six |
| `pa_request_packet` (attachments listed by file/page) | Payer PA portal (mock) | PA prep gates |
| `identity_verification_task` (both values + sources) | Epic intake WQ (mock) | — (exception) |
| `urgent_triage_notification` | Epic In Basket (mock) | — (clinical, never gated by admin) |
| `missing_info_request` (named document) | Fax/Comms (mock) | — |

Example payload:
```json
{"handoff_id":"HO-P01-SCHED-1","idempotency_key":"NP-PKT-0001:schedule:<gate-snapshot-hash>",
 "receiving_system":"Epic scheduling (MOCK STUB)","work_item":{"type":"stress_echo_ready_to_schedule",
 "packet_id":"NP-PKT-0001","mrn":"NP-100231","procedure":"Outpatient stress echocardiogram",
 "clinical_priority":"routine","plan":"PLAN-SCMA"},
 "gates_checked":[{"gate":"G-ID","status":"pass","basis":"ID-1 verified"}, "..."],
 "evidence_refs":[{"rule":"QUAL-2","file":"P01_packet.pdf","page":7}],
 "simulated_dependencies":[{"event_id":"EV-0003","type":"PAYER_PA_DECISION","simulated":true}],
 "policy_version":"SC-CARD-1.1","eval_date":"2026-10-01","status":"recorded"}
```
The same idempotency key is never sent twice (resending shows "already recorded").

### 8.5 Metrics panel (`/metrics`), labelled "10-case mock illustration, not a forecast"
Counts by state, holds by reason code, workload by owner, aging buckets, admin-readiness time computed from receipt → readiness event (business days), and the number of orders that reached scheduling with an open gate (must be 0). This shows how the pilot KPIs would be instrumented, not what they would be.

---

## 9. Verification (how we prove the behavior)
1. **Test-first golden matrix.** Write `test_golden_ten.py` from 6.3 **before** the rules. The engine must reproduce every cell. A mismatch is logged in `build_log.md` as an extraction, rule-reading or code problem.
2. **Boundary tests** for every window: the on-boundary date passes and the day before fails (09-01, 09-17, 07-03, 04-04). Include a material increase on exactly 09-17 → PRI-2.
3. **Invariant tests (`test_invariants.py`):**
   - no scheduling handoff is recorded with any failing gate;
   - no PA shows as approved without a `PAYER_PA_DECISION` event; the engine never writes events;
   - extraction files contain no derived keys;
   - priority is unchanged when payer, coverage or raw tracings are changed (mutation test);
   - unsigned docs change no rule result (mutation test: delete P07 p.8 → identical result);
   - a no-PA plan with coverage pending is never sendable;
   - every non-S rule result has a reason code, and every S result has ≥1 evidence ref;
   - the engine is deterministic (evaluate twice gives an identical state hash).
4. **Single-fact flip tests:** change one fact and assert that only the expected results change (e.g. P05 resting ECG date → 07-10 makes HMO-1 S and the case PA-ready).
5. **Manual walkthrough** of the demo script against the running app before the session. Record a screenshot of each key state.
6. `make test` must pass before `make demo`.

**Likely agent mistakes to check for (record any that really happen in `build_log.md`):** comparing MRN to member ID; marking QUAL-2 NM when one branch is merely missing; letting PPO "no PA" mean ready; using an unsigned or raw-tracing doc; window off-by-one; letting an exercise ECG satisfy HMO-1; inferring product from member ID prefix; treating the consult referral as the order; letting clinical holds block PA prep; reading expected outputs from data.

---

## 10. Build phases for Claude Code (checkpoint after each)
| Phase | Deliverable | Done when |
|---|---|---|
| 0 | Scaffold, Makefile, configs, pydantic models | `make setup` works; models validate the sample in 5.1 |
| 1 | 10 extraction JSONs (6.2) + `make_mock_packets.py` + `ingest.py` + `check_extractions.py` | 10 PDFs render; every quote is found on its page |
| 2 | `test_policy_dates`, `test_golden_ten`, boundary tests written (failing) | Tests exist and fail for the right reason |
| 3 | Rules engine, gates, actions | All rule, golden and boundary tests pass |
| 4 | Store, sim events, audit, handoffs + invariants | Invariant tests pass; scripted transitions (6.3) pass as tests |
| 5 | UI: queue, detail, page viewer, outbox | Manual walkthrough of P01, P03, P04, P05, P08 works |
| 6 | Metrics panel, reset/seed, docs (`assumptions.md`, `demo_script.md`) | `make demo` from clean in < 1 min |
| 7 | *(when real PDFs arrive)* Section 6.4 | New hand-derived golden table passes; corrections disclosed |
| Stretch | Simulated downstream failure → retry/dead-letter; per-site field for site variance | Only after 0–6 are solid |

Keep the UI plain (server-rendered, one CSS file). Reliability beats polish.

---

## 11. Demo plan (75 min session)

**5–15 Opening (slides):** hypothesis, then discovery questions from 1.7. Note which assumptions the room confirms or breaks; flags A1/A2 can be flipped live.

**15–50 Demo with discovery** (cases in this order; each answers a stakeholder):
1. **Queue overview (2 min):** all ten through one engine (press re-run), 1 sendable / 1 PA-ready / 8 held, each with an owner. "Nothing quietly passes." *(Ops)*
2. **P03 urgent (5 min):** triage is notified immediately while coverage is stale and the ECG report is missing. Show the simultaneous holds and the clinical vs admin separation. Record the simulated `reviewed_hold`. *(Ops, Clinical)*
3. **P04 identity (5 min):** two DOBs and two member IDs with page links, never auto-selected. Intake resolves through a simulated event, then a simulated E&B rerun. Epic stays the authority. *(IT, Intake)*
4. **P05 HMO-1 (5 min):** everything looks supported, but the plan rule catches a stale resting ECG. The Comms Coordinator request goes out *before* booking, which is the late-disruption lever. *(Ops, Intake)*
5. **P01 happy path (5 min):** try to send to scheduling → **blocked (G-PA-APPROVED)** and audited → prepare PA packet → simulated payer approval (labelled) → handoff sent → show the outbox payload and gates. *(IT)*
6. **Optional:** P08 (admin-ready ≠ clinically cleared), P07/P10 (not met ≠ denial; not met vs insufficient), P09 (consult ≠ order). Be ready to open any case the panel picks.
7. **Pilot (3 min):** 1–2 sites, stress echo only, 8–12 weeks. Baseline from the prior 3 months. Primary: disruption rate by cause and readiness time/aging. Secondary: handling-time sample and touches. Guardrails: zero gate leaks and time-to-triage. Compare against non-pilot sites, given site variance.

**50–65 Build debrief talking points:**
- **How I directed the agent:** CLAUDE.md invariants, a hand-derived golden table first, phased checkpoints, rules as pure functions.
- **A mistake found and corrected:** take it from `build_log.md` (real, not scripted).
- **Verification:** golden, boundary, invariant and mutation tests, plus the manual walkthrough.
- **What's simulated:** extraction (prep-time), payer/clinician decisions, E&B responses, every downstream system.
- **Production needs:**
  - Inbound: fax server and Epic WQ ingestion.
  - Matching: Epic/EMPI patient matching (ADT).
  - Benefits: real-time 270/271 via clearinghouse.
  - PA: payer PA channels (portal, 278, or FHIR PAS where the payer supports it).
  - Extraction: model extraction with confidence thresholds and a human-review queue.
  - Policy: versioning with rev-cycle and clinical sign-off.
  - Security: BAA/HIPAA controls, SSO/RBAC and audit retention.
  - Reliability: idempotent handoffs with retry and dead-letter queues, plus interface monitoring with a named support owner on each side.
