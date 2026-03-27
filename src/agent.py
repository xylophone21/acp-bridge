"""Agent manager for spawning and communicating with ACP agents via stdio."""

from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack
from typing import Any, Awaitable, Callable, Optional, Union

import acp
from acp.core import ClientSideConnection
from acp.interfaces import Client
from acp.schema import (
    AgentMessageChunk,
    AgentPlanUpdate,
    AgentThoughtChunk,
    AllowedOutcome,
    AvailableCommandsUpdate,
    ConfigOptionUpdate,
    CurrentModeUpdate,
    DeniedOutcome,
    PermissionOption,
    RequestPermissionResponse,
    SessionInfoUpdate,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
    UsageUpdate,
    UserMessageChunk,
)

from src.config import AgentConfig

logger = logging.getLogger(__name__)

# Type alias for the session_update union
SessionUpdate = Union[
    UserMessageChunk,
    AgentMessageChunk,
    AgentThoughtChunk,
    ToolCallStart,
    ToolCallProgress,
    AgentPlanUpdate,
    AvailableCommandsUpdate,
    CurrentModeUpdate,
    ConfigOptionUpdate,
    SessionInfoUpdate,
    UsageUpdate,
]

NotificationCallback = Callable[[str, SessionUpdate], Awaitable[None]]
PermissionCallback = Callable[[str, list[PermissionOption]], Awaitable[Optional[str]]]


