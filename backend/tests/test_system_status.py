"""Tests for GET /system/status: model availability, optimizer status,
and that no secrets are ever present in the response.
"""

import json

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _clear_llm_env(monkeypatch):
    import os

    for key in list(os.environ):
        if key.startswith("LLM_"):
            monkeypatch.delenv(key, raising=False)


def test_unconfigured_reports_unhealthy_with_reason(monkeypatch):
    _clear_llm_env(monkeypatch)
    response = client.get("/system/status")
    assert response.status_code == 200
    body = response.json()
    assert body["healthy"] is False
    assert body["model_availability"]["configured"] is False
    assert "reason" in body["model_availability"]


def test_configured_reports_models_and_no_secrets(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDERS", "openrouter")
    monkeypatch.setenv("LLM_API_KEY_OPENROUTER", "sk-or-v1-super-secret-value-should-never-appear")
    monkeypatch.setenv("LLM_MODELS_OPENROUTER", "model-a:free,model-b:free")

    response = client.get("/system/status")
    body = response.json()
    raw = json.dumps(body)

    assert body["model_availability"]["configured"] is True
    model_ids = {m["model"] for m in body["model_availability"]["models"]}
    assert model_ids == {"model-a:free", "model-b:free"}
    for m in body["model_availability"]["models"]:
        assert set(m.keys()) == {"provider", "model", "circuit_open", "failure_count"}

    assert "sk-or-v1-super-secret-value-should-never-appear" not in raw
    assert "LLM_API_KEY" not in raw


def test_optimizer_status_reports_cbc_available():
    response = client.get("/system/status")
    body = response.json()
    assert body["optimizer_status"]["solver"] == "CBC"
    assert isinstance(body["optimizer_status"]["available"], bool)


def test_healthy_true_requires_both_providers_and_optimizer(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDERS", "openrouter")
    monkeypatch.setenv("LLM_API_KEY_OPENROUTER", "fake-key")
    monkeypatch.setenv("LLM_MODELS_OPENROUTER", "model-a:free")

    response = client.get("/system/status")
    body = response.json()
    assert body["healthy"] == (
        body["model_availability"]["configured"] and body["optimizer_status"]["available"]
    )


def test_circuit_breaker_state_is_reflected(monkeypatch):
    from app.llm import circuit_breaker

    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDERS", "openrouter")
    monkeypatch.setenv("LLM_API_KEY_OPENROUTER", "fake-key")
    monkeypatch.setenv("LLM_MODELS_OPENROUTER", "model-a:free")

    circuit_breaker.reset()
    for _ in range(circuit_breaker.FAILURE_THRESHOLD):
        circuit_breaker.record_failure(
            "openrouter", "model-a:free", circuit_breaker.FailureKind.RATE_LIMIT_429
        )

    response = client.get("/system/status")
    body = response.json()
    entry = next(m for m in body["model_availability"]["models"] if m["model"] == "model-a:free")
    assert entry["circuit_open"] is True
    assert entry["failure_count"] == circuit_breaker.FAILURE_THRESHOLD

    circuit_breaker.reset()


def test_monitoring_summary_present_and_shaped():
    response = client.get("/system/status")
    body = response.json()
    monitoring = body["monitoring"]
    assert set(monitoring.keys()) == {
        "total_requests",
        "total_fallbacks",
        "total_validation_failures",
        "recent_average_latency_seconds",
        "tracked_in_memory",
    }


def test_response_never_contains_env_var_names_for_keys():
    response = client.get("/system/status")
    raw = json.dumps(response.json())
    for forbidden in ("LLM_API_KEY", "api_key", "apiKey", "Authorization", "Bearer"):
        assert forbidden not in raw
