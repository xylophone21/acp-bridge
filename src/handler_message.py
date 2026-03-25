"""Message handler for forwarding regular messages to agents."""

import asyncio
import logging

logger = logging.getLogger(__name__)


async def handle_message(
    text: str,
    channel: str,
    ts: str,
    thread_ts: str | None,
    feishu,
    agent_manager,
    session_manager,
    notification_flush_callback,
):
    """Handle a regular message (not a command) by forwarding to the agent."""
    thread_key = thread_ts or ts

    session = await session_manager.get_session(thread_key)
    if session is None:
        feishu.send_message(channel, thread_key, "No active session. Use #help for help.")
        return

    if session.busy:
        feishu.send_message(
            channel, thread_key,
            "Session is busy processing a previous message. Please wait or use `#cancel` to cancel.",
        )
        return

    await session_manager.set_busy(thread_key, True)

    content = [{"type": "text", "text": text}]

    async def _do_prompt():
        try:
            result = await agent_manager.prompt(session.session_id, content)
            logger.info("Prompt completed: stop_reason=%s", result.get("stopReason"))
            if notification_flush_callback:
                await notification_flush_callback(session.session_id)
        except Exception as e:
            logger.error("Failed to send prompt: %s", e)
            feishu.send_message(channel, thread_key, f"Error: {e}")
        finally:
            try:
                await session_manager.set_busy(thread_key, False)
            except ValueError:
                pass

    asyncio.create_task(_do_prompt())
