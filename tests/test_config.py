"""Tests for src/config.py"""

import os
import tempfile

import pytest

from src.config import Config, FeishuConfig, BridgeConfig, AgentConfig


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
allowed_users = ["user1"]

[[agents]]
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
        assert config.bridge.allowed_users == ["user1"]
        assert len(config.agents) == 1
        assert config.agents[0].name == "test-agent"
        assert config.agents[0].command == "echo"
        assert config.agents[0].args == ["hello"]

    def test_load_minimal_config(self, tmp_path):
        config_file = tmp_path / "minimal.toml"
        config_file.write_text("""\
[feishu]
app_id = "id"
app_secret = "secret"

[[agents]]
name = "a"
description = "d"
command = "c"
""")
        config = Config.load(str(config_file))
        assert config.bridge.default_workspace == "~"
        assert config.bridge.auto_approve is False
        assert config.bridge.allowed_users == []
        assert config.agents[0].env == {}
        assert config.agents[0].default_mode is None

    def test_load_no_agents_fails(self, tmp_path):
        config_file = tmp_path / "bad.toml"
        config_file.write_text("""\
[feishu]
app_id = "id"
app_secret = "secret"
""")
        with pytest.raises(AssertionError, match="At least one agent"):
            Config.load(str(config_file))

    def test_load_empty_agent_name_fails(self, tmp_path):
        config_file = tmp_path / "bad2.toml"
        config_file.write_text("""\
[feishu]
app_id = "id"
app_secret = "secret"

[[agents]]
name = ""
description = "d"
command = "c"
""")
        with pytest.raises(AssertionError, match="Agent name cannot be empty"):
            Config.load(str(config_file))

    def test_load_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            Config.load("/nonexistent/path.toml")

    def test_load_multiple_agents(self, tmp_path):
        config_file = tmp_path / "multi.toml"
        config_file.write_text("""\
[feishu]
app_id = "id"
app_secret = "secret"

[[agents]]
name = "agent1"
description = "first"
command = "cmd1"

[[agents]]
name = "agent2"
description = "second"
command = "cmd2"
auto_approve = true
default_mode = "fast"
default_model = "gpt-4"
""")
        config = Config.load(str(config_file))
        assert len(config.agents) == 2
        assert config.agents[1].auto_approve is True
        assert config.agents[1].default_mode == "fast"
        assert config.agents[1].default_model == "gpt-4"


class TestConfigInit:
    def test_init_creates_file(self, tmp_path):
        path = str(tmp_path / "new.toml")
        Config.init(path)
        assert os.path.exists(path)
        # Verify it's loadable (except validation — placeholder values)
        with open(path) as f:
            content = f.read()
        assert "app_id" in content
        assert "app_secret" in content
        assert "agents" in content

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
