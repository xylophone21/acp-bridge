"""Event router — dispatches incoming Feishu events to appropriate handlers."""

import logging
from typing import Callable, Coroutine

from acp_bridge.agent import AgentManager
from acp_bridge.config import Config
from acp_bridge.feishu import FeishuConnection, FeishuEvent
from acp_bridge.handler_command import handle_command
from acp_bridge.handler_message import handle_message
from acp_bridge.handler_permission import handle_permission_response
from acp_bridge.session import SessionManager

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
    msg_id = event.message_id
    root_message_id = event.root_id

    # Check pending permission requests first (keyed by root_message_id)
    if root_message_id in pending_permissions:
        perm = pending_permissions.pop(root_message_id)
        option_id = await handle_permission_response(
            text, perm["options"], feishu, event.conversation_id, msg_id
        )
        perm["future"].set_result(option_id)
        logger.info("[%s] Permission: %s", msg_id, "approved" if option_id else "denied")
        return

    # Look up existing session by root_message_id
    session = session_manager.get_session_by_root(root_message_id)
    is_command = event.clean_text.startswith("#")

    if session is not None:
        # In reply chain: ignore if @others but not @bot (talking to someone else)
        if not event.is_mention_bot and event.has_other_mentions and event.chat_type != "p2p":
            logger.info("[%s] Ignored: mentioning others in reply chain", msg_id)
            return

        if is_command:
            logger.info("[%s] Command: %s", msg_id, event.clean_text[:30])
            await handle_command(event, feishu, config, agent_manager, session_manager)
        elif session.busy:
            logger.info("[%s] Buffered (busy)", msg_id)
            await handle_message(
                event,
                feishu,
                config,
                agent_manager,
                session_manager,
                notification_flush_callback,
            )
        else:
            logger.info("[%s] Prompt", msg_id)
            await handle_message(
                event,
                feishu,
                config,
                agent_manager,
                session_manager,
                notification_flush_callback,
            )
    else:
        if not event.is_mention_bot:
            if event.chat_type != "p2p":
                logger.info("[%s] Ignored: not mentioned in group", msg_id)
                return

        if is_command:
            logger.info("[%s] Command (new): %s", msg_id, event.clean_text[:30])
            await handle_command(event, feishu, config, agent_manager, session_manager)
        else:
            logger.info("[%s] New conversation", msg_id)
            await handle_message(
                event,
                feishu,
                config,
                agent_manager,
                session_manager,
                notification_flush_callback,
            )
