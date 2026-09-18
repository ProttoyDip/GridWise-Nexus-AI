"""Compare adaptive solutions across futures, with no extra LLM calls."""

from app.models.request import ScenarioRequest
from app.models.response import DirectiveInterpretation, OptimizeResponse
from app.optimizer.scheduler import build_hourly_plan
from app.optimizer.cache import optimization_cache, optimization_cache_key
from app.simulation.scenario_generator import Uncertainty, generate_scenarios
from app.verifier.schedule_checker import recalculate_totals, verify_schedule


def _verified_future(scenario, directives, plan):
    totals = recalculate_totals(scenario.hours, plan)
    response = OptimizeResponse(
        scenario_id=scenario.scenario_id, directive_interpretation=directives,
        hourly_plan=plan, total_grid_kwh=totals.total_grid_kwh,
        total_cost_bdt=totals.total_cost_bdt, peak_grid_kwh=totals.peak_grid_kwh,
        plan_summary="Conditional optimum for this hypothetical future.",
    )
    verify_schedule(scenario, response)
    return totals


def _solve_future(scenario, directives, supplied_plan=None):
    if supplied_plan is not None:
        return supplied_plan, _verified_future(scenario, directives, supplied_plan)
    key = None
    try:
        key = optimization_cache_key(scenario.scenario_id, scenario.hours, scenario.battery, directives)
        cached = optimization_cache.get(key)
        if cached is not None:
            totals = _verified_future(scenario, directives, cached.hourly_plan)
            if abs(totals.total_cost_bdt - cached.total_cost_bdt) > 1e-5:
                raise ValueError("Cached simulation cost mismatch")
            return cached.hourly_plan, totals
    except Exception:
        pass  # An optional cache cannot stop a stress test.
    plan = build_hourly_plan(scenario, directives)
    totals = _verified_future(scenario, directives, plan)
    if key is not None:
        try:
            optimization_cache.put(key, plan, totals.total_cost_bdt)
        except Exception:
            pass
    return plan, totals


def simulate(scenario: ScenarioRequest, directives: list[DirectiveInterpretation],
             uncertainty: Uncertainty | None = None, nominal_plan=None, futures=None) -> dict:
    outcomes = []
    plans = {}
    for future in futures if futures is not None else generate_scenarios(scenario, uncertainty):
        try:
            plan, totals = _solve_future(future.scenario, directives, nominal_plan if future.name == "nominal" else None)
            outcomes.append({"name": future.name, "assumption": future.assumption, "feasible": True,
                             "cost_bdt": totals.total_cost_bdt, "grid_kwh": totals.total_grid_kwh,
                             "charge_kwh": sum(e.battery_kwh for e in plan if e.battery_action == "charge"),
                             "discharge_kwh": sum(e.battery_kwh for e in plan if e.battery_action == "discharge"),
                             "minimum_battery_kwh": min(e.battery_energy_after_kwh for e in plan)})
            plans[future.name] = [e.model_dump() for e in plan]
        except (ValueError, RuntimeError) as exc:
            # Infeasibility or verification failure is a risk outcome, not a
            # reason to relax the operator's constraints.
            outcomes.append({"name": future.name, "assumption": future.assumption, "feasible": False,
                             "error_type": type(exc).__name__})
    feasible = [o for o in outcomes if o["feasible"]]
    worst = max(feasible, key=lambda o: o["cost_bdt"]) if feasible else None
    nominal = next((o for o in feasible if o["name"] == "nominal"), None)
    spread = max(o["cost_bdt"] for o in feasible) - min(o["cost_bdt"] for o in feasible) if feasible else None
    infeasible = sum(not o["feasible"] for o in outcomes)
    return {
        "outcomes": outcomes,
        "best_plan": plans.get("nominal"),
        "best_plan_basis": "Nominal minimum-cost plan; hypothetical plans depend on future information.",
        "contingency_plans": plans,
        "risk": {"infeasible_futures": infeasible, "cost_spread_bdt": spread,
                 "worst_feasible_cost_bdt": worst["cost_bdt"] if worst else None,
                 "worst_cost_increase_bdt": worst["cost_bdt"] - nominal["cost_bdt"] if worst and nominal else None,
                 "probabilities_assigned": False,
                 "recommendation": ("Some stress futures are infeasible. Review reserve and grid limits before operations." if infeasible
                                    else "Review battery reserve and contingency plans for solar or demand uncertainty." if len(outcomes) > 1
                                    else "No uncertainty stress tests were triggered.")},
    }
