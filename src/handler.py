"""Event router — dispatches incoming Feishu events to appropriate handlers."""

import logging
from typing import Callable, Coroutine

from src.agent import AgentManager
from src.config import Config
from src.feishu import FeishuConnection, FeishuEvent
from src.handler_command import handle_command
from src.handler_message import handle_message
from src.handler_permission import handle_permission_response
from src.session import SessionManager

logger = logging.getLogger(__name__)


async def handle_event(
    event: FeishuEvent,
    feishu: FeishuConnection,
    config: Config,
    agent_manager: AgentManager,
    session_manager: SessionManager,
    pending_permissions: dict,
    notification_flush_callback: Callable[[str], Coroutine],
):
    text = event.text
    root_message_id = event.root_id

    # Check pending permission requests first (keyed by root_message_id)
    if root_message_id in pending_permissions:
        perm = pending_permissions.pop(root_message_id)
        option_id = await handle_permission_response(
            text, perm["options"], feishu, event.conversation_id, event.message_id
        )
        perm["future"].set_result(option_id)
        return

    # Look up existing session by root_message_id
    session = session_manager.get_session_by_root(root_message_id)
    is_command = event.clean_text.startswith("#")

    if session is not None:
        # --- Session exists ---
        if is_command:
            await handle_command(event, feishu, config, agent_manager, session_manager)
        else:
            await handle_message(
                event,
                feishu,
                config,
                agent_manager,
                session_manager,
                notification_flush_callback,
            )
    else:
        # --- No session ---
        if not event.is_mention_bot:
            # In DMs, treat all messages as if bot was mentioned
            if event.chat_type != "p2p":
                # Not mentioned in group → ignore silently
                return

        if is_command:
            await handle_command(event, feishu, config, agent_manager, session_manager)
        else:
            await handle_message(
                event,
                feishu,
                config,
                agent_manager,
                session_manager,
                notification_flush_callback,
            )
