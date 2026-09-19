"""Critical-load outage planning API."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator

from app.models.request import ScenarioRequest
from app.resilience import evaluate_outage

router = APIRouter(prefix="/resilience", tags=["resilience"])


class OutageRequest(BaseModel):
    scenario: ScenarioRequest
    start_hour: int = Field(..., ge=0, le=23)
    end_hour: int = Field(..., ge=0, le=23)
    critical_load_fraction: float = Field(0.5, gt=0, le=1)

    @model_validator(mode="after")
    def chronological_window(self) -> "OutageRequest":
        if self.end_hour < self.start_hour:
            raise ValueError("end_hour must be greater than or equal to start_hour")
        return self


@router.post("/outage")
def outage_plan(payload: OutageRequest) -> dict:
    try:
        return evaluate_outage(payload.scenario, payload.start_hour, payload.end_hour, payload.critical_load_fraction)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
