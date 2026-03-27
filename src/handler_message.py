"""Message handler for forwarding regular messages to agents."""

import asyncio
import logging
from typing import Callable, Coroutine, Optional

from src.agent import AgentManager
from src.config import Config
from src.feishu import FeishuConnection, FeishuEvent
from src.session import SessionManager
from src.utils import expand_path

logger = logging.getLogger(__name__)

# Serialize session auto-creation per thread so follow-up messages see a
# consistent session state and naturally fall into the normal busy buffer path.
_init_locks: dict[str, asyncio.Lock] = {}


async def handle_message(
    event: FeishuEvent,
    feishu: FeishuConnection,
    config: Config,
    agent_manager: AgentManager,
    session_manager: SessionManager,
    notification_flush_callback: Optional[Callable[[str], Coroutine]],
):
    """Handle a regular message (not a command) by forwarding to the agent.

    Supports auto-creating sessions, silent buffering when busy,
    flushing buffered messages after prompt completion, and
    recording last_bot_message_id.
    """
    text = event.text
    conversation_id = event.conversation_id
    root_message_id = event.root_id
    reply_id = event.message_id
    sender_id = event.sender_id

    session = session_manager.get_session_by_root(root_message_id)
    created = False
    early_reaction_id = None

    if session is None:
        # Show typing indicator early, before session creation
        try:
            early_reaction_id = await feishu.add_reaction(reply_id, "Typing")
        except Exception:
            logger.debug("Failed to add early typing indicator")
            early_reaction_id = None

        session, created = await _ensure_session(
            root_message_id,
            text,
            conversation_id,
            reply_id,
            feishu,
            config,
            agent_manager,
            session_manager,
        )
        if session is None:
            if early_reaction_id:
                try:
                    await feishu.remove_reaction(reply_id, early_reaction_id)
                except Exception:
                    logger.debug("Failed to remove early typing indicator")
            return

    # --- Session exists and is busy: silent buffer ---
    if not created and session.busy:
        session_manager.buffer_message(root_message_id, sender_id, text)
        logger.debug("Buffered message for busy session %s", root_message_id)
        return

    # --- Session exists and not busy: touch + send prompt ---
    if not created:
        session_manager.touch(root_message_id)
        session_manager.set_busy(root_message_id, True)
        early_reaction_id = None

    _start_prompt(
        session,
        text,
        root_message_id,
        reply_id,
        conversation_id,
        event,
        feishu,
        config,
        agent_manager,
        session_manager,
        notification_flush_callback,
        typing_reaction_id=early_reaction_id,
    )


async def _ensure_session(
    root_message_id,
    text,
    conversation_id,
    reply_id,
    feishu,
    config,
    agent_manager,
    session_manager,
):
    """Create a session exactly once per thread and return (session, created)."""
    lock = _init_locks.get(root_message_id)
    if lock is None:
        lock = asyncio.Lock()
        _init_locks[root_message_id] = lock

    async with lock:
        session = session_manager.get_session_by_root(root_message_id)
        if session is not None:
            return session, False

        try:
            agent_cfg = config.agent
            workspace = expand_path(config.bridge.default_workspace)
            logger.debug("Starting new_session for %s", root_message_id)
            result = await asyncio.wait_for(
                agent_manager.new_session(agent_cfg.name, workspace, agent_cfg.auto_approve),
                timeout=30,
            )
            logger.debug("Session ready for %s (session_id=%s)", root_message_id, result.get("sessionId", ""))
        except asyncio.TimeoutError:
            logger.warning("new_session timed out for %s", root_message_id)
            await feishu.send_message(conversation_id, reply_id, "Error: agent session creation timed out")
            return None, False
        except Exception as e:
            logger.warning("Failed to start agent: %s", e)
            await feishu.send_message(conversation_id, reply_id, f"Error: {e}")
            return None, False

        try:
            session, evicted = session_manager.create_session(
                root_message_id,
                session_id=result.get("sessionId", ""),
                conversation_id=conversation_id,
                trigger_text=text,
                config_options=result.get("configOptions"),
            )
            session_manager.set_busy(root_message_id, True)
        except RuntimeError as e:
            logger.warning("Session creation failed for %s: %s", root_message_id, e)
            await agent_manager.end_session(result.get("sessionId", ""))
            await feishu.send_message(conversation_id, reply_id, str(e))
            return None, False

        if evicted:
            try:
                await agent_manager.end_session(evicted.session_id)
            except Exception as e:
                logger.warning("Failed to end evicted session %s: %s", evicted.session_id, e)
            try:
                await feishu.add_reaction(evicted.trigger_message_id, "DONE")
                if evicted.last_bot_message_id:
                    await feishu.add_reaction(evicted.last_bot_message_id, "DONE")
            except Exception as e:
                logger.warning("Failed to add reaction on evicted session: %s", e)

        await _apply_defaults(config.agent, session, agent_manager)
        return session, True

    # Clean up lock after release — only if no one else is waiting
    if not lock.locked():
        _init_locks.pop(root_message_id, None)


