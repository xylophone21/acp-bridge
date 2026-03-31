"""Tests for src/handler.py — event routing logic.

Validates routing based on root_message_id, is_mention_bot, session existence,
and message type (# command vs plain text).
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from agent_bridge.feishu import FeishuEvent
from agent_bridge.session import SessionState


class FakeFeishu:
    def __init__(self):
        self.messages = []

    async def send_message(self, channel, thread_id, text):
        self.messages.append((channel, thread_id, text))
        return "msg_id_1"


class FakeSessionManager:
    """Fake SessionManager that returns sessions based on root_message_id."""

    def __init__(self, sessions=None):
        self._sessions = sessions or {}

    @property
    def sessions(self):
        return self._sessions

    def get_session_by_root(self, key):
        return self._sessions.get(key)


class FakeAgentManager:
    pass


class FakeConfig:
    class bridge:
        allowed_users = []
        default_workspace = "~"
        max_sessions = 10
        session_ttl_minutes = 60

    class agent:
        name = "kiro"
        command = "kiro-cli"
        args = ["acp"]
        auto_approve = False


def _make_event(
    text="hello",
    chat_id="ch1",
    message_id="m1",
    parent_id=None,
    root_id=None,
    is_mention_bot=False,
    sender_name="user1",
):
    return FeishuEvent(
        conversation_id=chat_id,
        message_id=message_id,
        parent_id=parent_id,
        text=text,
        root_id=root_id or message_id,
        is_mention_bot=is_mention_bot,
        sender_id=sender_name,
    )


class TestHandleEvent:
    """Tests for handle_event routing logic."""

    # ---- Test 1: no session + not @bot → ignored ----

    @pytest.mark.asyncio
    async def test_no_session_not_mention_bot_ignored(self):
        """No session + not @bot → message ignored (no handler called)."""
        from agent_bridge.handler import handle_event

        feishu = FakeFeishu()
        event = _make_event(text="hello", is_mention_bot=False)

        with patch("agent_bridge.handler.handle_command") as mock_cmd, \
             patch("agent_bridge.handler.handle_message") as mock_msg:
            await handle_event(
                event, feishu, FakeConfig(), FakeAgentManager(),
                FakeSessionManager(), {}, None,
            )
            mock_cmd.assert_not_called()
            mock_msg.assert_not_called()

        # No messages sent
        assert len(feishu.messages) == 0

    # ---- Test 2: no session + @bot + plain text → handle_message called ----

    @pytest.mark.asyncio
    async def test_no_session_mention_bot_plain_text_calls_handle_message(self):
        """No session + @bot + plain text → handle_message called (auto-create)."""
        from agent_bridge.handler import handle_event

        feishu = FakeFeishu()
        event = _make_event(text="please help me", is_mention_bot=True)

        with patch("agent_bridge.handler.handle_message", new_callable=AsyncMock) as mock_msg, \
             patch("agent_bridge.handler.handle_command", new_callable=AsyncMock) as mock_cmd:
            await handle_event(
                event, feishu, FakeConfig(), FakeAgentManager(),
                FakeSessionManager(), {}, None,
            )
            mock_msg.assert_called_once()
            mock_cmd.assert_not_called()
            # Verify correct arguments
            call_args = mock_msg.call_args
            assert call_args[0][0].text == "please help me"  # text
            assert call_args[0][0].conversation_id == "ch1"  # channel
            assert call_args[0][0].root_id == "m1"  # root_message_id

    # ---- Test 3: existing session + plain text → handle_message called ----

    @pytest.mark.asyncio
    async def test_existing_session_plain_text_routes_to_session(self):
        """Existing session + plain text → handle_message called (route to session)."""
        from agent_bridge.handler import handle_event

        feishu = FakeFeishu()
        session = SessionState(session_id="sess1", conversation_id="ch1")
        sm = FakeSessionManager(sessions={"root1": session})

        # Message in the thread of root1
        event = _make_event(
            text="continue the conversation",
            message_id="m2",
            root_id="root1",
            is_mention_bot=False,
        )

        with patch("agent_bridge.handler.handle_message", new_callable=AsyncMock) as mock_msg, \
             patch("agent_bridge.handler.handle_command", new_callable=AsyncMock) as mock_cmd:
            await handle_event(
                event, feishu, FakeConfig(), FakeAgentManager(),
                sm, {}, None,
            )
            mock_msg.assert_called_once()
            mock_cmd.assert_not_called()
            call_args = mock_msg.call_args
            assert call_args[0][0].text == "continue the conversation"
            assert call_args[0][0].root_id == "root1"  # root_message_id

    # ---- Test 4: existing session + # command → handle_command (no @bot needed) ----

    @pytest.mark.asyncio
    async def test_existing_session_command_no_mention_needed(self):
        """Existing session + # command → handle_command called (no @bot needed)."""
        from agent_bridge.handler import handle_event

        feishu = FakeFeishu()
        session = SessionState(session_id="sess1", conversation_id="ch1")
        sm = FakeSessionManager(sessions={"root1": session})

        event = _make_event(
            text="#session",
            message_id="m3",
            root_id="root1",
            is_mention_bot=False,  # Not mentioning bot
        )

        with patch("agent_bridge.handler.handle_command", new_callable=AsyncMock) as mock_cmd, \
             patch("agent_bridge.handler.handle_message", new_callable=AsyncMock) as mock_msg:
            await handle_event(
                event, feishu, FakeConfig(), FakeAgentManager(),
                sm, {}, None,
            )
            mock_cmd.assert_called_once()
            mock_msg.assert_not_called()
            call_args = mock_cmd.call_args
            assert call_args[0][0].text == "#session"
            assert call_args[0][0].root_id == "root1"  # root_message_id

    # ---- Test 5: no session + @bot + # command → handle_command called ----

    @pytest.mark.asyncio
    async def test_no_session_mention_bot_command_calls_handle_command(self):
        """No session + @bot + # command → handle_command called."""
        from agent_bridge.handler import handle_event

        feishu = FakeFeishu()
        event = _make_event(text="#help", is_mention_bot=True)

        with patch("agent_bridge.handler.handle_command", new_callable=AsyncMock) as mock_cmd, \
             patch("agent_bridge.handler.handle_message", new_callable=AsyncMock) as mock_msg:
            await handle_event(
                event, feishu, FakeConfig(), FakeAgentManager(),
                FakeSessionManager(), {}, None,
            )
            mock_cmd.assert_called_once()
            mock_msg.assert_not_called()

    # ---- Test 6: no session + not @bot + # command → ignored ----

    @pytest.mark.asyncio
    async def test_no_session_not_mention_bot_command_ignored(self):
        """No session + not @bot + # command → ignored."""
        from agent_bridge.handler import handle_event

        feishu = FakeFeishu()
        event = _make_event(text="#help", is_mention_bot=False)

        with patch("agent_bridge.handler.handle_command", new_callable=AsyncMock) as mock_cmd, \
             patch("agent_bridge.handler.handle_message", new_callable=AsyncMock) as mock_msg:
            await handle_event(
                event, feishu, FakeConfig(), FakeAgentManager(),
                FakeSessionManager(), {}, None,
            )
            mock_cmd.assert_not_called()
            mock_msg.assert_not_called()

        assert len(feishu.messages) == 0

    # ---- Test 7: pending permission response with root_message_id ----

    @pytest.mark.asyncio
    async def test_pending_permission_response_with_root_message_id(self):
        """Pending permission response handled correctly with root_message_id."""
        from acp.schema import PermissionOption

        from agent_bridge.handler import handle_event

        feishu = FakeFeishu()
        future = asyncio.get_event_loop().create_future()
        # Permission keyed by root_message_id (= root_id or message_id)
        pending = {
            "root1": {
                "options": [PermissionOption(option_id="opt1", name="Allow", kind="allow_once")],
                "future": future,
            }
        }
        event = _make_event(
            text="1",
            message_id="m2",
            root_id="root1",
            is_mention_bot=False,
        )

        await handle_event(
            event, feishu, FakeConfig(), FakeAgentManager(),
            FakeSessionManager(), pending, None,
        )
        assert future.result() == "opt1"
        assert "root1" not in pending

    @pytest.mark.asyncio
    async def test_pending_permission_no_root_id_uses_message_id(self):
        """Permission keyed by message_id when root_id is absent."""
        from acp.schema import PermissionOption

        from agent_bridge.handler import handle_event

        feishu = FakeFeishu()
        future = asyncio.get_event_loop().create_future()
        pending = {
            "m1": {
                "options": [PermissionOption(option_id="opt1", name="Allow", kind="allow_once")],
                "future": future,
            }
        }
        event = _make_event(text="1", message_id="m1", root_id=None)

        await handle_event(
            event, feishu, FakeConfig(), FakeAgentManager(),
            FakeSessionManager(), pending, None,
        )
        assert future.result() == "opt1"
        assert "m1" not in pending

    # ---- Test: root_message_id derivation ----

    @pytest.mark.asyncio
    async def test_root_message_id_uses_root_id_when_present(self):
        """root_message_id = root_id when root_id is present."""
        from agent_bridge.handler import handle_event

        feishu = FakeFeishu()
        session = SessionState(session_id="sess1", conversation_id="ch1")
        sm = FakeSessionManager(sessions={"root1": session})

        event = _make_event(
            text="hello",
            message_id="m5",
            root_id="root1",
            is_mention_bot=False,
        )

        with patch("agent_bridge.handler.handle_message", new_callable=AsyncMock) as mock_msg:
            await handle_event(
                event, feishu, FakeConfig(), FakeAgentManager(),
                sm, {}, None,
            )
            # Should route to root1 session
            call_args = mock_msg.call_args
            assert call_args[0][0].root_id == "root1"

    @pytest.mark.asyncio
    async def test_root_message_id_uses_message_id_when_no_root_id(self):
        """root_message_id = message_id when root_id is absent."""
        from agent_bridge.handler import handle_event

        feishu = FakeFeishu()
        session = SessionState(session_id="sess1", conversation_id="ch1")
        sm = FakeSessionManager(sessions={"m1": session})

        event = _make_event(
            text="hello",
            message_id="m1",
            root_id=None,
            is_mention_bot=False,
        )

        with patch("agent_bridge.handler.handle_message", new_callable=AsyncMock) as mock_msg:
            await handle_event(
                event, feishu, FakeConfig(), FakeAgentManager(),
                sm, {}, None,
            )
            call_args = mock_msg.call_args
            assert call_args[0][0].root_id == "m1"

    # ---- Test: existing session + @bot + plain text → still routes to session ----

    @pytest.mark.asyncio
    async def test_existing_session_mention_bot_plain_text_routes_to_session(self):
        """Existing session + @bot + plain text → routes to existing session (no new session)."""
        from agent_bridge.handler import handle_event

        feishu = FakeFeishu()
        session = SessionState(session_id="sess1", conversation_id="ch1")
        sm = FakeSessionManager(sessions={"root1": session})

        event = _make_event(
            text="more questions",
            message_id="m4",
            root_id="root1",
            is_mention_bot=True,  # Mentioning bot again
        )

        with patch("agent_bridge.handler.handle_message", new_callable=AsyncMock) as mock_msg:
            await handle_event(
                event, feishu, FakeConfig(), FakeAgentManager(),
                sm, {}, None,
            )
            mock_msg.assert_called_once()
            assert mock_msg.call_args[0][0].root_id == "root1"


