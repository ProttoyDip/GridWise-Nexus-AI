"""Bounded process-local interpretation cache; keys contain no credentials."""

import hashlib
import json
import math
import os
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import date

from app.llm.confidence import ConfidenceDecision
from app.llm.prompts import SYSTEM_PROMPT
from app.models.request import BatteryConfig, HourEntry
from app.models.response import DirectiveInterpretation


def interpretation_cache_key(operator_note: str, directive_context: dict) -> str:
    """Hash an unambiguous canonical note/context pair; never retain either."""
    serialized = json.dumps([operator_note.strip(), directive_context], sort_keys=True,
                            separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def build_directive_context(
    hours: list[HourEntry], battery: BatteryConfig, models: list[tuple[str, str]],
) -> dict:
    """Explicit allowlist: scenario inputs and public model IDs, no API keys.

    Note index and scenario ID do not change interpretation meaning. Forecasts
    are sorted as in the prompt. Date and prompt/policy versions prevent reuse
    across a new day's operating context or interpretation policy.
    """
    return {
        "battery": battery.model_dump(),
        "hours": [h.model_dump() for h in sorted(hours, key=lambda h: h.hour)],
        "models": models,
        "date": date.today().isoformat(),
        "prompt_hash": hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest(),
        "policy_version": 2,
    }


def _cacheable(result: DirectiveInterpretation) -> bool:
    metadata = result._confidence_metadata
    # Failure fallbacks may contain provider error text and must not poison
    # later requests after recovery. Genuine interpreted no_op is cacheable.
    return metadata is not None and metadata.agreement_count > 0 and metadata.decision != ConfidenceDecision.ESCALATE


@dataclass(frozen=True)
class _Entry:
    expires_at: float
    interpretation: DirectiveInterpretation


class InterpretationCache:
    """TTL/LRU cache with one in-flight computation per key across threads.

    Returned values are deep copies, preserving private confidence evidence
    without sharing mutable directive data. TTL zero disables caching.
    """

    def __init__(self, ttl_seconds: float = 300, max_entries: int = 1024,
                 clock: Callable[[], float] = time.monotonic):
        if not math.isfinite(ttl_seconds) or ttl_seconds < 0:
            raise ValueError("Cache TTL must be finite and non-negative")
        if type(max_entries) is not int or max_entries < 1:
            raise ValueError("Cache capacity must be a positive integer")
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._inflight: dict[str, Future] = {}

    def clear(self) -> None:
        """Drop stored results; leave existing computations running."""
        with self._lock:
            self._entries.clear()

    def _get_locked(self, key: str) -> DirectiveInterpretation | None:
        now = self._clock()
        for expired in [k for k, entry in self._entries.items() if entry.expires_at <= now]:
            del self._entries[expired]
        entry = self._entries.get(key)
        if entry is None:
            return None
        self._entries.move_to_end(key)
        return entry.interpretation.model_copy(deep=True)

    def get(self, key: str) -> DirectiveInterpretation | None:
        with self._lock:
            return self._get_locked(key)

    def get_or_compute(self, key: str, compute: Callable[[], DirectiveInterpretation]) -> DirectiveInterpretation:
        if self.ttl_seconds == 0:
            return compute()
        with self._lock:
            cached = self._get_locked(key)
            if cached is not None:
                return cached
            future = self._inflight.get(key)
            owner = future is None
            if owner:
                future = Future()
                self._inflight[key] = future
        if not owner:
            return future.result().model_copy(deep=True)
        try:
            result = compute()
            if _cacheable(result):
                stored = result.model_copy(deep=True)
                stored.note_index = 0
                with self._lock:
                    self._entries[key] = _Entry(self._clock() + self.ttl_seconds, stored)
                    self._entries.move_to_end(key)
                    while len(self._entries) > self.max_entries:
                        self._entries.popitem(last=False)
            future.set_result(result.model_copy(deep=True))
            return result.model_copy(deep=True)
        except BaseException as exc:
            future.set_exception(exc)
            raise
        finally:
            with self._lock:
                self._inflight.pop(key, None)


interpretation_cache = InterpretationCache(
    ttl_seconds=float(os.getenv("INTERPRETATION_CACHE_TTL_SECONDS", "300")),
    max_entries=int(os.getenv("INTERPRETATION_CACHE_MAX_ENTRIES", "1024")),
)
