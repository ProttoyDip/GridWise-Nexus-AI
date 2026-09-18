"""Unit tests for the curated model registry (app.llm.model_registry)."""

import pytest


@pytest.fixture(autouse=True)
def heuristic_defaults(monkeypatch, tmp_path):
    # Test the documented heuristic fallback independently of live reports.
    monkeypatch.setattr("app.llm.measured_defaults.DEFAULTS_PATH", tmp_path / "missing.json")

from app.llm.model_registry import (
    ARBITER_MODELS,
    FAST_MODELS,
    PRIMARY_INTERPRETERS,
    ModelSpec,
    build_provider,
    get_arbiter_model,
    get_fast_model,
    get_primary_model,
)
from app.llm.provider import (
    AnthropicProvider,
    LLMProviderError,
    OpenAICompatibleProvider,
)


def _clear_llm_env(monkeypatch):
    import os

    for key in list(os.environ):
        if key.startswith("LLM_"):
            monkeypatch.delenv(key, raising=False)


CATEGORY_EXPECTED_NAMES = {
    "primary": {
        "deepseek/deepseek-v4-flash-0731:free",
        "deepseek-v4-flash",
        "google/gemma-4-31b-it:free",
        "qwen/qwen3.8-27b:free",
    },
    "arbiter": {"nvidia/nemotron-3-ultra-550b-a55b:free", "gpt-5.6-luna"},
    "fast": {"liquid/lfm-2.5-2.6b:free"},
}


def test_category_membership_matches_spec():
    assert {m.name for m in PRIMARY_INTERPRETERS} == CATEGORY_EXPECTED_NAMES["primary"]
    assert {m.name for m in ARBITER_MODELS} == CATEGORY_EXPECTED_NAMES["arbiter"]
    assert {m.name for m in FAST_MODELS} == CATEGORY_EXPECTED_NAMES["fast"]


@pytest.mark.parametrize("category", [PRIMARY_INTERPRETERS, ARBITER_MODELS, FAST_MODELS])
def test_every_spec_has_required_fields(category):
    for spec in category:
        assert isinstance(spec, ModelSpec)
        assert spec.name
        assert spec.provider
        assert isinstance(spec.priority, int)
        assert spec.expected_latency > 0
        assert 0.0 <= spec.json_reliability_score <= 1.0
        assert 0.0 <= spec.reasoning_score <= 1.0


def test_priorities_are_unique_and_ascend_from_one():
    for category in (PRIMARY_INTERPRETERS, ARBITER_MODELS, FAST_MODELS):
        priorities = sorted(spec.priority for spec in category)
        assert priorities == list(range(1, len(category) + 1))


def test_get_primary_model_without_availability_filter_returns_top_priority():
    spec = get_primary_model(require_configured=False)
    assert spec is not None
    assert spec.priority == 1
    assert spec.name == "deepseek/deepseek-v4-flash-0731:free"


def test_get_fast_model_and_arbiter_model_without_filter():
    fast = get_fast_model(require_configured=False)
    assert fast is not None and fast.name == "liquid/lfm-2.5-2.6b:free"

    arbiter = get_arbiter_model(require_configured=False)
    assert arbiter is not None and arbiter.name == "nvidia/nemotron-3-ultra-550b-a55b:free"


def test_get_primary_model_returns_none_when_nothing_configured(monkeypatch):
    _clear_llm_env(monkeypatch)
    assert get_primary_model() is None
    assert get_fast_model() is None
    assert get_arbiter_model() is None


def test_get_primary_model_skips_unconfigured_providers_in_priority_order(monkeypatch):
    _clear_llm_env(monkeypatch)
    # Only experimentallab is configured; the #1-priority primary interpreter
    # is on openrouter, so this should fall through to the #2 entry.
    monkeypatch.setenv("LLM_PROVIDERS", "experimentallab")
    monkeypatch.setenv("LLM_API_KEY_EXPERIMENTALLAB", "fake-key")

    spec = get_primary_model()
    assert spec is not None
    assert spec.name == "deepseek-v4-flash"
    assert spec.provider == "experimentallab"


def test_get_arbiter_model_none_when_only_unrelated_provider_configured(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDERS", "nararouter")
    monkeypatch.setenv("LLM_API_KEY_NARAROUTER", "fake-key")

    # Neither arbiter model's provider (openrouter, experimentallab) is configured.
    assert get_arbiter_model() is None


def test_build_provider_resolves_key_and_instantiates_openai_compatible(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_API_KEY_OPENROUTER", "fake-key")
    spec = get_primary_model(require_configured=False)  # deepseek on openrouter

    provider = build_provider(spec)
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.model == spec.name
    assert provider._url == "https://openrouter.ai/api/v1/chat/completions"


def test_build_provider_raises_without_configured_key(monkeypatch):
    _clear_llm_env(monkeypatch)
    spec = get_primary_model(require_configured=False)
    with pytest.raises(LLMProviderError):
        build_provider(spec)


def test_build_provider_falls_back_to_unsuffixed_llm_api_key(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_API_KEY", "fake-key")
    spec = get_arbiter_model(require_configured=False)  # experimentallab or openrouter

    provider = build_provider(spec)
    assert isinstance(provider, (OpenAICompatibleProvider, AnthropicProvider))
