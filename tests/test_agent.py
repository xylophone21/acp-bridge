"""Tests for src/agent.py — AgentManager."""

import pytest

from agent_bridge.agent import AgentManager
from agent_bridge.config import AgentConfig


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
