"""Unit tests for the LLM operator-note interpreter, using fake providers
(no real network calls) to exercise success, retry, quota-failover, and
final-fallback paths."""

import json
import re

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


def test_quota_error_retries_same_provider_with_exponential_backoff_before_failing_over(monkeypatch):
    monkeypatch.setattr(interpreter_module, "RETRY_BACKOFF_SECONDS", 0)
    monkeypatch.setattr(interpreter_module, "EXPONENTIAL_BACKOFF_BASE_SECONDS", 0)
    exhausted = ScriptedProvider([LLMQuotaExceededError("out of credits")] * interpreter_module.MAX_ATTEMPTS)
    backup = ScriptedProvider([_no_op_json()])
    result = interpreter_module._interpret_single_note_uncached(
        _chain(("openrouter", exhausted), ("nararouter", backup)), "note text", 0, HOURS, BATTERY
    )
    assert result.directive_type == "no_op"
    # 429 gets exponential-backoff retries on the same provider before failing over.
    assert exhausted.calls == interpreter_module.MAX_ATTEMPTS
    assert backup.calls == 1


def test_quota_error_backoff_durations_are_exponential(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(interpreter_module.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(interpreter_module, "EXPONENTIAL_BACKOFF_BASE_SECONDS", 1.0)
    provider = ScriptedProvider([LLMQuotaExceededError("out of credits")] * interpreter_module.MAX_ATTEMPTS)
    interpreter_module._interpret_single_note_uncached(
        _chain(("openrouter", provider)), "note text", 0, HOURS, BATTERY
    )
    # attempt 1 fails -> sleep 1*2^0=1; attempt 2 fails -> sleep 1*2^1=2; attempt 3
    # fails (last attempt) -> no further sleep.
    assert sleeps == [1.0, 2.0]


def test_timeout_error_moves_to_next_model_immediately_without_retry(monkeypatch):
    from app.llm.provider import LLMTimeoutError

    monkeypatch.setattr(interpreter_module, "RETRY_BACKOFF_SECONDS", 0)
    timed_out = ScriptedProvider([LLMTimeoutError("connection timed out")])
    backup = ScriptedProvider([_no_op_json()])
    result = interpreter_module._interpret_single_note_uncached(
        _chain(("openrouter", timed_out), ("nararouter", backup)), "note text", 0, HOURS, BATTERY
    )
    assert result.directive_type == "no_op"
    assert timed_out.calls == 1  # no retry at all on a provider that timed out
    assert backup.calls == 1


def test_timeout_error_never_sleeps(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(interpreter_module.time, "sleep", lambda seconds: sleeps.append(seconds))
    from app.llm.provider import LLMTimeoutError

    provider = ScriptedProvider([LLMTimeoutError("connection timed out")])
    backup = ScriptedProvider([_no_op_json()])
    interpreter_module._interpret_single_note_uncached(
        _chain(("openrouter", provider), ("nararouter", backup)), "note text", 0, HOURS, BATTERY
    )
    assert sleeps == []


def test_server_error_5xx_does_a_short_flat_retry(monkeypatch):
    from app.llm.provider import LLMServerError

    monkeypatch.setattr(interpreter_module, "SHORT_RETRY_BACKOFF_SECONDS", 0)
    provider = ScriptedProvider([LLMServerError("upstream 503"), _no_op_json()])
    result = interpreter_module._interpret_single_note_uncached(
        _chain(("openrouter", provider)), "note text", 0, HOURS, BATTERY
    )
    assert result.directive_type == "no_op"
    assert provider.calls == 2  # retried on the same provider, unlike timeout


def test_server_error_5xx_backoff_is_short_and_flat_not_exponential(monkeypatch):
    from app.llm.provider import LLMServerError

    sleeps: list[float] = []
    monkeypatch.setattr(interpreter_module.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(interpreter_module, "SHORT_RETRY_BACKOFF_SECONDS", 0.5)
    provider = ScriptedProvider([LLMServerError("upstream 503")] * interpreter_module.MAX_ATTEMPTS)
    interpreter_module._interpret_single_note_uncached(
        _chain(("openrouter", provider)), "note text", 0, HOURS, BATTERY
    )
    # Flat 0.5s each time (not growing like the 429 exponential case), and no
    # sleep after the final attempt since there's nothing left to wait for.
    assert sleeps == [0.5, 0.5]


def test_json_parse_failure_never_sleeps_even_with_large_configured_backoffs(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(interpreter_module.time, "sleep", lambda seconds: sleeps.append(seconds))
    # Deliberately large, so any accidental sleep would be obvious/slow.
    monkeypatch.setattr(interpreter_module, "RETRY_BACKOFF_SECONDS", 100)
    monkeypatch.setattr(interpreter_module, "EXPONENTIAL_BACKOFF_BASE_SECONDS", 100)
    monkeypatch.setattr(interpreter_module, "SHORT_RETRY_BACKOFF_SECONDS", 100)
    provider = ScriptedProvider(["not json at all", _no_op_json()])
    result = interpreter_module._interpret_single_note_uncached(
        _chain(("openrouter", provider)), "note text", 0, HOURS, BATTERY
    )
    assert result.directive_type == "no_op"
    assert provider.calls == 2
    assert sleeps == []


def test_json_parse_failure_retries_with_a_repair_prompt(monkeypatch):
    from app.llm.json_parser import LLMOutputError

    monkeypatch.setattr(interpreter_module, "RETRY_BACKOFF_SECONDS", 0)
    prompts_seen: list[str] = []

    class RecordingProvider:
        def __init__(self, script):
            self._script = list(script)

        def complete(self, system_prompt, user_prompt):
            prompts_seen.append(user_prompt)
            item = self._script.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

    provider = RecordingProvider(["this is not json", _no_op_json()])
    interpreter_module._interpret_single_note_uncached(
        _chain(("openrouter", provider)), "note text", 0, HOURS, BATTERY
    )
    assert len(prompts_seen) == 2
    # The second attempt's prompt must differ from the first (a repair prompt
    # built from the failed output), not an identical resend.
    assert prompts_seen[1] != prompts_seen[0]
    assert "REPAIR" in prompts_seen[1] or "repair" in prompts_seen[1].lower()


def test_falls_back_to_no_op_after_all_providers_exhausted(monkeypatch):
    monkeypatch.setattr(interpreter_module, "RETRY_BACKOFF_SECONDS", 0)
    monkeypatch.setattr(interpreter_module, "EXPONENTIAL_BACKOFF_BASE_SECONDS", 0)
    first = ScriptedProvider([LLMProviderError("down")] * interpreter_module.MAX_ATTEMPTS)
    second = ScriptedProvider([LLMQuotaExceededError("out of credits")] * interpreter_module.MAX_ATTEMPTS)
    result = interpreter_module._interpret_single_note_uncached(
        _chain(("openrouter", first), ("nararouter", second)), "note text", 0, HOURS, BATTERY
    )
    assert result.directive_type == "no_op"
    assert result.applies is False
    assert "Falling back to no_op" in result.explanation
    assert first.calls == interpreter_module.MAX_ATTEMPTS
    assert second.calls == interpreter_module.MAX_ATTEMPTS


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
        """Keys its response off the note_index embedded in the prompt
        text (not call order), since notes now run concurrently and
        which thread's complete() lands first is not guaranteed."""

        def __init__(self):
            self.calls = 0

        def complete(self, system_prompt, user_prompt):
            self.calls += 1
            note_index = int(re.search(r"Operator note \(index (\d+)\)", user_prompt).group(1))
            return _solar_reduction_json() if note_index == 0 else _no_op_json()

    monkeypatch.setattr(
        interpreter_module, "get_provider_chain", lambda: [("openrouter", MultiNoteProvider())]
    )
    notes = ["Solar will drop.", "Distractor note."]
    results = interpreter_module.interpret_operator_notes(notes, HOURS, BATTERY)
    assert [r.note_index for r in results] == [0, 1]
    assert results[0].directive_type == "solar_reduction"
    assert results[1].directive_type == "no_op"


def test_open_circuit_is_skipped_without_being_called(monkeypatch):
    from app.llm import circuit_breaker
    from app.llm.circuit_breaker import FailureKind

    monkeypatch.setattr(interpreter_module, "RETRY_BACKOFF_SECONDS", 0)
    dead = ScriptedProvider([_no_op_json()])  # would succeed if called, but must not be
    backup = ScriptedProvider([_no_op_json()])

    for _ in range(circuit_breaker.FAILURE_THRESHOLD):
        circuit_breaker.record_failure("openrouter", "openrouter", FailureKind.RATE_LIMIT_429)

    result = interpreter_module._interpret_single_note_uncached(
        _chain(("openrouter", dead), ("nararouter", backup)), "note text", 0, HOURS, BATTERY
    )
    assert result.directive_type == "no_op"
    assert dead.calls == 0  # skipped: circuit was open
    assert backup.calls == 1


def test_success_resets_the_circuit(monkeypatch):
    from app.llm import circuit_breaker
    from app.llm.circuit_breaker import FailureKind

    monkeypatch.setattr(interpreter_module, "RETRY_BACKOFF_SECONDS", 0)
    circuit_breaker.record_failure("openrouter", "openrouter", FailureKind.RATE_LIMIT_429)
    circuit_breaker.record_failure("openrouter", "openrouter", FailureKind.RATE_LIMIT_429)
    assert circuit_breaker.is_open("openrouter", "openrouter") is False  # below threshold still

    provider = ScriptedProvider([_no_op_json()])
    interpreter_module._interpret_single_note_uncached(
        _chain(("openrouter", provider)), "note text", 0, HOURS, BATTERY
    )
    state = circuit_breaker.get_state("openrouter", "openrouter")
    assert state.failure_count == 0  # success cleared the prior 2 failures


def test_per_note_deadline_stops_starting_new_models(monkeypatch):
    monkeypatch.setattr(interpreter_module, "RETRY_BACKOFF_SECONDS", 0)
    monkeypatch.setattr(interpreter_module, "MAX_TOTAL_SECONDS_PER_NOTE", 0)  # already expired

    never_called = ScriptedProvider([_no_op_json()])
    result = interpreter_module._interpret_single_note_uncached(
        _chain(("openrouter", never_called)), "note text", 0, HOURS, BATTERY
    )
    assert result.directive_type == "no_op"
    assert never_called.calls == 0
