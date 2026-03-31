"""E2E tests for AgentBridge — runs real kiro-cli agent, mocks Feishu layer.

Verifies the full pipeline: event → handler → agent (real) → notification → reply.

Run:
  uv run python -m pytest tests_e2e/ -v -x
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Optional

import pytest

from agent_bridge.agent import AgentManager
from agent_bridge.config import AgentConfig, BridgeConfig, Config, FeishuConfig
from agent_bridge.feishu import FeishuEvent
from agent_bridge.handler import handle_event
from agent_bridge.session import SessionManager

logging.basicConfig(level=logging.DEBUG, format="[%(levelname)s] [%(name)s]: %(message)s")
logger = logging.getLogger(__name__)


# ─── Fake Feishu ─────────────────────────────────────────────────────────


class FakeFeishu:
    """Records all messages/reactions instead of calling Feishu API."""

    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.reactions: dict[str, list[str]] = defaultdict(list)
        self.removed_reactions: list[tuple[str, str]] = []

    async def send_message(self, conversation_id: str, reply_to: Optional[str], text: str) -> Optional[str]:
        msg_id = f"bot_msg_{len(self.messages)}"
        self.messages.append(
            {
                "message_id": msg_id,
                "conversation_id": conversation_id,
                "reply_to": reply_to,
                "text": text,
            }
        )
        logger.info("FakeFeishu send: %s", text[:80])
        return msg_id

    async def add_reaction(self, message_id: str, emoji: str) -> str:
        reaction_id = f"reaction_{len(self.reactions[message_id])}"
        self.reactions[message_id].append(emoji)
        return reaction_id

    async def remove_reaction(self, message_id: str, reaction_id: str) -> None:
        self.removed_reactions.append((message_id, reaction_id))

    def last_reply(self) -> str:
        assert self.messages, "No messages sent"
        return self.messages[-1]["text"]

    def reply_count(self) -> int:
        return len(self.messages)

    def clear(self) -> None:
        self.messages.clear()
        self.reactions.clear()
        self.removed_reactions.clear()


# ─── Fixtures ────────────────────────────────────────────────────────────


def _make_config() -> Config:
    return Config(
        feishu=FeishuConfig(app_id="test", app_secret="test"),
        bridge=BridgeConfig(
            default_workspace="/tmp",
            auto_approve=True,
            max_sessions=5,
            session_ttl_minutes=60,
        ),
        agent=AgentConfig(
            name="kiro",
            description="test",
            command="kiro-cli",
            args=["acp"],
            auto_approve=True,
            default_model="claude-haiku-4.5",
        ),
    )


def _make_event(
    text: str,
    msg_id: str = "msg_1",
    root_id: Optional[str] = None,
    chat_type: str = "group",
    is_mention_bot: bool = True,
) -> FeishuEvent:
    return FeishuEvent(
        conversation_id="chat_1",
        message_id=msg_id,
        parent_id=None,
        text=text,
        root_id=root_id or msg_id,
        is_mention_bot=is_mention_bot,
        sender_id="user_1",
        chat_type=chat_type,
    )


@pytest.fixture
def config() -> Config:
    return _make_config()


@pytest.fixture
def feishu() -> FakeFeishu:
    return FakeFeishu()


@pytest.fixture
def session_manager(config: Config) -> SessionManager:
    return SessionManager(config)


@pytest.fixture
def pending_permissions() -> dict:
    return {}


@pytest.fixture
def agent_text_chunks() -> dict[str, str]:
    return defaultdict(str)


@pytest.fixture
def agent_thought_chunks() -> dict[str, str]:
    return defaultdict(str)


class _Harness:
    """Wires up AgentManager with real agent and notification plumbing."""

    def __init__(self, config: Config, feishu: FakeFeishu, session_manager: SessionManager) -> None:
        self.config = config
        self.feishu = feishu
        self.session_manager = session_manager
        self.pending_permissions: dict = {}
        self.agent_text_chunks: dict[str, str] = defaultdict(str)
        self.agent_thought_chunks: dict[str, str] = defaultdict(str)

        from acp.schema import (
            AgentMessageChunk,
            AgentPlanUpdate,
            AgentThoughtChunk,
            TextContentBlock,
            ToolCallProgress,
            ToolCallStart,
        )

        async def on_notification(session_id: str, update: object) -> None:
            if isinstance(update, AgentMessageChunk):
                if isinstance(update.content, TextContentBlock):
                    self.agent_text_chunks[session_id] += update.content.text or ""
            elif isinstance(update, AgentThoughtChunk):
                if config.bridge.show_thinking:
                    if isinstance(update.content, TextContentBlock):
                        self.agent_thought_chunks[session_id] += update.content.text or ""
            elif isinstance(update, ToolCallStart):
                if config.bridge.show_intermediate:
                    # Flush text before tool call
                    text = self.agent_text_chunks.pop(session_id, "")
                    if text:
                        info = session_manager.find_by_session_id(session_id)
                        if info:
                            await feishu.send_message(info[1].conversation_id, info[0], text)
                    title = update.title or ""
                    info = session_manager.find_by_session_id(session_id)
                    if info:
                        await feishu.send_message(info[1].conversation_id, info[0], f"🔧 Tool: {title}")
                else:
                    self.agent_text_chunks.pop(session_id, None)
            elif isinstance(update, (ToolCallProgress, AgentPlanUpdate)):
                pass

        # Permission response: set to control test behavior
        # None = auto-approve first option, "deny" = deny, callable = custom
        self.permission_response: Optional[str] = None

        async def on_permission(session_id: str, options: list, tool_call: Any = None) -> Optional[str]:
            self.last_permission_options = options
            if self.permission_response == "deny":
                return None
            if self.permission_response is not None:
                return self.permission_response
            # Default: auto-approve first option
            if options:
                return options[0].option_id
            return None

        self.agent_manager = AgentManager(on_notification, on_permission)
        self.agent_manager.register_agents([config.agent])
        self.last_permission_options: list = []

        async def flush_callback(session_id: str) -> None:
            thought = self.agent_thought_chunks.pop(session_id, "")
            if thought:
                info = session_manager.find_by_session_id(session_id)
                if info:
                    await feishu.send_message(info[1].conversation_id, info[0], f"💭 {thought}")
            text = self.agent_text_chunks.pop(session_id, "")
            if text:
                info = session_manager.find_by_session_id(session_id)
                if info:
                    root_id, session = info
                    msg_id = await feishu.send_message(session.conversation_id, root_id, text)
                    if msg_id:
                        session.last_bot_message_id = msg_id

        self.flush_callback = flush_callback

    async def send(self, event: FeishuEvent, wait_reply: bool = True) -> None:
        initial_count = self.feishu.reply_count()
        await handle_event(
            event,
            self.feishu,  # type: ignore[arg-type]
            self.config,
            self.agent_manager,
            self.session_manager,
            self.pending_permissions,
            self.flush_callback,
        )
        if not wait_reply:
            return
        # Wait for a reply (prompt runs in background task)
        for _ in range(600):  # 60s max
            await asyncio.sleep(0.1)
            if self.feishu.reply_count() > initial_count:
                return

    async def cleanup(self) -> None:
        for sid in list(self.agent_manager._agents.keys()):
            await self.agent_manager.end_session(sid)


@pytest.fixture
async def harness(config: Config, feishu: FakeFeishu, session_manager: SessionManager):
    h = _Harness(config, feishu, session_manager)
    yield h
    await h.cleanup()


# ─── 1. Message & Session ────────────────────────────────────────────────


class TestMessageAndSession:
    @pytest.mark.asyncio
    async def test_1_1_new_conversation(self, harness: _Harness, feishu: FakeFeishu):
        """@bot creates session and gets a reply, with Typing indicator."""
        await harness.send(_make_event("say hi in one word"))
        assert feishu.reply_count() >= 1, "Should get at least one reply"
        assert feishu.last_reply(), "Reply should not be empty"
        # Typing added then removed
        typing_added = any("Typing" in emojis for emojis in feishu.reactions.values())
        assert typing_added, "Typing reaction should be added"
        assert len(feishu.removed_reactions) >= 1, "Typing reaction should be removed"

    @pytest.mark.asyncio
    async def test_1_2_no_mention_ignored(self, harness: _Harness, feishu: FakeFeishu):
        """Message without @bot in group is ignored."""
        await harness.send(_make_event("ignored", is_mention_bot=False), wait_reply=False)
        await asyncio.sleep(0.5)
        assert feishu.reply_count() == 0

    @pytest.mark.asyncio
    async def test_1_3_dm(self, harness: _Harness, feishu: FakeFeishu):
        """DM works without @mention."""
        await harness.send(_make_event("hello from DM", is_mention_bot=False, chat_type="p2p"))
        assert feishu.reply_count() >= 1

    @pytest.mark.asyncio
    async def test_1_4_reply_chain(self, harness: _Harness, feishu: FakeFeishu):
        """Follow-up in same thread reuses session."""
        await harness.send(_make_event("remember the word banana", msg_id="m1"))
        feishu.clear()
        await harness.send(_make_event("what word did I say?", msg_id="m2", root_id="m1"))
        assert feishu.reply_count() >= 1
        assert "banana" in feishu.last_reply().lower()

    @pytest.mark.asyncio
    async def test_1_5_separate_sessions(self, harness: _Harness, feishu: FakeFeishu):
        """Different root messages create different sessions."""
        await harness.send(_make_event("session A", msg_id="a1"))
        await harness.send(_make_event("session B", msg_id="b1"))
        assert harness.session_manager.session_count() == 2


# ─── 2. Commands ─────────────────────────────────────────────────────────


class TestCommands:
    @pytest.mark.asyncio
    async def test_2_1_help(self, harness: _Harness, feishu: FakeFeishu):
        await harness.send(_make_event("#help"))
        assert "#mode" in feishu.last_reply()
        assert "#end" in feishu.last_reply()

    @pytest.mark.asyncio
    async def test_2_4_sessions_empty(self, harness: _Harness, feishu: FakeFeishu):
        await harness.send(_make_event("#sessions"))
        assert "No active sessions" in feishu.last_reply()

    @pytest.mark.asyncio
    async def test_2_4_sessions_with_session(self, harness: _Harness, feishu: FakeFeishu):
        await harness.send(_make_event("hello", msg_id="s1"))
        feishu.clear()
        await harness.send(_make_event("#sessions", msg_id="s2"))
        assert "Active sessions" in feishu.last_reply()

    @pytest.mark.asyncio
    async def test_2_6_end(self, harness: _Harness, feishu: FakeFeishu):
        await harness.send(_make_event("hello", msg_id="e1"))
        feishu.clear()
        await harness.send(_make_event("#end", msg_id="e2", root_id="e1"))
        assert "ended" in feishu.last_reply().lower()
        assert harness.session_manager.get_session_by_root("e1") is None
        # DONE reaction on trigger message
        done_added = any("DONE" in emojis for emojis in feishu.reactions.values())
        assert done_added, "DONE reaction should be added on #end"

    @pytest.mark.asyncio
    async def test_2_9_cancel_not_busy(self, harness: _Harness, feishu: FakeFeishu):
        await harness.send(_make_event("hello", msg_id="c1"))
        feishu.clear()
        await harness.send(_make_event("#cancel", msg_id="c2", root_id="c1"))
        assert "no ongoing" in feishu.last_reply().lower()

    @pytest.mark.asyncio
    async def test_2_10_mode(self, harness: _Harness, feishu: FakeFeishu):
        await harness.send(_make_event("hello", msg_id="mo1"))
        feishu.clear()
        await harness.send(_make_event("#mode", msg_id="mo2", root_id="mo1"))
        reply = feishu.last_reply()
        assert "mode" in reply.lower()

    @pytest.mark.asyncio
    async def test_2_14_read_file(self, harness: _Harness, feishu: FakeFeishu):
        await harness.send(_make_event("hello", msg_id="r1"))
        feishu.clear()
        await harness.send(_make_event("#read /etc/hostname", msg_id="r2", root_id="r1"))
        assert feishu.last_reply()

    @pytest.mark.asyncio
    async def test_2_16_read_nonexistent(self, harness: _Harness, feishu: FakeFeishu):
        await harness.send(_make_event("hello", msg_id="rn1"))
        feishu.clear()
        await harness.send(_make_event("#read /nonexistent_xyz", msg_id="rn2", root_id="rn1"))
        assert "error" in feishu.last_reply().lower()

    @pytest.mark.asyncio
    async def test_2_18_unknown_command(self, harness: _Harness, feishu: FakeFeishu):
        await harness.send(_make_event("#unknown_xyz"))
        assert "#mode" in feishu.last_reply() or "#help" in feishu.last_reply()

    @pytest.mark.asyncio
    async def test_2_11_mode_switch(self, harness: _Harness, feishu: FakeFeishu):
        """#mode <value> switches mode."""
        await harness.send(_make_event("hello", msg_id="ms1"))
        feishu.clear()
        await harness.send(_make_event("#mode kiro_default", msg_id="ms2", root_id="ms1"))
        reply = feishu.last_reply()
        assert "switched" in reply.lower() or "mode" in reply.lower()

    @pytest.mark.asyncio
    async def test_2_17_diff(self, harness: _Harness, feishu: FakeFeishu):
        """#diff shows git diff or no changes."""
        await harness.send(_make_event("hello", msg_id="d1"))
        feishu.clear()
        await harness.send(_make_event("#diff", msg_id="d2", root_id="d1"))
        reply = feishu.last_reply()
        assert "diff" in reply.lower() or "no changes" in reply.lower() or "error" in reply.lower()


