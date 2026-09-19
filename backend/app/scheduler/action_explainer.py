"""Human-readable reason/impact text per action type. Confidence stays internal."""

from __future__ import annotations

_TEXT = {
    "BATTERY_CHARGE": (
        "Battery charged during low tariff hours.",
        "Reduced evening grid dependency and lower energy cost.",
    ),
    "BATTERY_DISCHARGE": (
        "Battery discharged when electricity is most expensive.",
        "Reduced peak demand and peak-hour cost.",
    ),
    "SOLAR_PRIORITY": (
        "High renewable availability in this window.",
        "More on-site solar consumed, less grid import.",
    ),
    "GRID_REDUCTION": (
        "Grid import is highest in this hour.",
        "Lower peak grid draw and feeder stress.",
    ),
    "BATTERY_PROTECTION": (
        "Battery reached its minimum reserve.",
        "Reserve maintained so the battery stays within safe limits.",
    ),
}


def explain(action_type: str) -> dict[str, str]:
    reason, impact = _TEXT[action_type]
    return {"reason": reason, "expected_impact": impact}
