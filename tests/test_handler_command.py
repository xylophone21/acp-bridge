"""Tests for src/handler_command.py"""

import pytest

from src.handler_command import _parse_workspace_and_comment


class TestParseWorkspaceAndComment:
    def test_no_args(self):
        ws, cmt = _parse_workspace_and_comment([])
        assert ws is None
        assert cmt is None

    def test_workspace_only(self):
        ws, cmt = _parse_workspace_and_comment(["~/project"])
        assert ws == "~/project"
        assert cmt is None

    def test_comment_only(self):
        ws, cmt = _parse_workspace_and_comment(["--", "fix", "bug"])
        assert ws is None
        assert cmt == "fix bug"

    def test_workspace_and_comment(self):
        ws, cmt = _parse_workspace_and_comment(["~/project", "--", "fix", "bug"])
        assert ws == "~/project"
        assert cmt == "fix bug"

    def test_separator_only(self):
        ws, cmt = _parse_workspace_and_comment(["--"])
        assert ws is None
        assert cmt is None

    def test_single_word_comment(self):
        ws, cmt = _parse_workspace_and_comment(["--", "refactor"])
        assert ws is None
        assert cmt == "refactor"