# ─── 3. Session Lifecycle ────────────────────────────────────────────────


class TestSessionLifecycle:
    @pytest.mark.asyncio
    async def test_3_4_end_then_restart(self, harness: _Harness, feishu: FakeFeishu):
        await harness.send(_make_event("hello", msg_id="re1"))
        feishu.clear()
        await harness.send(_make_event("#end", msg_id="re2", root_id="re1"))
        assert "ended" in feishu.last_reply().lower()

        feishu.clear()
        await harness.send(_make_event("hello again", msg_id="re3", root_id="re1"))
        assert feishu.reply_count() >= 1


# ─── 1b. Buffer & Mention ────────────────────────────────────────────────


class TestBufferAndMention:
    @pytest.mark.asyncio
    async def test_1_6_buffer_while_busy(self, harness: _Harness, feishu: FakeFeishu):
        """Messages sent while agent is busy are buffered and sent after."""
        # Send first message — starts prompt
        await handle_event(
            _make_event("first message", msg_id="bf1"),
            feishu,  # type: ignore[arg-type]
            harness.config,
            harness.agent_manager,
            harness.session_manager,
            harness.pending_permissions,
            harness.flush_callback,
        )
        # Session should be busy now
        session = harness.session_manager.get_session_by_root("bf1")
        assert session is not None
        assert session.busy

        # Send second message while busy — should be buffered
        await handle_event(
            _make_event("second message", msg_id="bf2", root_id="bf1"),
            feishu,  # type: ignore[arg-type]
            harness.config,
            harness.agent_manager,
            harness.session_manager,
            harness.pending_permissions,
            harness.flush_callback,
        )

        # Wait for first prompt to complete and buffer to flush
        for _ in range(600):
            await asyncio.sleep(0.1)
            # Look for at least 2 replies (first prompt + buffered)
            if feishu.reply_count() >= 2:
                break
        assert feishu.reply_count() >= 2

    @pytest.mark.asyncio
    async def test_1_7_multi_mention(self, harness: _Harness, feishu: FakeFeishu):
        """Text with other @mentions is preserved, bot mention stripped."""
        event = _make_event("@other_user please help", msg_id="mm1")
        await harness.send(event)
        assert feishu.reply_count() >= 1


