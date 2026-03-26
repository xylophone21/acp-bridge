"""Tests for src/feishu.py — unit tests for non-network logic."""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from unittest.mock import AsyncMock, MagicMock

from src.feishu import FeishuConnection, FeishuEvent, FeishuFile


class TestFeishuEvent:
    def test_basic_event(self):
        event = FeishuEvent(
            conversation_id="oc_123",
            message_id="om_456",
            parent_id=None,
            text="hello",
        )
        assert event.conversation_id == "oc_123"
        assert event.message_id == "om_456"
        assert event.parent_id is None
        assert event.text == "hello"
        assert event.files == []

    def test_event_with_parent(self):
        event = FeishuEvent(
            conversation_id="oc_123",
            message_id="om_456",
            parent_id="om_789",
            text="reply",
        )
        assert event.parent_id == "om_789"

    def test_event_with_files(self):
        files = [FeishuFile(file_key="fk1", file_name="test.txt", file_type="text")]
        event = FeishuEvent(
            conversation_id="oc_123",
            message_id="om_456",
            parent_id=None,
            text="see attachment",
            files=files,
        )
        assert len(event.files) == 1
        assert event.files[0].file_key == "fk1"

    def test_event_with_root_id(self):
        event = FeishuEvent(
            conversation_id="oc_123",
            message_id="om_456",
            parent_id="om_789",
            text="reply in thread",
            root_id="om_root_001",
        )
        assert event.root_id == "om_root_001"

    def test_event_with_root_id_none(self):
        event = FeishuEvent(
            conversation_id="oc_123",
            message_id="om_456",
            parent_id=None,
            text="independent message",
        )
        assert event.root_id == ""

    def test_is_mention_bot_defaults_false(self):
        event = FeishuEvent(
            conversation_id="oc_123",
            message_id="om_456",
            parent_id=None,
            text="hello",
        )
        assert event.is_mention_bot is False

    def test_sender_name_defaults_empty(self):
        event = FeishuEvent(
            conversation_id="oc_123",
            message_id="om_456",
            parent_id=None,
            text="hello",
        )
        assert event.sender_id == ""

    def test_event_with_all_new_fields(self):
        event = FeishuEvent(
            conversation_id="oc_123",
            message_id="om_456",
            parent_id="om_789",
            text="@bot help me",
            root_id="om_root_001",
            is_mention_bot=True,
            sender_id="Alice",
        )
        assert event.root_id == "om_root_001"
        assert event.is_mention_bot is True
        assert event.sender_id == "Alice"


class TestFeishuFile:
    def test_file_fields(self):
        f = FeishuFile(file_key="key", file_name="doc.pdf", file_type="pdf")
        assert f.file_key == "key"
        assert f.file_name == "doc.pdf"
        assert f.file_type == "pdf"


# --- _on_message_receive tests ---


def _make_mock_data(
    chat_id="oc_123",
    message_id="om_456",
    chat_type="group",
    content='{"text": "hello"}',
    parent_id=None,
    root_id=None,
    mentions=None,
    bot_open_id=None,
    sender_open_id="ou_sender",
):
    """Build a mock P2ImMessageReceiveV1 object."""
    data = MagicMock()
    msg = data.event.message
    msg.chat_id = chat_id
    msg.message_id = message_id
    msg.chat_type = chat_type
    msg.content = content
    msg.parent_id = parent_id or ""
    msg.root_id = root_id or ""
    msg.mentions = mentions
    msg.update_time = "123"

    data.event.sender.sender_id.open_id = sender_open_id
    return data


def _make_conn(bot_open_id="ou_bot"):
    """Create a FeishuConnection with mocked internals."""
    conn = FeishuConnection.__new__(FeishuConnection)
    conn._bot_open_id = bot_open_id
    conn._event_callback = None
    conn._client = MagicMock()
    return conn


def _receive(conn, data):
    """Call _on_message_receive and return the captured FeishuEvent."""
    captured = []
    conn._event_callback = lambda e: captured.append(e)
    conn._on_message_receive(data)
    return captured[0] if captured else None


