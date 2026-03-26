"""Agent manager for spawning and communicating with ACP agents via stdio."""

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Callable, Optional

from src.config import AgentConfig

logger = logging.getLogger(__name__)


@dataclass
class PermissionRequest:
    session_id: str
    options: list[dict]
    future: asyncio.Future


class AgentHandle:
    """Handle for communicating with a spawned agent process via JSON-RPC over stdio."""

    def __init__(self, process: asyncio.subprocess.Process):
        self.process = process
        self._request_id = 0                                    # Auto-incrementing JSON-RPC request ID
        self._pending: dict[int, asyncio.Future] = {}           # request_id → Future, resolved by _read_loop
        self._notification_callback: Optional[Callable] = None  # Called on agent notifications (e.g. streaming chunks)
        self._permission_callback: Optional[Callable] = None    # Called on agent permission requests
        self._read_task: Optional[asyncio.Task] = None          # Background task running _read_loop
        self._lock = asyncio.Lock()                             # Guards _request_id increment
        self._closed = False                                    # Set when _read_loop exits

    def start(self, notification_callback, permission_callback):
        self._notification_callback = notification_callback
        self._permission_callback = permission_callback
        self._read_task = asyncio.create_task(self._read_loop())

    async def _read_loop(self):
        """Read JSON-RPC messages from agent stdout."""
        try:
            while True:
                line = await self.process.stdout.readline()  # type: ignore[union-attr]
                if not line:
                    break
                line = line.decode().strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("Invalid JSON from agent: %s", line[:200])
                    continue

                if "id" in msg and "method" not in msg:
                    # Response to a request
                    req_id = msg["id"]
                    if req_id in self._pending:
                        self._pending.pop(req_id).set_result(msg)
                elif "method" in msg:
                    await self._handle_server_message(msg)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Agent read loop error: %s", e)
        finally:
            self._reject_all_pending()

    def _reject_all_pending(self):
        """Reject all pending futures with ConnectionError."""
        self._closed = True
        for future in self._pending.values():
            if not future.done():
                future.set_exception(ConnectionError("Agent process exited"))
        self._pending.clear()

    async def _handle_server_message(self, msg: dict):
        method = msg.get("method", "")
        params = msg.get("params", {})
        msg_id = msg.get("id")

        if method == "notifications/session":
            if self._notification_callback:
                await self._notification_callback(params)
        elif method == "requestPermission":
            if self._permission_callback and msg_id is not None:
                result = await self._permission_callback(params)
                await self._send_response(msg_id, result)
        else:
            logger.debug("Unhandled agent method: %s", method)

    async def _send_response(self, msg_id, result):
        response = {"jsonrpc": "2.0", "id": msg_id, "result": result}
        await self._write(response)

    async def _send_request(self, method: str, params: dict) -> dict:
        if self._closed:
            raise ConnectionError("Agent process already exited")

        async with self._lock:
            self._request_id += 1
            req_id = self._request_id

        future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = future

        msg = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        await self._write(msg)

        return await future

    async def _write(self, msg: dict):
        data = json.dumps(msg) + "\n"
        self.process.stdin.write(data.encode())  # type: ignore[union-attr]
        await self.process.stdin.drain()  # type: ignore[union-attr]

    async def initialize(self) -> dict:
        return await self._send_request("initialize", {
            "protocolVersion": "2025-03-26",
            "clientInfo": {"name": "agent-bridge", "version": "0.1.0"},
        })

    async def new_session(self, workspace: str) -> dict:
        return await self._send_request("sessions/new", {
            "workspace": workspace,
        })

    async def prompt(self, session_id: str, content: list[dict]) -> dict:
        return await self._send_request("sessions/prompt", {
            "sessionId": session_id,
            "content": content,
        })

    async def cancel(self, session_id: str):
        msg = {
            "jsonrpc": "2.0",
            "method": "notifications/cancel",
            "params": {"sessionId": session_id},
        }
        await self._write(msg)

    async def set_config_option(self, session_id: str, option_id: str, value: str) -> dict:
        return await self._send_request("sessions/setConfigOption", {
            "sessionId": session_id,
            "optionId": option_id,
            "value": value,
        })

    async def set_mode(self, session_id: str, mode_id: str) -> dict:
        return await self._send_request("sessions/setMode", {
            "sessionId": session_id,
            "modeId": mode_id,
        })

    async def set_model(self, session_id: str, model_id: str) -> dict:
        return await self._send_request("sessions/setModel", {
            "sessionId": session_id,
            "modelId": model_id,
        })

    async def kill(self):
        if self._read_task:
            self._read_task.cancel()
        try:
            self.process.kill()
            await self.process.wait()
        except ProcessLookupError:
            pass


