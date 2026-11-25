# Demo script (75-minute session, PLAN §11)

**Dataset:** `make demo` now serves the **real** packets (R01–R10). The case walkthrough below uses the mock set (P01–P10); run `NORTHPOINT_DATASET=mock make demo` for it, or use the real-set equivalents: R01 identity and missing indication (≈P04), R07 urgent and PA-ready, R06 HMO-1 (≈P05), R02 PA path (≈P01), R05 admin-ready but clinically held (≈P08), R04/R09 not met (≈P10/P07), R10 inactive (≈P06).

**Before the session:** `make test` (all green), then `make demo` (or `make demo PORT=8010` if 8000 is busy). Open `http://localhost:8000`. Press **Reset demo** right before you start sharing.

Everything shown is **SYNTHETIC – MOCK DATA**. Extraction was done at prep time; payer/clinician decisions, E&B responses and every downstream system are simulated.

---

## 0. Opening slides (5–15 min)
Hypothesis → discovery questions (PLAN §1.7). Note which assumptions the room confirms or breaks. Flags **A1** and **A2** are on the queue header and in `config/workflow.yaml`.

## 1. Queue overview (2 min) · *Ops*
- Press **Re-run all 10 through engine**. "One engine, ten packets, no patient-specific code. Every evaluation is audited."
- Point at the result: **1 sendable (P02), 1 ready for PA prep (P01), 8 held**, each with a next action and an owner. "Nothing quietly passes."
- Click the owner tabs: Clinical Triage (P03, P08), Intake, Auth, Ready for scheduling (P02 only).
- Say it: this split is a property of the mock set, not Northpoint's exception mix.

## 2. P03 urgent (5 min) · *Ops, Clinical*
- Priority card: **PRI-1 Urgent**, sourced to the 09-29 note on p.7. Click the link: page image, quote highlighted.
- At the same time: coverage **pending (stale E&B 08-15)** and QUAL-2 **insufficient** (resting ECG mentioned, report not attached).
- Gates panel: four simultaneous holds. G-CLIN blocks scheduling only.
- **Notify triage** → sent to the Epic In Basket stub, never gated by admin.
- Simulate → Clinical review decision `reviewed_hold` (Clinical Triage RN). Next action becomes "Triage reviewed and placed a hold"; the admin holds remain. No treatment decision is recorded.

## 3. P04 identity (5 min) · *IT, Intake*
- Identity card: DOB **1951-03-14 (p.1, 2, 6, 7) vs 1951-04-13 (p.3)**. Member ID **SCM-302177 (p.3, 4) vs SCM-302171 (p.5)**. "Never auto-selected."
- **Identity verification task** → Epic intake WQ stub, with both values and pages.
- Simulate identity resolution: try submitting without a note → *Not recorded* (note + Epic reference required). Then DOB `1951-03-14`, member `SCM-302177`, Epic ref `EPIC-REG-77`, note.
- Identity → verified, and the original conflicting values stay visible, marked SIMULATED. Coverage **still conflicting**, because the E&B on file names SCM-302171.
- Simulate E&B response: 2026-10-01, Summit Crest Medicare Advantage, SCM-302177, active → coverage verified → **Prepare PA submission packet**. "Epic stays the authority; we route the work."

## 4. P05 HMO-1 (5 min) · *Ops, Intake*
- QUAL-2 **supported via branch B** (equivocal exercise ECG 06-20). Everything looks fine, but **HMO-1 is insufficient**: the only resting ECG is 05-02, older than 07-03, and an exercise ECG never satisfies HMO-1.
- Priority **PRI-2 Expedited**: the newer 09-26 note documents a material increase since 09-22, which overrides the older 09-08 referral.
- **Send missing-info request** → Fax/Comms stub names "current resting ECG report (dated ≥ 2026-07-03)". "This goes out *before* booking. That's the late-disruption lever."

## 5. P01 happy path (5 min) · *IT*
- **Send to scheduling** → **BLOCKED: G-PA-APPROVED**. Show the audit trail entry.
- **Prepare PA packet** → outbox; attachments listed by file and page.
- Simulate payer decision `approved` (Auth Specialist). The gate shows the SIMULATED badge.
- **Send to scheduling** → sent. Open the outbox item: gates checked (all six pass), evidence refs, `simulated_dependencies`, idempotency key. Click again → "already recorded".

## 6. Optional cases
- **P08:** admin-ready but **PRI-4**. Same-day referral (worsening) vs cardiology note (stable). Simulate `cleared_for_scheduling` → sendable. Priority stays PRI-4; the clearance is a separate event.
- **P07:** REPEAT-1 **not met** (stress echo 06-11, no interval change). "Not met is not a payer denial." The unsigned 09-30 draft is listed as "unsigned, not used as evidence".
- **P10:** QUAL-1 and QUAL-2 **not met** (both branches failed). The raw tracing saying "ABNORMAL ECG" is shown as "present, not used". Contrast with P03 (insufficient, not not-met).
- **P09:** consult referral ≠ order (DOC-1 insufficient); product "Harborstone Health Commercial" is unresolved, so PA is unknown. Never inferred from the HH- member prefix.
- **Live flag flip (A1):** set `routine_requires_clinical_clearance: true` in `config/workflow.yaml`, restart → P02 is no longer sendable (G-CLIN). Flip it back.

## 6b. Stretch (optional, 3 min) · *IT*
- **Outbox → Simulate outage** on "Epic Cadence / scheduling WQ". Send P02 to scheduling: recorded once, delivery **retrying**. Retry twice → **dead letter** (audited, integration owner). **Restore**, then **Requeue dead letter** → delivered, with the same handoff ID and idempotency key. "Retries never re-gate or duplicate; a failed interface at 7 a.m. has a named owner."
- **Queue → Site: Riverside** (P04, P06, P08) and **Metrics → By ordering site**. "Site never changes a decision; it shows where holds concentrate." (§1.6.4)

## 7. Pilot (3 min)
1–2 sites, stress echo only, 8–12 weeks, baseline from the prior 3 months. Primary: disruption rate by cause, readiness time and aging. Secondary: handling-time sample, touches. Guardrails: **0 gate leaks** (the Metrics page shows it), time-to-triage. Compare against non-pilot sites. Open `/metrics` ("10-case mock illustration, not a forecast").

## 8. Build debrief (50–65 min)
- **How I directed the agent:** CLAUDE.md invariants, a hand-derived golden table written first (`tests/test_golden_ten.py`), phased checkpoints, rules as pure functions over facts + events + policy.
- **A real mistake found and corrected:** `docs/build_log.md` #1, a field named `date` silently shadowed the type and would have broken REPEAT-1. Also #5 (missing SIMULATED badge, caught by the UI walkthrough test).
- **Verification:** golden, boundary, invariant, mutation and flip tests (194 total), plus the HTTP walkthrough and the quote-on-page check.
- **Interpretation calls to name:** `docs/assumptions.md` B8 (what counts as a documented change), B10 (newest E&B supersedes), B12 (P04 also routes COV_CONFLICT).
- **What production needs:** PLAN §11 list. Fax/Epic WQ ingestion, EMPI matching, 270/271, PA channels (portal/278/FHIR PAS), model extraction with confidence thresholds and a review queue, policy versioning with sign-off, BAA/HIPAA/SSO/RBAC/audit retention, retries, dead-letter queues and interface monitoring with named owners.
