"""Compare cold MIP, warm MIP and lossless LP with validity/cost checks."""

import argparse
import json
from pathlib import Path
import statistics
import time

from app.guardrails.validator import validate_directive_interpretation
from app.models.request import ScenarioRequest
from app.optimizer.solver import solve_energy_schedule
from app.optimizer.warm_start import verify_pattern, warm_start_store
from app.verifier.schedule_checker import recalculate_totals

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=10)
    parser.add_argument("--output", type=Path, default=ROOT / "reports/optimizer_benchmark.json")
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("repeats must be positive")
    cases = json.loads((ROOT / "tests/fixtures/sample_cases.json").read_text())["cases"]
    samples = []
    warm_start_store.clear()
    for case in cases:
        original = ScenarioRequest.model_validate(case["input"])
        directives = validate_directive_interpretation(case["expected_output"]["directive_interpretation"], battery=original.battery)
        plan = solve_energy_schedule(original.hours, original.battery, directives, use_warm_start=False)
        warm_start_store.remember(original.hours, original.battery, directives, plan)
        # Slight forecast/tariff changes ensure this is similar-scenario reuse,
        # rather than an exact-result cache hit. Battery and numeric limits
        # are scaled together, preserving feasibility.
        data = original.model_dump()
        for hour in data["hours"]:
            hour["demand_kwh"] *= 1.01
            hour["solar_kwh"] *= 1.01
            hour["tariff_bdt_per_kwh"] *= 1 + (0.01 if hour["hour"] % 2 else -0.01)
        for field in data["battery"]:
            data["battery"][field] *= 1.01
        adjusted = [directive.model_copy(deep=True) for directive in directives]
        for directive in adjusted:
            if directive.structured_adjustment:
                for field in ("minimum_energy_kwh", "max_grid_kwh"):
                    if field in directive.structured_adjustment:
                        directive.structured_adjustment[field] *= 1.01
        samples.append((case["id"], ScenarioRequest.model_validate(data), adjusted))
    results = []
    for repeat in range(args.repeats):
        for case_id, scenario, directives in samples:
            row = {"case_id": case_id, "repeat": repeat, "warm_start_available": warm_start_store.find(scenario.hours, scenario.battery, directives) is not None}
            modes = [("before", False, True), ("warm_mip", True, True), ("after", True, False)]
            for mode, warm, binary in (modes if repeat % 2 == 0 else list(reversed(modes))):
                start = time.perf_counter()
                plan = solve_energy_schedule(scenario.hours, scenario.battery, directives,
                                             use_warm_start=warm, use_binary_modes=binary)
                elapsed = (time.perf_counter() - start) * 1000
                verify_pattern(scenario.hours, scenario.battery, directives, plan)
                row[mode + "_ms"] = elapsed
                row[mode + "_cost_bdt"] = recalculate_totals(scenario.hours, plan).total_cost_bdt
            for mode in ("after", "warm_mip"):
                if abs(row["before_cost_bdt"] - row[mode + "_cost_bdt"]) > max(1e-5, row["before_cost_bdt"] * 1e-7):
                    raise RuntimeError("Optimizer formulation objectives differ")
            results.append(row)
    before = statistics.mean(row["before_ms"] for row in results)
    after = statistics.mean(row["after_ms"] for row in results)
    report = {"case_count": len(samples), "repeats": args.repeats, "paired_solves": len(results),
              "before_average_ms": before, "after_average_ms": after,
              "warm_mip_average_ms": statistics.mean(row["warm_mip_ms"] for row in results),
              "speedup_percent": (before - after) / before * 100,
              "all_objectives_match": True, "all_schedules_verified": True,
              "method": "Direct solver calls; result and interpretation caches bypassed. Alternating comparison order on public cases with 1% changes. Before: original cold binary formulation. After: equivalent lossless LP formulation, with cycle cancellation and verified history storage. Native warm binary formulation is also measured separately. Timings include validation, model construction, applicable history search, CBC invocation and history storage, excluding independent benchmark verification. Training solves excluded. No time/node limits or nonzero optimality gaps.",
              "results": results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    args.output.with_suffix(".md").write_text(
        f"# Optimizer benchmark\n\n{report['method']}\n\n"
        f"- Cases: {len(samples)}; repetitions: {args.repeats}; paired solves: {len(results)}\n"
        f"- Before average: {before:.3f} ms\n- After average: {after:.3f} ms\n"
        f"- Native warm binary average: {report['warm_mip_average_ms']:.3f} ms\n"
        f"- Speedup: {report['speedup_percent']:.2f}%\n"
        "- Every schedule independently verified; every cold/warm objective matched.\n"
        "\nThese small CBC models are sensitive to process startup and machine load. A warm start is a hint, not a guarantee of speedup.\n")
    print(f"Before average: {before:.3f} ms; after average: {after:.3f} ms; speedup: {report['speedup_percent']:.2f}%", flush=True)


if __name__ == "__main__":
    main()
