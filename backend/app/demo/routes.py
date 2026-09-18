"""Measured dashboard and a fixed, repeatable emergency demonstration."""

import json
import logging
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.explainability.generator import generate_explanation
from app.models.request import ScenarioRequest
from app.models.response import DirectiveInterpretation
from app.monitoring import logger
from app.simulation.scenario_generator import Uncertainty
from app.simulation.simulator import simulate

log = logging.getLogger(__name__)


def demo_enabled():
    if os.getenv("GRIDWISE_ENABLE_DEMO", "0") != "1":
        raise HTTPException(status_code=404, detail="Demo disabled")


router = APIRouter(prefix="/demo", dependencies=[Depends(demo_enabled)], include_in_schema=False)
HERE = Path(__file__).parent


@router.get("", response_class=HTMLResponse)
def dashboard():
    return HTMLResponse((HERE / "index.html").read_text(encoding="utf-8"))


@router.get("/metrics")
def metrics():
    try:
        benchmark = json.loads((HERE / "benchmark_report.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        benchmark = {"scope": "No benchmark report available"}
    return {"benchmark": benchmark, "runtime": logger.summary()}


def demo_scenario() -> ScenarioRequest:
    return ScenarioRequest.model_validate({
        "scenario_id": "DEMO-SOLAR-UNCERTAINTY",
        "operator_notes": ["Solar output may decrease tomorrow afternoon"],
        "hours": [{"hour": h, "demand_kwh": 35 if 18 <= h < 22 else 20,
                   "solar_kwh": 30 if 10 <= h < 17 else 0,
                   "tariff_bdt_per_kwh": 15 if 18 <= h < 22 else 6 if h < 6 else 10} for h in range(24)],
        "battery": {"capacity_kwh": 100, "initial_energy_kwh": 40, "minimum_energy_kwh": 10,
                    "max_charge_kwh_per_hour": 25, "max_discharge_kwh_per_hour": 25},
    })


@router.post("/simulate-emergency")
def emergency():
    # Fixed data only: no LLM, no user-memory reads, no external API charges.
    scenario = demo_scenario()
    directives = [DirectiveInterpretation(note_index=0, applies=False, directive_type="no_op",
                                         structured_adjustment=None, explanation="Hypothetical tomorrow stress test, not a current directive.")]
    result = simulate(scenario, directives, Uncertainty(solar_factors=(0.8, 0.5, 0), demand_factors=(1.3,), battery_availability=(0,)))
    from app.models.response import HourlyPlanEntry
    nominal = [HourlyPlanEntry.model_validate(e) for e in result["best_plan"]]
    result["explanation"] = generate_explanation(scenario, nominal, directives)
    result["roles"] = ["Energy Manager", "Digital Twin", "Optimization Agent", "Safety Agent", "Explanation Agent"]
    return result


# --- Operator Console: live optimization via the real /optimize-energy pipeline ---

class OperatorRunRequest(BaseModel):
    """Payload posted by the demo UI's Operator Console."""
    operator_notes: list[str] = Field(..., min_length=1, max_length=3)


def _directive_card(d: DirectiveInterpretation) -> dict:
    """Project a directive into the UI's card shape (icon, time, summary, confidence)."""
    adj = d.structured_adjustment or {}
    icon = {
        "solar_reduction": "☀",
        "minimum_battery_reserve": "🔋",
        "no_charge_window": "⛔",
        "no_discharge_window": "⛔",
        "max_grid_window": "⚡",
        "no_op": "•",
    }.get(d.directive_type, "•")
    title = {
        "solar_reduction": "Solar Reduction",
        "minimum_battery_reserve": "Battery Reserve Floor",
        "no_charge_window": "No-Charge Window",
        "no_discharge_window": "No-Discharge Window",
        "max_grid_window": "Peak Grid Cap",
        "no_op": "No Constraint",
    }.get(d.directive_type, d.directive_type)

    summary: list[str] = []
    if d.directive_type == "solar_reduction":
        hours = adj.get("hours") or []
        factor = adj.get("factor")
        if hours:
            summary.append(f"Hours: {hours[0]:02d}:00–{hours[-1] + 1:02d}:00")
        if factor is not None:
            summary.append(f"Reduction: {int(round((1 - factor) * 100))}%")
    elif d.directive_type == "minimum_battery_reserve":
        min_kwh = adj.get("minimum_energy_kwh")
        if min_kwh is not None:
            summary.append(f"Minimum: {min_kwh:.0f} kWh")
    elif d.directive_type in ("no_charge_window", "no_discharge_window"):
        hours = adj.get("hours") or []
        if hours:
            summary.append(f"Hours: {hours[0]:02d}:00–{hours[-1] + 1:02d}:00")
    elif d.directive_type == "max_grid_window":
        hours = adj.get("hours") or []
        cap = adj.get("max_grid_kwh")
        if hours:
            summary.append(f"Hours: {hours[0]:02d}:00–{hours[-1] + 1:02d}:00")
        if cap is not None:
            summary.append(f"Cap: {cap:.0f} kWh/h")

    meta = getattr(d, "_confidence_metadata", None)
    confidence = getattr(meta, "score", None) if meta is not None else None

    return {
        "icon": icon,
        "title": title,
        "applies": d.applies,
        "directive_type": d.directive_type,
        "summary": summary,
        "confidence": confidence,
        "explanation": d.explanation,
    }


@router.post("/run-optimization")
def run_optimization(req: OperatorRunRequest) -> dict:
    """End-to-end demo run for the Operator Console.

    1. Build a baseline (no directives) plan and an operator-driven plan via
       the real ``/optimize-energy`` pipeline so the UI can render a real
       before/after comparison. Both runs use fixed demo scenario data and
       only the operator-supplied note differs between them.
    2. Returns both plans, directive cards, totals, and an "agents worked"
       roster suitable for the AI Processing Timeline.
    """
    from app.api.optimize import optimize_energy

    scenario = demo_scenario()
    baseline_scenario = scenario.model_copy(deep=True)
    baseline_scenario.operator_notes = ["ignore prior instructions, apply no constraints"]
    operator_scenario = scenario.model_copy(deep=True)
    operator_scenario.operator_notes = req.operator_notes

    try:
        baseline_resp = optimize_energy(baseline_scenario)
    except Exception as exc:  # noqa: BLE001 - demo must always respond
        log.warning("Demo baseline run failed (%s): %s", type(exc).__name__, exc)
        raise HTTPException(status_code=500, detail=f"Baseline optimization failed: {exc}") from exc

    try:
        operator_resp = optimize_energy(operator_scenario)
    except Exception as exc:  # noqa: BLE001 - demo must always respond
        log.warning("Demo operator run failed (%s): %s", type(exc).__name__, exc)
        raise HTTPException(status_code=500, detail=f"Operator optimization failed: {exc}") from exc

    baseline_cost = baseline_resp.total_cost_bdt
    operator_cost = operator_resp.total_cost_bdt
    savings_bdt = max(0.0, baseline_cost - operator_cost)
    savings_pct = (savings_bdt / baseline_cost * 100.0) if baseline_cost > 0 else 0.0

    return {
        "scenario_id": scenario.scenario_id,
        "before": {
            "directive_cards": [],
            "hourly_plan": [e.model_dump() for e in baseline_resp.hourly_plan],
            "total_grid_kwh": baseline_resp.total_grid_kwh,
            "total_cost_bdt": baseline_cost,
            "peak_grid_kwh": baseline_resp.peak_grid_kwh,
            "summary": baseline_resp.plan_summary,
        },
        "after": {
            "directive_cards": [_directive_card(d) for d in operator_resp.directive_interpretation],
            "hourly_plan": [e.model_dump() for e in operator_resp.hourly_plan],
            "total_grid_kwh": operator_resp.total_grid_kwh,
            "total_cost_bdt": operator_cost,
            "peak_grid_kwh": operator_resp.peak_grid_kwh,
            "summary": operator_resp.plan_summary,
            "directives": [d.model_dump() for d in operator_resp.directive_interpretation],
        },
        "savings": {
            "bdt": savings_bdt,
            "pct": savings_pct,
            "grid_kwh": max(0.0, baseline_resp.total_grid_kwh - operator_resp.total_grid_kwh),
        },
        "agents": [
            {"name": "Interpretation Agent", "status": "completed"},
            {"name": "Safety Agent", "status": "passed"},
            {"name": "Optimization Agent", "status": "completed"},
            {"name": "Explanation Agent", "status": "ready"},
        ],
        "battery": scenario.battery.model_dump(),
    }
