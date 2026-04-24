"""Configuration loading and validation."""

import os
from dataclasses import dataclass, field
from typing import Any, Optional

import toml


@dataclass
class FeishuConfig:
    app_id: str = "your_app_id"
    app_secret: str = "your_app_secret"


@dataclass
class BridgeConfig:
    default_workspace: str = "~"
    attachment_dir: str = "tmp/attachments"
    output_dir: str = "tmp/output"  # Agent output dir (images, scripts, etc.); also limits image uploads
    auto_approve: bool = False
    max_sessions: int = 10
    session_ttl_minutes: int = 720
    show_thinking: bool = False  # Send agent thinking/reasoning to user
    show_intermediate: bool = False  # Send intermediate output (before tool calls)


@dataclass
class AgentConfig:
    name: str = "kiro"
    description: str = "Kiro CLI - https://kiro.dev/cli/"
    command: str = "kiro-cli"
    args: list[str] = field(default_factory=lambda: ["acp"])
    env: dict[str, str] = field(default_factory=dict)
    auto_approve: bool = True
    default_mode: Optional[str] = None
    default_model: Optional[str] = None


@dataclass
class EvaluatorConfig:
    name: str = "my-evaluator"
    trigger_pattern: str = "Conclusion|Result|Finding"  # regex to match agent response, triggers evaluation
    command: str = ""  # fallback to [agent].command
    args: list[str] = field(default_factory=lambda: ["acp", "--agent", "my-evaluator"])  # fallback to [agent].args
    env: dict[str, str] = field(default_factory=dict)  # fallback to [agent].env
    workspace: str = ""  # fallback to bridge.default_workspace
    auto_approve: bool | None = None  # fallback to [agent].auto_approve
    prompt: str = (  # prepended to agent text when sending to evaluator
        "Please evaluate the following report.\n"
        "End the final text response with a standalone line: RESULT: PASS or RESULT: FAIL."
    )
    pass_pattern: str = r"(?mi)^\s*RESULT\s*:\s*PASS\s*$"  # regex to match evaluator response, means passed
    max_retries: int = 2
    retry_prompt: str = (  # prompt to ask main agent to retry
        "The evaluator found issues with your previous response\n"
        "Please revise and output the complete response again from the beginning\n"
        "(do not only output the changed parts):\n"
        "\n"
        "{feedback}"
    )


@dataclass
class Config:
    feishu: FeishuConfig
    bridge: BridgeConfig
    agent: AgentConfig
    evaluators: list[EvaluatorConfig] = field(default_factory=list)

    @staticmethod
    def load(path: str) -> "Config":
        import dataclasses

        def _filter(cls, data: dict) -> dict:
            names = {f.name for f in dataclasses.fields(cls)}
            return {k: v for k, v in data.items() if k in names}

        with open(path) as f:
            data = toml.load(f)

        feishu = FeishuConfig(**_filter(FeishuConfig, data["feishu"]))
        bridge = BridgeConfig(**_filter(BridgeConfig, data.get("bridge", {})))
        agent = AgentConfig(**_filter(AgentConfig, data["agent"]))

        evaluators = [
            EvaluatorConfig(**_filter(EvaluatorConfig, e))
            for e in data.get("evaluator", [])
        ]

        config = Config(feishu=feishu, bridge=bridge, agent=agent, evaluators=evaluators)
        config._validate()
        return config

    @staticmethod
    def init(path: str, override: bool = False):
        if not override and os.path.exists(path):
            raise FileExistsError(f"File already exists: {path}. Use --override to overwrite.")

        import collections
        import dataclasses

        def _to_dict(obj) -> dict:
            return collections.OrderedDict(
                (f.name, getattr(obj, f.name))
                for f in dataclasses.fields(obj)
                if getattr(obj, f.name) is not None
                and getattr(obj, f.name) != {}
            )

        main: dict[str, Any] = collections.OrderedDict([
            ("feishu", _to_dict(FeishuConfig())),
            ("bridge", _to_dict(BridgeConfig())),
            ("agent", _to_dict(AgentConfig())),
        ])
        ev = {"evaluator": [_to_dict(
            EvaluatorConfig(),
        )]}

        with open(path, "w") as f:
            f.write(toml.dumps(main))
            f.write("\n")
            f.write(toml.dumps(ev))
        print(f"Config scaffold written to: {path}")

    def _validate(self):
        assert self.agent, "An [agent] section must be configured"
        assert self.agent.name, "Agent name cannot be empty"
        assert self.agent.command, "Agent command cannot be empty"

        # Evaluator fields fallback to [agent] when empty
        names = set()
        for ev in self.evaluators:
            assert ev.name, "Evaluator name cannot be empty"
            assert ev.name not in names, f"Duplicate evaluator name: {ev.name}"
            names.add(ev.name)
        for ev in self.evaluators:
            if not ev.command.strip():
                ev.command = self.agent.command
            if not ev.args:
                ev.args = list(self.agent.args)
            if not ev.env and self.agent.env:
                ev.env = dict(self.agent.env)
            if not ev.workspace.strip():
                ev.workspace = self.bridge.default_workspace
            if ev.auto_approve is None:
                ev.auto_approve = self.agent.auto_approve
