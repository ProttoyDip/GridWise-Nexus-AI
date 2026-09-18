"""API checks for every public case, accepting any equivalent optimal schedule.

Offline tests replay fixture LLM responses through the real interpreter,
guardrails, optimizer and verifier. --live-llm additionally tests actual note
interpretation via configured providers. No reference schedule is supplied
to the optimizer or compared with its output.
"""

import json
import math
import re
import threading
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.llm import interpreter
from app.main import app

CASES = json.loads((Path(__file__).parent / "fixtures" / "sample_cases.json").read_text(encoding="utf-8"))["cases"]


class ReplayProvider:
    def __init__(self, directives):
        self.responses = directives
        self.calls = 0
        self.lock = threading.Lock()

    def complete(self, system_prompt, user_prompt):
        with self.lock:
            self.calls += 1
        index = int(re.search(r"Operator note \(index (\d+)\)", user_prompt).group(1))
        data = deepcopy(self.responses[index])
        data["note_index"] = 999  # Real interpreter must assign the index.
        data["explanation"] = "Fixture provider response; wording may differ."
        return json.dumps(data)


def assert_directives(actual, expected):
    assert len(actual) == len(expected)
    for result, reference in zip(actual, expected):
        for field in ("note_index", "applies", "directive_type"):
            assert result[field] == reference[field]
        assert isinstance(result["explanation"], str) and result["explanation"].strip()
        adjustment = result["structured_adjustment"]
        reference_adjustment = reference["structured_adjustment"]
        if reference_adjustment is None:
            assert adjustment is None
        else:
            assert set(adjustment) == set(reference_adjustment)
            assert adjustment["hours"] == reference_adjustment["hours"]
            assert all(type(h) is int for h in adjustment["hours"])
            for field in reference_adjustment.keys() - {"hours"}:
                assert adjustment[field] == pytest.approx(reference_adjustment[field])


def assert_schedule_and_cost(payload, result, expected_directives, optimal_cost):
    """Independent test oracle: no production verification functions used."""
    assert result["scenario_id"] == payload["scenario_id"]
    assert_directives(result["directive_interpretation"], expected_directives)
    plan = result["hourly_plan"]
    assert [entry["hour"] for entry in plan] == list(range(24))
    forecast = {h["hour"]: h for h in payload["hours"]}
    config = payload["battery"]
    previous = config["initial_energy_kwh"]
    for entry in plan:
        h = forecast[entry["hour"]]
        for field in ("grid_kwh", "solar_used_kwh", "battery_kwh", "battery_energy_after_kwh"):
            assert math.isfinite(entry[field]) and entry[field] >= 0
        action = entry["battery_action"]
        assert action in ("charge", "discharge", "idle")
        if action == "idle":
            assert entry["battery_kwh"] == 0
        charge = entry["battery_kwh"] if action == "charge" else 0
        discharge = entry["battery_kwh"] if action == "discharge" else 0
        energy = entry["battery_energy_after_kwh"]
        assert entry["grid_kwh"] + entry["solar_used_kwh"] + discharge == pytest.approx(h["demand_kwh"] + charge, abs=1e-5, rel=1e-7)
        assert energy == pytest.approx(previous + charge - discharge, abs=1e-5, rel=1e-7)
        assert config["minimum_energy_kwh"] - 1e-5 <= energy <= config["capacity_kwh"] + 1e-5
        assert charge <= config["max_charge_kwh_per_hour"] + 1e-5
        assert discharge <= config["max_discharge_kwh_per_hour"] + 1e-5
        assert entry["solar_used_kwh"] <= h["solar_kwh"] + 1e-5
        for directive in expected_directives:
            adjustment = directive["structured_adjustment"]
            if adjustment is None or entry["hour"] not in adjustment["hours"]:
                continue
            kind = directive["directive_type"]
            if kind == "solar_reduction":
                assert entry["solar_used_kwh"] <= h["solar_kwh"] * adjustment["factor"] + 1e-5
            elif kind == "minimum_battery_reserve":
                assert energy >= adjustment["minimum_energy_kwh"] - 1e-5
            elif kind == "no_charge_window":
                assert charge == pytest.approx(0, abs=1e-5)
            elif kind == "no_discharge_window":
                assert discharge == pytest.approx(0, abs=1e-5)
            elif kind == "max_grid_window":
                assert entry["grid_kwh"] <= adjustment["max_grid_kwh"] + 1e-5
        previous = energy
    assert previous == pytest.approx(config["initial_energy_kwh"], abs=1e-5, rel=1e-7)
    calculated_cost = math.fsum(entry["grid_kwh"] * forecast[entry["hour"]]["tariff_bdt_per_kwh"] for entry in plan)
    assert result["total_cost_bdt"] == pytest.approx(calculated_cost, abs=1e-5, rel=1e-7)
    assert calculated_cost == pytest.approx(optimal_cost, abs=0.01, rel=1e-7)
    assert result["total_grid_kwh"] == pytest.approx(math.fsum(entry["grid_kwh"] for entry in plan))
    assert result["peak_grid_kwh"] == pytest.approx(max(entry["grid_kwh"] for entry in plan))


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
@pytest.mark.parametrize("variant", ["original", "reordered", "scaled"])
def test_public_sample_pipeline(case, variant, monkeypatch):
    from app.api import optimize
    # Exercise the base interpreter with replay providers; adaptive routing
    # has separate fake-provider tests and the live API test below.
    monkeypatch.setattr(optimize, "interpret_operator_notes", interpreter.interpret_operator_notes)
    payload = deepcopy(case["input"])
    expected = deepcopy(case["expected_output"]["directive_interpretation"])
    optimal_cost = case["expected_output"]["total_cost_bdt"]
    if variant == "reordered":
        payload["scenario_id"] = "unseen-scenario"
        payload["hours"].reverse()
    elif variant == "scaled":
        # Scale physical quantities and prices without changing time windows.
        # Optimal cost scales by energy scale * price scale.
        energy_scale, price_scale = 0.137, 1.231
        payload["scenario_id"] = "scaled-unseen-scenario"
        for h in payload["hours"]:
            h["demand_kwh"] *= energy_scale
            h["solar_kwh"] *= energy_scale
            h["tariff_bdt_per_kwh"] *= price_scale
        for field in payload["battery"]:
            payload["battery"][field] *= energy_scale
        for directive in expected:
            adjustment = directive["structured_adjustment"]
            if adjustment:
                for field in ("minimum_energy_kwh", "max_grid_kwh"):
                    if field in adjustment:
                        adjustment[field] *= energy_scale
        optimal_cost *= energy_scale * price_scale
    provider = ReplayProvider(expected)
    monkeypatch.setattr(interpreter, "get_provider_chain", lambda: [("fixture", provider)])
    result = TestClient(app).post("/optimize-energy", json=payload)
    assert result.status_code == 200, result.text
    assert provider.calls == len(payload["operator_notes"])
    assert_schedule_and_cost(payload, result.json(), expected, optimal_cost)


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_public_sample_live_llm(case, live_llm_environment):
    result = TestClient(app).post("/optimize-energy", json=case["input"])
    assert result.status_code == 200, result.text
    assert_schedule_and_cost(case["input"], result.json(),
                             case["expected_output"]["directive_interpretation"],
                             case["expected_output"]["total_cost_bdt"])
