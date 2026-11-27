# Build log

Agent mistakes found and fixed, plus environment deviations. Each entry says what happened, how it was caught and how the fix was verified. Real entries only; nothing here is scripted.

## 2026-11-24 · Phase 0 (scaffold, configs, models)

### 1. `date` field shadowed the `date` type (code bug)
- **What:** `Document.date: Optional[date]` and `PriorStressEcho.date: Optional[date]`. Inside a class body, the field name `date` rebinds the name, so pydantic resolved the annotation to `Optional[None]` and rejected every real date.
- **Caught by:** `test_sample_validates_and_types_fields` (PLAN §5.1 sample) failed with `documents.0.date: Input should be None`.
- **Fix:** module alias `Date = date`, used for both fields.
- **Verified:** the sample test passes. Regression test `test_date_named_fields_accept_dates` covers `prior_stress_echo.date`, which the §5.1 sample does not exercise. That bug would otherwise have surfaced later as a confusing REPEAT-1 failure on P02/P07.

### 2. Dependency deviation: pdfplumber dropped (environment)
- **What:** `uv sync` failed. pdfplumber → pdfminer.six → `cryptography` 50.x has no prebuilt wheel for this machine (macOS 12, x86_64) and tried to compile Rust from source.
- **Decision:** remove pdfplumber. pypdf handles page text.
- **Verified:** `make setup` completes; `make test` passes.

### 3. Dependency deviation: pypdfium2 instead of pdftoppm (environment)
- **What:** poppler (`pdftoppm`) is not installed and Homebrew isn't available. Page PNGs are rendered with pypdfium2 (pip wheel, no system dependency). Same output: one PNG per page.

## 2026-11-24 · Phase 1 (extractions, mock PDFs, ingest, check)

### 4. pypdfium2 rebuilt from source on every `uv run` (environment)
- **What:** the latest pypdfium2 (5.15.2) has no wheel for this platform. Every `uv run` printed "Building pypdfium2", adding seconds to each script and to `make demo`.
- **Caught by:** watching the `make mock/ingest/check` output.
- **Fix:** pinned `pypdfium2==4.30.0`, which ships a compatible wheel.
- **Verified:** `uv run` starts in ~1s; PNGs still render (P07 p.8 inspected visually: unsigned-draft label and synthetic footer present).

## 2026-11-24 · Phases 2–3 (tests first, then engine)

- `test_golden_ten.py`, `test_rules.py` (boundaries, single-fact flips, traps) and `test_policy_dates.py` were written before any rule code. They failed with `ModuleNotFoundError: app.engine`, the right reason.
- The engine reproduced every §6.3 cell on its first run, so there was no extraction, rule-reading or code mismatch to report. Because a first-run pass can also mean the tests are too loose, I:
  - tightened `test_raw_tracing_ignored` from "equal except criteria" to **full state equality** (it still passes);
  - printed every case's statuses, reason texts and actions and read them against §6.2/§6.3.
- **Reason-text fixes found by that read-through** (not caught by tests, since the statuses were right):
  - P04's ID_CONFLICT text repeated a value for every page. It now groups pages per distinct value ("1951-03-14 (p.1, 2, 6, 7) vs 1951-04-13 (p.3)").
  - P01's priority text said only "consistent signed sources". It now states that the 09-10 increase falls outside the 14-day window [09-17, 10-01], which is the story P01 exists to show.
- Interpretation calls B7–B13 (see `assumptions.md`) were decided while writing the rules, not adjusted afterwards.

## 2026-11-24 · Phases 4–6 (store, handoffs, UI, demo)

### 5. Simulated-event badge missing on gates (UI bug)
- **What:** after a simulated payer approval, the G-PA-APPROVED gate showed a short "SIMULATED" chip. The spec requires "SIMULATED – not from PDF" next to anything derived from an event.
- **Caught by:** `tests/test_app.py::test_p01_walkthrough`.
- **Fix:** the gate badge now uses the full label.
- **Verified:** the test passes.

