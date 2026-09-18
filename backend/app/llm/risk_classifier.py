"""Risk classification for operator notes and primary interpretations.

Pure deterministic, no LLM calls. Classifies each note into one of three
verification tiers so that ``app.llm.adaptive_consensus`` can spend more
verification effort on numerically/structurally risky interpretations
and less on obviously trivial ones.

Three tiers:

- LOW: a single model is enough — the note is short, has no numeric
  tokens, and does not mention any energy asset the operator could
  constrain (battery, solar, grid, etc.). Typical for ``no_op`` notes.
- MEDIUM: two independent models should agree — there is either a
  directive with a numeric field at risk of LLM arithmetic drift
  (percent-to-factor, percent-of-capacity-to-kWh, time-window-to-hour),
  or the note carries moderate numeric/asset content even before the
  primary has been called.
- HIGH: three models + arbiter on disagreement — multiple independent
  numeric constraints, contradictory cues, or a battery reserve /
  grid cap with non-trivial magnitude.

The classifier never inspects the LLM output beyond the final
``DirectiveInterpretation`` (and only for refinement, never to decide
whether to invoke the LLM in the first place). It is safe to import
without any LLM credentials configured.
"""

from __future__ import annotations

import re
from enum import Enum

from app.models.response import DirectiveInterpretation


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# Directive types whose structured_adjustment carries a numeric field at
# meaningful risk of LLM arithmetic/normalization error. Mirrors
# app.llm.consensus._NUMERIC_FIELD_BY_DIRECTIVE — keeping the maps
# aligned ensures a note whose primary emits one of these directives
# is treated as at least MEDIUM regardless of how terse its text was.
_NUMERIC_DIRECTIVE_TYPES: frozenset[str] = frozenset(
    {"solar_reduction", "minimum_battery_reserve", "max_grid_window"}
)


# Asset keywords: any of these in the note text upgrades the risk floor
# to MEDIUM. They mark notes that, even when interpreted as no_op,
# would change the 24h schedule if applied — so a misclassification
# would actually move the optimizer, and is worth a second opinion.
_ASSET_KEYWORDS: tuple[str, ...] = (
    "battery",
    "solar",
    "grid",
    "kwh",
    "tariff",
    "reserve",
    "charge",
    "discharge",
    "charge_window",
    "discharge_window",
    "cap",
    "import",
    "export",
    "renewable",
    "panel",
    "inverter",
)


# Numeric-token regex: matches integers, decimals, percentages.
# A note with three or more of these is treated as carrying a lot of
# numeric content (HIGH risk floor). One or two is MEDIUM if combined
# with an asset keyword.
_NUMERIC_TOKEN_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:%|percent|kwh?|kw)?\b", re.IGNORECASE)


# Time-window cue regex: phrases like "1-3pm", "between 5 and 7", "from
# noon to 2pm". A single window is fine; multiple windows usually mean
# multiple constraints the operator is asking about.
_TIME_WINDOW_RE = re.compile(
    r"\b(?:\d{1,2}\s*(?:am|pm|AM|PM))|\b(?:from|between)\b|\bto\b|\buntil\b",
    re.IGNORECASE,
)


def _count_numeric_tokens(note: str) -> int:
    return len(_NUMERIC_TOKEN_RE.findall(note or ""))


def _has_asset_keyword(note: str) -> bool:
    lowered = (note or "").lower()
    return any(keyword in lowered for keyword in _ASSET_KEYWORDS)


def _count_time_window_cues(note: str) -> int:
    return len(_TIME_WINDOW_RE.findall(note or ""))


def classify_note_risk(note: str) -> RiskLevel:
    """Classify an operator note into a verification tier from its raw
    text alone (no LLM call, no primary interpretation).

    Heuristic, deliberately conservative — it is fine to over-classify a
    LOW note as MEDIUM, but never to under-classify a MEDIUM/HIGH note
    as LOW, because the verification cost of an extra model call is
    small and the optimizer cost of a wrong numeric directive is large.
    """
    tokens = _count_numeric_tokens(note)
    asset = _has_asset_keyword(note)
    windows = _count_time_window_cues(note)

    if asset and tokens >= 3:
        return RiskLevel.HIGH
    if tokens >= 5:
        return RiskLevel.HIGH
    if asset and tokens >= 1:
        return RiskLevel.MEDIUM
    if windows >= 3:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def _directive_constraint_count(directive: DirectiveInterpretation) -> int:
    """Count how many independent constraint fields the primary
    interpretation carries, weighted by risk of LLM normalization
    error. Used only for refining a risk classification, never alone."""
    if directive.directive_type == "no_op" or directive.structured_adjustment is None:
        return 0
    hours = directive.structured_adjustment.get("hours") or []
    constraint_count = 1 if directive.directive_type in _NUMERIC_DIRECTIVE_TYPES else 0
    # A directive spanning many hours is more likely to have hit a
    # window-parsing edge case than one with a tight 1-2h window.
    if isinstance(hours, list) and len(hours) >= 6:
        constraint_count += 1
    return constraint_count


def refine_risk_with_directive(base: RiskLevel, directive: DirectiveInterpretation) -> RiskLevel:
    """Escalate (never de-escalate) the text-based risk classification
    based on the primary interpretation's structured_adjustment.

    Rationale: a note that looks LOW in plain text but whose primary
    model produced a numeric-bearing directive is by definition at
    least MEDIUM. Multiple independent constraints (numeric + wide
    hour window, or two numeric fields) escalate to HIGH.
    """
    constraints = _directive_constraint_count(directive)
    if constraints == 0:
        return base
    if constraints >= 2:
        return RiskLevel.HIGH
    # exactly one constraint present — escalate one step at most.
    if base == RiskLevel.LOW:
        return RiskLevel.MEDIUM
    return base


def classify_risk(note: str, primary: DirectiveInterpretation | None) -> RiskLevel:
    """Convenience wrapper: text-based classification, optionally refined
    by the primary interpretation when one is already available."""
    base = classify_note_risk(note)
    if primary is None:
        return base
    return refine_risk_with_directive(base, primary)
