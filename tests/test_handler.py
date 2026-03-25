"""Tests for src/handler.py — event routing logic."""

import asyncio

import pytest

from src.feishu import FeishuEvent


class FakeFeishu:
    def __init__(self):
        self.messages = []

    def send_message(self, channel, thread_id, text):
        self.messages.append((channel, thread_id, text))
        return "msg_id_1"


class FakeSessionManager:
    def __init__(self):
        self.sessions = {}

    async def get_session(self, key):
        return self.sessions.get(key)


class FakeAgentManager:
    pass


class FakeConfig:
    class bridge:
        allowed_users = []
        default_workspace = "~"
    agents = []


class TestHandleEvent:
    @pytest.mark.asyncio
    async def test_help_command(self):
        from src.handler import handle_event

        feishu = FakeFeishu()
        event = FeishuEvent(chat_id="ch1", message_id="m1", parent_id=None, text="#help")

        await handle_event(
            event, feishu, FakeConfig(), FakeAgentManager(),
            FakeSessionManager(), {}, None,
        )
        assert len(feishu.messages) == 1
        assert "Available commands" in feishu.messages[0][2]

    @pytest.mark.asyncio
    async def test_shell_command(self):
        from src.handler import handle_event

        feishu = FakeFeishu()
        event = FeishuEvent(chat_id="ch1", message_id="m1", parent_id=None, text="!echo hi")

        await handle_event(
            event, feishu, FakeConfig(), FakeAgentManager(),
            FakeSessionManager(), {}, None,
        )
        assert len(feishu.messages) == 1
        assert "hi" in feishu.messages[0][2]

    @pytest.mark.asyncio
    async def test_regular_message_no_session(self):
        from src.handler import handle_event

        feishu = FakeFeishu()
        event = FeishuEvent(chat_id="ch1", message_id="m1", parent_id=None, text="hello agent")

        await handle_event(
            event, feishu, FakeConfig(), FakeAgentManager(),
            FakeSessionManager(), {}, None,
        )
        assert len(feishu.messages) == 1
        assert "No active session" in feishu.messages[0][2]

    @pytest.mark.asyncio
    async def test_permission_response_routing(self):
        from src.handler import handle_event

        feishu = FakeFeishu()
        future = asyncio.get_event_loop().create_future()
        pending = {
            "m1": {
                "options": [{"optionId": "opt1", "name": "Allow"}],
                "future": future,
            }
        }
        event = FeishuEvent(chat_id="ch1", message_id="m1", parent_id=None, text="1")

        await handle_event(
            event, feishu, FakeConfig(), FakeAgentManager(),
            FakeSessionManager(), pending, None,
        )
        assert future.result() == "opt1"
        assert "m1" not in pending
