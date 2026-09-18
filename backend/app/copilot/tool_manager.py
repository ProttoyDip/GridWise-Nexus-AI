"""Copilot tool wrappers.

Every function here calls an *existing* GridWise service directly (plain
Python function calls, not HTTP round-trips) — this module adds no new
optimization, interpretation, simulation, or explanation logic of its
own. Each tool returns a plain, JSON-safe dict and never raises: a tool
failure is reported as ``{"ok": False, "error": ...}`` so the agent can
still produce a graceful chat reply instead of a 500.

Imports of the underlying GridWise modules are deferred into each
function body (not module-level) to avoid import-order/circularity
issues with app.api.optimize, which itself imports from app.llm and
app.optimizer — the same lazy-import pattern app/demo/routes.py already
uses for the same reason.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.models.request import ScenarioRequest
    from app.models.response import DirectiveInterpretation, HourlyPlanEntry

logger = logging.getLogger(__name__)

DEFAULT_SCENARIO_ID = "COPILOT-SESSION"


def _default_hours() -> list[dict]:
    """A fixed, illustrative 24h forecast used only when the dashboard
    hasn't supplied its own current scenario via `context` — never
    presented as a real forecast, only as a basis for a demo/standalone
    Copilot response."""
    return [
        {
            "hour": h,
            "demand_kwh": 150.0,
            "solar_kwh": max(0.0, 100.0 - abs(h - 12) * 9.0),
            "tariff_bdt_per_kwh": 18.0 if 18 <= h < 22 else (6.0 if h < 6 else 10.0),
        }
        for h in range(24)
    ]


def _default_battery() -> dict:
    return {
        "capacity_kwh": 200.0,
        "initial_energy_kwh": 100.0,
        "minimum_energy_kwh": 30.0,
        "max_charge_kwh_per_hour": 50.0,
        "max_discharge_kwh_per_hour": 50.0,
    }


def build_scenario(operator_note: str, context: dict[str, Any] | None) -> "ScenarioRequest":
    """Build a ScenarioRequest for a tool call.

    `context`, when supplied by the dashboard, may carry
    {"scenario_id", "hours", "battery"} — the same shape as
    /optimize-energy's request body minus operator_notes, which always
    comes from the user's actual chat message so the Copilot never
    invents an operating instruction the user didn't give. Missing
    pieces fall back to a fixed default scenario (see _default_hours/
    _default_battery) so the Copilot still functions without a
    dashboard-supplied scenario.
    """
    from app.models.request import ScenarioRequest

    context = context or {}
    note = (operator_note or "").strip() or "No special operating conditions today"
    return ScenarioRequest.model_validate(
        {
            "scenario_id": context.get("scenario_id") or DEFAULT_SCENARIO_ID,
            "operator_notes": [note],
            "hours": context.get("hours") or _default_hours(),
            "battery": context.get("battery") or _default_battery(),
        }
    )


def optimize_energy(operator_note: str, context: dict[str, Any] | None) -> dict[str, Any]:
    """Tool: optimize_energy() — reuses the real /optimize-energy pipeline
    (interpretation, guardrails, solver, verifier) end to end."""
    from app.api.optimize import optimize_energy as _optimize_energy

    try:
        scenario = build_scenario(operator_note, context)
        response = _optimize_energy(scenario)
        return {
            "ok": True,
            "scenario": scenario,
            "directives": response.directive_interpretation,
            "plan": response.hourly_plan,
            "total_cost_bdt": response.total_cost_bdt,
            "total_grid_kwh": response.total_grid_kwh,
            "peak_grid_kwh": response.peak_grid_kwh,
            "plan_summary": response.plan_summary,
        }
    except Exception as exc:  # noqa: BLE001 - tool failures must never crash the chat
        logger.warning("Copilot optimize_energy tool failed (%s): %s", type(exc).__name__, exc)
        return {"ok": False, "error": str(exc)}


def explain_schedule(
    scenario: "ScenarioRequest | None",
    directives: "list[DirectiveInterpretation] | None",
    plan: "list[HourlyPlanEntry] | None",
) -> dict[str, Any]:
    """Tool: explain_schedule() — reuses the existing explainability
    generator against an already-computed result. Never fabricates an
    explanation independent of a real optimization."""
    if scenario is None or directives is None or plan is None:
        return {"ok": False, "error": "no previous optimization result in this session"}

    from app.explainability.generator import generate_explanation

    try:
        return {"ok": True, "explanation": generate_explanation(scenario, plan, directives)}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Copilot explain_schedule tool failed (%s): %s", type(exc).__name__, exc)
        return {"ok": False, "error": str(exc)}


def _infer_uncertainty_from_message(message: str):
    """Best-effort: turn an explicit percentage in the message (e.g. "what
    if solar drops by 50%") into a concrete stress factor. Returns None
    when no explicit percentage is stated, so the caller falls back to
    app.simulation.scenario_generator's existing keyword heuristic."""
    from app.simulation.scenario_generator import Uncertainty

    text = (message or "").casefold()
    match = re.search(r"(\d{1,3})\s*%", text)
    if not match:
        return None
    pct = min(100, max(0, int(match.group(1))))
    remaining = round(1 - pct / 100, 2)
    if "solar" in text:
        return Uncertainty(solar_factors=(remaining,))
    if "demand" in text or "load" in text:
        return Uncertainty(demand_factors=(round(1 + pct / 100, 2),))
    if "battery" in text:
        return Uncertainty(battery_availability=(remaining,))
    return None


def simulate_scenario(
    operator_note: str,
    context: dict[str, Any] | None,
    base_scenario: "ScenarioRequest | None" = None,
    base_directives: "list[DirectiveInterpretation] | None" = None,
    base_plan: "list[HourlyPlanEntry] | None" = None,
) -> dict[str, Any]:
    """Tool: simulate_scenario() — reuses the existing digital-twin
    simulator (app.simulation.simulator.simulate) to compare the current
    plan against stress-test futures. If no prior optimization exists in
    this session, first runs optimize_energy() to establish a nominal
    plan (never simulates against an invented baseline)."""
    from app.simulation.scenario_generator import infer_uncertainty
    from app.simulation.simulator import simulate

    try:
        scenario, directives, plan = base_scenario, base_directives, base_plan
        if scenario is None or directives is None or plan is None:
            primary = optimize_energy(operator_note, context)
            if not primary["ok"]:
                return primary
            scenario, directives, plan = primary["scenario"], primary["directives"], primary["plan"]

        uncertainty = _infer_uncertainty_from_message(operator_note) or infer_uncertainty([operator_note])
        result = simulate(scenario, directives, uncertainty, nominal_plan=plan)
        # Returned so the caller can remember the nominal scenario/directives
        # this simulation was run against, e.g. to answer a later "why?"
        # even if the session had no prior optimize_energy call.
        return {"ok": True, "result": result, "scenario": scenario, "directives": directives, "plan": plan}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Copilot simulate_scenario tool failed (%s): %s", type(exc).__name__, exc)
        return {"ok": False, "error": str(exc)}


def system_status() -> dict[str, Any]:
    """Tool: system_status() — reuses the real GET /system/status handler."""
    from app.api.system_status import get_system_status

    try:
        return {"ok": True, "status": get_system_status()}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Copilot system_status tool failed (%s): %s", type(exc).__name__, exc)
        return {"ok": False, "error": str(exc)}
