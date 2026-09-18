"""Lightweight in-process, per-session Copilot conversation memory.

No database, per the brief: a bounded, TTL-expiring, thread-safe
in-memory dict keyed by session_id, mirroring the same pattern already
used elsewhere in this codebase (app.llm.cache, app.optimizer.cache,
app.llm.circuit_breaker) — process-local, bounded, safe under the
concurrent request handling FastAPI/Starlette already does.

What's remembered per session: the recent chat turns (for display/
context only), and the *last real optimization result* (scenario,
validated directives, hourly plan) so a follow-up "why did you choose
this?" can explain the actual computed schedule instead of asking the
user to repeat themselves or inventing one.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.models.request import ScenarioRequest
    from app.models.response import DirectiveInterpretation, HourlyPlanEntry

SESSION_TTL_SECONDS = 3600
MAX_SESSIONS = 500
MAX_HISTORY_TURNS = 20


@dataclass
class ChatTurn:
    role: str  # "user" | "assistant"
    text: str
    intent: str | None = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class SessionState:
    history: list[ChatTurn] = field(default_factory=list)
    last_scenario: "ScenarioRequest | None" = None
    last_directives: "list[DirectiveInterpretation] | None" = None
    last_plan: "list[HourlyPlanEntry] | None" = None
    last_totals: dict[str, Any] | None = None
    updated_at: float = field(default_factory=time.time)


class ConversationMemory:
    def __init__(self, ttl_seconds: float = SESSION_TTL_SECONDS, max_sessions: int = MAX_SESSIONS) -> None:
        self._ttl = ttl_seconds
        self._max_sessions = max_sessions
        self._lock = threading.Lock()
        self._sessions: dict[str, SessionState] = {}

    def _evict_expired_locked(self) -> None:
        now = time.time()
        expired = [sid for sid, state in self._sessions.items() if now - state.updated_at > self._ttl]
        for sid in expired:
            del self._sessions[sid]

    def _get_or_create_locked(self, session_id: str) -> SessionState:
        self._evict_expired_locked()
        state = self._sessions.get(session_id)
        if state is None:
            if len(self._sessions) >= self._max_sessions:
                oldest_id = min(self._sessions, key=lambda sid: self._sessions[sid].updated_at)
                del self._sessions[oldest_id]
            state = SessionState()
            self._sessions[session_id] = state
        return state

    def get_session(self, session_id: str) -> SessionState:
        """Return this session's state, creating it if new. Reading does
        not require a prior message — used by the agent to check for a
        remembered prior optimization before deciding how to answer."""
        with self._lock:
            return self._get_or_create_locked(session_id)

    def add_turn(self, session_id: str, role: str, text: str, intent: str | None = None) -> None:
        with self._lock:
            state = self._get_or_create_locked(session_id)
            state.history.append(ChatTurn(role=role, text=text, intent=intent))
            if len(state.history) > MAX_HISTORY_TURNS:
                state.history = state.history[-MAX_HISTORY_TURNS:]
            state.updated_at = time.time()

    def update_optimization_result(
        self,
        session_id: str,
        scenario: "ScenarioRequest",
        directives: "list[DirectiveInterpretation]",
        plan: "list[HourlyPlanEntry]",
        totals: dict[str, Any],
    ) -> None:
        with self._lock:
            state = self._get_or_create_locked(session_id)
            state.last_scenario = scenario
            state.last_directives = directives
            state.last_plan = plan
            state.last_totals = totals
            state.updated_at = time.time()

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()


conversation_memory = ConversationMemory()