class TestOnMessageReceive:
    def test_plain_text_message(self):
        conn = _make_conn()
        data = _make_mock_data(content='{"text": "hello"}')
        event = _receive(conn, data)

        assert event is not None
        assert event.text == "hello"
        assert event.conversation_id == "oc_123"
        assert event.message_id == "om_456"
        assert event.chat_type == "group"

    def test_no_callback_does_nothing(self):
        conn = _make_conn()
        conn._event_callback = None
        data = _make_mock_data()
        # Should not raise
        conn._on_message_receive(data)

    def test_null_event_does_nothing(self):
        conn = _make_conn()
        captured = []
        conn._event_callback = lambda e: captured.append(e)
        data = MagicMock()
        data.event = None
        conn._on_message_receive(data)
        assert captured == []

    def test_null_message_does_nothing(self):
        conn = _make_conn()
        captured = []
        conn._event_callback = lambda e: captured.append(e)
        data = MagicMock()
        data.event.message = None
        conn._on_message_receive(data)
        assert captured == []

    def test_parent_id_empty_string_becomes_none(self):
        conn = _make_conn()
        data = _make_mock_data(parent_id="")
        event = _receive(conn, data)
        assert event.parent_id is None

    def test_parent_id_present(self):
        conn = _make_conn()
        data = _make_mock_data(parent_id="om_parent")
        event = _receive(conn, data)
        assert event.parent_id == "om_parent"

    def test_root_id_from_feishu(self):
        conn = _make_conn()
        data = _make_mock_data(root_id="om_root", parent_id="om_parent")
        event = _receive(conn, data)
        assert event.root_id == "om_root"

    def test_root_id_fallback_to_parent(self):
        conn = _make_conn()
        data = _make_mock_data(root_id="", parent_id="om_parent")
        event = _receive(conn, data)
        assert event.root_id == "om_parent"

    def test_root_id_fallback_to_message_id(self):
        conn = _make_conn()
        data = _make_mock_data(root_id="", parent_id="", message_id="om_self")
        event = _receive(conn, data)
        assert event.root_id == "om_self"

    def test_mention_bot_detected(self):
        conn = _make_conn(bot_open_id="ou_bot")
        mention = MagicMock()
        mention.id.open_id = "ou_bot"
        data = _make_mock_data(mentions=[mention])
        event = _receive(conn, data)
        assert event.is_mention_bot is True

    def test_mention_other_user_not_bot(self):
        conn = _make_conn(bot_open_id="ou_bot")
        mention = MagicMock()
        mention.id.open_id = "ou_other"
        data = _make_mock_data(mentions=[mention])
        event = _receive(conn, data)
        assert event.is_mention_bot is False

    def test_no_mentions(self):
        conn = _make_conn()
        data = _make_mock_data(mentions=None)
        event = _receive(conn, data)
        assert event.is_mention_bot is False

    def test_bot_open_id_not_set(self):
        conn = _make_conn(bot_open_id=None)
        mention = MagicMock()
        mention.id.open_id = "ou_bot"
        data = _make_mock_data(mentions=[mention])
        event = _receive(conn, data)
        assert event.is_mention_bot is False

    def test_invalid_json_content(self):
        conn = _make_conn()
        data = _make_mock_data(content="not json")
        event = _receive(conn, data)
        assert event.text == ""

    def test_empty_content(self):
        conn = _make_conn()
        data = _make_mock_data(content="")
        event = _receive(conn, data)
        assert event.text == ""

    def test_content_missing_text_key(self):
        conn = _make_conn()
        data = _make_mock_data(content='{"file_key": "fk1"}')
        event = _receive(conn, data)
        assert event.text == ""

    def test_sender_id_extracted(self):
        conn = _make_conn()
        data = _make_mock_data(sender_open_id="ou_user_123")
        event = _receive(conn, data)
        assert event.sender_id == "ou_user_123"

    def test_chat_type_p2p(self):
        conn = _make_conn()
        data = _make_mock_data(chat_type="p2p")
        event = _receive(conn, data)
        assert event.chat_type == "p2p"

    def test_exception_does_not_propagate(self):
        """Exceptions in _on_message_receive_inner should be caught."""
        conn = _make_conn()
        conn._event_callback = lambda e: (_ for _ in ()).throw(ValueError("boom"))
        data = _make_mock_data()
        # Should not raise
        conn._on_message_receive(data)


class TestSendMessage:
    @pytest.mark.asyncio
    async def test_reply_uses_reply_api(self):
        conn = _make_conn()
        resp = MagicMock()
        resp.success.return_value = True
        resp.data.message_id = "om_sent"
        conn._client.im.v1.message.areply = AsyncMock(return_value=resp)

        result = await conn.send_message("oc_123", "om_thread", "hi")
        assert result == "om_sent"
        conn._client.im.v1.message.areply.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_reply_uses_create_api(self):
        conn = _make_conn()
        resp = MagicMock()
        resp.success.return_value = True
        resp.data.message_id = "om_sent"
        conn._client.im.v1.message.acreate = AsyncMock(return_value=resp)

        result = await conn.send_message("oc_123", None, "hi")
        assert result == "om_sent"
        conn._client.im.v1.message.acreate.assert_called_once()

    @pytest.mark.asyncio
    async def test_failure_returns_none(self):
        conn = _make_conn()
        resp = MagicMock()
        resp.success.return_value = False
        resp.code = 400
        resp.msg = "bad request"
        conn._client.im.v1.message.acreate = AsyncMock(return_value=resp)

        result = await conn.send_message("oc_123", None, "hi")
        assert result is None


# --- Property-Based Tests ---


_id_strategy = st.text(
    min_size=1,
    max_size=50,
    alphabet=st.characters(whitelist_categories=("L", "N")),
)


class TestRootMessageIdExtraction:
    """**Validates: Requirements 2.1**"""

    @given(
        message_id=_id_strategy,
        root_id=_id_strategy,
    )
    @settings(max_examples=100)
    def test_root_message_id_extraction(self, message_id: str, root_id: str):
        event = FeishuEvent(
            conversation_id="oc_test",
            message_id=message_id,
            parent_id=None,
            text="test",
            root_id=root_id,
        )
        # root_id is always resolved (never empty in real usage)
        assert event.root_id == root_id
