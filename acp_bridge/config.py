"""Configuration loading and validation."""

import os
from dataclasses import dataclass, field
from typing import Optional

import toml


@dataclass
class FeishuConfig:
    app_id: str
    app_secret: str


@dataclass
class BridgeConfig:
    default_workspace: str = "~"
    attachment_dir: str = "tmp/attachments"
    auto_approve: bool = False
    allowed_users: list[str] = field(default_factory=list)
    max_sessions: int = 10
    session_ttl_minutes: int = 60
    show_thinking: bool = False  # Send agent thinking/reasoning to user
    show_intermediate: bool = False  # Send intermediate output (before tool calls)


@dataclass
class AgentConfig:
    name: str
    description: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    auto_approve: bool = False
    default_mode: Optional[str] = None
    default_model: Optional[str] = None


@dataclass
class Config:
    feishu: FeishuConfig
    bridge: BridgeConfig
    agent: AgentConfig

    @staticmethod
    def load(path: str) -> "Config":
        with open(path) as f:
            data = toml.load(f)

        feishu = FeishuConfig(**data["feishu"])
        bridge = BridgeConfig(**data.get("bridge", {}))

        agent = AgentConfig(**data["agent"])

        config = Config(feishu=feishu, bridge=bridge, agent=agent)
        config._validate()
        return config

    @staticmethod
    def init(path: str, override: bool = False):
        if not override and os.path.exists(path):
            raise FileExistsError(f"File already exists: {path}. Use --override to overwrite.")

        template = """\
[feishu]
app_id = "your_app_id"
app_secret = "your_app_secret"

[bridge]
default_workspace = "~"
attachment_dir = "tmp/attachments"
auto_approve = false
allowed_users = []
max_sessions = 10
session_ttl_minutes = 60
show_thinking = false
show_intermediate = false

[agent]
name = "kiro"
description = "Kiro CLI - https://kiro.dev/cli/"
command = "kiro-cli"
args = ["acp"]
auto_approve = false
"""
        with open(path, "w") as f:
            f.write(template)
        print(f"Config scaffold written to: {path}")

    def _validate(self):
        assert self.agent, "An [agent] section must be configured"
        assert self.agent.name, "Agent name cannot be empty"
        assert self.agent.command, "Agent command cannot be empty"
