"""Message handler for forwarding regular messages to agents."""

import asyncio
import logging
from typing import Callable, Coroutine, Optional

from acp_bridge.agent import AgentManager
from acp_bridge.config import Config
from acp_bridge.feishu import FeishuConnection, FeishuEvent
from acp_bridge.session import SessionManager
from acp_bridge.utils import expand_path

logger = logging.getLogger(__name__)

# Serialize session auto-creation per thread so follow-up messages see a
# consistent session state and naturally fall into the normal busy buffer path.
_init_locks: dict[str, asyncio.Lock] = {}


def is_session_creating(root_message_id: str) -> bool:
    """Check if a session is currently being created for the given root."""
    return root_message_id in _init_locks


async def handle_message(
    events: list[FeishuEvent],
    feishu: FeishuConnection,
    config: Config,
    agent_manager: AgentManager,
    session_manager: SessionManager,
    notification_flush_callback: Optional[Callable[[str], Coroutine]],
):
    """Handle one or more messages by forwarding to the agent.

    Accepts a list of FeishuEvent (single message or merged from buffer flush).
    """
    event = events[0]
    text = event.text
    conversation_id = event.conversation_id
    root_message_id = event.root_id
    reply_id = events[-1].message_id

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
        for e in events:
            session_manager.buffer_message(root_message_id, e)
        logger.debug("Buffered %d message(s) for busy session %s", len(events), root_message_id)
        return

    # --- Session exists and not busy: touch + send prompt ---
    if not created:
        session_manager.touch(root_message_id)
        session_manager.set_busy(root_message_id, True)
        early_reaction_id = None

    _start_prompt(
        session,
        events,
        root_message_id,
        reply_id,
        conversation_id,
        feishu,
        config,
        agent_manager,
        session_manager,
        notification_flush_callback,
        typing_reaction_id=early_reaction_id,
        new_session=created,
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
    events: list[FeishuEvent],
    root_message_id,
    reply_id,
    conversation_id,
    feishu,
    config,
    agent_manager,
    session_manager,
    notification_flush_callback,
    typing_reaction_id=None,
    new_session=False,
):
    """Launch the prompt task. Session must already be marked busy."""

    async def _do_prompt():
        nonlocal typing_reaction_id
        if typing_reaction_id is None:
            try:
                typing_reaction_id = await feishu.add_reaction(reply_id, "Typing")
            except Exception:
                logger.debug("Failed to add typing indicator", exc_info=True)

        # Build prompt from all events
        content = []
        try:
            for e in events:
                name, email = await feishu.get_user_info(e.sender_id)
                if name and email:
                    identity = f"{name}, {email}"
                elif name:
                    identity = name
                elif email:
                    identity = email
                else:
                    identity = "unknown user"
                    logger.debug("Could not resolve user info for sender %s", e.sender_id)
                text = e.text
                if e.files or e.parent_id:
                    workspace = expand_path(config.bridge.default_workspace)
                    text = await feishu.resolve_attachments(
                        e, workspace, config.bridge.attachment_dir,
                        resolve_parent=new_session,
                    )
                content.append({"type": "text", "text": f"[Current user: {identity}]\n{text}"})
            logger.debug("[%s] Sending prompt to agent: %.500s", reply_id, content)
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
                buffered_events = session_manager.flush_buffer(root_message_id)
                if buffered_events:
                    logger.debug("[%s] Buffer flushed, %d message(s)", reply_id, len(buffered_events))
                    asyncio.create_task(
                        handle_message(
                            buffered_events,
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