# ---------------------------------------------------------------------------
# Property-Based Tests (Hypothesis)
# ---------------------------------------------------------------------------



# Strategies
_non_command_text = st.text(min_size=1).filter(lambda t: not t.strip().startswith("#"))
_command_text = st.text(min_size=1).map(lambda t: "#" + t)
_id_text = st.text(min_size=1, max_size=40, alphabet="abcdefghijklmnopqrstuvwxyz0123456789_")


class TestHandleEventProperties:
    """Property-based tests for handle_event routing logic."""

    # Feature: session-refactor, Property 1: 自动创建 Session 以 Root_Message 为索引
    # Validates: Requirements 1.1, 1.5, 2.3
    @given(
        text=_non_command_text,
        chat_id=_id_text,
        message_id=_id_text,
        root_id=_id_text,
        sender_name=_id_text,
    )
    @settings(max_examples=100)
    @pytest.mark.asyncio
    async def test_property1_auto_create_session_uses_root_message_id(
        self, text, chat_id, message_id, root_id, sender_name
    ):
        """For any @bot non-command message with no existing session,
        handle_message is called with root_message_id = root_id."""
        from agent_bridge.handler import handle_event

        event = FeishuEvent(
            conversation_id=chat_id,
            message_id=message_id,
            parent_id=None,
            text=text,
            root_id=root_id,
            is_mention_bot=True,
            sender_id=sender_name,
        )

        with patch("agent_bridge.handler.handle_message", new_callable=AsyncMock) as mock_msg, \
             patch("agent_bridge.handler.handle_command", new_callable=AsyncMock) as mock_cmd:
            await handle_event(
                event, FakeFeishu(), FakeConfig(), FakeAgentManager(),
                FakeSessionManager(), {}, None,
            )
            mock_msg.assert_called_once()
            mock_cmd.assert_not_called()
            call_args = mock_msg.call_args
            assert call_args[0][0].root_id == root_id

    # Feature: session-refactor, Property 2: 消息路由到已有 Session
    # Validates: Requirements 1.2, 1.8, 2.2
    @given(
        text=_non_command_text,
        chat_id=_id_text,
        message_id=_id_text,
        root_id=_id_text,
        sender_name=_id_text,
        is_mention_bot=st.booleans(),
    )
    @settings(max_examples=100)
    @pytest.mark.asyncio
    async def test_property2_message_routes_to_existing_session(
        self, text, chat_id, message_id, root_id, sender_name, is_mention_bot
    ):
        """For any non-command message whose root_message_id matches an existing session,
        handle_message is called and handle_command is NOT called, no new session created."""
        from agent_bridge.handler import handle_event

        session = SessionState(session_id="sess1", conversation_id=chat_id)
        sm = FakeSessionManager(sessions={root_id: session})

        event = FeishuEvent(
            conversation_id=chat_id,
            message_id=message_id,
            parent_id=None,
            text=text,
            root_id=root_id,
            is_mention_bot=is_mention_bot,
            sender_id=sender_name,
        )

        with patch("agent_bridge.handler.handle_message", new_callable=AsyncMock) as mock_msg, \
             patch("agent_bridge.handler.handle_command", new_callable=AsyncMock) as mock_cmd:
            await handle_event(
                event, FakeFeishu(), FakeConfig(), FakeAgentManager(),
                sm, {}, None,
            )
            mock_msg.assert_called_once()
            mock_cmd.assert_not_called()
            # Verify routed to existing session's root_message_id
            assert mock_msg.call_args[0][0].root_id == root_id
            # No new session created (still only the original one)
            assert len(sm.sessions) == 1

    # Feature: session-refactor, Property 3: 忽略无关消息
    # Validates: Requirements 1.9, 2.4
    @given(
        text=_non_command_text,
        chat_id=_id_text,
        message_id=_id_text,
        root_id=_id_text,
        sender_name=_id_text,
    )
    @settings(max_examples=100)
    @pytest.mark.asyncio
    async def test_property3_ignore_irrelevant_messages(
        self, text, chat_id, message_id, root_id, sender_name
    ):
        """For any message with mention=False and no existing session,
        neither handle_message nor handle_command is called."""
        from agent_bridge.handler import handle_event

        event = FeishuEvent(
            conversation_id=chat_id,
            message_id=message_id,
            parent_id=None,
            text=text,
            root_id=root_id,
            is_mention_bot=False,
            sender_id=sender_name,
        )

        with patch("agent_bridge.handler.handle_message", new_callable=AsyncMock) as mock_msg, \
             patch("agent_bridge.handler.handle_command", new_callable=AsyncMock) as mock_cmd:
            await handle_event(
                event, FakeFeishu(), FakeConfig(), FakeAgentManager(),
                FakeSessionManager(), {}, None,
            )
            mock_msg.assert_not_called()
            mock_cmd.assert_not_called()

    # Feature: session-refactor, Property 4: Session 内指令无需 @机器人
    # Validates: Requirements 1.10
    @given(
        cmd_text=_command_text,
        chat_id=_id_text,
        message_id=_id_text,
        root_id=_id_text,
        sender_name=_id_text,
        is_mention_bot=st.booleans(),
    )
    @settings(max_examples=100)
    @pytest.mark.asyncio
    async def test_property4_session_command_no_mention_needed(
        self, cmd_text, chat_id, message_id, root_id, sender_name, is_mention_bot
    ):
        """For any # command within an existing session,
        handle_command is called regardless of is_mention_bot."""
        from agent_bridge.handler import handle_event

        session = SessionState(session_id="sess1", conversation_id=chat_id)
        sm = FakeSessionManager(sessions={root_id: session})

        event = FeishuEvent(
            conversation_id=chat_id,
            message_id=message_id,
            parent_id=None,
            text=cmd_text,
            root_id=root_id,
            is_mention_bot=is_mention_bot,
            sender_id=sender_name,
        )

        with patch("agent_bridge.handler.handle_command", new_callable=AsyncMock) as mock_cmd, \
             patch("agent_bridge.handler.handle_message", new_callable=AsyncMock) as mock_msg:
            await handle_event(
                event, FakeFeishu(), FakeConfig(), FakeAgentManager(),
                sm, {}, None,
            )
            mock_cmd.assert_called_once()
            mock_msg.assert_not_called()

    # Feature: session-refactor, Property 5: Session 外指令需要 @机器人
    # Validates: Requirements 1.11
    @given(
        cmd_text=_command_text,
        chat_id=_id_text,
        message_id=_id_text,
        root_id=_id_text,
        sender_name=_id_text,
        is_mention_bot=st.booleans(),
    )
    @settings(max_examples=100)
    @pytest.mark.asyncio
    async def test_property5_no_session_command_requires_mention(
        self, cmd_text, chat_id, message_id, root_id, sender_name, is_mention_bot
    ):
        """For any # command with no existing session,
        handle_command is called only when is_mention_bot=True."""
        from agent_bridge.handler import handle_event

        event = FeishuEvent(
            conversation_id=chat_id,
            message_id=message_id,
            parent_id=None,
            text=cmd_text,
            root_id=root_id,
            is_mention_bot=is_mention_bot,
            sender_id=sender_name,
        )

        with patch("agent_bridge.handler.handle_command", new_callable=AsyncMock) as mock_cmd, \
             patch("agent_bridge.handler.handle_message", new_callable=AsyncMock) as mock_msg:
            await handle_event(
                event, FakeFeishu(), FakeConfig(), FakeAgentManager(),
                FakeSessionManager(), {}, None,
            )
            if is_mention_bot:
                mock_cmd.assert_called_once()
            else:
                mock_cmd.assert_not_called()
            mock_msg.assert_not_called()
