"""Tests for src/feishu.py — unit tests for non-network logic."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from acp_bridge.feishu import FeishuConnection, FeishuEvent, FeishuFile


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
    message_type="text",
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
    msg.message_type = message_type
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

    def test_image_message(self):
        conn = _make_conn()
        data = _make_mock_data(
            message_type="image",
            content='{"image_key": "img_v3_abc"}',
        )
        event = _receive(conn, data)
        assert len(event.files) == 1
        assert event.files[0].file_key == "img_v3_abc"
        assert event.files[0].file_type == "image"
        assert "{{attachment:img_v3_abc}}" in event.text

    def test_file_message(self):
        conn = _make_conn()
        data = _make_mock_data(
            message_type="file",
            content='{"file_key": "fk_123", "file_name": "report.pdf"}',
        )
        event = _receive(conn, data)
        assert len(event.files) == 1
        assert event.files[0].file_key == "fk_123"
        assert event.files[0].file_name == "fk_123_report.pdf"
        assert event.files[0].file_type == "file"
        assert "{{attachment:fk_123}}" in event.text

    def test_post_message_with_images(self):
        conn = _make_conn()
        content = json.dumps({
            "title": "",
            "content": [
                [
                    {"tag": "text", "text": "看看这个"},
                    {"tag": "img", "image_key": "img_v3_001"},
                ],
                [
                    {"tag": "text", "text": "还有这个"},
                    {"tag": "img", "image_key": "img_v3_002"},
                ],
            ],
        })
        data = _make_mock_data(message_type="post", content=content)
        event = _receive(conn, data)
        assert len(event.files) == 2
        # Order preserved: text, image, text, image
        assert event.text.index("看看这个") < event.text.index("{{attachment:img_v3_001}}")
        assert event.text.index("还有这个") < event.text.index("{{attachment:img_v3_002}}")

    def test_post_message_text_only(self):
        conn = _make_conn()
        content = json.dumps({
            "title": "标题",
            "content": [[{"tag": "text", "text": "纯文字"}]],
        })
        data = _make_mock_data(message_type="post", content=content)
        event = _receive(conn, data)
        assert event.files == []
        assert "标题" in event.text
        assert "纯文字" in event.text


class TestDownloadAttachments:
    @pytest.mark.asyncio
    async def test_download_and_replace_placeholders(self, tmp_path):
        conn = _make_conn()
        conn._download_file = AsyncMock(return_value=b"fake image data")
        event = FeishuEvent(
            conversation_id="oc_1",
            message_id="om_1",
            parent_id=None,
            text="看看 {{attachment:img_k1}} 这个",
            files=[FeishuFile(file_key="img_k1", file_name="img_k1.png", file_type="image")],
        )
        result = await conn.resolve_attachments(event, str(tmp_path), "attachments")
        assert "[Attached image:" in result
        assert "img_k1.png" in result
        assert "{{attachment:" not in result
        assert (tmp_path / "attachments" / "img_k1.png").read_bytes() == b"fake image data"

    @pytest.mark.asyncio
    async def test_download_failure_removes_placeholder(self, tmp_path):
        conn = _make_conn()
        conn._download_file = AsyncMock(side_effect=Exception("network error"))
        event = FeishuEvent(
            conversation_id="oc_1",
            message_id="om_1",
            parent_id=None,
            text="看看 {{attachment:img_k1}}",
            files=[FeishuFile(file_key="img_k1", file_name="img_k1.png", file_type="image")],
        )
        result = await conn.resolve_attachments(event, str(tmp_path), "attachments")
        assert "{{attachment:" not in result


class TestGetParentFiles:
    def _mock_get_response(self, conn, msg_type, content_str):
        resp = MagicMock()
        resp.success.return_value = True
        item = MagicMock()
        item.msg_type = msg_type
        item.body.content = content_str
        resp.data.items = [item]
        conn._client.im.v1.message.aget = AsyncMock(return_value=resp)

    @pytest.mark.asyncio
    async def test_parent_image_message(self):
        conn = _make_conn()
        self._mock_get_response(conn, "image", '{"image_key": "img_k1"}')
        text, files = await conn._get_parent_content("om_parent")
        assert len(files) == 1
        assert files[0].file_key == "img_k1"
        assert files[0].file_type == "image"

    @pytest.mark.asyncio
    async def test_parent_file_message(self):
        conn = _make_conn()
        self._mock_get_response(conn, "file", '{"file_key": "fk_1", "file_name": "doc.pdf"}')
        text, files = await conn._get_parent_content("om_parent")
        assert len(files) == 1
        assert files[0].file_key == "fk_1"
        assert files[0].file_name == "fk_1_doc.pdf"
        assert files[0].file_type == "file"

    @pytest.mark.asyncio
    async def test_parent_post_with_images(self):
        conn = _make_conn()
        content = json.dumps({
            "content": [[
                {"tag": "text", "text": "hello"},
                {"tag": "img", "image_key": "img_a"},
                {"tag": "img", "image_key": "img_b"},
            ]]
        })
        self._mock_get_response(conn, "post", content)
        text, files = await conn._get_parent_content("om_parent")
        assert len(files) == 2
        assert files[0].file_key == "img_a"
        assert files[1].file_key == "img_b"

    @pytest.mark.asyncio
    async def test_parent_text_message_returns_empty(self):
        conn = _make_conn()
        self._mock_get_response(conn, "text", '{"text": "hello"}')
        text, files = await conn._get_parent_content("om_parent")
        assert files == []
        assert text == "hello"

    @pytest.mark.asyncio
    async def test_parent_api_failure_returns_empty(self):
        conn = _make_conn()
        resp = MagicMock()
        resp.success.return_value = False
        conn._client.im.v1.message.aget = AsyncMock(return_value=resp)
        text, files = await conn._get_parent_content("om_parent")
        assert text == ""
        assert files == []
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
