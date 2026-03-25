"""Tests for src/session.py"""

import asyncio

import pytest
import pytest_asyncio

from src.config import Config, FeishuConfig, BridgeConfig, AgentConfig
from src.session import SessionManager, SessionState


@pytest.fixture
def config():
    return Config(
        feishu=FeishuConfig(app_id="id", app_secret="secret"),
        bridge=BridgeConfig(default_workspace="/tmp"),
        agents=[
            AgentConfig(name="test", description="test agent", command="echo", auto_approve=True),
            AgentConfig(name="other", description="other agent", command="cat"),
        ],
    )


@pytest.fixture
def manager(config):
    return SessionManager(config)


class TestSessionManager:
    @pytest.mark.asyncio
    async def test_create_and_get_session(self, manager):
        session = await manager.create_session("t1", "test", None, "ch1", "sid1")
        assert session.session_id == "sid1"
        assert session.agent_name == "test"
        assert session.workspace == "/tmp"  # default
        assert session.auto_approve is True
        assert session.channel == "ch1"
        assert session.busy is False

        got = await manager.get_session("t1")
        assert got is not None
        assert got.session_id == "sid1"

    @pytest.mark.asyncio
    async def test_create_with_custom_workspace(self, manager):
        session = await manager.create_session("t2", "test", "/home", "ch1", "sid2")
        assert session.workspace == "/home"

    @pytest.mark.asyncio
    async def test_create_unknown_agent(self, manager):
        with pytest.raises(ValueError, match="Agent not found"):
            await manager.create_session("t3", "nonexistent", None, "ch1", "sid3")

    @pytest.mark.asyncio
    async def test_get_nonexistent_session(self, manager):
        assert await manager.get_session("nope") is None

    @pytest.mark.asyncio
    async def test_set_busy(self, manager):
        await manager.create_session("t1", "test", None, "ch1", "sid1")
        await manager.set_busy("t1", True)
        s = await manager.get_session("t1")
        assert s.busy is True

        await manager.set_busy("t1", False)
        s = await manager.get_session("t1")
        assert s.busy is False

    @pytest.mark.asyncio
    async def test_set_busy_nonexistent(self, manager):
        with pytest.raises(ValueError, match="Session not found"):
            await manager.set_busy("nope", True)

    @pytest.mark.asyncio
    async def test_end_session(self, manager):
        await manager.create_session("t1", "test", None, "ch1", "sid1")
        await manager.end_session("t1")
        assert await manager.get_session("t1") is None

    @pytest.mark.asyncio
    async def test_end_nonexistent_session(self, manager):
        with pytest.raises(ValueError, match="Session not found"):
            await manager.end_session("nope")

    @pytest.mark.asyncio
    async def test_find_by_session_id(self, manager):
        await manager.create_session("t1", "test", None, "ch1", "sid1")
        await manager.create_session("t2", "other", None, "ch2", "sid2")

        result = await manager.find_by_session_id("sid2")
        assert result is not None
        key, session = result
        assert key == "t2"
        assert session.agent_name == "other"

    @pytest.mark.asyncio
    async def test_find_by_session_id_not_found(self, manager):
        assert await manager.find_by_session_id("nope") is None

    @pytest.mark.asyncio
    async def test_update_config_options(self, manager):
        await manager.create_session("t1", "test", None, "ch1", "sid1")
        opts = [{"id": "opt1", "category": "mode"}]
        await manager.update_config_options("t1", opts)
        s = await manager.get_session("t1")
        assert s.config_options == opts

    @pytest.mark.asyncio
    async def test_update_modes(self, manager):
        await manager.create_session("t1", "test", None, "ch1", "sid1")
        modes = {"currentModeId": "fast", "availableModes": []}
        await manager.update_modes("t1", modes)
        s = await manager.get_session("t1")
        assert s.modes == modes

    @pytest.mark.asyncio
    async def test_update_models(self, manager):
        await manager.create_session("t1", "test", None, "ch1", "sid1")
        models = {"currentModelId": "gpt-4", "availableModels": []}
        await manager.update_models("t1", models)
        s = await manager.get_session("t1")
        assert s.models == models

    @pytest.mark.asyncio
    async def test_sessions_property(self, manager):
        assert manager.sessions == {}
        await manager.create_session("t1", "test", None, "ch1", "sid1")
        assert len(manager.sessions) == 1
        assert "t1" in manager.sessions
