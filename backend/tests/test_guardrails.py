"""Deterministic guardrail tests; no LLM or optimizer is involved."""

from copy import deepcopy

import pytest

from app.guardrails.validator import validate_directive_interpretation
from app.models.response import DirectiveInterpretation


def directive(kind="solar_reduction", **updates):
    adjustment = {"hours": [0, 13, 23]}
    fields = {
        "solar_reduction": ("factor", 0.2),
        "minimum_battery_reserve": ("minimum_energy_kwh", 50),
        "max_grid_window": ("max_grid_kwh", 100),
    }
    if kind in fields:
        key, value = fields[kind]
        adjustment[key] = value
    data = dict(
        note_index=0, applies=kind != "no_op", directive_type=kind,
        structured_adjustment=None if kind == "no_op" else adjustment,
        explanation="Operator constraint.",
    )
    data.update(updates)
    return data


def assert_rejected(data):
    result = validate_directive_interpretation([data])[0]
    assert result.directive_type == "no_op"
    assert result.applies is False
    assert result.structured_adjustment is None
    assert result.note_index == 0
    assert "guardrail validation failed" in result.explanation


@pytest.mark.parametrize("kind", [
    "solar_reduction", "minimum_battery_reserve", "max_grid_window",
    "no_charge_window", "no_discharge_window", "no_op",
])
def test_valid_directives_are_preserved(kind):
    data = directive(kind)
    assert validate_directive_interpretation([data])[0].model_dump() == data


@pytest.mark.parametrize("kind", ["unknown", "SOLAR_REDUCTION", None, [], {}])
def test_disallowed_types(kind):
    assert_rejected(directive(directive_type=kind))


@pytest.mark.parametrize("hours", [
    [-1], [24], [1.0], [True], ["1"], [None], [1, 1], [2, 1],
    None, "13", 13, {"hour": 13}, (1, 2),
])
def test_invalid_hours(hours):
    assert_rejected(directive(structured_adjustment={"hours": hours, "factor": 0.2}))


@pytest.mark.parametrize("kind", [
    "solar_reduction", "minimum_battery_reserve", "max_grid_window",
    "no_charge_window", "no_discharge_window",
])
def test_hours_required_for_each_window(kind):
    data = directive(kind)
    del data["structured_adjustment"]["hours"]
    assert_rejected(data)


@pytest.mark.parametrize("applies", [True, 0, "false", None])
def test_no_op_requires_literal_false(applies):
    assert_rejected(directive("no_op", applies=applies))


def test_no_op_requires_explicit_null_adjustment():
    assert_rejected(directive("no_op", structured_adjustment={}))
    data = directive("no_op")
    del data["structured_adjustment"]
    assert_rejected(data)


@pytest.mark.parametrize("kind", [
    "solar_reduction", "minimum_battery_reserve", "max_grid_window",
    "no_charge_window", "no_discharge_window",
])
@pytest.mark.parametrize("applies", [False, 1, "true", None])
def test_active_directives_require_literal_true(kind, applies):
    assert_rejected(directive(kind, applies=applies))


@pytest.mark.parametrize("kind,field", [
    ("solar_reduction", "factor"),
    ("minimum_battery_reserve", "minimum_energy_kwh"),
    ("max_grid_window", "max_grid_kwh"),
])
@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), -float("inf"), True, "5", None, []])
def test_impossible_numeric_values(kind, field, value):
    data = directive(kind)
    data["structured_adjustment"][field] = value
    assert_rejected(data)


@pytest.mark.parametrize("kind,field", [
    ("solar_reduction", "factor"),
    ("minimum_battery_reserve", "minimum_energy_kwh"),
    ("max_grid_window", "max_grid_kwh"),
])
def test_required_numeric_fields(kind, field):
    data = directive(kind)
    del data["structured_adjustment"][field]
    assert_rejected(data)


@pytest.mark.parametrize("adjustment", [None, [], "invalid"])
def test_active_directives_require_adjustment_object(adjustment):
    assert_rejected(directive(structured_adjustment=adjustment))


@pytest.mark.parametrize("value", [1.01, 100])
def test_solar_factor_cannot_exceed_one(value):
    assert_rejected(directive(structured_adjustment={"hours": [13], "factor": value}))


@pytest.mark.parametrize("kind,field,value", [
    ("solar_reduction", "factor", 0), ("solar_reduction", "factor", 1),
    ("minimum_battery_reserve", "minimum_energy_kwh", 0),
    ("max_grid_window", "max_grid_kwh", 0),
])
def test_numeric_boundaries(kind, field, value):
    data = directive(kind)
    data["structured_adjustment"][field] = value
    assert validate_directive_interpretation([data])[0].model_dump() == data


@pytest.mark.parametrize("data", [None, [], "not an object", {}, directive(note_index=True), directive(explanation="")])
def test_malformed_entries_fail_safe(data):
    assert_rejected(data)


def test_preserves_order_isolates_failures_and_does_not_mutate_input():
    data = [directive(), directive(note_index=0), directive("no_op", note_index=2)]
    original = deepcopy(data)
    result = validate_directive_interpretation(data)
    assert [item.note_index for item in result] == [0, 1, 2]
    assert [item.directive_type for item in result] == ["solar_reduction", "no_op", "no_op"]
    assert result[2].explanation == data[2]["explanation"]
    assert data == original


def test_interpreter_models_are_revalidated():
    model = DirectiveInterpretation.model_validate(directive())
    assert validate_directive_interpretation([model])[0] == model
    model.structured_adjustment["hours"] = [True]
    assert_rejected(model)


def test_empty_input():
    assert validate_directive_interpretation([]) == []
