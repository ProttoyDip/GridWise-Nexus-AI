"""Reject schedules that pass the response schema but violate physical rules."""

import pytest
from fastapi.testclient import TestClient

from app.api import optimize
from app.main import app
from app.models.request import ScenarioRequest
from app.models.response import HourlyPlanEntry, OptimizeResponse
from app.optimizer.solver import solve_energy_schedule
from app.verifier.schedule_checker import ScheduleValidationError, recalculate_totals, verify_schedule
from test_optimizer_directives import validated
from test_solver import battery, forecast


def example(discharge_first=False):
    scenario = ScenarioRequest(scenario_id="verified", operator_notes=["note"],
                               hours=forecast(solar=1), battery=battery(initial_energy_kwh=5, minimum_energy_kwh=2))
    plan = [HourlyPlanEntry(hour=h, grid_kwh=9, solar_used_kwh=1, battery_action="idle",
                           battery_kwh=0, battery_energy_after_kwh=5) for h in range(24)]
    plan[0].battery_action = "discharge" if discharge_first else "charge"
    plan[0].battery_kwh = 3
    plan[0].grid_kwh = 6 if discharge_first else 12
    plan[0].battery_energy_after_kwh = 2 if discharge_first else 8
    plan[1].battery_action = "charge" if discharge_first else "discharge"
    plan[1].battery_kwh = 3
    plan[1].grid_kwh = 12 if discharge_first else 6
    return scenario, response_for(scenario, plan)


def response_for(scenario, plan, directives=None):
    totals = recalculate_totals(scenario.hours, plan)
    return OptimizeResponse(scenario_id=scenario.scenario_id,
                            directive_interpretation=directives or validated("no_op", []),
                            hourly_plan=plan, total_grid_kwh=totals.total_grid_kwh,
                            total_cost_bdt=totals.total_cost_bdt, peak_grid_kwh=totals.peak_grid_kwh,
                            plan_summary="Test schedule.")


def test_valid_replay_with_unordered_forecast():
    scenario, response = example()
    scenario.hours.reverse()
    verify_schedule(scenario, response)


@pytest.mark.parametrize("field,value,message", [
    ("grid_kwh", 13, "energy balance"),
    ("battery_energy_after_kwh", 9, "continuity"),
    ("grid_kwh", float("nan"), "finite"),
    ("solar_used_kwh", float("inf"), "finite"),
    ("battery_kwh", -1, "non-negative"),
    ("battery_action", "unknown", "action"),
])
def test_corrupt_hour(field, value, message):
    scenario, response = example()
    setattr(response.hourly_plan[0], field, value)
    with pytest.raises(ScheduleValidationError, match=message):
        verify_schedule(scenario, response)


def test_solar_cannot_exceed_forecast_even_with_energy_balance():
    scenario, response = example()
    response.hourly_plan[2].solar_used_kwh = 2
    response.hourly_plan[2].grid_kwh = 8
    with pytest.raises(ScheduleValidationError, match="solar forecast"):
        verify_schedule(scenario, response)


@pytest.mark.parametrize("field,value,message,discharge_first", [
    ("capacity_kwh", 7, "bounds", False),
    ("minimum_energy_kwh", 3, "bounds", True),
    ("max_charge_kwh_per_hour", 2, "charge limit", False),
    ("max_discharge_kwh_per_hour", 2, "discharge limit", False),
])
def test_battery_limits(field, value, message, discharge_first):
    scenario, response = example(discharge_first)
    setattr(scenario.battery, field, value)
    with pytest.raises(ScheduleValidationError, match=message):
        verify_schedule(scenario, response)


@pytest.mark.parametrize("kind,hours,values", [
    ("solar_reduction", [2], {"factor": 0.5}),
    ("minimum_battery_reserve", [1], {"minimum_energy_kwh": 6}),
    ("no_charge_window", [0], {}),
    ("no_discharge_window", [1], {}),
    ("max_grid_window", [0], {"max_grid_kwh": 11}),
])
def test_each_directive_violation(kind, hours, values):
    scenario, response = example()
    response.directive_interpretation = validated(kind, hours, **values)
    with pytest.raises(ScheduleValidationError, match=kind):
        verify_schedule(scenario, response)


