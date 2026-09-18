"""POST /copilot/chat — GridWise AI Energy Copilot conversational endpoint.

Additive and judge-schema-safe, like GET /llm/status and GET /system/status:
not part of the fixed /optimize-energy contract. Delegates entirely to
app.copilot.copilot_agent, which reuses GridWise's existing optimizer,
interpreter, simulator, and explainability services rather than
duplicating any of their logic.

Never exposes API keys, internal prompts, or chain-of-thought: the
response model below has no field for any of them, by construction.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.copilot.copilot_agent import handle_message

router = APIRouter()


class CopilotChatRequest(BaseModel):
    session_id: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1)
    context: dict[str, Any] | None = None


class CopilotChatResponse(BaseModel):
    reply: str
    intent: str
    action_taken: str
    visual_data: dict[str, Any] | None = None


@router.post("/copilot/chat", response_model=CopilotChatResponse)
def copilot_chat(payload: CopilotChatRequest) -> CopilotChatResponse:
    result = handle_message(payload.session_id, payload.message, payload.context)
    return CopilotChatResponse.model_validate(result)
