"""Black-box GridWise judge simulation; weights are estimates, not official.

Run from backend: python judge_simulator.py --random-cases 12
Use --mode replay for deterministic API plumbing checks without LLM calls.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import logging
import os
from pathlib import Path
import random
import time
from unittest.mock import patch

from dotenv import load_dotenv
import httpx
import pulp
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent
KINDS = ("solar_reduction", "minimum_battery_reserve", "no_charge_window", "no_discharge_window", "max_grid_window", "no_op")
NUMBERS = {"solar_reduction": "factor", "minimum_battery_reserve": "minimum_energy_kwh", "max_grid_window": "max_grid_kwh"}
WEIGHTS = dict(health=5, api_schema=10, optimize_endpoint=5, directive_type=10,
               directive_hours=10, directive_values=10, energy_balance=10,
               battery_bounds=5, battery_continuity=5, rate_limits=5,
               solar_availability=3, directive_compliance=5, cost_calculation=7,
               end_battery_equality=5, optimality=5)
CASE_CHECKS = ("response_schema", "optimize_endpoint", "directive_type", "directive_hours",
               "directive_values", "energy_balance", "battery_bounds", "battery_continuity",
               "rate_limits", "solar_availability", "directive_compliance", "cost_calculation",
               "end_battery_equality", "optimality")


def finite_number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def equal(a, b, abs_tol=1e-5, rel_tol=1e-7):
    return finite_number(a) and finite_number(b) and math.isclose(a, b, abs_tol=abs_tol, rel_tol=rel_tol)


def at_most(a, b):
    return finite_number(a) and finite_number(b) and (a <= b or equal(a, b))


def hours_valid(hours):
    return isinstance(hours, list) and bool(hours) and all(type(h) is int and 0 <= h <= 23 for h in hours) and hours == sorted(set(hours))


def response_schema_valid(data, request):
    """Check the public contract without production response models."""
    top = {"scenario_id", "directive_interpretation", "hourly_plan", "total_grid_kwh", "total_cost_bdt", "peak_grid_kwh", "plan_summary"}
    if not isinstance(data, dict) or set(data) != top or data["scenario_id"] != request["scenario_id"]:
        return False
    if not isinstance(data["plan_summary"], str) or not data["plan_summary"].strip():
        return False
    if any(not finite_number(data[field]) or data[field] < 0 for field in ("total_grid_kwh", "total_cost_bdt", "peak_grid_kwh")):
        return False
    directives = data["directive_interpretation"]
    if not isinstance(directives, list) or len(directives) != len(request["operator_notes"]):
        return False
    for index, directive in enumerate(directives):
        if not isinstance(directive, dict) or set(directive) != {"note_index", "applies", "directive_type", "structured_adjustment", "explanation"}:
            return False
        if type(directive["note_index"]) is not int or directive["note_index"] != index or type(directive["applies"]) is not bool:
            return False
        kind = directive["directive_type"]
        if not isinstance(kind, str) or kind not in KINDS or not isinstance(directive["explanation"], str) or not directive["explanation"].strip():
            return False
        adjustment = directive["structured_adjustment"]
        if kind == "no_op":
            if directive["applies"] or adjustment is not None:
                return False
            continue
        field = NUMBERS.get(kind)
        if not directive["applies"] or not isinstance(adjustment, dict) or set(adjustment) != ({"hours", field} if field else {"hours"}):
            return False
        if not hours_valid(adjustment["hours"]):
            return False
        if field:
            value = adjustment[field]
            if not finite_number(value) or value < 0:
                return False
            if kind == "solar_reduction" and not 0 < value <= 1:
                return False
            if kind == "minimum_battery_reserve" and value > request["battery"]["capacity_kwh"]:
                return False
    plan = data["hourly_plan"]
    if not isinstance(plan, list) or len(plan) != 24:
        return False
    for index, entry in enumerate(plan):
        if not isinstance(entry, dict) or set(entry) != {"hour", "grid_kwh", "solar_used_kwh", "battery_action", "battery_kwh", "battery_energy_after_kwh"}:
            return False
        if type(entry["hour"]) is not int or entry["hour"] != index or entry["battery_action"] not in ("charge", "discharge", "idle"):
            return False
        if any(not finite_number(entry[field]) or entry[field] < 0 for field in ("grid_kwh", "solar_used_kwh", "battery_kwh", "battery_energy_after_kwh")):
            return False
        if entry["battery_action"] == "idle" and entry["battery_kwh"] != 0:
            return False
    return True


def check_result(request, data, expected, optimal_cost):
    """Replay physics and expected directive semantics independently."""
    checks = {name: False for name in CASE_CHECKS}
    checks["optimize_endpoint"] = True
    active = [d for d in expected if d["directive_type"] != "no_op"]
    numeric = [d for d in expected if d["directive_type"] in NUMBERS]
    checks["directive_hours"] = False if active else None
    checks["directive_values"] = False if numeric else None
    checks["response_schema"] = response_schema_valid(data, request)
    if not checks["response_schema"]:
        return checks
    actual = data["directive_interpretation"]
    checks["directive_type"] = all(got["directive_type"] == want["directive_type"] and got["applies"] == want["applies"]
                                   for got, want in zip(actual, expected))
    if active:
        checks["directive_hours"] = all(got["directive_type"] == want["directive_type"] and
                                        isinstance(got["structured_adjustment"], dict) and
                                        got["structured_adjustment"]["hours"] == want["structured_adjustment"]["hours"]
                                        for got, want in zip(actual, expected) if want["directive_type"] != "no_op")
    if numeric:
        checks["directive_values"] = all(got["directive_type"] == want["directive_type"] and
                                         isinstance(got["structured_adjustment"], dict) and
                                         equal(got["structured_adjustment"].get(NUMBERS[want["directive_type"]]),
                                               want["structured_adjustment"][NUMBERS[want["directive_type"]]], abs_tol=1e-6, rel_tol=1e-6)
                                         for got, want in zip(actual, expected) if want["directive_type"] in NUMBERS)
    for name in ("energy_balance", "battery_bounds", "battery_continuity", "rate_limits", "solar_availability", "directive_compliance"):
        checks[name] = True
    forecast = {h["hour"]: h for h in request["hours"]}
    battery = request["battery"]
    previous = battery["initial_energy_kwh"]
    plan = data["hourly_plan"]
    for entry in plan:
        hour = entry["hour"]
        h = forecast[hour]
        charge = entry["battery_kwh"] if entry["battery_action"] == "charge" else 0
        discharge = entry["battery_kwh"] if entry["battery_action"] == "discharge" else 0
        energy = entry["battery_energy_after_kwh"]
        checks["energy_balance"] &= equal(entry["grid_kwh"] + entry["solar_used_kwh"] + discharge, h["demand_kwh"] + charge)
        checks["battery_continuity"] &= equal(energy, previous + charge - discharge)
        checks["battery_bounds"] &= at_most(battery["minimum_energy_kwh"], energy) and at_most(energy, battery["capacity_kwh"])
        checks["rate_limits"] &= at_most(charge, battery["max_charge_kwh_per_hour"]) and at_most(discharge, battery["max_discharge_kwh_per_hour"])
        checks["solar_availability"] &= at_most(entry["solar_used_kwh"], h["solar_kwh"])
        for directive in expected:
            adjustment = directive["structured_adjustment"]
            if adjustment is None or hour not in adjustment["hours"]:
                continue
            kind = directive["directive_type"]
            compliant = (at_most(entry["solar_used_kwh"], h["solar_kwh"] * adjustment["factor"]) if kind == "solar_reduction" else
                         at_most(adjustment["minimum_energy_kwh"], energy) if kind == "minimum_battery_reserve" else
                         equal(charge, 0) if kind == "no_charge_window" else
                         equal(discharge, 0) if kind == "no_discharge_window" else
                         at_most(entry["grid_kwh"], adjustment["max_grid_kwh"]))
            checks["directive_compliance"] &= compliant
        previous = energy
    checks["end_battery_equality"] = equal(previous, battery["initial_energy_kwh"])
    cost = math.fsum(entry["grid_kwh"] * forecast[entry["hour"]]["tariff_bdt_per_kwh"] for entry in plan)
    checks["cost_calculation"] = (equal(data["total_cost_bdt"], cost) and
                                  equal(data["total_grid_kwh"], math.fsum(entry["grid_kwh"] for entry in plan)) and
                                  equal(data["peak_grid_kwh"], max(entry["grid_kwh"] for entry in plan)))
    checks["optimality"] = equal(cost, optimal_cost, abs_tol=0.01)
    return checks


def reference_optimal_cost(request, expected):
    """Independent original binary model; never call production solver/helpers."""
    model = pulp.LpProblem("judge_reference", pulp.LpMinimize)
    b = request["battery"]
    hours = sorted(request["hours"], key=lambda h: h["hour"])
    grid = pulp.LpVariable.dicts("g", range(24), lowBound=0)
    solar = {h["hour"]: pulp.LpVariable(f"s_{h['hour']}", lowBound=0, upBound=h["solar_kwh"]) for h in hours}
    charge = pulp.LpVariable.dicts("c", range(24), lowBound=0, upBound=b["max_charge_kwh_per_hour"])
    discharge = pulp.LpVariable.dicts("d", range(24), lowBound=0, upBound=b["max_discharge_kwh_per_hour"])
    energy = pulp.LpVariable.dicts("e", range(24), lowBound=b["minimum_energy_kwh"], upBound=b["capacity_kwh"])
    mode = pulp.LpVariable.dicts("m", range(24), cat=pulp.LpBinary)
    model += pulp.lpSum(grid[h["hour"]] * h["tariff_bdt_per_kwh"] for h in hours)
    for h in hours:
        i = h["hour"]
        model += grid[i] + solar[i] + discharge[i] == h["demand_kwh"] + charge[i]
        model += energy[i] == (b["initial_energy_kwh"] if i == 0 else energy[i - 1]) + charge[i] - discharge[i]
        model += charge[i] <= b["max_charge_kwh_per_hour"] * mode[i]
        model += discharge[i] <= b["max_discharge_kwh_per_hour"] * (1 - mode[i])
    model += energy[23] == b["initial_energy_kwh"]
    for directive in expected:
        adjustment = directive["structured_adjustment"]
        if adjustment is None:
            continue
        for hour in adjustment["hours"]:
            kind = directive["directive_type"]
            if kind == "solar_reduction":
                model += solar[hour] <= hours[hour]["solar_kwh"] * adjustment["factor"]
            elif kind == "minimum_battery_reserve":
                model += energy[hour] >= adjustment["minimum_energy_kwh"]
            elif kind == "no_charge_window":
                model += charge[hour] == 0
            elif kind == "no_discharge_window":
                model += discharge[hour] == 0
            elif kind == "max_grid_window":
                model += grid[hour] <= adjustment["max_grid_kwh"]
    status = model.solve(pulp.PULP_CBC_CMD(msg=False, threads=1, gapRel=0, gapAbs=0))
    if status != pulp.LpStatusOptimal or model.sol_status != pulp.LpSolutionOptimal:
        raise ValueError("Judge reference did not prove an optimum")
    return float(pulp.value(model.objective) or 0)


def _clock(hour):
    if hour == 0:
        return "midnight"
    if hour == 12:
        return "noon"
    return f"{hour % 12 or 12} {'AM' if hour < 12 else 'PM'}"


def generate_cases(count=24, seed=2026):
    """Generate feasible notes from known semantics, not from an LLM oracle."""
    rng = random.Random(seed)
    cases = []
    for index in range(count):
        capacity = rng.choice([20, 50, 200, 500])
        minimum = capacity * rng.choice([0, 0.05, 0.1])
        initial = capacity * rng.choice([0.4, 0.5, 0.7])
        hours = [dict(hour=hour, demand_kwh=round(capacity * rng.uniform(0.15, 0.6), 3),
                      solar_kwh=round(capacity * rng.uniform(0, 0.8), 3) if 6 <= hour <= 18 else 0,
                      tariff_bdt_per_kwh=rng.choice([0, 1, 3, 7, 12, 20]) if index % 7 else 0) for hour in range(24)]
        battery = dict(capacity_kwh=capacity, initial_energy_kwh=initial, minimum_energy_kwh=minimum,
                       max_charge_kwh_per_hour=capacity * rng.choice([0, 0.1, 0.3]),
                       max_discharge_kwh_per_hour=capacity * rng.choice([0, 0.1, 0.3]))
        kinds = [KINDS[index % len(KINDS)]]
        kinds += rng.sample([kind for kind in KINDS if kind not in kinds], rng.randrange(3))
        notes, expected = [], []
        for note_index, kind in enumerate(kinds):
            start = rng.randrange(23)
            end = rng.randrange(start + 1, 24)
            window = f"from {_clock(start)} until {_clock(end)} today"
            adjustment = {"hours": list(range(start, end))}
            if kind == "solar_reduction":
                percent = rng.choice([10, 23, 37, 65, 90])
                adjustment["factor"] = percent / 100
                note = (f"Usable solar will be at {percent}% of its forecast {window}." if index % 2 else
                        f"Solar output will drop by {100 - percent}% {window}.")
            elif kind == "minimum_battery_reserve":
                percent = rng.choice([15, 20, 25, 35])
                adjustment["minimum_energy_kwh"] = capacity * percent / 100
                note = (f"Keep at least {percent}% of battery capacity in reserve {window}." if index % 2 else
                        f"Battery energy must remain at least {adjustment['minimum_energy_kwh']} kWh {window}.")
            elif kind == "no_charge_window":
                note = f"Do not charge the battery {window}."
            elif kind == "no_discharge_window":
                note = f"Battery discharge is prohibited {window}."
            elif kind == "max_grid_window":
                # Idle storage and curtailed solar give a feasible witness;
                # the cap can still constrain grid-powered battery charging.
                limit = math.ceil(max(h["demand_kwh"] for h in hours[start:end]))
                adjustment["max_grid_kwh"] = limit
                note = f"Grid imports must not exceed {limit} kWh per hour {window}."
            else:
                adjustment = None
                note = rng.choice(["Battery maintenance is planned for next month.", "The arts department changed next week's meeting venue."])
            notes.append(note)
            expected.append(dict(note_index=note_index, applies=kind != "no_op", directive_type=kind,
                                 structured_adjustment=adjustment, explanation="Generated ground truth"))
        if index % 3 == 0:
            rng.shuffle(hours)
        cases.append(dict(id=f"RANDOM-{seed}-{index:03d}", source="random", input=dict(scenario_id=f"RANDOM-{seed}-{index:03d}",
                          operator_notes=notes, hours=hours, battery=battery), expected=expected, optimal_cost=None))
    return cases


@contextmanager
def replay_interpretation(expected):
    from app.api import optimize
    from app.llm.interpreter import interpret_operator_notes
    from app.llm.cache import interpretation_cache
    class Provider:
        model = "judge-replay"
        def complete(self, system, user):
            import re
            index = int(re.search(r"Operator note \(index (\d+)\)", user).group(1))
            return json.dumps(expected[index])
    interpretation_cache.clear()
    with patch("app.llm.interpreter.get_provider_chain", return_value=[("judge-replay", Provider())]), \
            patch.object(optimize, "interpret_operator_notes", interpret_operator_notes):
        yield
    interpretation_cache.clear()


def service_checks(client, valid_request):
    checks = {}
    try:
        health = client.get("/health")
        checks["health"] = health.status_code == 200 and health.json() == {"status": "ok"}
    except Exception:
        checks["health"] = False
    try:
        response = client.get("/openapi.json")
        paths = response.json()["paths"]
        checks["openapi"] = response.status_code == 200 and "get" in paths["/health"] and "post" in paths["/optimize-energy"]
    except Exception:
        checks["openapi"] = False
    invalid = [{}, deepcopy(valid_request), deepcopy(valid_request), deepcopy(valid_request)]
    invalid[1]["hours"].pop()
    invalid[2]["hours"][0]["hour"] = invalid[2]["hours"][1]["hour"]
    invalid[3]["battery"]["capacity_kwh"] = -1
    for index, payload in enumerate(invalid):
        try:
            checks[f"invalid_request_{index}"] = client.post("/optimize-energy", json=payload).status_code == 422
        except Exception:
            checks[f"invalid_request_{index}"] = False
    return checks


def run_case(client, case, mode="live"):
    started = time.perf_counter()
    request, expected = case["input"], case["expected"]
    row = dict(case_id=case["id"], source=case["source"], checks={name: False for name in CASE_CHECKS},
               status_code=None, error=None, latency_ms=None, actual_cost_bdt=None, optimal_cost_bdt=case["optimal_cost"])
    if not any(d["directive_type"] != "no_op" for d in expected):
        row["checks"]["directive_hours"] = None
    if not any(d["directive_type"] in NUMBERS for d in expected):
        row["checks"]["directive_values"] = None
    try:
        if mode == "replay":
            with replay_interpretation(expected):
                response = client.post("/optimize-energy", json=request)
        else:
            response = client.post("/optimize-energy", json=request)
        row["status_code"] = response.status_code
        if response.status_code == 200:
            data = response.json()
            row["checks"] = check_result(request, data, expected, case["optimal_cost"])
            if row["checks"]["response_schema"]:
                row["actual_cost_bdt"] = data["total_cost_bdt"]
    except Exception as exc:
        row["error"] = type(exc).__name__  # No error bodies or raw model responses.
    row["latency_ms"] = round((time.perf_counter() - started) * 1000, 3)
    row["failed_checks"] = [name for name, passed in row["checks"].items() if passed is False]
    row["passed"] = not row["failed_checks"]
    return row


def score_report(service, results):
    def rate(values):
        applicable = [value for value in values if value is not None]
        return sum(applicable) / len(applicable) if applicable else 0
    scores = {}
    for category, weight in WEIGHTS.items():
        if category == "health":
            values = [service["health"]]
        elif category == "api_schema":
            values = [value for name, value in service.items() if name != "health"] + [row["checks"]["response_schema"] for row in results]
        else:
            values = [row["checks"][category] for row in results]
        fraction = rate(values)
        scores[category] = dict(weight=weight, pass_rate=fraction, estimated_points=weight * fraction)
    return dict(estimated_score_out_of_100=round(sum(score["estimated_points"] for score in scores.values()), 2), categories=scores)


def write_report(path, metadata, service, results, generated):
    report = {**metadata, **score_report(service, results), "service_checks": service, "results": results,
              "summary": {source: dict(cases=sum(row["source"] == source for row in results),
                                       passed=sum(row["source"] == source and row["passed"] for row in results)) for source in ("public", "random")},
              "generated_cases": generated}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("live", "replay"), default="live")
    parser.add_argument("--base-url", help="Test a running API; omitted uses the real local ASGI application")
    parser.add_argument("--providers", help="Explicit comma-separated provider profile for local live runs; recorded in report")
    parser.add_argument("--random-cases", type=int, default=24)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--samples", type=Path, default=ROOT / "tests/fixtures/sample_cases.json")
    parser.add_argument("--output", type=Path, default=ROOT / "reports/judge_score_estimate.json")
    args = parser.parse_args()
    if args.random_cases < 1 or not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("random-cases and timeout must be positive")
    if args.mode == "replay" and args.base_url:
        parser.error("Replay is only available for the in-process API")
    if args.providers and (args.base_url or args.mode == "replay"):
        parser.error("Provider selection only applies to local live runs")
    load_dotenv(ROOT / ".env")
    if args.providers:
        from app.llm.provider import _PROVIDER_REGISTRY
        names = [name.strip().lower() for name in args.providers.split(",") if name.strip()]
        if not names or any(name not in _PROVIDER_REGISTRY for name in names):
            parser.error("Provider profile contains unknown/empty provider names")
        os.environ["LLM_PROVIDERS"] = ",".join(names)
    # Provider bodies can echo account identifiers; keep CLI output to judge
    # results rather than model error logs. API behavior remains unchanged.
    for name in ("app.llm", "app.api", "httpx"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    raw = args.samples.read_bytes()
    public = json.loads(raw)["cases"]
    if not public:
        parser.error("At least one public case is required")
    cases = [dict(id=case["id"], source="public", input=case["input"],
                  expected=case["expected_output"]["directive_interpretation"],
                  optimal_cost=case["expected_output"]["total_cost_bdt"]) for case in public]
    generated = generate_cases(args.random_cases, args.seed)
    for case in generated:
        case["optimal_cost"] = reference_optimal_cost(case["input"], case["expected"])
    cases += generated
    metadata = dict(generated_at=datetime.now(timezone.utc).isoformat(), mode=args.mode,
                    transport="remote_http" if args.base_url else "local_asgi", seed=args.seed,
                    provider_profile_override=args.providers,
                    configured_provider_names=os.getenv("LLM_PROVIDERS", os.getenv("LLM_PROVIDER", "")) if not args.base_url else None,
                    public_dataset_sha256=hashlib.sha256(raw).hexdigest(), complete=False,
                    disclaimer="Simulator-defined 100-point estimate; no official weights/rubric or hidden cases were supplied. Replay assumes correct LLM output and measures plumbing, not real interpretation accuracy. Live mode uses the actual API interpretation path. No reference hourly schedules are compared. Random cases have independently generated semantic labels and a separately built binary optimization oracle. Expected directives, not returned directives, govern compliance. Each category averages applicable checks; a score is not a prediction of the official result.",
                    timeout_seconds=args.timeout,
                    timeout_note="HTTP timeout applies to remote transport. Local ASGI requests are synchronous and use the application's provider timeouts.")
    from app.main import app
    from app.optimizer.cache import optimization_cache
    if not args.base_url:
        optimization_cache.clear()
    client = httpx.Client(base_url=args.base_url, timeout=args.timeout) if args.base_url else TestClient(app, raise_server_exceptions=False)
    results = []
    with client:
        service = service_checks(client, cases[0]["input"])
        for index, case in enumerate(cases):
            result = run_case(client, case, args.mode)
            results.append(result)
            write_report(args.output, metadata, service, results, generated)
            print(f"[{index + 1}/{len(cases)}] {case['id']}: {'PASS' if result['passed'] else 'FAIL'} {result['failed_checks']}", flush=True)
    metadata["complete"] = True
    report = write_report(args.output, metadata, service, results, generated)
    print(f"Estimated score: {report['estimated_score_out_of_100']}/100; report: {args.output}", flush=True)


if __name__ == "__main__":
    main()
