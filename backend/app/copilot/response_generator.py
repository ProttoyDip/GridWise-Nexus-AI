"""Turns raw tool results into the Copilot's chat reply text and
`visual_data` cards for the frontend.

Every number surfaced here is copied from an existing GridWise tool
result (app.copilot.tool_manager) — this module formats and labels, it
never computes or invents a value. Each reply explicitly distinguishes
calculated results ("optimization complete"), simulations ("hypothetical
projections, not applied changes"), and recommendations, per the
Copilot's system prompt rules.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.models.response import DirectiveInterpretation, HourlyPlanEntry

_DIRECTIVE_LABELS = {
    "solar_reduction": "Solar Reduction",
    "minimum_battery_reserve": "Battery Reserve Floor",
    "no_charge_window": "No-Charge Window",
    "no_discharge_window": "No-Discharge Window",
    "max_grid_window": "Peak Grid Cap",
    "no_op": "No Constraint",
}


def _hour_window(hours: list[int]) -> str | None:
    if not hours:
        return None
    return f"{hours[0]:02d}:00-{(hours[-1] + 1) % 24:02d}:00"


def _battery_windows(plan: "list[HourlyPlanEntry]") -> dict[str, str | None]:
    charge = [e.hour for e in plan if e.battery_action == "charge" and e.battery_kwh > 1e-6]
    discharge = [e.hour for e in plan if e.battery_action == "discharge" and e.battery_kwh > 1e-6]
    return {"charge_window": _hour_window(charge), "discharge_window": _hour_window(discharge)}


def _directive_cards(directives: "list[DirectiveInterpretation]") -> list[dict[str, Any]]:
    cards = []
    for directive in directives:
        if not directive.applies:
            continue
        adjustment = directive.structured_adjustment or {}
        cards.append(
            {
                "type": "directive",
                "title": _DIRECTIVE_LABELS.get(directive.directive_type, directive.directive_type),
                "directive_type": directive.directive_type,
                "time_window": _hour_window(adjustment.get("hours") or []),
                "status": "Validated",
                "explanation": directive.explanation,
            }
        )
    return cards


def optimization_reply(tool_result: dict[str, Any]) -> dict[str, Any]:
    if not tool_result.get("ok"):
        return {
            "reply": (
                f"I couldn't complete that optimization: {tool_result.get('error', 'unknown error')}. "
                "You can ask me to check system status to see what's affected."
            ),
            "action_taken": "optimize_energy_failed",
            "visual_data": None,
        }

    plan = tool_result["plan"]
    windows = _battery_windows(plan)
    cost = tool_result["total_cost_bdt"]
    grid = tool_result["total_grid_kwh"]
    peak = tool_result["peak_grid_kwh"]
    directive_cards = _directive_cards(tool_result["directives"])

    lines = [
        "Optimization complete ✓ (a calculated GridWise result, not a simulation).",
        f"Cost: {cost:,.2f} BDT · Grid usage: {grid:,.1f} kWh · Peak: {peak:,.1f} kWh.",
    ]
    if windows["charge_window"] or windows["discharge_window"]:
        parts = []
        if windows["charge_window"]:
            parts.append(f"charge {windows['charge_window']}")
        if windows["discharge_window"]:
            parts.append(f"discharge {windows['discharge_window']}")
        lines.append("Battery strategy: " + ", ".join(parts) + ".")

    return {
        "reply": "\n".join(lines),
        "action_taken": "optimize_energy",
        "visual_data": {
            "kind": "optimization_result",
            "cost_bdt": cost,
            "grid_kwh": grid,
            "peak_grid_kwh": peak,
            **windows,
            "directive_cards": directive_cards,
            "plan_summary": tool_result["plan_summary"],
        },
    }


def explanation_reply(tool_result: dict[str, Any]) -> dict[str, Any]:
    if not tool_result.get("ok"):
        return {
            "reply": (
                "I don't have a previous optimization result to explain yet in this session — "
                "ask me to optimize your energy usage first, then I can walk through the decision."
            ),
            "action_taken": "explain_schedule_unavailable",
            "visual_data": None,
        }

    explanation = tool_result["explanation"]
    why_points = list(explanation.get("battery_reasons", [])) + list(explanation.get("constraint_explanations", []))
    reply = "Why this strategy (from the actual computed schedule, not a guess):\n" + "\n".join(
        f"✓ {point}" for point in why_points[:6]
    )
    return {
        "reply": reply,
        "action_taken": "explain_schedule",
        "visual_data": {
            "kind": "explanation",
            "why_this_strategy": why_points,
            "cost_explanation": explanation.get("cost_explanation"),
        },
    }


def simulation_reply(tool_result: dict[str, Any]) -> dict[str, Any]:
    if not tool_result.get("ok"):
        return {
            "reply": f"I couldn't run that simulation: {tool_result.get('error', 'unknown error')}.",
            "action_taken": "simulate_scenario_failed",
            "visual_data": None,
        }

    result = tool_result["result"]
    outcomes = result.get("outcomes", [])
    risk = result.get("risk", {})
    lines = ["Simulation complete — these are hypothetical projections, not applied changes:"]
    for outcome in outcomes:
        if outcome["name"] == "nominal":
            continue
        if outcome["feasible"]:
            lines.append(
                f"• {outcome['assumption']}: cost {outcome['cost_bdt']:,.2f} BDT, "
                f"grid {outcome['grid_kwh']:,.1f} kWh"
            )
        else:
            lines.append(f"• {outcome['assumption']}: infeasible under current constraints")
    if risk.get("recommendation"):
        lines.append(f"Recommendation: {risk['recommendation']}")

    return {
        "reply": "\n".join(lines),
        "action_taken": "simulate_scenario",
        "visual_data": {"kind": "simulation_result", "outcomes": outcomes, "risk": risk},
    }


def status_reply(tool_result: dict[str, Any]) -> dict[str, Any]:
    if not tool_result.get("ok"):
        return {
            "reply": "I couldn't reach GridWise's system status service right now.",
            "action_taken": "system_status_failed",
            "visual_data": None,
        }

    status = tool_result["status"]
    healthy = status.get("healthy")
    models = status.get("model_availability", {}).get("models", [])
    active_models = [m for m in models if not m.get("circuit_open")]
    optimizer = status.get("optimizer_status", {})

    reply = (
        f"System is {'healthy ✓' if healthy else 'degraded ⚠'}. "
        f"{len(active_models)}/{len(models)} configured models are currently available. "
        f"Optimizer ({optimizer.get('solver', 'solver')}): "
        f"{'available' if optimizer.get('available') else 'unavailable'}."
    )
    return {
        "reply": reply,
        "action_taken": "system_status",
        "visual_data": {
            "kind": "system_status",
            "healthy": healthy,
            "active_models": len(active_models),
            "total_models": len(models),
            "optimizer_available": optimizer.get("available"),
        },
    }


# Small, fixed local knowledge base for general (non-tool) energy
# questions. Deliberately not an LLM call: these are stable factual
# definitions, so a fast, free, reproducible lookup is more appropriate
# than spending a model call (and risking an invented answer) on
# something that doesn't need GridWise's live data at all.
_ENERGY_FAQ: list[tuple[tuple[str, ...], str]] = [
    (
        ("peak shaving",),
        "Peak shaving means reducing electricity draw from the grid during the highest-demand "
        "(and usually highest-tariff) hours — typically by discharging a battery or shifting "
        "load — to lower the peak grid import and avoid the most expensive pricing tiers.",
    ),
    (
        ("battery storage", "battery help", "battery work"),
        "Battery storage lets you charge when electricity is cheap or solar is abundant, then "
        "discharge during expensive or high-demand hours, shifting energy use in time to cut "
        "cost and reduce peak grid draw.",
    ),
    (
        ("demand response",),
        "Demand response means adjusting electricity consumption in response to grid conditions "
        "or price signals — for example reducing non-essential load during a grid-constrained "
        "or high-tariff period.",
    ),
    (
        ("solar curtailment", "curtail"),
        "Solar curtailment is deliberately reducing usable solar output below what's physically "
        "available — for example during panel maintenance, or when generation exceeds what "
        "can be used or stored.",
    ),
    (
        ("grid cap", "grid import cap", "max grid"),
        "A grid import cap limits how much power can be drawn from the grid in a given hour, "
        "often due to feeder or transformer capacity limits — GridWise enforces this as a "
        "hard constraint (max_grid_window) when an operator note specifies one.",
    ),
    (
        ("tariff",),
        "A tariff is the price per kWh charged for grid electricity in a given hour; GridWise's "
        "optimizer schedules battery charging and discharging around tariff changes to minimize "
        "total cost.",
    ),
]

_FALLBACK_GENERAL_REPLY = (
    "I can help with energy optimization, explaining past decisions, simulating what-if "
    "scenarios, and system status — try one of the quick actions, or ask about a specific "
    "energy concept like peak shaving or battery storage."
)


def _general_energy_answer(message: str) -> str:
    text = (message or "").casefold()
    for keywords, answer in _ENERGY_FAQ:
        if any(keyword in text for keyword in keywords):
            return answer
    return _FALLBACK_GENERAL_REPLY


def general_reply(message: str) -> dict[str, Any]:
    return {
        "reply": _general_energy_answer(message),
        "action_taken": "general_energy_query",
        "visual_data": None,
    }
