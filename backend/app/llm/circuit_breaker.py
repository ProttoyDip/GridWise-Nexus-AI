"""Per-(provider, model) circuit breaker for GridWise's LLM layer.

The provider/model fallback chain (app.llm.provider.get_provider_chain)
already reacts to a failure *after it happens* — it retries or moves to
the next chain entry. What it does not do is remember: if a given
(provider, model) pair was failing a second ago, every note still has
to pay the cost of trying it again before falling through. This module
adds that memory, in-process, per (provider, model):

- After `FAILURE_THRESHOLD` consecutive counted failures, the circuit
  for that (provider, model) opens, and it should be skipped by callers
  for `COOLDOWN_SECONDS` — no request is even attempted against it.
- After the cooldown elapses, the circuit is implicitly eligible again
  (the next attempt naturally gets a fresh chance — this is a simple
  "cooldown then retry" breaker, not a half-open probe state).
- A successful call resets the failure count to 0, closing the circuit.

Only three failure kinds count toward tripping a circuit, matching the
brief exactly: rate-limit/quota (429), timeout, and upstream server
error (5xx). Everything else (malformed JSON, schema validation
failure, a permanent 4xx config error like an unrecognized model id) is
deliberately NOT counted — those are either about the model's *output
quality* rather than the provider's *operational health*, or are
permanent misconfigurations that a time-based cooldown can't fix
anyway, so counting them would just make the breaker trip on problems
retrying won't solve.

This module is standalone: it does not modify app.llm.provider,
app.llm.interpreter, or app.llm.consensus. It tracks state and answers
"should I skip this (provider, model) right now?" — wiring it into the
actual call sites (e.g. gating app.llm.interpreter._interpret_single_note's
chain walk, or app.llm.provider.get_provider_chain's chain construction)
is a separate, deliberately deferred step, consistent with how
app.llm.model_registry and app.llm.consensus were introduced as
standalone modules first.

Thread-safe: app.llm.interpreter runs notes concurrently via
asyncio.to_thread, so all state access here is guarded by a lock.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING

from app.llm.provider import LLMQuotaExceededError, LLMServerError, LLMTimeoutError

if TYPE_CHECKING:
    from app.llm.provider import LLMProvider

FAILURE_THRESHOLD = 3
COOLDOWN_SECONDS = 60.0


class FailureKind(Enum):
    """The three failure kinds the brief specifies as countable, plus
    OTHER for everything else (never counted — see module docstring)."""

    RATE_LIMIT_429 = auto()
    TIMEOUT = auto()
    SERVER_ERROR_5XX = auto()
    OTHER = auto()


_COUNTED_FAILURE_KINDS = frozenset(
    {FailureKind.RATE_LIMIT_429, FailureKind.TIMEOUT, FailureKind.SERVER_ERROR_5XX}
)

_STATUS_CODE_RE = re.compile(r"returned (\d{3})")


def classify_exception(exc: BaseException) -> FailureKind:
    """Best-effort classification of a provider-layer exception into a
    FailureKind, for callers that want to hand a raw exception straight
    to `record_failure_from_exception` instead of classifying it
    themselves.

    Recognizes `app.llm.provider`'s typed exceptions directly:
    `LLMQuotaExceededError` -> RATE_LIMIT_429, `LLMTimeoutError` -> TIMEOUT,
    `LLMServerError` -> SERVER_ERROR_5XX. For any other exception (e.g. a
    raw httpx exception caught before `app.llm.provider` wrapped it, or a
    generic `LLMProviderError`), falls back to inspecting the message text
    and chained cause (`exc.__cause__`, which `app.llm.provider` sets via
    `raise ... from exc`) for a timeout indicator or an embedded 3-digit
    HTTP status code. That fallback path is best-effort against
    `app.llm.provider`'s current error-message format — a caller that
    already knows the definite classification should call
    `record_failure(..., kind=...)` directly instead of going through
    this heuristic.
    """
    if isinstance(exc, LLMQuotaExceededError):
        return FailureKind.RATE_LIMIT_429
    if isinstance(exc, LLMTimeoutError):
        return FailureKind.TIMEOUT
    if isinstance(exc, LLMServerError):
        return FailureKind.SERVER_ERROR_5XX

    text = str(exc)
    cause = exc.__cause__
    cause_name = type(cause).__name__ if cause is not None else ""

    if "timeout" in text.lower() or "timeout" in cause_name.lower():
        return FailureKind.TIMEOUT

    match = _STATUS_CODE_RE.search(text)
    if match:
        status_code = int(match.group(1))
        if status_code == 429:
            return FailureKind.RATE_LIMIT_429
        if 500 <= status_code <= 599:
            return FailureKind.SERVER_ERROR_5XX

    return FailureKind.OTHER


@dataclass
class CircuitState:
    """Tracked state for one (provider, model) pair."""

    provider: str
    model: str
    failure_count: int = 0
    last_failure_time: float | None = None  # time.time() epoch seconds, or None if never failed


class CircuitBreaker:
    """Thread-safe in-process circuit breaker over (provider, model) pairs.

    A default module-level instance is exposed via the module-level
    functions below (`is_open`, `record_success`, `record_failure`, ...)
    for convenient use without instantiation; construct a private
    `CircuitBreaker()` instead when isolation is needed (e.g. tests).
    """

    def __init__(
        self,
        failure_threshold: int = FAILURE_THRESHOLD,
        cooldown_seconds: float = COOLDOWN_SECONDS,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._lock = threading.Lock()
        self._states: dict[tuple[str, str], CircuitState] = {}

    def _is_open_locked(self, state: CircuitState | None) -> bool:
        if state is None or state.failure_count < self._failure_threshold or state.last_failure_time is None:
            return False
        return (time.time() - state.last_failure_time) < self._cooldown_seconds

    def is_open(self, provider: str, model: str) -> bool:
        """True if this (provider, model) tripped its circuit and is
        still within its cooldown window — callers should skip it."""
        with self._lock:
            return self._is_open_locked(self._states.get((provider, model)))

    # Reads better at call sites deciding whether to attempt a request.
    should_skip = is_open

    def record_success(self, provider: str, model: str) -> None:
        """Reset the failure count on a successful call, closing the circuit."""
        with self._lock:
            state = self._states.get((provider, model))
            if state is not None:
                state.failure_count = 0
                state.last_failure_time = None

    def record_failure(self, provider: str, model: str, kind: FailureKind) -> None:
        """Record a failure for (provider, model). Only RATE_LIMIT_429 /
        TIMEOUT / SERVER_ERROR_5XX actually count toward tripping the
        circuit (see module docstring); other kinds are recorded as a
        no-op so callers don't need to pre-filter before calling."""
        if kind not in _COUNTED_FAILURE_KINDS:
            return
        with self._lock:
            state = self._states.get((provider, model))
            if state is None:
                state = CircuitState(provider=provider, model=model)
                self._states[(provider, model)] = state
            state.failure_count += 1
            state.last_failure_time = time.time()

    def record_failure_from_exception(self, provider: str, model: str, exc: BaseException) -> None:
        """Convenience: classify `exc` via `classify_exception` and record it."""
        self.record_failure(provider, model, classify_exception(exc))

    def get_state(self, provider: str, model: str) -> CircuitState | None:
        """The current tracked state for (provider, model), or None if it
        has never failed. Returns a copy, safe to inspect without a lock."""
        with self._lock:
            state = self._states.get((provider, model))
            return (
                CircuitState(state.provider, state.model, state.failure_count, state.last_failure_time)
                if state is not None
                else None
            )

    def cooldown_remaining_seconds(self, provider: str, model: str) -> float:
        """Seconds left before this (provider, model) is eligible again,
        or 0.0 if it isn't currently open."""
        with self._lock:
            state = self._states.get((provider, model))
            if not self._is_open_locked(state):
                return 0.0
            return max(0.0, self._cooldown_seconds - (time.time() - state.last_failure_time))

    def filter_chain(
        self, chain: list[tuple[str, "LLMProvider"]]
    ) -> list[tuple[str, "LLMProvider"]]:
        """Filter a provider_chain (as built by
        app.llm.provider.get_provider_chain) down to entries whose
        circuit is not currently open, preserving order.

        If every entry is currently open, returns the chain unfiltered
        rather than leaving the caller with nothing to try — when the
        whole chain looks dead, attempting the least-bad entry is more
        useful than refusing to try anything, since the breaker's own
        failure count could be stale (e.g. the upstream outage already
        ended, and only a real request will tell us that).
        """
        with self._lock:
            survivors = [
                (name, provider)
                for name, provider in chain
                if not self._is_open_locked(
                    self._states.get((name, getattr(provider, "model", name)))
                )
            ]
        return survivors if survivors else chain

    def reset(self, provider: str | None = None, model: str | None = None) -> None:
        """Clear tracked state. With no arguments, clears everything
        (primarily useful for tests); with both arguments, clears just
        that one (provider, model) pair."""
        with self._lock:
            if provider is None and model is None:
                self._states.clear()
            else:
                self._states.pop((provider, model), None)

    def snapshot(self) -> list[CircuitState]:
        """All currently tracked states (copies, not live references),
        e.g. for an observability endpoint alongside GET /llm/status."""
        with self._lock:
            return [
                CircuitState(s.provider, s.model, s.failure_count, s.last_failure_time)
                for s in self._states.values()
            ]


