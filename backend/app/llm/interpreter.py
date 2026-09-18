"""LLM-assisted operator-note interpretation.

Converts each operator note into a single `DirectiveInterpretation`
(one of the allowed directive types, or no_op) by prompting the
configured LLM provider chain. The model's raw output is untrusted: it
is strictly JSON-parsed and validated against the
`DirectiveInterpretation` schema and deterministic guardrails, retried a
bounded number of times per provider on transient/parse failures, and
fails over to the next configured provider in the chain when the
current one reports quota/rate-limit exhaustion. If every provider is
unusable or unconfigured, each note falls back to a safe `no_op` entry.
This module never raises to its caller for LLM-side failures — every
note always yields exactly one DirectiveInterpretation.

Confidence is tracked only on private internal metadata. A single valid
model result requests verification by the next distinct provider/model;
disagreement escalates through additional configured models. A strict
majority with at least two agreeing models is accepted. An unresolved
disagreement falls back to no_op. With only one valid result available,
that guarded result remains marked verify. Every API schedule still passes
the independent final verifier regardless of interpretation confidence.

Final guarded interpretations are cached by note and canonical scenario
context. TTL expiry or a context change causes fresh interpretation;
failure fallbacks are not cached. Stored values retain private confidence
evidence but never credentials or provider objects.

Notes are interpreted concurrently: each note's retry/failover state
is independent. Identical note/context pairs share one in-flight cache
computation, while different contexts run in parallel via `asyncio.gather` over
`asyncio.to_thread`-wrapped calls to the single-note worker rather than
a sequential loop. `asyncio.gather` returns results in the same order
its awaitables were given, regardless of which one finishes first, so
the returned list stays in note_index order even though completion
order is not guaranteed — this is on top of, not instead of,
`_call_and_parse` deterministically overwriting `note_index` itself.
Threads (not a fully async provider layer) are used deliberately: the
provider HTTP calls and retry backoff (`time.sleep`) are blocking, and
Python threads release the GIL during blocking I/O, giving real
wall-clock concurrency without rewriting `LLMProvider` to an async
interface.

Two entrypoints are exposed: `interpret_operator_notes` (sync — the
default; internally runs the async version via `asyncio.run`, so every
existing caller, including sync FastAPI routes and tests that
monkeypatch this function with a plain sync callable, keeps working
unchanged) and `interpret_operator_notes_async` (a real coroutine, for
callers that already run inside an event loop — never call the sync
wrapper from there, since `asyncio.run` cannot nest inside a running
loop).

Deterministic semantic checks beyond schema validation (numeric bounds,
directive-type-specific field rules, cross-note consistency) belong to
the guardrails layer, not here. The API passes its results through
guardrails before optimization.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

from pydantic import ValidationError

from app.guardrails.validator import validate_directive
from app.guardrails.physics_validator import correct_physical_values, validate_physical_directive
from app.llm import circuit_breaker
from app.llm.cache import build_directive_context, interpretation_cache, interpretation_cache_key
from app.llm.circuit_breaker import FailureKind
from app.llm.confidence import ConfidenceDecision, assess_confidence
from app.llm.prompts import SYSTEM_PROMPT, build_repair_prompt, build_user_prompt
from app.llm.json_parser import LLMOutputError, parse_json_object
from app.llm.output_schema import directive_json_schema
from app.llm.provider import (
    LLMProvider,
    LLMProviderError,
    LLMQuotaExceededError,
    LLMServerError,
    LLMTimeoutError,
    StructuredOutputUnsupported,
    get_provider_chain,
)
from app.models.request import BatteryConfig, HourEntry
from app.models.response import DirectiveInterpretation

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3

# Failure-specific retry strategy (app.llm.interpreter._interpret_single_note
# and the analogous app.llm.consensus._call_chain apply these per the
# exception kind actually raised, rather than one uniform policy):
#   - JSON parsing/validation failure: no sleep at all, retry immediately
#     with a correction ("repair") prompt built from the failed output —
#     the problem is prompt adherence, not transient load, so waiting
#     buys nothing and a corrected prompt is strictly more useful than an
#     identical retry.
#   - 429 (LLMQuotaExceededError): exponential backoff on the same
#     provider/model before giving up on it (1s, 2s, 4s, ...) — quota
#     resets are time-based, so a growing wait is the correct response,
#     but it still eventually falls through to the next chain entry once
#     MAX_ATTEMPTS is exhausted.
#   - Timeout (LLMTimeoutError): no retry at all — move to the next
#     model immediately. A provider that's already hanging is unlikely
#     to suddenly become fast, and free-tier chains have plenty of other
#     models to try faster than waiting out a slow one.
#   - 5xx (LLMServerError): a short, fixed retry — upstream 5xx blips are
#     often transient and clear within a second, but unlike 429 there's
#     no reason to believe backing off longer helps, so the delay stays
#     flat rather than growing.
#   - Any other LLMProviderError (e.g. a permanent 4xx config error):
#     falls back to the original flat linear backoff, unchanged.
RETRY_BACKOFF_SECONDS = 1.0
EXPONENTIAL_BACKOFF_BASE_SECONDS = 1.0
SHORT_RETRY_BACKOFF_SECONDS = 0.5

# Hard wall-clock budget for a single note's entire chain walk. Without
# this, a note that has to cascade through many rate-limited/hanging
# free-tier models (observed live: 81s-114s for a single note) can run
# well past a typical reverse-proxy/gateway timeout (often 30-60s),
# turning a slow-but-eventually-successful request into a client-visible
# 502/504 the server never even knows happened. Once the budget is spent,
# the chain walk stops starting new provider attempts and falls through
# to the same safe verify/no_op fallback used when providers are exhausted
# — never a partial/inconsistent result.
MAX_TOTAL_SECONDS_PER_NOTE = 25.0


def _fallback_no_op(note_index: int, reason: str) -> DirectiveInterpretation:
    return DirectiveInterpretation(
        note_index=note_index,
        applies=False,
        directive_type="no_op",
        structured_adjustment=None,
        explanation=f"Falling back to no_op: {reason}",
    )


def _extract_json_object(text: str) -> dict:
    """Compatibility entrypoint for decoder extraction and balanced fallback."""
    return parse_json_object(text)


def _call_and_parse(
    provider: LLMProvider, system_prompt: str, user_prompt: str, note_index: int
) -> DirectiveInterpretation:
    if getattr(provider, "supports_structured_output", False):
        try:
            raw_output = provider.complete_structured(system_prompt, user_prompt, directive_json_schema())
        except StructuredOutputUnsupported:
            raw_output = provider.complete(system_prompt, user_prompt)
    else:
        raw_output = provider.complete(system_prompt, user_prompt)
    # note_index is assigned deterministically by the caller, not trusted
    # from the model, so ordering/uniqueness is always guaranteed.
    try:
        parsed = dict(raw_output) if isinstance(raw_output, dict) else _extract_json_object(raw_output)
        parsed["note_index"] = note_index
        parsed = correct_physical_values(parsed)
        return validate_directive(parsed, note_index)
    except (ValueError, TypeError, RecursionError) as exc:
        raise LLMOutputError(raw_output, str(exc)) from exc


def _interpret_single_note(
    provider_chain: list[tuple[str, LLMProvider]],
    note: str,
    note_index: int,
    hours: list[HourEntry],
    battery: BatteryConfig,
) -> DirectiveInterpretation:
    """Reuse final guarded interpretations; assign each caller's note index."""
    context = build_directive_context(hours, battery, [
        (name, getattr(provider, "model", name)) for name, provider in provider_chain
    ])
    key = interpretation_cache_key(note, context)
    result = interpretation_cache.get_or_compute(
        key, lambda: _interpret_single_note_uncached(provider_chain, note, note_index, hours, battery),
    )
    result.note_index = note_index
    result = validate_directive(result, note_index)
    try:
        from app.memory import store
        result = store.directive_memory.boost(note, result)
    except Exception:
        pass
    return result


