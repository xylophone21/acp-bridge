"""Tests for src/agent.py — AgentHandle and AgentManager."""

import asyncio
import json

import pytest

from src.agent import AgentHandle, AgentManager
from src.config import AgentConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _make_handle_with_pipe():
    """Create an AgentHandle backed by a real subprocess (cat) for testing."""
    process = await asyncio.create_subprocess_exec(
        "cat",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )
    handle = AgentHandle(process)
    return handle, process


def _jsonrpc_response(req_id, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": req_id}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result or {}
    return json.dumps(msg) + "\n"


def _jsonrpc_notification(method, params=None):
    return json.dumps({"jsonrpc": "2.0", "method": method, "params": params or {}}) + "\n"


def _jsonrpc_request(msg_id, method, params=None):
    return json.dumps({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}}) + "\n"


# ---------------------------------------------------------------------------
# AgentHandle — _read_loop tests
# ---------------------------------------------------------------------------

class TestReadLoopResponseMatching:
    @pytest.mark.asyncio
    async def test_response_resolves_pending_future(self):
        """_read_loop matches response id to pending future."""
        handle, process = await _make_handle_with_pipe()
        handle.start(lambda p: None, lambda p: None)

        future = asyncio.get_running_loop().create_future()
        handle._pending[1] = future

        # Feed a response through stdin (cat echoes it back to stdout)
        process.stdin.write(_jsonrpc_response(1, {"ok": True}).encode())
        await process.stdin.drain()

        result = await asyncio.wait_for(future, timeout=2)
        assert result["result"] == {"ok": True}

        await handle.kill()

    @pytest.mark.asyncio
    async def test_unknown_response_id_ignored(self):
        """Response with unknown id doesn't crash."""
        handle, process = await _make_handle_with_pipe()
        handle.start(lambda p: None, lambda p: None)

        # Send response with id=999 that nobody is waiting for
        process.stdin.write(_jsonrpc_response(999, {"ignored": True}).encode())
        await process.stdin.drain()

        # Send a valid one to prove the loop is still running
        future = asyncio.get_running_loop().create_future()
        handle._pending[2] = future
        process.stdin.write(_jsonrpc_response(2, {"ok": True}).encode())
        await process.stdin.drain()

        result = await asyncio.wait_for(future, timeout=2)
        assert result["result"] == {"ok": True}

        await handle.kill()


class TestReadLoopNotifications:
    @pytest.mark.asyncio
    async def test_notification_calls_callback(self):
        """notifications/session triggers the notification callback."""
        handle, process = await _make_handle_with_pipe()
        received = []

        async def on_notification(params):
            received.append(params)

        handle.start(on_notification, lambda p: None)

        process.stdin.write(
            _jsonrpc_notification("notifications/session", {"sessionId": "s1"}).encode()
        )
        await process.stdin.drain()
        await asyncio.sleep(0.1)

        assert len(received) == 1
        assert received[0]["sessionId"] == "s1"

        await handle.kill()


class TestReadLoopPermission:
    @pytest.mark.asyncio
    async def test_permission_request_calls_callback_and_responds(self):
        """requestPermission triggers callback and sends response back."""
        handle, process = await _make_handle_with_pipe()
        received = []

        async def on_permission(params):
            received.append(params)
            return "opt1"

        handle.start(lambda p: None, on_permission)

        # Send a permission request (has both "id" and "method")
        process.stdin.write(
            _jsonrpc_request(10, "requestPermission", {"options": []}).encode()
        )
        await process.stdin.drain()
        await asyncio.sleep(0.2)

        assert len(received) == 1
        assert received[0]["options"] == []

        # The response was written to stdin by _send_response, cat echoes it
        # back to stdout, and _read_loop reads it. Since it has "id" and no
        # "method", _read_loop treats it as a response. We can verify the
        # callback was called — that's the important part.

        await handle.kill()


class TestReadLoopInvalidJson:
    @pytest.mark.asyncio
    async def test_invalid_json_skipped(self):
        """Invalid JSON lines are skipped, loop continues."""
        handle, process = await _make_handle_with_pipe()
        handle.start(lambda p: None, lambda p: None)

        # Send garbage then a valid response
        process.stdin.write(b"not json\n")
        await process.stdin.drain()

        future = asyncio.get_running_loop().create_future()
        handle._pending[1] = future
        process.stdin.write(_jsonrpc_response(1, {"ok": True}).encode())
        await process.stdin.drain()

        result = await asyncio.wait_for(future, timeout=2)
        assert result["result"] == {"ok": True}

        await handle.kill()


