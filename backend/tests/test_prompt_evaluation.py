"""Prompt benchmark protocol and recommendations without live inference."""

import json
from pathlib import Path

import pytest

from evaluation import evaluate_prompts as evaluation
from scripts import evaluate_llm

CASES = json.loads((Path(__file__).parent / "fixtures/sample_cases.json").read_text())["cases"]


def run(correct=True, latency=10):
    expected = CASES[0]["expected_output"]["directive_interpretation"][0]
    row = {**evaluate_llm.score_output(expected if correct else "{}", expected, 0),
           "error": None, "latency_ms": latency}
    return dict(status="completed", results=[row])


def metadata(**updates):
    return dict(complete=True, note_count=1, repeats=1, **updates)


def test_three_distinct_prompt_snapshots_exist():
    prompts = evaluation.load_prompts(evaluation.ROOT / "evaluation/prompts")
    assert tuple(prompts) == evaluation.PROMPT_NAMES
    assert len(set(prompts.values())) == 3
    for prompt in prompts.values():
        assert all(kind in prompt for kind in ("solar_reduction", "minimum_battery_reserve", "max_grid_window", "no_op"))


def test_missing_or_empty_prompt_is_rejected(tmp_path):
    with pytest.raises(FileNotFoundError):
        evaluation.load_prompts(tmp_path)
    for name in evaluation.PROMPT_NAMES:
        (tmp_path / f"{name}.txt").write_text("")
    with pytest.raises(ValueError):
        evaluation.load_prompts(tmp_path)


def test_fixture_checks_all_public_notes():
    assert evaluation.validate_samples(CASES) == 18
    with pytest.raises(ValueError):
        evaluation.validate_samples([])


def test_accuracy_outweighs_latency():
    prompts = dict(prompt_v1="baseline", prompt_v2="concise", prompt_v3="checklist")
    runs = {"prompt_v1": [run(False, 1)], "prompt_v2": [run(True, 20)], "prompt_v3": [run(True, 10)]}
    report = evaluation.build_report(prompts, runs, metadata())
    assert report["ranking"][0]["prompt_version"] == "prompt_v3"
    assert report["ranking"][-1]["prompt_version"] == "prompt_v1"
    assert report["recommendation"]["prompt_version"] == "prompt_v3"
    assert set(report["recommendation"]["accuracy_tied_versions"]) == {"prompt_v2", "prompt_v3"}


def test_partial_and_failed_reports_never_recommend_a_winner():
    prompts = {"prompt_v1": "baseline"}
    incomplete = dict(complete=False, note_count=1, repeats=1)
    assert evaluation.build_report(prompts, {"prompt_v1": [run()]}, incomplete)["recommendation"] is None
    assert evaluation.build_report(prompts, {"prompt_v1": []}, metadata())["recommendation"] is None
    assert evaluation.build_report(prompts, {"prompt_v1": [run()]}, dict(complete=True, note_count=2, repeats=1))["recommendation"] is None
    assert evaluation.build_report(prompts, {"prompt_v1": [run(False)]}, metadata())["recommendation"] is None


def test_repetitions_are_aggregated_with_applicable_denominators():
    result = evaluation.build_report({"prompt_v1": "baseline"}, {"prompt_v1": [run(), run(False)]},
                                     dict(complete=True, note_count=1, repeats=2))
    score = result["ranking"][0]
    assert score["metrics"]["fully_correct_rate"] == 0.5
    assert score["metrics"]["valid_json_count"] == 2
    assert [row["repeat"] for row in score["results"]] == [0, 1]
    assert len(score["prompt_sha256"]) == 64


def test_selected_system_prompt_and_text_only_are_used_on_raw_provider(monkeypatch):
    calls = []
    case = CASES[0]
    expected = case["expected_output"]["directive_interpretation"]
    class Model:
        supports_structured_output = True
        def complete_structured(self, *args):
            pytest.fail("Native format must not be used in text-only evaluation")
        def complete(self, system, user):
            import re
            index = int(re.search(r"Operator note \(index (\d+)\)", user).group(1))
            calls.append(system)
            return json.dumps(expected[index])
    monkeypatch.setenv("LLM_API_KEY_EXPERIMENTALLAB", "test-secret")
    monkeypatch.setattr(evaluate_llm, "_build_provider", lambda *args: Model())
    result = evaluate_llm.evaluate_model("experimentallab", "model", [case], 1,
                                       system_prompt="CUSTOM VERSION", structured_output=False)
    assert calls == ["CUSTOM VERSION"] * len(expected)
    assert all(not row["native_requested"] and row["fully_correct"] for row in result["results"])
    assert "test-secret" not in json.dumps(result)


def test_report_is_strict_json_and_has_all_requested_metrics(tmp_path):
    report = evaluation.build_report({"prompt_v1": "baseline"}, {"prompt_v1": [run()]}, metadata())
    path = tmp_path / "nested/prompt_score_report.json"
    evaluation.write_report(path, report)
    loaded = json.loads(path.read_text())
    metrics = loaded["ranking"][0]["metrics"]
    assert all(name in metrics for name in ("valid_json_rate", "correct_directive_type_rate", "correct_hours_rate",
                                            "correct_numeric_values_rate", "mean_latency_ms", "median_latency_ms"))