# Default shared instance + thin module-level wrappers, matching the
# rest of app.llm's convention of plain functions over classes for the
# common case (see app.llm.provider.get_provider_chain,
# app.llm.model_registry.get_primary_model, etc.).
_default_breaker = CircuitBreaker()


def is_open(provider: str, model: str) -> bool:
    return _default_breaker.is_open(provider, model)


def should_skip(provider: str, model: str) -> bool:
    return _default_breaker.should_skip(provider, model)


def record_success(provider: str, model: str) -> None:
    _default_breaker.record_success(provider, model)


def record_failure(provider: str, model: str, kind: FailureKind) -> None:
    _default_breaker.record_failure(provider, model, kind)


def record_failure_from_exception(provider: str, model: str, exc: BaseException) -> None:
    _default_breaker.record_failure_from_exception(provider, model, exc)


def get_state(provider: str, model: str) -> CircuitState | None:
    return _default_breaker.get_state(provider, model)


def cooldown_remaining_seconds(provider: str, model: str) -> float:
    return _default_breaker.cooldown_remaining_seconds(provider, model)


def filter_chain(chain: list[tuple[str, "LLMProvider"]]) -> list[tuple[str, "LLMProvider"]]:
    return _default_breaker.filter_chain(chain)


def reset(provider: str | None = None, model: str | None = None) -> None:
    _default_breaker.reset(provider, model)


def snapshot() -> list[CircuitState]:
    return _default_breaker.snapshot()
