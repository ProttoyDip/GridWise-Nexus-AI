"""Verify each directive through guardrails and the real optimization backend."""

from copy import deepcopy

import pytest

from app.guardrails.validator import validate_directive_interpretation
from app.models.request import ScenarioRequest
from app.optimizer.scheduler import build_hourly_plan
from app.optimizer.solver import OptimizationError, solve_energy_schedule
from test_solver import battery, check_plan, cost, forecast


def validated(kind, hours, **values):
    return validate_directive_interpretation([dict(
        note_index=0, applies=kind != "no_op", directive_type=kind,
        structured_adjustment=None if kind == "no_op" else dict(hours=hours, **values),
        explanation="Test directive.",
    )])


def test_solar_reduction_changes_only_selected_hours():
    hours = forecast(solar=10)
    config = battery(max_charge_kwh_per_hour=0, max_discharge_kwh_per_hour=0)
    directives = validated("solar_reduction", [0, 23], factor=0.25)
    plan = solve_energy_schedule(hours, config, directives)
    check_plan(plan, hours, config)
    for h in [0, 23]:
        assert plan[h].solar_used_kwh == pytest.approx(2.5)
        assert plan[h].grid_kwh == pytest.approx(7.5)
    assert plan[1].solar_used_kwh == pytest.approx(10)
    assert cost(plan, hours) == pytest.approx(75)


def test_minimum_battery_reserve_increases_hourly_floor():
    hours = forecast(demand=0, tariff=1)
    hours[1].demand_kwh = 10
    hours[1].tariff_bdt_per_kwh = 10
    config = battery(initial_energy_kwh=10)
    baseline = solve_energy_schedule(hours, config)
    plan = solve_energy_schedule(hours, config, validated("minimum_battery_reserve", [1], minimum_energy_kwh=8))
    check_plan(plan, hours, config)
    assert baseline[1].battery_energy_after_kwh == pytest.approx(0)
    assert plan[1].battery_energy_after_kwh >= 8 - 1e-6
    assert plan[1].grid_kwh == pytest.approx(8)
    assert cost(plan, hours) == pytest.approx(82)


def test_no_charge_window_blocks_cheap_charging():
    hours, config = forecast(), battery()
    hours[0].tariff_bdt_per_kwh = 1
    baseline = solve_energy_schedule(hours, config)
    plan = solve_energy_schedule(hours, config, validated("no_charge_window", [0]))
    check_plan(plan, hours, config)
    assert baseline[0].battery_action == "charge"
    assert plan[0].battery_action == "idle"
    assert cost(plan, hours) == pytest.approx(1160)


def test_no_discharge_window_blocks_expensive_discharge():
    hours = forecast(demand=0, tariff=1)
    hours[1].demand_kwh = 10
    hours[1].tariff_bdt_per_kwh = 10
    config = battery(initial_energy_kwh=10)
    plan = solve_energy_schedule(hours, config, validated("no_discharge_window", [1]))
    check_plan(plan, hours, config)
    assert plan[1].battery_action != "discharge"
    assert plan[1].grid_kwh >= 10 - 1e-6
    assert cost(plan, hours) == pytest.approx(100)


def test_max_grid_window_forces_battery_support():
    hours = forecast(demand=0, tariff=1)
    hours[1].demand_kwh = 10
    config = battery(initial_energy_kwh=10)
    plan = solve_energy_schedule(hours, config, validated("max_grid_window", [1], max_grid_kwh=3))
    check_plan(plan, hours, config)
    assert plan[1].grid_kwh <= 3 + 1e-6
    assert plan[1].battery_action == "discharge"
    assert plan[1].battery_kwh >= 7 - 1e-6


def test_no_op_leaves_schedule_unchanged():
    hours, config = forecast(), battery()
    assert solve_energy_schedule(hours, config, validated("no_op", [])) == solve_energy_schedule(hours, config)


@pytest.mark.parametrize("kind,values", [
    ("minimum_battery_reserve", {"minimum_energy_kwh": 11}),
    ("max_grid_window", {"max_grid_kwh": 0}),
])
def test_impossible_directives_report_infeasibility(kind, values):
    config = battery(max_charge_kwh_per_hour=0, max_discharge_kwh_per_hour=0)
    with pytest.raises(OptimizationError, match="Infeasible"):
        solve_energy_schedule(forecast(), config, validated(kind, [0], **values))


def test_final_battery_requirement_remains_hard_with_directives():
    with pytest.raises(OptimizationError, match="Infeasible"):
        solve_energy_schedule(forecast(), battery(), validated("minimum_battery_reserve", [23], minimum_energy_kwh=1))


def test_lower_reserve_does_not_relax_base_floor():
    hours, config = forecast(), battery(initial_energy_kwh=5, minimum_energy_kwh=5)
    plan = solve_energy_schedule(hours, config, validated("minimum_battery_reserve", list(range(24)), minimum_energy_kwh=0))
    check_plan(plan, hours, config)


def test_overlapping_directives_enforce_strongest_limits_and_preserve_inputs():
    hours = forecast(solar=10)
    config = battery(initial_energy_kwh=5, minimum_energy_kwh=2,
                     max_charge_kwh_per_hour=0, max_discharge_kwh_per_hour=0)
    directives = []
    for kind, values in [
        ("solar_reduction", {"factor": 0.5}),
        ("solar_reduction", {"factor": 0.2}),
        ("minimum_battery_reserve", {"minimum_energy_kwh": 3}),
        ("minimum_battery_reserve", {"minimum_energy_kwh": 5}),
        ("max_grid_window", {"max_grid_kwh": 9}),
        ("max_grid_window", {"max_grid_kwh": 8}),
    ]:
        item = validated(kind, [0], **values)[0]
        item.note_index = len(directives)
        directives.append(item)
    original = deepcopy((hours, config, directives))
    plan = solve_energy_schedule(hours, config, directives)
    check_plan(plan, hours, config)
    assert plan[0].solar_used_kwh == pytest.approx(2)
    assert plan[0].grid_kwh == pytest.approx(8)
    assert plan[0].battery_energy_after_kwh == pytest.approx(5)
    assert (hours, config, directives) == original
    assert solve_energy_schedule(hours, config, list(reversed(directives))) == plan


def test_scheduler_passes_validated_directives_to_solver():
    scenario = ScenarioRequest(scenario_id="test", operator_notes=["Reduce solar."],
                               hours=forecast(solar=10), battery=battery())
    directives = validated("solar_reduction", list(range(24)), factor=0)
    plan = build_hourly_plan(scenario, directives)
    check_plan(plan, scenario.hours, scenario.battery)
    assert all(entry.solar_used_kwh == 0 for entry in plan)
    assert cost(plan, scenario.hours) == pytest.approx(1200)