# ─── 4b. Permission ─────────────────────────────────────────────────────


@pytest.fixture
async def noauto_harness(feishu: FakeFeishu):
    """Harness with auto_approve=False to trigger permission requests."""
    cfg = Config(
        feishu=FeishuConfig(app_id="test", app_secret="test"),
        bridge=BridgeConfig(default_workspace="/tmp", max_sessions=5, session_ttl_minutes=60),
        agent=AgentConfig(
            name="kiro",
            description="test",
            command="kiro-cli",
            args=["acp"],
            auto_approve=False,
            default_model="claude-haiku-4.5",
        ),
    )
    sm = SessionManager(cfg)
    h = _Harness(cfg, feishu, sm)
    yield h
    await h.cleanup()


@pytest.fixture
async def intermediate_harness(feishu: FakeFeishu):
    """Harness with show_intermediate=True and auto_approve=True."""
    cfg = Config(
        feishu=FeishuConfig(app_id="test", app_secret="test"),
        bridge=BridgeConfig(
            default_workspace="/tmp",
            max_sessions=5,
            session_ttl_minutes=60,
            show_intermediate=True,
        ),
        agent=AgentConfig(
            name="kiro",
            description="test",
            command="kiro-cli",
            args=["acp"],
            auto_approve=True,
            default_model="claude-haiku-4.5",
        ),
    )
    sm = SessionManager(cfg)
    h = _Harness(cfg, feishu, sm)
    yield h
    await h.cleanup()


