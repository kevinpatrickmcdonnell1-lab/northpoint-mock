"""Schema + no derived keys + every quote found on its cited page (+ manifest page ranges).
Usage: check_extractions.py [mock|real|all]   (default: all present). Exit 1 on any error."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.pdfs import check_extraction_file, extractions_dir, missing_real_pdfs  # noqa: E402

if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    sets = ["mock", "real"] if which == "all" else [which]
    errors, n = [], 0
    for ds in sets:
        files = sorted(extractions_dir(ds).glob("*.json"))
        if ds == "real" and files and missing_real_pdfs():
            print(f"WARNING [real] quote check skipped: {len(missing_real_pdfs())} source PDFs not in "
                  "data/packets/: " + ", ".join(missing_real_pdfs()))
            if which == "real":
                sys.exit(1)
            continue
        n += len(files)
        errors += [f"[{ds}] {e}" for f in files for e in check_extraction_file(f)]
    for e in errors:
        print("ERROR", e)
    print(f"checked {n} extraction files ({', '.join(sets)}): {len(errors)} errors")
    sys.exit(1 if errors else 0)
