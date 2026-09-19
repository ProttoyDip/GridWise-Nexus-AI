"""Bounded, process-local TTL cache for verified optimization results."""

from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
import threading
import time

from app.models.request import BatteryConfig, FlexibleLoad, HourEntry
from app.models.response import DirectiveInterpretation, HourlyPlanEntry


def optimization_cache_key(
    scenario_id: str, hours: Sequence[HourEntry], battery: BatteryConfig,
    validated_directives: Sequence[DirectiveInterpretation],
    flexible_loads: Sequence[FlexibleLoad] = (),
) -> str:
    """Hash canonical public inputs; private metadata is excluded."""
    data = {
        "scenario_id": scenario_id,
        "hours": [hour.model_dump() for hour in sorted(hours, key=lambda entry: entry.hour)],
        "battery": battery.model_dump(),
        "validated_directives": [directive.model_dump() for directive in validated_directives],
        "flexible_loads": [load.model_dump() for load in flexible_loads],
    }
    serialized = json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class OptimizationResult:
    hourly_plan: list[HourlyPlanEntry]
    total_cost_bdt: float
    timestamp: datetime

    def copy(self) -> "OptimizationResult":
        return OptimizationResult([entry.model_copy(deep=True) for entry in self.hourly_plan],
                                  self.total_cost_bdt, self.timestamp)


@dataclass(frozen=True)
class _Entry:
    result: OptimizationResult
    expires_at: float


class OptimizationCache:
    """Thread-safe TTL/LRU storage with isolated copies on reads and writes.

    TTL is measured from insertion with a monotonic clock. Zero disables
    caching. Only verified successful results should be inserted by callers.
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

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def discard(self, key: str) -> None:
        with self._lock:
            self._entries.pop(key, None)

    def _expire(self, now: float) -> None:
        for key in [key for key, entry in self._entries.items() if entry.expires_at <= now]:
            del self._entries[key]

    def get(self, key: str) -> OptimizationResult | None:
        if self.ttl_seconds == 0:
            return None
        with self._lock:
            self._expire(self._clock())
            entry = self._entries.get(key)
            if entry is None:
                return None
            self._entries.move_to_end(key)
            return entry.result.copy()

    def put(self, key: str, hourly_plan: Sequence[HourlyPlanEntry], total_cost_bdt: float) -> None:
        if self.ttl_seconds == 0:
            return
        if not math.isfinite(total_cost_bdt) or total_cost_bdt < 0:
            raise ValueError("Cached cost must be finite and non-negative")
        result = OptimizationResult([entry.model_copy(deep=True) for entry in hourly_plan],
                                    total_cost_bdt, datetime.now(timezone.utc))
        with self._lock:
            now = self._clock()
            self._expire(now)
            self._entries[key] = _Entry(result, now + self.ttl_seconds)
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)


def _configured_cache() -> OptimizationCache:
    try:
        return OptimizationCache(
            ttl_seconds=float(os.getenv("OPTIMIZATION_CACHE_TTL_SECONDS", "300")),
            max_entries=int(os.getenv("OPTIMIZATION_CACHE_MAX_ENTRIES", "1024")),
        )
    except (ValueError, TypeError, OverflowError):
        # Invalid cache configuration must never prevent API startup.
        return OptimizationCache()


optimization_cache = _configured_cache()
