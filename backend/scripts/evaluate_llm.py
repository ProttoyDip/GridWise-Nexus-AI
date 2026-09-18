"""Evaluate individual models directly; never use cache, consensus or repair.

Run from backend: python -m scripts.evaluate_llm --apply-defaults
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from app.guardrails.validator import validate_directive
from app.llm.json_parser import parse_json_object, parse_strict_json_object
from app.llm.model_registry import ARBITER_MODELS, FAST_MODELS, PRIMARY_INTERPRETERS
from app.llm.output_schema import directive_json_schema
from app.llm.prompts import SYSTEM_PROMPT, build_user_prompt
from app.llm.provider import LLMProviderError, StructuredOutputUnsupported, _PROVIDER_REGISTRY, _build_provider, get_provider_chain
from app.models.request import ScenarioRequest

ROOT = Path(__file__).resolve().parents[1]
NUMERIC_FIELDS = {"solar_reduction": "factor", "minimum_battery_reserve": "minimum_energy_kwh", "max_grid_window": "max_grid_kwh"}


def candidate_models() -> list[tuple[str, str]]:
    candidates = [(provider, model) for provider, (_, models) in _PROVIDER_REGISTRY.items() for model in models]
    candidates.extend((spec.provider, spec.name) for spec in PRIMARY_INTERPRETERS + FAST_MODELS + ARBITER_MODELS)
    try:
        candidates.extend((name, model.model) for name, model in get_provider_chain())
    except LLMProviderError:
        pass
    return list(dict.fromkeys(candidates))


def score_output(raw, expected: dict, note_index: int) -> dict:
    scores = dict(valid_json=False, recoverable_json=False, valid_directive=False,
                  correct_directive_type=False, correct_hours=None, correct_numeric_values=None, fully_correct=False)
    adjustment = expected["structured_adjustment"]
    numeric_field = NUMERIC_FIELDS.get(expected["directive_type"])
    if adjustment is not None:
        scores["correct_hours"] = False
    if numeric_field:
        scores["correct_numeric_values"] = False
    try:
        parsed = parse_strict_json_object(raw)
        scores["valid_json"] = True
    except (ValueError, TypeError, RecursionError, OverflowError):
        try:
            parsed = dict(raw) if isinstance(raw, dict) else parse_json_object(raw)
        except (ValueError, TypeError, RecursionError, OverflowError):
            return scores
    scores["recoverable_json"] = True
    type_correct = parsed.get("directive_type") == expected["directive_type"]
    scores["correct_directive_type"] = type_correct
    actual = parsed.get("structured_adjustment")
    if adjustment is not None:
        hours = actual.get("hours") if isinstance(actual, dict) else None
        scores["correct_hours"] = type_correct and isinstance(hours, list) and all(type(h) is int for h in hours) and hours == adjustment["hours"]
    if numeric_field:
        number = actual.get(numeric_field) if isinstance(actual, dict) else None
        try:
            scores["correct_numeric_values"] = type_correct and type(number) in (int, float) and math.isfinite(number) and math.isclose(number, adjustment[numeric_field], rel_tol=1e-6, abs_tol=1e-6)
        except OverflowError:
            scores["correct_numeric_values"] = False
    try:
        parsed["note_index"] = note_index
        validate_directive(parsed, note_index)
        scores["valid_directive"] = True
    except (ValueError, TypeError, OverflowError):
        pass
    scores["fully_correct"] = (
        scores["valid_directive"] and type_correct and parsed.get("applies") is expected["applies"]
        and (actual is None if adjustment is None else scores["correct_hours"])
        and scores["correct_numeric_values"] is not False
    )
    return scores


def evaluate_model(provider_name: str, model_name: str, cases: list[dict], timeout: float,
                   *, system_prompt: str = SYSTEM_PROMPT, structured_output: bool = True) -> dict:
    api_key = os.getenv(f"LLM_API_KEY_{provider_name.upper()}", "").strip() or os.getenv("LLM_API_KEY", "").strip()
    summary = {"provider": provider_name, "model": model_name, "status": "completed", "results": []}
    if not api_key:
        summary["status"] = "missing_credentials"
        return summary
    model = _build_provider(provider_name, api_key, model_name)
    model._timeout = timeout
    for case in cases:
        scenario = ScenarioRequest.model_validate(case["input"])
        for expected in case["expected_output"]["directive_interpretation"]:
            index = expected["note_index"]
            row = {"case_id": case["id"], "note_index": index, "error": None, "latency_ms": None, "native_requested": False, "text_fallback": False}
            start = time.perf_counter()
            try:
                prompt = build_user_prompt(scenario.operator_notes[index], index, scenario.hours, scenario.battery)
                if structured_output and getattr(model, "supports_structured_output", False):
                    row["native_requested"] = True
                    try:
                        raw = model.complete_structured(system_prompt, prompt, directive_json_schema())
                    except StructuredOutputUnsupported:
                        row["text_fallback"] = True
                        raw = model.complete(system_prompt, prompt)
                else:
                    raw = model.complete(system_prompt, prompt)
                row.update(score_output(raw, expected, index))
            except (LLMProviderError, ValueError, TypeError, RecursionError, OverflowError) as exc:
                # Store categories only; provider bodies can echo credentials.
                row["error"] = type(exc).__name__
                row.update(score_output("", expected, index))
            row["latency_ms"] = round((time.perf_counter() - start) * 1000, 3)
            summary["results"].append(row)
    return summary


def summarize(model: dict) -> dict:
    rows = model["results"]
    metrics = {}
    for field in ("valid_json", "recoverable_json", "valid_directive", "correct_directive_type", "correct_hours", "correct_numeric_values", "fully_correct"):
        applicable = [row[field] for row in rows if row[field] is not None]
        metrics[field + "_rate"] = sum(applicable) / len(applicable) if applicable else None
        metrics[field + "_count"] = len(applicable)
    latencies = [row["latency_ms"] for row in rows if row["error"] is None]
    metrics["median_latency_ms"] = statistics.median(latencies) if latencies else None
    metrics["mean_latency_ms"] = statistics.mean(latencies) if latencies else None
    metrics["error_count"] = sum(row["error"] is not None for row in rows)
    return {**model, "metrics": metrics}


def rank_models(models: list[dict]) -> list[dict]:
    def sort_key(model):
        metrics = model["metrics"]
        return (-(metrics["fully_correct_rate"] or 0), -(metrics["valid_directive_rate"] or 0),
                -(metrics["valid_json_rate"] or 0), metrics["median_latency_ms"] if metrics["median_latency_ms"] is not None else math.inf,
                model["provider"], model["model"])
    return sorted(models, key=sort_key)


def select_defaults(ranked: list[dict], expected_notes: int) -> dict:
    eligible = [model for model in ranked if model["status"] == "completed" and len(model["results"]) == expected_notes
                and model["metrics"]["fully_correct_rate"] >= 0.9 and model["metrics"]["valid_json_rate"] >= 0.9]
    provider_order = {}
    for model in eligible:
        provider_order.setdefault(model["provider"], []).append(model["model"])
    def reference(model):
        return {"provider": model["provider"], "name": model["model"], "metrics": model["metrics"]} if model else None
    primary = eligible[0] if eligible else None
    fast = min(eligible, key=lambda model: model["metrics"]["median_latency_ms"]) if eligible else None
    alternatives = [model for model in eligible if primary and (model["provider"], model["model"]) != (primary["provider"], primary["model"])]
    diverse = [model for model in alternatives if model["provider"] != primary["provider"]]
    alternatives = diverse or alternatives
    arbiter = max(alternatives, key=lambda model: (model["metrics"]["correct_numeric_values_rate"] or 0,
                                                 model["metrics"]["fully_correct_rate"], -model["metrics"]["median_latency_ms"])) if alternatives else None
    return {"schema_version": 1, "provider_models": provider_order,
            "roles": {"primary": reference(primary), "fast": reference(fast), "arbiter": reference(arbiter)}}


def write_report(path: Path, models: list[dict], metadata: dict) -> dict:
    ranked = rank_models([summarize(model) for model in models])
    report = {**metadata, "rankings": ranked, "defaults": select_defaults(ranked, metadata["note_count"])}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    lines = ["# LLM interpretation ranking", "", f"Generated: {metadata['generated_at']}", "",
             f"Dataset: {metadata['case_count']} cases, {metadata['note_count']} notes per model. Timeout: {metadata['timeout_seconds']}s per HTTP call.", "",
             "Each candidate runs independently with native output attempted first. No cache, consensus, retries, repair, or cross-model failover is used. Explicit unsupported-format responses allow one text call; latency includes that call. Transport errors count as failures. JSON validity means the entire response is a strict JSON object; recoverable JSON is reported separately in the JSON artifact. Hours and numeric accuracy use only applicable notes, and require the correct directive type. Numeric tolerance: absolute/relative 1e-6.", "",
             "Ranking: full semantic accuracy, valid directive rate, strict JSON rate, then successful-response median latency. Defaults require full coverage and at least 90% full semantic and strict JSON accuracy. Public-sample performance does not establish hidden-test accuracy.", "",
             "| Rank | Provider | Model | JSON | Type | Hours | Numbers | Full | Median ms | Errors | Status |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    def percent(value):
        return f"{value:.1%}" if value is not None else "N/A"
    for index, model in enumerate(ranked, 1):
        m = model["metrics"]
        latency = f"{m['median_latency_ms']:.0f}" if m["median_latency_ms"] is not None else "N/A"
        lines.append(f"| {index} | {model['provider']} | {model['model']} | {percent(m['valid_json_rate'])} | {percent(m['correct_directive_type_rate'])} | {percent(m['correct_hours_rate'])} | {percent(m['correct_numeric_values_rate'])} | {percent(m['fully_correct_rate'])} | {latency} | {m['error_count']} | {model['status']} |")
    lines.extend(["", "## Selected defaults", ""])
    for role, model in report["defaults"]["roles"].items():
        lines.append(f"- {role}: {model['provider']}/{model['name']}" if model else f"- {role}: no eligible measured model; retain fallback policy")
    path.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path, default=ROOT / "tests/fixtures/sample_cases.json")
    parser.add_argument("--output", type=Path, default=ROOT / "reports/llm_ranking.json")
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--apply-defaults", action="store_true")
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or args.timeout <= 0 or args.workers < 1:
        parser.error("timeout and workers must be positive")
    load_dotenv(ROOT / ".env")
    raw = args.samples.read_bytes()
    cases = json.loads(raw)["cases"]
    for case in cases:
        scenario = ScenarioRequest.model_validate(case["input"])
        expected = case["expected_output"]["directive_interpretation"]
        if len(expected) != len(scenario.operator_notes):
            parser.error("Every operator note needs an expected interpretation")
        for index, directive in enumerate(expected):
            validate_directive(directive, index)
    metadata = {"generated_at": datetime.now(timezone.utc).isoformat(), "dataset_sha256": hashlib.sha256(raw).hexdigest(),
                "case_count": len(cases), "note_count": sum(len(c["input"]["operator_notes"]) for c in cases),
                "timeout_seconds": args.timeout, "workers": args.workers, "complete": False}
    if not metadata["note_count"]:
        parser.error("At least one labeled operator note is required")
    candidates = candidate_models()
    models = []
    print(f"Evaluating {len(candidates)} candidates on {metadata['note_count']} notes each", flush=True)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(evaluate_model, name, model, cases, args.timeout) for name, model in candidates]
        for future in as_completed(futures):
            model = future.result()
            models.append(model)
            report = write_report(args.output, models, metadata)
            metrics = summarize(model)["metrics"]
            print(f"[{len(models)}/{len(candidates)}] {model['provider']}/{model['model']}: {model['status']}, full={metrics['fully_correct_rate']}, errors={metrics['error_count']}", flush=True)
    metadata["complete"] = True
    report = write_report(args.output, models, metadata)
    # Refresh the demo's packaged metrics only after the full evaluation.
    from scripts.build_demo_benchmarks import publish_report
    try:
        publish_report(args.output)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"Demo benchmark refresh failed ({type(exc).__name__}); evaluation report retained.", flush=True)
    if args.apply_defaults:
        if not report["defaults"]["provider_models"]:
            parser.exit(1, "No model meets default-selection thresholds; existing defaults retained.\n")
        defaults = {**report["defaults"], "generated_at": metadata["generated_at"], "dataset_sha256": metadata["dataset_sha256"]}
        (ROOT / "app/llm/default_models.json").write_text(json.dumps(defaults, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(f"Report: {args.output} and {args.output.with_suffix('.md')}", flush=True)


if __name__ == "__main__":
    main()
