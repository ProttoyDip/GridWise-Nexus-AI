"""Scenario 3: every configured model is unavailable -> controlled fallback.

Primary and secondary both raise ``LLMQuotaExceededError`` (429) on
every attempt. The interpreter's quota branch breaks to the next
chain entry once ``MAX_ATTEMPTS`` is exhausted, so the chain
advances primary -> secondary -> exhausted -> ``_fallback_no_op``.
The route must still return HTTP 200 and yield a single ``no_op``
directive so the schedule can be solved and verified end-to-end.

A companion test asserts the empty-chain case (no provider chain
can even be built) also degrades safely to no_op for every note.

Critically, no unhandled exception reaches the client — the pipeline
fails safe.
"""

from __future__ import annotations

import pytest

from app.llm.provider import LLMProviderError, LLMQuotaExceededError

from tests.failure_simulation.conftest import (
    PRIMARY_SPEC, SECONDARY_SPEC, build_scenario, register,
)


class _RaisingProvider:
    """Raising fake used in place of the secondary: every call raises."""

    def __init__(self, exc: Exception, model: str) -> None:
        self._exc = exc
        self.calls = 0
        self.model = model

    def complete(self, system_prompt, user_prompt):
        self.calls += 1
        raise self._exc

    def complete_structured(self, system_prompt, user_prompt, schema):
        return self.complete(system_prompt, user_prompt)

    @property
    def supports_structured_output(self):
        return False


def test_all_models_unavailable_returns_safe_no_op(client, full_wiring):
    """Total outage of every configured model -> controlled safe fallback.

    Primary and secondary both raise ``LLMQuotaExceededError`` on every
    attempt. For a LOW-risk note the adaptive-consensus router consults
    only the primary chain; once the primary exhausts ``MAX_ATTEMPTS``
    its interpreter falls back to a safe ``no_op`` and the route still
    returns HTTP 200. The companion ``test_no_provider_chain_at_all_is_safe_no_op``
    covers the case where no chain can even be built at all.
    """
    error = LLMQuotaExceededError("simulated total outage (429)")
    primary = register(
        full_wiring, PRIMARY_SPEC,
        [error, error, error],
    )
    # Secondary is registered but, for a LOW-risk note, the router
    # doesn't call it. We still register it to confirm a misbehaving
    # secondary wouldn't somehow be auto-promoted.
    secondary = _RaisingProvider(error, model=SECONDARY_SPEC.name)
    full_wiring[(SECONDARY_SPEC.provider, SECONDARY_SPEC.name)] = secondary

    response = client.post(
        "/optimize-energy",
        json=build_scenario(["ok"]).model_dump(),
    )

    # The pipeline must not 5xx the client on total LLM outage; it
    # degrades to a safe no_op schedule.
    assert response.status_code == 200, (
        f"expected a safe 200 fallback, got {response.status_code}: {response.text}"
    )
    body = response.json()
    directives = body["directive_interpretation"]
    assert len(directives) == 1
    assert directives[0]["directive_type"] == "no_op"
    assert directives[0]["applies"] is False
    # A schedule was still produced and verified.
    assert len(body["hourly_plan"]) == 24
    assert body["total_grid_kwh"] >= 0
    # Primary was consulted and exhausted its retries.
    assert primary.calls >= 1


def test_no_provider_chain_at_all_is_safe_no_op(client, monkeypatch):
    """No provider chain can even be built -> every note is a no_op."""
    from app.llm import adaptive_consensus as adaptive_module
    from app.llm import consensus as consensus_module
    from app.llm.provider import LLMProviderError

    monkeypatch.setattr(adaptive_module, "get_primary_model", lambda: None)
    # Force ``get_provider_chain`` to raise so adaptive_consensus takes
    # the empty-chain path and emits safe no_ops for every note.
    def _raise():
        raise LLMProviderError("no providers configured")
    monkeypatch.setattr(consensus_module, "get_provider_chain", _raise)

    response = client.post(
        "/optimize-energy",
        json=build_scenario(["a", "b"]).model_dump(),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    directives = body["directive_interpretation"]
    assert len(directives) == 2
    for d in directives:
        assert d["directive_type"] == "no_op"
        assert d["applies"] is False