### 6. Port 8000 already in use (environment)
- **What:** another local process (likely the bookly-agent app) was listening on :8000, so the first live walkthrough hit the wrong server.
- **Caught by:** the uvicorn log (`[Errno 48] address already in use`).
- **Fix:** `make demo PORT=8010` (the Makefile port defaults to 8000). The other process was left running.

### 7. Two wrong expectations of mine during the live walkthrough (no code change)
- `curl -X POST -L` re-POSTed to the redirect target and got a 405, so the flash messages looked missing. Browsers switch to GET on a 303. The redirect URL carried the correct "BLOCKED … G-PA-APPROVED" message.
- Median admin readiness showed 7, where I expected 8. The engine was right: P08 is admin-ready because clinical clearance is excluded (§7.3), so the median is over {9, 7, 4}.

## 2026-11-24/25 · Phase 7 readiness + Stretch

### 8. Draft extractor misfiled two clinician notes as ECG reports (code bug)
- **What:** `extract_draft.py` guessed doc type by keyword over the whole page. P03's note says "Resting ECG reviewed in clinic", and P10's says "No exercise ECG in the past 12 months", so both were drafted as ECG reports. This is the same mention-without-report trap the QUAL-2 rule guards against.
- **Caught by:** `test_extract_draft_on_all_mock_packets_types` (70/72 types correct).
- **Fix:** classify from the page heading (short all-caps lines) first, and fall back to body text only if no heading matches.
- **Verified:** 72/72 on the mock set. Real pages will differ, which is why drafts must be reviewed by a human and can never be loaded.

### 9. Append-only test passed vacuously (test bug)
- **What:** `UPDATE deliveries …` on an empty table touches zero rows, so the trigger never fired and the test reported "DID NOT RAISE".
- **Fix:** the test now creates a delivery row first. The trigger was correct all along.

### 10. Two full test runs exceeded the 180 s tool timeout (slow start, not a hang)
- Two `uv run pytest` runs went past 180 s of wall-clock time. The second one finished on its own: "217 passed, 2 skipped … in 36.07s". So the extra time was spent before pytest started (`uv run` environment startup on this machine), not in a test. Other runs take 5–9 s. Recorded so a slow `make test` right before the demo isn't mistaken for a failure.

### Layout change (PLAN §6.4.4, done ahead of the real PDFs)
- The mock set moved to `data/packets/mock/` and `data/extractions/mock/` and is the permanent regression suite. The engine and UI select the real set automatically once `data/extractions/*.json` exists.

## 2026-11-25 · Phase 7: real packets (PLAN §6.4)

