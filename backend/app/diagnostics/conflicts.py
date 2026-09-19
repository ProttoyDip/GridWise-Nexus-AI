"""Explain infeasible directive combinations by testing minimal relaxations."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

from app.models.request import ScenarioRequest
from app.models.response import DirectiveInterpretation
from app.optimizer.solver import OptimizationError, solve_energy_schedule


@dataclass(frozen=True)
class ConflictDiagnosis:
    feasible: bool
    conflicting_note_indices: tuple[int, ...] = ()
    conflicting_directive_types: tuple[str, ...] = ()
    hours: tuple[int, ...] = ()
    summary: str = ""
    suggestions: tuple[str, ...] = ()


def _can_solve(
    scenario: ScenarioRequest,
    directives: list[DirectiveInterpretation],
) -> bool:
    try:
        solve_energy_schedule(
            scenario.hours,
            scenario.battery,
            directives,
            scenario.flexible_loads,
            use_warm_start=False,
        )
    except OptimizationError:
        return False
    return True


def _suggestion(directive: DirectiveInterpretation) -> str:
    kind = directive.directive_type
    if kind == "max_grid_window":
        return "Raise the grid-import limit or shorten its time window."
    if kind == "minimum_battery_reserve":
        return "Lower the temporary battery reserve or apply it to fewer hours."
    if kind == "no_discharge_window":
        return "Allow battery discharge during at least part of this window."
    if kind == "no_charge_window":
        return "Allow charging before the constrained period or shorten the no-charge window."
    if kind == "solar_reduction":
        return "Confirm the solar reduction and provide more grid or battery capacity for those hours."
    return "Revise or remove this instruction and analyze the scenario again."


def diagnose_conflicts(
    scenario: ScenarioRequest,
    directives: list[DirectiveInterpretation],
) -> ConflictDiagnosis:
    """Return the smallest directive set whose removal restores feasibility.

    There are at most three operator directives, so an exhaustive relaxation
    check is both fast and easier to trust than an LLM-generated diagnosis.
    """
    if _can_solve(scenario, directives):
        return ConflictDiagnosis(
            feasible=True,
            summary="The interpreted instructions can be satisfied together.",
        )

    active = [directive for directive in directives if directive.applies]
    if not active or not _can_solve(scenario, []):
        return ConflictDiagnosis(
            feasible=False,
            summary=(
                "The base forecast and battery configuration are infeasible even without "
                "operator instructions. Review demand, available supply, and battery limits."
            ),
            suggestions=(
                "Increase available grid or solar supply, reduce demand, or revise the battery limits.",
            ),
        )

    minimal_sets: list[tuple[DirectiveInterpretation, ...]] = []
    for size in range(1, len(active) + 1):
        for candidate in combinations(active, size):
            if not _can_solve(scenario, list(candidate)):
                minimal_sets.append(candidate)
        if minimal_sets:
            break

    # Prefer the first minimal conflicting set in note order for a stable message.
    conflict = tuple(sorted(minimal_sets[0], key=lambda item: item.note_index))
    hours = sorted(
        {
            int(hour)
            for directive in conflict
            for hour in (directive.structured_adjustment or {}).get("hours", [])
        }
    )
    note_numbers = ", ".join(str(item.note_index + 1) for item in conflict)
    return ConflictDiagnosis(
        feasible=False,
        conflicting_note_indices=tuple(item.note_index for item in conflict),
        conflicting_directive_types=tuple(item.directive_type for item in conflict),
        hours=tuple(hours),
        summary=(
            f"Instruction{'' if len(conflict) == 1 else 's'} {note_numbers} "
            "cannot be satisfied with the current forecast and battery limits."
        ),
        suggestions=tuple(dict.fromkeys(_suggestion(item) for item in conflict)),
    )
