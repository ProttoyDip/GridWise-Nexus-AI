"""Result cache correctness, expiration, mutation isolation and API fail-open."""

from copy import deepcopy
from datetime import timezone

import pytest
from fastapi.testclient import TestClient

from app.api import optimize
from app.main import app
from app.models.request import ScenarioRequest
from app.optimizer.cache import OptimizationCache, _configured_cache, optimization_cache_key
from app.optimizer.solver import OptimizationError
from app.verifier.schedule_checker import ScheduleValidationError
from test_optimizer_directives import validated
from test_solver import battery, forecast


@pytest.fixture
def api_cache(monkeypatch):
    now = [100.0]
    cache = OptimizationCache(ttl_seconds=10, clock=lambda: now[0])
    monkeypatch.setattr(optimize, "optimization_cache", cache)
    directives = validated("solar_reduction", [12, 13], factor=0.2)
    monkeypatch.setattr(optimize, "interpret_operator_notes", lambda *args: deepcopy(directives))
    original_solver = optimize.build_hourly_plan
    calls = []
    def solve(*args):
        calls.append(args)
        return original_solver(*args)
    monkeypatch.setattr(optimize, "build_hourly_plan", solve)
    scenario = ScenarioRequest(scenario_id="cached", operator_notes=["note"], hours=forecast(solar=1), battery=battery())
    return cache, now, calls, scenario, directives, TestClient(app)


def test_identical_requests_avoid_solver_and_still_verify(api_cache, monkeypatch):
    cache, _, calls, scenario, directives, client = api_cache
    verified = []
    verify = optimize.verify_schedule
    def check(*args):
        verified.append(True)
        verify(*args)
    monkeypatch.setattr(optimize, "verify_schedule", check)
    first = client.post("/optimize-energy", json=scenario.model_dump())
    second = client.post("/optimize-energy", json=scenario.model_dump())
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(calls) == 1 and len(verified) == 2
    key = optimization_cache_key(scenario.scenario_id, scenario.hours, scenario.battery, directives)
    entry = cache.get(key)
    assert entry.total_cost_bdt == first.json()["total_cost_bdt"]
    assert [e.model_dump() for e in entry.hourly_plan] == first.json()["hourly_plan"]
    assert entry.timestamp.tzinfo == timezone.utc
    assert "timestamp" not in second.json() and "cache" not in second.json()


def test_ttl_boundary_recomputes_and_does_not_extend_on_hit(api_cache):
    _, now, calls, scenario, _, client = api_cache
    assert client.post("/optimize-energy", json=scenario.model_dump()).status_code == 200
    now[0] = 109.999
    assert client.post("/optimize-energy", json=scenario.model_dump()).status_code == 200
    assert len(calls) == 1
    now[0] = 110
    assert client.post("/optimize-energy", json=scenario.model_dump()).status_code == 200
    assert len(calls) == 2


@pytest.mark.parametrize("change", ["scenario_id", "demand", "solar", "tariff", "battery", "directive"])
def test_every_input_dimension_invalidates_cache(api_cache, change):
    _, _, calls, scenario, directives, client = api_cache
    assert client.post("/optimize-energy", json=scenario.model_dump()).status_code == 200
    if change == "scenario_id":
        scenario.scenario_id = "another"
    elif change in ("demand", "solar", "tariff"):
        field = {"demand": "demand_kwh", "solar": "solar_kwh", "tariff": "tariff_bdt_per_kwh"}[change]
        setattr(scenario.hours[0], field, getattr(scenario.hours[0], field) + 1)
    elif change == "battery":
        scenario.battery.capacity_kwh += 1
    else:
        directives[0].structured_adjustment["factor"] = 0.3
    assert client.post("/optimize-energy", json=scenario.model_dump()).status_code == 200
    assert len(calls) == 2


def test_key_is_canonical_and_excludes_private_metadata(api_cache):
    _, _, _, scenario, directives, _ = api_cache
    key = optimization_cache_key(scenario.scenario_id, scenario.hours, scenario.battery, directives)
    scenario.hours.reverse()
    directives[0].structured_adjustment = dict(reversed(list(directives[0].structured_adjustment.items())))
    directives[0]._confidence_metadata = object()
    assert optimization_cache_key(scenario.scenario_id, scenario.hours, scenario.battery, directives) == key
    assert len(key) == 64


