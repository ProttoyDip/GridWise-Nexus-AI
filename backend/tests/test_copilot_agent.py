"""Orchestration tests for app.copilot.copilot_agent, with app.copilot.tool_manager
monkeypatched so no real LLM/optimizer/simulator calls happen — this file
tests dispatch and memory wiring, not the underlying GridWise services
(those have their own test suites)."""

import pytest

from app.copilot import copilot_agent, tool_manager
from app.copilot.conversation_memory import conversation_memory
from app.models.response import DirectiveInterpretation, HourlyPlanEntry

FAKE_DIRECTIVE = DirectiveInterpretation(
    note_index=0,
    applies=True,
    directive_type="no_charge_window",
    structured_adjustment={"hours": [2, 3]},
    explanation="Test directive.",
)
FAKE_PLAN = [
    HourlyPlanEntry(
        hour=h,
        grid_kwh=10.0,
        solar_used_kwh=0.0,
        battery_action="charge" if h == 2 else "idle",
        battery_kwh=5.0 if h == 2 else 0.0,
        battery_energy_after_kwh=50.0,
    )
    for h in range(24)
]


@pytest.fixture(autouse=True)
def clean_memory():
    conversation_memory.clear()
    yield
    conversation_memory.clear()


def _fake_optimize_ok(operator_note, context):
    return {
        "ok": True,
        "scenario": "SCENARIO",
        "directives": [FAKE_DIRECTIVE],
        "plan": FAKE_PLAN,
        "total_cost_bdt": 1234.5,
        "total_grid_kwh": 500.0,
        "peak_grid_kwh": 80.0,
        "plan_summary": "Test plan summary.",
    }


def test_optimization_request_calls_optimize_tool_and_stores_memory(monkeypatch):
    calls = []
    monkeypatch.setattr(tool_manager, "optimize_energy", lambda note, ctx: (calls.append((note, ctx)), _fake_optimize_ok(note, ctx))[1])

    result = copilot_agent.handle_message("sess-1", "Optimize tomorrow's energy", context={"hours": []})

    assert result["intent"] == "OPTIMIZATION_REQUEST"
    assert result["action_taken"] == "optimize_energy"
    assert result["visual_data"]["kind"] == "optimization_result"
    assert calls == [("Optimize tomorrow's energy", {"hours": []})]

    session = conversation_memory.get_session("sess-1")
    assert session.last_scenario == "SCENARIO"
    assert session.last_directives == [FAKE_DIRECTIVE]


def test_explanation_request_uses_remembered_optimization(monkeypatch):
    monkeypatch.setattr(tool_manager, "optimize_energy", lambda note, ctx: _fake_optimize_ok(note, ctx))
    captured = {}

    def fake_explain(scenario, directives, plan):
        captured["args"] = (scenario, directives, plan)
        return {"ok": True, "explanation": {"battery_reasons": ["Charged at low tariff."], "constraint_explanations": []}}

    monkeypatch.setattr(tool_manager, "explain_schedule", fake_explain)

    copilot_agent.handle_message("sess-2", "Optimize tomorrow", context=None)
    result = copilot_agent.handle_message("sess-2", "Why did you charge at night?", context=None)

    assert result["intent"] == "EXPLANATION_REQUEST"
    assert result["action_taken"] == "explain_schedule"
    assert captured["args"] == ("SCENARIO", [FAKE_DIRECTIVE], FAKE_PLAN)


def test_explanation_request_without_prior_optimization_is_graceful():
    result = copilot_agent.handle_message("sess-fresh", "Why did you charge at night?")
    assert result["intent"] == "EXPLANATION_REQUEST"
    assert result["action_taken"] == "explain_schedule_unavailable"
    assert "optimize" in result["reply"].lower()


def test_simulation_request_calls_simulate_tool(monkeypatch):
    captured = {}

    def fake_simulate(note, ctx, scenario, directives, plan):
        captured["args"] = (note, scenario, directives, plan)
        return {
            "ok": True,
            "result": {"outcomes": [{"name": "nominal", "feasible": True, "cost_bdt": 100, "grid_kwh": 50}], "risk": {}},
            "scenario": "SIM_SCENARIO",
            "directives": ["SIM_DIRECTIVE"],
            "plan": ["SIM_PLAN"],
        }

    monkeypatch.setattr(tool_manager, "simulate_scenario", fake_simulate)

    result = copilot_agent.handle_message("sess-3", "What if solar drops by 50%?")

    assert result["intent"] == "SIMULATION_REQUEST"
    assert result["action_taken"] == "simulate_scenario"
    assert captured["args"][0] == "What if solar drops by 50%?"
    session = conversation_memory.get_session("sess-3")
    assert session.last_scenario == "SIM_SCENARIO"


def test_status_request_calls_status_tool(monkeypatch):
    monkeypatch.setattr(
        tool_manager,
        "system_status",
        lambda: {
            "ok": True,
            "status": {
                "healthy": True,
                "model_availability": {"models": [{"circuit_open": False}]},
                "optimizer_status": {"available": True, "solver": "CBC"},
            },
        },
    )

    result = copilot_agent.handle_message("sess-4", "Is the system running?")

    assert result["intent"] == "STATUS_REQUEST"
    assert result["action_taken"] == "system_status"
    assert result["visual_data"]["healthy"] is True


def test_general_query_does_not_call_any_tool(monkeypatch):
    for name in ("optimize_energy", "explain_schedule", "simulate_scenario", "system_status"):
        monkeypatch.setattr(tool_manager, name, lambda *a, **k: pytest.fail(f"{name} should not be called"))

    result = copilot_agent.handle_message("sess-5", "What is peak shaving?")

    assert result["intent"] == "GENERAL_ENERGY_QUERY"
    assert result["action_taken"] == "general_energy_query"
    assert "peak shaving" in result["reply"].lower()


def test_response_never_contains_system_prompt_or_reasoning(monkeypatch):
    monkeypatch.setattr(tool_manager, "optimize_energy", lambda note, ctx: _fake_optimize_ok(note, ctx))
    result = copilot_agent.handle_message("sess-6", "Optimize my energy usage")
    assert set(result.keys()) == {"reply", "intent", "action_taken", "visual_data"}
    assert "SYSTEM_PROMPT" not in result["reply"]
    assert copilot_agent.SYSTEM_PROMPT not in result["reply"]


def test_session_persists_across_multiple_handle_message_calls(monkeypatch):
    """Simulates closing and reopening the chat: the same session_id used
    across separate handle_message calls (as separate HTTP requests would
    be) must still see the remembered optimization result."""
    monkeypatch.setattr(tool_manager, "optimize_energy", lambda note, ctx: _fake_optimize_ok(note, ctx))
    monkeypatch.setattr(
        tool_manager,
        "explain_schedule",
        lambda scenario, directives, plan: {"ok": True, "explanation": {"battery_reasons": ["reason"], "constraint_explanations": []}},
    )

    copilot_agent.handle_message("persist-1", "Optimize tomorrow")
    # Simulate the chat window being closed and reopened: a fresh call with
    # the same session_id, no new optimize call in between.
    result = copilot_agent.handle_message("persist-1", "Why did you choose this?")

    assert result["action_taken"] == "explain_schedule"
