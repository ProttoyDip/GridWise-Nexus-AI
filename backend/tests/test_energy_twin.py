import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.demo.routes import demo_scenario
from app.explainability.generator import generate_explanation
from app.llm.confidence import ConfidenceDecision, ConfidenceMetadata
from app.main import app
from app.memory.store import DirectiveMemory
from app.models.response import DirectiveInterpretation
from app.optimizer.scheduler import build_hourly_plan
from app.simulation.scenario_generator import Uncertainty, generate_scenarios, infer_uncertainty
from app.simulation.simulator import simulate
from scripts.build_demo_benchmarks import build_report


def noop():
    return DirectiveInterpretation(note_index=0, applies=False, directive_type="no_op",
                                   structured_adjustment=None, explanation="Tomorrow is hypothetical.")


@pytest.mark.parametrize("kwargs", [
    {"hours": (2, 1)}, {"hours": (1, 1)}, {"hours": (True,)}, {"hours": (24,)},
    {"solar_factors": (-.1,)}, {"solar_factors": (1.1,)}, {"solar_factors": (float('nan'),)},
    {"demand_factors": (.5,)}, {"battery_availability": (2,)}, {"solar_factors": (True,)},
    {"solar_factors": (.5,) * 9},
])
def test_invalid_uncertainty_rejected(kwargs):
    with pytest.raises(ValueError):
        Uncertainty(**kwargs)


def test_futures_are_copies_and_zero_solar_is_allowed():
    original = demo_scenario()
    snapshot = original.model_dump()
    futures = generate_scenarios(original, Uncertainty(hours=(13, 14), solar_factors=(0,), demand_factors=(1.3,), battery_availability=(0,)))
    assert original.model_dump() == snapshot
    assert len(futures) == 4
    solar = {h.hour: h for h in futures[1].scenario.hours}
    assert solar[13].solar_kwh == 0
    assert solar[12].solar_kwh == 30
    assert futures[3].scenario.battery.max_charge_kwh_per_hour == 0
    assert futures[3].scenario.battery.initial_energy_kwh == original.battery.initial_energy_kwh


def test_inference_only_triggers_hypotheticals():
    assert infer_uncertainty(["Solar may decrease tomorrow"]).solar_factors == (.8, .5, 0)
    assert not infer_uncertainty(["PV maintenance at 13:00"]).solar_factors


@pytest.mark.parametrize("kind,adjustment", [
    ("solar_reduction", {"hours": [13, 14], "factor": .5}),
    ("minimum_battery_reserve", {"hours": [13, 14], "minimum_energy_kwh": 50}),
    ("no_charge_window", {"hours": [13, 14]}),
    ("no_discharge_window", {"hours": [13, 14]}),
    ("max_grid_window", {"hours": [13, 14], "max_grid_kwh": 3}),
])
def test_all_directives_remain_hard_constraints_in_futures(kind, adjustment):
    directive = DirectiveInterpretation(note_index=0, applies=True, directive_type=kind,
                                       structured_adjustment=adjustment, explanation="Validated operating requirement")
    result = simulate(demo_scenario(), [directive], Uncertainty(solar_factors=(0,)))
    assert all(o["feasible"] for o in result["outcomes"])
    for plan in result["contingency_plans"].values():
        for e in plan:
            if e["hour"] in (13, 14):
                if kind == "minimum_battery_reserve":
                    assert e["battery_energy_after_kwh"] >= 50 - 1e-5
                elif kind == "no_charge_window":
                    assert e["battery_action"] != "charge"
                elif kind == "no_discharge_window":
                    assert e["battery_action"] != "discharge"
                elif kind == "max_grid_window":
                    assert e["grid_kwh"] <= 3 + 1e-5


def test_simulation_cache_avoids_repeated_solves(monkeypatch):
    from app.simulation import simulator
    original = simulator.build_hourly_plan
    calls = []
    def counted(*args):
        calls.append(1)
        return original(*args)
    monkeypatch.setattr(simulator, "build_hourly_plan", counted)
    settings = Uncertainty(solar_factors=(.5, 0))
    first = simulate(demo_scenario(), [noop()], settings)
    second = simulate(demo_scenario(), [noop()], settings)
    assert len(calls) == 3
    assert second == first


