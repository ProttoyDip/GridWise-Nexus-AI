"""Unit tests for app.llm.adaptive_consensus — fake providers, no network.

Covers the three risk tiers (LOW / MEDIUM / HIGH), the agreement and
disagreement branches within each, the single-provider safety collapse
(``provider.calls == len(operator_notes)`` contract), and the empty
provider-chain fallback to ``no_op``.
"""

from __future__ import annotations

import json

import pytest

from app.llm import adaptive_consensus as adaptive_module
from app.llm import consensus as consensus_module
from app.llm import interpreter as interpreter_module
from app.llm.model_registry import ModelSpec
from app.models.request import BatteryConfig, HourEntry

BATTERY = BatteryConfig(
    capacity_kwh=500,
    initial_energy_kwh=250,
    minimum_energy_kwh=50,
    max_charge_kwh_per_hour=100,
    max_discharge_kwh_per_hour=100,
)
HOURS = [HourEntry(hour=h, demand_kwh=150, solar_kwh=50, tariff_bdt_per_kwh=10) for h in range(24)]


PRIMARY_SPEC = ModelSpec(
    name="model-a",
    provider="providerA",
    priority=1,
    expected_latency=1.0,
    json_reliability_score=0.9,
    reasoning_score=0.9,
)
SECONDARY_SPEC = ModelSpec(
    name="model-b",
    provider="providerB",
    priority=2,
    expected_latency=1.0,
    json_reliability_score=0.9,
    reasoning_score=0.9,
)
TERTIARY_SPEC = ModelSpec(
    name="model-c",
    provider="providerC",
    priority=3,
    expected_latency=1.0,
    json_reliability_score=0.9,
    reasoning_score=0.9,
)
ARBITER_SPEC = ModelSpec(
    name="model-arbiter",
    provider="providerD",
    priority=1,
    expected_latency=1.0,
    json_reliability_score=0.9,
    reasoning_score=0.9,
)


class ScriptedProvider:
    def __init__(self, script, model="fake-model"):
        self._script = list(script)
        self.calls = 0
        self.model = model

    def complete(self, system_prompt, user_prompt):
        self.calls += 1
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def complete_structured(self, system_prompt, user_prompt, schema):
        return self.complete(system_prompt, user_prompt)

    @property
    def supports_structured_output(self):
        return False


def _directive_json(directive_type, applies, adjustment, explanation="test"):
    return json.dumps(
        {
            "note_index": 0,
            "applies": applies,
            "directive_type": directive_type,
            "structured_adjustment": adjustment,
            "explanation": explanation,
        }
    )


def _no_op_json():
    return _directive_json("no_op", False, None)


