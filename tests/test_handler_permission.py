"""Tests for src/handler_permission.py"""

import pytest
from acp.schema import PermissionOption

from agent_bridge.handler_permission import handle_permission_response


class FakeFeishu:
    def __init__(self):
        self.messages = []

    async def send_message(self, channel, thread_id, text):
        self.messages.append((channel, thread_id, text))


@pytest.fixture
def feishu():
    return FakeFeishu()


@pytest.fixture
def options():
    return [
        PermissionOption(option_id="opt1", name="Allow read", kind="allow_once"),
        PermissionOption(option_id="opt2", name="Allow write", kind="allow_once"),
    ]


class TestHandlePermissionResponse:
    @pytest.mark.asyncio
    async def test_deny(self, feishu, options):
        result = await handle_permission_response("deny", options, feishu, "ch1", "t1")
        assert result is None
        assert "denied" in feishu.messages[0][2].lower()

    @pytest.mark.asyncio
    async def test_deny_case_insensitive(self, feishu, options):
        result = await handle_permission_response("DENY", options, feishu, "ch1", "t1")
        assert result is None

    @pytest.mark.asyncio
    async def test_valid_choice_1(self, feishu, options):
        result = await handle_permission_response("1", options, feishu, "ch1", "t1")
        assert result == "opt1"
        assert "Approved" in feishu.messages[0][2]

    @pytest.mark.asyncio
    async def test_valid_choice_2(self, feishu, options):
        result = await handle_permission_response("2", options, feishu, "ch1", "t1")
        assert result == "opt2"

    @pytest.mark.asyncio
    async def test_out_of_range(self, feishu, options):
        result = await handle_permission_response("3", options, feishu, "ch1", "t1")
        assert result is None
        assert "Invalid" in feishu.messages[0][2]

    @pytest.mark.asyncio
    async def test_zero(self, feishu, options):
        result = await handle_permission_response("0", options, feishu, "ch1", "t1")
        assert result is None

    @pytest.mark.asyncio
    async def test_garbage_input(self, feishu, options):
        result = await handle_permission_response("asdf", options, feishu, "ch1", "t1")
        assert result is None
        assert "Invalid" in feishu.messages[0][2]

    @pytest.mark.asyncio
    async def test_whitespace_trimmed(self, feishu, options):
        result = await handle_permission_response("  1  ", options, feishu, "ch1", "t1")
        assert result == "opt1"
