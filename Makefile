# Northpoint stress-echo readiness mock. Requires uv (https://docs.astral.sh/uv/).
# SYNTHETIC – MOCK DATA.
RUN := uv run
PORT ?= 8000

.PHONY: setup extractions mock ingest check test reset seed demo serve draft

setup:
	uv sync

# Re-author the ten mock extraction JSONs (document facts only, PLAN §6.2)
extractions:
	$(RUN) python scripts/author_mock_extractions.py

# Render mock packet PDFs FROM data/extractions/*.json
mock:
	$(RUN) python scripts/make_mock_packets.py

# Split pages -> data/pages/<file>/<page>.txt + .png
ingest:
	$(RUN) python scripts/ingest.py

# Phase 7 (prep time, real PDFs): draft skeleton for human review, e.g.
#   make draft FILE=Real_batch.pdf KEY=R01 PAGES=1-7
draft:
	$(RUN) python scripts/extract_draft.py $(FILE) --packet-key $(KEY) $(if $(PAGES),--pages $(PAGES),)

# Schema + no derived keys + every quote found on its cited page
check:
	$(RUN) python scripts/check_extractions.py

test:
	$(RUN) pytest -q

# Wipe SQLite DB (sim events, audit, outbox) and outbox JSON files. Source facts are untouched.
reset:
	rm -f northpoint.db northpoint.db-wal northpoint.db-shm outbox/*.json

seed:
	$(RUN) python -m app.seed

serve:
	$(RUN) uvicorn app.main:app --port $(PORT)

# Reset DB, render + ingest + check, evaluate all ten, serve on http://localhost:8000
demo: reset mock ingest check seed serve
