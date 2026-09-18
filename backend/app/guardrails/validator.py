"""Guardrail validation for LLM directive output.

Checks directive_type membership, note_index completeness/uniqueness,
ascending-unique hour arrays within 0-23, numeric ranges (solar factor
in [0,1], non-negative reserve/grid-cap values), and applies semantics
(no_op => applies=False, all others => applies=True) per Problem
Statement Section 08. Must fail safe (fallback to no_op) rather than
raise on malformed model output.
"""

from __future__ import annotations

import math
from typing import Any, get_args

from app.models.response import DirectiveInterpretation, DirectiveType


def _check_directive(data: dict[str, Any], note_index: int) -> None:
    if data.get("directive_type") not in get_args(DirectiveType):
        raise ValueError("directive_type is not allowed")
    if type(data.get("note_index")) is not int or data["note_index"] != note_index:
        raise ValueError("note_index must match the directive's position")

    directive_type = data["directive_type"]
    if directive_type == "no_op":
        if data.get("applies") is not False:
            raise ValueError("no_op requires applies=false")
        if "structured_adjustment" not in data or data["structured_adjustment"] is not None:
            raise ValueError("no_op requires structured_adjustment=null")
        return

    if data.get("applies") is not True:
        raise ValueError("other directives require applies=true")
    adjustment = data.get("structured_adjustment")
    if not isinstance(adjustment, dict):
        raise ValueError("structured_adjustment must be an object")
    hours = adjustment.get("hours")
    if not isinstance(hours, list) or not all(type(h) is int and 0 <= h <= 23 for h in hours):
        raise ValueError("hours must be a list of integers from 0 through 23")
    if any(left >= right for left, right in zip(hours, hours[1:])):
        raise ValueError("hours must be unique and ascending")

    numeric_field = {
        "solar_reduction": "factor",
        "minimum_battery_reserve": "minimum_energy_kwh",
        "max_grid_window": "max_grid_kwh",
    }.get(directive_type)
    if numeric_field is not None:
        value = adjustment.get(numeric_field)
        if type(value) not in (int, float):
            raise ValueError(f"{numeric_field} must be a number")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"{numeric_field} must be finite")
        if value < 0 or (numeric_field == "factor" and value > 1):
            raise ValueError(f"{numeric_field} is outside its allowed range")


def validate_directive(
    directive: dict[str, Any] | DirectiveInterpretation, note_index: int,
) -> DirectiveInterpretation:
    """Strictly validate one candidate, raising on malformed output."""
    data = directive.model_dump() if isinstance(directive, DirectiveInterpretation) else directive
    if not isinstance(data, dict):
        raise ValueError("directive must be an object")
    _check_directive(data, note_index)
    result = DirectiveInterpretation.model_validate(data, strict=True)
    if isinstance(directive, DirectiveInterpretation):
        result._confidence_metadata = directive._confidence_metadata
    return result


def validate_directive_interpretation(
    directives: list[dict[str, Any] | DirectiveInterpretation],
) -> list[DirectiveInterpretation]:
    """Validate decoded LLM output or interpreter results without mutating them.

    Supply one entry per operator note, in note order. Invalid entries become
    safe no_op directives at the same position; valid entries retain their
    content. Every result is freshly validated, including supplied model
    instances. Bounds are intrinsic: factor in [0, 1], finite non-negative
    energy values. Scenario feasibility belongs to the optimizer/verifier.
    """
    validated = []
    for note_index, directive in enumerate(directives):
        try:
            validated.append(validate_directive(directive, note_index))
        except (ValueError, TypeError) as exc:
            validated.append(
                DirectiveInterpretation(
                    note_index=note_index,
                    applies=False,
                    directive_type="no_op",
                    structured_adjustment=None,
                    explanation=f"Falling back to no_op: guardrail validation failed ({exc})",
                )
            )
    return validated
