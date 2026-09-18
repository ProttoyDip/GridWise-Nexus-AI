"""Lightweight internal observability for /optimize-energy requests.

Tracks, per request: request_id, latency, models_used, fallback_count,
consensus_result, optimizer_time, validation_failures. In-process only —
there is no external sink (no log-shipping, no third-party telemetry) —
so nothing recorded here ever leaves this process.

Safety contract (enforced by construction, not by convention alone):
- No API keys: `RequestMetrics` has no field capable of holding one —
  `models_used` is restricted to the same "provider/model" id strings
  already surfaced by GET /llm/status, never a raw provider response or
  credential.
- No sensitive prompts: operator note text, system prompts, and raw LLM
  output are never accepted by `record_request` — only counts and short
  enum-like labels. There is no field to put prompt text in even by
  mistake.
- No raw secrets/error text: failures are recorded as counts
  (`fallback_count`, `validation_failures`), never as exception messages
  or response bodies, which could incidentally embed a URL, key
  fragment, or other operational detail from an upstream provider.

Callers (e.g. app.api.optimize) are expected to wrap `record_request` in
a broad try/except — see that module — so a monitoring bug can never
fail a real request. This module raises only on clearly-programmer-error
misuse (e.g. a negative latency), never on transient issues.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field

MAX_TRACKED_REQUESTS = 1000

# "provider/model" — the same id shape already exposed publicly by
# GET /llm/status, so allowing it here reveals nothing new. Only the
# leading provider segment (a simple registry slug like "openrouter")
# is tightly constrained; the model segment is validated loosely since
# real model ids are themselves often "vendor/model:tag" (OpenRouter's
# convention), so the full id can contain more than one "/".
_PROVIDER_SLUG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_MAX_MODEL_ID_LENGTH = 200


def _validate_model_id(model_id: str) -> str:
    if not isinstance(model_id, str) or "/" not in model_id:
        raise ValueError(
            f"models_used entries must look like 'provider/model', got {model_id!r}"
        )
    provider_part, _, model_part = model_id.partition("/")
    if not _PROVIDER_SLUG_RE.match(provider_part):
        raise ValueError(f"models_used provider segment is invalid: {provider_part!r}")
    if not model_part or len(model_id) > _MAX_MODEL_ID_LENGTH or any(c.isspace() for c in model_part):
        raise ValueError(f"models_used model segment is invalid: {model_part!r}")
    return model_id


@dataclass(frozen=True)
class RequestMetrics:
    """One /optimize-energy request's observability record.

    Attributes:
        request_id: Server-generated identifier for this request
            (e.g. a uuid4), not derived from any client-supplied secret.
        latency_seconds: Total wall-clock time for the request.
        models_used: Distinct "provider/model" ids actually invoked
            (public identifiers only — see module docstring).
        fallback_count: How many directives fell back to no_op because
            every configured LLM provider/model failed or ran out of
            quota (interpretation-side failure, not a validation
            rejection).
        consensus_result: One label per directive describing how it was
            resolved (e.g. "accept", "verify", "escalate" — see
            app.llm.confidence.ConfidenceDecision — or "unknown" if no
            confidence evidence was available for that path).
        optimizer_time_seconds: Wall-clock time spent building the
            hourly schedule (the solver step), a subset of
            latency_seconds.
        validation_failures: How many directives were rejected by the
            deterministic guardrails layer and replaced with a safe
            no_op (a directive-quality failure, distinct from
            fallback_count's provider-availability failure).
        recorded_at: Epoch seconds when this record was created.
    """

    request_id: str
    latency_seconds: float
    models_used: tuple[str, ...]
    fallback_count: int
    consensus_result: tuple[str, ...]
    optimizer_time_seconds: float
    validation_failures: int
    recorded_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if not self.request_id:
            raise ValueError("request_id must be non-empty")
        if self.latency_seconds < 0 or self.optimizer_time_seconds < 0:
            raise ValueError("latency/optimizer time must be non-negative")
        if self.fallback_count < 0 or self.validation_failures < 0:
            raise ValueError("fallback_count/validation_failures must be non-negative")
        for model_id in self.models_used:
            _validate_model_id(model_id)


class MonitoringLog:
    """Thread-safe, bounded, in-process ring buffer of RequestMetrics."""

    def __init__(self, max_tracked_requests: int = MAX_TRACKED_REQUESTS) -> None:
        self._max = max_tracked_requests
        self._lock = threading.Lock()
        self._records: list[RequestMetrics] = []
        self._total_requests = 0
        self._total_fallbacks = 0
        self._total_validation_failures = 0

    def record(self, metrics: RequestMetrics) -> None:
        with self._lock:
            self._records.append(metrics)
            if len(self._records) > self._max:
                self._records.pop(0)
            self._total_requests += 1
            self._total_fallbacks += metrics.fallback_count
            self._total_validation_failures += metrics.validation_failures

    def recent(self, limit: int = 50) -> list[RequestMetrics]:
        with self._lock:
            return list(self._records[-limit:])

    def summary(self) -> dict:
        """Aggregate counters safe to expose (no per-request detail),
        e.g. for a status/observability endpoint."""
        with self._lock:
            recent = self._records[-50:]
            avg_latency = sum(r.latency_seconds for r in recent) / len(recent) if recent else 0.0
            return {
                "total_requests": self._total_requests,
                "total_fallbacks": self._total_fallbacks,
                "total_validation_failures": self._total_validation_failures,
                "recent_average_latency_seconds": round(avg_latency, 4),
                "tracked_in_memory": len(self._records),
            }

    def clear(self) -> None:
        with self._lock:
            self._records.clear()
            self._total_requests = 0
            self._total_fallbacks = 0
            self._total_validation_failures = 0


# Default shared instance + thin module-level wrappers, matching the
# rest of the app's convention (see app.llm.circuit_breaker).
_default_log = MonitoringLog()


def record_request(
    request_id: str,
    latency_seconds: float,
    models_used: tuple[str, ...],
    fallback_count: int,
    consensus_result: tuple[str, ...],
    optimizer_time_seconds: float,
    validation_failures: int,
) -> RequestMetrics:
    """Build and store one RequestMetrics record. See RequestMetrics for
    field semantics. Raises ValueError on malformed input (e.g. a
    models_used entry that isn't a plain "provider/model" id) — callers
    should treat monitoring as best-effort and catch broadly around this
    call, per the module docstring."""
    metrics = RequestMetrics(
        request_id=request_id,
        latency_seconds=latency_seconds,
        models_used=tuple(models_used),
        fallback_count=fallback_count,
        consensus_result=tuple(consensus_result),
        optimizer_time_seconds=optimizer_time_seconds,
        validation_failures=validation_failures,
    )
    _default_log.record(metrics)
    return metrics


def recent(limit: int = 50) -> list[RequestMetrics]:
    return _default_log.recent(limit)


def summary() -> dict:
    return _default_log.summary()


def clear() -> None:
    _default_log.clear()
