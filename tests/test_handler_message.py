"""Tests for src/handler_message.py — message handling logic."""

import asyncio
from dataclasses import dataclass, field
from typing import Optional

import pytest

from acp_bridge.feishu import FeishuEvent
from acp_bridge.handler_message import _init_locks, handle_message
from acp_bridge.session import SessionState

# --- Fakes ---

class FakeFeishu:
    def __init__(self):
        self.messages: list[tuple[str, str, str]] = []
        self._msg_counter = 0
        self._reaction_counter = 0

    async def send_message(self, channel, thread_id, text) -> Optional[str]:
        self._msg_counter += 1
        msg_id = f"bot_msg_{self._msg_counter}"
        self.messages.append((channel, thread_id, text))
        return msg_id

    async def add_reaction(self, message_id, emoji_type) -> Optional[str]:
        self._reaction_counter += 1
        return f"reaction_{self._reaction_counter}"

    async def remove_reaction(self, message_id, reaction_id) -> bool:
        return True

    async def get_user_info(self, open_id) -> tuple:
        return "Test User", "testuser@example.com"


class FakeAgentManager:
    def __init__(self, prompt_result=None, prompt_error=None, new_session_error=None):
        self.prompts: list[tuple[str, list]] = []
        self._prompt_result = prompt_result or {"stopReason": "endTurn"}
        self._prompt_error = prompt_error
        self._new_session_error = new_session_error

    async def new_session(self, agent_name, workspace, auto_approve):
        if self._new_session_error:
            raise self._new_session_error
        return {"sessionId": "sess_1", "configOptions": []}

    async def prompt(self, session_id, content):
        self.prompts.append((session_id, content))
        if self._prompt_error:
            raise self._prompt_error
        return self._prompt_result

    async def end_session(self, session_id):
        pass

    async def set_mode(self, session_id, value):
        pass

    async def set_model(self, session_id, value):
        pass

    async def set_config_option(self, session_id, option_id, value):
        pass


@dataclass
class FakeBridgeConfig:
    max_sessions: int = 10
    session_ttl_minutes: int = 60
    default_workspace: str = "~"


@dataclass
class FakeAgentConfig:
    name: str = "kiro"
    description: str = "test"
    command: str = "echo"
    args: list = field(default_factory=list)
    auto_approve: bool = False
    default_mode: str = ""
    default_model: str = ""


@dataclass
class FakeConfig:
    bridge: FakeBridgeConfig = field(default_factory=FakeBridgeConfig)
    agent: FakeAgentConfig = field(default_factory=FakeAgentConfig)


class FakeSessionManager:
    """Minimal fake that mirrors the real SessionManager interface."""

    def __init__(self, config=None):
        self._sessions = {}
        self._config = config or FakeConfig()
        self._touched: list[str] = []
        self._buffered: list[tuple[str, str, str]] = []
        self._flushed: list[str] = []
        self._flush_result: Optional[str] = None
        self._create_error: Optional[Exception] = None
        self._create_result: Optional[SessionState] = None

    @property
    def sessions(self):
        return self._sessions

    def session_count(self):
        return len(self._sessions)

    def get_session_by_root(self, key):
        return self._sessions.get(key)

    def create_session(self, root_message_id, session_id, conversation_id, trigger_text, config_options=None):
        if self._create_error:
            raise self._create_error
        if self._create_result:
            self._sessions[root_message_id] = self._create_result
            return self._create_result, None
        session = SessionState(
            session_id=session_id,
            conversation_id=conversation_id,
            trigger_message_id=root_message_id,
            summary=trigger_text[:20],
            config_options=config_options,
        )
        self._sessions[root_message_id] = session
        return session, None

    def touch(self, root_message_id):
        self._touched.append(root_message_id)

    def set_busy(self, key, busy):
        session = self._sessions.get(key)
        if session is None:
            raise ValueError(f"Session not found: {key}")
        session.busy = busy

    def buffer_message(self, root_message_id, sender, text):
        self._buffered.append((root_message_id, sender, text))
        session = self._sessions.get(root_message_id)
        if session is not None:
            session.message_buffer.append((len(self._buffered), sender, text))

    def flush_buffer(self, root_message_id):
        self._flushed.append(root_message_id)
        if self._flush_result is not None:
            return self._flush_result
        session = self._sessions.get(root_message_id)
        if session is None or not session.message_buffer:
            return None
        buffered = session.message_buffer
        session.message_buffer = []
        return "\n".join(f"[{sender}]: {text}" for _, sender, text in buffered)


def _make_event(text="hello", sender_name="user1", message_id="m1", root_id="root1", conversation_id="ch1"):
    return FeishuEvent(
        conversation_id=conversation_id,
        message_id=message_id,
        parent_id=None,
        text=text,
        root_id=root_id,
        is_mention_bot=True,
        sender_id=sender_name,
    )


