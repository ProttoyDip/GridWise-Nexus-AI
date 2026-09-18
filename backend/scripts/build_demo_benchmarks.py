"""Package measured benchmark summaries, never example or invented numbers."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build_report(source: Path) -> dict:
    report = json.loads(source.read_text(encoding="utf-8"))
    rankings = report.get("rankings", [])
    eligible = [r for r in rankings if r.get("status") == "completed" and len(r.get("results", [])) == report.get("note_count")]
    measured = [r for r in eligible if r.get("metrics", {}).get("mean_latency_ms") is not None]
    best = max(measured, key=lambda r: (r["metrics"]["fully_correct_rate"], -r["metrics"]["mean_latency_ms"]), default=None)
    return {"generated_at": report.get("generated_at"), "complete": report.get("complete", False),
            "case_count": report.get("case_count"), "note_count": report.get("note_count"),
            "models_tested": len(rankings), "models_completed": len(eligible),
            "dataset_sha256": report.get("dataset_sha256"),
            "best_interpreter": f"{best['provider']}/{best['model']}" if best else None,
            "accuracy": best["metrics"]["fully_correct_rate"] if best else None,
            "mean_latency_seconds": best["metrics"]["mean_latency_ms"] / 1000 if best else None,
            "source": "reports/llm_ranking.json",
            "scope": "Measured public-sample interpretation benchmark; not hidden-test accuracy"}


def publish_report(source: Path, target: Path | None = None) -> Path:
    target = target if target is not None else ROOT / "app/demo/benchmark_report.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(build_report(source), indent=2) + "\n", encoding="utf-8")
    return target


if __name__ == "__main__":
    target = publish_report(ROOT / "reports/llm_ranking.json")
    print(f"Wrote {target}")
