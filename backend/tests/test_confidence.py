"""Confidence evidence controls model verification without changing API fields."""

import json
import re

import pytest
from fastapi.testclient import TestClient

from app.api import optimize
from app.guardrails.validator import validate_directive_interpretation
from app.llm import interpreter
from app.llm.confidence import ConfidenceDecision, assess_confidence
from app.llm.provider import LLMProviderError, LLMQuotaExceededError
from app.main import app
from app.models.request import ScenarioRequest
from app.models.response import DirectiveInterpretation
from test_solver import battery, forecast


def output(kind="solar_reduction", factor=0.2, explanation="Test interpretation."):
    return json.dumps(dict(
        note_index=999, applies=kind != "no_op", directive_type=kind,
        structured_adjustment=None if kind == "no_op" else {"hours": [13], "factor": factor},
        explanation=explanation,
    ))


class Model:
    def __init__(self, name, *responses):
        self.model = name
        self.responses = iter(responses)
        self.calls = 0

    def complete(self, system_prompt, user_prompt):
        self.calls += 1
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr(interpreter, "RETRY_BACKOFF_SECONDS", 0)
    monkeypatch.setattr(interpreter, "EXPONENTIAL_BACKOFF_BASE_SECONDS", 0)
    monkeypatch.setattr(interpreter, "SHORT_RETRY_BACKOFF_SECONDS", 0)
    monkeypatch.setattr(interpreter, "EXPONENTIAL_BACKOFF_BASE_SECONDS", 0)
    monkeypatch.setattr(interpreter, "SHORT_RETRY_BACKOFF_SECONDS", 0)


def interpret(*models):
    return interpreter._interpret_single_note(
        [("provider", model) for model in models], "note", 0, forecast(), battery(),
    )


def test_single_valid_candidate_requests_verification():
    model = Model("a", output())
    result = interpret(model)
    metadata = result._confidence_metadata
    assert metadata.confidence_score == 0.6
    assert metadata.agreement_count == 1
    assert metadata.models_used == ("provider/a",)
    assert metadata.decision == ConfidenceDecision.VERIFY
    assert model.calls == 1


def test_agreement_accepts_and_stops_before_unused_models():
    first = Model("a", output(factor=1, explanation="first"))
    second = Model("b", output(factor=1.0, explanation="different wording"))
    unused = Model("c")
    result = interpret(first, second, unused)
    metadata = result._confidence_metadata
    assert metadata.confidence_score == 1
    assert metadata.agreement_count == 2
    assert metadata.models_used == ("provider/a", "provider/b")
    assert metadata.decision == ConfidenceDecision.ACCEPT
    assert unused.calls == 0


def test_disagreement_escalates_to_third_model_and_accepts_majority():
    first, second, third = Model("a", output()), Model("b", output("no_op")), Model("c", output())
    result = interpret(first, second, third)
    metadata = result._confidence_metadata
    assert result.directive_type == "solar_reduction"
    assert metadata.confidence_score == pytest.approx(2 / 3)
    assert metadata.agreement_count == 2
    assert metadata.decision == ConfidenceDecision.ACCEPT
    assert metadata.models_used == ("provider/a", "provider/b", "provider/c")
    assert third.calls == 1


def test_unresolved_disagreement_falls_back_with_escalation_evidence():
    result = interpret(Model("a", output()), Model("b", output("no_op")))
    assert result.directive_type == "no_op"
    assert result.applies is False and result.structured_adjustment is None
    assert "disagreement" in result.explanation
    assert result._confidence_metadata.confidence_score == 0.5
    assert result._confidence_metadata.agreement_count == 1
    assert result._confidence_metadata.decision == ConfidenceDecision.ESCALATE


def test_retry_does_not_inflate_agreement():
    model = Model("a", "not JSON", output())
    result = interpret(model)
    assert model.calls == 2
    assert result._confidence_metadata.agreement_count == 1
    assert result._confidence_metadata.models_used == ("provider/a",)
    assert result._confidence_metadata.decision == ConfidenceDecision.VERIFY


def test_duplicate_chain_entries_cannot_manufacture_agreement():
    model = Model("a", output())
    duplicate = Model("a")
    result = interpret(model, duplicate)
    assert model.calls == 1 and duplicate.calls == 0
    assert result._confidence_metadata.agreement_count == 1
    assert result._confidence_metadata.decision == ConfidenceDecision.VERIFY


def test_failed_model_attempts_are_tracked_but_not_votes():
    failed = Model("a", *[LLMQuotaExceededError("no quota") for _ in range(interpreter.MAX_ATTEMPTS)])
    result = interpret(failed, Model("b", output()))
    metadata = result._confidence_metadata
    assert metadata.models_used == ("provider/a", "provider/b")
    assert metadata.agreement_count == 1
    assert metadata.confidence_score == 0.6
    assert failed.calls == interpreter.MAX_ATTEMPTS


