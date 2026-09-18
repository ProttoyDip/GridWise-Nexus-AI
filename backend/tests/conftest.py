"""Optional real-provider checks are explicit so normal tests remain offline."""

import pytest


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

