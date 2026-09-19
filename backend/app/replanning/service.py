"""Re-optimize future hours from an observed battery state."""

from __future__ import annotations

from app.guardrails.validator import validate_directive_interpretation
from app.models.request import FlexibleLoad, ScenarioRequest
from app.models.response import DirectiveInterpretation
from app.optimizer.solver import solve_energy_schedule


def _future_directives(
    directives: list[DirectiveInterpretation],
    current_hour: int,
) -> list[DirectiveInterpretation]:
    future = []
    for directive in directives:
        if not directive.applies or directive.structured_adjustment is None:
            continue
        adjustment = dict(directive.structured_adjustment)
        adjustment["hours"] = [hour for hour in adjustment.get("hours", []) if hour >= current_hour]
        if not adjustment["hours"]:
            continue
        future.append(directive.model_copy(update={"structured_adjustment": adjustment}))
    return future


def replan_remaining_day(
    scenario: ScenarioRequest,
    directives: list[DirectiveInterpretation],
    current_hour: int,
    current_battery_energy_kwh: float,
) -> dict:
    if not 0 <= current_hour <= 23:
        raise ValueError("current_hour must be between 0 and 23")
    if not scenario.battery.minimum_energy_kwh <= current_battery_energy_kwh <= scenario.battery.capacity_kwh:
        raise ValueError("current battery energy must be within the configured battery bounds")

    checked = validate_directive_interpretation(directives, battery=scenario.battery)
    adjusted = scenario.model_copy(deep=True)
    adjusted.scenario_id = f"{scenario.scenario_id} / replan-{current_hour:02d}"
    adjusted.battery.initial_energy_kwh = current_battery_energy_kwh
    for entry in adjusted.hours:
        if entry.hour < current_hour:
            entry.demand_kwh = 0
            entry.solar_kwh = 0
            entry.tariff_bdt_per_kwh = 0
    remaining_loads = []
    for load in adjusted.flexible_loads:
        if load.latest_hour < current_hour:
            continue
        remaining_loads.append(FlexibleLoad.model_validate({
            **load.model_dump(),
            "earliest_hour": max(load.earliest_hour, current_hour),
        }))
    adjusted.flexible_loads = remaining_loads

    locks: list[DirectiveInterpretation] = []
    completed = list(range(current_hour))
    if completed:
        locks = [
            DirectiveInterpretation(
                note_index=0,
                applies=True,
                directive_type="no_charge_window",
                structured_adjustment={"hours": completed},
                explanation="Completed hours are locked during replanning.",
            ),
            DirectiveInterpretation(
                note_index=0,
                applies=True,
                directive_type="no_discharge_window",
                structured_adjustment={"hours": completed},
                explanation="Completed hours are locked during replanning.",
            ),
        ]

    plan = solve_energy_schedule(
        adjusted.hours,
        adjusted.battery,
        [*_future_directives(checked, current_hour), *locks],
        adjusted.flexible_loads,
        use_warm_start=False,
    )
    remaining = [entry for entry in plan if entry.hour >= current_hour]
    forecast = {entry.hour: entry for entry in scenario.hours}
    return {
        "scenario_id": adjusted.scenario_id,
        "current_hour": current_hour,
        "current_battery_energy_kwh": current_battery_energy_kwh,
        "remaining_plan": [
            {**entry.model_dump(), "flexible_loads": dict(entry.flexible_loads)}
            for entry in remaining
        ],
        "remaining_grid_kwh": sum(entry.grid_kwh for entry in remaining),
        "remaining_cost_bdt": sum(entry.grid_kwh * forecast[entry.hour].tariff_bdt_per_kwh for entry in remaining),
        "remaining_peak_grid_kwh": max(entry.grid_kwh for entry in remaining),
    }
