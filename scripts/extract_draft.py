"""OPTIONAL prep-time draft extraction for real PDFs (PLAN §6.4.2). Never runs at demo time.

Reads ingested page text (data/pages/<file>/<n>.txt) and writes a DRAFT skeleton to
data/extractions/drafts/<KEY>.draft.json:
  - one document per page, doc_type guessed from page headings;
  - identifiers parsed from labelled lines (Patient/Name, DOB, MRN, Member ID, Packet ID);
  - candidate dates and every non-boilerplate line offered as quote candidates.

A human must then: split/merge documents, fill `fields` from the page, keep only the
quotes that back a field, set signed/signer, and save the result to
data/extractions/<KEY>.json with extraction_method "manual". Log every change in
`corrections[]`. Drafts are never loaded by the engine (separate directory, and they
fail schema on purpose).

No model is called: deterministic heuristics only, so a draft cannot invent facts.

Usage: uv run python scripts/extract_draft.py <file.pdf> [--packet-key P01] [--pages 1-7]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.pdfs import DATA, PAGES  # noqa: E402

DRAFTS = DATA / "extractions" / "drafts"

DOC_TYPE_HINTS = [  # first match wins; specific before generic
    ("ecg_tracing_raw", r"machine[- ]generated|ecg tracing|no clinician interpretation"),
    ("exercise_ecg_report", r"exercise (treadmill )?ecg|treadmill|exercise stress test"),
    ("resting_ecg_report", r"resting (12-lead )?ecg|12-lead ecg|electrocardiogram report"),
    ("benefits_response", r"eligibility|benefits response|coverage on"),
    ("insurance_card", r"insurance card|member card|card \(copy\)"),
    ("registration_face_sheet", r"registration|face sheet"),
    ("stress_echo_order", r"stress echocardiogram order|procedure:"),
    ("referral", r"referral|service requested"),
    ("clinician_note", r"clinician note|progress note|assessment|clinic note|history:"),
]
ID_PATTERNS = {
    "name": r"(?:Patient|Name)\s*:\s*(.+)",
    "dob": r"(?:DOB|Date of birth)\s*:\s*(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})",
    "mrn": r"MRN\s*:\s*([A-Z0-9-]+)",
    "packet_id": r"Packet ID\s*:\s*([A-Z0-9-]+)",
    "member_id": r"Member ID\s*:\s*([A-Z0-9-]+)",
}
DATE = r"\b(20\d{2}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/20\d{2})\b"
BOILERPLATE = re.compile(r"SYNTHETIC|NORTHPOINT HEART|page \d+ of \d+", re.I)


def _match(text: str):
    low = text.lower()
    return next((t for t, pat in DOC_TYPE_HINTS if re.search(pat, low)), None)


def guess_type(text: str) -> str:
    """Classify from the page HEADING first. Body text often mentions other tests
    ("Resting ECG reviewed in clinic") and must not turn a note into an ECG report."""
    headings = [ln.strip() for ln in text.splitlines()
                if ln.strip().isupper() and len(ln.strip()) < 60 and not BOILERPLATE.search(ln)]
    for h in headings:
        if (t := _match(h)):
            return t
    return _match(text) or "other"


def draft_page(page: int, text: str) -> dict:
    ids = {k: m.group(1).strip() for k, pat in ID_PATTERNS.items() if (m := re.search(pat, text))}
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not BOILERPLATE.search(ln)]
    return {
        "doc_id": f"DRAFT-p{page}", "doc_type": guess_type(text), "pages": [page],
        "date": None, "signed": None, "signer": None, "identifiers": ids, "fields": {},
        "quotes": [],
        "_review": {
            "candidate_dates": sorted(set(re.findall(DATE, text))),
            "signature_lines": [ln for ln in lines if re.search(r"sign", ln, re.I)],
            "quote_candidates": [{"page": page, "text": ln} for ln in lines],
            "todo": "confirm doc_type/date/signed/signer; fill fields; keep only quotes that back a field",
        },
    }


def build_draft(file: str, key: str, pages: list[int], pages_root: Path = PAGES) -> dict:
    return {
        "_status": "DRAFT – not loaded by the engine; human review required",
        "packet_id": None, "patient_key": key, "source_file": file,
        "received_at": None, "received_channel": None, "ordering_site": None,
        "extraction_method": "manual",
        "documents": [draft_page(p, (pages_root / file / f"{p}.txt").read_text()) for p in pages],
        "corrections": [],
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--packet-key", default=None)
    ap.add_argument("--pages", default=None, help="original page range, e.g. 1-7")
    a = ap.parse_args(argv)
    folder = PAGES / a.file
    if not folder.exists():
        sys.exit(f"{a.file} not ingested; run `make ingest` first")
    if a.pages:
        lo, hi = map(int, a.pages.split("-"))
        pages = list(range(lo, hi + 1))
    else:
        pages = sorted(int(p.stem) for p in folder.glob("*.txt"))
    key = a.packet_key or Path(a.file).stem.split("_")[0]
    DRAFTS.mkdir(parents=True, exist_ok=True)
    out = DRAFTS / f"{key}.draft.json"
    out.write_text(json.dumps(build_draft(a.file, key, pages), indent=2) + "\n")
    print(f"wrote {out.relative_to(DATA.parent)} ({len(pages)} pages) – review before use")


if __name__ == "__main__":
    main()
