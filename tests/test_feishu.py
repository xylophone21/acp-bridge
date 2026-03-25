"""Tests for src/feishu.py — unit tests for non-network logic."""

from src.feishu import FeishuEvent, FeishuFile


class TestFeishuEvent:
    def test_basic_event(self):
        event = FeishuEvent(
            chat_id="oc_123",
            message_id="om_456",
            parent_id=None,
            text="hello",
        )
        assert event.chat_id == "oc_123"
        assert event.message_id == "om_456"
        assert event.parent_id is None
        assert event.text == "hello"
        assert event.files == []

    def test_event_with_parent(self):
        event = FeishuEvent(
            chat_id="oc_123",
            message_id="om_456",
            parent_id="om_789",
            text="reply",
        )
        assert event.parent_id == "om_789"

    def test_event_with_files(self):
        files = [FeishuFile(file_key="fk1", file_name="test.txt", file_type="text")]
        event = FeishuEvent(
            chat_id="oc_123",
            message_id="om_456",
            parent_id=None,
            text="see attachment",
            files=files,
        )
        assert len(event.files) == 1
        assert event.files[0].file_key == "fk1"


class TestFeishuFile:
    def test_file_fields(self):
        f = FeishuFile(file_key="key", file_name="doc.pdf", file_type="pdf")
        assert f.file_key == "key"
        assert f.file_name == "doc.pdf"
        assert f.file_type == "pdf"
