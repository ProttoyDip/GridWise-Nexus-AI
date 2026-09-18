"""Deterministic stress scenarios, never asserted to be probabilistic forecasts."""

import math
import re
from dataclasses import dataclass

from app.models.request import ScenarioRequest


@dataclass(frozen=True)
class FutureScenario:
    name: str
    scenario: ScenarioRequest
    assumption: str


@dataclass(frozen=True)
class Uncertainty:
    hours: tuple[int, ...] = tuple(range(12, 18))
    solar_factors: tuple[float, ...] = ()
    demand_factors: tuple[float, ...] = ()
    battery_availability: tuple[float, ...] = ()

    def __post_init__(self):
        if any(type(h) is not int or not 0 <= h <= 23 for h in self.hours) or tuple(sorted(set(self.hours))) != self.hours:
            raise ValueError("Uncertainty hours must be unique ascending integers 0–23")
        if sum(map(len, (self.solar_factors, self.demand_factors, self.battery_availability))) > 8:
            raise ValueError("At most eight stress futures are supported")
        for values, lower, upper in ((self.solar_factors, 0, 1), (self.demand_factors, 1, 5), (self.battery_availability, 0, 1)):
            if any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) or not lower <= v <= upper for v in values):
                raise ValueError("Invalid uncertainty factor")


def infer_uncertainty(notes: list[str]) -> Uncertainty:
    """Notes only trigger stress tests; no invented operating directives.

    Defaults are declared assumptions (afternoon 12–18). Explicit settings
    should be supplied to generate_scenarios for other windows or severity.
    """
    text = " ".join(notes).casefold()
    uncertain = bool(re.search(r"\b(may|might|uncertain|risk|possible|unavailable|outage)\b", text))
    if not uncertain:
        return Uncertainty()
    return Uncertainty(
        solar_factors=(0.8, 0.5, 0.0) if re.search(r"\b(solar|pv)\b", text) else (),
        demand_factors=(1.1, 1.3) if re.search(r"\b(demand|load)\b", text) else (),
        battery_availability=(0.5, 0.0) if re.search(r"\b(battery|outage)\b", text) else (),
    )


def generate_scenarios(scenario: ScenarioRequest, uncertainty: Uncertainty | None = None) -> list[FutureScenario]:
    settings = uncertainty if uncertainty is not None else infer_uncertainty(scenario.operator_notes)
    futures = [FutureScenario("nominal", scenario.model_copy(deep=True), "Submitted forecast")]
    for kind, values in (("solar", settings.solar_factors), ("demand", settings.demand_factors), ("battery", settings.battery_availability)):
        for index, factor in enumerate(values):
            data = scenario.model_dump()
            for hour in data["hours"]:
                if hour["hour"] in settings.hours and kind in ("solar", "demand"):
                    hour["solar_kwh" if kind == "solar" else "demand_kwh"] *= factor
            if kind == "battery":
                # Availability reduces power capability, preserving capacity,
                # initial energy, reserve floors and end-of-day equality.
                data["battery"]["max_charge_kwh_per_hour"] *= factor
                data["battery"]["max_discharge_kwh_per_hour"] *= factor
            data["scenario_id"] += f"::twin:{kind}:{index}"
            futures.append(FutureScenario(f"{kind}_{index}", ScenarioRequest.model_validate(data),
                           f"{kind} factor {factor} " + (f"at hours {list(settings.hours)}" if kind != "battery" else "for battery charge/discharge capability all day")))
    return futures