def test_end_of_day_equality_is_checked_after_continuity():
    scenario, response = example()
    last = response.hourly_plan[23]
    last.battery_action, last.battery_kwh = "charge", 1
    last.grid_kwh, last.battery_energy_after_kwh = 10, 6
    with pytest.raises(ScheduleValidationError, match="End-of-day"):
        verify_schedule(scenario, response)


@pytest.mark.parametrize("field", ["total_grid_kwh", "total_cost_bdt", "peak_grid_kwh"])
@pytest.mark.parametrize("value", [12345, float("nan"), float("inf")])
def test_reported_totals_are_recalculated(field, value):
    scenario, response = example()
    setattr(response, field, value)
    with pytest.raises(ScheduleValidationError, match=field):
        verify_schedule(scenario, response)


@pytest.mark.parametrize("kind", ["missing_hour", "duplicate_hour", "wrong_id", "missing_directive", "invalid_directive", "idle_flow"])
def test_structural_errors(kind):
    scenario, response = example()
    if kind == "missing_hour":
        response.hourly_plan.pop()
    elif kind == "duplicate_hour":
        response.hourly_plan[23].hour = 0
    elif kind == "wrong_id":
        response.scenario_id = "other"
    elif kind == "missing_directive":
        response.directive_interpretation.clear()
    elif kind == "invalid_directive":
        response.directive_interpretation[0].applies = True
    else:
        response.hourly_plan[2].battery_kwh = 1
    with pytest.raises(ScheduleValidationError):
        verify_schedule(scenario, response)


def test_real_solver_decimal_schedule_passes_verification():
    scenario, _ = example()
    scenario.hours = forecast(demand=1.23456789, solar=0.12345678, tariff=3.456789)
    plan = solve_energy_schedule(scenario.hours, scenario.battery)
    verify_schedule(scenario, response_for(scenario, plan))


def test_api_returns_verified_directive_compliant_schedule(monkeypatch):
    scenario, _ = example()
    directives = validated("solar_reduction", list(range(24)), factor=0.2)
    monkeypatch.setattr(optimize, "interpret_operator_notes", lambda *args: directives)
    result = TestClient(app).post("/optimize-energy", json=scenario.model_dump())
    assert result.status_code == 200
    response = OptimizeResponse.model_validate(result.json())
    verify_schedule(scenario, response)
    assert all(entry.solar_used_kwh <= 0.2 + 1e-6 for entry in response.hourly_plan)
    assert response.total_cost_bdt == pytest.approx(1176)


def test_api_guardrails_invalid_interpretation_before_optimization(monkeypatch):
    scenario, _ = example()
    monkeypatch.setattr(optimize, "interpret_operator_notes", lambda *args: [{"directive_type": "unknown"}])
    result = TestClient(app).post("/optimize-energy", json=scenario.model_dump())
    assert result.status_code == 200
    assert result.json()["directive_interpretation"][0]["directive_type"] == "no_op"


def test_api_rejects_invalid_solver_schedule(monkeypatch):
    scenario, response = example()
    response.hourly_plan[3].grid_kwh += 1
    monkeypatch.setattr(optimize, "interpret_operator_notes", lambda *args: validated("no_op", []))
    monkeypatch.setattr(optimize, "build_hourly_plan", lambda *args: response.hourly_plan)
    result = TestClient(app).post("/optimize-energy", json=scenario.model_dump())
    assert result.status_code == 500
    assert "energy balance" in result.json()["detail"]
    assert "hourly_plan" not in result.json()


def test_api_rejects_infeasible_directives(monkeypatch):
    scenario, _ = example()
    monkeypatch.setattr(optimize, "interpret_operator_notes",
                        lambda *args: validated("minimum_battery_reserve", [23], minimum_energy_kwh=6))
    result = TestClient(app).post("/optimize-energy", json=scenario.model_dump())
    assert result.status_code == 422
    assert "Infeasible" in result.json()["detail"]


def test_api_preserves_request_validation():
    result = TestClient(app).post("/optimize-energy", json={})
    assert result.status_code == 422
