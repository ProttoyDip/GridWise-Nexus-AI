"""Warm starts are feasible hints, never fixed decisions or relaxed optima."""

import pytest
import pulp

from app.optimizer import solver
from app.optimizer.warm_start import WarmStartStore, verify_pattern, warm_start_store
from test_optimizer_directives import validated
from test_solver import battery, forecast, cost, check_plan


def training():
    hours = forecast()
    config = battery(initial_energy_kwh=5, max_charge_kwh_per_hour=0, max_discharge_kwh_per_hour=0)
    plan = solver.solve_energy_schedule(hours, config, use_warm_start=False)
    return hours, config, plan


def test_similar_tariff_and_battery_state_adapt_feasible_seed():
    hours, config, plan = training()
    store = WarmStartStore()
    store.remember(hours, config, [], plan)
    hours[0].tariff_bdt_per_kwh *= 1.01
    config.initial_energy_kwh += 0.1
    hours[2].demand_kwh += 1
    seed = store.find(hours, config, [])
    assert seed is not None
    verify_pattern(hours, config, [], seed)
    assert seed[-1].battery_energy_after_kwh == pytest.approx(config.initial_energy_kwh)
    assert seed[2].grid_kwh == pytest.approx(hours[2].demand_kwh)


@pytest.mark.parametrize("difference", ["tariff", "battery", "hours", "numeric", "infeasible"])
def test_incompatible_or_infeasible_patterns_are_rejected(difference):
    hours, config, plan = training()
    directives = validated("max_grid_window", [12], max_grid_kwh=11)
    store = WarmStartStore()
    store.remember(hours, config, directives, plan)
    if difference == "tariff":
        hours[0].tariff_bdt_per_kwh = 50
    elif difference == "battery":
        config.capacity_kwh = 30
    elif difference == "hours":
        directives[0].structured_adjustment["hours"] = [13]
    elif difference == "numeric":
        directives[0].structured_adjustment["max_grid_kwh"] = 100
    else:
        hours[12].demand_kwh = 12
    assert store.find(hours, config, directives) is None


def test_similar_constraints_and_no_op_explanations_do_not_block_reuse():
    hours, config, plan = training()
    directives = validated("max_grid_window", [12], max_grid_kwh=11)
    store = WarmStartStore()
    store.remember(hours, config, directives, plan)
    directives[0].structured_adjustment["max_grid_kwh"] = 11.5
    directives[0].explanation = "Another model explanation"
    assert store.find(hours, config, directives) is not None


def test_invalid_schedules_are_not_remembered_and_copies_are_isolated():
    hours, config, plan = training()
    store = WarmStartStore()
    store.remember(hours, config, [], plan)
    plan[0].grid_kwh += 1
    with pytest.raises(ValueError):
        store.remember(hours, config, [], plan)
    seed = store.find(hours, config, [])
    assert seed[0].grid_kwh == 10
    seed[0].grid_kwh += 1
    assert store.find(hours, config, [])[0].grid_kwh == 10


def test_history_expiry_and_capacity_bound():
    now = [0]
    store = WarmStartStore(max_entries=2, ttl_seconds=10, clock=lambda: now[0])
    hours, config, plan = training()
    for _ in range(3):
        store.remember(hours, config, [], plan)
    assert len(store._patterns) == 2
    now[0] = 10
    assert store.find(hours, config, []) is None


def test_native_warm_start_sets_values_without_fixing_or_changing_optimum(monkeypatch):
    hours = forecast()
    config = battery()
    solver.solve_energy_schedule(hours, config)  # Store a valid flat-price solution.
    hours[0].tariff_bdt_per_kwh = 4.8  # Similar profile, new optimal charge pattern.
    original_solve = pulp.LpProblem.solve
    hints = []
    def capture(model, backend):
        if backend.optionsDict.get("warmStart"):
            hints.append([variable.varValue for variable in model.variables()])
            assert all(variable.lowBound != variable.upBound for variable in model.variables())
            assert backend.optionsDict["gapRel"] == backend.optionsDict["gapAbs"] == 0
        return original_solve(model, backend)
    monkeypatch.setattr(pulp.LpProblem, "solve", capture)
    warm = solver.solve_energy_schedule(hours, config, use_binary_modes=True)
    cold = solver.solve_energy_schedule(hours, config, use_warm_start=False, use_binary_modes=True)
    assert hints and all(value is not None for value in hints[0])
    check_plan(warm, hours, config)
    assert cost(warm, hours) == pytest.approx(cost(cold, hours))
    assert cost(warm, hours) < 1200


