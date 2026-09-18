"""Bounded history of verified solutions used only as CBC incumbent hints."""

from dataclasses import dataclass
import math
import threading
import time

from app.models.request import BatteryConfig, HourEntry, ScenarioRequest
from app.models.response import DirectiveInterpretation, HourlyPlanEntry, OptimizeResponse
from app.verifier.schedule_checker import recalculate_totals, verify_schedule


def verify_pattern(hours, battery, directives, plan) -> None:
    """Replay a candidate against all current constraints before trusting it."""
    checked = [directive.model_copy(deep=True) for directive in directives]
    if not checked:
        checked = [DirectiveInterpretation(note_index=0, applies=False, directive_type="no_op",
                                           structured_adjustment=None, explanation="No constraints")]
    for index, directive in enumerate(checked):
        directive.note_index = index
    scenario = ScenarioRequest(scenario_id="warm-start", operator_notes=["constraint"] * len(checked),
                               hours=hours, battery=battery)
    totals = recalculate_totals(hours, plan)
    response = OptimizeResponse(scenario_id=scenario.scenario_id, directive_interpretation=checked,
                                hourly_plan=plan, **vars(totals), plan_summary="Verified warm-start pattern")
    verify_schedule(scenario, response)


def _constraints(directives):
    return sorted((directive.directive_type, tuple(directive.structured_adjustment["hours"]),
                   tuple(sorted((key, value) for key, value in directive.structured_adjustment.items() if key != "hours")))
                  for directive in directives if directive.directive_type != "no_op")


def _distance(old_hours, old_battery, old_directives, hours, battery, directives):
    old_tariffs = [hour.tariff_bdt_per_kwh for hour in old_hours]
    tariffs = [hour.tariff_bdt_per_kwh for hour in hours]
    old_scale, scale = max(max(old_tariffs), 1e-12), max(max(tariffs), 1e-12)
    tariff_distance = sum(abs(a / old_scale - b / scale) for a, b in zip(old_tariffs, tariffs)) / 24
    battery_distance = max(abs(old_battery.capacity_kwh - battery.capacity_kwh) / max(old_battery.capacity_kwh, battery.capacity_kwh),
                           *(abs(getattr(old_battery, field) / old_battery.capacity_kwh - getattr(battery, field) / battery.capacity_kwh)
                             for field in ("initial_energy_kwh", "minimum_energy_kwh", "max_charge_kwh_per_hour", "max_discharge_kwh_per_hour")))
    before, after = _constraints(old_directives), _constraints(directives)
    if len(before) != len(after):
        return None
    constraint_distance = 0.0
    for left, right in zip(before, after):
        if left[:2] != right[:2] or [key for key, _ in left[2]] != [key for key, _ in right[2]]:
            return None
        for (_, a), (_, b) in zip(left[2], right[2]):
            constraint_distance = max(constraint_distance, abs(a - b) / max(abs(a), abs(b), 1e-12))
    if max(tariff_distance, battery_distance, constraint_distance) > 0.1:
        return None
    return tariff_distance + battery_distance + constraint_distance


def _adapt(pattern, hours, battery, directives):
    solar_cap = {hour.hour: hour.solar_kwh for hour in hours}
    for directive in directives:
        if directive.directive_type == "solar_reduction":
            for hour in directive.structured_adjustment["hours"]:
                solar_cap[hour] = min(solar_cap[hour], hours[hour].solar_kwh * directive.structured_adjustment["factor"])
    ratio = battery.capacity_kwh / pattern.battery.capacity_kwh
    energy = battery.initial_energy_kwh
    plan = []
    for hour, old in zip(hours, pattern.plan):
        amount = old.battery_kwh * ratio
        charge = amount if old.battery_action == "charge" else 0
        discharge = amount if old.battery_action == "discharge" else 0
        energy += charge - discharge
        need = hour.demand_kwh + charge - discharge
        if need < 0:
            raise ValueError("Previous discharge exceeds current demand")
        solar = min(need, solar_cap[hour.hour])
        plan.append(HourlyPlanEntry(hour=hour.hour, grid_kwh=need - solar, solar_used_kwh=solar,
                                   battery_action=old.battery_action, battery_kwh=amount,
                                   battery_energy_after_kwh=energy))
    verify_pattern(hours, battery, directives, plan)
    return plan


@dataclass(frozen=True)
class _Pattern:
    hours: list[HourEntry]
    battery: BatteryConfig
    directives: list[DirectiveInterpretation]
    plan: list[HourlyPlanEntry]
    expires_at: float


class WarmStartStore:
    def __init__(self, max_entries=32, ttl_seconds=300, clock=time.monotonic):
        if type(max_entries) is not int or max_entries < 1 or not math.isfinite(ttl_seconds) or ttl_seconds < 0:
            raise ValueError("Warm-start history needs positive capacity and finite non-negative TTL")
        self.max_entries, self.ttl_seconds, self.clock = max_entries, ttl_seconds, clock
        self._patterns = []
        self._lock = threading.Lock()

    def clear(self):
        with self._lock:
            self._patterns.clear()

    def remember(self, hours, battery, directives, plan):
        if self.ttl_seconds == 0:
            return
        hours = sorted(hours, key=lambda hour: hour.hour)
        verify_pattern(hours, battery, directives, plan)
        pattern = _Pattern([hour.model_copy(deep=True) for hour in hours], battery.model_copy(deep=True),
                           [directive.model_copy(deep=True) for directive in directives],
                           [entry.model_copy(deep=True) for entry in plan], self.clock() + self.ttl_seconds)
        with self._lock:
            self._patterns = [old for old in self._patterns if old.expires_at > self.clock()]
            self._patterns.append(pattern)
            self._patterns = self._patterns[-self.max_entries:]

    def find(self, hours, battery, directives):
        hours = sorted(hours, key=lambda hour: hour.hour)
        with self._lock:
            self._patterns = [old for old in self._patterns if old.expires_at > self.clock()]
            patterns = list(self._patterns)
        candidates = []
        for pattern in reversed(patterns):
            distance = _distance(pattern.hours, pattern.battery, pattern.directives, hours, battery, directives)
            if distance is not None:
                candidates.append((distance, pattern))
        for _, pattern in sorted(candidates, key=lambda item: item[0]):
            try:
                return _adapt(pattern, hours, battery, directives)
            except (ValueError, TypeError, OverflowError):
                continue
        return None


warm_start_store = WarmStartStore()
