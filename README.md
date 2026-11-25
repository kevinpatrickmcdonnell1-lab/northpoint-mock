# Northpoint stress-echo readiness mock

**SYNTHETIC – MOCK DATA.** All names and IDs are fictional. Nothing connects to a real system.

Built for a Tennr Solutions Consultant demo. The spec is in [PLAN.md](PLAN.md), the invariants are in [CLAUDE.md](CLAUDE.md), and the walkthrough is in [docs/demo_script.md](docs/demo_script.md).

```
make setup              # uv sync (Python 3.11)
make test               # 228 tests: mock + real golden tables, boundaries, invariants, transitions, UI, stretch
make demo               # reset DB → render mock PDFs → ingest → check quotes → evaluate all 10 → serve :8000
make demo PORT=8010     # if :8000 is taken
```

## How it works
`CaseState = evaluate(source_facts, simulated_events, policy)`. It's a pure function ([app/engine.py](app/engine.py)) with no I/O and no clock, and the same code runs for all ten patients.

| Layer | Where |
|---|---|
| Document facts (no derived fields, schema-enforced) | [data/extractions/](data/extractions/), [app/models.py](app/models.py) |
| Policy dates, windows, plan matrix | [config/policy_sc_card_1_1.yaml](config/policy_sc_card_1_1.yaml) |
| Assumption flags A1/A2 | [config/workflow.yaml](config/workflow.yaml) |
| Rules PRI, ID-1, plan, COV-1, DOC-1, QUAL-1/2, REPEAT-1, HMO-1 | [app/rules/](app/rules/) |
| Gates, actions → owner → Tennr product | [app/gates.py](app/gates.py), [app/actions.py](app/actions.py) |
| SQLite (append-only events/audit via triggers), handoff stubs | [app/store.py](app/store.py), [app/handoffs.py](app/handoffs.py) |
| UI: queue, case detail, page viewer, outbox, metrics | [app/main.py](app/main.py), [app/templates/](app/templates/) |
| Expected outcomes (only here) | [tests/test_golden_ten.py](tests/test_golden_ten.py), `tests/golden_real.yaml` (real, hand-derived) |
| Mock regression set | `data/packets/mock/`, `data/extractions/mock/` |

Real set (R01–R10, active): **0 sendable · 2 ready for PA prep (R02, R07) · 8 held**. Mock regression set (P01–P10, `NORTHPOINT_DATASET=mock`): 1 sendable · 1 PA-ready · 8 held.

## Build status
| Phase | Status |
|---|---|
| 0 Scaffold, Makefile, configs, models | ✅ |
| 1 Extractions, mock PDFs, ingest, quote check | ✅ |
| 2 Golden / boundary / policy-date tests (written first) | ✅ |
| 3 Rules, gates, actions | ✅ all golden cells reproduced |
| 4 Store, sim events, audit, handoffs, invariants, transitions | ✅ |
| 5 UI: queue, detail, page viewer, outbox | ✅ HTTP walkthrough of P01/P03/P04/P05/P08 tested |
| 6 Metrics, reset/seed, docs | ✅ clean build in ~5 s |
| 7 Real PDFs (PLAN §6.4) | ✅ hand-derived golden table written first ([tests/golden_real.yaml](tests/golden_real.yaml)); 10 PDFs ingested (86 pages); every extraction quote found on its page; engine matches all 10 rows; app runs on the real set. extractions human-verified by Kevin 2026-11-25 (`llm_draft_human_verified`) |
| Stretch | ✅ simulated downstream outage → retry → dead-letter → requeue; per-site field, filter and metrics |

Mistakes and judgment calls are logged in [docs/build_log.md](docs/build_log.md) and [docs/assumptions.md](docs/assumptions.md).