def test_simulator_physics_cost_and_monotonic_solar_loss():
    scenario = demo_scenario()
    result = simulate(scenario, [noop()], Uncertainty(solar_factors=(.8, .5, 0), battery_availability=(0,)))
    assert all(o["feasible"] for o in result["outcomes"])
    solar_costs = [o["cost_bdt"] for o in result["outcomes"][:4]]
    assert solar_costs == sorted(solar_costs)
    assert not result["risk"]["probabilities_assigned"]
    tariffs = {h.hour: h.tariff_bdt_per_kwh for h in scenario.hours}
    for outcome in result["outcomes"]:
        plan = result["contingency_plans"][outcome["name"]]
        assert outcome["cost_bdt"] == pytest.approx(sum(e["grid_kwh"] * tariffs[e["hour"]] for e in plan))
        assert plan[-1]["battery_energy_after_kwh"] == pytest.approx(40)
    disabled = result["outcomes"][-1]
    assert disabled["charge_kwh"] == disabled["discharge_kwh"] == 0
    assert result["best_plan"] == result["contingency_plans"]["nominal"]


def test_infeasible_future_preserves_grid_directive():
    scenario = demo_scenario()
    directive = DirectiveInterpretation(note_index=0, applies=True, directive_type="max_grid_window",
                                       structured_adjustment={"hours": [13], "max_grid_kwh": 0}, explanation="Grid unavailable at 13.")
    result = simulate(scenario, [directive], Uncertainty(hours=(13,), solar_factors=(0,), battery_availability=(0,)))
    assert result["outcomes"][0]["feasible"]
    # Battery can compensate solar loss, but the zero-power future still has
    # solar and remains feasible. Use a combined baseline without solar to
    # force an impossible zero-grid hour with battery power disabled.
    scenario.hours[13].solar_kwh = 0
    result = simulate(scenario, [directive], Uncertainty(battery_availability=(0,)))
    assert result["outcomes"][0]["feasible"]
    assert not result["outcomes"][1]["feasible"]
    assert result["risk"]["infeasible_futures"] == 1


def test_explanation_is_pure_and_reference_cost_is_independent():
    scenario = demo_scenario()
    directive = DirectiveInterpretation(note_index=0, applies=True, directive_type="solar_reduction",
                                       structured_adjustment={"hours": [13, 14], "factor": .5}, explanation="Half solar.")
    plan = build_hourly_plan(scenario, [directive])
    snapshot = [e.model_dump() for e in plan]
    explanation = generate_explanation(scenario, plan, [directive])
    assert [e.model_dump() for e in plan] == snapshot
    reference = sum(max(0, h.demand_kwh - h.solar_kwh * (.5 if h.hour in (13, 14) else 1)) * h.tariff_bdt_per_kwh for h in scenario.hours)
    assert explanation["reference_cost_bdt"] == pytest.approx(reference)
    assert "solar_reduction" in explanation["constraint_explanations"][0]
    assert any("charge" in r for r in explanation["battery_reasons"])


def test_memory_persistence_normalization_feedback_and_graph(tmp_path):
    path = tmp_path / "memory.json"
    memory = DirectiveMemory(path)
    memory.record("PV Maintenance", "solar_reduction", True)
    memory.record("  pv   maintenance ", "solar_reduction", True)
    memory.record("pv maintenance", "solar_reduction", False)
    loaded = DirectiveMemory(path)
    assert json.loads(path.read_text())["entries"][0]["success_rate"] == pytest.approx(2/3)
    assert loaded.lookup("PV maintenance")[0]["success_rate"] == pytest.approx(2/3)
    context = json.loads(loaded.context("PV maintenance"))
    assert context[0]["directive"] == "solar_reduction" and "phrase" not in context[0]
    graph = loaded.graph()
    assert any(n["label"] == "pv maintenance" for n in graph["nodes"])
    assert any(e["target"] == "solar_reduction" for e in graph["edges"])


