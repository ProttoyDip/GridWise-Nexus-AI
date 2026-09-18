"""Unit tests for the per-(provider, model) circuit breaker."""

import threading
import time

import pytest

from app.llm import circuit_breaker as breaker_module
from app.llm.circuit_breaker import (
    CircuitBreaker,
    CircuitState,
    FailureKind,
    classify_exception,
)
from app.llm.provider import LLMProviderError, LLMQuotaExceededError


class FakeProvider:
    def __init__(self, model: str):
        self.model = model


def test_circuit_closed_initially():
    cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=60)
    assert cb.is_open("openrouter", "model-a") is False
    assert cb.get_state("openrouter", "model-a") is None


def test_circuit_stays_closed_below_threshold():
    cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=60)
    cb.record_failure("openrouter", "model-a", FailureKind.RATE_LIMIT_429)
    cb.record_failure("openrouter", "model-a", FailureKind.TIMEOUT)
    assert cb.is_open("openrouter", "model-a") is False
    assert cb.get_state("openrouter", "model-a").failure_count == 2


def test_circuit_opens_after_threshold_reached():
    cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=60)
    for _ in range(3):
        cb.record_failure("openrouter", "model-a", FailureKind.SERVER_ERROR_5XX)
    assert cb.is_open("openrouter", "model-a") is True
    assert cb.should_skip("openrouter", "model-a") is True


@pytest.mark.parametrize("kind", [FailureKind.RATE_LIMIT_429, FailureKind.TIMEOUT, FailureKind.SERVER_ERROR_5XX])
def test_only_specified_failure_kinds_count(kind):
    cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=60)
    cb.record_failure("p", "m", kind)
    assert cb.is_open("p", "m") is True


def test_other_failure_kind_never_counted():
    cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=60)
    for _ in range(10):
        cb.record_failure("p", "m", FailureKind.OTHER)
    assert cb.is_open("p", "m") is False
    assert cb.get_state("p", "m") is None


def test_cooldown_expires_after_configured_duration():
    cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=0.1)
    cb.record_failure("p", "m", FailureKind.TIMEOUT)
    assert cb.is_open("p", "m") is True
    time.sleep(0.15)
    assert cb.is_open("p", "m") is False


def test_default_thresholds_match_spec():
    assert breaker_module.FAILURE_THRESHOLD == 3
    assert breaker_module.COOLDOWN_SECONDS == 60.0


def test_success_resets_failure_count():
    cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=60)
    cb.record_failure("p", "m", FailureKind.RATE_LIMIT_429)
    cb.record_failure("p", "m", FailureKind.RATE_LIMIT_429)
    cb.record_success("p", "m")
    state = cb.get_state("p", "m")
    assert state.failure_count == 0
    assert state.last_failure_time is None
    # a fresh failure after reset should not immediately open (starts from 0 again)
    cb.record_failure("p", "m", FailureKind.RATE_LIMIT_429)
    assert cb.is_open("p", "m") is False


def test_record_success_on_never_failed_pair_is_a_safe_noop():
    cb = CircuitBreaker()
    cb.record_success("p", "m")  # must not raise
    assert cb.get_state("p", "m") is None


def test_cooldown_remaining_seconds_decreases_and_hits_zero_after_expiry():
    cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=0.2)
    cb.record_failure("p", "m", FailureKind.TIMEOUT)
    remaining = cb.cooldown_remaining_seconds("p", "m")
    assert 0 < remaining <= 0.2
    time.sleep(0.25)
    assert cb.cooldown_remaining_seconds("p", "m") == 0.0


def test_different_models_tracked_independently():
    cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=60)
    cb.record_failure("openrouter", "model-a", FailureKind.RATE_LIMIT_429)
    assert cb.is_open("openrouter", "model-a") is True
    assert cb.is_open("openrouter", "model-b") is False
    assert cb.is_open("nararouter", "model-a") is False


def test_filter_chain_removes_open_entries_preserving_order():
    cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=60)
    chain = [
        ("openrouter", FakeProvider("model-a")),
        ("openrouter", FakeProvider("model-b")),
        ("nararouter", FakeProvider("model-c")),
    ]
    cb.record_failure("openrouter", "model-b", FailureKind.TIMEOUT)

    filtered = cb.filter_chain(chain)

    assert [(name, p.model) for name, p in filtered] == [
        ("openrouter", "model-a"),
        ("nararouter", "model-c"),
    ]


