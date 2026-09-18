"""Shared fixtures for failure-simulation scenarios.

Provides:
- ``BATTERY`` / ``HOURS`` matching the rest of the test suite.
- ``build_scenario`` for constructing well-formed ``ScenarioRequest``s.
- ``ScriptedProvider`` returning a queue of strings/exceptions.
- ``full_wiring`` that monkeypatches adaptive_consensus and its
  dependencies to a fully controlled triple-provider fake setup, with
  retry/backoff sleeps disabled (see ``test_adaptive_consensus.py`` for
  the same pattern).
- ``client`` yielding a FastAPI ``TestClient`` bound to ``app.main:app``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.llm import adaptive_consensus as adaptive_module
from app.llm import consensus as consensus_module
from app.llm import interpreter as interpreter_module
from app.llm.model_registry import ModelSpec
from app.main import app
from app.models.request import BatteryConfig, HourEntry, ScenarioRequest

BATTERY = BatteryConfig(
    capacity_kwh=500,
    initial_energy_kwh=250,
    minimum_energy_kwh=50,
    max_charge_kwh_per_hour=100,
    max_discharge_kwh_per_hour=100,
)
HOURS = [
    HourEntry(hour=h, demand_kwh=150, solar_kwh=50, tariff_bdt_per_kwh=10)
    for h in range(24)
]


# Three providers so MEDIUM/HIGH risk paths have alternates available.
PRIMARY_SPEC = ModelSpec(
    name="model-a", provider="providerA", priority=1,
    expected_latency=1.0, json_reliability_score=0.9, reasoning_score=0.9,
)
SECONDARY_SPEC = ModelSpec(
    name="model-b", provider="providerB", priority=2,
    expected_latency=1.0, json_reliability_score=0.9, reasoning_score=0.9,
)
TERTIARY_SPEC = ModelSpec(
    name="model-c", provider="providerC", priority=3,
    expected_latency=1.0, json_reliability_score=0.9, reasoning_score=0.9,
)
ARBITER_SPEC = ModelSpec(
    name="model-arbiter", provider="providerD", priority=1,
    expected_latency=1.0, json_reliability_score=0.9, reasoning_score=0.9,
)


class ScriptedProvider:
    """Drop-in fake LLM returning queued strings or raising queued exceptions."""

    def __init__(self, script, model: str = "fake-model") -> None:
        self._script = list(script)
        self.calls = 0
        self.model = model
        # Per-call record of (system_prompt, user_prompt, returned_value)
        # so tests can assert e.g. that a repair prompt was issued.
        self.history: list[tuple[str, str, Any]] = []

    def complete(self, system_prompt, user_prompt):
        self.calls += 1
        item = self._script.pop(0) if self._script else None
        self.history.append((system_prompt, user_prompt, item))
        if isinstance(item, Exception):
            raise item
        return item

    def complete_structured(self, system_prompt, user_prompt, schema):
        return self.complete(system_prompt, user_prompt)

    @property
    def supports_structured_output(self):
        return False


def directive_json(directive_type, applies, adjustment, explanation="test", note_index=0):
    return json.dumps(
        {
            "note_index": note_index,
            "applies": applies,
            "directive_type": directive_type,
            "structured_adjustment": adjustment,
            "explanation": explanation,
        }
    )


def no_op_json():
    return directive_json("no_op", False, None)


def build_scenario(operator_notes=None):
    return ScenarioRequest(
        scenario_id="failure-sim",
        operator_notes=list(operator_notes) if operator_notes else ["ok"],
        hours=HOURS,
        battery=BATTERY,
    )


@pytest.fixture
def full_wiring(monkeypatch):
    """Wire adaptive_consensus + consensus + interpreter to fakes.

    Mirrors ``tests/test_adaptive_consensus.py``: helpers consumed
    inside adaptive_consensus code are patched on ``adaptive_module``,
    while helpers consumed by consensus helpers (``_arbitrate``,
    ``_build_chain_for_spec``, ``_select_secondary_spec``) are patched
    on ``consensus_module``. Retry/backoff sleeps are zeroed on both
    ``interpreter_module`` and ``consensus_module``.
    """
    providers: dict[tuple[str, str], ScriptedProvider] = {}

    def fake_build_provider(spec: ModelSpec):
        key = (spec.provider, spec.name)
        if key not in providers:
            raise AssertionError(f"build_provider called for unregistered spec {key}")
        return providers[key]

    monkeypatch.setattr(adaptive_module, "get_primary_model", lambda: PRIMARY_SPEC)
    monkeypatch.setattr(adaptive_module, "get_arbiter_model", lambda: ARBITER_SPEC)
    monkeypatch.setattr(consensus_module, "get_primary_model", lambda: PRIMARY_SPEC)
    monkeypatch.setattr(consensus_module, "get_arbiter_model", lambda: ARBITER_SPEC)
    monkeypatch.setattr(consensus_module, "get_fast_model", lambda: None)
    monkeypatch.setattr(
        adaptive_module, "PRIMARY_INTERPRETERS",
        [PRIMARY_SPEC, SECONDARY_SPEC, TERTIARY_SPEC],
    )
    monkeypatch.setattr(
        adaptive_module, "_configured_provider_names",
        lambda: {"providerA", "providerB", "providerC", "providerD"},
    )
    monkeypatch.setattr(consensus_module, "build_provider", fake_build_provider)
    monkeypatch.setattr(consensus_module, "get_provider_chain", lambda: [])
    monkeypatch.setattr(
        consensus_module, "PRIMARY_INTERPRETERS",
        [PRIMARY_SPEC, SECONDARY_SPEC, TERTIARY_SPEC],
    )
    monkeypatch.setattr(
        consensus_module, "_configured_provider_names",
        lambda: {"providerA", "providerB", "providerC", "providerD"},
    )
    monkeypatch.setattr(interpreter_module, "RETRY_BACKOFF_SECONDS", 0)
    monkeypatch.setattr(interpreter_module, "EXPONENTIAL_BACKOFF_BASE_SECONDS", 0)
    monkeypatch.setattr(interpreter_module, "SHORT_RETRY_BACKOFF_SECONDS", 0)
    monkeypatch.setattr(consensus_module, "RETRY_BACKOFF_SECONDS", 0)
    monkeypatch.setattr(consensus_module, "EXPONENTIAL_BACKOFF_BASE_SECONDS", 0)
    monkeypatch.setattr(consensus_module, "SHORT_RETRY_BACKOFF_SECONDS", 0)
    return providers


def register(wiring, spec: ModelSpec, script):
    provider = ScriptedProvider(script, model=spec.name)
    wiring[(spec.provider, spec.name)] = provider
    return provider


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