@pytest.fixture
def full_wiring(monkeypatch):
    """Wire adaptive_consensus + its dependencies to fully controlled
    fakes, isolated from real env/network. Three providers configured
    so MEDIUM and HIGH paths have alternates available."""
    providers: dict[tuple[str, str], ScriptedProvider] = {}

    def fake_build_provider(spec: ModelSpec):
        key = (spec.provider, spec.name)
        if key not in providers:
            raise AssertionError(f"build_provider called for unregistered spec {key}")
        return providers[key]

    # Patch on the *source* modules where each helper is actually
    # looked up at call time. Adaptive_consensus imports get_primary_model
    # / get_arbiter_model / PRIMARY_INTERPRETERS / _configured_provider_names
    # into its own namespace, so patch those there. build_provider,
    # get_provider_chain, get_fast_model, get_arbiter_model, and the
    # consensus-internal PRIMARY_INTERPRETERS / _configured_provider_names
    # lookups happen inside consensus's helpers
    # (consensus._build_chain_for_spec, consensus._select_secondary_spec,
    # consensus._arbitrate), so patch those on the consensus module where
    # they were originally imported.
    monkeypatch.setattr(adaptive_module, "get_primary_model", lambda: PRIMARY_SPEC)
    monkeypatch.setattr(adaptive_module, "get_arbiter_model", lambda: ARBITER_SPEC)
    monkeypatch.setattr(consensus_module, "get_primary_model", lambda: PRIMARY_SPEC)
    monkeypatch.setattr(consensus_module, "get_arbiter_model", lambda: ARBITER_SPEC)
    monkeypatch.setattr(consensus_module, "get_fast_model", lambda: None)
    monkeypatch.setattr(
        adaptive_module,
        "PRIMARY_INTERPRETERS",
        [PRIMARY_SPEC, SECONDARY_SPEC, TERTIARY_SPEC],
    )
    monkeypatch.setattr(
        adaptive_module,
        "_configured_provider_names",
        lambda: {"providerA", "providerB", "providerC", "providerD"},
    )
    monkeypatch.setattr(consensus_module, "build_provider", fake_build_provider)
    monkeypatch.setattr(consensus_module, "get_provider_chain", lambda: [])
    monkeypatch.setattr(
        consensus_module,
        "PRIMARY_INTERPRETERS",
        [PRIMARY_SPEC, SECONDARY_SPEC, TERTIARY_SPEC],
    )
    monkeypatch.setattr(
        consensus_module,
        "_configured_provider_names",
        lambda: {"providerA", "providerB", "providerC", "providerD"},
    )
    # Stop interpreter retry/backoff sleeps so tests don't drag.
    monkeypatch.setattr(interpreter_module, "RETRY_BACKOFF_SECONDS", 0)
    monkeypatch.setattr(interpreter_module, "EXPONENTIAL_BACKOFF_BASE_SECONDS", 0)
    monkeypatch.setattr(interpreter_module, "SHORT_RETRY_BACKOFF_SECONDS", 0)
    monkeypatch.setattr(consensus_module, "RETRY_BACKOFF_SECONDS", 0)
    monkeypatch.setattr(consensus_module, "EXPONENTIAL_BACKOFF_BASE_SECONDS", 0)
    monkeypatch.setattr(consensus_module, "SHORT_RETRY_BACKOFF_SECONDS", 0)
    return providers


def _register(wiring, spec: ModelSpec, script):
    provider = ScriptedProvider(script, model=spec.name)
    wiring[(spec.provider, spec.name)] = provider
    return provider


# --- LOW RISK -------------------------------------------------------------


def test_low_risk_note_invokes_only_primary(full_wiring):
    primary = _register(full_wiring, PRIMARY_SPEC, [_no_op_json()])

    result = adaptive_module.interpret_note_with_adaptive_consensus(
        "department meeting at 3pm", 0, HOURS, BATTERY
    )

    assert result.directive_type == "no_op"
    assert primary.calls == 1
    # No secondary, tertiary, or arbiter should be instantiated for LOW.
    assert (SECONDARY_SPEC.provider, SECONDARY_SPEC.name) not in full_wiring
    assert (TERTIARY_SPEC.provider, TERTIARY_SPEC.name) not in full_wiring
    assert (ARBITER_SPEC.provider, ARBITER_SPEC.name) not in full_wiring


def test_low_risk_with_numeric_directive_promotes_to_medium(full_wiring):
    """A note that *looks* LOW in text but yields a numeric primary
    should still get a second opinion — the risk classifier refines
    using the primary interpretation."""
    primary = _register(
        full_wiring,
        PRIMARY_SPEC,
        [_directive_json("solar_reduction", True, {"hours": [13], "factor": 0.2})],
    )
    secondary = _register(
        full_wiring,
        SECONDARY_SPEC,
        [_directive_json("solar_reduction", True, {"hours": [13], "factor": 0.2})],
    )

    result = adaptive_module.interpret_note_with_adaptive_consensus(
        "ok", 0, HOURS, BATTERY
    )

    assert result.directive_type == "solar_reduction"
    assert primary.calls == 1
    assert secondary.calls == 1


# --- MEDIUM RISK ----------------------------------------------------------


