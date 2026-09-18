"""Tests for concurrent (asyncio.gather-based) operator-note interpretation.

Verifies, for 1, 2, and 3 notes:
- each note is still interpreted correctly and independently
- note_index ordering in the returned list matches input order, even
  when notes complete out of order
- wall-clock time reflects real concurrency (parallel, not sequential)

Uses a fake provider (time.sleep for latency, no real network calls) so
these tests are fast and deterministic regardless of live provider speed.
"""

import json
import re
import threading
import time

from app.llm import interpreter as interpreter_module
from app.models.request import BatteryConfig, HourEntry

BATTERY = BatteryConfig(
    capacity_kwh=200,
    initial_energy_kwh=100,
    minimum_energy_kwh=30,
    max_charge_kwh_per_hour=50,
    max_discharge_kwh_per_hour=50,
)
HOURS = [HourEntry(hour=h, demand_kwh=100, solar_kwh=0, tariff_bdt_per_kwh=5) for h in range(24)]

_NOTE_INDEX_RE = re.compile(r"Operator note \(index (\d+)\)")


class LatencyProbeProvider:
    """Fake provider that sleeps a per-note-index delay (extracted from
    the prompt text, since that's how the real interpreter tags each
    call) before responding, and records completion order + total call
    count in a thread-safe way. Lets tests assert both correctness and
    real concurrency without any network I/O."""

    def __init__(self, delay_by_note_index: dict[int, float], default_delay: float = 0.05) -> None:
        self._delay_by_note_index = delay_by_note_index
        self._default_delay = default_delay
        self._lock = threading.Lock()
        self.completion_order: list[int] = []
        self.call_count = 0

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        match = _NOTE_INDEX_RE.search(user_prompt)
        note_index = int(match.group(1)) if match else -1
        delay = self._delay_by_note_index.get(note_index, self._default_delay)

        time.sleep(delay)

        with self._lock:
            self.call_count += 1
            self.completion_order.append(note_index)

        return json.dumps(
            {
                "note_index": note_index,
                "applies": True,
                "directive_type": "no_charge_window",
                "structured_adjustment": {"hours": [note_index % 23]},
                "explanation": f"probe response for note {note_index}",
            }
        )


def _run(operator_notes: list[str], delay_by_note_index: dict[int, float]):
    provider = LatencyProbeProvider(delay_by_note_index)
    original_get_chain = interpreter_module.get_provider_chain
    interpreter_module.get_provider_chain = lambda: [("openrouter", provider)]
    try:
        start = time.perf_counter()
        results = interpreter_module.interpret_operator_notes(operator_notes, HOURS, BATTERY)
        elapsed = time.perf_counter() - start
    finally:
        interpreter_module.get_provider_chain = original_get_chain
    return results, elapsed, provider


def test_one_note_is_interpreted_correctly():
    results, elapsed, provider = _run(["Keep the battery from charging 2pm-4pm."], {0: 0.05})

    assert len(results) == 1
    assert results[0].note_index == 0
    assert results[0].directive_type == "no_charge_window"
    assert provider.call_count == 1
    assert elapsed < 0.2  # single note, no parallelism needed, should be fast


def test_two_notes_run_in_parallel_and_preserve_order():
    # Note 0 is deliberately the SLOWER of the two, so if execution were
    # still sequential-by-index, note 1 could never finish before note 0.
    # If concurrent, note 1 finishes first despite starting second.
    delays = {0: 0.3, 1: 0.05}
    results, elapsed, provider = _run(["slow note", "fast note"], delays)

    assert [r.note_index for r in results] == [0, 1]
    assert all(r.directive_type == "no_charge_window" for r in results)
    assert provider.call_count == 2

    # Proof of concurrency: note 1 (0.05s) completed before note 0 (0.3s),
    # even though note 0 was submitted first.
    assert provider.completion_order[0] == 1

    # Proof of real wall-clock parallelism: sequential would take
    # >= 0.3 + 0.05 = 0.35s; concurrent should be close to max(0.3, 0.05).
    assert elapsed < 0.35


def test_three_notes_run_in_parallel_and_preserve_order():
    # Delays are inversely related to note_index: note 0 is slowest,
    # note 2 is fastest, so completion order should be roughly reversed
    # from submission order if (and only if) they truly run concurrently.
    delays = {0: 0.3, 1: 0.2, 2: 0.05}
    results, elapsed, provider = _run(["note a", "note b", "note c"], delays)

    assert [r.note_index for r in results] == [0, 1, 2]
    assert all(r.directive_type == "no_charge_window" for r in results)
    assert provider.call_count == 3

    # The fastest note (2) should not be the last one to complete, which
    # would only happen under strict index-order sequential execution.
    assert provider.completion_order[-1] != 2

    # Sequential would take >= 0.3 + 0.2 + 0.05 = 0.55s; concurrent should
    # be close to max(0.3, 0.2, 0.05) = 0.3s. Generous margin for CI jitter.
    assert elapsed < 0.45


def test_async_entrypoint_used_directly_from_a_running_event_loop():
    """interpret_operator_notes_async must be usable directly (not via
    the sync asyncio.run wrapper) by a caller that already has its own
    event loop running — the sync wrapper cannot nest under asyncio.run."""
    import asyncio

    provider = LatencyProbeProvider({0: 0.05, 1: 0.05, 2: 0.05})
    original_get_chain = interpreter_module.get_provider_chain
    interpreter_module.get_provider_chain = lambda: [("openrouter", provider)]
    try:
        results = asyncio.run(
            interpreter_module.interpret_operator_notes_async(
                ["note a", "note b", "note c"], HOURS, BATTERY
            )
        )
    finally:
        interpreter_module.get_provider_chain = original_get_chain

    assert [r.note_index for r in results] == [0, 1, 2]
    assert provider.call_count == 3
