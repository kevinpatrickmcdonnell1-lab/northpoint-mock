# CLAUDE.md — Northpoint stress-echo readiness mock

This is a mock for a Tennr Solutions Consultant interview demo. The full spec is in `PLAN.md`. Build it phase by phase (PLAN §10) and stop for review at each checkpoint.

## Non-negotiable invariants
1. **One engine for all ten patients.** The rules are pure functions over document facts + simulated events + policy config. No patient-specific branches, and no expected outcomes stored anywhere the engine can read. Expected outcomes live only in `tests/`.
2. **Extraction files hold document facts only** (what a page says, plus file/page/quote). Derived fields (`priority`, `status`, `verified`, `pa_required`, `ready`, …) are forbidden by schema.
3. **Never invent approval or clearance.** Payer decisions, clinician clearances, identity resolutions and new E&B responses exist only as simulated events created by an explicit UI action, labelled SIMULATED, with actor role, timestamps and an audit entry. The engine never creates events.
4. **Source evidence is immutable.** Simulated events are stored separately and never overwrite or relabel PDF evidence.
5. **Clinical priority is independent** of payer, coverage, PA and identity. Raw ECG tracings, labs, payer type and unsigned documents never affect any rule.
6. **Gates are hard.** Nothing reaches the scheduling handoff with a failing gate. Blocked attempts are recorded. Show every simultaneous hold, not just the first.
7. **Never auto-select between conflicting identifiers.** Show both values with their file/page sources.
8. **"Not met" is not a denial, and "supported" is not an approval.** Use these exact words in the UI.
9. **Never compare identifiers of different types** (MRN vs member ID vs packet ID).
10. **Dates come from `config/policy_sc_card_1_1.yaml`**, evaluated as of 2026-10-01. No `date.today()` in rule code.

## Working agreements
- Write the tests first: build `tests/test_golden_ten.py` from PLAN §6.3 before writing rule code. If the engine disagrees with the table, **stop and report** whether the cause is an extraction error, a rule-reading error or a code bug. Don't edit the table to match the code without explaining why.
- Log every mistake you make and fix, and every judgment call on rule wording, in `docs/build_log.md` and `docs/assumptions.md` (date, what happened, how it was caught, how it was verified).
- Keep the stack small: Python 3.11+, FastAPI, Jinja2, HTMX, SQLite, pydantic v2, pytest. No live LLM or OCR calls at runtime. No hosting.
- Choose reliability over polish: plain server-rendered UI, one CSS file.
- `make test` must pass before you call a phase done. `make demo` resets the DB, ingests, evaluates all ten and serves on localhost:8000.
- All mock artifacts carry "SYNTHETIC – MOCK DATA". All names and IDs are fictional.
