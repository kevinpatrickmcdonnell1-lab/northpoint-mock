"""Render mock packet PDFs FROM the extraction JSONs (PLAN §6.1). SYNTHETIC – MOCK DATA."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.pdfs import MOCK_EXTRACTIONS, load_extractions, render_packet  # noqa: E402

if __name__ == "__main__":
    for key, ef in load_extractions(MOCK_EXTRACTIONS).items():
        print("rendered", render_packet(ef).name)
