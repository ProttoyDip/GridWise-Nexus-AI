"""GridWise model registry: measured defaults with curated fallback priors.

When default_models.json contains an eligible benchmark selection, role
selectors prefer it and populate ModelSpec scores from measured JSON/full
accuracy and median latency. Otherwise the hand-picked priors below apply.
The following historical rationale describes those fallback priors only.

The 32-entry provider/model chain in `app.llm.provider` (built from
LLM_PROVIDERS/LLM_MODELS_<NAME> env config) is a *resilience* mechanism —
every known-free model, tried in discovery order, purely to survive
quota exhaustion. It is not a *quality* mechanism: nothing in it
distinguishes a model well-suited to precise structured extraction from
a tiny model, a code-specialist, or a model likely to be slow.

This module is the quality layer on top of that: a small, hand-picked
set of models grouped by the role they should play in interpretation
(primary interpreter, low-latency first opinion, high-reasoning
arbiter for disagreements), each carrying metadata to support future
consensus/latency-aware routing (see the architecture plan this module
implements the first piece of).

IMPORTANT — score honesty: `json_reliability_score`, `reasoning_score`,
and `expected_latency` below are qualitative *heuristic* estimates
based on each model's known size/family class (e.g. "flash"/"mini"
variants trade reasoning depth for speed; larger "ultra"/"super"
variants trade speed for reasoning depth). They are not derived from
benchmarking GridWise's actual interpretation task against these
models — only basic "does it answer at all" connectivity was verified
live (see conversation history). Treat them as an initial, adjustable
prior, not ground truth; recalibrate once real accuracy/latency data
from production traffic is available.

This module does not replace `app.llm.provider.get_provider_chain()` —
that full fallback chain remains available and unchanged. Callers that
want the curated selection should use `get_primary_model()` /
`get_fast_model()` / `get_arbiter_model()`, and fall back to the full
chain (or to `None`-handling) when a curated pick isn't usable, e.g.
because its provider has no configured API key.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from app.llm.provider import (
    LLMProvider,
    LLMProviderError,
    _build_provider,
    get_provider_chain,
)


@dataclass(frozen=True)
class ModelSpec:
    """One curated model and its role-selection metadata.

    Attributes:
        name: The exact model id string passed to the provider's API
            (matches what `app.llm.provider._PROVIDER_REGISTRY` would
            accept for `provider`).
        provider: Provider name key (e.g. "openrouter", "experimentallab"),
            matching `app.llm.provider._PROVIDER_REGISTRY`.
        priority: Lower tries first within its category. Ties broken by
            list order.
        expected_latency: Heuristic estimate, in seconds, of typical
            time-to-completion for a short interpretation prompt. Not
            measured — see module docstring.
        json_reliability_score: Heuristic 0.0-1.0 estimate of how
            reliably the model returns strict, parseable JSON when
            instructed to. Not measured — see module docstring.
        reasoning_score: Heuristic 0.0-1.0 estimate of the model's
            reliability on the small arithmetic/normalization judgment
            calls GridWise needs (percent-to-factor, time-window-to-hour,
            percent-of-capacity-to-kWh). Not measured — see module
            docstring.
    """

    name: str
    provider: str
    priority: int
    expected_latency: float
    json_reliability_score: float
    reasoning_score: float


# Primary interpreters: the default first-choice models for actually
# interpreting an operator note. Chosen for a fast/reliable-JSON/
# decent-arithmetic balance — mid-size "flash" class general models,
# not the smallest or the largest available.
PRIMARY_INTERPRETERS: list[ModelSpec] = [
    ModelSpec(
        name="deepseek/deepseek-v4-flash-0731:free",
        provider="openrouter",
        priority=1,
        expected_latency=2.5,
        json_reliability_score=0.90,
        reasoning_score=0.75,
    ),
    ModelSpec(
        name="deepseek-v4-flash",
        provider="experimentallab",
        priority=2,
        expected_latency=2.0,
        json_reliability_score=0.88,
        reasoning_score=0.72,
    ),
    ModelSpec(
        name="google/gemma-4-31b-it:free",
        provider="openrouter",
        priority=3,
        expected_latency=3.0,
        json_reliability_score=0.85,
        reasoning_score=0.78,
    ),
    ModelSpec(
        name="qwen/qwen3.8-27b:free",
        provider="openrouter",
        priority=4,
        expected_latency=3.5,
        json_reliability_score=0.82,
        reasoning_score=0.80,
    ),
]

# Arbiter models: used only to break ties/disagreements between primary
# interpreters (e.g. in a consensus layer), not as a default first hop.
# Larger/stronger, higher expected latency is an acceptable trade since
# they are called rarely, on disagreement only.
ARBITER_MODELS: list[ModelSpec] = [
    ModelSpec(
        name="nvidia/nemotron-3-ultra-550b-a55b:free",
        provider="openrouter",
        priority=1,
        expected_latency=6.0,
        json_reliability_score=0.85,
        reasoning_score=0.95,
    ),
    ModelSpec(
        name="gpt-5.6-luna",
        provider="experimentallab",
        priority=2,
        expected_latency=4.0,
        json_reliability_score=0.90,
        reasoning_score=0.90,
    ),
]

# Fast models: lowest-latency first opinion, e.g. for a cheap spot-check
# vote in a consensus scheme. Smaller model, correspondingly lower
# reasoning/JSON reliability priors — not suitable as a sole source of
# truth for numeric-bearing directives.
FAST_MODELS: list[ModelSpec] = [
    ModelSpec(
        name="liquid/lfm-2.5-2.6b:free",
        provider="openrouter",
        priority=1,
        expected_latency=1.0,
        json_reliability_score=0.65,
        reasoning_score=0.55,
    ),
]


def _configured_provider_names() -> set[str]:
    """Provider names that currently have a usable entry in the full
    fallback chain (i.e. an API key is actually configured for them).

    Reuses `get_provider_chain()` rather than re-reading environment
    variables directly, so this module never drifts out of sync with
    how `app.llm.provider` actually resolves configuration.
    """
    try:
        chain = get_provider_chain()
    except LLMProviderError:
        return set()
    return {name for name, _ in chain}


def _select(category: list[ModelSpec], require_configured: bool) -> ModelSpec | None:
    from app.llm.measured_defaults import measured_role
    from app.llm.provider import _PROVIDER_REGISTRY

    role = "primary" if category is PRIMARY_INTERPRETERS else "fast" if category is FAST_MODELS else "arbiter"
    measured = measured_role(role)
    if measured and measured["provider"] in _PROVIDER_REGISTRY and measured["name"] in _PROVIDER_REGISTRY[measured["provider"]][1]:
        available = not require_configured or any(
            name == measured["provider"] and provider.model == measured["name"]
            for name, provider in _available_chain()
        )
        if available:
            metrics = measured["metrics"]
            return ModelSpec(measured["name"], measured["provider"], 0,
                             metrics["median_latency_ms"] / 1000,
                             metrics["valid_json_rate"], metrics["fully_correct_rate"])
    ordered = sorted(category, key=lambda spec: spec.priority)
    if not ordered:
        return None
    if not require_configured:
        return ordered[0]

    configured = _configured_provider_names()
    for spec in ordered:
        if spec.provider in configured:
            return spec
    return None


def _available_chain():
    try:
        return get_provider_chain()
    except LLMProviderError:
        return []


def get_primary_model(require_configured: bool = True) -> ModelSpec | None:
    """Return the highest-priority primary interpreter model.

    With `require_configured=True` (default), only considers providers
    that currently have an API key configured (per
    `app.llm.provider.get_provider_chain()`), skipping down the priority
    list otherwise. Returns None if no primary interpreter's provider is
    configured — callers should fall back to the full chain
    (`app.llm.provider.get_provider_chain()`) in that case rather than
    fail, per GridWise's fail-safe design.
    """
    return _select(PRIMARY_INTERPRETERS, require_configured)


def get_fast_model(require_configured: bool = True) -> ModelSpec | None:
    """Return the highest-priority fast/low-latency model. See
    `get_primary_model` for `require_configured` semantics and the
    fall-back-to-full-chain contract on a None result."""
    return _select(FAST_MODELS, require_configured)


def get_arbiter_model(require_configured: bool = True) -> ModelSpec | None:
    """Return the highest-priority arbiter (disagreement tie-break)
    model. See `get_primary_model` for `require_configured` semantics
    and the fall-back-to-full-chain contract on a None result."""
    return _select(ARBITER_MODELS, require_configured)


def build_provider(spec: ModelSpec) -> LLMProvider:
    """Instantiate an LLMProvider for a curated ModelSpec.

    Resolves the API key the same way `app.llm.provider.get_provider_chain`
    does (LLM_API_KEY_<PROVIDER>, falling back to the unsuffixed
    LLM_API_KEY), so a ModelSpec plugs directly into the same provider
    machinery used by the full fallback chain. Raises LLMProviderError if
    no key is configured for the spec's provider — callers should treat
    this the same as any other provider failure (retry, fail over, or
    fail safe to no_op), never let it propagate raw.
    """
    upper = spec.provider.upper()
    api_key = os.getenv(f"LLM_API_KEY_{upper}", "").strip() or os.getenv("LLM_API_KEY", "").strip()
    if not api_key:
        raise LLMProviderError(f"No API key configured for provider '{spec.provider}' (LLM_API_KEY_{upper})")
    return _build_provider(spec.provider, api_key, spec.name)
