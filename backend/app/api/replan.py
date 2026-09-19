"""Remaining-day replanning API."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.models.request import ScenarioRequest
from app.models.response import DirectiveInterpretation
from app.optimizer.solver import OptimizationError
from app.replanning import replan_remaining_day

router = APIRouter(tags=["replanning"])


class ReplanRequest(BaseModel):
    scenario: ScenarioRequest
    directives: list[DirectiveInterpretation] = Field(..., min_length=1, max_length=3)
    current_hour: int = Field(..., ge=0, le=23)
    current_battery_energy_kwh: float = Field(..., ge=0)


@router.post("/replan")
def replan(payload: ReplanRequest) -> dict:
    try:
        return replan_remaining_day(
            payload.scenario,
            payload.directives,
            payload.current_hour,
            payload.current_battery_energy_kwh,
        )
    except (ValueError, OptimizationError) as exc:
        raise HTTPException(status_code=422, detail=f"Replanning failed: {exc}") from exc
