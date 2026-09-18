"""Builds the 24-hour hourly_plan.

Takes validated directives plus the base scenario (demand, solar,
tariff, battery limits) and produces a schedule that satisfies energy
balance, battery bounds/rate limits, end-of-day neutrality, and every
applicable directive constraint, while minimizing total grid cost
(Problem Statement Sections 05, 09).
"""

from collections.abc import Sequence

from app.models.request import ScenarioRequest
from app.models.response import DirectiveInterpretation, HourlyPlanEntry
from app.optimizer.solver import solve_energy_schedule


def build_hourly_plan(
    scenario: ScenarioRequest,
    directives: Sequence[DirectiveInterpretation] = (),
) -> list[HourlyPlanEntry]:
    """Schedule a scenario using directives returned by the guardrail layer."""
    return solve_energy_schedule(scenario.hours, scenario.battery, directives)
