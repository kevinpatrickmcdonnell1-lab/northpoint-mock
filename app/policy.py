"""Load and validate policy + workflow config. All rule dates come from here (invariant 10)."""
from __future__ import annotations

import re
from datetime import date, timedelta
from functools import cached_property
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = ROOT / "config" / "policy_sc_card_1_1.yaml"
WORKFLOW_PATH = ROOT / "config" / "workflow.yaml"

WindowName = Literal[
    "benefits_response", "pri2_material_increase", "resting_ecg", "exercise_ecg", "prior_stress_echo"
]


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


class Windows(_Cfg):
    benefits_response: int
    pri2_material_increase: int
    resting_ecg: int
    exercise_ecg: int
    prior_stress_echo: int


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


def load_policy(path: Path = POLICY_PATH) -> Policy:
    return Policy.model_validate(yaml.safe_load(path.read_text()))


def load_workflow(path: Path = WORKFLOW_PATH) -> Workflow:
    return Workflow.model_validate(yaml.safe_load(path.read_text()))
