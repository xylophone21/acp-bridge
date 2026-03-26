"""Tests for src/bridge.py — unit tests for helper functions and TTL eviction."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.bridge import _flush_agent_chunks, _format_plan, _handle_evicted_sessions, _send_tool_msg
from src.session import SessionState


class TestFormatPlan:
    def test_empty_plan(self):
        assert _format_plan([]) == "*Plan*"

    def test_mixed_statuses(self):
        entries = [
            {"content": "Step 1", "status": "completed"},
            {"content": "Step 2", "status": "in_progress"},
            {"content": "Step 3", "status": "pending"},
        ]
        result = _format_plan(entries)
        assert "[x] Step 1" in result
        assert "[>] Step 2" in result
        assert "[ ] Step 3" in result

    def test_unknown_status(self):
        entries = [{"content": "Mystery", "status": "unknown"}]
        result = _format_plan(entries)
        assert "[?] Mystery" in result

    def test_missing_status(self):
        entries = [{"content": "No status"}]
        result = _format_plan(entries)
        assert "[ ] No status" in result


class TestFlushAgentChunks:
    @pytest.mark.asyncio
    async def test_flush_text_chunks(self):
        session = SessionState(session_id="s1", conversation_id="ch1")
        sm = MagicMock()
        sm.find_by_session_id.return_value = ("root1", session)
        feishu = AsyncMock()
        feishu.send_message.return_value = "bot_msg_1"
        agent_text_chunks = {"s1": "hello world"}
        agent_thought_chunks = {}

        await _flush_agent_chunks("s1", feishu, sm, agent_text_chunks, agent_thought_chunks)

        feishu.send_message.assert_called_once_with("ch1", "root1", "hello world")
        assert session.last_bot_message_id == "bot_msg_1"
        assert "s1" not in agent_text_chunks

    @pytest.mark.asyncio
    async def test_flush_thought_chunks(self):
        session = SessionState(session_id="s1", conversation_id="ch1")
        sm = MagicMock()
        sm.find_by_session_id.return_value = ("root1", session)
        feishu = AsyncMock()
        feishu.send_message.return_value = "bot_msg_2"
        agent_text_chunks = {}
        agent_thought_chunks = {"s1": "thinking..."}

        await _flush_agent_chunks("s1", feishu, sm, agent_text_chunks, agent_thought_chunks)

        feishu.send_message.assert_called_once_with("ch1", "root1", "> thinking...")
        assert session.last_bot_message_id == "bot_msg_2"

    @pytest.mark.asyncio
    async def test_flush_no_session_found(self):
        sm = MagicMock()
        sm.find_by_session_id.return_value = None
        feishu = AsyncMock()
        agent_text_chunks = {"s1": "text"}
        agent_thought_chunks = {}

        await _flush_agent_chunks("s1", feishu, sm, agent_text_chunks, agent_thought_chunks)

        feishu.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_flush_updates_last_bot_message_id(self):
        session = SessionState(session_id="s1", conversation_id="ch1", last_bot_message_id="old_id")
        sm = MagicMock()
        sm.find_by_session_id.return_value = ("root1", session)
        feishu = AsyncMock()
        feishu.send_message.return_value = "new_bot_msg"
        agent_text_chunks = {"s1": "new text"}
        agent_thought_chunks = {}

        await _flush_agent_chunks("s1", feishu, sm, agent_text_chunks, agent_thought_chunks)

        assert session.last_bot_message_id == "new_bot_msg"


class TestSendToolMsg:
    @pytest.mark.asyncio
    async def test_send_tool_msg(self):
        session = SessionState(session_id="s1", conversation_id="ch1")
        sm = MagicMock()
        sm.find_by_session_id.return_value = ("root1", session)
        feishu = AsyncMock()
        feishu.send_message.return_value = "tool_msg_id"

        await _send_tool_msg(feishu, sm, "s1", "🔧 Tool: test")

        feishu.send_message.assert_called_once_with("ch1", "root1", "🔧 Tool: test")
        assert session.last_bot_message_id == "tool_msg_id"

    @pytest.mark.asyncio
    async def test_send_tool_msg_no_session(self):
        sm = MagicMock()
        sm.find_by_session_id.return_value = None
        feishu = AsyncMock()

        await _send_tool_msg(feishu, sm, "unknown", "msg")

        feishu.send_message.assert_not_called()


class TestHandleEvictedSessions:
    @pytest.mark.asyncio
    async def test_evicts_expired_sessions(self):
        expired_session = SessionState(
            session_id="s1",
            conversation_id="ch1",
            trigger_message_id="trigger1",
            last_bot_message_id="bot1",
        )
        agent_manager = AsyncMock()
        feishu = AsyncMock()

        await _handle_evicted_sessions([expired_session], agent_manager, feishu)

        agent_manager.end_session.assert_called_with("s1")
        feishu.add_reaction.assert_any_call("trigger1", "DONE")
        feishu.add_reaction.assert_any_call("bot1", "DONE")

    @pytest.mark.asyncio
    async def test_eviction_skips_reaction_on_no_last_bot_message(self):
        expired_session = SessionState(
            session_id="s2",
            conversation_id="ch1",
            trigger_message_id="trigger2",
            last_bot_message_id="",
        )
        agent_manager = AsyncMock()
        feishu = AsyncMock()

        await _handle_evicted_sessions([expired_session], agent_manager, feishu)

        agent_manager.end_session.assert_called_with("s2")
        # Only trigger message reaction, not bot message
        feishu.add_reaction.assert_called_once_with("trigger2", "DONE")

    @pytest.mark.asyncio
    async def test_eviction_handles_end_session_error(self):
        expired_session = SessionState(
            session_id="s3",
            conversation_id="ch1",
            trigger_message_id="trigger3",
        )
        agent_manager = AsyncMock()
        agent_manager.end_session.side_effect = Exception("process gone")
        feishu = AsyncMock()

        await _handle_evicted_sessions([expired_session], agent_manager, feishu)

        # Should still try to add reaction even if end_session fails
        feishu.add_reaction.assert_called_once_with("trigger3", "DONE")

    @pytest.mark.asyncio
    async def test_eviction_handles_reaction_error(self):
        expired_session = SessionState(
            session_id="s4",
            conversation_id="ch1",
            trigger_message_id="trigger4",
            last_bot_message_id="bot4",
        )
        agent_manager = AsyncMock()
        feishu = AsyncMock()
        feishu.add_reaction.side_effect = Exception("API error")

        await _handle_evicted_sessions([expired_session], agent_manager, feishu)

        # Should not block eviction even if reaction fails
        agent_manager.end_session.assert_called_with("s4")

    @pytest.mark.asyncio
    async def test_no_expired_sessions(self):
        agent_manager = AsyncMock()
        feishu = AsyncMock()

        await _handle_evicted_sessions([], agent_manager, feishu)

        agent_manager.end_session.assert_not_called()
        feishu.add_reaction.assert_not_called()

    @pytest.mark.asyncio
    async def test_multiple_expired_sessions(self):
        sessions = [
            SessionState(session_id="s1", conversation_id="ch1", trigger_message_id="t1", last_bot_message_id="b1"),
            SessionState(session_id="s2", conversation_id="ch2", trigger_message_id="t2"),
        ]
        agent_manager = AsyncMock()
        feishu = AsyncMock()

        await _handle_evicted_sessions(sessions, agent_manager, feishu)

        assert agent_manager.end_session.call_count == 2
        agent_manager.end_session.assert_any_call("s1")
        agent_manager.end_session.assert_any_call("s2")
        feishu.add_reaction.assert_any_call("t1", "DONE")
        feishu.add_reaction.assert_any_call("b1", "DONE")
        feishu.add_reaction.assert_any_call("t2", "DONE")


# Feature: session-refactor, Property 12: 淘汰时终止进程并添加 Reaction
# **Validates: Requirements 3.5, 3.6, 4.4, 4.5**



def _session_state_strategy():
    """Strategy to generate random SessionState objects."""
    return st.builds(
        SessionState,
        session_id=st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=("L", "N"))),
        conversation_id=st.text(min_size=1, max_size=10, alphabet=st.characters(whitelist_categories=("L", "N"))),
        trigger_message_id=st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=("L", "N"))),
        last_bot_message_id=st.text(min_size=0, max_size=20, alphabet=st.characters(whitelist_categories=("L", "N"))),
    )


class TestHandleEvictedSessionsProperty:
    @pytest.mark.asyncio
    @given(sessions=st.lists(_session_state_strategy(), min_size=0, max_size=10))
    @settings(max_examples=100)
    async def test_eviction_terminates_and_reacts(self, sessions):
        """Property 12: For every evicted session, end_session is called with
        session.session_id, add_reaction is called on trigger_message_id with
        'DONE', and if last_bot_message_id is non-empty, add_reaction is also
        called on it with 'DONE'."""
        agent_manager = AsyncMock()
        feishu = AsyncMock()

        await _handle_evicted_sessions(sessions, agent_manager, feishu)

        # Verify end_session called once per session
        assert agent_manager.end_session.call_count == len(sessions)
        for session in sessions:
            agent_manager.end_session.assert_any_call(session.session_id)

        # Verify add_reaction called on trigger_message_id for every session
        for session in sessions:
            feishu.add_reaction.assert_any_call(session.trigger_message_id, "DONE")

        # Verify add_reaction called on last_bot_message_id only when non-empty
        expected_reaction_count = len(sessions)  # one for each trigger_message_id
        for session in sessions:
            if session.last_bot_message_id:
                expected_reaction_count += 1
                feishu.add_reaction.assert_any_call(session.last_bot_message_id, "DONE")

        assert feishu.add_reaction.call_count == expected_reaction_count