class _BridgeClient(Client):
    """ACP Client that forwards agent events to bridge callbacks."""

    def __init__(
        self,
        notification_cb: NotificationCallback,
        permission_cb: PermissionCallback,
    ) -> None:
        self._notification_cb = notification_cb
        self._permission_cb = permission_cb

    async def session_update(self, session_id: str, update: SessionUpdate, **kwargs: Any) -> None:
        await self._notification_cb(session_id, update)

    async def request_permission(
        self,
        options: list[PermissionOption],
        session_id: str,
        tool_call: Any,
        **kwargs: Any,
    ) -> RequestPermissionResponse:
        option_id = await self._permission_cb(session_id, options)
        if option_id is None:
            return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
        return RequestPermissionResponse(outcome=AllowedOutcome(option_id=option_id, outcome="selected"))

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        logger.debug("Agent ext notification: %s", method)

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        logger.debug("Agent ext method: %s", method)
        return {}

    async def write_text_file(self, content: str, path: str, session_id: str, **kwargs: Any) -> None:
        return None

    async def read_text_file(
        self, path: str, session_id: str, limit: int | None = None, line: int | None = None, **kwargs: Any
    ) -> Any:
        raise NotImplementedError

    async def create_terminal(
        self,
        command: str,
        session_id: str,
        args: list[str] | None = None,
        cwd: str | None = None,
        env: Any = None,
        output_byte_limit: int | None = None,
        **kwargs: Any,
    ) -> Any:
        raise NotImplementedError

    async def terminal_output(self, session_id: str, terminal_id: str, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def release_terminal(self, session_id: str, terminal_id: str, **kwargs: Any) -> None:
        return None

    async def wait_for_terminal_exit(self, session_id: str, terminal_id: str, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def kill_terminal(self, session_id: str, terminal_id: str, **kwargs: Any) -> None:
        return None

    def on_connect(self, conn: Any) -> None:
        pass


class _AgentEntry:
    """Holds a live agent connection and its exit stack."""

    def __init__(
        self,
        conn: ClientSideConnection,
        stack: AsyncExitStack,
        process: asyncio.subprocess.Process,
    ) -> None:
        self.conn = conn
        self.stack = stack
        self.process = process

    async def close(self) -> None:
        try:
            await self.stack.aclose()
        except Exception:
            pass
        try:
            self.process.kill()
            await self.process.wait()
        except (ProcessLookupError, OSError):
            pass


class AgentManager:
    """Manages spawned agent processes and ACP communication."""

    def __init__(
        self,
        notification_cb: NotificationCallback,
        permission_cb: PermissionCallback,
    ) -> None:
        self._agents: dict[str, _AgentEntry] = {}
        self._agent_configs: dict[str, AgentConfig] = {}
        self._auto_approve: dict[str, bool] = {}
        self._notification_cb = notification_cb
        self._permission_cb = permission_cb

    def register_agents(self, configs: list[AgentConfig]) -> None:
        for config in configs:
            self._agent_configs[config.name] = config

    async def new_session(self, agent_name: str, workspace: str, auto_approve: bool) -> dict[str, Any]:
        config = self._agent_configs.get(agent_name)
        if config is None:
            raise ValueError(f"Agent config not found: {agent_name}")

        client = _BridgeClient(self._notification_cb, self._permission_cb)
        env = dict(**config.env) if config.env else None

        stack = AsyncExitStack()
        try:
            conn, process = await stack.enter_async_context(
                acp.spawn_agent_process(client, config.command, *config.args, env=env)
            )
            logger.debug("Agent process spawned (pid=%s)", process.pid)

            init_resp = await conn.initialize(
                protocol_version=acp.PROTOCOL_VERSION,
                client_info=acp.schema.Implementation(name="agent-bridge", version="0.1.0"),
            )
            logger.debug("Agent initialized: %s", init_resp.agent_info)

            session_resp = await conn.new_session(cwd=workspace)
            logger.debug("Session created: %s", session_resp.session_id)
        except BaseException:
            await stack.aclose()
            raise

        session_id = session_resp.session_id
        self._agents[session_id] = _AgentEntry(conn, stack, process)
        self._auto_approve[session_id] = auto_approve

        return session_resp.model_dump(mode="json", by_alias=True, exclude_none=True)

    def _get_entry(self, session_id: str) -> _AgentEntry:
        entry = self._agents.get(session_id)
        if entry is None:
            raise ValueError(f"Session not found: {session_id}")
        return entry

    async def prompt(self, session_id: str, content: list[dict[str, Any]]) -> dict[str, Any]:
        entry = self._get_entry(session_id)
        prompt_content: list[Any] = [TextContentBlock(**c) for c in content]
        try:
            resp = await entry.conn.prompt(prompt=prompt_content, session_id=session_id)
        except Exception:
            if entry.process.returncode is not None:
                logger.warning("Agent process died (pid=%s, rc=%s), cleaning up session %s",
                               entry.process.pid, entry.process.returncode, session_id)
                await self.end_session(session_id)
            raise
        return resp.model_dump(mode="json", by_alias=True, exclude_none=True)

    async def cancel(self, session_id: str) -> None:
        entry = self._get_entry(session_id)
        try:
            await entry.conn.cancel(session_id=session_id)
        except Exception:
            if entry.process.returncode is not None:
                logger.warning("Agent process died during cancel, cleaning up session %s", session_id)
                await self.end_session(session_id)
            raise

    async def set_config_option(self, session_id: str, option_id: str, value: str) -> dict[str, Any]:
        entry = self._get_entry(session_id)
        try:
            resp = await entry.conn.set_config_option(config_id=option_id, session_id=session_id, value=value)
        except Exception:
            if entry.process.returncode is not None:
                logger.warning("Agent process died during set_config_option, cleaning up session %s", session_id)
                await self.end_session(session_id)
            raise
        return resp.model_dump(mode="json", by_alias=True, exclude_none=True)

    async def set_mode(self, session_id: str, mode_id: str) -> dict[str, Any]:
        entry = self._get_entry(session_id)
        try:
            resp = await entry.conn.set_session_mode(mode_id=mode_id, session_id=session_id)
        except Exception:
            if entry.process.returncode is not None:
                logger.warning("Agent process died during set_mode, cleaning up session %s", session_id)
                await self.end_session(session_id)
            raise
        return resp.model_dump(mode="json", by_alias=True, exclude_none=True)

    async def set_model(self, session_id: str, model_id: str) -> dict[str, Any]:
        entry = self._get_entry(session_id)
        try:
            resp = await entry.conn.set_session_model(model_id=model_id, session_id=session_id)
        except Exception:
            if entry.process.returncode is not None:
                logger.warning("Agent process died during set_model, cleaning up session %s", session_id)
                await self.end_session(session_id)
            raise
        return resp.model_dump(mode="json", by_alias=True, exclude_none=True)

    async def end_session(self, session_id: str) -> None:
        entry = self._agents.pop(session_id, None)
        self._auto_approve.pop(session_id, None)
        if entry:
            await entry.close()

    def is_auto_approve(self, session_id: str) -> bool:
        return self._auto_approve.get(session_id, False)

    def has_session(self, session_id: str) -> bool:
        return session_id in self._agents

    def orphan_session_ids(self, known_session_ids: set[str]) -> list[str]:
        """Return agent session IDs that have no matching session in session_manager."""
        return [sid for sid in self._agents if sid not in known_session_ids]
