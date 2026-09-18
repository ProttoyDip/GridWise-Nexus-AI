"""End-to-end tests for POST /copilot/chat, with app.copilot.tool_manager
monkeypatched (no real LLM/optimizer calls) — verifies the HTTP contract,
schema, and that nothing sensitive ever appears in the response."""

import json

import pytest
from fastapi.testclient import TestClient

from app.copilot import tool_manager
from app.copilot.conversation_memory import conversation_memory
from app.main import app
from app.models.response import DirectiveInterpretation, HourlyPlanEntry

client = TestClient(app)

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


def test_optimization_request_end_to_end(monkeypatch):
    monkeypatch.setattr(tool_manager, "optimize_energy", lambda note, ctx: _fake_optimize_ok(note, ctx))

    response = client.post(
        "/copilot/chat", json={"session_id": "api-1", "message": "Optimize tomorrow's energy"}
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"reply", "intent", "action_taken", "visual_data"}
    assert body["intent"] == "OPTIMIZATION_REQUEST"
    assert body["action_taken"] == "optimize_energy"


def test_explanation_request_end_to_end(monkeypatch):
    monkeypatch.setattr(tool_manager, "optimize_energy", lambda note, ctx: _fake_optimize_ok(note, ctx))
    monkeypatch.setattr(
        tool_manager,
        "explain_schedule",
        lambda scenario, directives, plan: {
            "ok": True,
            "explanation": {"battery_reasons": ["Charged during low tariff hours."], "constraint_explanations": []},
        },
    )

    client.post("/copilot/chat", json={"session_id": "api-2", "message": "Optimize tomorrow"})
    response = client.post("/copilot/chat", json={"session_id": "api-2", "message": "Why did you charge at night?"})

    assert response.status_code == 200
    body = response.json()
    assert body["intent"] == "EXPLANATION_REQUEST"
    assert body["action_taken"] == "explain_schedule"


def test_simulation_request_end_to_end(monkeypatch):
    monkeypatch.setattr(
        tool_manager,
        "simulate_scenario",
        lambda note, ctx, scenario, directives, plan: {
            "ok": True,
            "result": {"outcomes": [], "risk": {"recommendation": "Review reserve."}},
            "scenario": "S",
            "directives": ["D"],
            "plan": ["P"],
        },
    )

    response = client.post("/copilot/chat", json={"session_id": "api-3", "message": "What if solar drops?"})
    assert response.status_code == 200
    body = response.json()
    assert body["intent"] == "SIMULATION_REQUEST"
    assert body["action_taken"] == "simulate_scenario"


def test_status_request_end_to_end(monkeypatch):
    monkeypatch.setattr(
        tool_manager,
        "system_status",
        lambda: {
            "ok": True,
            "status": {
                "healthy": True,
                "model_availability": {"models": []},
                "optimizer_status": {"available": True, "solver": "CBC"},
            },
        },
    )

    response = client.post("/copilot/chat", json={"session_id": "api-4", "message": "Is system healthy?"})
    assert response.status_code == 200
    assert response.json()["intent"] == "STATUS_REQUEST"


def test_chat_state_restored_after_reconnect_with_same_session(monkeypatch):
    """Simulates "close and reopen the chat": conversation memory is
    server-side and keyed only by session_id, so a client that persists
    session_id locally (e.g. localStorage) and reconnects sees continuity
    without resending prior context."""
    monkeypatch.setattr(tool_manager, "optimize_energy", lambda note, ctx: _fake_optimize_ok(note, ctx))
    monkeypatch.setattr(
        tool_manager,
        "explain_schedule",
        lambda scenario, directives, plan: {"ok": True, "explanation": {"battery_reasons": ["r"], "constraint_explanations": []}},
    )

    client.post("/copilot/chat", json={"session_id": "reconnect-1", "message": "Optimize tomorrow"})
    # ... client "closes" (nothing server-side changes) ...
    response = client.post("/copilot/chat", json={"session_id": "reconnect-1", "message": "Why did you choose this?"})
    assert response.json()["action_taken"] == "explain_schedule"


def test_response_never_exposes_api_keys_or_prompts(monkeypatch):
    monkeypatch.setattr(tool_manager, "optimize_energy", lambda note, ctx: _fake_optimize_ok(note, ctx))
    response = client.post("/copilot/chat", json={"session_id": "api-5", "message": "Optimize my cost"})
    raw = json.dumps(response.json())
    for forbidden in ("api_key", "API_KEY", "Authorization", "Bearer", "SYSTEM_PROMPT", "sk-or-v1", "sk-nry-"):
        assert forbidden not in raw


def test_missing_message_is_rejected():
    response = client.post("/copilot/chat", json={"session_id": "api-6", "message": ""})
    assert response.status_code == 422


def test_missing_session_id_is_rejected():
    response = client.post("/copilot/chat", json={"message": "hello"})
    assert response.status_code == 422