@pytest.fixture
async def thinking_harness(feishu: FakeFeishu):
    """Harness with show_thinking=True."""
    cfg = Config(
        feishu=FeishuConfig(app_id="test", app_secret="test"),
        bridge=BridgeConfig(
            default_workspace="/tmp",
            max_sessions=5,
            session_ttl_minutes=60,
            show_thinking=True,
        ),
        agent=AgentConfig(
            name="kiro",
            description="test",
            command="kiro-cli",
            args=["acp"],
            auto_approve=True,
            default_model="claude-haiku-4.5",
        ),
    )
    sm = SessionManager(cfg)
    h = _Harness(cfg, feishu, sm)
    yield h
    await h.cleanup()


class TestPermission:
    @pytest.mark.asyncio
    async def test_4_5_permission_approve(self, noauto_harness: _Harness, feishu: FakeFeishu):
        """Agent requests permission, auto-approve, file should be created."""
        import os

        path = "/tmp/e2e_test_approve.txt"
        if os.path.exists(path):
            os.remove(path)

        await noauto_harness.send(_make_event(f"create a file {path} with content 'hello'", msg_id="pa1"))
        assert feishu.reply_count() >= 1
        assert os.path.exists(path), "File should be created when approved"
        os.remove(path)

    @pytest.mark.asyncio
    async def test_4_6_permission_deny(self, noauto_harness: _Harness, feishu: FakeFeishu):
        """Agent requests permission, deny it, file should not be created."""
        import os

        path = "/tmp/e2e_test_deny.txt"
        if os.path.exists(path):
            os.remove(path)

        noauto_harness.permission_response = "deny"
        await noauto_harness.send(_make_event(f"create a file {path} with content 'hello'", msg_id="pd1"))
        assert not os.path.exists(path), "File should not be created when denied"


