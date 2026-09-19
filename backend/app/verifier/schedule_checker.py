"""Independent replay of schedule physics, directives, and reported totals."""

import math
from collections.abc import Sequence
from dataclasses import dataclass

from app.guardrails.validator import validate_directive_interpretation
from app.models.request import HourEntry, ScenarioRequest
from app.models.response import HourlyPlanEntry, OptimizeResponse


class ScheduleValidationError(ValueError):
    """A schedule must not be returned because verification failed."""


@dataclass(frozen=True)
class ScheduleTotals:
    total_grid_kwh: float
    total_cost_bdt: float
    peak_grid_kwh: float


def recalculate_totals(hours: Sequence[HourEntry], schedule: Sequence[HourlyPlanEntry]) -> ScheduleTotals:
    """Calculate totals by hour identifier, regardless of forecast order."""
    tariffs = {h.hour: h.tariff_bdt_per_kwh for h in hours}
    return ScheduleTotals(
        math.fsum(e.grid_kwh for e in schedule),
        math.fsum(e.grid_kwh * tariffs[e.hour] for e in schedule),
        max(e.grid_kwh for e in schedule),
    )


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ScheduleValidationError(message)


def _equal(left: float, right: float) -> bool:
    return math.isclose(left, right, abs_tol=1e-5, rel_tol=1e-7)


def _at_most(value: float, limit: float) -> bool:
    return value <= limit or _equal(value, limit)


def verify_schedule(scenario: ScenarioRequest, response: OptimizeResponse) -> None:
    """Raise ScheduleValidationError on invalid physics, directives or totals.

    Battery energy is measured at hour end. Comparisons allow CBC precision
    with absolute 1e-5 and relative 1e-7 tolerances; values are never repaired.
    """
    _require(response.scenario_id == scenario.scenario_id, "Scenario identifier mismatch")
    _require([e.hour for e in response.hourly_plan] == list(range(24)), "Schedule must contain hours 0 through 23 in order")
    _require(sorted(h.hour for h in scenario.hours) == list(range(24)), "Invalid forecast hours")
    directives = response.directive_interpretation
    _require(len(directives) == len(scenario.operator_notes), "Directive count must match operator notes")
    _require(validate_directive_interpretation(directives) == directives, "Invalid directives")
    _require(all(math.isfinite(v) for v in scenario.battery.model_dump().values()), "Non-finite battery configuration")
    _require(all(math.isfinite(v) for h in scenario.hours for v in (h.demand_kwh, h.solar_kwh, h.tariff_bdt_per_kwh)), "Non-finite forecast")
    forecast = {h.hour: h for h in scenario.hours}
    battery = scenario.battery
    previous = battery.initial_energy_kwh
    flexible_totals = {load.name: 0.0 for load in scenario.flexible_loads}
    flexible_by_name = {load.name: load for load in scenario.flexible_loads}
    for entry in response.hourly_plan:
        h = forecast[entry.hour]
        prefix = f"Hour {entry.hour}: "
        _require(all(math.isfinite(v) and v >= 0 for v in (
            entry.grid_kwh, entry.solar_used_kwh, entry.battery_kwh, entry.battery_energy_after_kwh,
        )), prefix + "energy values must be finite and non-negative")
        _require(entry.battery_action in ("charge", "discharge", "idle"), prefix + "invalid battery action")
        _require(entry.battery_action != "idle" or entry.battery_kwh == 0, prefix + "idle battery must have zero flow")
        charge = entry.battery_kwh if entry.battery_action == "charge" else 0
        discharge = entry.battery_kwh if entry.battery_action == "discharge" else 0
        _require(set(entry.flexible_loads).issubset(flexible_by_name), prefix + "unknown flexible load")
        _require(all(math.isfinite(value) and value >= 0 for value in entry.flexible_loads.values()), prefix + "invalid flexible load energy")
        flexible_energy = math.fsum(entry.flexible_loads.values())
        for name, value in entry.flexible_loads.items():
            load = flexible_by_name[name]
            _require(load.earliest_hour <= entry.hour <= load.latest_hour, prefix + f"{name} scheduled outside its window")
            _require(_at_most(value, load.max_power_kwh_per_hour), prefix + f"{name} power limit exceeded")
            flexible_totals[name] += value
        _require(_equal(entry.grid_kwh + entry.solar_used_kwh + discharge, h.demand_kwh + flexible_energy + charge), prefix + "energy balance violated")
        _require(_at_most(entry.solar_used_kwh, h.solar_kwh), prefix + "solar forecast exceeded")
        _require(_equal(entry.battery_energy_after_kwh, previous + charge * battery.charge_efficiency - discharge / battery.discharge_efficiency), prefix + "battery continuity violated")
        _require(_at_most(battery.minimum_energy_kwh, entry.battery_energy_after_kwh) and _at_most(entry.battery_energy_after_kwh, battery.capacity_kwh), prefix + "battery bounds violated")
        _require(_at_most(charge, battery.max_charge_kwh_per_hour), prefix + "charge limit exceeded")
        _require(_at_most(discharge, battery.max_discharge_kwh_per_hour), prefix + "discharge limit exceeded")
        for directive in directives:
            adjustment = directive.structured_adjustment
            if directive.directive_type == "no_op" or entry.hour not in adjustment["hours"]:
                continue
            kind = directive.directive_type
            if kind == "solar_reduction":
                compliant = _at_most(entry.solar_used_kwh, h.solar_kwh * adjustment["factor"])
            elif kind == "minimum_battery_reserve":
                compliant = _at_most(adjustment["minimum_energy_kwh"], entry.battery_energy_after_kwh)
            elif kind == "no_charge_window":
                compliant = _equal(charge, 0)
            elif kind == "no_discharge_window":
                compliant = _equal(discharge, 0)
            else:  # max_grid_window; types were checked above
                compliant = _at_most(entry.grid_kwh, adjustment["max_grid_kwh"])
            _require(compliant, prefix + f"{kind} directive violated")
        previous = entry.battery_energy_after_kwh
    for load in scenario.flexible_loads:
        _require(_equal(flexible_totals[load.name], load.energy_kwh), f"Flexible load {load.name} energy requirement not met")
    _require(_equal(previous, battery.initial_energy_kwh), "End-of-day battery energy differs from initial energy")
    totals = recalculate_totals(scenario.hours, response.hourly_plan)
    for name in ("total_grid_kwh", "total_cost_bdt", "peak_grid_kwh"):
        reported = getattr(response, name)
        _require(math.isfinite(reported) and reported >= 0 and _equal(reported, getattr(totals, name)), f"Incorrect {name}")
