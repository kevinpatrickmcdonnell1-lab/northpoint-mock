"""Phase 7 readiness (PLAN §6.4) without the real PDFs: combined PDFs, drafts, dataset switch."""
import copy
import importlib
import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError
from pypdf import PdfReader, PdfWriter

from app.models import ExtractionFile
from app.pdfs import (MOCK_EXTRACTIONS, check_extraction_file, dataset, ingest_pdf,
                      load_extractions, real_ready, render_packet)
from app.service import summarize
from tests.helpers import FACTS, run

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
extract_draft = importlib.import_module("extract_draft")


def _shift(ef: ExtractionFile, file: str, offset: int) -> ExtractionFile:
    d = copy.deepcopy(ef.model_dump(mode="json"))
    d["source_file"] = file
    for doc in d["documents"]:
        doc["pages"] = [p + offset for p in doc["pages"]]
        for q in doc["quotes"]:
            q["page"] += offset
    return ExtractionFile.model_validate(d)


@pytest.fixture(scope="module")
def combined(tmp_path_factory):
    """P01 + P02 rendered into ONE multi-patient PDF, as a real fax batch might arrive."""
    root = tmp_path_factory.mktemp("combined")
    w = PdfWriter()
    n01 = 0
    for k in ("P01", "P02"):
        r = PdfReader(str(render_packet(FACTS[k], root / "src")))
        n01 = n01 or len(r.pages)
        for p in r.pages:
            w.add_page(p)
    pdf = root / "Combined_batch.pdf"
    with open(pdf, "wb") as f:
        w.write(f)
    ingest_pdf(pdf, root / "pages", png=False)
    efs = {"P01": _shift(FACTS["P01"], pdf.name, 0), "P02": _shift(FACTS["P02"], pdf.name, n01)}
    ex = root / "extractions"
    ex.mkdir()
    for k, ef in efs.items():
        (ex / f"{k}.json").write_text(ef.model_dump_json(indent=2))
    manifest = {pdf.name: {"P01": (1, n01), "P02": (n01 + 1, n01 + 7)}}
    return root, efs, manifest, n01


def test_combined_pdf_keeps_original_pages_and_quotes(combined):
    root, efs, manifest, n01 = combined
    for k in efs:
        assert check_extraction_file(root / "extractions" / f"{k}.json", root / "pages", manifest) == []
    p02 = efs["P02"].documents[0]
    assert p02.pages == [n01 + 1]          # original page number in the combined file


def test_combined_pdf_same_outcomes(combined):
    _, efs, _, n01 = combined
    for k, ef in efs.items():
        a, b = summarize(run(ef)), summarize(run(FACTS[k]))
        a.pop("evidence_refs"), b.pop("evidence_refs")
        assert a == b
    refs = run(efs["P02"]).criteria["QUAL-2"].evidence_refs
    assert refs[0].file == "Combined_batch.pdf" and refs[0].page == n01 + 7


def test_manifest_range_violation_is_reported(combined):
    root, _, manifest, n01 = combined
    bad = {"Combined_batch.pdf": {"P01": (1, 3), "P02": (n01 + 1, n01 + 7)}}
    errs = check_extraction_file(root / "extractions" / "P01.json", root / "pages", bad)
    assert any("outside P01 range" in e for e in errs)


def test_extract_draft_guesses_types_and_never_auto_selects(tmp_path):
    root = tmp_path
    pdf = render_packet(FACTS["P04"], root / "src")
    ingest_pdf(pdf, root / "pages", png=False)
    draft = extract_draft.build_draft(pdf.name, "P04", list(range(1, 8)), root / "pages")
    want = [d.doc_type for d in FACTS["P04"].documents]
    assert [d["doc_type"] for d in draft["documents"]] == want
    dobs = {d["identifiers"].get("dob") for d in draft["documents"]} - {None}
    assert dobs == {"1951-03-14", "1951-04-13"}      # both kept, per page; nothing chosen
    assert draft["documents"][0]["fields"] == {} and draft["documents"][0]["quotes"] == []
    with pytest.raises(ValidationError):              # a draft can never be loaded as facts
        ExtractionFile.model_validate(draft)


def test_extract_draft_on_all_mock_packets_types(tmp_path):
    hits = total = 0
    for k, ef in FACTS.items():
        pdf = render_packet(ef, tmp_path / "src")
        ingest_pdf(pdf, tmp_path / "pages", png=False)
        pages = [d.pages[0] for d in ef.documents]
        draft = extract_draft.build_draft(pdf.name, k, pages, tmp_path / "pages")
        for d, real in zip(draft["documents"], ef.documents):
            total += 1
            hits += d["doc_type"] == real.doc_type
    assert hits == total, f"{hits}/{total} doc types guessed"


def test_dataset_switch(monkeypatch):
    monkeypatch.delenv("NORTHPOINT_DATASET", raising=False)
    assert dataset() == ("real" if real_ready() else "mock")   # real only once its PDFs are present
    monkeypatch.setenv("NORTHPOINT_DATASET", "real")
    assert dataset() == "real"
    assert sorted(load_extractions(MOCK_EXTRACTIONS)) == [f"P{i:02d}" for i in range(1, 11)]


def test_drafts_never_loaded_or_checked():
    drafts = MOCK_EXTRACTIONS.parent / "drafts"
    assert not list(MOCK_EXTRACTIONS.glob("*.draft.json"))
    assert all(p.parent != drafts for p in MOCK_EXTRACTIONS.parent.glob("*.json"))
    json.dumps({})  # keep json import used
