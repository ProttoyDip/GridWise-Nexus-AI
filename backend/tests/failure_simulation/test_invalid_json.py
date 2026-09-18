"""Scenario 1: LLM returns invalid JSON -> repair prompt or fallback.

The primary provider returns a malformed string first, then a valid
directive on the repair-prompt retry. We assert:

- the route returns HTTP 200,
- the final directive is a recognised ``no_op`` (the repair attempt
  returned a no_op so the schedule runs without altering constraints),
- the primary was called at least twice (initial + repair),
- one of the calls used a "repair" prompt (heuristic: ``user_prompt``
  contains the malformed output we returned first).
"""

from __future__ import annotations

from tests.failure_simulation.conftest import (
    PRIMARY_SPEC, SECONDARY_SPEC, build_scenario, no_op_json, register,
)


MALFORMED = "this is not valid JSON {{ broken: maybe?"


def test_invalid_json_triggers_repair_or_fallback(client, full_wiring):
    primary = register(
        full_wiring, PRIMARY_SPEC,
        # First call: garbage. Second call (on repair prompt): clean no_op.
        [MALFORMED, no_op_json()],
    )
    # Secondary exists so MEDIUM-risk promotion has somewhere to go,
    # but a successful repair should keep risk LOW.
    register(full_wiring, SECONDARY_SPEC, [])

    response = client.post(
        "/optimize-energy",
        json=build_scenario(["ok"]).model_dump(),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    directives = body["directive_interpretation"]
    assert len(directives) == 1
    assert directives[0]["directive_type"] == "no_op"
    assert directives[0]["applies"] is False
    # Schedule was still built and verified end-to-end.
    assert len(body["hourly_plan"]) == 24
    assert body["total_grid_kwh"] >= 0
    # Primary was hit at least twice: initial attempt + repair attempt.
    assert primary.calls >= 2, (
        f"expected at least 2 primary calls (initial + repair), got {primary.calls}"
    )
    # The repair branch sets ``attempt_prompt = build_repair_prompt(...)``
    # which embeds the malformed output verbatim. Assert at least one
    # call carried the malformed text in the user prompt.
    saw_repair = any(MALFORMED in user_prompt for _, user_prompt, _ in primary.history)
    assert saw_repair, "expected at least one repair-prompt retry to embed the malformed output"
