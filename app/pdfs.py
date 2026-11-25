"""Mock PDF rendering, ingest (page text + PNG) and quote checking.

Used by scripts/make_mock_packets.py, scripts/ingest.py and scripts/check_extractions.py.
"""
from __future__ import annotations

import json
import os
import re
import textwrap
from pathlib import Path

from app.models import ExtractionFile, find_derived_keys

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PAGES = DATA / "pages"
# PLAN §6.4: real packets live in data/packets + data/extractions; the mock set is kept
# as a regression suite under data/packets/mock + data/extractions/mock.
MOCK_PACKETS = DATA / "packets" / "mock"
MOCK_EXTRACTIONS = DATA / "extractions" / "mock"
REAL_PACKETS = DATA / "packets"
REAL_EXTRACTIONS = DATA / "extractions"
SYNTHETIC = "SYNTHETIC – MOCK DATA"


def dataset() -> str:
    """'real' once real extraction files exist (or NORTHPOINT_DATASET=real), else 'mock'."""
    forced = os.environ.get("NORTHPOINT_DATASET")
    if forced in ("mock", "real"):
        return forced
    return "real" if real_ready() else "mock"


def real_ready() -> bool:
    """Real set is active only when its extractions AND every cited source PDF are present."""
    files = sorted(REAL_EXTRACTIONS.glob("*.json"))
    if not files:
        return False
    sources = {json.loads(f.read_text())["source_file"] for f in files}
    return all((REAL_PACKETS / s).exists() for s in sources)


def missing_real_pdfs() -> list[str]:
    sources = {json.loads(f.read_text())["source_file"] for f in REAL_EXTRACTIONS.glob("*.json")}
    return sorted(s for s in sources if not (REAL_PACKETS / s).exists())


def packets_dir(ds: str | None = None) -> Path:
    return REAL_PACKETS if (ds or dataset()) == "real" else MOCK_PACKETS


def extractions_dir(ds: str | None = None) -> Path:
    return REAL_EXTRACTIONS if (ds or dataset()) == "real" else MOCK_EXTRACTIONS

TITLES = {
    "referral": "Referral",
    "stress_echo_order": "Stress Echocardiogram Order",
    "clinician_note": "Clinician Note",
    "registration_face_sheet": "Registration Face Sheet",
    "insurance_card": "Insurance Card (copy)",
    "benefits_response": "Eligibility & Benefits Response",
    "resting_ecg_report": "Resting ECG Report",
    "exercise_ecg_report": "Exercise ECG Report",
    "ecg_tracing_raw": "ECG Tracing (machine printout)",
    "other": "Other",
}
ADMIN_TYPES = {"registration_face_sheet", "insurance_card", "benefits_response"}
ID_LABELS = [("name", "Patient"), ("dob", "DOB"), ("mrn", "MRN"),
             ("packet_id", "Packet ID"), ("member_id", "Member ID")]


def load_extractions(directory: Path | None = None) -> dict[str, ExtractionFile]:
    directory = directory or extractions_dir()
    out = {}
    for f in sorted(directory.glob("*.json")):
        out[f.stem] = ExtractionFile.model_validate_json(f.read_text())
    return out


# --------------------------------------------------------------------------- #
# Render                                                                       #
# --------------------------------------------------------------------------- #

def page_lines(ef: ExtractionFile, page: int) -> list[str]:
    """Text lines printed on a page. Identifier labels match app.rules.common.id_quote."""
    docs = [d for d in ef.documents if page in d.pages]
    lines: list[str] = ["NORTHPOINT HEART & VASCULAR", SYNTHETIC, ""]
    for d in docs:
        lines.append(TITLES[d.doc_type].upper())
        lines.append(f"Document date: {d.date}" if d.date else "Document date: (none)")
        for key, label in ID_LABELS:
            val = getattr(d.identifiers, key)
            if val is not None:
                lines.append(f"{label}: {val}")
        lines.append("")
        for q in d.quotes:
            if q.page == page:
                lines.extend(textwrap.wrap(q.text, 88) or [""])
        lines.append("")
        if d.doc_type in ADMIN_TYPES:
            lines.append("Signature: n/a (administrative record)")
        elif d.doc_type == "ecg_tracing_raw":
            lines.append("Machine-generated. No clinician interpretation.")
        elif d.signed:
            lines.append(f"Electronically signed: {d.signer} on {d.date}")
        else:
            lines.append("UNSIGNED DRAFT - not signed by a clinician")
    return lines


