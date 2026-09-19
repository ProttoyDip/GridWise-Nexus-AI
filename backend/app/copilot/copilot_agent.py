"""GridWise AI Energy Copilot — orchestration entrypoint.

This module is the only "agent" logic in the Copilot: given a chat
message, it classifies intent (intent_router), calls the matching
existing GridWise tool (tool_manager), formats the real result into a
reply (response_generator), and records what happened in per-session
memory (conversation_memory). It performs no optimization, LLM
interpretation, or simulation itself — every number in a reply traces
back to an existing GridWise service call.

SYSTEM_PROMPT documents the Copilot's contract. The intent router below
is deterministic (not an LLM call) specifically so tool selection stays
fast, free, and reproducible; SYSTEM_PROMPT is kept here so any future
LLM-backed step (e.g. paraphrasing a reply, or a fallback intent
classifier for ambiguous phrasing) inherits the same rules rather than
drifting from them. It is never sent back to the client — see
app.api.copilot, which only ever returns {reply, intent, action_taken,
visual_data}.
"""

from __future__ import annotations

from typing import Any

from app.copilot import response_generator as replies
from app.copilot import tool_manager as tools
from app.copilot.conversation_memory import conversation_memory
from app.copilot.intent_router import Intent, classify_intent

SYSTEM_PROMPT = """You are GridWise AI Energy Copilot.

You assist energy operators in understanding and optimizing campus energy systems.

You can:
- analyze energy scenarios
- explain optimization decisions
- simulate possible future conditions
- summarize energy strategies

Rules:
1. Never invent optimization results.
2. Always use GridWise tools for actual calculations.
3. Clearly distinguish:
   - calculated results
   - simulations
   - recommendations
4. Never reveal internal reasoning.
5. Provide concise operational explanations.
6. Help users make informed energy decisions.
"""


def handle_message(session_id: str, message: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Handle one Copilot chat turn. Always returns
    {reply, intent, action_taken, visual_data} and never raises — every
    tool call inside is already failure-safe (see tool_manager)."""
    conversation_memory.add_turn(session_id, "user", message)
    intent = classify_intent(message)
    session = conversation_memory.get_session(session_id)

    if intent is Intent.OPTIMIZATION_REQUEST:
        result = tools.optimize_energy(message, context)
        reply = replies.optimization_reply(result)
        if result.get("ok"):
            conversation_memory.update_optimization_result(
                session_id,
                result["scenario"],
                result["directives"],
                result["plan"],
                {
                    "total_cost_bdt": result["total_cost_bdt"],
                    "total_grid_kwh": result["total_grid_kwh"],
                    "peak_grid_kwh": result["peak_grid_kwh"],
                },
            )

    elif intent is Intent.EXPLANATION_REQUEST:
        result = tools.explain_schedule(session.last_scenario, session.last_directives, session.last_plan)
        reply = replies.explanation_reply(result)

    elif intent is Intent.SIMULATION_REQUEST:
        result = tools.simulate_scenario(
            message, context, session.last_scenario, session.last_directives, session.last_plan
        )
        reply = replies.simulation_reply(result)
        if result.get("ok"):
            # A simulation also establishes/refreshes the nominal
            # scenario/directives/plan it was run against, so a follow-up
            # "why?" can still explain it even if the session had no prior
            # optimize_energy call.
            conversation_memory.update_optimization_result(
                session_id, result["scenario"], result["directives"], result["plan"], {}
            )

    elif intent is Intent.SCHEDULE_REQUEST:
        result = tools.get_action_schedule(message, context)
        if result.get("ok"):
            lines = [f"{a['start_time']}-{a['end_time']}  {a['type'].replace('_', ' ').title()} - {a['reason']}" for a in result["actions"]]
            text = "Recommended actions for today:\n" + "\n".join(lines) if lines else "No special actions are needed today."
            reply = {"reply": text, "action_taken": "get_action_schedule", "visual_data": {"actions": result["actions"]}}
        else:
            reply = {"reply": "I couldn't build an action schedule right now.", "action_taken": "get_action_schedule_failed", "visual_data": None}

    elif intent is Intent.APP_HELP_REQUEST:
        import re as _re

        secret = _re.search(r"prompt|api key|env", message.casefold())
        reply = {"reply": tools.SECRET_REFUSAL if secret else tools.APP_GUIDE, "action_taken": "explain_application_usage", "visual_data": None}

    elif intent is Intent.STATUS_REQUEST:
        result = tools.system_status()
        reply = replies.status_reply(result)

    else:
        reply = replies.general_reply(message)

    conversation_memory.add_turn(session_id, "assistant", reply["reply"], intent.value)

    return {
        "reply": reply["reply"],
        "intent": intent.value,
        "action_taken": reply["action_taken"],
        "visual_data": reply["visual_data"],
    }
