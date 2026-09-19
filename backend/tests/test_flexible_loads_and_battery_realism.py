import pytest

from app.models.request import BatteryConfig, FlexibleLoad, HourEntry, ScenarioRequest
from app.models.response import DirectiveInterpretation, OptimizeResponse
from app.optimizer.solver import solve_energy_schedule
from app.verifier.schedule_checker import recalculate_totals, verify_schedule


def hours() -> list[HourEntry]:
    return [
        HourEntry(
            hour=hour,
            demand_kwh=10,
            solar_kwh=0,
            tariff_bdt_per_kwh=1 if hour in (2, 3) else 10,
        )
        for hour in range(24)
    ]


def battery(**changes) -> BatteryConfig:
    values = {
        "capacity_kwh": 20,
        "initial_energy_kwh": 10,
        "minimum_energy_kwh": 0,
        "max_charge_kwh_per_hour": 10,
        "max_discharge_kwh_per_hour": 10,
    }
    values.update(changes)
    return BatteryConfig(**values)


def test_flexible_load_runs_inside_window_and_meets_energy():
    task = FlexibleLoad(name="Water pump", energy_kwh=20, max_power_kwh_per_hour=10, earliest_hour=0, latest_hour=3)
    plan = solve_energy_schedule(hours(), battery(max_charge_kwh_per_hour=0, max_discharge_kwh_per_hour=0), flexible_loads=[task])
    assigned = [(entry.hour, entry.flexible_loads.get("Water pump", 0)) for entry in plan]
    assert sum(value for _, value in assigned) == pytest.approx(20)
    assert all(value == 0 for hour, value in assigned if hour not in (0, 1, 2, 3))
    assert sum(value for hour, value in assigned if hour in (2, 3)) == pytest.approx(20)


def test_realistic_efficiency_is_independently_verified():
    config = battery(charge_efficiency=0.9, discharge_efficiency=0.9)
    task = FlexibleLoad(name="Pump", energy_kwh=5, max_power_kwh_per_hour=5, earliest_hour=2, latest_hour=3)
    scenario = ScenarioRequest(
        scenario_id="realistic",
        operator_notes=["No restriction"],
        hours=hours(),
        battery=config,
        flexible_loads=[task],
    )
    directive = DirectiveInterpretation(note_index=0, applies=False, directive_type="no_op", structured_adjustment=None, explanation="No change.")
    plan = solve_energy_schedule(scenario.hours, config, [directive], scenario.flexible_loads)
    totals = recalculate_totals(scenario.hours, plan)
    response = OptimizeResponse(
        scenario_id=scenario.scenario_id,
        directive_interpretation=[directive],
        hourly_plan=plan,
        total_grid_kwh=totals.total_grid_kwh,
        total_cost_bdt=totals.total_cost_bdt,
        peak_grid_kwh=totals.peak_grid_kwh,
        plan_summary="Verified realistic schedule.",
    )
    verify_schedule(scenario, response)


def test_battery_wear_cost_can_suppress_tariff_arbitrage():
    no_wear = solve_energy_schedule(hours(), battery(degradation_cost_bdt_per_kwh=0))
    high_wear = solve_energy_schedule(hours(), battery(degradation_cost_bdt_per_kwh=100))
    assert sum(entry.battery_kwh for entry in no_wear) > 0
    assert sum(entry.battery_kwh for entry in high_wear) == pytest.approx(0)
