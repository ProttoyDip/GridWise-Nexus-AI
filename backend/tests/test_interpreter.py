"""Unit tests for the LLM operator-note interpreter, using fake providers
(no real network calls) to exercise success, retry, quota-failover, and
final-fallback paths."""

import json

import pytest

from app.llm import interpreter as interpreter_module
from app.llm.provider import LLMProviderError, LLMQuotaExceededError
from app.models.request import BatteryConfig, HourEntry

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


class ScriptedProvider:
    """Fake LLMProvider that plays back a scripted sequence of responses/errors."""

    def __init__(self, script: list[str | Exception]) -> None:
        self._script = list(script)
        self.calls = 0

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.calls += 1
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _no_op_json() -> str:
    return json.dumps(
        {
            "note_index": 999,  # deliberately wrong; interpreter must overwrite it
            "applies": False,
            "directive_type": "no_op",
            "structured_adjustment": None,
            "explanation": "irrelevant note",
        }
    )


def _solar_reduction_json() -> str:
    return json.dumps(
        {
            "note_index": 0,
            "applies": True,
            "directive_type": "solar_reduction",
            "structured_adjustment": {"hours": [13, 14], "factor": 0.2},
            "explanation": "80% reduction leaves 20% usable.",
        }
    )


def _chain(*providers: tuple[str, object]) -> list[tuple[str, object]]:
    return list(providers)


def test_successful_first_try_parses_and_overwrites_note_index():
    provider = ScriptedProvider([_solar_reduction_json()])
    result = interpreter_module._interpret_single_note(
        _chain(("openrouter", provider)), "note text", 3, HOURS, BATTERY
    )
    assert result.note_index == 3
    assert result.directive_type == "solar_reduction"
    assert result.structured_adjustment == {"hours": [13, 14], "factor": 0.2}
    assert provider.calls == 1


def test_retries_same_provider_after_malformed_json_then_succeeds(monkeypatch):
    monkeypatch.setattr(interpreter_module, "RETRY_BACKOFF_SECONDS", 0)
    provider = ScriptedProvider(["not json at all", _no_op_json()])
    result = interpreter_module._interpret_single_note(
        _chain(("openrouter", provider)), "note text", 1, HOURS, BATTERY
    )
    assert result.note_index == 1
    assert result.directive_type == "no_op"
    assert provider.calls == 2


def test_retries_same_provider_after_non_quota_error_then_succeeds(monkeypatch):
    monkeypatch.setattr(interpreter_module, "RETRY_BACKOFF_SECONDS", 0)
    provider = ScriptedProvider([LLMProviderError("timeout"), _no_op_json()])
    result = interpreter_module._interpret_single_note(
        _chain(("openrouter", provider)), "note text", 2, HOURS, BATTERY
    )
    assert result.note_index == 2
    assert provider.calls == 2


def test_quota_error_fails_over_to_next_provider_without_retrying_first(monkeypatch):
    monkeypatch.setattr(interpreter_module, "RETRY_BACKOFF_SECONDS", 0)
    exhausted = ScriptedProvider([LLMQuotaExceededError("out of credits")])
    backup = ScriptedProvider([_no_op_json()])
    result = interpreter_module._interpret_single_note(
        _chain(("openrouter", exhausted), ("nararouter", backup)), "note text", 0, HOURS, BATTERY
    )
    assert result.directive_type == "no_op"
    assert exhausted.calls == 1  # no retry on a provider that's out of quota
    assert backup.calls == 1


def test_falls_back_to_no_op_after_all_providers_exhausted(monkeypatch):
    monkeypatch.setattr(interpreter_module, "RETRY_BACKOFF_SECONDS", 0)
    first = ScriptedProvider([LLMProviderError("down")] * interpreter_module.MAX_ATTEMPTS)
    second = ScriptedProvider([LLMQuotaExceededError("out of credits")])
    result = interpreter_module._interpret_single_note(
        _chain(("openrouter", first), ("nararouter", second)), "note text", 0, HOURS, BATTERY
    )
    assert result.directive_type == "no_op"
    assert result.applies is False
    assert "Falling back to no_op" in result.explanation
    assert first.calls == interpreter_module.MAX_ATTEMPTS
    assert second.calls == 1


def test_strips_markdown_code_fences():
    fenced = "```json\n" + _no_op_json() + "\n```"
    provider = ScriptedProvider([fenced])
    result = interpreter_module._interpret_single_note(
        _chain(("openrouter", provider)), "note text", 0, HOURS, BATTERY
    )
    assert result.directive_type == "no_op"


@pytest.mark.parametrize("kind,adjustment", [
    ("solar_reduction", {"hours": 13, "factor": 0.2}),
    ("solar_reduction", {"hours": [13], "factor": "20%"}),
    ("minimum_battery_reserve", {"hours": [13], "minimum_energy_kwh": "half"}),
    ("max_grid_window", {"hours": [13], "max_grid_kwh": {"value": 5}}),
])
def test_malformed_adjustment_types_retry_instead_of_crashing(monkeypatch, kind, adjustment):
    monkeypatch.setattr(interpreter_module, "RETRY_BACKOFF_SECONDS", 0)
    malformed = json.dumps(dict(note_index=0, applies=True, directive_type=kind,
                                structured_adjustment=adjustment, explanation="Malformed model output."))
    provider = ScriptedProvider([malformed, _solar_reduction_json()])
    result = interpreter_module._interpret_single_note(
        _chain(("fixture", provider)), "note", 0, HOURS, BATTERY
    )
    assert result.directive_type == "solar_reduction"
    assert result.structured_adjustment == {"hours": [13, 14], "factor": 0.2}
    assert provider.calls == 2


def test_interpret_operator_notes_falls_back_when_no_provider_configured(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDERS", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    notes = ["Solar will drop to 20% from 1pm to 3pm.", "Unrelated announcement."]
    results = interpreter_module.interpret_operator_notes(notes, HOURS, BATTERY)
    assert [r.note_index for r in results] == [0, 1]
    assert all(r.directive_type == "no_op" and r.applies is False for r in results)


def test_interpret_operator_notes_preserves_order_and_indices(monkeypatch):
    monkeypatch.setattr(interpreter_module, "RETRY_BACKOFF_SECONDS", 0)

    class MultiNoteProvider:
        def __init__(self):
            self.calls = 0

        def complete(self, system_prompt, user_prompt):
            self.calls += 1
            return _solar_reduction_json() if self.calls == 1 else _no_op_json()

    monkeypatch.setattr(
        interpreter_module, "get_provider_chain", lambda: [("openrouter", MultiNoteProvider())]
    )
    notes = ["Solar will drop.", "Distractor note."]
    results = interpreter_module.interpret_operator_notes(notes, HOURS, BATTERY)
    assert [r.note_index for r in results] == [0, 1]
    assert results[0].directive_type == "solar_reduction"
    assert results[1].directive_type == "no_op"