def test_medium_risk_agreement_keeps_primary(full_wiring):
    primary = _register(
        full_wiring,
        PRIMARY_SPEC,
        [_directive_json("solar_reduction", True, {"hours": [13, 14], "factor": 0.2}, "p")],
    )
    secondary = _register(
        full_wiring,
        SECONDARY_SPEC,
        [_directive_json("solar_reduction", True, {"hours": [13, 14], "factor": 0.21})],
    )

    result = adaptive_module.interpret_note_with_adaptive_consensus(
        "solar drops 80%", 0, HOURS, BATTERY
    )

    assert result.explanation == "p"
    assert primary.calls == 1
    assert secondary.calls == 1


def test_medium_risk_disagreement_calls_arbiter(full_wiring):
    _register(
        full_wiring,
        PRIMARY_SPEC,
        [_directive_json("solar_reduction", True, {"hours": [13], "factor": 0.2})],
    )
    _register(
        full_wiring,
        SECONDARY_SPEC,
        [_directive_json("solar_reduction", True, {"hours": [13], "factor": 0.9})],
    )
    arbiter = _register(
        full_wiring,
        ARBITER_SPEC,
        [_directive_json("solar_reduction", True, {"hours": [13], "factor": 0.2}, "arb")],
    )

    result = adaptive_module.interpret_note_with_adaptive_consensus(
        "ambiguous solar note", 0, HOURS, BATTERY
    )

    assert result.explanation == "arb"
    assert arbiter.calls == 1


# --- HIGH RISK ------------------------------------------------------------


def test_high_risk_primary_secondary_agreement_skips_arbiter(full_wiring):
    primary = _register(
        full_wiring,
        PRIMARY_SPEC,
        [_directive_json(
            "max_grid_window", True,
            {"hours": list(range(0, 18)), "max_grid_kwh": 50.0},
        )],
    )
    secondary = _register(
        full_wiring,
        SECONDARY_SPEC,
        [_directive_json(
            "max_grid_window", True,
            {"hours": list(range(0, 18)), "max_grid_kwh": 50.5},
        )],
    )

    # HIGH verification races both available providers; the tertiary may
    # start before the agreeing secondary finishes.
    tertiary = _register(full_wiring, TERTIARY_SPEC, [_directive_json(
        "max_grid_window", True,
        {"hours": list(range(0, 18)), "max_grid_kwh": 50.0},
    )])
    result = adaptive_module.interpret_note_with_adaptive_consensus(
        "battery 100 kWh reserve, grid limit 50 kWh, solar 30 kWh",
        0, HOURS, BATTERY,
    )

    assert result.directive_type == "max_grid_window"
    assert primary.calls == 1
    assert secondary.calls == 1
    assert tertiary.calls <= 1
    assert (ARBITER_SPEC.provider, ARBITER_SPEC.name) not in full_wiring


def test_high_risk_three_way_majority_picks_winner_without_arbiter(full_wiring):
    primary = _register(
        full_wiring,
        PRIMARY_SPEC,
        [_directive_json("solar_reduction", True, {"hours": [13], "factor": 0.2})],
    )
    secondary = _register(
        full_wiring,
        SECONDARY_SPEC,
        [_directive_json("solar_reduction", True, {"hours": [13], "factor": 0.5})],
    )
    tertiary = _register(
        full_wiring,
        TERTIARY_SPEC,
        [_directive_json("solar_reduction", True, {"hours": [13], "factor": 0.2})],
    )

    result = adaptive_module.interpret_note_with_adaptive_consensus(
        "battery 100 kWh reserve, grid limit 50 kWh, solar 30 kWh",
        0, HOURS, BATTERY,
    )

    # Tertiary agrees with primary — 2-of-3 majority is factor=0.2.
    assert result.structured_adjustment == {"hours": [13], "factor": 0.2}
    assert primary.calls == 1
    assert secondary.calls == 1
    assert tertiary.calls == 1


