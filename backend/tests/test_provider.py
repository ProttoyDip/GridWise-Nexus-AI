"""Unit tests for LLM provider chain resolution from environment variables."""

import pytest

from app.llm.provider import (
    _PROVIDER_REGISTRY,
    AnthropicProvider,
    LLMProviderError,
    OpenAICompatibleProvider,
    get_provider_chain,
)


def _clear_llm_env(monkeypatch):
    for key in list(__import__("os").environ):
        if key.startswith("LLM_"):
            monkeypatch.delenv(key, raising=False)


def _unique_ordered(names: list[str]) -> list[str]:
    seen: list[str] = []
    for name in names:
        if not seen or seen[-1] != name:
            seen.append(name)
    return seen


def test_raises_when_nothing_configured(monkeypatch):
    _clear_llm_env(monkeypatch)
    with pytest.raises(LLMProviderError):
        get_provider_chain()


def test_backward_compatible_single_provider_pins_one_model(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "openai/gpt-4o-mini")

    chain = get_provider_chain()
    assert len(chain) == 1
    name, provider = chain[0]
    assert name == "openrouter"
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.model == "openai/gpt-4o-mini"
    assert provider._url == "https://openrouter.ai/api/v1/chat/completions"


def test_unconfigured_model_expands_to_curated_free_list(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    # LLM_MODEL intentionally unset: should expand to the full default list

    chain = get_provider_chain()
    from app.llm.measured_defaults import ordered_models
    default_models = ordered_models("openrouter", _PROVIDER_REGISTRY["openrouter"][1])
    assert len(chain) == len(default_models)
    assert [provider.model for _, provider in chain] == default_models
    assert all(name == "openrouter" for name, _ in chain)


def test_explicit_models_list_overrides_default(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODELS_OPENROUTER", "model-a:free, model-b:free")

    chain = get_provider_chain()
    assert [provider.model for _, provider in chain] == ["model-a:free", "model-b:free"]


def test_multi_provider_chain_in_priority_order_with_default_expansion(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDERS", "openrouter,nararouter,experimentallab")
    monkeypatch.setenv("LLM_API_KEY_OPENROUTER", "key-a")
    monkeypatch.setenv("LLM_API_KEY_NARAROUTER", "key-b")
    monkeypatch.setenv("LLM_API_KEY_EXPERIMENTALLAB", "key-c")

    chain = get_provider_chain()
    names = [name for name, _ in chain]
    assert _unique_ordered(names) == ["openrouter", "nararouter", "experimentallab"]

    # each provider's block should match its own registry default length
    for provider_name in ("openrouter", "nararouter", "experimentallab"):
        block = [m for n, m in chain if n == provider_name]
        assert len(block) == len(_PROVIDER_REGISTRY[provider_name][1])

    urls = {name: provider._url for name, provider in chain}
    assert urls["openrouter"] == "https://openrouter.ai/api/v1/chat/completions"
    assert urls["nararouter"] == "https://router.bynara.id/v1/chat/completions"
    assert urls["experimentallab"] == "https://api.experientiallabs.ai/v1/chat/completions"


def test_provider_missing_api_key_is_skipped_not_fatal(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDERS", "openrouter,nararouter")
    monkeypatch.setenv("LLM_API_KEY_NARAROUTER", "key-b")
    monkeypatch.setenv("LLM_MODELS_NARAROUTER", "only-model")
    # LLM_API_KEY_OPENROUTER intentionally unset

    chain = get_provider_chain()
    assert [name for name, _ in chain] == ["nararouter"]


def test_anthropic_uses_native_provider_class(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_API_KEY", "anthropic-key")

    chain = get_provider_chain()
    assert len(chain) == 1
    name, provider = chain[0]
    assert name == "anthropic"
    assert isinstance(provider, AnthropicProvider)


def test_unsupported_provider_name_is_skipped(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDERS", "not-a-real-provider,nararouter")
    monkeypatch.setenv("LLM_API_KEY_NARAROUTER", "key-b")
    monkeypatch.setenv("LLM_MODELS_NARAROUTER", "only-model")

    chain = get_provider_chain()
    assert [name for name, _ in chain] == ["nararouter"]


def test_all_unusable_raises(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDERS", "openrouter,nararouter")
    with pytest.raises(LLMProviderError):
        get_provider_chain()


def test_no_claude_aliases_in_third_party_default_lists():
    """NaraRouter/ExperimentalLab default lists must never auto-select a
    `claude-*` id: neither service is a verified Anthropic reseller and
    neither exposes pricing to confirm the claim (see provider.py registry
    comment)."""
    for provider_name in ("nararouter", "experimentallab"):
        for model in _PROVIDER_REGISTRY[provider_name][1]:
            assert not model.lower().startswith("claude"), (provider_name, model)