- **Order of work, as §6.4.3 requires:** read all ten real packets, wrote `tests/golden_real.yaml` by hand (with the reasoning per row), and only then wrote the extractions and ran the engine.
- **Result:** the engine reproduced all ten hand-derived rows on the first run (statuses + ordered actions). A read-through of every reason text against the pages found one cosmetic issue (R01's conflict text listed "p.2, 2" because the referral and order share a page), now fixed.
- **The real set is very different from the mock set:** 0 sendable, 2 PA-prep ready (R02, R07), 8 held. R07 is PRI-1 *and* PA-prep ready, which shows that clinical holds never block PA prep.

### 11. Model gaps the real pages exposed (rule-reading/extraction, not code bugs)
Found while extracting, before the engine ran. Each change keeps the mock regression suite green (136 → 228 tests total).
- `received_channel` had no value for "not stated". Only R01 says fax, so `not_stated` was added.
- QUAL-1 matched the order's indication by keyword ("exertional"). R02's order says "chest pressure with uphill walking", which is exertional without the word. Orders now carry `indication_type`, classified at extraction like a note's `complaint_type`. The text match is kept only as a fallback.
- REPEAT-1 read only the assessment. In 8 packets the prior-stress-echo history comes in a separate signed 10-01 addendum ("no stress echocardiogram … 2026-04-04 through 2026-10-01"). It now uses the assessment's statement, else the newest signed clinician addendum that states it.
- DOC-1 gave the generic "no signed order" message when an order exists but names no indication (R01). It now asks for a corrected order naming the indication.
- `extraction_method: llm_draft_unverified` was added, so drafted facts are labelled honestly until a person verifies them.

### 12. Real dataset activated before its PDFs existed (code bug)
- **What:** the dataset switch turned on as soon as real extraction JSONs existed. The PDFs were supplied in chat, not on disk, so the app would have switched to the real set with a broken page viewer, and `make check` failed with 252 "not ingested" errors.
- **Caught by:** `test_dataset_switch` failing, plus the check output.
- **Fix:** the real set activates only when every cited source PDF is in `data/packets/`. Until then `make check` warns and lists the missing files.
- **Verified:** active dataset = mock; `make check` passes with the warning; 228 tests pass.

### 13. Real PDFs ingested and quote-checked (2026-11-25)
- The user put the ten PDFs in `data/packets/02 Patient PDFs/`. Ingest only reads `data/packets/*.pdf`, so they were moved up one level (same file names; nothing else touched).
- `make ingest`: 86 pages (R01 9, R09 8, others 7). The page counts match every page number cited in the extractions.
- `check_extractions.py real`: **0 errors**. Every quote in R01–R10 was found on its cited page, so the extractions written from the chat text line up with the real files.
- **Not vacuous:** spot checks (R01 p.3 DOB 06/21/1958, R10 p.7 termination line, R09 p.5 prior stress echo sentence), plus a negative control: a made-up quote was rejected ("quote not on p.2").
- The app switched to the real set automatically (B24). Seed: 0 sendable, 2 PA-prep ready, 8 held. A live HTTP walkthrough worked: queue, R01 conflicting DOBs with pages, R01 "names the procedure but no indication", page viewer highlight on the real page image, R02 PA packet sent, R05 scheduling blocked by G-CLIN and audited.
- **Human sign-off (PLAN §6.4.2):** Kevin reviewed the extractions against the pages using `docs/extraction_review.md` and replied "verified" with no corrections on 2026-11-25. All ten are now `llm_draft_human_verified`; `corrections[]` stays empty. Re-checked afterwards: quote check 0 errors (mock + real), 228 tests pass.

### 14. Review sheet regenerated during sign-off (my mistake)
- **What:** when Kevin said "verified", the same command that read the sheet's tick state also re-ran `review_sheet.py`, overwriting the sheet before any edits besides ticks could be compared.
- **Caught by:** re-reading my own command output.
- **Impact: none.** All ten boxes were unticked when read, no corrections were given, and VS Code and Cursor local history (which keep a copy on every save) hold no saved version of `extraction_review.md`. The sheet was never edited and saved before it was regenerated, so nothing was lost.
- **Lesson:** read and diff user-edited files before regenerating them.

## 2026-11-27 · Seam hardening (branch `fix/seam-hardening`)

A review of the seams between document facts, policy config and simulated events found ten problems. Each fix below was driven by a reproducing test written first and confirmed failing, and neither expected-results table (`tests/test_golden_ten.py`, `tests/golden_real.yaml`) changed.

### Fix 1. Older urgent/expedited request silently downgraded (code bug, A3)
- **Problem:** with one newest priority candidate, an older candidate counted as contradicting only when the stated trajectories differed. An older signed referral requesting urgent review, followed by a newer note that doesn't repeat the request, gave PRI-3 with no flag.
- **Found by:** review. Reproduced on a P03 variant built in the test (09-15 referral `urgent_review_requested=true, new_symptoms_at_rest`; 09-29 note urgent=false, no material increase): PRI-3, expected PRI-4.
- **Fix:** `rules/priority.py`: if any older candidate's tier is higher than the newest candidate's, return PRI-4 and name both documents and their tiers. Logged as A3a in `assumptions.md`.
- **Verified:** `test_fix1_older_higher_tier_is_pri4_not_downgrade` and `test_fix1_older_expedited_also_unresolved` failed with `'PRI-3' == 'PRI-4'`, then passed. Golden tables unchanged (no current case has an older higher-tier candidate).

### Fix 2. PA decision accepted with no PA submission (code bug)
- **Problem:** on P04, a simulated `PAYER_PA_DECISION approved` could be recorded before identity was resolved and before any PA packet existed. After identity resolution and a matching E&B response, the case became sendable with zero `pa_request_packet` handoffs.
- **Found by:** review; reproduced through the service layer.
- **Fix:** `Service.simulate` rejects `PAYER_PA_DECISION` unless the case has a `pa_request_packet` handoff, and records that handoff's ID on the event (new optional `SimEvent.pa_handoff_id`, valid only on PA decisions). The UI shows the rejection as a "Not recorded: …" flash, not a 500. The case page's event table shows which packet a decision answers. The setups in `test_invariants.py::test_reason_codes_survive_transitions` and `test_transitions.py::test_wrong_role_cannot_record_event` now send a PA packet first; their assertions are unchanged.
- **Verified:** `test_fix2_pa_decision_rejected_without_pa_packet`, `test_fix2_pa_decision_records_pa_handoff_id` and `test_fix2_ui_shows_rejection_not_500` failed first (no `ValueError`; no `pa_handoff_id`; no flash), then passed. 234 tests pass.

### Fix 3. Same order could be sent to scheduling twice (code bug)
- **Problem:** the scheduling idempotency key hashed the gate snapshot plus every simulated event ID. Sending P02 to scheduling, recording an unrelated `CLINICAL_REVIEW_DECISION`, and sending again recorded a second handoff (`HO-P02-SCHED-2`) for the same order.
- **Found by:** review; reproduced through `handoffs.send`.
- **Fix:** `handoffs.active_scheduling_handoff()`: a case has at most one active scheduling handoff, meaning one not followed by a `scheduling_recall`. When one is active, `send()` returns `already_sent` (or `already_recorded` if the idempotency key is identical, as before) and records no handoff. A re-send is allowed only after a recall (Fix 4). PA packets are unchanged. The outbox now reads in insertion order (`rowid`), because `created_at` has 1-second resolution and a recall can share a second with the handoff it recalls.
- **Verified:** `test_fix3_no_second_scheduling_handoff_after_unrelated_event` failed (`'sent' == 'already_sent'`), then passed. `test_fix3_pa_packet_behaviour_unchanged` and `test_fix3_ui_message` pass. 237 tests pass.

### Fix 4. No recall when a case lost readiness after a scheduling handoff (missing behaviour)
- **Problem:** send P02 to scheduling, then record `CLINICAL_REVIEW_DECISION not_cleared` (Cardiologist). The case became not sendable, but the scheduling handoff stood and nothing told scheduling.
- **Found by:** review; reproduced through the service layer.
- **Fix:** after every simulated event, `Service.simulate` calls `handoffs.recall_if_needed()`. If the case has an active scheduling handoff and is no longer sendable, it records one `scheduling_recall` handoff to the same receiving system. The payload carries the current failing gates (with basis), reason codes and the original handoff ID, and a `handoff_sent` audit row is written with `kind: scheduling_recall`. It is idempotent: the recall's key is per original handoff, and a recalled handoff is no longer active, so later events add nothing. The case page shows a RECALLED banner and the audit row, and the outbox row shows "recalls HO-…". Metrics count only active handoffs as "handed to scheduling" and show a recall count. The recall lives in the service layer; the engine still writes nothing.
- **Why reuse `handoff_sent` for the audit row:** `audit_log.kind` has a CHECK constraint, and `CREATE TABLE IF NOT EXISTS` would not update an existing `northpoint.db`. A new kind would fail on any DB created before this branch.
- **Verified:** `test_fix4_recall_full_loop` (send → not_cleared → 1 recall → unrelated identity event → still 1 → cleared → sendable → HO-P02-SCHED-2 sent) and `test_fix4_case_page_and_outbox_show_recall` failed first (0 recalls; no banner), then passed. `test_fix4_no_recall_without_scheduling_handoff` guards the no-handoff case. 240 tests pass.

### Fix 5. Config mistakes failed open (code bug)
- **Problems (all reproduced):** `plans.PLAN-HSHMO.extra: ["HMO1"]` silently dropped HMO-1, so P05 became ready for PA prep. `no_pa_evidence_gate: []` passed G-EVID with nothing checked. `["DOC1"]` crashed evaluation at request time with a `KeyError`. `windows.resting_ecg: -90` was accepted.
- **Found by:** review.
- **Fix:** `app/rule_ids.py` is now the one registry of rule IDs. `policy.py` validates against it, and `rules/evidence.py` and `actions.py` build their rule lists from it (evidence asserts its output matches the registry). At load, `plans.*.extra` entries must be plan-extra rules (`HMO-1`); `no_pa_evidence_gate` must be non-empty and contain only evidence rules; every window must be a strict integer > 0 (rejects negative, zero, float, bool and string). Violations raise `ConfigError`, listing every problem with its YAML path. `app.main` validates config at import, so `uvicorn` and `make seed` fail at startup.
- **Config hash:** `load_config()` reads each YAML once, validates those bytes and hashes the same bytes (SHA-256 over both files, labelled and length-prefixed). The hash is stored as `config_sha256` beside `policy_version` in every evaluation audit row, and shown in the case page footer.
- **Verified:** the 11 Fix 5 tests failed first (no error raised; no registry; no `config_sha256`), then passed. `-90`, `0`, `True` and `"90"` are rejected; `1.5` already was. The hash test shows identical bytes give the same hash and a comment-only edit changes it. 252 tests pass.

### Fix 6. Confirmed identity value not checked against the packet (code bug)
- **Problem:** confirming P04's DOB as 1999-09-09, a value on no page, gave identity verified with no warning.
- **Found by:** review.
- **Fix:** `rules/identity.py` still accepts the confirmation (Epic is the authority). If a confirmed value matches no document value for that field, the reason text gets "Warning: dob confirmed value 1999-09-09 not found in packet (Epic ref …)". The flag is recorded in `detail.confirmed_not_in_packet` (and on the field entry), and the identity card shows a warning box. The check runs on every confirmed field, including one the packet did not dispute; before, such a confirmation was ignored silently. DOB comparison parses ISO dates, so formatting can't create a false warning.
- **Verified:** the four Fix 6 tests (no-match flagged, match not flagged, undisputed field checked, case-page warning) failed first, then passed. 256 tests pass.

### Fix 7. Responses dropped silently by the evaluation-date filter (code bug, A8)
- **Problem:** a simulated benefits response dated 2026-10-02 on P04 was dropped by A8. Coverage stayed conflicting, and the reason text still described the old mismatch with no sign that a newer response existed.
- **Found by:** review.
- **Fix:** `rules/coverage.py` lists every benefits response ignored for being dated after the evaluation date, whether in-packet (read from `ef.documents`, because `usable_docs` already drops them) or simulated. It appends to whatever the coverage result is, e.g. "1 response dated 2026-10-02 ignored (after evaluation date 2026-10-01, A8): SIMULATED EV-0001.", and records `detail.ignored_after_eval_date`. The simulate form rejects a `response_date` after the evaluation date. The message is shown inline inside the E&B form (and as the page flash), and no event is recorded. The service layer still accepts such events, so the reason-text path stays testable and A8 stays the engine's rule.
- **Verified:** the simulated, in-packet and form tests failed first, then passed. `test_fix7_no_ignored_text_when_nothing_ignored` guards the baseline. 260 tests pass.

### Fix 8. Evidence links fell back to an unrelated quote (code bug)
- **Problem:** when a field had no quote of its own, `rules/common.py::ref()` returned the document's first quote, so an evidence link could highlight text that doesn't support the cited field (e.g. a signer line cited for `complaint_type`).
- **Found by:** review (about 42% of non-default fields in the real set have no field-specific quote).
- **Fix:** `ref()` uses only a quote recorded for the cited field. Otherwise it returns the document's first page with no quote, and records the field on the ref (new optional `EvidenceRef.field`). Evidence links then go to `?field=<field>`, and the page viewer shows "No quote recorded for <field>; showing the page" with no highlight. `check_extractions.py` prints a WARNING for each judgment field (`complaint_type`, `indication_type`, `symptom_trajectory`, `is_assessment`, `assessment_complete`, `symptom_course_documented`, `relevant_history_documented`, `ischemia_concern`, `clinician_interpreted`, `complete`) set to a non-absent value without its own quote. Currently 143 warnings (79 real, 64 mock); `make check` still exits 0. No extraction file was edited.
- **Verified:** `test_fix8_ref_without_field_quote_has_no_quote`, `test_fix8_page_viewer_says_no_quote` and `test_fix8_check_extractions_warns_but_passes` failed first (an unrelated quote was returned; no message; no warnings), then passed. `test_fix8_ref_with_field_quote_unchanged` guards the quoted path. 264 tests pass.

### Fix 9. Page viewer back link broken on the real set (code bug)
- **Problem:** the viewer took the case key from `file.split("_")[0]`, so `Patient_01_….pdf` linked back to `/case/Patient` (404). It worked on the mock set only because those files are named `P01_packet.pdf`.
- **Found by:** review.
- **Fix:** every page link on the case page (criteria and status-card evidence, identity values, document pages) is built by `page_url()` and carries `?case=<key>`. The viewer uses that key if the case cites that file; otherwise `case_for_page()` finds the extraction whose documents cite that file and page. It never parses the file name. If nothing cites the page, the link goes back to the queue.
- **Also caught by the new test:** while adding the helper I placed it between `@app.get("/page/…")` and `page_viewer`, so FastAPI registered the helper as the route (`FastAPIError: Invalid args for response field`). Fixed by moving the decorator back.
- **Verified:** `test_fix9_crawl_case_pages_page_links_and_back_links[mock|real]` opens every `/case/<key>` page and every `/page/` link on it for both datasets, asserts each returns 200, carries the case key and has exactly one back link to that case, and that the back link returns 200. `test_fix9_back_link_falls_back_to_citing_extraction[mock|real]` checks the lookup without `?case=`. Three of the four failed first (mock links had no case key; real fallback → `/case/Patient`); the mock fallback passed only by the naming accident. All four pass, none skipped. 268 tests pass.

### Fix 10. Demo script written for the wrong dataset (doc bug)
- **Problem:** `make demo` serves the real set (R01–R10), but the walkthrough used P01–P10, and its baseline line ("1 sendable, 1 PA-ready, 8 held") was wrong for the real set (0 sendable; R02 and R07 PA-ready).
- **Found by:** review.
- **Fix:** `docs/demo_script.md` is rewritten around the real set: baseline counts and owner tabs, R07 (urgent and PA-ready), R01 (identity conflict), R02 (PA path, including the Fix 2 rejection), R05 (clearance → send → Cardiologist `not_cleared` → automatic recall), R06 (HMO-1), R04/R09 (not met), R10 (inactive), end-of-demo metrics, and a note not to flip A1/A2 live. The mock walkthrough is kept as an appendix. Every ID, number and status was taken from running the steps (service layer, then HTTP on the real set). The `not_cleared` flash now says the handoff was recalled, so the beat is visible without scrolling.
- **Verified:** `tests/test_demo_script_real.py` runs the scripted sequence in script order on the real set and asserts each outcome (`test_1`…`test_9`), and checks the document itself (`test_10`/`test_11`: no mock keys in the main walkthrough; every stated number present). Against the old script, 26 of those doc checks failed; all pass now. `test_12_recall_flash_in_ui` was written after the flash change, not before. 309 tests pass.

## Verification summary
- **After seam hardening (2026-11-27):** `make test`: 309 tests (228 before the branch); `make check`: 0 errors, 143 warnings (judgment fields without their own quote, by design). Both golden tables unchanged.
- `make test`: 228 tests, including the hand-derived real golden table (10 rows). Earlier counts: 194, then 217. Golden table, window boundaries, single-fact flips, invariants (gate leaks, no events from the engine, no derived keys, immutable sources via SQLite triggers, mutation tests on priority and unsigned docs, reason/evidence completeness, determinism, no patient-specific code), the four scripted transitions, an HTTP walkthrough of P01/P03/P04/P05/P08, and quote-on-page checks for all ten packets.
- `make reset mock ingest check seed` from clean: ~5 s.
- Live server walkthrough of P01 over HTTP: blocked → PA packet → simulated approval → sent → already recorded; metrics showed 0 gate leaks; reset cleared the outbox.
