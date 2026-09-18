"""Scenario 5: LLM returns an unknown directive_type -> guardrail substitution.

The primary provider returns a JSON object whose ``directive_type``
is ``"hack_the_planet"`` — not in the whitelist enforced by
``app.guardrails.validator``. ``validate_directive_interpretation``
must replace the offending entry with a safe ``no_op`` (the
guardrails contract is fail-safe, not fail-loud). The route still
returns HTTP 200 with a verifiable 24-hour schedule.

We assert:
- response status is 200,
- the surviving directive is ``no_op`` with ``applies=False``,
- the schedule was still built and verified end-to-end.
"""

from __future__ import annotations

from tests.failure_simulation.conftest import (
    PRIMARY_SPEC, build_scenario, directive_json, register,
)


def test_unknown_directive_type_is_replaced_with_no_op(client, full_wiring):
    register(
        full_wiring, PRIMARY_SPEC,
        [directive_json("hack_the_planet", True, {"hours": [0]})],
    )

    response = client.post(
        "/optimize-energy",
        json=build_scenario(["sneaky"]).model_dump(),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    directives = body["directive_interpretation"]
    assert len(directives) == 1
    assert directives[0]["directive_type"] == "no_op"
    assert directives[0]["applies"] is False
    assert len(body["hourly_plan"]) == 24


def test_numeric_out_of_bounds_directive_is_replaced_with_no_op(client, full_wiring):
    """Out-of-range numeric fields also trip the guardrail (factor=2.0 is
    outside [0, 1]). Same fail-safe contract: substituted with no_op."""
    register(
        full_wiring, PRIMARY_SPEC,
        [directive_json("solar_reduction", True, {"hours": [13], "factor": 2.0})],
    )

    response = client.post(
        "/optimize-energy",
        json=build_scenario(["absurd"]).model_dump(),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    directives = body["directive_interpretation"]
    assert len(directives) == 1
    assert directives[0]["directive_type"] == "no_op"
