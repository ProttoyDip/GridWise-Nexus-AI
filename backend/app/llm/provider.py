"""LLM provider abstraction with multi-provider failover.

Defines a minimal provider interface (`LLMProvider.complete`), concrete
implementations for the providers GridWise supports, and an ordered
provider chain read entirely from environment variables so operators
can configure several API keys (e.g. across OpenRouter, NaraRouter,
ExperimentalLab, OpenAI, Anthropic) and have interpretation
automatically fail over to the next one when a provider runs out of
quota.

Every failure a provider can produce is normalized to `LLMProviderError`
(or its subclass `LLMQuotaExceededError` for rate-limit/quota
exhaustion specifically) so the interpreter layer can retry or fail
over uniformly without knowing provider-specific exception types.

Environment configuration
--------------------------
`LLM_PROVIDERS` — comma-separated, priority-ordered list of provider
names to use as a failover chain, e.g. `openrouter,nararouter,openai`.
Falls back to the single `LLM_PROVIDER` var if `LLM_PROVIDERS` is unset,
for backward compatibility.

For each provider name in the chain, GridWise reads:
- `LLM_API_KEY_<NAME>` (falls back to `LLM_API_KEY` if only one provider
  is configured)
- `LLM_MODEL_<NAME>` (falls back to `LLM_MODEL`, then a built-in default)

where `<NAME>` is the provider name upper-cased (e.g. `LLM_API_KEY_OPENROUTER`).
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod

import httpx

DEFAULT_TIMEOUT_SECONDS = 30.0


class LLMProviderError(Exception):
    """Raised whenever a provider cannot produce a completion."""


class LLMQuotaExceededError(LLMProviderError):
    """Raised when a provider reports rate-limit/quota exhaustion (HTTP 429
    or an equivalent "insufficient credits" style error). Distinct from
    other provider errors so callers can fail over to the next configured
    provider instead of retrying a provider that is out of tokens."""


class LLMProvider(ABC):
    """Abstract chat-completion provider: system+user text in, raw text out."""

    model: str

    @abstractmethod
    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """Return the raw text completion for the given prompts.

        Must raise LLMQuotaExceededError on rate-limit/quota exhaustion,
        or LLMProviderError for any other failure — never a
        provider-specific exception.
        """
        raise NotImplementedError


_QUOTA_STATUS_CODES = {429}
_QUOTA_KEYWORDS = ("quota", "insufficient", "rate limit", "rate_limit", "credit")


def _looks_like_quota_error(status_code: int, body_text: str) -> bool:
    if status_code in _QUOTA_STATUS_CODES:
        return True
    lowered = body_text.lower()
    return any(keyword in lowered for keyword in _QUOTA_KEYWORDS)


class AnthropicProvider(LLMProvider):
    """Anthropic's native Messages API (not OpenAI-compatible)."""

    API_URL = "https://api.anthropic.com/v1/messages"
    ANTHROPIC_VERSION = "2023-06-01"

    def __init__(self, api_key: str, model: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self._api_key = api_key
        self.model = model
        self._timeout = timeout

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        try:
            response = httpx.post(
                self.API_URL,
                headers={
                    "x-api-key": self._api_key,
                    "anthropic-version": self.ANTHROPIC_VERSION,
                    "content-type": "application/json",
                },
                json={
                    "model": self.model,
                    "max_tokens": 1024,
                    "system": system_prompt,
                    "messages": [{"role": "user", "content": user_prompt}],
                },
                timeout=self._timeout,
            )
            response.raise_for_status()
            body = response.json()
            return "".join(
                block.get("text", "") for block in body.get("content", []) if block.get("type") == "text"
            )
        except httpx.HTTPStatusError as exc:
            text = exc.response.text[:500]
            if _looks_like_quota_error(exc.response.status_code, text):
                raise LLMQuotaExceededError(f"Anthropic API quota/rate-limit hit: {text}") from exc
            raise LLMProviderError(f"Anthropic API returned {exc.response.status_code}: {text}") from exc
        except httpx.HTTPError as exc:
            raise LLMProviderError(f"Anthropic API request failed: {exc}") from exc
        except (KeyError, ValueError, TypeError) as exc:
            raise LLMProviderError(f"Anthropic API returned an unparseable response: {exc}") from exc


class OpenAICompatibleProvider(LLMProvider):
    """Any provider exposing an OpenAI-style POST {base_url}/chat/completions
    endpoint with Bearer auth — covers OpenAI itself, OpenRouter, NaraRouter,
    ExperimentalLab, and similar routers/aggregators."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._api_key = api_key
        self.model = model
        self._timeout = timeout

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        try:
            response = httpx.post(
                self._url,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0,
                },
                timeout=self._timeout,
            )
            response.raise_for_status()
            body = response.json()
            return body["choices"][0]["message"]["content"]
        except httpx.HTTPStatusError as exc:
            text = exc.response.text[:500]
            if _looks_like_quota_error(exc.response.status_code, text):
                raise LLMQuotaExceededError(f"{self._url} quota/rate-limit hit: {text}") from exc
            raise LLMProviderError(f"{self._url} returned {exc.response.status_code}: {text}") from exc
        except httpx.HTTPError as exc:
            raise LLMProviderError(f"{self._url} request failed: {exc}") from exc
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise LLMProviderError(f"{self._url} returned an unparseable response: {exc}") from exc


# Registry of supported provider names -> (base_url or None for Anthropic's
# native API, ordered default free-model list). base_url=None means "use
# AnthropicProvider". The default lists below were built by querying each
# provider's live /v1/models endpoint and verifying each entry actually
# answers a chat-completions call at zero account balance — not guessed.
#
# Deliberately excluded from the NaraRouter/ExperimentalLab lists: any
# `claude-*` aliased model id. Both catalogs list ids that exactly match
# current Anthropic model names (claude-opus-5, claude-sonnet-5, etc.)
# despite neither service being a known official Anthropic reseller, and
# neither exposes per-model pricing to verify the claim. That naming
# collision doesn't prove anything is wrong, but it can't be verified
# either, so GridWise never auto-selects those aliases as defaults. Use
# the real `anthropic` provider entry (native API, official keys) for
# actual Anthropic models.
_PROVIDER_REGISTRY: dict[str, tuple[str | None, list[str]]] = {
    "anthropic": (None, ["claude-sonnet-5"]),
    "openai": ("https://api.openai.com/v1", ["gpt-4o-mini"]),
    "openrouter": (
        "https://openrouter.ai/api/v1",
        [
            "google/gemma-4-31b-it:free",
            "deepseek/deepseek-v4-flash-0731:free",
            "nvidia/nemotron-3-ultra-550b-a55b:free",
            "nvidia/nemotron-3-super-120b-a12b:free",
            "z-ai/glm-5.2:free",
            "qwen/qwen3.8-27b:free",
            "inclusionai/ling-3.0-flash-vl:free",
            "nex-agi/nex-n2.5-mini:free",
            "nex-agi/nex-n2.5-pro:free",
            "inclusionai/ling-3.0-flash-sante:free",
            "inclusionai/ling-3.0-flash-fin:free",
            "dots-studio/dots-3-note-preview:free",
            "liquid/lfm-2.5-2.6b:free",
            "nvidia/nemotron-3.5-lightning:free",
            "thinkingmachines/inkling-small:free",
            "poolside/laguna-s-2.1:free",
            "thinkingmachines/inkling:free",
            "poolside/laguna-xs-2.1:free",
            "cohere/north-mini-code:free",
            "google/gemma-4-26b-a4b-it:free",
            "openrouter/free",
        ],
    ),
    "nararouter": (
        "https://router.bynara.id/v1",
        [
            "agnes-2.5-flash",
            "ling-3.0-flash-fin-free",
            "ling-3.0-flash-sante-free",
            "ling-3.0-flash-vl-free",
            "nemotron-3-ultra-free",
            "nemotron-3.5-lightning-free",
            "nemotron-3-super-free",
        ],
    ),
    "experimentallab": (
        "https://api.experientiallabs.ai/v1",
        [
            "gpt-5.6-luna",
            "deepseek-v4-flash",
            "deepseek-v4.1-flash",
            "qwen3.8-27b",
        ],
    ),
}


def _build_provider(name: str, api_key: str, model: str) -> LLMProvider:
    base_url, _default_models = _PROVIDER_REGISTRY[name]
    if base_url is None:
        return AnthropicProvider(api_key=api_key, model=model)
    return OpenAICompatibleProvider(base_url=base_url, api_key=api_key, model=model)


def _resolve_provider_names() -> list[str]:
    raw_chain = os.getenv("LLM_PROVIDERS", "").strip()
    if raw_chain:
        names = [n.strip().lower() for n in raw_chain.split(",") if n.strip()]
    else:
        single = os.getenv("LLM_PROVIDER", "").strip().lower()
        names = [single] if single else []
    return names


def _resolve_models_for_provider(name: str, single_provider: bool) -> list[str]:
    """Resolve the ordered list of models to try for one provider.

    Priority: LLM_MODELS_<NAME> (explicit comma-separated list) > the
    single-provider-only LLM_MODEL/LLM_MODEL_<NAME> > the built-in
    curated default free-model list for that provider. An explicit
    single-model override always wins over the default list, so setting
    LLM_MODEL_<NAME> pins GridWise to exactly that model instead of
    rotating through every known-free one.
    """
    upper = name.upper()

    explicit_list = os.getenv(f"LLM_MODELS_{upper}", "").strip()
    if explicit_list:
        return [m.strip() for m in explicit_list.split(",") if m.strip()]

    explicit_single = os.getenv(f"LLM_MODEL_{upper}", "").strip()
    if not explicit_single and single_provider:
        explicit_single = os.getenv("LLM_MODEL", "").strip()
    if explicit_single:
        return [explicit_single]

    return list(_PROVIDER_REGISTRY[name][1])


def get_provider_chain() -> list[tuple[str, LLMProvider]]:
    """Build the ordered list of (provider_name, provider) to try in turn.

    Reads LLM_PROVIDERS (preferred, comma-separated priority list) or the
    single-provider LLM_PROVIDER for backward compatibility. For each
    provider, every configured model becomes its own chain entry (in
    order), so a quota/rate-limit failure on one model automatically
    rotates to the next model on the same provider before falling
    through to the next provider. Per-provider keys come from
    LLM_API_KEY_<NAME>, falling back to the unsuffixed LLM_API_KEY when
    only one provider is configured. Providers missing an API key are
    skipped (not fatal) so a partially-configured chain still works.

    Raises LLMProviderError only if the resulting chain is empty (nothing
    usable configured at all), so callers can fail safe.
    """
    names = _resolve_provider_names()
    if not names:
        raise LLMProviderError(
            "No LLM provider configured (set LLM_PROVIDERS or LLM_PROVIDER)"
        )

    single_provider = len(names) == 1
    chain: list[tuple[str, LLMProvider]] = []
    skipped: list[str] = []

    for name in names:
        if name not in _PROVIDER_REGISTRY:
            supported = ", ".join(sorted(_PROVIDER_REGISTRY))
            skipped.append(f"{name} (unsupported; supported: {supported})")
            continue

        upper = name.upper()
        api_key = os.getenv(f"LLM_API_KEY_{upper}", "").strip()
        if not api_key and single_provider:
            api_key = os.getenv("LLM_API_KEY", "").strip()
        if not api_key:
            skipped.append(f"{name} (no LLM_API_KEY_{upper} set)")
            continue

        models = _resolve_models_for_provider(name, single_provider)
        for model in models:
            chain.append((name, _build_provider(name, api_key, model)))

    if not chain:
        detail = f" ({'; '.join(skipped)})" if skipped else ""
        raise LLMProviderError(f"No usable LLM provider in configured chain{detail}")

    return chain
