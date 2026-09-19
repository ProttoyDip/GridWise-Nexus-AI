"""Builds the human action timeline from an optimized plan."""

from __future__ import annotations

from typing import Any

from app.scheduler.action_explainer import explain
from app.scheduler.schedule_analyzer import analyze, contiguous_windows


def _hh(hour: int) -> str:
    return f"{hour % 24:02d}:00"


def _make(action_type: str, start: int, end: int, priority: str, confidence: float, **extra: Any) -> dict[str, Any]:
    entry = {
        "type": action_type,
        "start_time": _hh(start),
        "end_time": _hh(end + 1),
        "priority": priority,
        **explain(action_type),
        "_confidence": confidence,
    }
    entry.update(extra)
    return entry


def generate_daily_actions(plan: list[Any], hours: list[Any], battery: dict[str, Any]) -> dict[str, Any]:
    """Return {"daily_actions": [...]} sorted by start time. Only meaningful
    events are emitted: one-hour idle blips and trivial windows are skipped."""
    a = analyze(plan, hours, battery)
    actions: list[dict[str, Any]] = []

    for start, end in contiguous_windows(a["charge_hours"]):
        avg = sum(a["tariffs"][h] for h in range(start, end + 1)) / (end - start + 1)
        cheap = avg <= a["low_tariff_cut"]
        act = _make("BATTERY_CHARGE", start, end, "HIGH" if cheap else "MEDIUM", 0.9 if cheap else 0.75)
        if not cheap:
            act["reason"] = "Battery charged to prepare for higher-tariff hours."
        actions.append(act)

    for start, end in contiguous_windows(a["discharge_hours"]):
        avg = sum(a["tariffs"][h] for h in range(start, end + 1)) / (end - start + 1)
        peak = avg >= a["high_tariff_cut"]
        if not peak and end == start:
            continue
        act = _make("BATTERY_DISCHARGE", start, end, "HIGH" if peak else "LOW", 0.9 if peak else 0.7)
        if not peak:
            act["reason"] = "Battery discharged to cover demand and avoid grid import."
            act["expected_impact"] = "Lower grid energy purchased in this window."
        actions.append(act)

    for start, end in contiguous_windows(a["solar_hours"]):
        if end - start + 1 >= 2:
            actions.append(_make("SOLAR_PRIORITY", start, end, "MEDIUM", 0.85))

    if a["peak_grid_hour"] is not None and a["discharge_hours"]:
        hour = a["peak_grid_hour"]
        if a["tariffs"].get(hour, 0) >= a["high_tariff_cut"]:
            actions.append(_make("GRID_REDUCTION", hour, hour, "HIGH", 0.8))

    for start, end in contiguous_windows(a["near_reserve_hours"]):
        act = _make("BATTERY_PROTECTION", start, end, "HIGH", 0.95)
        act["reason"] = f"Battery is at its minimum reserve of {a['reserve_kwh']:g} kWh."
        actions.append(act)

    actions.sort(key=lambda x: (x["start_time"], x["type"]))
    return {"daily_actions": [{k: v for k, v in x.items() if not k.startswith("_")} for x in actions]}
