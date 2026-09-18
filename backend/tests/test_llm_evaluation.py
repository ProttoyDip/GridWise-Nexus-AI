"""Benchmark scoring and runtime selection without real provider calls."""

import copy
import json
from pathlib import Path

import pytest

from scripts import evaluate_llm as evaluation
from app.llm import measured_defaults, model_registry
from app.llm.provider import LLMProviderError, StructuredOutputUnsupported, get_provider_chain


CASES = json.loads((Path(__file__).parent / "fixtures/sample_cases.json").read_text())["cases"]
EXPECTED = next(d for case in CASES for d in case["expected_output"]["directive_interpretation"]
                if d["directive_type"] == "solar_reduction")


def test_strict_json_and_recovery_are_separate():
    raw = json.dumps(EXPECTED)
    assert evaluation.score_output(raw, EXPECTED, EXPECTED["note_index"])["valid_json"]
    result = evaluation.score_output("```json\n" + raw + "\n```", EXPECTED, EXPECTED["note_index"])
    assert not result["valid_json"]
    assert result["recoverable_json"] and result["fully_correct"]


@pytest.mark.parametrize("raw", ["{} trailing", "{", '[]', '{"a":1,"a":2}', '{"a":NaN}'])
def test_invalid_json_fails_strict_metric(raw):
    assert not evaluation.score_output(raw, EXPECTED, 0)["valid_json"]


@pytest.mark.parametrize("field,value,metric", [
    ("directive_type", "no_op", "correct_directive_type"),
    ("hours", [True], "correct_hours"),
    ("hours", [23], "correct_hours"),
    ("factor", True, "correct_numeric_values"),
    ("factor", float("inf"), "correct_numeric_values"),
    ("factor", 0.123456, "correct_numeric_values"),
])
def test_semantic_mismatches(field, value, metric):
    actual = copy.deepcopy(EXPECTED)
    if field == "directive_type":
        actual[field] = value
    else:
        actual["structured_adjustment"][field] = value
    result = evaluation.score_output(actual, EXPECTED, EXPECTED["note_index"])
    assert not result[metric] and not result["fully_correct"]


def test_no_op_has_no_hour_or_numeric_denominator():
    expected = next(d for case in CASES for d in case["expected_output"]["directive_interpretation"] if d["directive_type"] == "no_op")
    result = evaluation.score_output(expected, expected, expected["note_index"])
    assert result["fully_correct"]
    assert result["correct_hours"] is None and result["correct_numeric_values"] is None


def test_provider_calls_every_note_without_cache_and_counts_failures(monkeypatch):
    calls = []
    class Fake:
        supports_structured_output = True
        def complete_structured(self, *args):
            calls.append("native")
            raise StructuredOutputUnsupported("unsupported")
        def complete(self, *args):
            calls.append("text")
            raise LLMProviderError("secret-key-sensitive-body")
    monkeypatch.setenv("LLM_API_KEY_OPENROUTER", "secret-key")
    monkeypatch.setattr(evaluation, "_build_provider", lambda *args: Fake())
    model = evaluation.evaluate_model("openrouter", "fake", CASES, 1)
    count = sum(len(case["input"]["operator_notes"]) for case in CASES)
    assert len(calls) == count * 2
    metrics = evaluation.summarize(model)["metrics"]
    assert metrics["error_count"] == count and metrics["fully_correct_rate"] == 0
    assert metrics["median_latency_ms"] is None
    assert "secret-key" not in json.dumps(model)


def make_model(name, correct=True, latency=100, provider="openrouter"):
    score = evaluation.score_output(EXPECTED if correct else "{}", EXPECTED, EXPECTED["note_index"])
    return evaluation.summarize({"provider": provider, "model": name, "status": "completed",
                                 "results": [{**score, "latency_ms": latency, "error": None}]})


