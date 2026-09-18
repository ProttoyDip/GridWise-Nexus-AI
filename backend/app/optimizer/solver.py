"""Cost-minimizing 24-hour scheduling with PuLP's bundled CBC solver."""

from __future__ import annotations

import math
from collections.abc import Sequence

import pulp

from app.models.request import BatteryConfig, HourEntry
from app.models.response import DirectiveInterpretation, HourlyPlanEntry
from app.optimizer.directives import apply_directives


class OptimizationError(RuntimeError):
    """The optimization backend failed or did not produce an optimal plan."""


def solve_energy_schedule(
    hours: list[HourEntry], battery: BatteryConfig,
    directives: Sequence[DirectiveInterpretation] = (),
) -> list[HourlyPlanEntry]:
    """Return a chronological optimal schedule for a complete daily forecast.

    Battery energy is measured at the end of each hour. Charging/discharging
    is lossless, grid export is forbidden, and unused solar may be curtailed.
    The battery returns to its initial energy at the end of hour 23.
    Directives must have passed guardrail validation; all are hard constraints.
    Invalid input raises ValueError; backend failures raise OptimizationError.
    """
    hours = [HourEntry.model_validate(h.model_dump(), strict=True) for h in hours]
    battery = BatteryConfig.model_validate(battery.model_dump(), strict=True)
    if len(hours) != 24 or sorted(h.hour for h in hours) != list(range(24)):
        raise ValueError("hours must cover each hour from 0 through 23 exactly once")
    if not all(
        math.isfinite(value)
        for h in hours
        for value in (h.demand_kwh, h.solar_kwh, h.tariff_bdt_per_kwh)
    ) or not all(math.isfinite(value) for value in battery.model_dump().values()):
        raise ValueError("forecast and battery values must be finite")
    hours.sort(key=lambda h: h.hour)

    model = pulp.LpProblem("daily_energy_schedule", pulp.LpMinimize)
    indices = range(24)
    grid = pulp.LpVariable.dicts("grid", indices, lowBound=0)
    solar_used = {
        h.hour: pulp.LpVariable(f"solar_used_{h.hour}", lowBound=0, upBound=h.solar_kwh)
        for h in hours
    }
    battery_charge = pulp.LpVariable.dicts(
        "battery_charge", indices, lowBound=0, upBound=battery.max_charge_kwh_per_hour,
    )
    battery_discharge = pulp.LpVariable.dicts(
        "battery_discharge", indices, lowBound=0, upBound=battery.max_discharge_kwh_per_hour,
    )
    battery_energy = pulp.LpVariable.dicts(
        "battery_energy", indices,
        lowBound=battery.minimum_energy_kwh, upBound=battery.capacity_kwh,
    )
    charging = pulp.LpVariable.dicts("charging", indices, cat=pulp.LpBinary)
    model += pulp.lpSum(grid[h.hour] * h.tariff_bdt_per_kwh for h in hours)
    for h in hours:
        hour = h.hour
        model += (
            grid[hour] + solar_used[hour] + battery_discharge[hour]
            == h.demand_kwh + battery_charge[hour]
        ), f"energy_balance_{hour}"
        previous_energy = battery.initial_energy_kwh if hour == 0 else battery_energy[hour - 1]
        model += (
            battery_energy[hour] == previous_energy + battery_charge[hour] - battery_discharge[hour]
        ), f"battery_continuity_{hour}"
        model += battery_charge[hour] <= battery.max_charge_kwh_per_hour * charging[hour]
        model += battery_discharge[hour] <= battery.max_discharge_kwh_per_hour * (1 - charging[hour])
    model += battery_energy[23] == battery.initial_energy_kwh, "final_battery_energy"
    apply_directives(
        model, directives, hours, grid=grid, solar_used=solar_used,
        battery_charge=battery_charge, battery_discharge=battery_discharge,
        battery_energy=battery_energy,
    )

    try:
        status = model.solve(pulp.PULP_CBC_CMD(msg=False, threads=1))
    except pulp.PulpSolverError as exc:
        raise OptimizationError("CBC could not solve the energy schedule") from exc
    if status != pulp.LpStatusOptimal:
        raise OptimizationError(f"Energy optimization status: {pulp.LpStatus[status]}")

    def value(variable: pulp.LpVariable) -> float:
        result = variable.value()
        if result is None or not math.isfinite(result):
            raise OptimizationError("Solver returned a missing or non-finite value")
        # Remove tiny negative values caused by solver tolerances.
        return max(0.0, result)

    schedule = []
    for hour in indices:
        charge = value(battery_charge[hour])
        discharge = value(battery_discharge[hour])
        if charge > 0:
            action, amount = "charge", charge
        elif discharge > 0:
            action, amount = "discharge", discharge
        else:
            action, amount = "idle", 0.0
        schedule.append(HourlyPlanEntry(
            hour=hour, grid_kwh=value(grid[hour]), solar_used_kwh=value(solar_used[hour]),
            battery_action=action, battery_kwh=amount,
            battery_energy_after_kwh=value(battery_energy[hour]),
        ))
    return schedule
