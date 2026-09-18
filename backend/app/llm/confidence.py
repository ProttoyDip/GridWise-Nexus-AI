"""Internal, deterministic agreement evidence; never an API response field."""

from collections import Counter
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.response import DirectiveInterpretation


class ConfidenceDecision(str, Enum):
    ACCEPT = "accept"
    VERIFY = "verify"
    ESCALATE = "escalate"


@dataclass(frozen=True)
class ConfidenceMetadata:
    confidence_score: float
    agreement_count: int
    models_used: tuple[str, ...]
    decision: ConfidenceDecision


def semantic_key(directive: "DirectiveInterpretation") -> tuple:
    """Compare directive meaning, ignoring explanation and note index."""
    adjustment = directive.structured_adjustment
    numeric_field = {
        "solar_reduction": "factor",
        "minimum_battery_reserve": "minimum_energy_kwh",
        "max_grid_window": "max_grid_kwh",
    }.get(directive.directive_type)
    # Python numeric equality treats 1 and 1.0 as the same bound. Only fields
    # consumed by the optimizer affect agreement, never arbitrary extra keys.
    return (
        directive.directive_type, directive.applies,
        tuple(adjustment["hours"]) if adjustment is not None else None,
        adjustment[numeric_field] if numeric_field is not None else None,
    )


def assess_confidence(
    votes: list[tuple[str, "DirectiveInterpretation"]], models_used: list[str],
) -> tuple["DirectiveInterpretation | None", ConfidenceMetadata]:
    """Use distinct provider/model votes, never counting retries as agreement.

    A single valid vote scores 0.6 (verify). A strict majority with at least
    two agreeing models scores agreement/valid_votes (accept). Ties and no
    valid evidence escalate. This is an evidence score, not a calibrated
    probability that a natural-language interpretation is correct.
    """
    distinct = dict(votes)
    if not distinct:
        return None, ConfidenceMetadata(0.0, 0, tuple(dict.fromkeys(models_used)), ConfidenceDecision.ESCALATE)
    candidates = list(distinct.values())
    keys = [semantic_key(candidate) for candidate in candidates]
    winner_key, count = Counter(keys).most_common(1)[0]
    winner = candidates[keys.index(winner_key)]
    score = count / len(candidates) if len(candidates) > 1 else 0.6
    if count >= 2 and score > 0.5:
        decision = ConfidenceDecision.ACCEPT
    elif len(candidates) == 1:
        decision = ConfidenceDecision.VERIFY
    else:
        decision = ConfidenceDecision.ESCALATE
    return winner, ConfidenceMetadata(score, count, tuple(dict.fromkeys(models_used)), decision)
