"""Session manager for tracking chat thread to agent session mappings."""

import asyncio
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SessionState:
    session_id: str
    agent_name: str
    workspace: str
    auto_approve: bool
    channel: str  # chat_id
    busy: bool = False
    initial_ts: str = ""  # message_id of the #new message
    config_options: Optional[list] = None
    modes: Optional[dict] = None
    models: Optional[dict] = None


class SessionManager:
    """Manages active sessions between chat threads and agents."""

    def __init__(self, config):
        self._sessions: dict[str, SessionState] = {}
        self._config = config
        self._lock = asyncio.Lock()

    async def create_session(
        self,
        thread_key: str,
        agent_name: str,
        workspace: Optional[str],
        channel: str,
        session_id: str,
    ) -> SessionState:
        workspace = workspace or self._config.bridge.default_workspace

        agent_config = next(
            (a for a in self._config.agents if a.name == agent_name), None
        )
        if agent_config is None:
            raise ValueError(f"Agent not found: {agent_name}")

        session = SessionState(
            session_id=session_id,
            agent_name=agent_name,
            workspace=workspace,
            auto_approve=agent_config.auto_approve,
            channel=channel,
            initial_ts=thread_key,
        )

        async with self._lock:
            self._sessions[thread_key] = session
        return session

    async def get_session(self, thread_key: str) -> Optional[SessionState]:
        return self._sessions.get(thread_key)

    async def set_busy(self, thread_key: str, busy: bool):
        async with self._lock:
            session = self._sessions.get(thread_key)
            if session is None:
                raise ValueError(f"Session not found: {thread_key}")
            session.busy = busy

    async def end_session(self, thread_key: str):
        async with self._lock:
            if thread_key not in self._sessions:
                raise ValueError(f"Session not found: {thread_key}")
            del self._sessions[thread_key]

    async def find_by_session_id(
        self, session_id: str
    ) -> Optional[tuple[str, SessionState]]:
        for key, session in self._sessions.items():
            if session.session_id == session_id:
                return (key, session)
        return None

    async def update_config_options(self, thread_key: str, config_options: list):
        async with self._lock:
            session = self._sessions.get(thread_key)
            if session is None:
                raise ValueError(f"Session not found: {thread_key}")
            session.config_options = config_options

    async def update_modes(self, thread_key: str, modes: dict):
        async with self._lock:
            session = self._sessions.get(thread_key)
            if session is None:
                raise ValueError(f"Session not found: {thread_key}")
            session.modes = modes

    async def update_models(self, thread_key: str, models: dict):
        async with self._lock:
            session = self._sessions.get(thread_key)
            if session is None:
                raise ValueError(f"Session not found: {thread_key}")
            session.models = models

    @property
    def sessions(self) -> dict[str, SessionState]:
        return self._sessions
