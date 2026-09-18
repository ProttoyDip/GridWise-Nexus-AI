"""Untrusted LLM values must never become impossible optimizer constraints."""

from copy import deepcopy
import json

import pytest
from fastapi.testclient import TestClient

from app.api import optimize
from app.guardrails.physics_validator import validate_physical_directive, validate_physical_directives
from app.guardrails.validator import validate_directive_interpretation
from app.llm import interpreter
from app.main import app
from app.models.response import DirectiveInterpretation
from app.models.request import ScenarioRequest
from app.verifier.schedule_checker import verify_schedule
from test_solver import battery, forecast


def directive(kind="solar_reduction", value=0.2, hours=None, index=0):
    fields = {"solar_reduction": "factor", "minimum_battery_reserve": "minimum_energy_kwh", "max_grid_window": "max_grid_kwh"}
    adjustment = {"hours": [12, 13] if hours is None else hours}
    if kind in fields:
        adjustment[fields[kind]] = value
    return dict(note_index=index, applies=kind != "no_op", directive_type=kind,
                structured_adjustment=adjustment if kind != "no_op" else None,
                explanation="LLM interpretation")


def guarded(data):
    return validate_physical_directive(data, battery(), 0)


@pytest.mark.parametrize("kind,value", [
    ("solar_reduction", 0), ("solar_reduction", -0.1), ("solar_reduction", 1.01),
    ("minimum_battery_reserve", -1), ("minimum_battery_reserve", 10.01),
    ("max_grid_window", -1),
])
def test_impossible_values_are_not_clamped(kind, value):
    result = guarded(directive(kind, value))
    assert result.directive_type == "no_op"
    assert result.applies is False and result.structured_adjustment is None


@pytest.mark.parametrize("kind,value", [
    ("solar_reduction", 1), ("solar_reduction", 0.00001),
    ("minimum_battery_reserve", 0), ("minimum_battery_reserve", 10),
    ("max_grid_window", 0), ("max_grid_window", 100),
])
def test_physical_boundary_values_preserved(kind, value):
    data = directive(kind, value)
    assert guarded(data).model_dump() == data


@pytest.mark.parametrize("kind", ["solar_reduction", "minimum_battery_reserve", "max_grid_window"])
@pytest.mark.parametrize("value", [True, False, None, float("nan"), float("inf"), -float("inf"), 10**400, [], {}, "NaN", "Infinity", "20%", "one"])
def test_invalid_numeric_llm_values_fail_safe(kind, value):
    assert guarded(directive(kind, value)).directive_type == "no_op"


@pytest.mark.parametrize("hours", [[-1], [24], [1.5], [True], [False], [None], ["noon"], ["1.5"], [], "12", [float("nan")], [10**400], [12, 24]])
def test_invalid_hours_do_not_get_dropped_or_clipped(hours):
    assert guarded(directive(hours=hours)).directive_type == "no_op"


@pytest.mark.parametrize("kind,value", [("solar_reduction", "0.2"), ("minimum_battery_reserve", "10"), ("max_grid_window", "0")])
def test_safe_corrections_preserve_meaning_and_input(kind, value):
    data = directive(kind, value, ["13", 12.0, 13, 12])
    original = deepcopy(data)
    result = guarded(data)
    assert result.directive_type == kind
    assert result.structured_adjustment["hours"] == [12, 13]
    numeric = next(v for k, v in result.structured_adjustment.items() if k != "hours")
    assert type(numeric) in (int, float)
    assert "normalized" in result.explanation
    assert data == original
    assert validate_physical_directive(result, battery(), 0).model_dump() == result.model_dump()


@pytest.mark.parametrize("kind", ["no_charge_window", "no_discharge_window"])
def test_window_directives_share_hour_correction(kind):
    assert guarded(directive(kind, hours=[23, 0, 23])).structured_adjustment == {"hours": [0, 23]}


@pytest.mark.parametrize("data", [None, [], {}, directive("unknown"), directive(index=True), directive(index=99), directive(value="bad")])
def test_schema_errors_remain_fail_safe(data):
    assert guarded(data).directive_type == "no_op"


def test_list_isolation_and_battery_context():
    data = [directive("minimum_battery_reserve", 11), directive("no_charge_window", hours=[3, 2, 2], index=1)]
    result = validate_directive_interpretation(data, battery=battery())
    assert [d.note_index for d in result] == [0, 1]
    assert [d.directive_type for d in result] == ["no_op", "no_charge_window"]
    assert result[1].structured_adjustment["hours"] == [2, 3]
    assert validate_physical_directives([], battery()) == []
    assert validate_physical_directive(data[0], battery(capacity_kwh=20), 0).directive_type == "minimum_battery_reserve"


def test_mutated_model_is_rechecked():
    model = DirectiveInterpretation.model_validate(directive("minimum_battery_reserve", 10))
    model.structured_adjustment["minimum_energy_kwh"] = 11
    assert guarded(model).directive_type == "no_op"


def test_real_llm_parse_corrects_before_schema_rejection():
    class Provider:
        def complete(self, *args):
            return json.dumps(directive(value="0.2", hours=[13, 12, 13]))
    result = interpreter._interpret_single_note_uncached([("fake", Provider())], "note", 0, forecast(), battery())
    assert result.directive_type == "solar_reduction"
    assert result.structured_adjustment == {"hours": [12, 13], "factor": 0.2}


@pytest.mark.parametrize("data", [directive(value=0), directive("minimum_battery_reserve", 11)])
def test_interpreter_checks_physics_before_confidence_votes(data):
    class Provider:
        calls = 0
        def complete(self, *args):
            self.calls += 1
            return json.dumps(data)
    provider = Provider()
    result = interpreter._interpret_single_note_uncached([("fake", provider)], "note", 0, forecast(), battery())
    assert result.directive_type == "no_op" and provider.calls == 1


@pytest.mark.parametrize("data,expected_kind", [
    (directive(value=0), "no_op"), (directive("minimum_battery_reserve", 11), "no_op"),
    (directive("max_grid_window", -1), "no_op"),
    (directive(value="0.2", hours=[13, 12, 13]), "solar_reduction"),
])
def test_api_applies_physics_and_preserves_response_schema(monkeypatch, data, expected_kind):
    scenario = ScenarioRequest(scenario_id="physics", operator_notes=["note"], hours=forecast(solar=1), battery=battery())
    monkeypatch.setattr(optimize, "interpret_operator_notes", lambda *args: [deepcopy(data)])
    response = TestClient(app).post("/optimize-energy", json=scenario.model_dump())
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["directive_interpretation"][0]["directive_type"] == expected_kind
    assert set(payload["directive_interpretation"][0]) == set(directive())
    from app.models.response import OptimizeResponse
    verify_schedule(scenario, OptimizeResponse.model_validate(payload))