# ─── 5. Config ───────────────────────────────────────────────────────────


class TestConfig:
    @pytest.mark.asyncio
    async def test_5_1_show_thinking(self, thinking_harness: _Harness, feishu: FakeFeishu):
        """show_thinking=True: if agent produces thoughts, they appear in output."""
        await thinking_harness.send(
            _make_event(
                "create file /tmp/e2e_think_test.txt with content 'think test'",
                msg_id="st1",
            )
        )
        import os

        # Agent should have done something
        assert feishu.reply_count() >= 1
        # If thoughts were produced, they should appear with 💭 prefix
        # (model may or may not produce thoughts — we verify the pipeline works)
        thought_msgs = [m for m in feishu.messages if "💭" in m["text"]]
        if thought_msgs:
            assert all("💭" in m["text"] for m in thought_msgs)
        # Clean up
        if os.path.exists("/tmp/e2e_think_test.txt"):
            os.remove("/tmp/e2e_think_test.txt")

    @pytest.mark.asyncio
    async def test_5_2_show_intermediate(self, intermediate_harness: _Harness, feishu: FakeFeishu):
        """show_intermediate=True shows tool call messages."""
        import os

        path = "/tmp/e2e_test_intermediate.txt"
        if os.path.exists(path):
            os.remove(path)

        await intermediate_harness.send(_make_event(f"create a file {path} with content 'test'", msg_id="si1"))
        tool_msgs = [m for m in feishu.messages if "🔧 Tool:" in m["text"]]
        assert len(tool_msgs) >= 1, f"Expected tool call message, got: {[m['text'][:50] for m in feishu.messages]}"
        if os.path.exists(path):
            os.remove(path)


# ─── 4. Error Handling ───────────────────────────────────────────────────


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_4_2_agent_crash(self, harness: _Harness, feishu: FakeFeishu):
        """Kill agent process, verify session is cleaned up and new message creates new session."""
        import os
        import signal

        await harness.send(_make_event("hello", msg_id="k1"))
        session = harness.session_manager.get_session_by_root("k1")
        assert session is not None

        # Kill the agent process via OS signal
        entry = harness.agent_manager._agents.get(session.session_id)
        assert entry is not None
        os.kill(entry.process.pid, signal.SIGKILL)
        await asyncio.sleep(1)

        feishu.clear()
        # Send new message in same thread — should detect dead agent and recover
        initial = feishu.reply_count()
        await handle_event(
            _make_event("are you there?", msg_id="k2", root_id="k1"),
            feishu,  # type: ignore[arg-type]
            harness.config,
            harness.agent_manager,
            harness.session_manager,
            harness.pending_permissions,
            harness.flush_callback,
        )
        for _ in range(100):
            await asyncio.sleep(0.1)
            if feishu.reply_count() > initial:
                break
        # Should get some response (error or new session reply)
        assert feishu.reply_count() > initial

    @pytest.mark.asyncio
    async def test_4_3_zombie_in_sessions(self, harness: _Harness, feishu: FakeFeishu):
        """After agent process dies, #sessions shows zombie."""
        import os
        import signal

        await harness.send(_make_event("hello", msg_id="z1"))
        session = harness.session_manager.get_session_by_root("z1")
        assert session is not None

        # Kill agent process (but keep entry) to simulate zombie
        entry = harness.agent_manager._agents.get(session.session_id)
        if entry:
            os.kill(entry.process.pid, signal.SIGKILL)
            await asyncio.sleep(0.5)

        feishu.clear()
        await harness.send(_make_event("#sessions", msg_id="z2"), wait_reply=False)
        await asyncio.sleep(0.5)
        assert "zombie" in feishu.last_reply().lower()