def test_high_risk_three_way_disagreement_calls_arbiter(full_wiring):
    _register(
        full_wiring,
        PRIMARY_SPEC,
        [_directive_json("solar_reduction", True, {"hours": [13], "factor": 0.2})],
    )
    _register(
        full_wiring,
        SECONDARY_SPEC,
        [_directive_json("solar_reduction", True, {"hours": [13], "factor": 0.5})],
    )
    _register(
        full_wiring,
        TERTIARY_SPEC,
        [_directive_json("solar_reduction", True, {"hours": [13], "factor": 0.8})],
    )
    arbiter = _register(
        full_wiring,
        ARBITER_SPEC,
        [_directive_json("solar_reduction", True, {"hours": [13], "factor": 0.2}, "arb")],
    )

    result = adaptive_module.interpret_note_with_adaptive_consensus(
        "battery 100 kWh reserve, grid limit 50 kWh, solar 30 kWh",
        0, HOURS, BATTERY,
    )

    assert result.explanation == "arb"
    assert arbiter.calls == 1


# --- FALLBACKS ------------------------------------------------------------


def test_no_provider_configured_falls_back_to_no_op(monkeypatch):
    monkeypatch.setattr(adaptive_module, "get_primary_model", lambda: None)
    monkeypatch.setattr(consensus_module, "get_provider_chain", lambda: [])

    result = adaptive_module.interpret_note_with_adaptive_consensus(
        "anything", 0, HOURS, BATTERY
    )

    assert result.directive_type == "no_op"
    assert result.applies is False


def test_single_provider_collapses_to_low_style_behavior(monkeypatch):
    """Single-provider safety: with only providerA configured, even a
    HIGH-risk note must not try to call a second provider — there is
    no second vote to gather."""
    providers: dict[tuple[str, str], ScriptedProvider] = {}

    def fake_build_provider(spec: ModelSpec):
        key = (spec.provider, spec.name)
        if key not in providers:
            raise AssertionError(f"build_provider called for unregistered spec {key}")
        return providers[key]

    primary = ScriptedProvider(
        [_directive_json(
            "max_grid_window", True,
            {"hours": list(range(0, 18)), "max_grid_kwh": 50.0},
            "primary-only",
        )]
    )
    providers[(PRIMARY_SPEC.provider, PRIMARY_SPEC.name)] = primary

    monkeypatch.setattr(adaptive_module, "get_primary_model", lambda: PRIMARY_SPEC)
    monkeypatch.setattr(adaptive_module, "get_arbiter_model", lambda: None)
    monkeypatch.setattr(consensus_module, "get_primary_model", lambda: PRIMARY_SPEC)
    monkeypatch.setattr(consensus_module, "get_arbiter_model", lambda: None)
    monkeypatch.setattr(consensus_module, "get_fast_model", lambda: None)
    monkeypatch.setattr(adaptive_module, "PRIMARY_INTERPRETERS", [PRIMARY_SPEC])
    monkeypatch.setattr(
        adaptive_module, "_configured_provider_names", lambda: {"providerA"}
    )
    monkeypatch.setattr(
        consensus_module, "_configured_provider_names", lambda: {"providerA"}
    )
    monkeypatch.setattr(consensus_module, "PRIMARY_INTERPRETERS", [PRIMARY_SPEC])
    monkeypatch.setattr(consensus_module, "build_provider", fake_build_provider)
    monkeypatch.setattr(consensus_module, "get_provider_chain", lambda: [])

    result = adaptive_module.interpret_note_with_adaptive_consensus(
        "battery 100 kWh reserve, grid limit 50 kWh, solar 30 kWh",
        0, HOURS, BATTERY,
    )

    # Primary-only — exactly one call regardless of risk tier.
    assert result.explanation == "primary-only"
    assert primary.calls == 1


def test_batch_preserves_note_order(full_wiring):
    _register(
        full_wiring,
        PRIMARY_SPEC,
        [_no_op_json(), _directive_json(
            "no_charge_window", True, {"hours": [5]}
        )],
    )

    results = adaptive_module.interpret_operator_notes_with_adaptive_consensus(
        ["first note", "second note"], HOURS, BATTERY
    )

    assert len(results) == 2
    assert results[0].directive_type == "no_op"
    assert results[1].directive_type == "no_charge_window"
    assert results[0].note_index == 0
    assert results[1].note_index == 1
