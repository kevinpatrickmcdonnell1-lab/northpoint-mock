"""Write docs/extraction_review.md: every real extracted fact beside its page + quote,
for the human sign-off in PLAN §6.4.2. Read-only over data/extractions/*.json."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.pdfs import REAL_EXTRACTIONS, load_extractions  # noqa: E402

out = ["# Real extraction review sheet", "",
       "Tick each packet after checking the facts against the page (open `/page/<file>/<page>` in the app).",
       "When all ten are ticked, tell Claude \"verified\" (plus any corrections); it will switch",
       "`extraction_method` to `llm_draft_human_verified` and log corrections in `corrections[]`.", ""]
for key, ef in load_extractions(REAL_EXTRACTIONS).items():
    out += [f"## [ ] {key} · {ef.source_file}", "",
            f"received {ef.received_at} via {ef.received_channel} · packet {ef.packet_id}", "",
            "| Doc | Type | Pages | Date | Signed | Key facts | Quotes (page) |", "|---|---|---|---|---|---|---|"]
    for d in ef.documents:
        facts = {k: v for k, v in d.fields.model_dump(mode="json").items()
                 if v not in (None, False, "not_stated", [], {"stated": False, "date": None},
                              {"stated": False, "none_since": None})}
        ids = {k: v for k, v in d.identifiers.model_dump(mode="json").items() if v}
        q = "<br>".join(f"p.{x.page}: “{x.text}”" for x in d.quotes)
        out.append(f"| {d.doc_id} | {d.doc_type} | {d.pages} | {d.date or ''} | "
                   f"{d.signer or ('yes' if d.signed else 'no')} | "
                   f"{json.dumps(ids)}<br>{json.dumps(facts)} | {q} |")
    out.append("")
(ROOT / "docs" / "extraction_review.md").write_text("\n".join(out) + "\n")
print("wrote docs/extraction_review.md")
