"""Analytical scenarios and constraint replay for the real CBC solver."""

import pytest
import pulp

from app.models.request import BatteryConfig, HourEntry
from app.optimizer.solver import OptimizationError, solve_energy_schedule


def forecast(demand=10, solar=0, tariff=5):
    return [HourEntry(hour=h, demand_kwh=demand, solar_kwh=solar, tariff_bdt_per_kwh=tariff) for h in range(24)]


def battery(**changes):
    data = dict(capacity_kwh=10, initial_energy_kwh=0, minimum_energy_kwh=0,
                max_charge_kwh_per_hour=10, max_discharge_kwh_per_hour=10)
    data.update(changes)
    return BatteryConfig(**data)


def check_plan(plan, hours, config):
    assert [entry.hour for entry in plan] == list(range(24))
    previous = config.initial_energy_kwh
    for entry, h in zip(plan, sorted(hours, key=lambda h: h.hour)):
        charge = entry.battery_kwh if entry.battery_action == "charge" else 0
        discharge = entry.battery_kwh if entry.battery_action == "discharge" else 0
        assert 0 <= entry.grid_kwh
        assert 0 <= entry.solar_used_kwh <= h.solar_kwh + 1e-6
        assert charge <= config.max_charge_kwh_per_hour + 1e-6
        assert discharge <= config.max_discharge_kwh_per_hour + 1e-6
        assert entry.grid_kwh + entry.solar_used_kwh + discharge == pytest.approx(h.demand_kwh + charge, abs=1e-6)
        assert entry.battery_energy_after_kwh == pytest.approx(previous + charge - discharge, abs=1e-6)
        assert config.minimum_energy_kwh - 1e-6 <= entry.battery_energy_after_kwh <= config.capacity_kwh + 1e-6
        previous = entry.battery_energy_after_kwh
    assert previous == pytest.approx(config.initial_energy_kwh, abs=1e-6)


def cost(plan, hours):
    tariffs = {h.hour: h.tariff_bdt_per_kwh for h in hours}
    return sum(entry.grid_kwh * tariffs[entry.hour] for entry in plan)


def test_grid_only_when_battery_rates_are_zero():
    hours = forecast()
    config = battery(initial_energy_kwh=5, minimum_energy_kwh=2,
                     max_charge_kwh_per_hour=0, max_discharge_kwh_per_hour=0)
    plan = solve_energy_schedule(hours, config)
    check_plan(plan, hours, config)
    assert all(entry.grid_kwh == 10 and entry.battery_action == "idle" for entry in plan)
    assert cost(plan, hours) == pytest.approx(1200)


def test_surplus_solar_is_curtailed():
    hours, config = forecast(solar=20), battery()
    plan = solve_energy_schedule(hours, config)
    check_plan(plan, hours, config)
    assert cost(plan, hours) == pytest.approx(0)


def test_cheap_grid_energy_is_shifted_to_expensive_hours():
    hours, config = forecast(), battery()
    hours[0].tariff_bdt_per_kwh = 1
    plan = solve_energy_schedule(hours, config)
    check_plan(plan, hours, config)
    assert plan[0].battery_action == "charge"
    assert plan[0].battery_kwh == pytest.approx(10)
    assert cost(plan, hours) == pytest.approx(1120)


def test_stores_solar_for_later_demand():
    hours, config = forecast(demand=0), battery()
    hours[0].solar_kwh = 10
    hours[23].demand_kwh = 10
    plan = solve_energy_schedule(hours, config)
    check_plan(plan, hours, config)
    assert cost(plan, hours) == pytest.approx(0)
    assert plan[23].battery_action == "discharge"


def test_reserve_and_rates_limit_savings():
    hours = forecast(demand=0)
    hours[0].tariff_bdt_per_kwh = 1
    hours[23].demand_kwh = 10
    config = battery(initial_energy_kwh=4, minimum_energy_kwh=4,
                     max_charge_kwh_per_hour=3, max_discharge_kwh_per_hour=2)
    plan = solve_energy_schedule(hours, config)
    check_plan(plan, hours, config)
    assert cost(plan, hours) == pytest.approx(42)


def test_initial_energy_cannot_be_consumed_for_free():
    hours, config = forecast(), battery(initial_energy_kwh=10)
    plan = solve_energy_schedule(hours, config)
    check_plan(plan, hours, config)
    assert cost(plan, hours) == pytest.approx(1200)


def test_zero_tariffs_and_zero_demand():
    hours, config = forecast(demand=0, tariff=0), battery(initial_energy_kwh=5)
    plan = solve_energy_schedule(hours, config)
    check_plan(plan, hours, config)
    assert cost(plan, hours) == 0


def test_unordered_input_returns_chronological_plan_without_mutation():
    hours, config = forecast(), battery()
    hours.reverse()
    plan = solve_energy_schedule(hours, config)
    check_plan(plan, hours, config)
    assert hours[0].hour == 23


@pytest.mark.parametrize("kind", ["missing", "duplicate", "infinite_forecast", "infinite_battery"])
def test_invalid_input(kind):
    hours, config = forecast(), battery()
    if kind == "missing":
        hours.pop()
    elif kind == "duplicate":
        hours[23].hour = 0
    elif kind == "infinite_forecast":
        hours[0].solar_kwh = float("inf")
    else:
        config.capacity_kwh = float("inf")
    with pytest.raises(ValueError):
        solve_energy_schedule(hours, config)


def test_nonoptimal_result_is_not_returned(monkeypatch):
    monkeypatch.setattr(pulp.LpProblem, "solve", lambda *args, **kwargs: pulp.LpStatusInfeasible)
    with pytest.raises(OptimizationError, match="Infeasible"):
        solve_energy_schedule(forecast(), battery())


def test_backend_error_is_reported(monkeypatch):
    def fail(*args, **kwargs):
        raise pulp.PulpSolverError("unavailable")
    monkeypatch.setattr(pulp.LpProblem, "solve", fail)
    with pytest.raises(OptimizationError, match="CBC"):
        solve_energy_schedule(forecast(), battery())
