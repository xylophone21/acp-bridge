"""Tests for src/config.py"""

import os

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from acp_bridge.config import Config


class TestConfigLoad:
    def test_load_valid_config(self, tmp_path):
        config_file = tmp_path / "test.toml"
        config_file.write_text("""\
[feishu]
app_id = "test-id"
app_secret = "test-secret"

[bridge]
default_workspace = "/tmp"
auto_approve = true
max_sessions = 5
session_ttl_minutes = 30

[agent]
name = "test-agent"
description = "A test agent"
command = "echo"
args = ["hello"]
auto_approve = false
""")
        config = Config.load(str(config_file))
        assert config.feishu.app_id == "test-id"
        assert config.feishu.app_secret == "test-secret"
        assert config.bridge.default_workspace == "/tmp"
        assert config.bridge.auto_approve is True
        assert config.bridge.max_sessions == 5
        assert config.bridge.session_ttl_minutes == 30
        assert config.agent.name == "test-agent"
        assert config.agent.command == "echo"
        assert config.agent.args == ["hello"]

    def test_load_minimal_config(self, tmp_path):
        config_file = tmp_path / "minimal.toml"
        config_file.write_text("""\
[feishu]
app_id = "id"
app_secret = "secret"

[agent]
name = "a"
description = "d"
command = "c"
""")
        config = Config.load(str(config_file))
        assert config.bridge.default_workspace == "~"
        assert config.bridge.auto_approve is False
        assert config.agent.env == {}
        assert config.agent.default_mode is None

    def test_load_missing_agent_fails(self, tmp_path):
        """Missing [agent] section should raise KeyError."""
        config_file = tmp_path / "bad.toml"
        config_file.write_text("""\
[feishu]
app_id = "id"
app_secret = "secret"
""")
        with pytest.raises(KeyError, match="agent"):
            Config.load(str(config_file))

    def test_load_empty_agent_name_fails(self, tmp_path):
        config_file = tmp_path / "bad2.toml"
        config_file.write_text("""\
[feishu]
app_id = "id"
app_secret = "secret"

[agent]
name = ""
description = "d"
command = "c"
""")
        with pytest.raises(AssertionError, match="Agent name cannot be empty"):
            Config.load(str(config_file))

    def test_load_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            Config.load("/nonexistent/path.toml")

    def test_load_default_max_sessions_and_ttl(self, tmp_path):
        """max_sessions defaults to 10 and session_ttl_minutes defaults to 60
        when not specified in config."""
        config_file = tmp_path / "defaults.toml"
        config_file.write_text("""\
[feishu]
app_id = "id"
app_secret = "secret"

[agent]
name = "a"
description = "d"
command = "c"
""")
        config = Config.load(str(config_file))
        assert config.bridge.max_sessions == 10
        assert config.bridge.session_ttl_minutes == 60


class TestConfigInit:
    def test_init_creates_file(self, tmp_path):
        path = str(tmp_path / "new.toml")
        Config.init(path)
        assert os.path.exists(path)
        with open(path) as f:
            content = f.read()
        assert "app_id" in content
        assert "app_secret" in content

    def test_init_no_override(self, tmp_path):
        path = str(tmp_path / "existing.toml")
        with open(path, "w") as f:
            f.write("existing")
        with pytest.raises(FileExistsError):
            Config.init(path, override=False)

    def test_init_with_override(self, tmp_path):
        path = str(tmp_path / "existing.toml")
        with open(path, "w") as f:
            f.write("existing")
        Config.init(path, override=True)
        with open(path) as f:
            content = f.read()
        assert "app_id" in content

    def test_init_generates_correct_format(self, tmp_path):
        """Verify init generates sample config with [agent] single table,
        max_sessions, and session_ttl_minutes."""
        path = str(tmp_path / "sample.toml")
        Config.init(path)
        with open(path) as f:
            content = f.read()
        # Should use [agent] single table, not [[agents]]
        assert "[agent]" in content
        assert "[[agents]]" not in content
        # Should include session management fields
        assert "max_sessions" in content
        assert "session_ttl_minutes" in content
        # Should be parseable as valid TOML with correct structure
        import toml
        data = toml.loads(content)
        assert "agent" in data
        assert isinstance(data["agent"], dict)
        assert "bridge" in data
        assert "max_sessions" in data["bridge"]
        assert "session_ttl_minutes" in data["bridge"]


# Feature: session-refactor, Property 13: 配置缺省值
# Validates: Requirements 5.5


@given(
    include_max_sessions=st.booleans(),
    include_session_ttl=st.booleans(),
    max_sessions_val=st.integers(min_value=1, max_value=1000),
    session_ttl_val=st.integers(min_value=1, max_value=10080),
)
@settings(max_examples=100)
def test_config_default_values_property(
    tmp_path_factory,
    include_max_sessions,
    include_session_ttl,
    max_sessions_val,
    session_ttl_val,
):
    """Property 13: 配置缺省值 — when max_sessions or session_ttl_minutes
    are missing from the config, they default to 10 and 60 respectively.
    When present, they match the provided values."""
    tmp_path = tmp_path_factory.mktemp("cfg")
    bridge_lines = []
    if include_max_sessions or include_session_ttl:
        bridge_lines.append("[bridge]")
        if include_max_sessions:
            bridge_lines.append(f"max_sessions = {max_sessions_val}")
        if include_session_ttl:
            bridge_lines.append(f"session_ttl_minutes = {session_ttl_val}")

    bridge_section = "\n".join(bridge_lines)

    config_content = f"""\
[feishu]
app_id = "test-id"
app_secret = "test-secret"

{bridge_section}

[agent]
name = "test"
description = "test agent"
command = "echo"
"""
    config_file = tmp_path / "test.toml"
    config_file.write_text(config_content)

    config = Config.load(str(config_file))

    if include_max_sessions:
        assert config.bridge.max_sessions == max_sessions_val
    else:
        assert config.bridge.max_sessions == 10

    if include_session_ttl:
        assert config.bridge.session_ttl_minutes == session_ttl_val
    else:
        assert config.bridge.session_ttl_minutes == 60
