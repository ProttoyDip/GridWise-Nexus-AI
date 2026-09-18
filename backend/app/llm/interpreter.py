"""LLM-assisted operator-note interpretation.

Converts each operator note into a single `DirectiveInterpretation`
(one of the allowed directive types, or no_op) by prompting the
configured LLM provider chain one note at a time. The model's raw
output is untrusted: it is strictly JSON-parsed and validated against
the `DirectiveInterpretation` schema (app.models.response), retried a
bounded number of times per provider on transient/parse failures, and
fails over to the next configured provider in the chain when the
current one reports quota/rate-limit exhaustion. If every provider is
unusable or unconfigured, each note falls back to a safe `no_op` entry.
This module never raises to its caller for LLM-side failures — every
note always yields exactly one DirectiveInterpretation.

Deterministic semantic checks beyond schema validation (numeric bounds,
directive-type-specific field rules, cross-note consistency) belong to
the guardrails layer, not here. The API passes its results through
guardrails before optimization.
"""

from __future__ import annotations

import json
import logging
import time

from pydantic import ValidationError

from app.llm.prompts import SYSTEM_PROMPT, build_user_prompt
from app.llm.provider import (
    LLMProvider,
    LLMProviderError,
    LLMQuotaExceededError,
    get_provider_chain,
)
from app.models.request import BatteryConfig, HourEntry
from app.models.response import DirectiveInterpretation

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 1.0


def _fallback_no_op(note_index: int, reason: str) -> DirectiveInterpretation:
    return DirectiveInterpretation(
        note_index=note_index,
        applies=False,
        directive_type="no_op",
        structured_adjustment=None,
        explanation=f"Falling back to no_op: {reason}",
    )


def _extract_json_object(text: str) -> dict:
    """Pull a single JSON object out of raw model output.

    Tolerates markdown code fences and incidental leading/trailing text
    around the object, since models occasionally wrap JSON even when
    told not to.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:]
        stripped = stripped.strip()

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object found in model output")

    parsed = json.loads(stripped[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("model output did not decode to a JSON object")
    return parsed


def _call_and_parse(
    provider: LLMProvider, system_prompt: str, user_prompt: str, note_index: int
) -> DirectiveInterpretation:
    raw_output = provider.complete(system_prompt, user_prompt)
    parsed = _extract_json_object(raw_output)
    # note_index is assigned deterministically by the caller, not trusted
    # from the model, so ordering/uniqueness is always guaranteed.
    parsed["note_index"] = note_index
    return DirectiveInterpretation.model_validate(parsed)


def _interpret_single_note(
    provider_chain: list[tuple[str, LLMProvider]],
    note: str,
    note_index: int,
    hours: list[HourEntry],
    battery: BatteryConfig,
) -> DirectiveInterpretation:
    user_prompt = build_user_prompt(note=note, note_index=note_index, hours=hours, battery=battery)

    last_error: Exception | None = None
    for provider_name, provider in provider_chain:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                return _call_and_parse(provider, SYSTEM_PROMPT, user_prompt, note_index)
            except LLMQuotaExceededError as exc:
                last_error = exc
                logger.warning(
                    "Provider '%s' out of quota on note %d, failing over to next provider: %s",
                    provider_name,
                    note_index,
                    exc,
                )
                break  # don't retry a provider that is out of tokens; move to next provider
            except LLMProviderError as exc:
                last_error = exc
                logger.warning(
                    "Provider '%s' error on note %d attempt %d/%d: %s",
                    provider_name,
                    note_index,
                    attempt,
                    MAX_ATTEMPTS,
                    exc,
                )
            except (ValueError, TypeError, json.JSONDecodeError, ValidationError) as exc:
                last_error = exc
                logger.warning(
                    "Malformed output from '%s' for note %d attempt %d/%d: %s",
                    provider_name,
                    note_index,
                    attempt,
                    MAX_ATTEMPTS,
                    exc,
                )

            if attempt < MAX_ATTEMPTS:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)

    return _fallback_no_op(
        note_index, f"all configured LLM providers failed or ran out of quota ({last_error})"
    )


def interpret_operator_notes(
    operator_notes: list[str],
    hours: list[HourEntry],
    battery: BatteryConfig,
) -> list[DirectiveInterpretation]:
    """Interpret every operator note against the configured provider chain.

    Always returns exactly one DirectiveInterpretation per input note, in
    order. If no provider chain can be built at all (nothing configured),
    every note falls back to no_op rather than raising.
    """
    try:
        provider_chain = get_provider_chain()
    except LLMProviderError as exc:
        logger.warning("No usable LLM provider, falling back to no_op for all notes: %s", exc)
        return [_fallback_no_op(index, str(exc)) for index in range(len(operator_notes))]

    return [
        _interpret_single_note(provider_chain, note, index, hours, battery)
        for index, note in enumerate(operator_notes)
    ]
