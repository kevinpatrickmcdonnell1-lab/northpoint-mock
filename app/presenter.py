"""Value lens: audience-facing callouts for the three layers of the demo.

Display copy only. Nothing here is read by the engine, and no expected outcome lives here
(CLAUDE.md invariant 1). Numbers quoted are Northpoint's own profile figures (PLAN §1.1).
"""
from __future__ import annotations

LAYERS = {
    1: {
        "title": "Document extraction",
        "tagline": "Facts, not conclusions. Every field cited to a file, a page and a quote.",
        "products": ["Intake Operations"],
        "changes": "The packet arrives split by document type with every field cited to its page. "
                   "Coordinators stop reconciling faxes page by page and touch exceptions only.",
        "measure": "Active handling minutes per order (18 today, a staff estimate) · "
                   "extraction correction rate as a guardrail",
        "assumption": "Reconciling packets is a real share of the 18 minutes.",
        "ask_role": "Intake",
        "ask": "How long does reconciling one fax packet take today, and which document is most often "
               "missing or wrong?",
    },
    2: {
        "title": "Policy engine",
        "tagline": "Your payer rules, applied at intake from versioned config. "
                   "Clinical urgency routed separately from admin work.",
        "products": ["Insurance Benefits Manager", "Qualification Review Engine", "Intake Operations routing"],
        "changes": "PA need and every criterion are checked when the order arrives, so document requests "
                   "go out before booking, not on the day. Clinical review is routed on its own lane.",
        "measure": "Late readiness disruptions by cause (10% today, 5% target) · "
                   "time to urgent-triage notification as a guardrail",
        "assumption": "Most of the 48 monthly disruptions are PA or document gaps findable at intake.",
        "ask_role": "Operations",
        "ask": "Of the 48 late disruptions, which plans and which missing documents come up most?",
    },
    3: {
        "title": "Case state & audit",
        "tagline": "One live state per order, recomputed from facts and events. "
                   "Nothing reaches scheduling unready.",
        "products": ["Prior Authorization Manager", "Communications Coordinator", "Epic scheduling handoff"],
        "changes": "One queue per owner with a next action. Human decisions are recorded as audited events, "
                   "and a hard gate stands before scheduling.",
        "measure": "Median admin readiness (5 business days today, 3 target) with aging · repeat touches · "
                   "zero orders handed over with an open gate",
        "assumption": "Unclear ownership, not effort, is what slows follow-up.",
        "ask_role": "IT & Integrations",
        "ask": "How do you match patients today, which Epic work queue would take this handoff, and who is "
               "paged when the interface fails?",
    },
}

PILOT = {
    "scope": "1–2 sites · stress echo only · 8–12 weeks · baseline from the prior 3 months · "
             "non-pilot sites as comparison",
    "primary": ["Late readiness disruption rate by cause (baseline 10%, aspiration 5%)",
                "Median admin readiness time (baseline 5 business days, aspiration 3) with aging"],
    "secondary": ["Handling-time sample before and after", "Repeat touches per order",
                  "Exception mix by reason code and owner"],
    "guardrails": ["Zero orders handed to scheduling with an open gate",
                   "Time from receipt to urgent-triage notification", "Extraction correction rate"],
    "bounded": "6 minutes off each of 600 orders ≈ 60 gross staff hours a month; 10% → 5% ≈ 24 fewer "
               "disrupted appointments a month. Bounded examples to test, not promises, and not revenue.",
}
