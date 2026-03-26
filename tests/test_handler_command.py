"""Tests for src/handler_command.py"""

import time
from collections import OrderedDict

import pytest

from src.feishu import FeishuEvent
from src.handler_command import HELP_MESSAGE, _format_relative_time, handle_command
from src.session import SessionState


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeFeishu:
    def __init__(self):
        self.messages = []

    async def send_message(self, channel, thread_id, text):
        self.messages.append((channel, thread_id, text))
        return "msg_id_1"


class FakeSessionManager:
    def __init__(self, sessions=None):
        self._sessions = OrderedDict(sessions or {})

    def list_sessions(self):
        return list(self._sessions.values())

    def get_session_by_root(self, key):
        return self._sessions.get(key)

    def end_session(self, key):
        if key in self._sessions:
            del self._sessions[key]


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


class FakeAgentManager:
    def is_auto_approve(self, session_id):
        return False


def _make_event(text="#help", root_id="root1", conversation_id="ch1", message_id="msg1"):
    return FeishuEvent(
        conversation_id=conversation_id,
        message_id=message_id,
        parent_id=None,
        text=text,
        root_id=root_id,
        is_mention_bot=True,
        sender_id="user1",
    )


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_session(**kwargs):
    defaults = dict(
        session_id="sess1",
        conversation_id="ch1",
        busy=False,
        trigger_message_id="root1",
        summary="Hello world test msg",
        last_active=time.time() - 120,
        last_bot_message_id="bot_msg_1",
    )
    defaults.update(kwargs)
    return SessionState(**defaults)


# ---------------------------------------------------------------------------
# _format_relative_time tests
# ---------------------------------------------------------------------------

class TestFormatRelativeTime:
    def test_seconds_ago(self):
        now = time.time()
        assert _format_relative_time(now - 30) == "30s ago"

    def test_minutes_ago(self):
        now = time.time()
        assert _format_relative_time(now - 120) == "2m ago"

    def test_hours_ago(self):
        now = time.time()
        assert _format_relative_time(now - 7200) == "2h ago"

    def test_days_ago(self):
        now = time.time()
        assert _format_relative_time(now - 172800) == "2d ago"


# ---------------------------------------------------------------------------
# #sessions enhanced output tests (Requirements 6.1, 6.2)
# ---------------------------------------------------------------------------

class TestSessionsCommand:
    """Verify #sessions shows summary, status, last active; does NOT show agent name."""

    @pytest.mark.asyncio
    async def test_sessions_shows_summary(self):
        session = _make_session(summary="Fix the login bug")
        sm = FakeSessionManager(sessions={"root1": session})
        feishu = FakeFeishu()

        await handle_command(_make_event(text="#sessions", root_id="root1"), feishu,
            FakeConfig(), FakeAgentManager(), sm,
        )

        output = feishu.messages[0][2]
        assert "Fix the login bug" in output

    @pytest.mark.asyncio
    async def test_sessions_shows_status_idle(self):
        session = _make_session(busy=False)
        sm = FakeSessionManager(sessions={"root1": session})
        feishu = FakeFeishu()

        await handle_command(_make_event(text="#sessions", root_id="root1"), feishu,
            FakeConfig(), FakeAgentManager(), sm,
        )

        output = feishu.messages[0][2]
        assert "idle" in output

    @pytest.mark.asyncio
    async def test_sessions_shows_status_busy(self):
        session = _make_session(busy=True)
        sm = FakeSessionManager(sessions={"root1": session})
        feishu = FakeFeishu()

        await handle_command(_make_event(text="#sessions", root_id="root1"), feishu,
            FakeConfig(), FakeAgentManager(), sm,
        )

        output = feishu.messages[0][2]
        assert "busy" in output

    @pytest.mark.asyncio
    async def test_sessions_shows_relative_time(self):
        session = _make_session(last_active=time.time() - 300)
        sm = FakeSessionManager(sessions={"root1": session})
        feishu = FakeFeishu()

        await handle_command(_make_event(text="#sessions", root_id="root1"), feishu,
            FakeConfig(), FakeAgentManager(), sm,
        )

        output = feishu.messages[0][2]
        assert "5m ago" in output

    @pytest.mark.asyncio
    async def test_sessions_does_not_show_agent_name(self):
        session = _make_session()
        sm = FakeSessionManager(sessions={"root1": session})
        feishu = FakeFeishu()

        await handle_command(_make_event(text="#sessions", root_id="root1"), feishu,
            FakeConfig(), FakeAgentManager(), sm,
        )

        output = feishu.messages[0][2]
        # agent name "kiro" should NOT appear in #sessions output
        assert "kiro" not in output.lower()
        assert "agent" not in output.lower()

    @pytest.mark.asyncio
    async def test_sessions_empty(self):
        sm = FakeSessionManager(sessions={})
        feishu = FakeFeishu()

        await handle_command(_make_event(text="#sessions", root_id="root1"), feishu,
            FakeConfig(), FakeAgentManager(), sm,
        )

        output = feishu.messages[0][2]
        assert "No active sessions" in output

    @pytest.mark.asyncio
    async def test_sessions_multiple(self):
        s1 = _make_session(summary="Task A", busy=False, last_active=time.time() - 60)
        s2 = _make_session(session_id="sess2", summary="Task B", busy=True, last_active=time.time() - 3600)
        sm = FakeSessionManager(sessions={"r1": s1, "r2": s2})
        feishu = FakeFeishu()

        await handle_command(_make_event(text="#sessions", root_id="r1"), feishu,
            FakeConfig(), FakeAgentManager(), sm,
        )

        output = feishu.messages[0][2]
        assert "Task A" in output
        assert "Task B" in output
        assert "idle" in output
        assert "busy" in output
        assert "2" in output  # count


