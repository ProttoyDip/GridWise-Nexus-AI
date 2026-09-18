"""Optional real-provider checks are explicit so normal tests remain offline."""

import pytest


@pytest.fixture(autouse=True)
def isolated_interpretation_cache(tmp_path, monkeypatch):
    """Keep tests independent while allowing repeat requests within a test."""
    from app.llm import circuit_breaker
    from app.llm.cache import interpretation_cache
    from app.optimizer.cache import optimization_cache
    from app.optimizer.warm_start import warm_start_store
    from app.memory import store
    monkeypatch.setattr(store, "directive_memory", store.DirectiveMemory(tmp_path / "memory.json"))
    interpretation_cache.clear()
    optimization_cache.clear()
    warm_start_store.clear()
    circuit_breaker.reset()
    yield
    interpretation_cache.clear()
    optimization_cache.clear()
    warm_start_store.clear()
    circuit_breaker.reset()


def pytest_addoption(parser):
    parser.addoption("--live-llm", action="store_true", default=False,
                     help="Run public sample cases against configured real LLM providers")


@pytest.fixture
def live_llm_environment(request, monkeypatch):
    if not request.config.getoption("--live-llm"):
        pytest.skip("Use --live-llm to test real provider interpretation")
    from pathlib import Path
    from dotenv import dotenv_values

    # Load credentials for this test only, without printing them or replacing
    # explicitly configured process environment variables.
    import os
    for key, value in dotenv_values(Path(__file__).parents[1] / ".env").items():
        if value and key not in os.environ:
            monkeypatch.setenv(key, value)
