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
    auto_approve: bool = False
    allowed_users: list[str] = field(default_factory=list)


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
    agents: list[AgentConfig]

    @staticmethod
    def load(path: str) -> "Config":
        with open(path) as f:
            data = toml.load(f)

        feishu = FeishuConfig(**data["feishu"])
        bridge = BridgeConfig(**{k: v for k, v in data.get("bridge", {}).items()})

        agents = []
        for a in data.get("agents", []):
            agents.append(AgentConfig(**a))

        config = Config(feishu=feishu, bridge=bridge, agents=agents)
        config._validate()
        return config

    @staticmethod
    def init(path: str, override: bool = False):
        if not override and os.path.exists(path):
            raise FileExistsError(f"File already exists: {path}. Use --override to overwrite.")

        template = """\
[feishu]
app_id = "your-app-id"
app_secret = "your-app-secret"

[bridge]
default_workspace = "~"
auto_approve = false
allowed_users = []

[[agents]]
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
        assert self.agents, "At least one agent must be configured"
        for agent in self.agents:
            assert agent.name, "Agent name cannot be empty"
            assert agent.command, "Agent command cannot be empty"