# ---------------------------------------------------------------------------
# #session output tests (Requirements 1.3, 1.4)
# ---------------------------------------------------------------------------

class TestSessionCommand:
    """Verify #session shows session_id, status, summary, trigger_message_id;
    does NOT show agent_name or workspace."""

    @pytest.mark.asyncio
    async def test_session_shows_required_fields(self):
        session = _make_session(
            session_id="sess_abc",
            summary="Debug the crash",
            trigger_message_id="root1",
            busy=True,
        )
        sm = FakeSessionManager(sessions={"root1": session})
        feishu = FakeFeishu()

        await handle_command(_make_event(text="#session", root_id="root1"), feishu,
            FakeConfig(), FakeAgentManager(), sm,
        )

        output = feishu.messages[0][2]
        assert "busy" in output
        assert "Debug the crash" in output
        assert "Auto-approve" in output

    @pytest.mark.asyncio
    async def test_session_does_not_show_agent_name_or_workspace(self):
        session = _make_session()
        sm = FakeSessionManager(sessions={"root1": session})
        feishu = FakeFeishu()

        await handle_command(_make_event(text="#session", root_id="root1"), feishu,
            FakeConfig(), FakeAgentManager(), sm,
        )

        output = feishu.messages[0][2]
        assert "agent" not in output.lower()
        assert "workspace" not in output.lower()

    @pytest.mark.asyncio
    async def test_session_no_active_conversation(self):
        sm = FakeSessionManager(sessions={})
        feishu = FakeFeishu()

        await handle_command(_make_event(text="#session", root_id="root1"), feishu,
            FakeConfig(), FakeAgentManager(), sm,
        )

        output = feishu.messages[0][2]
        assert "No active conversation" in output


# ---------------------------------------------------------------------------
# #help output tests (Requirements 1.3, 1.4, 1.5)
# ---------------------------------------------------------------------------

class TestHelpCommand:
    """Verify #help does NOT mention #new, #agents, or ! commands."""

    @pytest.mark.asyncio
    async def test_help_no_new_command(self):
        assert "#new" not in HELP_MESSAGE

    @pytest.mark.asyncio
    async def test_help_no_agents_command(self):
        assert "#agents" not in HELP_MESSAGE

    @pytest.mark.asyncio
    async def test_help_no_shell_command(self):
        # No "!" shell command reference
        assert "!" not in HELP_MESSAGE

    @pytest.mark.asyncio
    async def test_help_output_sent(self):
        sm = FakeSessionManager(sessions={})
        feishu = FakeFeishu()

        await handle_command(_make_event(text="#help", root_id="root1"), feishu,
            FakeConfig(), FakeAgentManager(), sm,
        )

        output = feishu.messages[0][2]
        assert "#help" in output
        assert "#sessions" in output


# ---------------------------------------------------------------------------
# Session-dependent command with no session → "No active conversation."
# ---------------------------------------------------------------------------

class TestSessionDependentCommandNoSession:
    """Session-dependent commands without an active session should reply
    'No active conversation.'"""

    @pytest.mark.asyncio
    async def test_end_no_session(self):
        sm = FakeSessionManager(sessions={})
        feishu = FakeFeishu()

        await handle_command(_make_event(text="#end", root_id="root1"), feishu,
            FakeConfig(), FakeAgentManager(), sm,
        )

        assert "No active conversation" in feishu.messages[0][2]

    @pytest.mark.asyncio
    async def test_cancel_no_session(self):
        sm = FakeSessionManager(sessions={})
        feishu = FakeFeishu()

        await handle_command(_make_event(text="#cancel", root_id="root1"), feishu,
            FakeConfig(), FakeAgentManager(), sm,
        )

        assert "No active conversation" in feishu.messages[0][2]

    @pytest.mark.asyncio
    async def test_diff_no_session(self):
        sm = FakeSessionManager(sessions={})
        feishu = FakeFeishu()

        await handle_command(_make_event(text="#diff", root_id="root1"), feishu,
            FakeConfig(), FakeAgentManager(), sm,
        )

        assert "No active conversation" in feishu.messages[0][2]

    @pytest.mark.asyncio
    async def test_mode_no_session(self):
        sm = FakeSessionManager(sessions={})
        feishu = FakeFeishu()

        await handle_command(_make_event(text="#mode", root_id="root1"), feishu,
            FakeConfig(), FakeAgentManager(), sm,
        )

        assert "No active conversation" in feishu.messages[0][2]

    @pytest.mark.asyncio
    async def test_model_no_session(self):
        sm = FakeSessionManager(sessions={})
        feishu = FakeFeishu()

        await handle_command(_make_event(text="#model", root_id="root1"), feishu,
            FakeConfig(), FakeAgentManager(), sm,
        )

        assert "No active conversation" in feishu.messages[0][2]


# ---------------------------------------------------------------------------
# Property-Based Tests
# ---------------------------------------------------------------------------