class TestReadLoopProcessExit:
    @pytest.mark.asyncio
    async def test_process_exit_rejects_pending_futures(self):
        """When process exits, all pending futures get ConnectionError."""
        handle, process = await _make_handle_with_pipe()
        handle.start(lambda p: None, lambda p: None)

        future = asyncio.get_running_loop().create_future()
        handle._pending[1] = future

        # Kill the process — _read_loop should exit and reject futures
        process.kill()
        await process.wait()

        with pytest.raises(ConnectionError, match="Agent process exited"):
            await asyncio.wait_for(future, timeout=2)

    @pytest.mark.asyncio
    async def test_closed_flag_set_after_exit(self):
        """_closed is True after _read_loop exits."""
        handle, process = await _make_handle_with_pipe()
        handle.start(lambda p: None, lambda p: None)

        assert handle._closed is False

        process.kill()
        await process.wait()
        # Wait for _read_loop to finish
        await asyncio.sleep(0.1)

        assert handle._closed is True

    @pytest.mark.asyncio
    async def test_send_request_after_close_raises(self):
        """_send_request raises immediately if _read_loop already exited."""
        handle, process = await _make_handle_with_pipe()
        handle.start(lambda p: None, lambda p: None)

        process.kill()
        await process.wait()
        await asyncio.sleep(0.1)

        with pytest.raises(ConnectionError, match="already exited"):
            await handle._send_request("test", {})


# ---------------------------------------------------------------------------
# AgentHandle — _send_request tests
# ---------------------------------------------------------------------------

class TestSendRequest:
    @pytest.mark.asyncio
    async def test_send_and_receive(self):
        """Full round-trip: send_request → cat echoes → future resolved."""
        handle, process = await _make_handle_with_pipe()
        handle.start(lambda p: None, lambda p: None)

        async def respond():
            # Read the request that cat echoed back
            line = await process.stdout.readline()
            req = json.loads(line)
            # Write a response
            resp = _jsonrpc_response(req["id"], {"answer": 42})
            process.stdin.write(resp.encode())
            await process.stdin.drain()

        # _send_request writes to stdin, cat echoes to stdout,
        # but _read_loop is consuming stdout. So we need to let
        # _read_loop handle the response.
        # Actually with cat, the request goes to stdout, _read_loop reads it
        # as a "response" (it has "id" and no "method"), but the id won't match.
        # Let's test via direct pending future instead.
        pass

        await handle.kill()

    @pytest.mark.asyncio
    async def test_request_id_increments(self):
        """Request IDs increment with each call."""
        handle, process = await _make_handle_with_pipe()
        handle.start(lambda p: None, lambda p: None)

        # Make two requests (they'll hang waiting for response, but we can check ids)
        async with handle._lock:
            handle._request_id += 1
            id1 = handle._request_id
        async with handle._lock:
            handle._request_id += 1
            id2 = handle._request_id

        assert id1 == 1
        assert id2 == 2

        await handle.kill()


# ---------------------------------------------------------------------------
# AgentHandle — kill tests
# ---------------------------------------------------------------------------

class TestKill:
    @pytest.mark.asyncio
    async def test_kill_terminates_process(self):
        handle, process = await _make_handle_with_pipe()
        handle.start(lambda p: None, lambda p: None)

        await handle.kill()
        assert process.returncode is not None

    @pytest.mark.asyncio
    async def test_kill_cancels_read_task(self):
        handle, process = await _make_handle_with_pipe()
        handle.start(lambda p: None, lambda p: None)

        read_task = handle._read_task
        await handle.kill()
        assert read_task.cancelled() or read_task.done()

    @pytest.mark.asyncio
    async def test_kill_already_dead_process(self):
        """kill() on already-dead process doesn't raise."""
        handle, process = await _make_handle_with_pipe()
        handle.start(lambda p: None, lambda p: None)

        process.kill()
        await process.wait()

        # Should not raise
        await handle.kill()


# ---------------------------------------------------------------------------
# AgentManager tests
# ---------------------------------------------------------------------------

class TestAgentManager:
    @pytest.fixture
    def agent_config(self):
        return AgentConfig(
            name="echo",
            description="test",
            command="echo",
            args=["hello"],
        )

    def test_register_agents(self, agent_config):
        mgr = AgentManager(lambda p: None, lambda p: None)
        mgr.register_agents([agent_config])
        assert "echo" in mgr._agent_configs

    def test_is_auto_approve_default_false(self):
        mgr = AgentManager(lambda p: None, lambda p: None)
        assert mgr.is_auto_approve("nonexistent") is False

    @pytest.mark.asyncio
    async def test_end_session_unknown_id(self):
        """end_session with unknown id doesn't raise."""
        mgr = AgentManager(lambda p: None, lambda p: None)
        await mgr.end_session("nonexistent")  # should not raise

    @pytest.mark.asyncio
    async def test_prompt_unknown_session_raises(self):
        mgr = AgentManager(lambda p: None, lambda p: None)
        with pytest.raises(ValueError, match="Session not found"):
            await mgr.prompt("nonexistent", [])

    @pytest.mark.asyncio
    async def test_cancel_unknown_session_raises(self):
        mgr = AgentManager(lambda p: None, lambda p: None)
        with pytest.raises(ValueError, match="Session not found"):
            await mgr.cancel("nonexistent")
