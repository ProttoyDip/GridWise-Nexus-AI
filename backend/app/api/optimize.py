"""POST /optimize-energy — main LLM interpretation + optimization endpoint.

Orchestrates the pipeline: request validation -> LLM interpreter ->
guardrail validation -> optimizer -> final verifier -> response.

NOTE: LLM interpretation and optimization are not implemented yet.
This handler validates the request against the schema and returns a
structurally valid PLACEHOLDER response (every note is reported as
no_op, and the schedule simply buys all demand from the grid). It does
not represent a real interpretation or an optimized/valid schedule.
"""

from fastapi import APIRouter

from app.models.request import ScenarioRequest
from app.models.response import (
    DirectiveInterpretation,
    HourlyPlanEntry,
    OptimizeResponse,
)

router = APIRouter()


@router.post("/optimize-energy", response_model=OptimizeResponse)
def optimize_energy(payload: ScenarioRequest) -> OptimizeResponse:
    # TODO: replace with LLM interpreter -> guardrails -> optimizer -> verifier.

    directive_interpretation = [
        DirectiveInterpretation(
            note_index=index,
            applies=False,
            directive_type="no_op",
            structured_adjustment=None,
            explanation="Placeholder: LLM interpretation not yet implemented.",
        )
        for index in range(len(payload.operator_notes))
    ]

    sorted_hours = sorted(payload.hours, key=lambda h: h.hour)
    hourly_plan = [
        HourlyPlanEntry(
            hour=hour.hour,
            grid_kwh=hour.demand_kwh,
            solar_used_kwh=0,
            battery_action="idle",
            battery_kwh=0,
            battery_energy_after_kwh=payload.battery.initial_energy_kwh,
        )
        for hour in sorted_hours
    ]

    total_grid_kwh = sum(entry.grid_kwh for entry in hourly_plan)
    total_cost_bdt = sum(
        entry.grid_kwh * hour.tariff_bdt_per_kwh
        for entry, hour in zip(hourly_plan, sorted_hours)
    )
    peak_grid_kwh = max(entry.grid_kwh for entry in hourly_plan)

    return OptimizeResponse(
        scenario_id=payload.scenario_id,
        directive_interpretation=directive_interpretation,
        hourly_plan=hourly_plan,
        total_grid_kwh=total_grid_kwh,
        total_cost_bdt=total_cost_bdt,
        peak_grid_kwh=peak_grid_kwh,
        plan_summary=(
            "Placeholder plan: grid fully supplies demand every hour. "
            "LLM interpretation and optimization are not implemented yet."
        ),
    )