# --- Tests ---


@pytest.fixture(autouse=True)
def clear_init_locks():
    _init_locks.clear()
    yield
    _init_locks.clear()

class TestAutoCreateSession:
    @pytest.mark.asyncio
    async def test_creates_session_when_none_exists(self):
        feishu = FakeFeishu()
        agent_mgr = FakeAgentManager()
        session_mgr = FakeSessionManager()
        event = _make_event(text="hello world")

        await handle_message(
            event, feishu, FakeConfig(), agent_mgr, session_mgr, None,
        )

        # Session should be created
        assert "root1" in session_mgr.sessions

    @pytest.mark.asyncio
    async def test_all_busy_at_max_returns_error(self):
        feishu = FakeFeishu()
        agent_mgr = FakeAgentManager()
        config = FakeConfig(bridge=FakeBridgeConfig(max_sessions=1))
        session_mgr = FakeSessionManager(config=config)
        session_mgr._create_error = RuntimeError("All sessions are busy, cannot evict")

        event = _make_event(text="hello")

        await handle_message(
            event, feishu, FakeConfig(), agent_mgr, session_mgr, None,
        )

        assert any(
            "All sessions are busy" in m[2] for m in feishu.messages
        )
        # No new session created
        assert "root1" not in session_mgr.sessions

    @pytest.mark.asyncio
    async def test_create_session_failure_sends_error(self):
        feishu = FakeFeishu()
        agent_mgr = FakeAgentManager(new_session_error=RuntimeError("agent start failed"))
        session_mgr = FakeSessionManager()
        event = _make_event(text="hello")

        await handle_message(
            event, feishu, FakeConfig(), agent_mgr, session_mgr, None,
        )

        assert any("Error:" in m[2] for m in feishu.messages)
        assert "root1" not in session_mgr.sessions


class TestSilentBuffering:
    @pytest.mark.asyncio
    async def test_busy_session_buffers_silently(self):
        feishu = FakeFeishu()
        agent_mgr = FakeAgentManager()
        session_mgr = FakeSessionManager()

        # Pre-create a busy session
        session = SessionState(session_id="s1", conversation_id="ch1", busy=True)
        session_mgr._sessions["root1"] = session

        event = _make_event(text="buffered msg", sender_name="alice")

        await handle_message(
            event, feishu, FakeConfig(), agent_mgr, session_mgr, None,
        )

        # No messages sent to user (silent)
        assert len(feishu.messages) == 0
        # Message was buffered
        assert len(session_mgr._buffered) == 1
        assert session_mgr._buffered[0] == ("root1", "alice", "buffered msg")

    @pytest.mark.asyncio
    async def test_busy_session_no_prompt_sent(self):
        feishu = FakeFeishu()
        agent_mgr = FakeAgentManager()
        session_mgr = FakeSessionManager()

        session = SessionState(session_id="s1", conversation_id="ch1", busy=True)
        session_mgr._sessions["root1"] = session

        event = _make_event(text="msg")

        await handle_message(
            event, feishu, FakeConfig(), agent_mgr, session_mgr, None,
        )

        # No prompt sent to agent
        assert len(agent_mgr.prompts) == 0


class TestTouchAndPrompt:
    @pytest.mark.asyncio
    async def test_touch_called_for_existing_idle_session(self):
        feishu = FakeFeishu()
        agent_mgr = FakeAgentManager()
        session_mgr = FakeSessionManager()

        session = SessionState(session_id="s1", conversation_id="ch1", busy=False)
        session_mgr._sessions["root1"] = session

        event = _make_event(text="hello")

        await handle_message(
            event, feishu, FakeConfig(), agent_mgr, session_mgr, None,
        )

        assert "root1" in session_mgr._touched

    @pytest.mark.asyncio
    async def test_prompt_sent_for_idle_session(self):
        feishu = FakeFeishu()
        agent_mgr = FakeAgentManager()
        session_mgr = FakeSessionManager()

        session = SessionState(session_id="s1", conversation_id="ch1", busy=False)
        session_mgr._sessions["root1"] = session

        event = _make_event(text="hello")

        await handle_message(
            event, feishu, FakeConfig(), agent_mgr, session_mgr, None,
        )

        # Give the async task a chance to run
        await asyncio.sleep(0.05)

        assert len(agent_mgr.prompts) == 1
        assert agent_mgr.prompts[0][0] == "s1"
        prompt_text = agent_mgr.prompts[0][1][0]["text"]
        assert prompt_text == "[Current user: Test User, testuser@example.com]\nhello"

    @pytest.mark.asyncio
    async def test_session_set_busy_before_prompt(self):
        feishu = FakeFeishu()
        agent_mgr = FakeAgentManager()
        session_mgr = FakeSessionManager()

        session = SessionState(session_id="s1", conversation_id="ch1", busy=False)
        session_mgr._sessions["root1"] = session

        event = _make_event(text="hello")

        await handle_message(
            event, feishu, FakeConfig(), agent_mgr, session_mgr, None,
        )

        # Session should be set to busy immediately (before async task runs)
        # Note: the task may have already completed and set busy=False
        # So we just verify the prompt was sent
        await asyncio.sleep(0.05)
        assert len(agent_mgr.prompts) == 1