class AgentManager:
    """Manages spawned agent processes and ACP communication."""

    def __init__(self, notification_callback, permission_callback):
        self._agents: dict[str, AgentHandle] = {}
        self._agent_configs: dict[str, AgentConfig] = {}
        self._auto_approve: dict[str, bool] = {}
        self._notification_callback = notification_callback
        self._permission_callback = permission_callback

    def register_agents(self, configs: list):
        for config in configs:
            self._agent_configs[config.name] = config

    async def new_session(
        self, agent_name: str, workspace: str, auto_approve: bool
    ) -> dict:
        config = self._agent_configs.get(agent_name)
        if config is None:
            raise ValueError(f"Agent config not found: {agent_name}")

        cmd = [config.command] + config.args
        env = dict(**config.env) if config.env else None

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=None,  # inherit
            env=env,
        )

        handle = AgentHandle(process)
        handle.start(self._notification_callback, self._permission_callback)

        # Initialize
        init_resp = await handle.initialize()
        if "error" in init_resp:
            await handle.kill()
            raise RuntimeError(f"Agent init failed: {init_resp['error']}")

        # Create session
        session_resp = await handle.new_session(workspace)
        if "error" in session_resp:
            await handle.kill()
            raise RuntimeError(f"Session creation failed: {session_resp['error']}")

        result = session_resp.get("result", {})
        session_id = result.get("sessionId", "")

        self._agents[session_id] = handle
        self._auto_approve[session_id] = auto_approve

        return result

    async def prompt(self, session_id: str, content: list[dict]) -> dict:
        handle = self._agents.get(session_id)
        if handle is None:
            raise ValueError(f"Session not found: {session_id}")
        resp = await handle.prompt(session_id, content)
        return resp.get("result", {})

    async def cancel(self, session_id: str):
        handle = self._agents.get(session_id)
        if handle is None:
            raise ValueError(f"Session not found: {session_id}")
        await handle.cancel(session_id)

    async def set_config_option(self, session_id: str, option_id: str, value: str) -> dict:
        handle = self._agents.get(session_id)
        if handle is None:
            raise ValueError(f"Session not found: {session_id}")
        resp = await handle.set_config_option(session_id, option_id, value)
        return resp.get("result", {})

    async def set_mode(self, session_id: str, mode_id: str) -> dict:
        handle = self._agents.get(session_id)
        if handle is None:
            raise ValueError(f"Session not found: {session_id}")
        resp = await handle.set_mode(session_id, mode_id)
        return resp.get("result", {})

    async def set_model(self, session_id: str, model_id: str) -> dict:
        handle = self._agents.get(session_id)
        if handle is None:
            raise ValueError(f"Session not found: {session_id}")
        resp = await handle.set_model(session_id, model_id)
        return resp.get("result", {})

    async def end_session(self, session_id: str):
        handle = self._agents.pop(session_id, None)
        self._auto_approve.pop(session_id, None)
        if handle:
            await handle.kill()

    def is_auto_approve(self, session_id: str) -> bool:
        return self._auto_approve.get(session_id, False)
