"""Event router — dispatches incoming Feishu events to appropriate handlers."""

import logging
from typing import Optional

from src.feishu import FeishuConnection, FeishuEvent
from src.handler_command import handle_command
from src.handler_message import handle_message
from src.handler_permission import handle_permission_response
from src.handler_shell import handle_shell_command

logger = logging.getLogger(__name__)


async def handle_event(
    event: FeishuEvent,
    feishu: FeishuConnection,
    config,
    agent_manager,
    session_manager,
    pending_permissions: dict,
    notification_flush_callback,
):
    text = event.text
    channel = event.chat_id
    ts = event.message_id
    thread_ts = event.parent_id

    # Check user permission (Feishu events don't carry user ID in the same way,
    # but we keep the structure for future use)
    # allowed = config.bridge.allowed_users
    # if allowed and user not in allowed: ...

    thread_key = thread_ts or ts

    # Check pending permission requests first
    if thread_key in pending_permissions:
        perm = pending_permissions.pop(thread_key)
        option_id = await handle_permission_response(
            text, perm["options"], feishu, channel, thread_key
        )
        perm["future"].set_result(option_id)
        return

    # Shell commands (!)
    if text.strip().startswith("!"):
        await handle_shell_command(text, channel, thread_ts, feishu, config, session_manager)
        return

    # Bot commands (#)
    if text.strip().startswith("#"):
        await handle_command(
            text, channel, ts, thread_ts, feishu, config, agent_manager, session_manager
        )
        return

    # Regular messages — forward to agent
    await handle_message(
        text, channel, ts, thread_ts, feishu, agent_manager, session_manager,
        notification_flush_callback,
    )