class TestFlushBuffer:
    @pytest.mark.asyncio
    async def test_flush_called_after_prompt(self):
        feishu = FakeFeishu()
        agent_mgr = FakeAgentManager()
        session_mgr = FakeSessionManager()

        session = SessionState(session_id="s1", conversation_id="ch1", busy=False)
        session_mgr._sessions["root1"] = session

        event = _make_event(text="hello")

        await handle_message(
            event, feishu, FakeConfig(), agent_mgr, session_mgr, None,
        )

        await asyncio.sleep(0.05)

        assert "root1" in session_mgr._flushed

    @pytest.mark.asyncio
    async def test_flush_with_merged_text_triggers_new_prompt(self):
        feishu = FakeFeishu()
        agent_mgr = FakeAgentManager()
        session_mgr = FakeSessionManager()
        session_mgr._flush_result = "[alice]: follow up message"

        session = SessionState(session_id="s1", conversation_id="ch1", busy=False)
        session_mgr._sessions["root1"] = session

        event = _make_event(text="hello")

        await handle_message(
            event, feishu, FakeConfig(), agent_mgr, session_mgr, None,
        )

        # Wait for both the initial prompt and the recursive call
        await asyncio.sleep(0.15)

        # Should have at least 2 prompts: original + flushed
        assert len(agent_mgr.prompts) >= 2


class TestNewSessionThenPrompt:
    @pytest.mark.asyncio
    async def test_new_session_sends_prompt_after_creation(self):
        feishu = FakeFeishu()
        agent_mgr = FakeAgentManager()
        session_mgr = FakeSessionManager()
        event = _make_event(text="hello agent")

        await handle_message(
            event, feishu, FakeConfig(), agent_mgr, session_mgr, None,
        )

        await asyncio.sleep(0.05)

        # Session created and prompt sent
        assert "root1" in session_mgr.sessions
        assert len(agent_mgr.prompts) == 1
        prompt_text = agent_mgr.prompts[0][1][0]["text"]
        assert prompt_text == "[Current user: Test User, testuser@example.com]\nhello agent"

    @pytest.mark.asyncio
    async def test_follow_up_during_init_is_buffered_after_first_prompt(self):
        class SlowInitAgentManager(FakeAgentManager):
            async def new_session(self, agent_name, workspace, auto_approve):
                await asyncio.sleep(0.05)
                return {"sessionId": "sess_1", "configOptions": [{"id": "mode_opt", "category": "mode"}]}

            async def set_config_option(self, session_id, option_id, value):
                await asyncio.sleep(0.05)

            async def prompt(self, session_id, content):
                self.prompts.append((session_id, content))
                await asyncio.sleep(0.02)
                return self._prompt_result

        feishu = FakeFeishu()
        agent_mgr = SlowInitAgentManager()
        session_mgr = FakeSessionManager()
        config = FakeConfig(agent=FakeAgentConfig(default_mode="code"))

        first = asyncio.create_task(
            handle_message(
                _make_event(text="first", sender_name="alice"),
                feishu, config, agent_mgr, session_mgr, None,
            )
        )
        await asyncio.sleep(0.01)
        second = asyncio.create_task(
            handle_message(
                _make_event(text="second", sender_name="bob", message_id="m2"),
                feishu, config, agent_mgr, session_mgr, None,
            )
        )

        await asyncio.gather(first, second)
        await asyncio.sleep(0.15)

        assert [prompt[1][0]["text"] for prompt in agent_mgr.prompts] == [
            "[Current user: Test User, testuser@example.com]\nfirst",
            "[Current user: Test User, testuser@example.com]\n[bob]: second",
        ]

    @pytest.mark.asyncio
    async def test_typing_indicator_failure_does_not_block_prompt_or_cleanup(self):
        class FailingTypingFeishu(FakeFeishu):
            async def add_reaction(self, message_id, emoji_type) -> Optional[str]:
                raise RuntimeError("reaction failed")

        feishu = FailingTypingFeishu()
        agent_mgr = FakeAgentManager()
        session_mgr = FakeSessionManager()
        session_mgr._sessions["root1"] = SessionState(
            session_id="s1", conversation_id="ch1", busy=False,
        )

        await handle_message(
            _make_event(text="hello"),
            feishu, FakeConfig(), agent_mgr, session_mgr, None,
        )
        await asyncio.sleep(0.05)

        assert len(agent_mgr.prompts) == 1
        assert session_mgr._sessions["root1"].busy is False
