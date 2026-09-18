"""Judge checks must catch semantic and physical faults independently."""

from copy import deepcopy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import judge_simulator as judge
from app.main import app
from app.models.request import ScenarioRequest

CASES = json.loads((Path(__file__).parent / "fixtures/sample_cases.json").read_text())["cases"]


def first_case():
    case = deepcopy(CASES[0])
    return case["input"], case["expected_output"], case["expected_output"]["directive_interpretation"], case["expected_output"]["total_cost_bdt"]


def test_valid_public_response_passes_all_independent_checks():
    request, response, expected, cost = first_case()
    checks = judge.check_result(request, response, expected, cost)
    assert all(value is not False for value in checks.values())


@pytest.mark.parametrize("field,value", [("total_cost_bdt", float("nan")), ("total_cost_bdt", True),
                                         ("scenario_id", "wrong"), ("hourly_plan", []), ("plan_summary", "")])
def test_api_contract_checks_reject_invalid_values(field, value):
    request, response, _, _ = first_case()
    response[field] = value
    assert not judge.response_schema_valid(response, request)


def test_private_or_extra_fields_fail_schema():
    request, response, _, _ = first_case()
    response["confidence_score"] = 1
    assert not judge.response_schema_valid(response, request)


@pytest.mark.parametrize("hours", [[True], [24], [-1], [2, 1], [1, 1], [1.5], [], "1"])
def test_hours_are_strict(hours):
    assert not judge.hours_valid(hours)


@pytest.mark.parametrize("mutation,failed_check", [
    ("grid", "energy_balance"), ("transition", "battery_continuity"),
    ("capacity", "battery_bounds"), ("rate", "rate_limits"),
    ("solar", "solar_availability"), ("cost", "cost_calculation"),
    ("end", "end_battery_equality"), ("hours", "directive_hours"),
    ("numeric", "directive_values"), ("optimality", "optimality"),
])
def test_fault_injection_is_detected(mutation, failed_check):
    request, response, expected, cost = first_case()
    expected = deepcopy(expected)
    if mutation == "grid":
        response["hourly_plan"][0]["grid_kwh"] += 1
    elif mutation == "transition":
        response["hourly_plan"][0]["battery_energy_after_kwh"] += 1
    elif mutation == "capacity":
        response["hourly_plan"][0]["battery_energy_after_kwh"] = request["battery"]["capacity_kwh"] + 1
    elif mutation == "rate":
        response["hourly_plan"][0]["battery_action"] = "charge"
        response["hourly_plan"][0]["battery_kwh"] = request["battery"]["max_charge_kwh_per_hour"] + 1
    elif mutation == "solar":
        response["hourly_plan"][0]["solar_used_kwh"] = request["hours"][0]["solar_kwh"] + 1
    elif mutation == "cost":
        response["total_cost_bdt"] += 1
    elif mutation == "end":
        response["hourly_plan"][-1]["battery_energy_after_kwh"] += 1
    elif mutation == "hours":
        response["directive_interpretation"][0]["structured_adjustment"]["hours"] = [0]
    elif mutation == "numeric":
        response["directive_interpretation"][0]["structured_adjustment"]["factor"] = 0.3
    else:
        cost += 1
    checks = judge.check_result(request, response, expected, cost)
    assert checks["response_schema"]
    assert not checks[failed_check]


def test_dropped_directive_cannot_evade_expected_compliance_check():
    request, response, expected, cost = first_case()
    expected = deepcopy(expected)
    dropped = response["directive_interpretation"][0]
    dropped.update(directive_type="no_op", applies=False, structured_adjustment=None)
    hour = expected[0]["structured_adjustment"]["hours"][0]
    solar = next(h["solar_kwh"] for h in request["hours"] if h["hour"] == hour)
    response["hourly_plan"][hour]["solar_used_kwh"] = solar
    checks = judge.check_result(request, response, expected, cost)
    assert checks["response_schema"]
    assert not checks["directive_type"] and not checks["directive_compliance"]


def test_random_generation_is_reproducible_and_covers_all_types():
    generated = judge.generate_cases(12, 42)
    assert generated == judge.generate_cases(12, 42)
    assert generated != judge.generate_cases(12, 43)
    assert {d["directive_type"] for case in generated for d in case["expected"]} == set(judge.KINDS)
    for case in generated:
        ScenarioRequest.model_validate(case["input"])
        assert judge.reference_optimal_cost(case["input"], case["expected"]) >= 0


@pytest.mark.parametrize("case_index", range(10))
def test_separate_reference_model_matches_public_optimal_objectives(case_index):
    case = CASES[case_index]
    actual = judge.reference_optimal_cost(case["input"], case["expected_output"]["directive_interpretation"])
    assert actual == pytest.approx(case["expected_output"]["total_cost_bdt"], abs=0.01, rel=1e-7)


def test_health_openapi_and_request_validation_are_checked():
    request, _, _, _ = first_case()
    checks = judge.service_checks(TestClient(app), request)
    assert all(checks.values())


def test_replay_checks_real_api_and_restores_production_entrypoint():
    from app.api import optimize
    original = optimize.interpret_operator_notes
    raw = CASES[0]
    case = dict(id=raw["id"], source="public", input=raw["input"],
                expected=raw["expected_output"]["directive_interpretation"],
                optimal_cost=raw["expected_output"]["total_cost_bdt"])
    result = judge.run_case(TestClient(app), case, "replay")
    assert result["passed"] and result["status_code"] == 200
    assert optimize.interpret_operator_notes is original


def test_unreachable_api_fails_closed_without_leaking_error_bodies():
    class Broken:
        def post(self, *args, **kwargs):
            raise RuntimeError("api-key-sensitive")
    case = judge.generate_cases(1)[0]
    case["optimal_cost"] = 0
    result = judge.run_case(Broken(), case)
    assert not result["passed"] and result["error"] == "RuntimeError"
    assert "api-key-sensitive" not in json.dumps(result)


def test_weights_total_100_and_failure_changes_score():
    assert sum(judge.WEIGHTS.values()) == 100
    service = dict(health=True, openapi=True)
    row = dict(checks={key: True for key in judge.CASE_CHECKS})
    assert judge.score_report(service, [row])["estimated_score_out_of_100"] == 100
    row["checks"]["energy_balance"] = False
    assert judge.score_report(service, [row])["estimated_score_out_of_100"] == 90
