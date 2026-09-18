"""Unit tests for app.monitoring.logger: field validation, storage,
aggregation, and the "never a secret in here" safety contract.
"""

import threading

import pytest

from app.monitoring.logger import MonitoringLog, RequestMetrics, record_request


def _metrics(**overrides) -> RequestMetrics:
    defaults = dict(
        request_id="req-1",
        latency_seconds=0.5,
        models_used=("openrouter/model-a",),
        fallback_count=0,
        consensus_result=("accept",),
        optimizer_time_seconds=0.1,
        validation_failures=0,
    )
    defaults.update(overrides)
    return RequestMetrics(**defaults)


def test_valid_metrics_construct_cleanly():
    metrics = _metrics()
    assert metrics.request_id == "req-1"
    assert metrics.models_used == ("openrouter/model-a",)


def test_empty_request_id_rejected():
    with pytest.raises(ValueError):
        _metrics(request_id="")


@pytest.mark.parametrize("field", ["latency_seconds", "optimizer_time_seconds"])
def test_negative_timings_rejected(field):
    with pytest.raises(ValueError):
        _metrics(**{field: -0.1})


@pytest.mark.parametrize("field", ["fallback_count", "validation_failures"])
def test_negative_counts_rejected(field):
    with pytest.raises(ValueError):
        _metrics(**{field: -1})


@pytest.mark.parametrize(
    "bad_model_id",
    [
        "no-slash-here",
        "definitely-not-a-provider-model-id-and-has-no-slash-character",
        "provider/model with spaces",
        "",
        "provider/",
        "/model",
    ],
)
def test_malformed_model_ids_rejected(bad_model_id):
    with pytest.raises(ValueError):
        _metrics(models_used=(bad_model_id,))


def test_well_formed_model_ids_accepted():
    metrics = _metrics(models_used=("openrouter/deepseek/deepseek-v4-flash-0731:free",))
    assert metrics.models_used == ("openrouter/deepseek/deepseek-v4-flash-0731:free",)


def test_dataclass_has_no_field_for_prompts_or_secrets():
    """Structural guarantee: the fields that exist are exactly the ones
    the brief specified (plus a recorded_at timestamp), so there is no
    field to accidentally put a prompt, raw LLM output, or API key into."""
    field_names = set(RequestMetrics.__dataclass_fields__.keys())
    assert field_names == {
        "request_id",
        "latency_seconds",
        "models_used",
        "fallback_count",
        "consensus_result",
        "optimizer_time_seconds",
        "validation_failures",
        "recorded_at",
    }


def test_log_records_and_returns_recent():
    log = MonitoringLog()
    log.record(_metrics(request_id="a"))
    log.record(_metrics(request_id="b"))
    recent = log.recent(limit=10)
    assert [m.request_id for m in recent] == ["a", "b"]


def test_log_bounded_by_max_tracked_requests():
    log = MonitoringLog(max_tracked_requests=3)
    for i in range(10):
        log.record(_metrics(request_id=f"req-{i}"))
    recent = log.recent(limit=100)
    assert len(recent) == 3
    assert [m.request_id for m in recent] == ["req-7", "req-8", "req-9"]


def test_summary_aggregates_counts_and_average_latency():
    log = MonitoringLog()
    log.record(_metrics(request_id="a", latency_seconds=1.0, fallback_count=1, validation_failures=2))
    log.record(_metrics(request_id="b", latency_seconds=3.0, fallback_count=0, validation_failures=0))
    summary = log.summary()
    assert summary["total_requests"] == 2
    assert summary["total_fallbacks"] == 1
    assert summary["total_validation_failures"] == 2
    assert summary["recent_average_latency_seconds"] == pytest.approx(2.0)
    assert summary["tracked_in_memory"] == 2


def test_summary_on_empty_log_is_safe():
    log = MonitoringLog()
    summary = log.summary()
    assert summary["total_requests"] == 0
    assert summary["recent_average_latency_seconds"] == 0.0


def test_clear_resets_everything():
    log = MonitoringLog()
    log.record(_metrics())
    log.clear()
    assert log.recent() == []
    assert log.summary()["total_requests"] == 0


def test_module_level_record_request_wraps_default_log():
    from app.monitoring import logger as monitoring_logger

    monitoring_logger.clear()
    record_request(
        request_id="req-x",
        latency_seconds=0.2,
        models_used=("openrouter/model-a",),
        fallback_count=0,
        consensus_result=("accept",),
        optimizer_time_seconds=0.05,
        validation_failures=0,
    )
    assert monitoring_logger.summary()["total_requests"] == 1
    monitoring_logger.clear()


def test_thread_safety_under_concurrent_recording():
    log = MonitoringLog(max_tracked_requests=10000)

    def hammer(n):
        for i in range(200):
            log.record(_metrics(request_id=f"{n}-{i}"))

    threads = [threading.Thread(target=hammer, args=(t,)) for t in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert log.summary()["total_requests"] == 2000
