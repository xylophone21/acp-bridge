"""Tests for src/utils.py"""

from agent_bridge.utils import expand_path, safe_backticks


class TestExpandPath:
    def test_expand_tilde(self):
        result = expand_path("~/test")
        assert "~" not in result
        assert result.endswith("/test")

    def test_expand_env_var(self, monkeypatch):
        monkeypatch.setenv("MY_TEST_VAR", "/custom/path")
        result = expand_path("$MY_TEST_VAR/sub")
        assert result == "/custom/path/sub"

    def test_plain_path(self):
        assert expand_path("/absolute/path") == "/absolute/path"

    def test_relative_path(self):
        assert expand_path("relative/path") == "relative/path"


class TestSafeBackticks:
    def test_no_backticks(self):
        assert safe_backticks("hello world") == "```"

    def test_with_triple_backticks(self):
        result = safe_backticks("some ``` code")
        assert result == "````"
        assert "```" in result

    def test_with_quad_backticks(self):
        result = safe_backticks("some ```` and ``` code")
        assert result == "`````"

    def test_empty_string(self):
        assert safe_backticks("") == "```"
