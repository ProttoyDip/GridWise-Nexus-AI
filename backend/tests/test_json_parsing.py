"""Adversarial extraction, native format payloads, and bounded repair retries."""

import json
from copy import deepcopy

import httpx
import pytest

from app.llm import interpreter, json_parser, provider
from app.llm.json_parser import JSONParsingError, parse_json_object
from app.llm.output_schema import directive_json_schema
from app.llm.provider import AnthropicProvider, OpenAICompatibleProvider, StructuredOutputUnsupported
from test_solver import battery, forecast

DATA = dict(note_index=0, applies=False, directive_type="no_op", structured_adjustment=None,
            explanation='Quoted "words", a backslash \\, and literal braces { } } {.')
TEXT = json.dumps(DATA)


@pytest.mark.parametrize("wrapper", [
    "{}", "```json\n{}\n```", "```\n{}\n```", "Here is the directive:\n{}\nDone.",
    '\ufeff{}', 'Prose with "{{quoted braces}}" then {}',
])
def test_decoder_handles_wrappers_and_string_braces(wrapper, monkeypatch):
    def must_not_run(text):
        raise AssertionError("Successful decoding must precede balancing")
    monkeypatch.setattr(json_parser, "extract_with_brace_balancing", must_not_run)
    assert parse_json_object(wrapper.format(TEXT)) == DATA


def test_nested_objects_and_arrays_are_one_top_level_value():
    data = {"outer": {"array": [{"value": "escaped quote: \\\" }"}, [1, 2]]}}
    assert parse_json_object("JSON:\n" + json.dumps(data) + "\nEnd") == data


@pytest.mark.parametrize("text", [
    "Prose {placeholder} then " + TEXT,
    TEXT + " trailing prose {placeholder}",
])
def test_balancing_fallback_skips_balanced_prose_fragments(text):
    assert parse_json_object(text) == DATA


def test_balancer_respects_quotes_escapes_and_nested_delimiters():
    data = {"a": [{"text": 'braces } { [ ], quote " and slash \\'}, {"b": [1, 2]}]}
    assert json_parser.extract_with_brace_balancing("prefix " + json.dumps(data)) == data


@pytest.mark.parametrize("text", [
    "", "no JSON", "null", "[]", "[" + TEXT + "]", json.dumps(TEXT),
    '{"outer": ' + TEXT,  # Never select a child of an incomplete parent.
    '{"outer": ' + TEXT + ', "broken": }',
    '{"a": [}', '{"a": "unterminated}', "{'a': 1}", '{"a": 1,}',
    '{"a": 1, "a": 2}', '{"a": 1, "\\u0061": 2}',
    '{"a": NaN}', '{"a": Infinity}', '{"a": -Infinity}', '{"a": 1e999}',
    TEXT + "\n" + TEXT, TEXT + "\n[]", '{"explanation": "bad\x01control"}',
])
def test_invalid_or_ambiguous_json_is_rejected(text):
    with pytest.raises(ValueError):
        parse_json_object(text)


def test_output_size_is_bounded():
    with pytest.raises(JSONParsingError, match="size limit"):
        parse_json_object("x" * (json_parser.MAX_OUTPUT_CHARACTERS + 1))


class TextModel:
    model = "test-model"

    def __init__(self, *responses):
        self.responses = iter(responses)
        self.prompts = []

    def complete(self, system_prompt, user_prompt):
        self.prompts.append(user_prompt)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


class NativeModel(TextModel):
    supports_structured_output = True

    def complete_structured(self, system_prompt, user_prompt, schema):
        self.schema = schema
        return super().complete(system_prompt, user_prompt)

    def complete(self, *args):
        raise AssertionError("Text must not be requested when native structured output works")


def interpret(model):
    return interpreter._interpret_single_note([("test", model)], "Original note.", 0, forecast(), battery())


def test_native_object_has_priority_and_does_not_mutate_output(monkeypatch):
    data = deepcopy(DATA)
    data["note_index"] = 999
    original = deepcopy(data)
    model = NativeModel(data)
    def no_text_parser(text):
        raise AssertionError("Native objects bypass text extraction")
    monkeypatch.setattr(interpreter, "_extract_json_object", no_text_parser)
    result = interpret(model)
    assert result.note_index == 0
    assert data == original
    assert model.schema == directive_json_schema()


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("bad_output", ["missing JSON", TEXT[:-1], DATA | {"applies": True}])
def test_parsing_or_validation_failure_retries_with_repair_prompt(monkeypatch, native, bad_output):
    monkeypatch.setattr(interpreter, "RETRY_BACKOFF_SECONDS", 0)
    if not native and isinstance(bad_output, dict):
        bad_output = json.dumps(bad_output)
    model = NativeModel(bad_output, deepcopy(DATA)) if native else TextModel(bad_output, TEXT)
    result = interpret(model)
    assert result.directive_type == "no_op"
    assert len(model.prompts) == 2
    assert "REPAIR REQUEST" not in model.prompts[0]
    assert "REPAIR REQUEST" in model.prompts[1]
    assert "Original note." in model.prompts[1]
    assert "Validation error" in model.prompts[1]
    assert "Previous response" in model.prompts[1]
    assert result._confidence_metadata.agreement_count == 1


def test_repair_retries_are_bounded(monkeypatch):
    monkeypatch.setattr(interpreter, "RETRY_BACKOFF_SECONDS", 0)
    model = TextModel(*["broken JSON"] * interpreter.MAX_ATTEMPTS)
    result = interpret(model)
    assert len(model.prompts) == interpreter.MAX_ATTEMPTS
    assert all("REPAIR REQUEST" in prompt for prompt in model.prompts[1:])
    assert result._confidence_metadata.confidence_score == 0
    assert "Falling back" in result.explanation


