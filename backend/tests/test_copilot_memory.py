"""Unit tests for app.copilot.conversation_memory."""

from app.copilot.conversation_memory import ConversationMemory


def test_new_session_starts_empty():
    memory = ConversationMemory()
    session = memory.get_session("s1")
    assert session.history == []
    assert session.last_scenario is None


def test_add_turn_records_history_in_order():
    memory = ConversationMemory()
    memory.add_turn("s1", "user", "optimize tomorrow", intent="OPTIMIZATION_REQUEST")
    memory.add_turn("s1", "assistant", "Optimization complete.", intent="OPTIMIZATION_REQUEST")
    session = memory.get_session("s1")
    assert [t.role for t in session.history] == ["user", "assistant"]
    assert session.history[0].text == "optimize tomorrow"


def test_history_bounded_to_max_turns():
    memory = ConversationMemory()
    for i in range(50):
        memory.add_turn("s1", "user", f"message {i}")
    session = memory.get_session("s1")
    assert len(session.history) <= 20


def test_sessions_are_isolated():
    memory = ConversationMemory()
    memory.add_turn("a", "user", "hello from a")
    memory.add_turn("b", "user", "hello from b")
    assert memory.get_session("a").history[0].text == "hello from a"
    assert memory.get_session("b").history[0].text == "hello from b"


def test_update_optimization_result_is_remembered():
    memory = ConversationMemory()
    memory.update_optimization_result("s1", "SCENARIO", "DIRECTIVES", "PLAN", {"total_cost_bdt": 100})
    session = memory.get_session("s1")
    assert session.last_scenario == "SCENARIO"
    assert session.last_directives == "DIRECTIVES"
    assert session.last_plan == "PLAN"
    assert session.last_totals == {"total_cost_bdt": 100}


def test_ttl_expiry_clears_stale_sessions():
    clock = {"t": 0.0}
    memory = ConversationMemory(ttl_seconds=10)
    # Patch time.time via monkeypatch-free trick: directly manipulate state.
    memory.add_turn("s1", "user", "hi")
    session = memory.get_session("s1")
    session.updated_at -= 20  # simulate 20s of inactivity, past the 10s TTL
    fresh = memory.get_session("s1")
    assert fresh.history == []  # evicted and recreated empty


def test_max_sessions_evicts_oldest():
    memory = ConversationMemory(max_sessions=3)
    for i in range(3):
        memory.add_turn(f"s{i}", "user", "hi")
    # Make s0 look oldest.
    memory.get_session("s0").updated_at -= 100
    memory.add_turn("s3", "user", "hi")  # should evict s0
    assert "s0" not in memory._sessions
    assert "s3" in memory._sessions


def test_clear_removes_all_sessions():
    memory = ConversationMemory()
    memory.add_turn("s1", "user", "hi")
    memory.clear()
    assert memory.get_session("s1").history == []