@pytest.mark.parametrize("operation", ["get", "put", "key"])
def test_cache_exceptions_never_break_api(api_cache, monkeypatch, operation):
    cache, _, calls, scenario, _, client = api_cache
    def broken(*args):
        raise RuntimeError("Cache unavailable")
    if operation == "key":
        monkeypatch.setattr(optimize, "optimization_cache_key", broken)
    else:
        monkeypatch.setattr(cache, operation, broken)
    for _ in range(2):
        assert client.post("/optimize-energy", json=scenario.model_dump()).status_code == 200
    assert len(calls) == 2


@pytest.mark.parametrize("corruption", ["plan", "cost"])
def test_corrupt_cache_results_are_discarded_and_recomputed(api_cache, corruption):
    cache, _, calls, scenario, directives, client = api_cache
    first = client.post("/optimize-energy", json=scenario.model_dump())
    assert first.status_code == 200
    key = optimization_cache_key(scenario.scenario_id, scenario.hours, scenario.battery, directives)
    cached = cache.get(key)
    cost = cached.total_cost_bdt
    if corruption == "plan":
        cached.hourly_plan[0].grid_kwh += 1
    else:
        cost += 1
    cache.put(key, cached.hourly_plan, cost)
    second = client.post("/optimize-energy", json=scenario.model_dump())
    assert second.status_code == 200 and second.json() == first.json()
    assert len(calls) == 2


@pytest.mark.parametrize("failure", ["solver", "verifier"])
def test_failed_results_are_never_cached(api_cache, monkeypatch, failure):
    _, _, calls, scenario, _, client = api_cache
    target = "build_hourly_plan" if failure == "solver" else "verify_schedule"
    original = getattr(optimize, target)
    def broken(*args):
        if failure == "solver":
            raise OptimizationError("Unavailable")
        raise ScheduleValidationError("Invalid")
    monkeypatch.setattr(optimize, target, broken)
    first = client.post("/optimize-energy", json=scenario.model_dump())
    assert first.status_code == (422 if failure == "solver" else 500)
    monkeypatch.setattr(optimize, target, original)
    second = client.post("/optimize-energy", json=scenario.model_dump())
    assert second.status_code == 200
    assert len(calls) == (1 if failure == "solver" else 2)


def test_copy_isolation_and_lru_bound(api_cache):
    source, _, _, scenario, directives, client = api_cache
    assert client.post("/optimize-energy", json=scenario.model_dump()).status_code == 200
    key = optimization_cache_key(scenario.scenario_id, scenario.hours, scenario.battery, directives)
    result = source.get(key)
    cache = OptimizationCache(max_entries=2)
    cache.put("a", result.hourly_plan, result.total_cost_bdt)
    result.hourly_plan[0].grid_kwh += 100
    copy = cache.get("a")
    original_grid = copy.hourly_plan[0].grid_kwh
    copy.hourly_plan[0].grid_kwh += 100
    assert cache.get("a").hourly_plan[0].grid_kwh == original_grid
    cache.put("b", copy.hourly_plan, copy.total_cost_bdt)
    cache.get("a")
    cache.put("c", copy.hourly_plan, copy.total_cost_bdt)
    assert cache.get("b") is None and cache.get("a") is not None
    cache.clear()
    assert cache.get("a") is None


def test_zero_ttl_disables_cache(api_cache):
    source, _, _, scenario, directives, client = api_cache
    assert client.post("/optimize-energy", json=scenario.model_dump()).status_code == 200
    entry = source.get(optimization_cache_key(scenario.scenario_id, scenario.hours, scenario.battery, directives))
    cache = OptimizationCache(ttl_seconds=0)
    cache.put("key", entry.hourly_plan, entry.total_cost_bdt)
    assert cache.get("key") is None


@pytest.mark.parametrize("ttl", ["invalid", "-1", "nan", "inf"])
def test_invalid_configuration_does_not_break_startup(monkeypatch, ttl):
    monkeypatch.setenv("OPTIMIZATION_CACHE_TTL_SECONDS", ttl)
    assert _configured_cache().ttl_seconds == 300
