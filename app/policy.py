"""Load and validate policy + workflow config. All rule dates come from here (invariant 10)."""
from __future__ import annotations

import hashlib
import re
from datetime import date, timedelta
from functools import cached_property
from pathlib import Path
from typing import Annotated, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app import rule_ids

ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = ROOT / "config" / "policy_sc_card_1_1.yaml"
WORKFLOW_PATH = ROOT / "config" / "workflow.yaml"

WindowName = Literal[
    "benefits_response", "pri2_material_increase", "resting_ecg", "exercise_ecg", "prior_stress_echo"
]


class ConfigError(ValueError):
    """A config file failed validation. Raised at load (startup), never at request time."""


# A window is a whole number of days > 0. strict=True rejects bools, floats and strings.
WindowDays = Annotated[int, Field(strict=True, gt=0)]


class _Cfg(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Effective(_Cfg):
    from_: date = Field(alias="from")
    to: date


class Plan(_Cfg):
    payer: str
    product: str
    pa: Literal["required", "not_required"]
    extra: list[str] = Field(default_factory=list)

    @field_validator("extra")
    @classmethod
    def _known_extra_rules(cls, v: list[str]) -> list[str]:
        bad = [r for r in v if r not in rule_ids.PLAN_EXTRA_RULES]
        if bad:
            raise ValueError(f"unknown plan rule ID(s) {bad}; allowed: {list(rule_ids.PLAN_EXTRA_RULES)}")
        return v


class Windows(_Cfg):
    benefits_response: WindowDays
    pri2_material_increase: WindowDays
    resting_ecg: WindowDays
    exercise_ecg: WindowDays
    prior_stress_echo: WindowDays


def normalize_product(raw: Optional[str]) -> str:
    """Case/whitespace/punctuation-insensitive. Does NOT strip words, so a missing
    product line ('Harborstone Health Commercial') stays unresolved."""
    if not raw:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", raw.lower())).strip()


class Policy(_Cfg):
    model_config = ConfigDict(extra="forbid", frozen=True, ignored_types=(cached_property,))

    policy_version: str
    effective: Effective
    eval_date: date
    windows: Windows
    plans: dict[str, Plan]
    product_aliases: dict[str, str]

    @model_validator(mode="after")
    def _check(self) -> "Policy":
        if not (self.effective.from_ <= self.eval_date <= self.effective.to):
            raise ValueError("eval_date outside policy effective range")
        for alias, plan_id in self.product_aliases.items():
            if plan_id not in self.plans:
                raise ValueError(f"alias {alias!r} -> unknown plan {plan_id}")
            if normalize_product(alias) != alias:
                raise ValueError(f"alias {alias!r} must be stored normalized")
        return self

    def window_start(self, name: WindowName) -> date:
        """Inclusive start date of a look-back window ending on eval_date."""
        return self.eval_date - timedelta(days=getattr(self.windows, name))

    def resolve_product(self, raw: Optional[str]) -> Optional[str]:
        """Exact-product alias lookup. Never infers from member-ID prefixes."""
        return self.product_aliases.get(normalize_product(raw))


class Workflow(_Cfg):
    routine_requires_clinical_clearance: bool
    no_pa_evidence_gate: list[str]
    sim_clock_start: str
    delivery_max_attempts: int = 3
    owner_roles: list[str]
    tennr_products: list[str]

    @field_validator("no_pa_evidence_gate")
    @classmethod
    def _known_gate_rules(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("must name at least one rule (an empty gate would pass with nothing "
                             "checked)")
        bad = [r for r in v if r not in rule_ids.EVIDENCE_RULES]
        if bad:
            raise ValueError(f"unknown rule ID(s) {bad}; allowed: {list(rule_ids.EVIDENCE_RULES)}")
        return v


def _load(model, path: Path, raw: bytes | None = None):
    raw = path.read_bytes() if raw is None else raw
    try:
        return model.model_validate(yaml.safe_load(raw))
    except ValidationError as e:
        lines = [f"  {'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()]
        raise ConfigError(f"{path.name} is invalid; fix it before starting:\n" + "\n".join(lines)) from None


def load_policy(path: Path = POLICY_PATH) -> Policy:
    return _load(Policy, path)


def load_workflow(path: Path = WORKFLOW_PATH) -> Workflow:
    return _load(Workflow, path)


def config_sha256(policy_bytes: bytes, workflow_bytes: bytes) -> str:
    """SHA-256 over both YAML files' exact bytes (labelled, so the split point is unambiguous)."""
    h = hashlib.sha256()
    for label, b in ((b"policy", policy_bytes), (b"workflow", workflow_bytes)):
        h.update(label + b"\0" + str(len(b)).encode() + b"\0" + b)
    return h.hexdigest()


def load_config(policy_path: Path = POLICY_PATH,
                workflow_path: Path = WORKFLOW_PATH) -> tuple[Policy, Workflow, str]:
    """Read each file once, validate it, and hash the same bytes that were validated."""
    pb, wb = policy_path.read_bytes(), workflow_path.read_bytes()
    return _load(Policy, policy_path, pb), _load(Workflow, workflow_path, wb), config_sha256(pb, wb)