def test_wrong_output_type_can_be_repaired(monkeypatch):
    monkeypatch.setattr(interpreter, "RETRY_BACKOFF_SECONDS", 0)
    model = TextModel(b"invalid bytes output", TEXT)
    assert interpret(model).directive_type == "no_op"
    assert len(model.prompts) == 2


def test_arbiter_also_retries_with_repair_prompt(monkeypatch):
    from app.llm import consensus
    monkeypatch.setattr(consensus, "RETRY_BACKOFF_SECONDS", 0)
    model = TextModel("invalid arbiter output", TEXT)
    result = consensus._call_chain([("test", model)], "system", "Original arbitration context", 0)
    assert result.directive_type == "no_op"
    assert "REPAIR REQUEST" in model.prompts[1]
    assert "Original arbitration context" in model.prompts[1]


def test_transport_retry_does_not_request_json_repair(monkeypatch):
    monkeypatch.setattr(interpreter, "RETRY_BACKOFF_SECONDS", 0)
    model = TextModel(provider.LLMProviderError("transport failed"), TEXT)
    interpret(model)
    assert model.prompts[0] == model.prompts[1]


def test_new_model_starts_with_original_prompt_after_failed_repairs(monkeypatch):
    monkeypatch.setattr(interpreter, "RETRY_BACKOFF_SECONDS", 0)
    first = TextModel(*["invalid"] * interpreter.MAX_ATTEMPTS)
    second = TextModel(TEXT)
    second.model = "second-model"
    interpreter._interpret_single_note([("test", first), ("test", second)], "note", 0, forecast(), battery())
    assert "REPAIR REQUEST" not in second.prompts[0]


@pytest.mark.parametrize("kind", ["openai", "anthropic"])
def test_native_provider_sends_correct_schema_payload(monkeypatch, kind):
    captured = []
    def post(url, **kwargs):
        captured.append(kwargs["json"])
        body = ({"choices": [{"message": {"content": TEXT}}]} if kind == "openai" else
                {"content": [{"type": "text", "text": TEXT}]})
        return httpx.Response(200, json=body, request=httpx.Request("POST", url))
    monkeypatch.setattr(provider.httpx, "post", post)
    model = (OpenAICompatibleProvider("https://api.openai.com/v1", "test-key", "test-model")
             if kind == "openai" else AnthropicProvider("test-key", "test-model"))
    assert interpreter._call_and_parse(model, "system", "note", 0).directive_type == "no_op"
    payload = captured[0]
    if kind == "openai":
        assert payload["response_format"] == {"type": "json_schema", "json_schema": {
            "name": "operator_directive", "strict": True, "schema": directive_json_schema(),
        }}
    else:
        assert payload["output_config"] == {"format": {"type": "json_schema", "schema": directive_json_schema()}}


@pytest.mark.parametrize("kind", ["openai", "anthropic"])
def test_explicit_unsupported_native_format_falls_back_to_text(monkeypatch, kind):
    captured = []
    format_key = "response_format" if kind == "openai" else "output_config"
    def post(url, **kwargs):
        payload = kwargs["json"]
        captured.append(payload)
        if format_key in payload:
            return httpx.Response(400, text=f"{format_key} is not supported", request=httpx.Request("POST", url))
        body = ({"choices": [{"message": {"content": TEXT}}]} if kind == "openai" else
                {"content": [{"type": "text", "text": TEXT}]})
        return httpx.Response(200, json=body, request=httpx.Request("POST", url))
    monkeypatch.setattr(provider.httpx, "post", post)
    model = (OpenAICompatibleProvider("https://example.com/v1", "test-key", "test-model")
             if kind == "openai" else AnthropicProvider("test-key", "test-model"))
    assert interpreter._call_and_parse(model, "system", "note", 0).directive_type == "no_op"
    assert len(captured) == 2
    assert format_key not in captured[1]


@pytest.mark.parametrize("status,message,error_type", [
    (429, "quota exhausted", provider.LLMQuotaExceededError),
    (401, "invalid credentials", provider.LLMProviderError),
    (400, "Invalid schema for response_format", provider.LLMProviderError),
    (400, "Invalid schema for json_schema: unsupported keyword", provider.LLMProviderError),
    (500, "response_format is not supported", provider.LLMProviderError),
])
def test_quota_auth_schema_and_server_errors_do_not_downgrade_to_text(monkeypatch, status, message, error_type):
    calls = []
    def post(url, **kwargs):
        calls.append(kwargs)
        return httpx.Response(status, text=message, request=httpx.Request("POST", url))
    monkeypatch.setattr(provider.httpx, "post", post)
    model = OpenAICompatibleProvider("https://example.com/v1", "test-key", "test-model")
    with pytest.raises(error_type) as caught:
        interpreter._call_and_parse(model, "system", "note", 0)
    assert not isinstance(caught.value, StructuredOutputUnsupported)
    assert len(calls) == 1


def test_schema_is_strict_and_has_no_confidence_fields():
    schema = directive_json_schema()
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(DATA)
    assert "confidence" not in json.dumps(schema)
    for variant in schema["properties"]["structured_adjustment"]["anyOf"]:
        if variant["type"] == "object":
            assert variant["additionalProperties"] is False
            assert set(variant["required"]) == set(variant["properties"])
