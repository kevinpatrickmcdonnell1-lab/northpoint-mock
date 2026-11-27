"""Single registry of rule IDs, imported by policy validation and by the rules (Fix 5).

Config may only name IDs from here; a typo fails at load time instead of failing open.
"""
from __future__ import annotations

PRIORITY_TIERS = ("PRI-1", "PRI-2", "PRI-3", "PRI-4")
ID_1 = "ID-1"
COV_1 = "COV-1"
PLAN = "PLAN"

DOC_1 = "DOC-1"
QUAL_1 = "QUAL-1"
QUAL_2 = "QUAL-2"
REPEAT_1 = "REPEAT-1"
HMO_1 = "HMO-1"
EVIDENCE = "EVIDENCE"            # the common evidence test (derived from the rules above)

# Criteria rules evaluated for every case, in display order. Valid in no_pa_evidence_gate.
EVIDENCE_RULES = (DOC_1, QUAL_1, QUAL_2, REPEAT_1, HMO_1)
# Rules that apply only when a plan lists them under plans.<id>.extra.
PLAN_EXTRA_RULES = (HMO_1,)

ALL_RULE_IDS = (*PRIORITY_TIERS, ID_1, COV_1, PLAN, *EVIDENCE_RULES, EVIDENCE)
