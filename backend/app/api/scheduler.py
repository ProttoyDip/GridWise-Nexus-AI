"""POST /scheduler/actions — additive endpoint (not part of the fixed
/optimize-energy contract). Runs the existing optimizer, then converts its
plan into operator actions without altering it."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.api.optimize import optimize_energy
from app.models.request import ScenarioRequest
from app.scheduler import generate_daily_actions

router = APIRouter()


@router.post("/scheduler/actions")
def scheduler_actions(scenario: ScenarioRequest) -> dict[str, Any]:
    result = optimize_energy(scenario)
    payload = scenario.model_dump()
    return generate_daily_actions(result.hourly_plan, payload["hours"], payload["battery"])