def test_accuracy_precedes_latency_and_defaults_require_coverage():
    ranked = evaluation.rank_models([make_model("wrong-fast", False, 1), make_model("accurate-slow", True, 1000),
                                     make_model("accurate-fast", True, 100)])
    assert ranked[0]["model"] == "accurate-fast"
    assert ranked[-1]["model"] == "wrong-fast"
    assert evaluation.select_defaults(ranked, 1)["roles"]["primary"]["name"] == "accurate-fast"
    assert not evaluation.select_defaults(ranked, 18)["provider_models"]


def test_arbiter_uses_another_provider():
    ranked = evaluation.rank_models([make_model("primary", latency=1), make_model("arbiter", provider="experimentallab")])
    roles = evaluation.select_defaults(ranked, 1)["roles"]
    assert roles["arbiter"]["provider"] != roles["primary"]["provider"]


def test_candidates_include_entire_registry():
    candidates = evaluation.candidate_models()
    assert len(candidates) == len(set(candidates))
    assert all((provider, name) in candidates for provider, (_, names) in evaluation._PROVIDER_REGISTRY.items() for name in names)


@pytest.fixture
def measured_report(monkeypatch, tmp_path):
    name = evaluation._PROVIDER_REGISTRY["openrouter"][1][-1]
    model = make_model(name, latency=123)
    defaults = evaluation.select_defaults([model], 1)
    path = tmp_path / "defaults.json"
    path.write_text(json.dumps(defaults))
    monkeypatch.setattr(measured_defaults, "DEFAULTS_PATH", path)
    return name, path


def test_measured_defaults_select_runtime_model_and_preserve_fallbacks(measured_report, monkeypatch):
    name, _ = measured_report
    fallback = evaluation._PROVIDER_REGISTRY["openrouter"][1]
    ordered = measured_defaults.ordered_models("openrouter", fallback)
    assert ordered[0] == name and set(ordered) == set(fallback)
    selected = model_registry.get_primary_model(require_configured=False)
    assert selected.name == name and selected.expected_latency == 0.123
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.delenv("LLM_PROVIDERS", raising=False)
    monkeypatch.setenv("LLM_API_KEY_OPENROUTER", "fake")
    monkeypatch.setenv("LLM_MODEL_OPENROUTER", "explicit")
    assert [provider.model for _, provider in get_provider_chain()] == ["explicit"]
    assert model_registry.get_primary_model().name != name


def test_interpreter_promotes_measured_primary_across_providers(measured_report, monkeypatch):
    from types import SimpleNamespace
    import os
    for key in list(os.environ):
        if key.startswith("LLM_MODEL"):
            monkeypatch.delenv(key)
    name, _ = measured_report
    chain = [("experimentallab", SimpleNamespace(model="other")), ("openrouter", SimpleNamespace(model=name))]
    assert measured_defaults.order_interpretation_chain(chain)[0] == chain[1]
    monkeypatch.setenv("LLM_MODELS_EXPERIMENTALLAB", "other")
    assert measured_defaults.order_interpretation_chain(chain) == chain


@pytest.mark.parametrize("data", ["bad json", '[]', '{"schema_version":2}', '{"schema_version":1,"provider_models":[],"roles":{}}'])
def test_bad_reports_keep_original_defaults(data, monkeypatch, tmp_path):
    path = tmp_path / "defaults.json"
    path.write_text(data)
    monkeypatch.setattr(measured_defaults, "DEFAULTS_PATH", path)
    assert measured_defaults.ordered_models("openrouter", ["a", "b"]) == ["a", "b"]


def test_ranking_report_has_all_requested_metrics(tmp_path):
    model = make_model("accurate")
    metadata = dict(generated_at="test", case_count=1, note_count=1, timeout_seconds=1, complete=True)
    path = tmp_path / "ranking.json"
    evaluation.write_report(path, [model], metadata)
    report = json.loads(path.read_text())
    assert report["complete"]
    assert report["rankings"][0]["metrics"]["fully_correct_rate"] == 1
    markdown = path.with_suffix(".md").read_text()
    assert all(label in markdown for label in ("JSON", "Type", "Hours", "Numbers", "Median ms"))
