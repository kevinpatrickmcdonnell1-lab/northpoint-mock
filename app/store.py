"""SQLite store: cases (source facts), sim_events + fact_reviews + audit_log (append-only), outbox.

Append-only and immutability are enforced by triggers, not just by convention.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

from app.fact_review import FactReview
from app.models import ExtractionFile, SimEvent

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
  patient_key TEXT PRIMARY KEY, packet_id TEXT NOT NULL, source_file TEXT NOT NULL,
  extraction_json TEXT NOT NULL, loaded_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sim_events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL,
  patient_key TEXT NOT NULL, type TEXT NOT NULL, event_json TEXT NOT NULL,
  simulated INTEGER NOT NULL CHECK (simulated = 1));
CREATE TABLE IF NOT EXISTS audit_log (
  audit_id INTEGER PRIMARY KEY AUTOINCREMENT, patient_key TEXT, at TEXT NOT NULL,
  actor TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('evaluation','state_change','sim_event',
  'handoff_attempt','handoff_sent','handoff_blocked','correction','delivery_attempt','dead_letter',
  'outage')), detail_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS outbox (
  handoff_id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL, patient_key TEXT NOT NULL,
  work_item_type TEXT NOT NULL, receiving_system TEXT NOT NULL, payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS deliveries (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, handoff_id TEXT NOT NULL, attempt INTEGER NOT NULL,
  at TEXT NOT NULL, result TEXT NOT NULL CHECK (result IN ('delivered','failed','dead_letter','requeued')),
  error TEXT);
CREATE TABLE IF NOT EXISTS fact_reviews (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, review_id TEXT UNIQUE NOT NULL, patient_key TEXT NOT NULL,
  doc_id TEXT NOT NULL, field TEXT NOT NULL, action TEXT NOT NULL CHECK (action IN ('confirm','correct')),
  review_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS claims (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, patient_key TEXT NOT NULL, code TEXT NOT NULL,
  action TEXT NOT NULL CHECK (action IN ('claim','takeover','release')),
  actor_role TEXT NOT NULL, actor_name TEXT NOT NULL, note TEXT, at TEXT NOT NULL,
  after_audit_id INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS receiver_outages (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, receiving_system TEXT NOT NULL, down INTEGER NOT NULL,
  at TEXT NOT NULL, note TEXT, simulated INTEGER NOT NULL CHECK (simulated = 1));
"""
for _t in ("cases", "sim_events", "audit_log", "outbox", "deliveries", "receiver_outages",
           "fact_reviews", "claims"):
    SCHEMA += f"""
CREATE TRIGGER IF NOT EXISTS {_t}_no_update BEFORE UPDATE ON {_t}
  BEGIN SELECT RAISE(ABORT, '{_t} is append-only'); END;
CREATE TRIGGER IF NOT EXISTS {_t}_no_delete BEFORE DELETE ON {_t}
  BEGIN SELECT RAISE(ABORT, '{_t} is append-only'); END;"""


