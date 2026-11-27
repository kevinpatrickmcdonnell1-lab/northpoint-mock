# Demo script (75-minute session, PLAN §11)

**Dataset:** `make demo` serves the **real** packets (R01–R10). This walkthrough is written for them. The mock-set walkthrough is kept in the [Appendix](#appendix-mock-set-walkthrough).

**Every number, ID and status below comes from running these steps.** `tests/test_demo_script_real.py` runs the same sequence, in this order, through the service layer and asserts each stated outcome, and also checks that this file states them. If you change a step, change the test too.

**Before the session:** `make test` (all green), then `make demo` (or `make demo PORT=8010` if 8000 is busy). Open `http://localhost:8000`. Press **Reset demo** right before you start sharing. The event and handoff IDs below (EV-0001…, HO-…) assume a fresh Reset and these steps in this order.

Everything shown is **SYNTHETIC – MOCK DATA**. Extraction was done at prep time; payer/clinician decisions, E&B responses and every downstream system are simulated.

---

## 0. Opening slides (5–15 min)
Hypothesis → discovery questions (PLAN §1.7). Note which assumptions the room confirms or breaks. Flags **A1** and **A2** are shown on the queue header and live in `config/workflow.yaml`. **Don't flip them live** (see [the note on flags](#a-note-on-flipping-a1a2)).

## 1. Queue overview (2 min) · *Ops*
- Press **Re-run all 10 through engine**. "One engine, ten packets, no patient-specific code. Every evaluation is audited, with the policy version and a SHA-256 of the config files."
- Baseline: **0 sendable, 2 ready for PA prep (R02, R07), 8 held**. Every case has a next action and an owner. "Nothing quietly passes."
- Owner tabs: **Clinical Triage** shows R07, R08 (urgent). **Clinical Review** shows R03, R05, R06, R10 (expedited). **Ready for scheduling** is empty.
- Say it: this split is a property of these ten packets, not Northpoint's exception mix.

## 2. R07 urgent *and* PA-ready (4 min) · *Ops, Clinical*
- Priority card: **PRI-1 Urgent review**, sourced to the referral on p.2 ("Urgent clinical review requested"). Click the link: page image, quote highlighted.
- At the same time: identity, coverage and evidence all pass, so **PA prep is ready**. Gates: only **G-CLIN** and **G-PA-APPROVED** block, and both block scheduling only.
- **Notify triage** → `HO-R07-TRIAGE-1` to the Epic In Basket stub, never gated by admin.
- **Prepare PA packet** → `HO-R07-PA-1`. "Clinical holds never block PA prep. Auth starts today, not after triage."

## 3. R01 identity conflict (5 min) · *IT, Intake*
- Identity card: DOB **1958-06-12 (p.2, 4, 5) vs 1958-06-21 (p.3, 9)**, each value linked to its page. "Never auto-selected."
- Holds at once: **G-ID, G-COV, G-EVID, G-PA-APPROVED**. Coverage is pending (no E&B response in the window), and DOC-1 is insufficient because the order "names the procedure but no indication". The consult-referral reason isn't the order's indication.
- **Identity verification task** → `HO-R01-ID-1` to the Epic intake WQ stub, with both values and pages.
- Optional: in **Simulate → Identity resolution**, try confirming a DOB that appears on no page. It's accepted (Epic is the authority), but the card shows a **"not found in packet"** warning.

## 4. R02 PA path (6 min) · *IT, Auth*
- **Send to scheduling** → **BLOCKED: G-PA-APPROVED**. Show the audit row.
- Try **Simulate payer decision → approved** first → *Not recorded: No PA request packet has been sent for R02…* "A payer can't decide on a request nobody sent."
- **Prepare PA packet** → `HO-R02-PA-1` (attachments listed by file and page).
- **Simulate payer decision → approved** (Auth Specialist) → `EV-0001`, linked to `HO-R02-PA-1`. The G-PA-APPROVED gate shows the SIMULATED badge, and the next action becomes **Send scheduling handoff**.
- **Send to scheduling** → **sent**, `HO-R02-SCHED-1`. Open it in the outbox: all six gates pass, plus evidence refs, `simulated_dependencies` and the idempotency key. Click again → **already recorded** (no second handoff).

## 5. R05 clearance, send, then automatic recall (6 min) · *Ops, Clinical, IT*
- R05 is a PPO (no PA) and admin-ready, but **PRI-2 Expedited**: the only hold is **G-CLIN**. "No PA removes only the PA step: identity, coverage, every evidence rule and clinical review still gate scheduling." **Send to scheduling** → **BLOCKED: G-CLIN**.
- **Simulate clinical decision → cleared_for_scheduling** (Clinical Review RN) → `EV-0002`; sendable.
- **Send to scheduling** → `HO-R05-SCHED-1`.
- Now the Cardiologist changes their mind: **Simulate clinical decision → not_cleared** (Cardiologist) → `EV-0003`. The flash reads "…scheduling handoff recalled automatically (HO-R05-RECALL-1)": the case lost readiness, and the system **automatically recorded `HO-R05-RECALL-1`** to the same scheduling stub. The payload names the original handoff `HO-R05-SCHED-1`, failing gate **G-CLIN** and reason **CLIN_NOT_CLEARED**.
- The case page shows a **RECALLED** banner and an audit row; the outbox row says "recalls HO-R05-SCHED-1". **Send to scheduling** again → **BLOCKED: G-CLIN**.
- "This is what 'scheduling sometimes gets not-ready orders' looks like when it's handled: an order that stops being ready is pulled back automatically, once, with the reason."

## 6. R06 HMO-1 (4 min) · *Ops, Intake*
- Plan resolves to **Harborstone Health Commercial HMO**. QUAL-2 is **supported via branch B** (the in-window exercise ECG); branch A is unknown.
- But **HMO-1 is insufficient**: the HMO rule needs a complete clinician-interpreted resting ECG dated ≥ 2026-07-03, the packet has none, and an exercise ECG never satisfies HMO-1.
- **Send missing-info request** → `HO-R06-INFO-1` names "current resting ECG report (dated ≥ 2026-07-03)". "This goes out *before* booking. That's the late-disruption lever."

## 7. R04 and R09: not met (4 min) · *Auth*
- **R04:** QUAL-1 **not met** (complete assessment documents a nonexertional complaint, no ischemia concern). QUAL-2 is **insufficient**, not not-met: branch A failed, branch B unknown. "One failed branch never implies the other."
- **R09:** REPEAT-1 **not met**: prior stress echo 2026-08-15 is inside the 180-day window and no interval change is documented.
- Both show **Do not prepare PA**. "Not met is not a payer denial", in those exact words on screen. **Prepare PA packet** → **BLOCKED: G-EVID**.

## 8. R10: inactive coverage (2 min) · *Intake*
- Coverage **inactive**: "Benefits response dated 2026-09-30: coverage not active on 2026-10-01 (ended 2026-09-15)." Link to p.7.
- Next action: **Obtain current coverage from patient** → Intake via Communications Coordinator. **Send missing-info request** → `HO-R10-INFO-1`.

## 9. Metrics (2 min)
Open `/metrics` ("10-case mock illustration, not a forecast"). After the steps above: **8 held, 1 handed to scheduling, 1 ready for PA prep** (R07). **0 gate leaks**, **1 scheduling recall**, **5 blocked attempts**, 1 urgent triage notification.

## 10. Pilot (3 min)
1–2 sites, stress echo only, 8–12 weeks, baseline from the prior 3 months. Primary: disruption rate by cause, readiness time and aging. Secondary: handling-time sample, touches. Guardrails: **0 gate leaks** (Metrics shows it), time-to-triage. Compare against non-pilot sites.

## A note on flipping A1/A2
A2 now follows the brief (R5): a no-PA plan skips only the PA step, so `no_pa_evidence_gate` defaults to DOC-1, QUAL-1, QUAL-2 and REPEAT-1. Both flags remain config switches to confirm in discovery. Don't flip A1 (`routine_requires_clinical_clearance`) or A2 (`no_pa_evidence_gate`) live. The policy is read when the app starts, so a flip takes a YAML edit plus **Reset**, and Reset clears every simulated event, handoff and audit row, which wipes the state you've built. Describe the flip instead. The effect is pinned by tests (`test_flag_A1_routine_requires_clearance`, `test_flag_A2_ppo_evidence_gate_configurable`). A typo in either flag now stops the app at startup with a clear message instead of failing open, and the case-page footer shows the config SHA-256, so any edit is visible.

## Build debrief (50–65 min)
- **How I directed the agent:** CLAUDE.md invariants, a hand-derived golden table written first (`tests/test_golden_ten.py`, `tests/golden_real.yaml`), phased checkpoints, rules as pure functions over facts + events + policy.
- **Real mistakes found and corrected:** `docs/build_log.md` #1 (a field named `date` silently shadowed the type and would have broken REPEAT-1), #5 (missing SIMULATED badge), and the seam-hardening fixes: a PA approval with no PA submission (Fix 2), no recall after readiness was lost (Fix 4), and config typos that failed open (Fix 5).
- **Verification:** golden, boundary, invariant, mutation and flip tests, a crawl of every case page and page link on both datasets, this script as a test, and the quote-on-page check.
- **Interpretation calls to name:** `docs/assumptions.md` B8 (what counts as a documented change), A3a (an older higher tier is unresolved, never silently downgraded), B10 (newest E&B supersedes).
- **What production needs:** PLAN §11 list. Fax/Epic WQ ingestion, EMPI matching, 270/271, PA channels (portal/278/FHIR PAS), model extraction with confidence thresholds and a review queue, policy versioning with sign-off, BAA/HIPAA/SSO/RBAC/audit retention, retries, dead-letter queues and interface monitoring with named owners.

---

## Appendix: mock set walkthrough

Cases P01–P10. Run `NORTHPOINT_DATASET=mock make demo`. The mock set is the regression suite; its baseline is **1 sendable (P02), 1 ready for PA prep (P01), 8 held**.

- **P03 urgent:** PRI-1 from the 09-29 note on p.7. Coverage pending (stale E&B 08-15) and QUAL-2 insufficient (resting ECG mentioned, report not attached). Four simultaneous holds; G-CLIN blocks scheduling only. **Notify triage**, then Simulate `reviewed_hold` (Clinical Triage RN): the admin holds remain.
- **P04 identity:** DOB 1951-03-14 (p.1, 2, 6, 7) vs 1951-04-13 (p.3); member ID SCM-302177 (p.3, 4) vs SCM-302171 (p.5). Identity resolution needs a note and an Epic reference. Confirm DOB 1951-03-14 and member SCM-302177 → identity verified, coverage still conflicting. Simulate E&B (2026-10-01, Summit Crest Medicare Advantage, SCM-302177, active) → coverage verified → PA prep ready. (An E&B dated after 2026-10-01 is refused by the form, A8.)
- **P05 HMO-1:** QUAL-2 supported via branch B (equivocal exercise ECG 06-20), but HMO-1 insufficient (resting ECG 05-02 is older than 07-03). PRI-2: the newer 09-26 note documents a material increase since 09-22, overriding the 09-08 referral.
- **P01 happy path:** Send to scheduling → BLOCKED G-PA-APPROVED → Prepare PA packet → Simulate `approved` → Send → sent → Send again → already recorded.
- **P08:** admin-ready but PRI-4 (same-day referral "worsening" vs cardiology note "stable"). Simulate `cleared_for_scheduling` → sendable; priority stays PRI-4.
- **P07 / P10:** not met (REPEAT-1; QUAL-1 + QUAL-2) vs P03's insufficient. P07's unsigned 09-30 draft and P10's raw tracing are shown as not used.
- **P09:** consult referral ≠ order (DOC-1 insufficient); "Harborstone Health Commercial" stays unresolved (never inferred from the HH- prefix).
- **Stretch (P02):** Outbox → Simulate outage on the scheduling stub → Send P02 → retrying → dead letter → Restore → Requeue → delivered with the same handoff ID and key. Queue → Site: Riverside (P04, P06, P08) and Metrics → By ordering site.
