"""Scenario-aware physical checks and conservative, lossless corrections.

Sort/deduplicate hours and decode plain JSON numeric strings. Never infer
percentages, drop invalid hours, or clamp impossible physical quantities.
"""

from __future__ import annotations

from copy import deepcopy
import json
import math
from typing import Any

from app.models.request import BatteryConfig
from app.models.response import DirectiveInterpretation

NUMERIC_FIELDS = {
    "solar_reduction": "factor",
    "minimum_battery_reserve": "minimum_energy_kwh",
    "max_grid_window": "max_grid_kwh",
}


def _number(value: Any) -> int | float:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, RecursionError) as exc:
            raise ValueError("Expected a plain numeric value") from exc
    if type(value) not in (int, float):
        raise ValueError("Numbers must not be booleans or containers")
    try:
        if not math.isfinite(value):
            raise ValueError("Numbers must be finite")
    except OverflowError as exc:
        raise ValueError("Number exceeds the finite numeric range") from exc
    return value


def correct_physical_values(data: dict) -> dict:
    """Normalize representation without changing physical intent.

    Raises on ambiguous/invalid values; full physical bounds are checked
    separately with battery context. Input dictionaries are never mutated.
    """
    result = deepcopy(data)
    if result.get("directive_type") == "no_op":
        return result
    adjustment = result.get("structured_adjustment")
    if not isinstance(adjustment, dict):
        raise ValueError("Active directives require an adjustment object")
    hours = adjustment.get("hours")
    if not isinstance(hours, list) or not hours:
        raise ValueError("Active directives require a nonempty hours list")
    normalized = []
    for hour in hours:
        number = _number(hour)
        if not 0 <= number <= 23 or int(number) != number:
            raise ValueError("Hours must be integers from 0 through 23")
        normalized.append(int(number))
    adjustment["hours"] = sorted(set(normalized))
    field = NUMERIC_FIELDS.get(result.get("directive_type"))
    if field:
        adjustment[field] = _number(adjustment.get(field))
    return result


def validate_physical_directive(
    directive: dict[str, Any] | DirectiveInterpretation,
    battery: BatteryConfig,
    note_index: int,
) -> DirectiveInterpretation:
    """Return a guarded directive or a same-position safe no_op."""
    # Local import lets the schema validator expose this as an optional layer.
    from app.guardrails.validator import validate_directive

    original = directive.model_dump() if isinstance(directive, DirectiveInterpretation) else directive
    try:
        if not isinstance(original, dict):
            raise ValueError("Directive must be an object")
        corrected = correct_physical_values(original)
        result = validate_directive(corrected, note_index)
        adjustment = result.structured_adjustment
        if result.directive_type == "solar_reduction":
            if not 0 < adjustment["factor"] <= 1:
                raise ValueError("Solar factor must satisfy 0 < factor <= 1")
        elif result.directive_type == "minimum_battery_reserve":
            capacity = _number(battery.capacity_kwh)
            if capacity <= 0 or not 0 <= adjustment["minimum_energy_kwh"] <= capacity:
                raise ValueError("Battery reserve must be between zero and capacity")
        elif result.directive_type == "max_grid_window":
            if adjustment["max_grid_kwh"] < 0:
                raise ValueError("Grid limit must be non-negative")
        if corrected != original:
            result.explanation += " [physical guardrails: normalized numeric/hour formatting]"
        elif isinstance(directive, DirectiveInterpretation):
            result._confidence_metadata = deepcopy(directive._confidence_metadata)
        return result
    except (ValueError, TypeError, OverflowError, RecursionError) as exc:
        return DirectiveInterpretation(
            note_index=note_index, applies=False, directive_type="no_op",
            structured_adjustment=None,
            explanation=f"Falling back to no_op: physical guardrail validation failed ({exc})",
        )


def validate_physical_directives(
    directives: list[dict[str, Any] | DirectiveInterpretation], battery: BatteryConfig,
) -> list[DirectiveInterpretation]:
    return [validate_physical_directive(directive, battery, index)
            for index, directive in enumerate(directives)]
