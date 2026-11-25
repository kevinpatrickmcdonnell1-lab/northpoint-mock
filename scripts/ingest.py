"""Split every PDF (mock regression set + real packets) into page text + PNG under
data/pages/<file>/<page>.*. Keeps original file + original page number, so a combined
multi-patient PDF works: extraction files cite (source_file, page) directly.
Optional data/packets/manifest.yaml assigns page ranges per packet in a combined PDF.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.pdfs import MOCK_PACKETS, REAL_PACKETS, ingest_pdf, load_manifest  # noqa: E402

if __name__ == "__main__":
    for d in (MOCK_PACKETS, REAL_PACKETS):
        for pdf in sorted(d.glob("*.pdf")):
            print(f"ingested {d.name}/{pdf.name}: {ingest_pdf(pdf)} pages")
    for file, ranges in load_manifest().items():
        print(f"manifest {file}: " + ", ".join(f"{k} p.{a}-{b}" for k, (a, b) in ranges.items()))
