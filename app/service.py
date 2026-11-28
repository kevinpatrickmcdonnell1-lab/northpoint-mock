"""Application service: wires store + engine + simulated events + audit.

The engine stays pure; this layer does the I/O. Only explicit calls to `simulate()`
(from a labelled UI control or a test) ever create events.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from app.engine import evaluate, state_hash
from app import fact_review as fr
from app.models import BenefitsResponseFields, CaseState, ExtractionFile, SimEvent
from app.pdfs import extractions_dir as active_extractions_dir, load_extractions
from app.policy import POLICY_PATH, WORKFLOW_PATH, Policy, Workflow, load_config
from app.store import Store

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "northpoint.db"
DEFAULT_OUTBOX = ROOT / "outbox"

ALLOWED_ROLES = {
    "PAYER_PA_DECISION": ["Auth Specialist"],
    "CLINICAL_REVIEW_DECISION": ["Clinical Triage RN", "Clinical Review RN", "Cardiologist"],
    "IDENTITY_RESOLUTION": ["Intake Coordinator"],
    "BENEFITS_RESPONSE": ["Insurance Benefits Manager (mock)"],
}


def wall_clock() -> str:
    return datetime.now().isoformat(timespec="seconds")


def summarize(s: CaseState) -> dict[str, Any]:
    return {
        "priority": s.priority.status, "identity": s.identity.status,
        "coverage": s.coverage.status, "plan": s.plan.detail.get("plan_id"),
        "pa_requirement": s.pa_requirement,
        "criteria": {k: v.status for k, v in s.criteria.items()},
        "failing_gates": [g.gate for g in s.gates if not g.passed],
        "actions": [a.reason_code for a in s.actions],
        # R6: the audit records who owns the next step, not only the reason codes.
        "next_actions": [{"reason_code": a.reason_code, "owner_role": a.owner_role,
                          "tennr_product": a.tennr_product} for a in s.actions],
        "next_owner": s.actions[0].owner_role if s.actions else None,
        "pa_prep_ready": s.pa_prep_ready, "scheduling_sendable": s.scheduling_sendable,
        "admin_ready": s.admin_ready,
        "evidence_refs": {k: [f"{r.file} p.{r.page}" for r in v.evidence_refs]
                          for k, v in s.criteria.items()},
        "simulated_event_ids": s.simulated_event_ids,
    }


def diff(prev: Optional[dict], cur: dict) -> dict:
    if prev is None:
        return {"initial": True}
    out = {}
    for k in cur:
        if k == "evidence_refs":
            continue
        if prev.get(k) != cur[k]:
            out[k] = {"from": prev.get(k), "to": cur[k]}
    return out


class Service:
    def __init__(self, db_path: Path | str = DEFAULT_DB, outbox_dir: Path = DEFAULT_OUTBOX,
                 policy: Optional[Policy] = None, workflow: Optional[Workflow] = None,
                 extractions_dir: Path | None = None,
                 policy_path: Path = POLICY_PATH, workflow_path: Path = WORKFLOW_PATH):
        # Validate config first, so a bad YAML fails at startup with a ConfigError (Fix 5).
        loaded_policy, loaded_workflow, self.config_sha256 = load_config(policy_path, workflow_path)
        self.store = Store(db_path)
        # One writer at a time: event IDs and handoff IDs are numbered from counts, so two
        # simultaneous clicks would otherwise collide on a UNIQUE key (sqlite IntegrityError).
        self.lock = threading.RLock()
        self.outbox_dir = Path(outbox_dir)
        self.policy = policy or loaded_policy
        self.workflow = workflow or loaded_workflow
        if policy or workflow:
            self.config_sha256 = "in-memory override (not from the YAML files)"
        self.extractions_dir = extractions_dir or active_extractions_dir()

    # ---- setup ----------------------------------------------------------------
    def seed(self, evaluate: bool = True) -> None:
        """Load the packets' extracted facts. With evaluate=False the packets have arrived
        but the engine has not run yet (the demo's starting state after Reset)."""
        if self.store.keys():
            return
        for key, ef in load_extractions(self.extractions_dir).items():
            self.store.load_case(ef, wall_clock())
        if evaluate:
            self.evaluate_all(actor="system (seed)")

    def engine_has_run(self) -> bool:
        return self.store.db.execute(
            "SELECT 1 FROM audit_log WHERE kind='evaluation' LIMIT 1").fetchone() is not None

    # ---- facts: source + human corrections ------------------------------------
    def case(self, key: str) -> ExtractionFile:
        """The reviewed facts: the immutable source extraction with every human correction laid
        over it. Everything that evaluates or hands off reads this; `store.case` is the source."""
        return fr.apply_reviews(self.store.case(key), self.store.reviews(key))

    def flags(self, key: str) -> list[fr.Flag]:
        return fr.check_facts(self.case(key))

    def open_flags(self, key: str) -> list[fr.Flag]:
        return fr.open_flags(self.flags(key), self.store.reviews(key))

    # ---- evaluation -----------------------------------------------------------
    def state(self, key: str) -> CaseState:
        return evaluate(self.case(key), self.store.events(key), self.policy, self.workflow)

    def states(self) -> list[CaseState]:
        events = self.store.events()
        return [evaluate(self.case(k), events, self.policy, self.workflow) for k in self.store.keys()]

    def evaluate_and_record(self, key: str, actor: str = "system", kind: str = "evaluation") -> CaseState:
        s = self.state(key)
        cur = summarize(s)
        prev = self.store.last_evaluation(key)
        changes = diff(prev, cur)
        detail = {"policy_version": s.policy_version, "config_sha256": self.config_sha256,
                  "eval_date": str(s.eval_date),
                  "state_hash": state_hash(s), **cur, "diff": changes}
        self.store.audit(key, wall_clock(), actor, "evaluation", detail)
        if kind == "state_change" and changes:
            self.store.audit(key, wall_clock(), actor, "state_change", {"diff": changes})
        return s

    def evaluate_all(self, actor: str = "system") -> list[CaseState]:
        return [self.evaluate_and_record(k, actor) for k in self.store.keys()]

    # ---- human fact review (explicit action only) -------------------------------
    def review_fact(self, key: str, doc_id: str, field: str, action: str, actor_role: str,
                    actor_name: str, reason: Optional[str] = None, value: Optional[str] = None,
                    quote_text: Optional[str] = None, quote_page: Optional[int] = None) -> fr.FactReview:
        """Record that a person confirmed or corrected one fact. Every refusal raises ValueError
        before anything is written. A correction must quote the page text that supports it."""
        ef = self.case(key)
        doc = next((d for d in ef.documents if d.doc_id == doc_id), None)
        if doc is None:
            raise ValueError(f"No document {doc_id} in {key}.")
        if actor_role not in fr.REVIEW_ROLES:
            raise ValueError(f"{actor_role or 'No role'} cannot review facts "
                             f"(allowed: {', '.join(fr.REVIEW_ROLES)}).")
        if not (actor_name or "").strip():
            raise ValueError("A fact review needs the reviewer's name.")
        if field in fr.IDENTIFIER_FIELDS:
            raise ValueError(f"{field} is an identifier. Disagreeing identifiers are settled by "
                             "Identity resolution on the case page, never by editing what a page says.")
        quoted = {q.field for q in doc.quotes}
        allowed = set(fr.correctable_fields(doc.doc_type)) | (quoted if action == "confirm" else set())
        if field not in allowed:
            raise ValueError(f"{field} is not a fact on {doc_id} ({doc.doc_type}).")
        if fr.is_clinical(field) and actor_role not in fr.CLINICAL_ROLES:
            raise ValueError(f"{field} is a clinical fact: only {', '.join(fr.CLINICAL_ROLES)} "
                             "may confirm or correct it.")
        reason = (reason or "").strip() or None
        before = fr.current_value(doc, field) if field in fr.correctable_fields(doc.doc_type) else None
        after = None
        if action == "correct":
            if not (quote_text or "").strip():
                raise ValueError("A correction needs the page text that supports the new value.")
            if quote_page not in doc.pages:
                raise ValueError(f"p.{quote_page} is not one of this document's pages {doc.pages}.")
            if not fr.quote_on_page(quote_text, ef.source_file, quote_page):
                raise ValueError(f"That text was not found on p.{quote_page}. Copy it exactly from the page.")
            if not reason:
                raise ValueError("A correction needs a reason.")
            after = fr.validate_correction(ef, doc_id, field, value or "", quote_page, quote_text.strip())
        elif action == "confirm":
            if not reason and any(f.doc_id == doc_id and f.field == field for f in self.open_flags(key)):
                raise ValueError(f"{field} is flagged; say why it is right in the note before confirming.")
            value = quote_text = quote_page = None
        else:
            raise ValueError(f"Unknown review action {action!r}.")
        r = fr.FactReview(
            review_id=f"FR-{self.store.review_count() + 1:04d}", patient_key=key, doc_id=doc_id,
            field=field, action=action, value=after, reason=reason,
            quote_text=quote_text.strip() if quote_text else None, quote_page=quote_page,
            actor_role=actor_role, actor_name=actor_name.strip(),
            recorded_at=datetime.now().replace(microsecond=0))
        self.store.add_review(r)
        self.store.audit(key, wall_clock(), actor_role, "correction",
                         {**r.model_dump(mode="json"), "before": before,
                          "after": after if action == "correct" else before})
        if action == "correct":
            self.evaluate_and_record(key, actor=f"system (after {r.review_id})", kind="state_change")
            from app.handoffs import recall_if_needed
            recall_if_needed(self, key)
        return r

    # ---- work-item claims ---------------------------------------------------------
    def claim_holder(self, key: str, code: str) -> Optional[dict]:
        """Who is working this item now: the latest claim or takeover, unless released since."""
        holder = None
        for c in self.store.claims(key):
            if c["code"] == code:
                holder = None if c["action"] == "release" else c
        return holder

    def holders(self) -> dict[tuple[str, str], dict]:
        out: dict[tuple[str, str], dict] = {}
        for c in self.store.claims():
            k = (c["patient_key"], c["code"])
            if c["action"] == "release":
                out.pop(k, None)
            else:
                out[k] = c
        return out

    def claim(self, key: str, code: str, actor_role: str, actor_name: str, *, takeover: bool = False,
              release: bool = False, note: Optional[str] = None) -> str:
        """Claim, take over or release a work item. Raises ValueError (nothing recorded) when
        someone else holds it, a takeover has no reason, or a non-holder tries to release."""
        h = self.claim_holder(key, code)
        mine = h is not None and h["actor_name"] == actor_name and h["actor_role"] == actor_role
        note = (note or "").strip() or None
        if release:
            if not mine:
                raise ValueError("only the person holding an item can release it.")
            action = "release"
        elif h is None or mine:
            if mine:
                return "claim"          # already yours: nothing new to record
            action = "claim"
        elif not takeover:
            raise ValueError(f"claimed by {h['actor_name']} ({h['actor_role']}) at {h['at']}.")
        elif not note:
            raise ValueError("taking over someone's item needs a reason.")
        else:
            action = "takeover"
        self.store.add_claim(key, code, action, actor_role, actor_name, wall_clock(), note)
        return action

    # ---- simulated events (explicit action only) ------------------------------
    def next_event_time(self) -> datetime:
        start = datetime.fromisoformat(self.workflow.sim_clock_start)
        return start + timedelta(minutes=30 * self.store.event_count())

    def simulate(self, key: str, type_: str, decision: str, actor_role: str,
                 actor_name: Optional[str] = None, note: Optional[str] = None,
                 confirmed: Optional[dict[str, str]] = None, epic_reference: Optional[str] = None,
                 benefits: Optional[dict] = None) -> SimEvent:
        if actor_role not in ALLOWED_ROLES.get(type_, []):
            raise ValueError(f"{actor_role} cannot record {type_} (allowed: {ALLOWED_ROLES.get(type_)})")
        self.store.case(key)  # must exist
        pa_handoff_id = None
        if type_ == "PAYER_PA_DECISION":
            # Fix 2: a payer can only decide on a PA request that was actually submitted.
            packets = [o for o in self.store.outbox(key) if o["work_item"]["type"] == "pa_request_packet"]
            if not packets:
                raise ValueError(f"No PA request packet has been sent for {key}, so there is no "
                                 "payer decision to record. Prepare the PA packet first.")
            pa_handoff_id = packets[-1]["handoff_id"]
        ev = SimEvent(
            event_id=f"EV-{self.store.event_count() + 1:04d}", patient_key=key, type=type_,
            decision=decision, actor_role=actor_role, actor_name=actor_name or None,
            event_time=self.next_event_time(), recorded_at=datetime.now().replace(microsecond=0),
            note=note or None, confirmed=confirmed or None, epic_reference=epic_reference or None,
            benefits=BenefitsResponseFields.model_validate(benefits) if benefits else None,
            pa_handoff_id=pa_handoff_id,
        )
        self.store.add_event(ev)
        self.store.audit(key, wall_clock(), actor_role, "sim_event",
                         {"simulated": True, **ev.model_dump(mode="json")})
        self.evaluate_and_record(key, actor=f"system (after {ev.event_id})", kind="state_change")
        # Fix 4: a case that loses readiness after a scheduling handoff is recalled (service
        # layer, not the engine: the engine stays pure and never records anything).
        from app.handoffs import recall_if_needed
        recall_if_needed(self, key)
        return ev
