"""Tests for the parallel model racer (app.llm.racer).

Uses fake providers with controlled latencies to verify:
- Concurrent execution (wall-clock proves parallelism)
- Early cancellation on agreement
- Timeout safety
- Single-chain passthrough
- Latency logging
- Fallback on failure
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time

import pytest

from app.llm import racer as racer_module
from app.llm import interpreter as interpreter_module
from app.llm.racer import RaceResult, race_interpretations, _check_agreement
from app.models.request import BatteryConfig, HourEntry
from app.models.response import DirectiveInterpretation

BATTERY = BatteryConfig(
    capacity_kwh=200,
    initial_energy_kwh=100,
    minimum_energy_kwh=30,
    max_charge_kwh_per_hour=50,
    max_discharge_kwh_per_hour=50,
)
HOURS = [
    HourEntry(hour=h, demand_kwh=100, solar_kwh=0, tariff_bdt_per_kwh=5)
    for h in range(24)
]

_NOTE_INDEX_RE = re.compile(r"Operator note \(index (\d+)\)")


class DelayProvider:
    """Fake provider with controlled delay and thread-safe call tracking."""

    def __init__(
        self,
        response_json: str,
        delay: float = 0.0,
        model: str = "fake-model",
    ) -> None:
        self._response = response_json
        self._delay = delay
        self.model = model
        self._lock = threading.Lock()
        self.calls = 0
        self.completion_order: list[float] = []

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        time.sleep(self._delay)
        with self._lock:
            self.calls += 1
            self.completion_order.append(time.perf_counter())
        return self._response


class FailingProvider:
    """Provider that raises on every call."""

    def __init__(self, model: str = "failing-model"):
        self.model = model
        self.calls = 0

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.calls += 1
        raise Exception("Provider failure")


def _solar_json(factor: float = 0.2, hours: list[int] | None = None) -> str:
    return json.dumps({
        "note_index": 0,
        "applies": True,
        "directive_type": "solar_reduction",
        "structured_adjustment": {
            "hours": hours or [13, 14],
            "factor": factor,
        },
        "explanation": f"solar factor {factor}",
    })


def _no_op_json() -> str:
    return json.dumps({
        "note_index": 0,
        "applies": False,
        "directive_type": "no_op",
        "structured_adjustment": None,
        "explanation": "irrelevant note",
    })


def _no_charge_json(hours: list[int] | None = None) -> str:
    return json.dumps({
        "note_index": 0,
        "applies": True,
        "directive_type": "no_charge_window",
        "structured_adjustment": {"hours": hours or [13, 14]},
        "explanation": "no charging",
    })


def _make_chain(provider, name="test") -> list[tuple[str, object]]:
    return [(name, provider)]


# ===================================================================
# _check_agreement unit tests
# ===================================================================


class TestCheckAgreement:
    """Unit tests for the agreement checking logic."""

    def test_two_agreeing_results(self):
        d1 = DirectiveInterpretation.model_validate(json.loads(_solar_json(0.2)))
        d2 = DirectiveInterpretation.model_validate(json.loads(_solar_json(0.21)))
        votes = [("a", d1), ("b", d2)]
        winner = _check_agreement(votes, min_agreement=2)
        assert winner is not None
        assert winner.structured_adjustment["factor"] == 0.2

    def test_two_disagreeing_results(self):
        d1 = DirectiveInterpretation.model_validate(json.loads(_solar_json(0.2)))
        d2 = DirectiveInterpretation.model_validate(json.loads(_solar_json(0.8)))
        votes = [("a", d1), ("b", d2)]
        winner = _check_agreement(votes, min_agreement=2)
        assert winner is None

    def test_three_way_majority(self):
        d1 = DirectiveInterpretation.model_validate(json.loads(_solar_json(0.2)))
        d2 = DirectiveInterpretation.model_validate(json.loads(_solar_json(0.8)))
        d3 = DirectiveInterpretation.model_validate(json.loads(_solar_json(0.2)))
        votes = [("a", d1), ("b", d2), ("c", d3)]
        winner = _check_agreement(votes, min_agreement=2)
        assert winner is not None
        assert winner.structured_adjustment["factor"] == 0.2

    def test_single_result_insufficient(self):
        d1 = DirectiveInterpretation.model_validate(json.loads(_solar_json(0.2)))
        votes = [("a", d1)]
        winner = _check_agreement(votes, min_agreement=2)
        assert winner is None

    def test_empty_votes(self):
        winner = _check_agreement([], min_agreement=2)
        assert winner is None


# ===================================================================
# race_interpretations integration tests
# ===================================================================


class TestRaceInterpretations:
    """Integration tests for the parallel racing logic."""

    def test_single_chain_no_racing_overhead(self):
        provider = DelayProvider(_no_charge_json(), delay=0.05)
        result = asyncio.run(race_interpretations(
            chains=[("only", _make_chain(provider))],
            note="test note", note_index=0, hours=HOURS, battery=BATTERY,
        ))
        assert result.winner is not None
        assert result.winner.directive_type == "no_charge_window"
        assert result.cancelled_count == 0
        assert len(result.votes) == 1

    def test_empty_chains(self):
        result = asyncio.run(race_interpretations(
            chains=[], note="test", note_index=0, hours=HOURS, battery=BATTERY,
        ))
        assert result.winner is None
        assert result.votes == []

    def test_two_agreeing_models_returns_winner(self):
        p1 = DelayProvider(_solar_json(0.2), delay=0.05)
        p2 = DelayProvider(_solar_json(0.21), delay=0.05)  # within tolerance
        result = asyncio.run(race_interpretations(
            chains=[
                ("model-a", _make_chain(p1, "provA")),
                ("model-b", _make_chain(p2, "provB")),
            ],
            note="test", note_index=0, hours=HOURS, battery=BATTERY,
            min_agreement=2,
        ))
        assert result.winner is not None
        assert result.winner.directive_type == "solar_reduction"
        assert len(result.votes) == 2

    def test_two_disagreeing_models_returns_no_winner(self):
        p1 = DelayProvider(_solar_json(0.2), delay=0.05)
        p2 = DelayProvider(_solar_json(0.8), delay=0.05)
        result = asyncio.run(race_interpretations(
            chains=[
                ("model-a", _make_chain(p1, "provA")),
                ("model-b", _make_chain(p2, "provB")),
            ],
            note="test", note_index=0, hours=HOURS, battery=BATTERY,
            min_agreement=2,
        ))
        assert result.winner is None
        assert len(result.votes) == 2

    def test_early_cancellation_on_agreement(self):
        """When two fast models agree, the slow third should be cancelled.

        Note: asyncio.to_thread wraps real OS threads which cannot be
        interrupted mid-sleep. ``task.cancel()`` prevents the result
        from being *processed* and raises CancelledError once the thread
        finishes.  We use a moderate delay (0.5s) so the test is fast
        while still proving the cancellation logic works.
        """
        p_fast1 = DelayProvider(_solar_json(0.2), delay=0.05)
        p_fast2 = DelayProvider(_solar_json(0.2), delay=0.05)
        p_slow = DelayProvider(_solar_json(0.8), delay=0.5)

        result = asyncio.run(race_interpretations(
            chains=[
                ("fast-a", _make_chain(p_fast1, "provA")),
                ("fast-b", _make_chain(p_fast2, "provB")),
                ("slow-c", _make_chain(p_slow, "provC")),
            ],
            note="test", note_index=0, hours=HOURS, battery=BATTERY,
            min_agreement=2,
        ))

        assert result.winner is not None
        assert result.winner.directive_type == "solar_reduction"
        assert result.cancelled_count >= 1
        # The racer collected exactly 2 votes (the agreeing ones) and
        # cancelled the third before processing its result.
        assert len(result.votes) == 2

    def test_parallel_execution_wall_clock(self):
        """Two models each taking 0.2s should complete in ~0.2s, not 0.4s."""
        p1 = DelayProvider(_solar_json(0.2), delay=0.2)
        p2 = DelayProvider(_solar_json(0.8), delay=0.2)

        start = time.perf_counter()
        result = asyncio.run(race_interpretations(
            chains=[
                ("a", _make_chain(p1, "provA")),
                ("b", _make_chain(p2, "provB")),
            ],
            note="test", note_index=0, hours=HOURS, battery=BATTERY,
            min_agreement=2,
        ))
        elapsed = time.perf_counter() - start

        # Sequential would be >= 0.4s; parallel should be ~0.2s.
        assert elapsed < 0.35
        assert len(result.votes) == 2

    def test_one_model_fails_other_continues(self):
        p_ok = DelayProvider(_no_charge_json(), delay=0.05)
        p_fail = FailingProvider()

        result = asyncio.run(race_interpretations(
            chains=[
                ("ok", _make_chain(p_ok, "provA")),
                ("fail", _make_chain(p_fail, "provB")),
            ],
            note="test", note_index=0, hours=HOURS, battery=BATTERY,
            min_agreement=1,
        ))

        assert result.winner is not None
        assert result.winner.directive_type == "no_charge_window"

    def test_all_models_fail_returns_no_winner(self):
        p1 = FailingProvider()
        p2 = FailingProvider()

        result = asyncio.run(race_interpretations(
            chains=[
                ("a", _make_chain(p1, "provA")),
                ("b", _make_chain(p2, "provB")),
            ],
            note="test", note_index=0, hours=HOURS, battery=BATTERY,
            min_agreement=2,
        ))

        assert result.winner is None
        assert len(result.votes) == 0

    def test_overall_timeout_returns_no_winner(self):
        """With a very short overall timeout, slow models should be cancelled.

        Note: asyncio.wait_for timeout fires in the event loop, but the
        underlying thread continues running until its sleep finishes.
        We verify the *RaceResult* is correct (no winner, timeout logged).
        """
        p_slow = DelayProvider(_solar_json(0.2), delay=0.5)

        result = asyncio.run(race_interpretations(
            chains=[("slow", _make_chain(p_slow, "provA"))],
            note="test", note_index=0, hours=HOURS, battery=BATTERY,
            timeout=0.2,
        ))

        # The single-chain path should have hit the timeout.
        # Winner is None because the chain didn't complete in time.
        assert result.winner is None

    def test_preserves_note_index(self):
        provider = DelayProvider(_no_charge_json(), delay=0.01)
        result = asyncio.run(race_interpretations(
            chains=[("a", _make_chain(provider))],
            note="test", note_index=7, hours=HOURS, battery=BATTERY,
        ))
        assert result.winner is not None
        assert result.winner.note_index == 7

    def test_race_duration_is_measured(self):
        provider = DelayProvider(_no_charge_json(), delay=0.05)
        result = asyncio.run(race_interpretations(
            chains=[("a", _make_chain(provider))],
            note="test", note_index=0, hours=HOURS, battery=BATTERY,
        ))
        assert result.race_duration_ms > 0


# ===================================================================
# Latency logging tests
# ===================================================================


class TestLatencyLogging:
    """Verify that per-model and overall latency is logged."""

    def test_per_chain_latency_logged(self, caplog):
        provider = DelayProvider(_no_charge_json(), delay=0.01)
        with caplog.at_level(logging.INFO, logger="app.llm.racer"):
            asyncio.run(race_interpretations(
                chains=[("test-model", _make_chain(provider))],
                note="test", note_index=0, hours=HOURS, battery=BATTERY,
            ))
        # Should see per-chain latency log.
        chain_logs = [r for r in caplog.records if "chain" in r.message and "completed in" in r.message]
        assert len(chain_logs) >= 1
        assert "test-model" in chain_logs[0].message

    def test_overall_race_duration_logged(self, caplog):
        provider = DelayProvider(_no_charge_json(), delay=0.01)
        with caplog.at_level(logging.INFO, logger="app.llm.racer"):
            asyncio.run(race_interpretations(
                chains=[("a", _make_chain(provider))],
                note="test", note_index=0, hours=HOURS, battery=BATTERY,
            ))
        finish_logs = [r for r in caplog.records if "finished" in r.message]
        assert len(finish_logs) >= 1

    def test_early_stop_logged(self, caplog):
        p1 = DelayProvider(_solar_json(0.2), delay=0.01)
        p2 = DelayProvider(_solar_json(0.2), delay=0.01)
        p3 = DelayProvider(_solar_json(0.8), delay=2.0)

        with caplog.at_level(logging.INFO, logger="app.llm.racer"):
            asyncio.run(race_interpretations(
                chains=[
                    ("a", _make_chain(p1, "provA")),
                    ("b", _make_chain(p2, "provB")),
                    ("c", _make_chain(p3, "provC")),
                ],
                note="test", note_index=0, hours=HOURS, battery=BATTERY,
                min_agreement=2,
            ))
        early_stop_logs = [r for r in caplog.records if "early stop" in r.message]
        assert len(early_stop_logs) >= 1
        assert "cancelled" in early_stop_logs[0].message
