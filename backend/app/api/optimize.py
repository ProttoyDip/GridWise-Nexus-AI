"""Interpret, validate, optimize, and independently verify before responding."""

from fastapi import APIRouter, HTTPException

from app.guardrails.validator import validate_directive_interpretation
from app.llm.interpreter import interpret_operator_notes
from app.models.request import ScenarioRequest
from app.models.response import OptimizeResponse
from app.optimizer.scheduler import build_hourly_plan
from app.optimizer.solver import OptimizationError
from app.verifier.schedule_checker import recalculate_totals, verify_schedule

router = APIRouter()


@router.post("/optimize-energy", response_model=OptimizeResponse)
def optimize_energy(payload: ScenarioRequest) -> OptimizeResponse:
    try:
        directives = validate_directive_interpretation(
            interpret_operator_notes(payload.operator_notes, payload.hours, payload.battery)
        )
        plan = build_hourly_plan(payload, directives)
        totals = recalculate_totals(payload.hours, plan)
        response = OptimizeResponse(
            scenario_id=payload.scenario_id,
            directive_interpretation=directives,
            hourly_plan=plan,
            total_grid_kwh=totals.total_grid_kwh,
            total_cost_bdt=totals.total_cost_bdt,
            peak_grid_kwh=totals.peak_grid_kwh,
            plan_summary="Minimum grid-cost schedule satisfying validated directives and battery limits.",
        )
        verify_schedule(payload, response)
        return response
    except OptimizationError as exc:
        raise HTTPException(status_code=422, detail=f"Optimization failed: {exc}") from exc
    except (ValueError, KeyError, TypeError, OverflowError) as exc:
        raise HTTPException(status_code=500, detail=f"Schedule verification failed: {exc}") from exc
