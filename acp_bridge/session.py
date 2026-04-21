"""Session manager for tracking chat thread to agent session mappings."""

import asyncio
import collections
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from acp_bridge.config import Config
from acp_bridge.feishu import FeishuEvent

logger = logging.getLogger(__name__)


@dataclass
class SessionState:
    session_id: str
    conversation_id: str
    busy: bool = False
    trigger_message_id: str = ""
    config_options: Optional[list] = None
    summary: str = ""
    last_active: float = 0.0
    last_bot_message_id: str = ""
    reply_to_message_id: str = ""  # message_id of the trigger msg for replies
    evaluator_session_ids: dict[str, str] = field(default_factory=dict)  # evaluator_name -> session_id
    eval_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # User -> Agent buffer: messages that arrive while agent is busy.
    message_buffer: list[FeishuEvent] = field(default_factory=list)


class SessionManager:
    """Manages active sessions. All methods are sync (no I/O)."""

    def __init__(self, config: Config):
        self._sessions: collections.OrderedDict[str, SessionState] = collections.OrderedDict()
        self._config = config

    def create_session(
        self,
        root_message_id: str,
        session_id: str,
        conversation_id: str,
        trigger_text: str,
        config_options: Optional[list] = None,
    ) -> tuple[SessionState, Optional[SessionState]]:
        """Create session, evict LRU if at capacity. Returns (new, evicted)."""
        evicted = None
        if len(self._sessions) >= self._config.bridge.max_sessions:
            evicted = self._evict_lru()
            if evicted is None:
                raise RuntimeError("All sessions are busy, cannot evict")
        session = SessionState(
            session_id=session_id,
            conversation_id=conversation_id,
            trigger_message_id=root_message_id,
            summary=trigger_text[:20],
            last_active=time.time(),
            config_options=config_options,
        )
        self._sessions[root_message_id] = session
        return session, evicted

    def touch(self, root_message_id: str):
        session = self._sessions.get(root_message_id)
        if session is None:
            return
        session.last_active = time.time()
        self._sessions.move_to_end(root_message_id)

    def _evict_lru(self) -> Optional[SessionState]:
        """Remove and return the least recently used non-busy session."""
        for key in list(self._sessions.keys()):
            s = self._sessions[key]
            if not s.busy:
                del self._sessions[key]
                return s
        return None

    def evict_ttl_expired(self) -> list[SessionState]:
        """Evict sessions that have been idle longer than TTL.

        Busy sessions get 2x TTL as a grace period — if they still exceed
        that, they are likely stuck and should be cleaned up.
        """
        ttl = self._config.bridge.session_ttl_minutes * 60
        now = time.time()
        expired: list[SessionState] = []
        for key in list(self._sessions.keys()):
            s = self._sessions[key]
            idle_time = now - s.last_active
            threshold = ttl * 2 if s.busy else ttl
            if idle_time > threshold:
                del self._sessions[key]
                expired.append(s)
        return expired

    def buffer_message(self, root_message_id: str, event: FeishuEvent) -> None:
        s = self._sessions.get(root_message_id)
        if s is None:
            raise ValueError(f"Session not found: {root_message_id}")
        s.message_buffer.append(event)

    def flush_buffer(self, root_message_id: str) -> list[FeishuEvent]:
        """Flush buffered events. Returns list of FeishuEvent or empty list."""
        s = self._sessions.get(root_message_id)
        if s is None:
            return []
        if not s.message_buffer:
            return []
        events = list(s.message_buffer)
        s.message_buffer = []
        return events

    def get_session_by_root(self, key: str) -> Optional[SessionState]:
        return self._sessions.get(key)

    def set_busy(self, key: str, busy: bool):
        s = self._sessions.get(key)
        if s is None:
            raise ValueError(f"Session not found: {key}")
        s.busy = busy

    def end_session(self, key: str):
        if key not in self._sessions:
            raise ValueError(f"Session not found: {key}")
        del self._sessions[key]

    def find_by_session_id(self, session_id: str) -> Optional[tuple[str, SessionState]]:
        for key, s in self._sessions.items():
            if s.session_id == session_id:
                return (key, s)
        return None

    def update_config_options(self, key: str, config_options: list):
        s = self._sessions.get(key)
        if s is None:
            raise ValueError(f"Session not found: {key}")
        s.config_options = config_options

    def session_count(self) -> int:
        return len(self._sessions)

    def list_sessions(self) -> list[SessionState]:
        """Return a snapshot list of all sessions."""
        return list(self._sessions.values())