@pytest.mark.parametrize("failure", ["history", "unsupported", "nonoptimal"])
def test_warm_start_failures_fall_back_to_normal_solver(monkeypatch, failure):
    hours, config, plan = training()
    warm_start_store.remember(hours, config, [], plan)
    calls = []
    original = pulp.LpProblem.solve
    def solve(model, backend):
        calls.append(bool(backend.optionsDict.get("warmStart")))
        if calls[-1]:
            if failure == "unsupported":
                raise pulp.PulpSolverError("MIP starts unsupported")
            if failure == "nonoptimal":
                return pulp.LpStatusNotSolved
        return original(model, backend)
    monkeypatch.setattr(pulp.LpProblem, "solve", solve)
    if failure == "history":
        monkeypatch.setattr(warm_start_store, "find", lambda *args: (_ for _ in ()).throw(RuntimeError("History broken")))
    result = solver.solve_energy_schedule(hours, config, use_binary_modes=True)
    check_plan(result, hours, config)
    assert calls == ([False] if failure == "history" else [True, False])


def test_integer_feasible_status_is_not_mistaken_for_proven_optimality(monkeypatch):
    def feasible_only(model, backend):
        model.sol_status = pulp.LpSolutionIntegerFeasible
        return pulp.LpStatusOptimal
    monkeypatch.setattr(pulp.LpProblem, "solve", feasible_only)
    with pytest.raises(solver.OptimizationError):
        solver.solve_energy_schedule(forecast(), battery(), use_warm_start=False)


def test_lossless_lp_cancels_simultaneous_actions_without_changing_cost(monkeypatch):
    def simultaneous(model, backend):
        assert not backend.mip
        assert not any(variable.cat == pulp.LpInteger for variable in model.variables())
        for variable in model.variables():
            variable.varValue = (10 if variable.name.startswith("grid_") else
                                 5 if variable.name.startswith("battery_energy_") else
                                 5 if variable.name in ("battery_charge_0", "battery_discharge_0") else 0)
        model.sol_status = pulp.LpSolutionOptimal
        return pulp.LpStatusOptimal
    monkeypatch.setattr(pulp.LpProblem, "solve", simultaneous)
    hours, config = forecast(), battery(initial_energy_kwh=5)
    plan = solver.solve_energy_schedule(hours, config)
    check_plan(plan, hours, config)
    assert all(entry.battery_action == "idle" and entry.battery_kwh == 0 for entry in plan)
    assert cost(plan, hours) == 1200


@pytest.mark.parametrize("case_index", range(10))
def test_lp_and_original_binary_optima_match_all_public_cases(case_index):
    import json
    from pathlib import Path
    from app.guardrails.validator import validate_directive_interpretation
    from app.models.request import ScenarioRequest
    cases = json.loads((Path(__file__).parent / "fixtures/sample_cases.json").read_text())["cases"]
    case = cases[case_index]
    scenario = ScenarioRequest.model_validate(case["input"])
    directives = validate_directive_interpretation(case["expected_output"]["directive_interpretation"], battery=scenario.battery)
    linear = solver.solve_energy_schedule(scenario.hours, scenario.battery, directives)
    binary = solver.solve_energy_schedule(scenario.hours, scenario.battery, directives, use_binary_modes=True)
    verify_pattern(scenario.hours, scenario.battery, directives, linear)
    verify_pattern(scenario.hours, scenario.battery, directives, binary)
    assert cost(linear, scenario.hours) == pytest.approx(cost(binary, scenario.hours), abs=1e-5, rel=1e-7)