def _interpret_single_note_uncached(
    provider_chain: list[tuple[str, LLMProvider]],
    note: str,
    note_index: int,
    hours: list[HourEntry],
    battery: BatteryConfig,
) -> DirectiveInterpretation:
    user_prompt = build_user_prompt(note=note, note_index=note_index, hours=hours, battery=battery)

    # Skip models whose circuit is already open (repeated recent 429/timeout/5xx)
    # rather than paying a doomed request; falls back to the unfiltered chain
    # if literally everything is currently open (see circuit_breaker.filter_chain).
    provider_chain = circuit_breaker.filter_chain(provider_chain)

    deadline = time.monotonic() + MAX_TOTAL_SECONDS_PER_NOTE
    last_error: Exception | None = None
    votes: list[tuple[str, DirectiveInterpretation]] = []
    models_used: list[str] = []
    for provider_name, provider in provider_chain:
        if time.monotonic() >= deadline:
            last_error = last_error or LLMProviderError(
                f"note {note_index} exceeded its {MAX_TOTAL_SECONDS_PER_NOTE}s interpretation budget"
            )
            break
        model = getattr(provider, "model", provider_name)
        model_id = f"{provider_name}/{model}"
        if model_id in models_used:
            continue
        models_used.append(model_id)
        attempt_prompt = user_prompt
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                candidate = _call_and_parse(provider, SYSTEM_PROMPT, attempt_prompt, note_index)
                candidate = validate_physical_directive(candidate, battery, note_index)
                circuit_breaker.record_success(provider_name, model)
                votes.append((model_id, candidate))
                winner, metadata = assess_confidence(votes, models_used)
                if metadata.decision == ConfidenceDecision.ACCEPT:
                    winner._confidence_metadata = metadata
                    return winner
                # Verify a single result or escalate disagreements using the
                # next distinct model. A valid response is never retried to
                # manufacture extra agreement from the same model.
                break
            except LLMQuotaExceededError as exc:
                # 429: exponential backoff, retrying the same model before
                # eventually falling through to the next chain entry.
                last_error = exc
                circuit_breaker.record_failure(provider_name, model, FailureKind.RATE_LIMIT_429)
                logger.warning(
                    "Provider '%s' quota/429 on note %d attempt %d/%d, exponential backoff: %s",
                    provider_name,
                    note_index,
                    attempt,
                    MAX_ATTEMPTS,
                    exc,
                )
                if attempt < MAX_ATTEMPTS and time.monotonic() < deadline:
                    time.sleep(EXPONENTIAL_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
                continue
            except LLMTimeoutError as exc:
                # Timeout: no retry on this model at all — move to the next
                # model immediately, a hanging provider won't suddenly speed up.
                last_error = exc
                circuit_breaker.record_failure(provider_name, model, FailureKind.TIMEOUT)
                logger.warning(
                    "Provider '%s' timed out on note %d, moving to next model immediately: %s",
                    provider_name,
                    note_index,
                    exc,
                )
                break
            except LLMServerError as exc:
                # 5xx: short, flat retry (transient upstream blip, not worth
                # a growing backoff).
                last_error = exc
                circuit_breaker.record_failure(provider_name, model, FailureKind.SERVER_ERROR_5XX)
                logger.warning(
                    "Provider '%s' returned a server error on note %d attempt %d/%d, short retry: %s",
                    provider_name,
                    note_index,
                    attempt,
                    MAX_ATTEMPTS,
                    exc,
                )
                if attempt < MAX_ATTEMPTS and time.monotonic() < deadline:
                    time.sleep(SHORT_RETRY_BACKOFF_SECONDS)
                continue
            except LLMProviderError as exc:
                # Any other provider-side failure (e.g. a permanent 4xx
                # configuration error): unchanged flat linear backoff. Not
                # recorded in the circuit breaker — see FailureKind.OTHER.
                last_error = exc
                logger.warning(
                    "Provider '%s' error on note %d attempt %d/%d: %s",
                    provider_name,
                    note_index,
                    attempt,
                    MAX_ATTEMPTS,
                    exc,
                )
                if attempt < MAX_ATTEMPTS and time.monotonic() < deadline:
                    time.sleep(RETRY_BACKOFF_SECONDS * attempt)
                continue
            except (ValueError, TypeError, json.JSONDecodeError, ValidationError) as exc:
                # JSON parsing/validation failure: no sleep at all, retry
                # immediately with a correction ("repair") prompt.
                last_error = exc
                if isinstance(exc, LLMOutputError):
                    attempt_prompt = build_repair_prompt(user_prompt, exc.raw_output, str(exc))
                logger.warning(
                    "Malformed output from '%s' for note %d attempt %d/%d, retrying with repair prompt: %s",
                    provider_name,
                    note_index,
                    attempt,
                    MAX_ATTEMPTS,
                    exc,
                )
                continue

    winner, metadata = assess_confidence(votes, models_used)
    if metadata.decision == ConfidenceDecision.VERIFY:
        # No further independent models are available. The candidate has
        # passed strict deterministic guardrails; API schedule verification
        # remains mandatory. Keep the evidence marked verify, never accept.
        winner._confidence_metadata = metadata
        return winner
    fallback = _fallback_no_op(
        note_index, f"all configured LLM providers failed or ran out of quota ({last_error})"
        if not votes else "model disagreement remained unresolved after escalation"
    )
    fallback._confidence_metadata = metadata
    return fallback


async def interpret_operator_notes_async(
    operator_notes: list[str],
    hours: list[HourEntry],
    battery: BatteryConfig,
) -> list[DirectiveInterpretation]:
    """Interpret every operator note concurrently against the configured
    provider chain.

    Always returns exactly one DirectiveInterpretation per input note, in
    the same order the notes were given — see the module docstring for
    why that ordering guarantee holds even though the notes run in
    parallel and may complete in a different order. If no provider chain
    can be built at all (nothing configured), every note falls back to
    no_op rather than raising, with no network calls attempted.
    """
    try:
        from app.llm.measured_defaults import order_interpretation_chain
        provider_chain = order_interpretation_chain(get_provider_chain())
    except LLMProviderError as exc:
        logger.warning("No usable LLM provider, falling back to no_op for all notes: %s", exc)
        results = [_fallback_no_op(index, str(exc)) for index in range(len(operator_notes))]
        _, metadata = assess_confidence([], [])
        for result in results:
            result._confidence_metadata = metadata
        return results

    tasks = [
        asyncio.to_thread(_interpret_single_note, provider_chain, note, index, hours, battery)
        for index, note in enumerate(operator_notes)
    ]
    return list(await asyncio.gather(*tasks))


def interpret_operator_notes(
    operator_notes: list[str],
    hours: list[HourEntry],
    battery: BatteryConfig,
) -> list[DirectiveInterpretation]:
    """Synchronous entrypoint: interprets every note concurrently (see
    `interpret_operator_notes_async`) but is itself an ordinary blocking
    call, so existing sync callers (FastAPI's sync route handlers,
    tests that monkeypatch this function with a plain sync callable)
    need no changes.

    Do not call this from inside a running asyncio event loop —
    `asyncio.run` cannot nest. Call `interpret_operator_notes_async`
    directly from async code instead.
    """
    return asyncio.run(interpret_operator_notes_async(operator_notes, hours, battery))
