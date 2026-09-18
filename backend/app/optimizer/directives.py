"""Translate validated operator directives into hourly optimization constraints."""

from collections.abc import Mapping, Sequence

import pulp

from app.models.request import HourEntry
from app.models.response import DirectiveInterpretation


def apply_directives(
    model: pulp.LpProblem,
    directives: Sequence[DirectiveInterpretation],
    hours: Sequence[HourEntry],
    *,
    grid: Mapping[int, pulp.LpVariable],
    solar_used: Mapping[int, pulp.LpVariable],
    battery_charge: Mapping[int, pulp.LpVariable],
    battery_discharge: Mapping[int, pulp.LpVariable],
    battery_energy: Mapping[int, pulp.LpVariable],
) -> None:
    """Add each validated directive to the model without modifying inputs.

    Solar factors apply to the original forecast. Overlapping solar/grid
    caps enforce the smallest limit; overlapping reserves enforce the largest
    floor, including the base battery floor. Contradictory directives remain
    hard constraints and are reported as infeasible by the solver.
    """
    forecast = {h.hour: h.solar_kwh for h in hours}
    for index, directive in enumerate(directives):
        kind = directive.directive_type
        if kind == "no_op":
            continue
        adjustment = directive.structured_adjustment
        if adjustment is None:
            raise ValueError("apply_directives requires validated directives")
        for hour in adjustment["hours"]:
            if kind == "solar_reduction":
                constraint = solar_used[hour] <= forecast[hour] * adjustment["factor"]
            elif kind == "minimum_battery_reserve":
                constraint = battery_energy[hour] >= adjustment["minimum_energy_kwh"]
            elif kind == "no_charge_window":
                constraint = battery_charge[hour] == 0
            elif kind == "no_discharge_window":
                constraint = battery_discharge[hour] == 0
            elif kind == "max_grid_window":
                constraint = grid[hour] <= adjustment["max_grid_kwh"]
            else:
                raise ValueError(f"Unsupported directive type: {kind}")
            model += constraint, f"directive_{index}_{kind}_{hour}"
