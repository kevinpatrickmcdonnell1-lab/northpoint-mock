"""Phase 1: every mock packet renders, and every quote is found on its cited page."""
import pytest

from app.pdfs import MOCK_EXTRACTIONS, check_extraction_file, ingest_pdf, load_extractions, render_packet


@pytest.fixture(scope="module")
def pages(tmp_path_factory):
    root = tmp_path_factory.mktemp("render")
    for ef in load_extractions(MOCK_EXTRACTIONS).values():
        ingest_pdf(render_packet(ef, root / "packets"), root / "pages", png=False)
    return root / "pages"


@pytest.mark.parametrize("f", sorted(MOCK_EXTRACTIONS.glob("*.json")), ids=lambda p: p.stem)
def test_quotes_found_on_cited_pages(f, pages):
    assert check_extraction_file(f, pages) == []


def test_every_page_marked_synthetic(pages):
    for txt in pages.rglob("*.txt"):
        assert "SYNTHETIC – MOCK DATA" in txt.read_text(), txt