def test_memory_does_not_override_llm_or_safety_decisions(tmp_path):
    memory = DirectiveMemory(tmp_path / "memory.json")
    for _ in range(2):
        memory.record("maintenance", "solar_reduction", True)
    d = DirectiveInterpretation(note_index=0, applies=True, directive_type="solar_reduction",
                               structured_adjustment={"hours": [13], "factor": .8}, explanation="Current interpretation")
    d._confidence_metadata = ConfidenceMetadata(.6, 1, ("provider/model",), ConfidenceDecision.VERIFY)
    before = d.model_dump()
    memory.boost("maintenance", d)
    assert d.model_dump() == before
    assert d._confidence_metadata.confidence_score == pytest.approx(.65)
    assert d._confidence_metadata.agreement_count == 1
    assert d._confidence_metadata.decision == ConfidenceDecision.VERIFY
    d._confidence_metadata = replace(d._confidence_metadata, decision=ConfidenceDecision.ESCALATE)
    memory.boost("maintenance", d)
    assert d._confidence_metadata.confidence_score == pytest.approx(.65)
    assert memory.boost("maintenance", noop()).directive_type == "no_op"


def test_memory_corruption_bounded_and_no_credentials(tmp_path):
    path = tmp_path / "memory.json"
    path.write_text("invalid")
    memory = DirectiveMemory(path, max_entries=2)
    for phrase in ("one", "two", "three"):
        memory.record(phrase, "max_grid_window", True)
    assert not memory.lookup("one")
    with pytest.raises(ValueError):
        memory.record("API_KEY=secret", "max_grid_window", True)


def test_demo_opt_in_and_fixed_simulation(monkeypatch):
    client = TestClient(app)
    monkeypatch.delenv("GRIDWISE_ENABLE_DEMO", raising=False)
    assert client.get("/demo").status_code == 404
    monkeypatch.setenv("GRIDWISE_ENABLE_DEMO", "1")
    assert client.get("/demo").status_code == 200
    assert client.get("/demo/metrics").status_code == 200
    result = client.post("/demo/simulate-emergency")
    assert result.status_code == 200
    assert len(result.json()["outcomes"]) == 6
    assert "Digital Twin" in result.json()["roles"]


def test_api_nominal_optimality_schema_and_optional_failure(monkeypatch):
    from app.api import optimize
    from app.agents import coordinator
    monkeypatch.setattr(optimize, "interpret_operator_notes", lambda *args: [noop()])
    monkeypatch.setenv("GRIDWISE_DIGITAL_TWIN_ENABLED", "1")
    client = TestClient(app)
    payload = demo_scenario().model_dump()
    first = client.post("/optimize-energy", json=payload)
    assert first.status_code == 200
    assert set(first.json()) == {"scenario_id", "directive_interpretation", "hourly_plan", "total_grid_kwh", "total_cost_bdt", "peak_grid_kwh", "plan_summary"}
    def fail(*args):
        raise RuntimeError("Optional enrichment unavailable")
    monkeypatch.setattr(coordinator, "record_decision", fail)
    second = client.post("/optimize-energy", json=payload)
    assert second.status_code == 200 and second.json() == first.json()


def test_dashboard_report_uses_measured_data_and_handles_failed_models(tmp_path):
    path = tmp_path / "ranking.json"
    path.write_text(json.dumps({"note_count": 1, "complete": True, "rankings": [
        {"provider": "p", "model": "quota", "status": "completed", "results": [{}], "metrics": {"fully_correct_rate": 0, "mean_latency_ms": None}},
        {"provider": "p", "model": "good", "status": "completed", "results": [{}], "metrics": {"fully_correct_rate": .9, "mean_latency_ms": 1500}},
    ]}))
    report = build_report(path)
    assert report["models_tested"] == 2
    assert report["best_interpreter"] == "p/good"
    assert report["accuracy"] == .9 and report["mean_latency_seconds"] == 1.5
