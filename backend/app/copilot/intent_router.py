"""Deterministic intent classification for the GridWise AI Energy Copilot.

Keyword/regex matching, not an LLM call, is deliberate: this decides
*which existing GridWise tool to invoke*, and that decision needs to be
instant, free, and 100% reproducible for the phrasings GridWise's own
quick-action buttons and demo script use. It is not attempting general
natural-language understanding — phrasing outside these patterns safely
falls through to GENERAL_ENERGY_QUERY rather than guessing which paid
tool (LLM interpretation, optimizer, simulator) to run.

Patterns use small character-count gaps (e.g. `\\bmodels?\\b.{0,20}\\b...`)
rather than exact adjacency, so ordinary phrasing variation ("models are
active" vs "models active") still classifies correctly.
"""

from __future__ import annotations

import re
from enum import Enum


class Intent(str, Enum):
    OPTIMIZATION_REQUEST = "OPTIMIZATION_REQUEST"
    EXPLANATION_REQUEST = "EXPLANATION_REQUEST"
    SIMULATION_REQUEST = "SIMULATION_REQUEST"
    STATUS_REQUEST = "STATUS_REQUEST"
    SCHEDULE_REQUEST = "SCHEDULE_REQUEST"
    APP_HELP_REQUEST = "APP_HELP_REQUEST"
    GENERAL_ENERGY_QUERY = "GENERAL_ENERGY_QUERY"


# Checked in this order: explanation/simulation/status phrasing often also
# contains words like "cost" or "battery" that would otherwise match the
# broader optimization patterns, so the more specific question-shaped
# intents are checked first.
_EXPLANATION_PATTERNS = [
    r"\bwhy\b",
    r"\bexplain(ed|ing|ation)?\b",
    r"\breason(ing)?\b",
    r"\bjustif",
]

_SIMULATION_PATTERNS = [
    r"\bwhat if\b",
    r"\bwhat happens\b",
    r"\bsimulat",
    r"\bsuppose\b",
    r"\bhypothetical",
    r"\bscenario where\b",
]

_STATUS_PATTERNS = [
    r"\bsystem\b.{0,20}\b(running|healthy|status|health|online|up)\b",
    r"\bis\b.{0,25}\b(running|healthy|online|up)\b",
    r"\bmodels?\b.{0,20}\b(active|available|healthy)\b",
    r"\boptimi[sz]ation\b.{0,15}\bhealthy\b",
    r"\bstatus\b",
    r"\bhealth ?check\b",
]

_SCHEDULE_PATTERNS = [
    r"\bwhat should i do\b",
    r"\baction (plan|schedule)\b",
    r"\bwhat (actions?|steps?)\b",
    r"\bwhen should i\b",
]

_APP_HELP_PATTERNS = [
    r"\bhow (do|can) i use\b",
    r"\bwhat can (i|you) do\b",
    r"\bhow does (this|gridwise)\b",
    r"\bwhere (can|do) i (see|find)\b",
    r"\bhelp (me )?(use|understand|navigate)\b",
    r"\buser guide\b",
]

_SECRET_PATTERNS = [r"\bsystem prompt\b", r"\bapi keys?\b", r"\benv(ironment)? var", r"\binternal prompt"]

_OPTIMIZATION_PATTERNS = [
    r"\boptimi[sz]e",
    r"\breduce\b.{0,20}\bcost\b",
    r"\bcheapest\b",
    r"\blower\b.{0,15}\b(bill|cost)\b",
    r"\bbattery schedule\b",
    r"\bsave\b.{0,15}\b(energy|money|cost)\b",
    r"\bminimi[sz]e\b.{0,15}\bcost\b",
]


def _matches_any(patterns: list[str], text: str) -> bool:
    return any(re.search(pattern, text) for pattern in patterns)


def classify_intent(message: str) -> Intent:
    """Classify a single chat message into one of the five supported
    Copilot intents. Never raises; unmatched or empty text safely falls
    back to GENERAL_ENERGY_QUERY."""
    text = (message or "").strip().casefold()
    if not text:
        return Intent.GENERAL_ENERGY_QUERY

    if _matches_any(_SECRET_PATTERNS, text) or _matches_any(_APP_HELP_PATTERNS, text):
        return Intent.APP_HELP_REQUEST
    if _matches_any(_SCHEDULE_PATTERNS, text):
        return Intent.SCHEDULE_REQUEST
    if _matches_any(_EXPLANATION_PATTERNS, text):
        return Intent.EXPLANATION_REQUEST
    if _matches_any(_SIMULATION_PATTERNS, text):
        return Intent.SIMULATION_REQUEST
    if _matches_any(_STATUS_PATTERNS, text):
        return Intent.STATUS_REQUEST
    if _matches_any(_OPTIMIZATION_PATTERNS, text):
        return Intent.OPTIMIZATION_REQUEST
    return Intent.GENERAL_ENERGY_QUERY
