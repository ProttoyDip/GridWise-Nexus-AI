"""Compare versioned system prompts on public notes using one fixed model."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import argparse
import hashlib
import json
import math
from pathlib import Path

from dotenv import load_dotenv

from app.guardrails.validator import validate_directive
from app.llm.model_registry import get_primary_model
from app.llm.provider import _PROVIDER_REGISTRY
from app.models.request import ScenarioRequest
from scripts.evaluate_llm import evaluate_model, summarize

ROOT = Path(__file__).resolve().parents[1]
PROMPT_NAMES = ("prompt_v1", "prompt_v2", "prompt_v3")


def load_prompts(directory: Path) -> dict[str, str]:
    prompts = {name: (directory / f"{name}.txt").read_text(encoding="utf-8") for name in PROMPT_NAMES}
    if any(not prompt.strip() for prompt in prompts.values()):
        raise ValueError("Prompt files must not be empty")
    return prompts


def validate_samples(cases: list[dict]) -> int:
    count = 0
    for case in cases:
        scenario = ScenarioRequest.model_validate(case["input"])
        expected = case["expected_output"]["directive_interpretation"]
        if len(expected) != len(scenario.operator_notes):
            raise ValueError("Every public note needs an expected directive")
        for index, directive in enumerate(expected):
            validate_directive(directive, index)
        count += len(expected)
    if count == 0:
        raise ValueError("At least one labeled public operator note is required")
    return count


def rank_prompts(scores: list[dict]) -> list[dict]:
    def key(score):
        metrics = score["metrics"]
        return (-(metrics["fully_correct_rate"] or 0), -(metrics["valid_json_rate"] or 0),
                -(metrics["valid_directive_rate"] or 0),
                metrics["median_latency_ms"] if metrics["median_latency_ms"] is not None else math.inf,
                score["prompt_version"])
    return sorted(scores, key=key)


def build_report(prompts, runs, metadata):
    scores = []
    for name, prompt in prompts.items():
        completed = runs[name]
        rows = [{**row, "repeat": repeat} for repeat, run in enumerate(completed) for row in run["results"]]
        score = summarize({"prompt_version": name, "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                           "status": "completed" if completed and all(run["status"] == "completed" for run in completed) else "incomplete",
                           "results": rows})
        scores.append(score)
    ranking = rank_prompts(scores)
    eligible = [score for score in ranking if metadata["complete"] and score["status"] == "completed"
                and len(score["results"]) == metadata["note_count"] * metadata["repeats"]
                and score["metrics"]["fully_correct_rate"] >= 0.9 and score["metrics"]["valid_json_rate"] >= 0.9]
    recommendation = None
    if eligible:
        best = eligible[0]
        quality = (best["metrics"]["fully_correct_rate"], best["metrics"]["valid_json_rate"], best["metrics"]["valid_directive_rate"])
        tied = [score["prompt_version"] for score in eligible if
                (score["metrics"]["fully_correct_rate"], score["metrics"]["valid_json_rate"], score["metrics"]["valid_directive_rate"]) == quality]
        recommendation = {"prompt_version": best["prompt_version"], "prompt_sha256": best["prompt_sha256"],
                          "accuracy_tied_versions": tied,
                          "reason": "Rank by full directive accuracy, strict JSON validity, valid directive rate, then median successful-response latency. Require full coverage and >=90% full accuracy and JSON validity. If accuracy is tied, latency is only a provisional tie-breaker."}
    return {**metadata, "ranking": ranking, "recommendation": recommendation}


def write_report(path: Path, report: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path, default=ROOT / "tests/fixtures/sample_cases.json")
    parser.add_argument("--prompts", type=Path, default=ROOT / "evaluation/prompts")
    parser.add_argument("--output", type=Path, default=ROOT / "evaluation/prompt_score_report.json")
    parser.add_argument("--provider", choices=tuple(_PROVIDER_REGISTRY))
    parser.add_argument("--model")
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--text-only", action="store_true", help="Disable native JSON enforcement to evaluate unassisted prompt output")
    args = parser.parse_args()
    if bool(args.provider) != bool(args.model):
        parser.error("Supply --provider and --model together")
    if args.repeats < 1 or args.workers < 1 or not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("repeats, workers and timeout must be positive")
    load_dotenv(ROOT / ".env")
    if not args.provider:
        selected = get_primary_model()
        if selected is None:
            parser.error("No configured default model; configure credentials or specify a provider/model")
        args.provider, args.model = selected.provider, selected.name
    prompts = load_prompts(args.prompts)
    raw = args.samples.read_bytes()
    cases = json.loads(raw)["cases"]
    notes = validate_samples(cases)
    metadata = {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
                "provider": args.provider, "model": args.model, "dataset_sha256": hashlib.sha256(raw).hexdigest(),
                "case_count": len(cases), "note_count": notes, "repeats": args.repeats, "workers": args.workers,
                "timeout_seconds": args.timeout, "native_structured_output": not args.text_only, "complete": False,
                "method": "Fixed provider/model and user prompt across versions. Raw first responses scored without cache, corrections, repair, retries, consensus, or failover. Strict JSON means the entire response; hours and numeric accuracy require matching directive type and apply only to relevant notes. Numeric absolute/relative tolerance is 1e-6. Transport errors count as failures; successful-response latency excludes failed calls. Native format enforcement, when enabled and supported, can mask differences in unassisted JSON validity. Each repetition rotates launch order; parallel workers can affect latency. No credentials or raw provider responses are stored. Public-sample results do not establish hidden-test accuracy."}
    runs = {name: [] for name in prompts}
    print(f"Evaluating {len(prompts)} prompts on {notes} notes x {args.repeats} repeats using {args.provider}/{args.model}", flush=True)
    for repeat in range(args.repeats):
        names = list(prompts)
        names = names[repeat % len(names):] + names[:repeat % len(names)]
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {name: pool.submit(evaluate_model, args.provider, args.model, cases, args.timeout,
                                        system_prompt=prompts[name], structured_output=not args.text_only) for name in names}
            for name, future in futures.items():
                run = future.result()
                runs[name].append(run)
                write_report(args.output, build_report(prompts, runs, metadata))
                metrics = summarize(run)["metrics"]
                print(f"{name} repeat {repeat + 1}: full={metrics['fully_correct_rate']}, errors={metrics['error_count']}", flush=True)
    metadata["complete"] = True
    report = build_report(prompts, runs, metadata)
    write_report(args.output, report)
    recommendation = report["recommendation"]
    print(f"Report: {args.output}", flush=True)
    if recommendation is None:
        parser.exit(1, "No prompt met coverage and accuracy thresholds.\n")
    print(f"Recommended: {recommendation['prompt_version']}", flush=True)


if __name__ == "__main__":
    main()
