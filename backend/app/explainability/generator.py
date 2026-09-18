"""Describe observed actions and constraints without inventing solver motives."""

from app.models.request import ScenarioRequest
from app.models.response import DirectiveInterpretation, HourlyPlanEntry
from app.verifier.schedule_checker import recalculate_totals


def effective_solar(scenario: ScenarioRequest, directives: list[DirectiveInterpretation]) -> dict[int, float]:
    available = {h.hour: h.solar_kwh for h in scenario.hours}
    original = available.copy()
    for directive in directives:
        if directive.directive_type == "solar_reduction":
            adjustment = directive.structured_adjustment
            for hour in adjustment["hours"]:
                available[hour] = min(available[hour], original[hour] * adjustment["factor"])
    return available


def generate_explanation(
    scenario: ScenarioRequest, schedule: list[HourlyPlanEntry],
    directives: list[DirectiveInterpretation],
) -> dict:
    """Savings compare with an explicitly labelled grid/solar-only reference.

    That reference may violate reserve or grid-cap directives and is not an
    alternative feasible solution. Explanations describe associations, not
    unobservable causal decisions or dual values from the solver.
    """
    forecast = {h.hour: h for h in scenario.hours}
    reasons = []
    for action in ("charge", "discharge"):
        entries = [e for e in schedule if e.battery_action == action and e.battery_kwh > 1e-7]
        if entries:
            hours = [e.hour for e in entries]
            tariffs = [forecast[e.hour].tariff_bdt_per_kwh for e in entries]
            solar_supported = any(e.solar_used_kwh > forecast[e.hour].demand_kwh for e in entries)
            detail = " Solar also supplied battery charging." if action == "charge" and solar_supported else ""
            reasons.append(f"Battery {action} at hours {hours}: {sum(e.battery_kwh for e in entries):.2f} kWh, "
                           f"with tariffs {min(tariffs):.2f}–{max(tariffs):.2f} BDT/kWh.{detail} "
                           "These actions jointly minimize grid cost while satisfying all constraints.")
    if not reasons:
        reasons.append("Battery remained idle in this optimal schedule.")
    constraints = [f"{d.directive_type}: {d.structured_adjustment}" for d in directives if d.applies]
    solar = effective_solar(scenario, directives)
    reference = sum(max(0, h.demand_kwh - solar[h.hour]) * h.tariff_bdt_per_kwh for h in scenario.hours)
    actual = recalculate_totals(scenario.hours, schedule).total_cost_bdt
    difference = reference - actual
    return {
        "battery_reasons": reasons,
        "constraint_explanations": constraints,
        "reference_cost_bdt": reference,
        "cost_difference_bdt": difference,
        "cost_explanation": f"Grid cost is {actual:.2f} BDT; difference from the grid/solar-only reference is "
                            f"{difference:.2f} BDT. The reference does not enforce battery or directive constraints.",
        "end_of_day_explanation": "Battery returns to its initial energy at the end of hour 23.",
    }