def _start_prompt(
    session,
    text,
    root_message_id,
    reply_id,
    conversation_id,
    event,
    feishu,
    config,
    agent_manager,
    session_manager,
    notification_flush_callback,
    typing_reaction_id=None,
):
    """Launch the prompt task. Session must already be marked busy."""

    async def _do_prompt():
        nonlocal typing_reaction_id
        if typing_reaction_id is None:
            try:
                typing_reaction_id = await feishu.add_reaction(reply_id, "Typing")
            except Exception:
                logger.debug("Failed to add typing indicator", exc_info=True)

        content = [{"type": "text", "text": text}]
        try:
            logger.debug("Sending prompt to agent: %.100s", text)
            result = await agent_manager.prompt(session.session_id, content)
            logger.debug("Prompt completed: stop_reason=%s", result.get("stopReason"))
            if notification_flush_callback:
                await notification_flush_callback(session.session_id)
        except Exception as e:
            logger.warning("Failed to send prompt: %s", e)
            if not agent_manager.has_session(session.session_id):
                try:
                    session_manager.end_session(root_message_id)
                    logger.warning("Removed dead session %s", root_message_id)
                except ValueError:
                    pass
                try:
                    await feishu.add_reaction(session.trigger_message_id, "DONE")
                except Exception:
                    pass
            await feishu.send_message(conversation_id, reply_id, f"Error: {e}")
        finally:
            # Remove typing indicator (best-effort)
            if typing_reaction_id:
                try:
                    await feishu.remove_reaction(reply_id, typing_reaction_id)
                except Exception:
                    logger.debug("Failed to remove typing indicator", exc_info=True)

            try:
                session_manager.set_busy(root_message_id, False)
            except ValueError:
                logger.debug("Session already removed when clearing busy flag")

            # Flush buffered messages and send as new prompt if any
            try:
                merged = session_manager.flush_buffer(root_message_id)
                if merged:
                    logger.debug("Buffer flushed, sending merged prompt: %.100s", merged)
                    from dataclasses import replace

                    merged_event = replace(event, text=merged)
                    asyncio.create_task(
                        handle_message(
                            merged_event,
                            feishu,
                            config,
                            agent_manager,
                            session_manager,
                            notification_flush_callback,
                        )
                    )
            except Exception as e:
                logger.warning("Failed to flush buffer: %s", e)

    asyncio.create_task(_do_prompt())


async def _apply_defaults(agent_cfg, session, agent_manager) -> None:
    """Apply default_mode and default_model to a newly created session."""
    for category, value in [
        ("mode", agent_cfg.default_mode),
        ("model", agent_cfg.default_model),
    ]:
        if not value:
            continue
        value = value.rstrip("!")
        try:
            if session.config_options:
                for opt in session.config_options:
                    if opt.get("category") == category:
                        await agent_manager.set_config_option(session.session_id, opt["id"], value)
                        logger.debug("Set default %s=%s via config_options", category, value)
                        break
                else:
                    if category == "mode":
                        await agent_manager.set_mode(session.session_id, value)
                    else:
                        await agent_manager.set_model(session.session_id, value)
                    logger.debug("Set default %s=%s via legacy API", category, value)
            else:
                if category == "mode":
                    await agent_manager.set_mode(session.session_id, value)
                else:
                    await agent_manager.set_model(session.session_id, value)
                logger.debug("Set default %s=%s via legacy API", category, value)
        except Exception as e:
            logger.warning("Failed to set default %s=%s: %s", category, value, e)