def test_filter_chain_returns_original_when_everything_open():
    cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=60)
    chain = [("openrouter", FakeProvider("model-a")), ("nararouter", FakeProvider("model-b"))]
    for name, provider in chain:
        cb.record_failure(name, provider.model, FailureKind.SERVER_ERROR_5XX)

    filtered = cb.filter_chain(chain)

    assert filtered == chain


def test_reset_single_pair():
    cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=60)
    cb.record_failure("p", "m1", FailureKind.RATE_LIMIT_429)
    cb.record_failure("p", "m2", FailureKind.RATE_LIMIT_429)
    cb.reset("p", "m1")
    assert cb.get_state("p", "m1") is None
    assert cb.get_state("p", "m2") is not None


def test_reset_all():
    cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=60)
    cb.record_failure("p", "m1", FailureKind.RATE_LIMIT_429)
    cb.record_failure("p", "m2", FailureKind.RATE_LIMIT_429)
    cb.reset()
    assert cb.snapshot() == []


def test_snapshot_returns_copies_not_live_references():
    cb = CircuitBreaker(failure_threshold=5, cooldown_seconds=60)
    cb.record_failure("p", "m", FailureKind.RATE_LIMIT_429)
    snap = cb.snapshot()
    assert len(snap) == 1
    snap[0].failure_count = 999  # mutate the copy
    assert cb.get_state("p", "m").failure_count == 1  # internal state untouched


def test_classify_exception_recognizes_quota_error():
    assert classify_exception(LLMQuotaExceededError("out of credits")) == FailureKind.RATE_LIMIT_429


def test_classify_exception_recognizes_5xx_from_message():
    exc = LLMProviderError("https://api.example.com/v1/chat/completions returned 503: overloaded")
    assert classify_exception(exc) == FailureKind.SERVER_ERROR_5XX


def test_classify_exception_recognizes_429_from_message():
    exc = LLMProviderError("https://api.example.com/v1/chat/completions returned 429: too many requests")
    assert classify_exception(exc) == FailureKind.RATE_LIMIT_429


def test_classify_exception_recognizes_other_4xx_as_other():
    exc = LLMProviderError("https://api.example.com/v1/chat/completions returned 403: forbidden")
    assert classify_exception(exc) == FailureKind.OTHER


def test_classify_exception_recognizes_timeout_from_message():
    exc = LLMProviderError("request failed: connection timeout after 30s")
    assert classify_exception(exc) == FailureKind.TIMEOUT


def test_classify_exception_recognizes_timeout_from_chained_cause():
    class FakeReadTimeout(Exception):
        pass

    try:
        try:
            raise FakeReadTimeout("upstream took too long")
        except FakeReadTimeout as inner:
            raise LLMProviderError("request failed") from inner
    except LLMProviderError as exc:
        assert classify_exception(exc) == FailureKind.TIMEOUT


def test_classify_exception_defaults_to_other_for_unrecognized_errors():
    assert classify_exception(ValueError("malformed JSON")) == FailureKind.OTHER


def test_record_failure_from_exception_uses_classifier():
    cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=60)
    cb.record_failure_from_exception("p", "m", LLMQuotaExceededError("out of credits"))
    assert cb.is_open("p", "m") is True


def test_record_failure_from_exception_ignores_non_counted_kind():
    cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=60)
    cb.record_failure_from_exception("p", "m", ValueError("bad json"))
    assert cb.is_open("p", "m") is False
    assert cb.get_state("p", "m") is None


def test_module_level_default_breaker_wrappers(monkeypatch):
    breaker_module.reset()  # isolate from any state other tests left behind
    breaker_module.record_failure("p", "m", FailureKind.RATE_LIMIT_429)
    breaker_module.record_failure("p", "m", FailureKind.RATE_LIMIT_429)
    breaker_module.record_failure("p", "m", FailureKind.RATE_LIMIT_429)
    assert breaker_module.is_open("p", "m") is True
    breaker_module.record_success("p", "m")
    assert breaker_module.is_open("p", "m") is False
    breaker_module.reset()


def test_thread_safety_under_concurrent_failures():
    cb = CircuitBreaker(failure_threshold=1000, cooldown_seconds=60)

    def hammer():
        for _ in range(200):
            cb.record_failure("p", "m", FailureKind.RATE_LIMIT_429)

    threads = [threading.Thread(target=hammer) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert cb.get_state("p", "m").failure_count == 2000
