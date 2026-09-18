"""End-to-end failure-simulation tests for the optimize-energy stack.

Each scenario exercises the full request path (interpret -> guardrail ->
solve -> verify) via FastAPI's TestClient with scripted fake providers.
The companion harness module produces a PASS/FAIL report from these
tests.
"""
