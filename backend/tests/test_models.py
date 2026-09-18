"""Validates ScenarioRequest and OptimizeResponse schemas against the public sample case pack."""

import json
from pathlib import Path

import pytest

from app.models.request import ScenarioRequest
from app.models.response import OptimizeResponse

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample_cases.json"


def _load_cases() -> list[dict]:
    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return data["cases"]


CASES = _load_cases()


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_input_validates_as_scenario_request(case: dict) -> None:
    ScenarioRequest.model_validate(case["input"])


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_expected_output_validates_as_optimize_response(case: dict) -> None:
    OptimizeResponse.model_validate(case["expected_output"])
