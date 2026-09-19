"""Measured reliability metrics for one optimization result.

Read-only: replays checks against the finished response and never alters it.
Chain-of-thought is never exposed; only pass rates and agreement scores.
"""

from __future__ import annotations

import math
from typing import Any

from app.models.request import ScenarioRequest
from app.models.response import OptimizeResponse
from app.verifier.schedule_checker import recalculate_totals, verify_schedule


def _close(a: float, b: float) -> bool:
    return math.isclose(a, b, abs_tol=1e-5, rel_tol=1e-7)


def _le(a: float, b: float) -> bool:
    return a <= b or _close(a, b)


def _pct(passed: int, total: int) -> float | None:
    return round(100 * passed / total, 1) if total else None


def _constraint_checks(scenario: ScenarioRequest, response: OptimizeResponse) -> tuple[int, int]:
    """Count physics and directive checks that hold, hour by hour."""
    forecast = {h.hour: h for h in scenario.hours}
    battery = scenario.battery
    passed = total = 0
    previous = battery.initial_energy_kwh
    for entry in response.hourly_plan:
        h = forecast[entry.hour]
        charge = entry.battery_kwh if entry.battery_action == "charge" else 0.0
        discharge = entry.battery_kwh if entry.battery_action == "discharge" else 0.0
        checks = [
            _close(entry.grid_kwh + entry.solar_used_kwh + discharge, h.demand_kwh + charge),
            _le(entry.solar_used_kwh, h.solar_kwh),
            _close(entry.battery_energy_after_kwh, previous + charge - discharge),
            _le(battery.minimum_energy_kwh, entry.battery_energy_after_kwh) and _le(entry.battery_energy_after_kwh, battery.capacity_kwh),
            _le(charge, battery.max_charge_kwh_per_hour) and _le(discharge, battery.max_discharge_kwh_per_hour),
        ]
        for directive in response.directive_interpretation:
            adjustment = directive.structured_adjustment
            if directive.directive_type == "no_op" or adjustment is None or entry.hour not in adjustment["hours"]:
                continue
            kind = directive.directive_type
            if kind == "solar_reduction":
                checks.append(_le(entry.solar_used_kwh, h.solar_kwh * adjustment["factor"]))
            elif kind == "minimum_battery_reserve":
                checks.append(_le(adjustment["minimum_energy_kwh"], entry.battery_energy_after_kwh))
            elif kind == "no_charge_window":
                checks.append(_close(charge, 0.0))
            elif kind == "no_discharge_window":
                checks.append(_close(discharge, 0.0))
            elif kind == "max_grid_window":
                checks.append(_le(entry.grid_kwh, adjustment["max_grid_kwh"]))
        passed += sum(checks)
        total += len(checks)
        previous = entry.battery_energy_after_kwh
    return passed, total


def compute_reliability(scenario: ScenarioRequest, response: OptimizeResponse) -> dict[str, Any]:
    """Percentages (or None when a signal is unavailable) for the reliability panel."""
    scores = []
    for directive in response.directive_interpretation:
        meta = getattr(directive, "_confidence_metadata", None)
        if meta is not None:
            scores.append(meta.confidence_score)
    understanding = round(100 * sum(scores) / len(scores), 1) if scores else None

    passed, total = _constraint_checks(scenario, response)

    try:
        verify_schedule(scenario, response)
        full_verify = True
    except ValueError:
        full_verify = False
    totals = recalculate_totals(scenario.hours, response.hourly_plan)
    totals_ok = _close(totals.total_cost_bdt, response.total_cost_bdt) and _close(totals.total_grid_kwh, response.total_grid_kwh)
    cyclic = _close(response.hourly_plan[-1].battery_energy_after_kwh, scenario.battery.initial_energy_kwh)
    validity_checks = [full_verify, totals_ok, cyclic]

    return {
        "directive_understanding": understanding,
        "constraint_validation": _pct(passed, total),
        "optimization_validity": _pct(sum(validity_checks), len(validity_checks)),
        "checks_run": total + len(validity_checks),
    }