class Store:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self):
        self.db.close()

    # ---- cases (source facts, immutable) --------------------------------------
    def load_case(self, ef: ExtractionFile, at: str) -> None:
        self.db.execute("INSERT INTO cases VALUES (?,?,?,?,?)",
                        (ef.patient_key, ef.packet_id, ef.source_file,
                         ef.model_dump_json(), at))

    def keys(self) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT patient_key FROM cases ORDER BY patient_key")]

    def case(self, key: str) -> ExtractionFile:
        row = self.db.execute("SELECT extraction_json FROM cases WHERE patient_key=?", (key,)).fetchone()
        if row is None:
            raise KeyError(key)
        return ExtractionFile.model_validate_json(row[0])

    def case_json(self, key: str) -> str:
        return self.db.execute("SELECT extraction_json FROM cases WHERE patient_key=?", (key,)).fetchone()[0]

    # ---- simulated events (append-only) ---------------------------------------
    def add_event(self, ev: SimEvent) -> None:
        assert ev.simulated is True
        self.db.execute("INSERT INTO sim_events (event_id, patient_key, type, event_json, simulated) "
                        "VALUES (?,?,?,?,1)", (ev.event_id, ev.patient_key, ev.type, ev.model_dump_json()))

    def events(self, key: Optional[str] = None) -> list[SimEvent]:
        q, args = "SELECT event_json FROM sim_events", ()
        if key:
            q, args = q + " WHERE patient_key=?", (key,)
        return [SimEvent.model_validate_json(r[0]) for r in self.db.execute(q + " ORDER BY seq", args)]

    def event_count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM sim_events").fetchone()[0]

    # ---- human fact reviews (append-only; the extraction in `cases` never changes) --------
    def add_review(self, r: FactReview) -> None:
        self.db.execute("INSERT INTO fact_reviews (review_id, patient_key, doc_id, field, action, "
                        "review_json) VALUES (?,?,?,?,?,?)",
                        (r.review_id, r.patient_key, r.doc_id, r.field, r.action, r.model_dump_json()))

    def reviews(self, key: Optional[str] = None) -> list[FactReview]:
        q, args = "SELECT review_json FROM fact_reviews", ()
        if key:
            q, args = q + " WHERE patient_key=?", (key,)
        return [FactReview.model_validate_json(r[0]) for r in self.db.execute(q + " ORDER BY seq", args)]

    def review_count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM fact_reviews").fetchone()[0]

    # ---- work-item claims (append-only; the current holder is derived) -----------
    def add_claim(self, key: str, code: str, action: str, actor_role: str, actor_name: str,
                  at: str, note: Optional[str] = None) -> None:
        last = self.db.execute("SELECT COALESCE(MAX(audit_id), 0) FROM audit_log").fetchone()[0]
        self.db.execute("INSERT INTO claims (patient_key, code, action, actor_role, actor_name, note, at, "
                        "after_audit_id) VALUES (?,?,?,?,?,?,?,?)",
                        (key, code, action, actor_role, actor_name, note, at, last))

    def claims(self, key: Optional[str] = None) -> list[dict]:
        q, args = "SELECT * FROM claims", ()
        if key:
            q, args = q + " WHERE patient_key=?", (key,)
        return [dict(r) for r in self.db.execute(q + " ORDER BY seq", args)]

    # ---- audit (append-only) --------------------------------------------------
    def audit(self, key: Optional[str], at: str, actor: str, kind: str, detail: dict[str, Any]) -> None:
        self.db.execute("INSERT INTO audit_log (patient_key, at, actor, kind, detail_json) VALUES (?,?,?,?,?)",
                        (key, at, actor, kind, json.dumps(detail, default=str)))

    def audit_rows(self, key: Optional[str] = None, kind: Optional[str] = None) -> list[dict]:
        q, args = "SELECT * FROM audit_log WHERE 1=1", []
        if key:
            q += " AND patient_key=?"
            args.append(key)
        if kind:
            q += " AND kind=?"
            args.append(kind)
        rows = self.db.execute(q + " ORDER BY audit_id DESC", args).fetchall()
        return [{**dict(r), "detail": json.loads(r["detail_json"])} for r in rows]

    def last_evaluation(self, key: str) -> Optional[dict]:
        rows = self.audit_rows(key, "evaluation")
        return rows[0]["detail"] if rows else None

    # ---- outbox ---------------------------------------------------------------
    def outbox_add(self, payload: dict, at: str) -> None:
        self.db.execute("INSERT INTO outbox VALUES (?,?,?,?,?,?,?)",
                        (payload["handoff_id"], payload["idempotency_key"], payload["patient_key"],
                         payload["work_item"]["type"], payload["receiving_system"],
                         json.dumps(payload, default=str), at))

    def outbox_by_key(self, idem: str) -> Optional[dict]:
        r = self.db.execute("SELECT payload_json FROM outbox WHERE idempotency_key=?", (idem,)).fetchone()
        return json.loads(r[0]) if r else None

    def outbox(self, key: Optional[str] = None) -> list[dict]:
        q, args = "SELECT payload_json FROM outbox", ()
        if key:
            q, args = q + " WHERE patient_key=?", (key,)
        # rowid = insertion order (created_at has 1 s resolution, so ties are possible)
        return [json.loads(r[0]) for r in self.db.execute(q + " ORDER BY rowid", args)]

    # ---- deliveries + simulated receiver outages (Stretch) --------------------
    def add_delivery(self, handoff_id: str, attempt: int, at: str, result: str,
                     error: Optional[str] = None) -> None:
        self.db.execute("INSERT INTO deliveries (handoff_id, attempt, at, result, error) VALUES (?,?,?,?,?)",
                        (handoff_id, attempt, at, result, error))

    def deliveries(self, handoff_id: Optional[str] = None) -> list[dict]:
        q, args = "SELECT * FROM deliveries", ()
        if handoff_id:
            q, args = q + " WHERE handoff_id=?", (handoff_id,)
        return [dict(r) for r in self.db.execute(q + " ORDER BY seq", args)]

    def set_outage(self, receiving_system: str, down: bool, at: str, note: Optional[str] = None) -> None:
        self.db.execute("INSERT INTO receiver_outages (receiving_system, down, at, note, simulated) "
                        "VALUES (?,?,?,?,1)", (receiving_system, int(down), at, note))

    def outages(self) -> dict[str, bool]:
        out: dict[str, bool] = {}
        for r in self.db.execute("SELECT receiving_system, down FROM receiver_outages ORDER BY seq"):
            out[r[0]] = bool(r[1])
        return out

    def outbox_count(self, key: str, work_type: str) -> int:
        return self.db.execute("SELECT COUNT(*) FROM outbox WHERE patient_key=? AND work_item_type=?",
                               (key, work_type)).fetchone()[0]
