"""Scenario 2: primary model returns 429 -> retry on same model, then succeed.

The primary provider raises ``LLMQuotaExceededError`` on the first
attempt. The interpreter's quota branch retries with exponential
backoff (zeroed in ``full_wiring``) on the same provider/model; on the
second attempt we return a clean ``no_op``. We assert:

- the route returns HTTP 200,
- the final directive is a recognised ``no_op``,
- the primary was called at least twice (initial 429 + retry success),
- the secondary was not consulted (the retry path stays on the same
  model rather than failing over).
"""

from __future__ import annotations

from app.llm.provider import LLMQuotaExceededError

from tests.failure_simulation.conftest import (
    PRIMARY_SPEC, SECONDARY_SPEC, build_scenario, no_op_json, register,
)


def test_primary_429_retries_then_succeeds(client, full_wiring):
    primary = register(
        full_wiring, PRIMARY_SPEC,
        [
            LLMQuotaExceededError("synthetic 429 from primary"),
            no_op_json(),
        ],
    )
    secondary = register(full_wiring, SECONDARY_SPEC, [])

    response = client.post(
        "/optimize-energy",
        json=build_scenario(["anything"]).model_dump(),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    directives = body["directive_interpretation"]
    assert len(directives) == 1
    assert directives[0]["directive_type"] == "no_op"
    assert body["total_grid_kwh"] >= 0
    # At least two primary calls: the 429 + the retry that succeeded.
    assert primary.calls >= 2, (
        f"expected the same primary to be retried after 429, got {primary.calls} calls"
    )
    # Secondary should not have been consulted on this note.
    assert secondary.calls == 0