def render_packet(ef: ExtractionFile, out_dir: Path = MOCK_PACKETS) -> Path:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / ef.source_file
    n_pages = max(p for d in ef.documents for p in d.pages)
    c = canvas.Canvas(str(path), pagesize=letter)
    c.setTitle(f"{ef.packet_id} ({SYNTHETIC})")
    w, h = letter
    for page in range(1, n_pages + 1):
        y = h - 60
        for i, line in enumerate(page_lines(ef, page)):
            c.setFont("Helvetica-Bold" if i == 0 else "Helvetica", 12 if i == 0 else 10.5)
            c.drawString(54, y, line)
            y -= 16
        c.setFont("Helvetica-Oblique", 8.5)
        c.drawString(54, 36, f"{SYNTHETIC} · {ef.source_file} · page {page} of {n_pages} · "
                             f"received via {ef.received_channel} {ef.received_at}"
                             + (f" · site: {ef.ordering_site}" if ef.ordering_site else ""))
        c.showPage()
    c.save()
    return path


# --------------------------------------------------------------------------- #
# Ingest                                                                       #
# --------------------------------------------------------------------------- #

def ingest_pdf(pdf: Path, pages_root: Path = PAGES, png: bool = True) -> int:
    """Split a PDF (single packet or combined) into original-file/original-page outputs."""
    from pypdf import PdfReader

    out = pages_root / pdf.name
    out.mkdir(parents=True, exist_ok=True)
    reader = PdfReader(str(pdf))
    for i, page in enumerate(reader.pages, start=1):
        (out / f"{i}.txt").write_text(page.extract_text() or "")
    if png:
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(str(pdf))
        for i in range(len(doc)):
            doc[i].render(scale=1.6).to_pil().save(out / f"{i + 1}.png")
        doc.close()
    (out / "manifest.json").write_text(json.dumps(
        {"file": pdf.name, "pages": len(reader.pages)}, indent=2))
    return len(reader.pages)


# --------------------------------------------------------------------------- #
# Check                                                                        #
# --------------------------------------------------------------------------- #

MANIFEST = REAL_PACKETS / "manifest.yaml"


def load_manifest(path: Path = MANIFEST) -> dict[str, dict[str, tuple[int, int]]]:
    """Optional page ranges for combined PDFs (PLAN §6.4.1):
        Combined_packets.pdf: {P01: [1, 7], P02: [8, 14]}
    Page numbers stay the ORIGINAL file's page numbers."""
    if not path.exists():
        return {}
    import yaml
    raw = yaml.safe_load(path.read_text()) or {}
    return {f: {k: (int(v[0]), int(v[1])) for k, v in m.items()} for f, m in raw.items()}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def check_extraction_file(path: Path, pages_root: Path = PAGES,
                          manifest: dict | None = None) -> list[str]:
    """Schema, no derived keys, every quote found on its cited page, and (for combined
    PDFs listed in the manifest) every cited page inside that packet's page range."""
    errors: list[str] = []
    raw = json.loads(path.read_text())
    for hit in find_derived_keys(raw):
        errors.append(f"{path.name}: derived key {hit}")
    try:
        ef = ExtractionFile.model_validate(raw)
    except Exception as e:  # noqa: BLE001
        return errors + [f"{path.name}: schema: {e}"]
    manifest = load_manifest() if manifest is None else manifest
    rng = manifest.get(ef.source_file, {}).get(ef.patient_key)
    if ef.source_file in manifest and rng is None:
        errors.append(f"{path.name}: {ef.patient_key} has no page range in manifest for {ef.source_file}")
    for d in ef.documents:
        if rng and any(not (rng[0] <= p <= rng[1]) for p in d.pages):
            errors.append(f"{path.name}: {d.doc_id} pages {d.pages} outside {ef.patient_key} range {rng}")
        if not d.quotes:
            errors.append(f"{path.name}: {d.doc_id} has no quotes")
        for q in d.quotes:
            txt_path = pages_root / ef.source_file / f"{q.page}.txt"
            if not txt_path.exists():
                errors.append(f"{path.name}: {d.doc_id} page {q.page} not ingested")
                continue
            if _norm(q.text) not in _norm(txt_path.read_text()):
                errors.append(f"{path.name}: {d.doc_id} quote not on p.{q.page}: {q.text!r}")
    return errors
