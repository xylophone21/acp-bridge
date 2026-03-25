"""Tests for src/bridge.py — unit tests for helper functions."""

from src.bridge import _format_plan


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
