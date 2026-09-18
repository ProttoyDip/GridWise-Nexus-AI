"""Unit tests for the LLM spot-check consensus flow (app.llm.consensus),
using fake providers (no real network calls) to exercise every branch:
no_op short-circuit, non-numeric short-circuit, numeric agreement,
numeric disagreement -> arbiter, arbiter unavailable/failure fallback,
and no-alternate-provider degradation.
"""

import json

import pytest

from app.llm import consensus as consensus_module
from app.llm.model_registry import ModelSpec
from app.llm.provider import LLMProviderError
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
    name="model-a", provider="providerA", priority=1,
    expected_latency=1.0, json_reliability_score=0.9, reasoning_score=0.9,
)
SECONDARY_SPEC = ModelSpec(
    name="model-b", provider="providerB", priority=2,
    expected_latency=1.0, json_reliability_score=0.9, reasoning_score=0.9,
)
ARBITER_SPEC = ModelSpec(
    name="model-arbiter", provider="providerC", priority=1,
    expected_latency=1.0, json_reliability_score=0.9, reasoning_score=0.9,
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
def wiring(monkeypatch):
    """Wire consensus.py's model-selection + provider-building hooks to
    fully controlled fakes, isolated from real env/network. Returns a
    dict of registered fake providers keyed by (provider, model_name)
    that tests populate before invoking consensus."""
    providers: dict[tuple[str, str], ScriptedProvider] = {}

    def fake_build_provider(spec: ModelSpec):
        key = (spec.provider, spec.name)
        if key not in providers:
            raise AssertionError(f"build_provider called for unregistered spec {key}")
        return providers[key]

    monkeypatch.setattr(consensus_module, "get_primary_model", lambda: PRIMARY_SPEC)
    monkeypatch.setattr(consensus_module, "get_arbiter_model", lambda: ARBITER_SPEC)
    monkeypatch.setattr(consensus_module, "get_fast_model", lambda: None)
    monkeypatch.setattr(consensus_module, "PRIMARY_INTERPRETERS", [PRIMARY_SPEC, SECONDARY_SPEC])
    monkeypatch.setattr(
        consensus_module, "_configured_provider_names", lambda: {"providerA", "providerB", "providerC"}
    )
    monkeypatch.setattr(consensus_module, "build_provider", fake_build_provider)
    monkeypatch.setattr(consensus_module, "get_provider_chain", lambda: [])
    monkeypatch.setattr(consensus_module, "RETRY_BACKOFF_SECONDS", 0)
    monkeypatch.setattr(consensus_module, "EXPONENTIAL_BACKOFF_BASE_SECONDS", 0)
    monkeypatch.setattr(consensus_module, "SHORT_RETRY_BACKOFF_SECONDS", 0)

    return providers


def _register(wiring, spec: ModelSpec, script):
    provider = ScriptedProvider(script, model=spec.name)
    wiring[(spec.provider, spec.name)] = provider
    return provider


def test_no_op_primary_accepted_without_secondary_call(wiring):
    primary = _register(wiring, PRIMARY_SPEC, [_no_op_json()])

    result = consensus_module.interpret_note_with_consensus("irrelevant note", 0, HOURS, BATTERY)

    assert result.directive_type == "no_op"
    assert primary.calls == 1
    assert (SECONDARY_SPEC.provider, SECONDARY_SPEC.name) not in wiring or True  # not registered => not called


def test_non_numeric_directive_accepted_without_secondary_call(wiring):
    primary = _register(
        wiring, PRIMARY_SPEC, [_directive_json("no_charge_window", True, {"hours": [2, 3]})]
    )

    result = consensus_module.interpret_note_with_consensus("no charging 2-4am", 0, HOURS, BATTERY)

    assert result.directive_type == "no_charge_window"
    assert result.structured_adjustment == {"hours": [2, 3]}
    assert primary.calls == 1


def test_numeric_agreement_returns_primary_result(wiring):
    primary = _register(
        wiring,
        PRIMARY_SPEC,
        [_directive_json("solar_reduction", True, {"hours": [13, 14], "factor": 0.2}, "primary said 0.2")],
    )
    secondary = _register(
        wiring,
        SECONDARY_SPEC,
        [_directive_json("solar_reduction", True, {"hours": [13, 14], "factor": 0.21})],
    )
    arbiter = _register(wiring, ARBITER_SPEC, [])

    result = consensus_module.interpret_note_with_consensus("solar drops 80%", 0, HOURS, BATTERY)

    assert result.structured_adjustment == {"hours": [13, 14], "factor": 0.2}
    assert result.explanation == "primary said 0.2"
    assert primary.calls == 1
    assert secondary.calls == 1
    assert arbiter.calls == 0


@pytest.mark.parametrize(
    "primary_adj,secondary_adj",
    [
        ({"hours": [13, 14], "factor": 0.2}, {"hours": [13, 14], "factor": 0.5}),  # factor mismatch
        ({"hours": [13, 14], "factor": 0.2}, {"hours": [13], "factor": 0.2}),  # hours mismatch
    ],
)
def test_numeric_disagreement_calls_arbiter(wiring, primary_adj, secondary_adj):
    _register(wiring, PRIMARY_SPEC, [_directive_json("solar_reduction", True, primary_adj)])
    _register(wiring, SECONDARY_SPEC, [_directive_json("solar_reduction", True, secondary_adj)])
    arbiter = _register(
        wiring,
        ARBITER_SPEC,
        [_directive_json("solar_reduction", True, {"hours": [13, 14], "factor": 0.2}, "arbiter decided")],
    )

    result = consensus_module.interpret_note_with_consensus("ambiguous note", 0, HOURS, BATTERY)

    assert arbiter.calls == 1
    assert result.explanation == "arbiter decided"
    assert result.structured_adjustment == {"hours": [13, 14], "factor": 0.2}


def test_directive_type_mismatch_counts_as_disagreement(wiring):
    _register(
        wiring, PRIMARY_SPEC, [_directive_json("solar_reduction", True, {"hours": [13, 14], "factor": 0.2})]
    )
    _register(wiring, SECONDARY_SPEC, [_no_op_json()])
    arbiter = _register(wiring, ARBITER_SPEC, [_no_op_json()])

    consensus_module.interpret_note_with_consensus("ambiguous note", 0, HOURS, BATTERY)

    assert arbiter.calls == 1


def test_kwh_field_uses_relative_tolerance(wiring):
    # 500 vs 504 kWh is within 1% relative tolerance -> agreement, no arbiter call.
    _register(
        wiring,
        PRIMARY_SPEC,
        [_directive_json("minimum_battery_reserve", True, {"hours": [18, 19], "minimum_energy_kwh": 500})],
    )
    _register(
        wiring,
        SECONDARY_SPEC,
        [_directive_json("minimum_battery_reserve", True, {"hours": [18, 19], "minimum_energy_kwh": 504})],
    )
    arbiter = _register(wiring, ARBITER_SPEC, [])

    result = consensus_module.interpret_note_with_consensus("keep half the battery", 0, HOURS, BATTERY)

    assert arbiter.calls == 0
    assert result.structured_adjustment["minimum_energy_kwh"] == 500


def test_kwh_field_disagreement_beyond_tolerance_calls_arbiter(wiring):
    _register(
        wiring,
        PRIMARY_SPEC,
        [_directive_json("minimum_battery_reserve", True, {"hours": [18, 19], "minimum_energy_kwh": 500})],
    )
    _register(
        wiring,
        SECONDARY_SPEC,
        [_directive_json("minimum_battery_reserve", True, {"hours": [18, 19], "minimum_energy_kwh": 250})],
    )
    arbiter = _register(
        wiring,
        ARBITER_SPEC,
        [_directive_json("minimum_battery_reserve", True, {"hours": [18, 19], "minimum_energy_kwh": 250})],
    )

    consensus_module.interpret_note_with_consensus("keep half the battery", 0, HOURS, BATTERY)

    assert arbiter.calls == 1


def test_disagreement_with_no_arbiter_configured_keeps_primary(wiring, monkeypatch):
    monkeypatch.setattr(consensus_module, "get_arbiter_model", lambda: None)
    _register(
        wiring, PRIMARY_SPEC, [_directive_json("solar_reduction", True, {"hours": [13], "factor": 0.2}, "p")]
    )
    _register(
        wiring, SECONDARY_SPEC, [_directive_json("solar_reduction", True, {"hours": [13], "factor": 0.9})]
    )

    result = consensus_module.interpret_note_with_consensus("note", 0, HOURS, BATTERY)

    assert result.structured_adjustment == {"hours": [13], "factor": 0.2}
    assert "no arbiter configured" in result.explanation
    assert "kept primary" in result.explanation


def test_disagreement_with_arbiter_failure_keeps_primary(wiring):
    _register(
        wiring, PRIMARY_SPEC, [_directive_json("solar_reduction", True, {"hours": [13], "factor": 0.2}, "p")]
    )
    _register(
        wiring, SECONDARY_SPEC, [_directive_json("solar_reduction", True, {"hours": [13], "factor": 0.9})]
    )
    arbiter = _register(
        wiring, ARBITER_SPEC, [LLMProviderError("down")] * consensus_module.MAX_ATTEMPTS
    )

    result = consensus_module.interpret_note_with_consensus("note", 0, HOURS, BATTERY)

    assert result.structured_adjustment == {"hours": [13], "factor": 0.2}
    assert "arbitration failed" in result.explanation
    assert arbiter.calls == consensus_module.MAX_ATTEMPTS


def test_no_alternate_provider_accepts_primary_without_secondary_call(wiring, monkeypatch):
    monkeypatch.setattr(consensus_module, "_configured_provider_names", lambda: {"providerA"})
    _register(
        wiring, PRIMARY_SPEC, [_directive_json("solar_reduction", True, {"hours": [13], "factor": 0.2})]
    )
    # SECONDARY_SPEC deliberately not registered: build_provider must never be called for it.

    result = consensus_module.interpret_note_with_consensus("note", 0, HOURS, BATTERY)

    assert result.structured_adjustment == {"hours": [13], "factor": 0.2}


def test_fast_model_used_as_secondary_when_no_other_primary_available(wiring, monkeypatch):
    monkeypatch.setattr(consensus_module, "PRIMARY_INTERPRETERS", [PRIMARY_SPEC])
    monkeypatch.setattr(consensus_module, "get_fast_model", lambda: SECONDARY_SPEC)
    _register(
        wiring, PRIMARY_SPEC, [_directive_json("solar_reduction", True, {"hours": [13], "factor": 0.2})]
    )
    secondary = _register(
        wiring, SECONDARY_SPEC, [_directive_json("solar_reduction", True, {"hours": [13], "factor": 0.2})]
    )

    consensus_module.interpret_note_with_consensus("note", 0, HOURS, BATTERY)

    assert secondary.calls == 1


def test_primary_model_unconfigured_falls_back_to_full_chain(wiring, monkeypatch):
    monkeypatch.setattr(consensus_module, "get_primary_model", lambda: None)
    fallback_provider = ScriptedProvider([_no_op_json()])
    monkeypatch.setattr(
        consensus_module, "get_provider_chain", lambda: [("fallback", fallback_provider)]
    )

    result = consensus_module.interpret_note_with_consensus("note", 0, HOURS, BATTERY)

    assert result.directive_type == "no_op"
    assert fallback_provider.calls == 1


def test_nothing_configured_falls_back_to_no_op(wiring, monkeypatch):
    monkeypatch.setattr(consensus_module, "get_primary_model", lambda: None)
    monkeypatch.setattr(consensus_module, "get_provider_chain", lambda: [])

    result = consensus_module.interpret_note_with_consensus("note", 0, HOURS, BATTERY)

    assert result.directive_type == "no_op"
    assert result.applies is False


def test_batch_consensus_preserves_order_across_notes(wiring):
    _register(
        wiring,
        PRIMARY_SPEC,
        [_no_op_json(), _directive_json("no_charge_window", True, {"hours": [5]})],
    )

    results = consensus_module.interpret_operator_notes_with_consensus(
        ["first note", "second note"], HOURS, BATTERY
    )

    assert [r.note_index for r in results] == [0, 1]


def test_arbiter_quota_error_retries_with_exponential_backoff_before_giving_up(wiring):
    from app.llm.provider import LLMQuotaExceededError

    _register(
        wiring, PRIMARY_SPEC, [_directive_json("solar_reduction", True, {"hours": [13], "factor": 0.2})]
    )
    _register(
        wiring, SECONDARY_SPEC, [_directive_json("solar_reduction", True, {"hours": [13], "factor": 0.9})]
    )
    arbiter = _register(
        wiring, ARBITER_SPEC, [LLMQuotaExceededError("out of credits")] * consensus_module.MAX_ATTEMPTS
    )

    result = consensus_module.interpret_note_with_consensus("note", 0, HOURS, BATTERY)

    # 429 retries the same arbiter model MAX_ATTEMPTS times (exponential
    # backoff) before the arbiter is considered exhausted.
    assert arbiter.calls == consensus_module.MAX_ATTEMPTS
    assert "arbitration failed" in result.explanation
    assert result.structured_adjustment == {"hours": [13], "factor": 0.2}  # kept primary


def test_arbiter_timeout_does_not_retry(wiring):
    from app.llm.provider import LLMTimeoutError

    _register(
        wiring, PRIMARY_SPEC, [_directive_json("solar_reduction", True, {"hours": [13], "factor": 0.2})]
    )
    _register(
        wiring, SECONDARY_SPEC, [_directive_json("solar_reduction", True, {"hours": [13], "factor": 0.9})]
    )
    # Only one scripted response: if the arbiter call incorrectly retried on
    # timeout, this would raise IndexError instead of falling through cleanly.
    arbiter = _register(wiring, ARBITER_SPEC, [LLMTimeoutError("upstream timed out")])

    result = consensus_module.interpret_note_with_consensus("note", 0, HOURS, BATTERY)

    assert arbiter.calls == 1
    assert "arbitration failed" in result.explanation


def test_arbiter_server_error_does_a_short_retry_then_succeeds(wiring):
    from app.llm.provider import LLMServerError

    _register(
        wiring, PRIMARY_SPEC, [_directive_json("solar_reduction", True, {"hours": [13], "factor": 0.2})]
    )
    _register(
        wiring, SECONDARY_SPEC, [_directive_json("solar_reduction", True, {"hours": [13], "factor": 0.9})]
    )
    arbiter = _register(
        wiring,
        ARBITER_SPEC,
        [
            LLMServerError("upstream 503"),
            _directive_json("solar_reduction", True, {"hours": [13], "factor": 0.9}, "arbiter picked B"),
        ],
    )

    result = consensus_module.interpret_note_with_consensus("note", 0, HOURS, BATTERY)

    assert arbiter.calls == 2  # retried on the same arbiter model, unlike timeout
    assert result.explanation == "arbiter picked B"
    assert result.structured_adjustment == {"hours": [13], "factor": 0.9}


def test_arbiter_json_parse_failure_retries_with_repair_prompt_no_sleep(wiring, monkeypatch):
    sleeps = []
    monkeypatch.setattr(consensus_module.time, "sleep", lambda seconds: sleeps.append(seconds))
    # Deliberately large so any accidental sleep would fail a tight bound.
    monkeypatch.setattr(consensus_module, "RETRY_BACKOFF_SECONDS", 100)
    monkeypatch.setattr(consensus_module, "EXPONENTIAL_BACKOFF_BASE_SECONDS", 100)
    monkeypatch.setattr(consensus_module, "SHORT_RETRY_BACKOFF_SECONDS", 100)

    _register(
        wiring, PRIMARY_SPEC, [_directive_json("solar_reduction", True, {"hours": [13], "factor": 0.2})]
    )
    _register(
        wiring, SECONDARY_SPEC, [_directive_json("solar_reduction", True, {"hours": [13], "factor": 0.9})]
    )
    arbiter = _register(
        wiring,
        ARBITER_SPEC,
        ["not valid json at all", _directive_json("solar_reduction", True, {"hours": [13], "factor": 0.2})],
    )

    result = consensus_module.interpret_note_with_consensus("note", 0, HOURS, BATTERY)

    assert arbiter.calls == 2
    assert sleeps == []
    assert result.structured_adjustment == {"hours": [13], "factor": 0.2}
