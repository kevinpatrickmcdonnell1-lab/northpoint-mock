# Phase 7 runbook: swapping in the real patient PDFs (PLAN §6.4)

The tooling is built and tested on the mock set. What's left needs the real PDFs and a human: the hand-derived golden table.

## Layout (already in place)
| | Mock (regression suite) | Real |
|---|---|---|
| PDFs | `data/packets/mock/` | `data/packets/` |
| Extractions | `data/extractions/mock/` | `data/extractions/` |
| Drafts (never loaded) | — | `data/extractions/drafts/` |
| Golden table | `tests/test_golden_ten.py` | `tests/golden_real.yaml` (you write it) |

The app switches to the real set automatically once any `data/extractions/*.json` exists. Override with `NORTHPOINT_DATASET=mock|real`. The mock tests always run against the mock set.

## Steps
1. **Drop the PDFs** into `data/packets/`. If one PDF holds several patients, add `data/packets/manifest.yaml`:
   ```yaml
   Real_batch.pdf: {R01: [1, 7], R02: [8, 15]}   # ORIGINAL page numbers
   ```
2. `make ingest` writes page text and PNGs to `data/pages/<file>/`. Read the pages yourself.
3. **Derive the golden table by hand first.** Copy `tests/golden_real.example.yaml` to `tests/golden_real.yaml` and fill every row from the real pages, before running the engine on them. `test_golden_real.py` fails while real extractions exist without this table.
4. **Draft the extractions** (optional): `make draft FILE=Real_batch.pdf KEY=R01 PAGES=1-7` writes `data/extractions/drafts/R01.draft.json`. The draft is heuristic: one document per page, doc type from the page heading, identifiers from labelled lines, and quote candidates. It invents no facts and deliberately fails schema.
5. **Finish each extraction by hand**: fill in `fields`, set `signed`/`signer`, keep only the quotes that back a field, and set `received_at`, `received_channel` and `ordering_site`. Save to `data/extractions/<KEY>.json` with `extraction_method: manual`. If an LLM drafted it and a human verified it, use `llm_draft_human_verified`. Record every change from the draft or OCR in `corrections[]`.
6. `make check`: schema, no derived keys, every quote found on its cited page, and pages within the manifest ranges.
7. `make test`: each mismatch in `test_golden_real.py` is an **extraction error, a misread rule or a code bug**. Record which in `docs/build_log.md`. Never edit the table to match the engine without writing down why.
8. `make demo` now serves the real set. Mock regression tests keep running.