def test_all_models_failed_have_zero_confidence():
    failed = Model("a", *[LLMProviderError("down") for _ in range(interpreter.MAX_ATTEMPTS)])
    result = interpret(failed)
    assert result.directive_type == "no_op"
    assert result._confidence_metadata.confidence_score == 0
    assert result._confidence_metadata.agreement_count == 0
    assert result._confidence_metadata.models_used == ("provider/a",)
    assert result._confidence_metadata.decision == ConfidenceDecision.ESCALATE


def test_unconfigured_providers_have_zero_confidence(monkeypatch):
    def missing():
        raise LLMProviderError("No configured providers")
    monkeypatch.setattr(interpreter, "get_provider_chain", missing)
    results = interpreter.interpret_operator_notes(["note", "other"], forecast(), battery())
    for result in results:
        assert result._confidence_metadata.confidence_score == 0
        assert result._confidence_metadata.agreement_count == 0
        assert result._confidence_metadata.models_used == ()
        assert result._confidence_metadata.decision == ConfidenceDecision.ESCALATE


@pytest.mark.parametrize("adjustment", [
    {"hours": [True], "factor": 0.2},
    {"hours": [13], "factor": float("nan")},
    {"factor": 0.2},
])
def test_invalid_candidates_do_not_count_as_evidence(adjustment):
    data = json.loads(output())
    data["structured_adjustment"] = adjustment
    model = Model("a", json.dumps(data), output())
    result = interpret(model)
    assert model.calls == 2
    assert result._confidence_metadata.agreement_count == 1
    assert result._confidence_metadata.confidence_score == 0.6


def test_guardrails_preserve_internal_metadata_without_serializing_it():
    result = interpret(Model("a", output()))
    checked = validate_directive_interpretation([result])[0]
    assert checked._confidence_metadata == result._confidence_metadata
    assert set(checked.model_dump()) == {
        "note_index", "applies", "directive_type", "structured_adjustment", "explanation",
    }
    assert "confidence" not in checked.model_dump_json()


def test_concurrent_notes_have_independent_metadata(monkeypatch):
    class PerNoteModel:
        def __init__(self, name):
            self.model = name

        def complete(self, system_prompt, user_prompt):
            index = int(re.search(r"Operator note \(index (\d+)\)", user_prompt).group(1))
            return output("no_op") if index == 1 and self.model == "b" else output()

    monkeypatch.setattr(interpreter, "get_provider_chain",
                        lambda: [("provider", PerNoteModel("a")), ("provider", PerNoteModel("b"))])
    results = interpreter.interpret_operator_notes(["one", "two"], forecast(), battery())
    assert [result.note_index for result in results] == [0, 1]
    assert results[0]._confidence_metadata.decision == ConfidenceDecision.ACCEPT
    assert results[0]._confidence_metadata.agreement_count == 2
    assert results[1]._confidence_metadata.decision == ConfidenceDecision.ESCALATE
    assert results[1]._confidence_metadata.agreement_count == 1


def test_api_schema_and_response_do_not_expose_metadata_and_verifier_still_runs(monkeypatch):
    monkeypatch.setattr(interpreter, "get_provider_chain",
                        lambda: [("provider", Model("a", output())), ("provider", Model("b", output()))])
    verified = []
    real_verify = optimize.verify_schedule
    def verify(scenario, response):
        verified.append(response.directive_interpretation[0]._confidence_metadata)
        real_verify(scenario, response)
    monkeypatch.setattr(optimize, "verify_schedule", verify)
    scenario = ScenarioRequest(scenario_id="confidence", operator_notes=["note"], hours=forecast(), battery=battery())
    response = TestClient(app).post("/optimize-energy", json=scenario.model_dump())
    assert response.status_code == 200
    assert len(verified) == 1 and verified[0].decision == ConfidenceDecision.ACCEPT
    for name in ("confidence_score", "agreement_count", "models_used", "_confidence_metadata"):
        assert name not in response.text
        assert name not in json.dumps(app.openapi())
        assert name not in DirectiveInterpretation.model_json_schema()["properties"]


def test_confidence_assessment_does_not_count_duplicate_model_votes():
    candidate = interpreter._extract_json_object(output())
    candidate["note_index"] = 0
    directive = DirectiveInterpretation.model_validate(candidate)
    _, metadata = assess_confidence([("a", directive), ("a", directive)], ["a", "a"])
    assert metadata.agreement_count == 1
    assert metadata.models_used == ("a",)
    assert metadata.decision == ConfidenceDecision.VERIFY
