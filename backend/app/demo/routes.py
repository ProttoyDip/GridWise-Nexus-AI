"""Measured dashboard and a fixed, repeatable emergency demonstration."""

import json
import logging
import os
import queue
import threading
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from app.explainability.generator import effective_solar, generate_explanation
from app.models.request import ScenarioRequest
from app.models.response import DirectiveInterpretation, HourlyPlanEntry, OptimizeResponse
from app.monitoring.progress import observe_progress
from app.verifier.schedule_checker import recalculate_totals, verify_schedule
from app.monitoring import logger
from app.simulation.scenario_generator import Uncertainty
from app.simulation.simulator import simulate

log = logging.getLogger(__name__)


def demo_enabled():
    if os.getenv("GRIDWISE_ENABLE_DEMO", "0") != "1":
        raise HTTPException(status_code=404, detail="Demo disabled")


router = APIRouter(prefix="/demo", dependencies=[Depends(demo_enabled)], include_in_schema=False)
HERE = Path(__file__).parent


@router.get("/plotly.js")
def chart_library():
    return FileResponse(HERE / "vendor/plotly-basic-4.0.0.min.js", media_type="application/javascript")


@router.get("/app.js")
def dashboard_script():
    return FileResponse(HERE / "app.js", media_type="application/javascript")


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

    @field_validator("operator_notes")
    @classmethod
    def validate_notes(cls, notes):
        if any(not note.strip() or len(note) > 2000 for note in notes):
            raise ValueError("Each instruction must contain 1–2000 characters")
        return [note.strip() for note in notes]


def _format_hours(hours):
    groups = []
    for hour in hours:
        if groups and hour == groups[-1][-1] + 1:
            groups[-1].append(hour)
        else:
            groups.append([hour])
    return ", ".join(f"{group[0]:02d}:00–{group[-1] + 1:02d}:00" for group in groups)


def _directive_card(d: DirectiveInterpretation) -> dict:
    """Public operational summaries; confidence evidence remains private."""
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
    hours = adj.get("hours", [])
    if hours:
        summary.append("Hours: " + _format_hours(hours))
    if d.directive_type == "solar_reduction":
        hours = adj.get("hours") or []
        factor = adj.get("factor")
        if factor is not None:
            summary.append(f"Reduction: {int(round((1 - factor) * 100))}%")
    elif d.directive_type == "minimum_battery_reserve":
        min_kwh = adj.get("minimum_energy_kwh")
        if min_kwh is not None:
            summary.append(f"Minimum: {min_kwh:.0f} kWh")
    elif d.directive_type == "max_grid_window":
        hours = adj.get("hours") or []
        cap = adj.get("max_grid_kwh")
        if cap is not None:
            summary.append(f"Cap: {cap:.0f} kWh/h")

    return {
        "icon": icon,
        "title": title,
        "applies": d.applies,
        "directive_type": d.directive_type,
        "summary": summary,
        "explanation": d.explanation,
    }


@router.post("/run-optimization")
def run_optimization(req: OperatorRunRequest) -> dict:
    """One real interpretation/optimization run with a labelled idle reference."""
    from app.api.optimize import optimize_energy

    scenario = demo_scenario()
    scenario.operator_notes = req.operator_notes
    operator_resp = optimize_energy(scenario)
    solar = effective_solar(scenario, operator_resp.directive_interpretation)
    reference_plan = [HourlyPlanEntry(
        hour=h.hour, grid_kwh=max(0, h.demand_kwh - solar[h.hour]),
        solar_used_kwh=min(h.demand_kwh, solar[h.hour]), battery_action="idle", battery_kwh=0,
        battery_energy_after_kwh=scenario.battery.initial_energy_kwh,
    ) for h in sorted(scenario.hours, key=lambda h: h.hour)]
    totals = recalculate_totals(scenario.hours, reference_plan)
    baseline_resp = OptimizeResponse(
        scenario_id=scenario.scenario_id,
        directive_interpretation=[DirectiveInterpretation(note_index=i, applies=False, directive_type="no_op",
            explanation="Idle-battery reference excludes operator constraints.") for i in range(len(req.operator_notes))],
        hourly_plan=reference_plan, total_grid_kwh=totals.total_grid_kwh, total_cost_bdt=totals.total_cost_bdt,
        peak_grid_kwh=totals.peak_grid_kwh, plan_summary="Grid/solar reference with idle battery; operator reserve and grid limits are not enforced.",
    )
    verify_schedule(scenario, baseline_resp)

    baseline_cost = baseline_resp.total_cost_bdt
    operator_cost = operator_resp.total_cost_bdt
    savings_bdt = baseline_cost - operator_cost
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
            "grid_kwh": baseline_resp.total_grid_kwh - operator_resp.total_grid_kwh,
        },
        "agents": [
            {"name": "Interpretation Agent", "status": "completed"},
            {"name": "Safety Agent", "status": "passed"},
            {"name": "Optimization Agent", "status": "completed"},
            {"name": "Explanation Agent", "status": "ready"},
        ],
        "battery": scenario.battery.model_dump(),
        "forecast": [h.model_dump() for h in sorted(scenario.hours, key=lambda h: h.hour)],
        "explanation": generate_explanation(scenario, operator_resp.hourly_plan, operator_resp.directive_interpretation),
    }


_stream_slots = threading.BoundedSemaphore(4)


@router.post("/run-optimization-stream")
def run_optimization_stream(req: OperatorRunRequest):
    """Stream safe stage summaries from actual pipeline events, plus results."""
    if not _stream_slots.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="All operator slots are busy. Try again shortly.")
    events = queue.Queue()

    def work():
        try:
            with observe_progress(events.put):
                result = run_optimization(req)
            events.put({"event": "result", "data": result})
        except HTTPException as exc:
            events.put({"event": "error", "status": exc.status_code,
                        "detail": "No valid schedule could be generated. Review the instructions and system status."})
        except Exception:
            events.put({"event": "error", "status": 500, "detail": "Optimization failed. Try again or review system status."})
        finally:
            events.put(None)
            _stream_slots.release()

    def stream():
        while True:
            try:
                event = events.get(timeout=10)
            except queue.Empty:
                yield json.dumps({"event": "heartbeat"}) + "\n"
                continue
            if event is None:
                return
            yield json.dumps(event, ensure_ascii=True, allow_nan=False) + "\n"

    try:
        threading.Thread(target=work, daemon=True).start()
    except Exception:
        _stream_slots.release()
        raise
    return StreamingResponse(stream(), media_type="application/x-ndjson", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
