"""TTL, concurrency, credential isolation, and API cache integration."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.llm import cache as cache_module, interpreter
from app.llm.cache import InterpretationCache, build_directive_context, interpretation_cache_key
from app.llm.confidence import ConfidenceDecision, ConfidenceMetadata
from app.llm.provider import LLMQuotaExceededError
from app.main import app
from app.models.request import ScenarioRequest
from app.models.response import DirectiveInterpretation
from test_solver import battery, forecast


def result():
    directive = DirectiveInterpretation(note_index=0, applies=False, directive_type="no_op",
                                         structured_adjustment=None, explanation="Unrelated note.")
    directive._confidence_metadata = ConfidenceMetadata(0.6, 1, ("test/model",), ConfidenceDecision.VERIFY)
    return directive


class Model:
    model = "model"

    def __init__(self):
        self.calls = 0
        self._api_key = "sensitive-test-api-key"
        self.lock = threading.Lock()

    def complete(self, system_prompt, user_prompt):
        with self.lock:
            self.calls += 1
        return result().model_dump_json()


@pytest.fixture
def clock_cache(monkeypatch):
    now = [0.0]
    cache = InterpretationCache(ttl_seconds=10, max_entries=3, clock=lambda: now[0])
    monkeypatch.setattr(interpreter, "interpretation_cache", cache)
    return cache, now


def key(note="note"):
    return interpretation_cache_key(note, {"context": "test"})


def interpret(model, note="note", index=0, hours=None, config=None):
    return interpreter._interpret_single_note([("test", model)], note, index,
                                               hours if hours is not None else forecast(),
                                               config if config is not None else battery())


def test_key_is_canonical_hash_of_note_and_context():
    first = interpretation_cache_key(" note ", {"battery": 10, "hours": [1, 2]})
    assert first == interpretation_cache_key("note", {"hours": [1, 2], "battery": 10})
    assert len(first) == 64 and all(char in "0123456789abcdef" for char in first)
    assert first != interpretation_cache_key("other", {"battery": 10, "hours": [1, 2]})
    assert interpretation_cache_key("ab", {"x": "c"}) != interpretation_cache_key("a", {"x": "bc"})


def test_repeated_note_avoids_llm_calls_and_preserves_confidence(clock_cache):
    model = Model()
    first, second = interpret(model), interpret(model)
    assert model.calls == 1
    assert first == second and first is not second
    assert second._confidence_metadata == first._confidence_metadata


def test_reused_note_receives_current_index(clock_cache):
    model = Model()
    first = interpret(model, index=2)
    second = interpret(model, index=0)
    assert model.calls == 1
    assert first.note_index == 2 and second.note_index == 0


def test_ttl_expires_exactly_at_deadline(clock_cache):
    _, now = clock_cache
    model = Model()
    interpret(model)
    now[0] = 9.999
    interpret(model)
    assert model.calls == 1
    now[0] = 10
    interpret(model)
    assert model.calls == 2


@pytest.mark.parametrize("changed", ["battery", "demand", "solar", "tariff", "model", "note"])
def test_directive_context_changes_force_fresh_interpretation(clock_cache, changed):
    model, hours, config = Model(), forecast(), battery()
    interpret(model, hours=hours, config=config)
    note = "note"
    if changed == "battery":
        config.capacity_kwh += 1
    elif changed == "demand":
        hours[0].demand_kwh += 1
    elif changed == "solar":
        hours[0].solar_kwh += 1
    elif changed == "tariff":
        hours[0].tariff_bdt_per_kwh += 1
    elif changed == "model":
        model.model = "different-model"
    else:
        note = "different note"
    interpret(model, note=note, hours=hours, config=config)
    assert model.calls == 2


def test_reordered_forecast_has_same_context(clock_cache):
    model, hours = Model(), forecast()
    interpret(model, hours=hours)
    hours.reverse()
    interpret(model, hours=hours)
    assert model.calls == 1


def test_date_change_invalidates_context(monkeypatch):
    class Day:
        value = "2026-09-18"
        @classmethod
        def today(cls):
            return cls()
        def isoformat(self):
            return self.value
    monkeypatch.setattr(cache_module, "date", Day)
    first = build_directive_context(forecast(), battery(), [("test", "model")])
    Day.value = "2026-09-19"
    second = build_directive_context(forecast(), battery(), [("test", "model")])
    assert interpretation_cache_key("note", first) != interpretation_cache_key("note", second)


def test_mutating_returned_adjustment_does_not_corrupt_cache(clock_cache):
    cache, _ = clock_cache
    directive = DirectiveInterpretation(note_index=0, applies=True, directive_type="solar_reduction",
                                        structured_adjustment={"hours": [1], "factor": 0.2}, explanation="Reduction.")
    directive._confidence_metadata = result()._confidence_metadata
    first = cache.get_or_compute(key(), lambda: directive)
    first.structured_adjustment["hours"].append(2)
    directive.structured_adjustment["factor"] = 0.8
    second = cache.get(key())
    assert second.structured_adjustment == {"hours": [1], "factor": 0.2}


def test_lru_capacity_eviction(clock_cache):
    cache, _ = clock_cache
    for note in ("one", "two", "three"):
        cache.get_or_compute(key(note), result)
    cache.get(key("one"))
    cache.get_or_compute(key("four"), result)
    assert cache.get(key("two")) is None
    assert cache.get(key("one")) is not None
    assert len(cache._entries) == 3


def test_clear_drops_cached_results(clock_cache):
    cache, _ = clock_cache
    cache.get_or_compute(key(), result)
    cache.clear()
    assert cache.get(key()) is None


def test_zero_ttl_disables_cache(monkeypatch):
    monkeypatch.setattr(interpreter, "interpretation_cache", InterpretationCache(ttl_seconds=0))
    model = Model()
    interpret(model)
    interpret(model)
    assert model.calls == 2


def test_concurrent_identical_misses_share_one_computation(clock_cache):
    cache, _ = clock_cache
    entered, release = threading.Event(), threading.Event()
    calls = []
    def compute():
        calls.append(1)
        entered.set()
        assert release.wait(5)
        return result()
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(cache.get_or_compute, key(), compute) for _ in range(4)]
        assert entered.wait(5)
        release.set()
        results = [future.result(timeout=5) for future in futures]
    assert len(calls) == 1
    assert len({id(value) for value in results}) == 4


def test_concurrent_duplicate_notes_get_distinct_indices(clock_cache, monkeypatch):
    model = Model()
    monkeypatch.setattr(interpreter, "get_provider_chain", lambda: [("test", model)])
    values = interpreter.interpret_operator_notes(["same", "same", "same"], forecast(), battery())
    assert model.calls == 1
    assert [value.note_index for value in values] == [0, 1, 2]


def test_compute_exception_does_not_leave_stuck_entry(clock_cache):
    cache, _ = clock_cache
    def fail():
        raise RuntimeError("failed compute")
    with pytest.raises(RuntimeError):
        cache.get_or_compute(key(), fail)
    assert cache.get(key()) is None
    assert not cache._inflight
    assert cache.get_or_compute(key(), result).directive_type == "no_op"


def test_failure_fallback_does_not_poison_cache(clock_cache, monkeypatch):
    monkeypatch.setattr(interpreter, "EXPONENTIAL_BACKOFF_BASE_SECONDS", 0)
    class RecoveringModel(Model):
        def complete(self, system_prompt, user_prompt):
            self.calls += 1
            if self.calls <= interpreter.MAX_ATTEMPTS:
                raise LLMQuotaExceededError("Temporary provider failure")
            return result().model_dump_json()
    model = RecoveringModel()
    first = interpret(model)
    assert first._confidence_metadata.agreement_count == 0
    second = interpret(model)
    assert second._confidence_metadata.agreement_count == 1
    interpret(model)
    assert model.calls == interpreter.MAX_ATTEMPTS + 1


def test_no_api_keys_or_provider_objects_are_stored(clock_cache):
    cache, _ = clock_cache
    model = Model()
    interpret(model)
    assert model._api_key not in repr(cache._entries)
    assert not cache._inflight
    model._api_key = "rotated-test-api-key"
    interpret(model)
    assert model.calls == 1
    assert model._api_key not in repr(cache._entries)


def test_repeated_api_requests_use_cache_without_response_fields(clock_cache, monkeypatch):
    from app.api import optimize
    monkeypatch.setattr(optimize, "interpret_operator_notes", interpreter.interpret_operator_notes)
    model = Model()
    monkeypatch.setattr(interpreter, "get_provider_chain", lambda: [("test", model)])
    scenario = ScenarioRequest(scenario_id="first", operator_notes=["same note"], hours=forecast(), battery=battery())
    client = TestClient(app)
    first = client.post("/optimize-energy", json=scenario.model_dump())
    scenario.scenario_id = "different-id"
    second = client.post("/optimize-energy", json=scenario.model_dump())
    assert first.status_code == second.status_code == 200
    assert model.calls == 1
    assert second.json()["scenario_id"] == "different-id"
    assert set(first.json()) == set(second.json())
    assert "cache" not in second.text and "confidence_score" not in second.text


@pytest.mark.parametrize("settings", [
    {"ttl_seconds": -1}, {"ttl_seconds": float("inf")}, {"ttl_seconds": float("nan")},
    {"max_entries": 0}, {"max_entries": True}, {"max_entries": 1.5},
])
def test_invalid_cache_configuration(settings):
    with pytest.raises(ValueError):
        InterpretationCache(**settings)
