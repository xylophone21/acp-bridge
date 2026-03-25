"""Bridge — main event loop connecting Feishu to ACP agents."""

import asyncio
import logging
from collections import defaultdict

from src.agent import AgentManager
from src.config import Config
from src.feishu import FeishuConnection, FeishuEvent
from src.handler import handle_event
from src.session import SessionManager

logger = logging.getLogger(__name__)


async def run_bridge(config: Config):
    logger.info("Default workspace: %s", config.bridge.default_workspace)
    logger.info("Auto-approve: %s", config.bridge.auto_approve)
    logger.info("Configured agents: %d", len(config.agents))
    for agent in config.agents:
        logger.info("  - %s (%s): %s", agent.name, agent.command, agent.description)

    # Shared state
    message_buffers: dict[str, str] = defaultdict(str)  # session_id -> buffered text
    thought_buffers: dict[str, str] = defaultdict(str)
    pending_permissions: dict[str, dict] = {}  # thread_key -> {options, future}

    event_queue: asyncio.Queue[FeishuEvent] = asyncio.Queue()

    feishu = FeishuConnection(config.feishu.app_id, config.feishu.app_secret)
    session_manager = SessionManager(config)

    def on_notification(params: dict):
        """Handle agent session notifications (message chunks, tool calls, etc.)."""
        session_id = params.get("sessionId", "")
        update = params.get("update", {})
        update_type = update.get("type", "")

        if update_type == "agentMessageChunk":
            content = update.get("content", {})
            if content.get("type") == "text":
                message_buffers[session_id] += content.get("text", "")
        elif update_type == "agentThoughtChunk":
            content = update.get("content", {})
            if content.get("type") == "text":
                thought_buffers[session_id] += content.get("text", "")
        elif update_type == "toolCall":
            title = update.get("title", "")
            _flush_buffers(session_id, feishu, session_manager, message_buffers, thought_buffers)
            # Send tool call notification
            asyncio.get_event_loop().call_soon_threadsafe(
                _send_tool_msg_sync, feishu, session_manager, session_id, f"🔧 Tool: {title}"
            )
        elif update_type == "toolCallUpdate":
            pass  # Tool call status updates — could enhance later
        elif update_type == "plan":
            entries = update.get("entries", [])
            if entries:
                _flush_buffers(session_id, feishu, session_manager, message_buffers, thought_buffers)
                plan_text = _format_plan(entries)
                asyncio.get_event_loop().call_soon_threadsafe(
                    _send_tool_msg_sync, feishu, session_manager, session_id, plan_text
                )
        else:
            # For non-chunk updates, flush buffers
            _flush_buffers(session_id, feishu, session_manager, message_buffers, thought_buffers)

    async def on_permission(params: dict) -> str | None:
        """Handle permission requests from agents."""
        session_id = params.get("sessionId", "")
        options = params.get("options", [])

        info = None
        for key, s in session_manager.sessions.items():
            if s.session_id == session_id:
                info = (key, s)
                break

        if info is None:
            logger.error("Session not found for permission: %s", session_id)
            return None

        thread_key, session = info

        if agent_manager.is_auto_approve(session_id):
            if options:
                return options[0].get("optionId")
            return None

        # Format and send permission request
        options_text = "\n".join(
            f"{i + 1}. {opt.get('name', '')}" for i, opt in enumerate(options)
        )
        msg = f"⚠️ Permission Required\n\n{options_text}\n\nReply with the number to approve, or 'deny' to reject."
        feishu.send_message(session.channel, thread_key, msg)

        future = asyncio.get_event_loop().create_future()
        pending_permissions[thread_key] = {"options": options, "future": future}
        return await future

    agent_manager = AgentManager(on_notification, on_permission)
    agent_manager.register_agents(config.agents)

    async def notification_flush_callback(session_id: str):
        _flush_buffers(session_id, feishu, session_manager, message_buffers, thought_buffers)

    # Feishu event callback — push to async queue
    def on_feishu_event(event: FeishuEvent):
        event_queue.put_nowait(event)

    # Start Feishu WebSocket in a background thread (it blocks)
    loop = asyncio.get_event_loop()
    feishu_task = loop.run_in_executor(None, feishu.connect, on_feishu_event)

    logger.info("Bridge started, waiting for events...")

    # Main event loop
    try:
        while True:
            event = await event_queue.get()
            logger.debug("Processing event: chat_id=%s, text=%s", event.chat_id, event.text[:50])
            asyncio.create_task(
                handle_event(
                    event, feishu, config, agent_manager, session_manager,
                    pending_permissions, notification_flush_callback,
                )
            )
    except asyncio.CancelledError:
        logger.info("Bridge shutting down...")


def _flush_buffers(session_id, feishu, session_manager, message_buffers, thought_buffers):
    """Flush accumulated message and thought buffers to Feishu."""
    info = None
    for key, s in session_manager.sessions.items():
        if s.session_id == session_id:
            info = (key, s)
            break

    if info is None:
        return

    thread_key, session = info

    if session_id in message_buffers and message_buffers[session_id]:
        text = message_buffers.pop(session_id)
        feishu.send_message(session.channel, thread_key, text)

    if session_id in thought_buffers and thought_buffers[session_id]:
        text = thought_buffers.pop(session_id)
        # Format thoughts as blockquote
        thought_text = "\n".join(f"> {line}" for line in text.splitlines())
        feishu.send_message(session.channel, thread_key, thought_text)


def _send_tool_msg_sync(feishu, session_manager, session_id, msg):
    for key, s in session_manager.sessions.items():
        if s.session_id == session_id:
            feishu.send_message(s.channel, key, msg)
            return


def _format_plan(entries: list[dict]) -> str:
    lines = ["*Plan*"]
    for entry in entries:
        status = entry.get("status", "pending")
        marker = {"completed": "[x]", "in_progress": "[>]", "pending": "[ ]"}.get(status, "[?]")
        lines.append(f"{marker} {entry.get('content', '')}")
    return "\n".join(lines)
