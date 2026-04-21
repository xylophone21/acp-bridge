"""Bridge — main event loop connecting Feishu to ACP agents."""

import asyncio
import json
import logging
import os
import signal
from collections import defaultdict
from typing import Any, Optional

from acp.schema import (
    AgentMessageChunk,
    AgentPlanUpdate,
    AgentThoughtChunk,
    ContentToolCallContent,
    FileEditToolCallContent,
    PermissionOption,
    TerminalToolCallContent,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
)

from acp_bridge.agent import AgentManager
from acp_bridge.config import Config
from acp_bridge.evaluator import (
    find_matching_evaluator,
    on_eval_notification,
    run_evaluation,
)
from acp_bridge.feishu import FeishuConnection, FeishuEvent
from acp_bridge.handler import handle_event
from acp_bridge.session import SessionManager
from acp_bridge.utils import pretty_raw, safe_backticks, unescape_json_strings

logger = logging.getLogger(__name__)


async def _handle_evicted_sessions(
    expired: list,
    agent_manager: AgentManager,
    feishu: FeishuConnection,
) -> None:
    """Process evicted sessions: terminate agent processes and add DONE reactions."""
    for session in expired:
        # Clean up evaluator sessions bound to this session
        for ev_sid in session.evaluator_session_ids.values():
            try:
                await agent_manager.end_session(ev_sid)
            except Exception as e:
                logger.warning("Failed to end evaluator session %s: %s", ev_sid, e)

        try:
            await agent_manager.end_session(session.session_id)
        except Exception as e:
            logger.warning("Failed to end session %s: %s", session.session_id, e)

        try:
            await feishu.add_reaction(session.trigger_message_id, "DONE")
        except Exception as e:
            logger.warning(
                "Failed to add reaction on trigger message %s: %s",
                session.trigger_message_id,
                e,
            )

        if session.last_bot_message_id:
            try:
                await feishu.add_reaction(session.last_bot_message_id, "DONE")
            except Exception as e:
                logger.warning(
                    "Failed to add reaction on bot message %s: %s",
                    session.last_bot_message_id,
                    e,
                )

        logger.debug("TTL evicted session %s (summary: %s)", session.session_id, session.summary)


async def _ttl_eviction_loop(
    session_manager: SessionManager,
    agent_manager: AgentManager,
    feishu: FeishuConnection,
) -> None:
    """Background task: every 60 seconds, evict TTL-expired sessions."""
    while True:
        await asyncio.sleep(60)
        try:
            expired = session_manager.evict_ttl_expired()
            await _handle_evicted_sessions(expired, agent_manager, feishu)
        except Exception as e:
            logger.warning("TTL eviction loop error: %s", e)


async def run_bridge(config: Config):
    logger.info("Default workspace: %s", config.bridge.default_workspace)
    logger.info("Auto-approve: %s", config.bridge.auto_approve)
    logger.info(
        "Agent: %s (%s): %s",
        config.agent.name,
        config.agent.command,
        config.agent.description,
    )

    # Agent → Feishu buffers: accumulate streaming chunks from ACP agent,
    # flushed to Feishu when a tool call or other event interrupts the stream.
    # Keyed by ACP session_id.
    agent_text_chunks: dict[str, str] = defaultdict(str)
    agent_thought_chunks: dict[str, str] = defaultdict(str)

    # Sessions with pending text clear — set when a tool call interrupts
    # the text stream. Cleared (and text discarded) when the next chunk
    # arrives. If no new chunk arrives, the text survives to final flush.
    pending_text_clear: set[str] = set()

    # Pending ToolCallStart per session — buffered until a different event arrives
    # or the tool completes, so multiple ToolCallStart updates for the same tool
    # are collapsed into a single message.
    pending_tool_start: dict[str, ToolCallStart] = {}

    # Permission requests awaiting user response, keyed by root_message_id.
    pending_permissions: dict[str, dict] = {}

    event_queue: asyncio.Queue[FeishuEvent] = asyncio.Queue()

    feishu = FeishuConnection(config.feishu.app_id, config.feishu.app_secret)
    session_manager = SessionManager(config)

    async def _flush_pending_tool_start(session_id: str) -> None:
        """Send the buffered ToolCallStart message for a session, if any."""
        start = pending_tool_start.pop(session_id, None)
        if start is None:
            return
        title = start.title or ""
        msg = f"🔧 Tool: {title}"
        if start.raw_input:
            msg += "\n" + _format_raw_data("Input", start.raw_input)
        await _send_tool_msg(feishu, session_manager, session_id, msg)

    def _log_accumulated_chunks(session_id: str, text_chunks: dict, thought_chunks: dict,
                                *, final: bool = False) -> None:
        """Log accumulated text/thought chunks when switching to a different notification type."""
        tag = "FINAL" if final else "FLUSH"
        text = text_chunks.get(session_id, "")
        if text:
            logger.info("[%s] [%s] Accumulated text (%d chars):\n%s",
                        tag, session_id[:8], len(text), text[:2000])
        thought = thought_chunks.get(session_id, "")
        if thought:
            logger.info("[%s] [%s] Accumulated thought (%d chars):\n%s",
                        tag, session_id[:8], len(thought), thought[:2000])

    async def on_notification(session_id: str, update):
        """Handle agent session notifications (message chunks, tool calls, etc.)."""
        if isinstance(update, AgentMessageChunk):
            await _flush_pending_tool_start(session_id)
            if isinstance(update.content, TextContentBlock):
                # Lazy clear: discard old text on first new chunk after a tool call.
                if session_id in pending_text_clear:
                    agent_text_chunks.pop(session_id, None)
                    pending_text_clear.discard(session_id)
                if session_id not in agent_text_chunks or not agent_text_chunks[session_id]:
                    logger.info("[RESP] [%s] Agent responding (first chunk)...", session_id[:8])
                agent_text_chunks[session_id] += update.content.text or ""
        elif isinstance(update, AgentThoughtChunk):
            if config.bridge.show_thinking:
                if isinstance(update.content, TextContentBlock):
                    if session_id not in agent_thought_chunks or not agent_thought_chunks[session_id]:
                        logger.info("[THINK] [%s] Agent thinking (first chunk)...", session_id[:8])
                    agent_thought_chunks[session_id] += update.content.text or ""
        elif isinstance(update, ToolCallStart):
            if session_id not in pending_text_clear:
                _log_accumulated_chunks(session_id, agent_text_chunks, agent_thought_chunks)
            title = update.title or ""
            if update.raw_input:
                raw = pretty_raw(update.raw_input)
                logger.info("[TOOL] [%s] %s\n  input: %s", session_id[:8], title[:80], raw[:2000])
            elif title:
                logger.info("[TOOL] [%s] %s", session_id[:8], title[:80])
            if config.bridge.show_intermediate:
                prev = pending_tool_start.get(session_id)
                if prev is not None and prev.tool_call_id != update.tool_call_id:
                    await _flush_pending_tool_start(session_id)
                await _flush_agent_chunks(
                                    session_id,
                                    feishu,
                                    session_manager,
                                    agent_text_chunks,
                                    agent_thought_chunks,
                                    config,
                                )
                if update.raw_input:
                    # Has parameters — send immediately (discard any buffered start).
                    pending_tool_start.pop(session_id, None)
                    title = update.title or ""
                    msg = f"🔧 Tool: {title}\n" + _format_raw_data("Input", update.raw_input)
                    await _send_tool_msg(feishu, session_manager, session_id, msg)
                else:
                    # No parameters yet — buffer and wait for a richer update.
                    pending_tool_start[session_id] = update
            else:
                # Don't discard text immediately — mark for lazy clear.
                # If the agent produces new text after this tool call,
                # the old text is cleared on the first new chunk.
                # If not (e.g. agent ends with a plan-complete tool),
                # the text survives to final flush.
                pending_text_clear.add(session_id)
                agent_thought_chunks.pop(session_id, None)
        elif isinstance(update, ToolCallProgress):
            if update.status in ("completed", "failed"):
                icon = "[DONE]" if update.status == "completed" else "[FAIL]"
                if update.raw_output:
                    out = pretty_raw(update.raw_output)
                    logger.info(
                        "%s [%s] %s\n  output (%d chars): %s",
                        icon, session_id[:8], (update.title or "")[:80], len(out), out[:2000],
                    )
                else:
                    logger.info("%s [%s] %s", icon, session_id[:8], (update.title or "")[:80])
            if config.bridge.show_intermediate:
                await _flush_pending_tool_start(session_id)
                parts: list[str] = []
                if update.status in ("completed", "failed"):
                    label = "✅ Done" if update.status == "completed" else "❌ Failed"
                    title = update.title or ""
                    parts.append(f"{label}: {title}" if title else label)
                if update.raw_output:
                    parts.append(_format_raw_data("Output", update.raw_output))
                if update.content:
                    formatted = _format_tool_content(update.content)
                    if formatted:
                        parts.append(formatted)
                if parts:
                    await _send_tool_msg(feishu, session_manager, session_id, "\n".join(parts))
        elif isinstance(update, AgentPlanUpdate):
            if config.bridge.show_intermediate:
                await _flush_pending_tool_start(session_id)
                entries = update.entries or []
                if entries:
                    await _flush_agent_chunks(
                                        session_id,
                                        feishu,
                                        session_manager,
                                        agent_text_chunks,
                                        agent_thought_chunks,
                                        config,
                                    )
                    plan_text = _format_plan(
                        [e.model_dump(mode="json", by_alias=True, exclude_none=True) for e in entries]
                    )
                    await _send_tool_msg(feishu, session_manager, session_id, plan_text)
        else:
            logger.debug("Notification [%s]: %s", session_id, type(update).__name__)
            if config.bridge.show_intermediate:
                await _flush_pending_tool_start(session_id)
                await _flush_agent_chunks(
                                    session_id,
                                    feishu,
                                    session_manager,
                                    agent_text_chunks,
                                    agent_thought_chunks,
                                    config,
                                )

    async def on_permission(session_id: str, options: list[PermissionOption], tool_call: Any = None) -> Optional[str]:
        """Handle permission requests from agents."""
        info = session_manager.find_by_session_id(session_id)

        if info is None:
            logger.warning("Session not found for permission: %s", session_id)
            return None

        root_message_id, session = info

        if agent_manager.is_auto_approve(session_id):
            if options:
                return options[0].option_id
            return None

        # Format and send permission request
        parts = ["⚠️ Permission Required"]
        if tool_call:
            title = getattr(tool_call, 'title', None)
            raw_input = getattr(tool_call, 'raw_input', None)
            if title:
                parts.append(f"\n🔧 {title}")
            if raw_input:
                detail = pretty_raw(raw_input)
                if len(detail) > 500:
                    detail = detail[:500] + "\n... (truncated)"
                fence = safe_backticks(detail)
                parts.append(f"{fence}\n{detail}\n{fence}")
        options_text = "\n".join(f"{i + 1}. {opt.name}" for i, opt in enumerate(options))
        parts.append(f"\n{options_text}\n\nReply with the number to approve, or 'deny' to reject.")
        await feishu.send_message(session.conversation_id, root_message_id, "\n".join(parts))

        future: asyncio.Future[Optional[str]] = asyncio.get_running_loop().create_future()
        pending_permissions[root_message_id] = {"options": options, "future": future}
        return await future

    async def _on_notification(session_id: str, update) -> None:
        # Main agent sessions are tracked in SessionManager; others are evaluator sessions
        if session_manager.find_by_session_id(session_id):
            await on_notification(session_id, update)
        else:
            await on_eval_notification(session_id, update)

    agent_manager = AgentManager(_on_notification, on_permission)
    agent_manager.register_agents([config.agent])

    async def notification_flush_callback(session_id: str):
        _log_accumulated_chunks(session_id, agent_text_chunks, agent_thought_chunks, final=True)
        pending_text_clear.discard(session_id)

        # --- Evaluator quality gate ---
        agent_text = agent_text_chunks.get(session_id, "")
        evaluator_cfg = find_matching_evaluator(agent_text, config) if agent_text else None

        if evaluator_cfg is not None:
            info = session_manager.find_by_session_id(session_id)
            if not info:
                logger.warning("[EVAL] Session not found for %s, skipping evaluation", session_id[:8])
            else:
                root_key, sess = info
                reply_to = sess.reply_to_message_id or root_key
                conversation_id = sess.conversation_id

                for attempt in range(1, evaluator_cfg.max_retries + 1):
                    logger.info("[EVAL] Attempt %d/%d for session %s",
                                attempt, evaluator_cfg.max_retries, session_id[:8])

                    if config.bridge.show_intermediate and conversation_id and reply_to:
                        await feishu.send_message(
                            conversation_id, reply_to,
                            f"🔍 质量评估中... ({attempt}/{evaluator_cfg.max_retries})",
                        )

                    passed, feedback = await run_evaluation(
                        agent_text, evaluator_cfg, sess, agent_manager
                    )

                    if passed:
                        logger.info("[EVAL] PASS on attempt %d", attempt)
                        break

                    logger.info("[EVAL] FAIL on attempt %d: %s", attempt, feedback[:500])

                    if attempt >= evaluator_cfg.max_retries:
                        logger.warning("[EVAL] Max retries reached, sending with warning")
                        agent_text_chunks[session_id] = (
                            agent_text + "\n\n> ⚠️ 此回答未通过自动质量评估，请注意核实。"
                        )
                        break

                    # Send feedback to original agent for revision
                    retry_prompt = evaluator_cfg.retry_prompt.format(feedback=feedback)
                    content = [{"type": "text", "text": retry_prompt}]
                    try:
                        # Clear old chunks before re-prompting
                        agent_text_chunks.pop(session_id, None)
                        agent_thought_chunks.pop(session_id, None)

                        await agent_manager.prompt(session_id, content)

                        # Collect the new response (prompt triggers on_notification which fills chunks)
                        agent_text = agent_text_chunks.get(session_id, "")
                        if not agent_text:
                            logger.warning("[EVAL] Agent returned empty response on retry")
                            break
                    except Exception as e:
                        logger.warning("[EVAL] Retry prompt failed: %s", e)
                        break

        await _flush_agent_chunks(session_id, feishu, session_manager, agent_text_chunks, agent_thought_chunks, config)

    # Fetch bot info before starting WebSocket
    await feishu.init()

    # Feishu event callback — called from SDK thread, must be thread-safe
    def on_feishu_event(event: FeishuEvent):
        loop.call_soon_threadsafe(event_queue.put_nowait, event)

    # Start Feishu WebSocket in a background thread (it blocks)
    loop = asyncio.get_running_loop()
    feishu_task = loop.run_in_executor(None, feishu.connect, on_feishu_event)

    # Wait for connection — connect() blocks forever on success,
    # so if it returns within 1s, something went wrong.
    done, _ = await asyncio.wait({feishu_task}, timeout=1)
    if done:
        feishu_task.result()  # raises the exception if any
        raise RuntimeError("Feishu connect() returned unexpectedly")

    # Start TTL eviction background task
    ttl_task = asyncio.create_task(_ttl_eviction_loop(session_manager, agent_manager, feishu))

    logger.info("Bridge started, waiting for events...")

    # Graceful shutdown on SIGTERM / SIGINT; second signal force-exits.
    shutdown_event = asyncio.Event()

    def _request_shutdown():
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _request_shutdown)

    # Main event loop
    try:
        while not shutdown_event.is_set():
            try:
                event = await asyncio.wait_for(event_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            logger.info(
                "[%s] Received: %s (root=%s, sender=%s)",
                event.message_id,
                event.clean_text[:50] or "(empty)",
                event.root_id,
                event.sender_id,
            )

            async def _safe_handle(ev):
                try:
                    await handle_event(
                        ev,
                        feishu,
                        config,
                        agent_manager,
                        session_manager,
                        pending_permissions,
                        notification_flush_callback,
                    )
                    logger.debug("Event handled for root_id=%s", ev.root_id)
                except Exception:
                    logger.exception("Unhandled error in handle_event")

            asyncio.create_task(_safe_handle(event))
    finally:
        logger.info("Bridge shutting down...")
        ttl_task.cancel()
        for sid in list(agent_manager._agents):
            try:
                await asyncio.wait_for(agent_manager.end_session(sid), timeout=5)
            except Exception:
                pass
        logger.info("All agents terminated")
        # feishu.connect() blocks a thread forever with no stop API;
        # os._exit is the only way to terminate without hanging.
        os._exit(0)


async def _flush_agent_chunks(
    session_id: str,
    feishu: FeishuConnection,
    session_manager: SessionManager,
    agent_text_chunks: dict[str, str],
    agent_thought_chunks: dict[str, str],
    config: Optional[Config] = None,
) -> None:
    """Flush accumulated agent streaming chunks to Feishu as messages."""
    info = session_manager.find_by_session_id(session_id)
    if info is None:
        logger.debug("Flush skipped [%s]: session not found", session_id)
        return

    root_message_id, session = info
    reply_to = session.reply_to_message_id or root_message_id

    if session_id in agent_text_chunks and agent_text_chunks[session_id]:
        text = agent_text_chunks.pop(session_id)

        # Detect markdown links: ![alt](path) and [text](path)
        # Upload images via send_image, other files via upload_file
        import os
        import re

        link_pattern = re.compile(r'(!?)\[([^\]]*)\]\(([^)]+)\)')
        img_exts = ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp')
        workspace = os.path.expanduser(config.bridge.default_workspace or "~") if config else os.getcwd()
        allowed_dirs = []
        if config:
            for d in (config.bridge.output_dir, config.bridge.attachment_dir):
                if d:
                    allowed_dirs.append(os.path.realpath(os.path.join(workspace, d)))
        img_count = 0
        file_count = 0
        for m in link_pattern.finditer(text):
            full_match, bang, _, link_path = m.group(0), m.group(1), m.group(2), m.group(3)
            abs_path = link_path if os.path.isabs(link_path) else os.path.join(workspace, link_path)
            abs_path = os.path.realpath(abs_path)
            if not os.path.isfile(abs_path):
                continue
            is_image = bang == '!' or os.path.splitext(abs_path)[1].lower() in img_exts
            if allowed_dirs and not any(abs_path.startswith(d + os.sep) for d in allowed_dirs):
                kind = "Image" if is_image else "File"
                logger.warning("%s outside allowed dirs, skipping: %s", kind, abs_path)
                text = text.replace(full_match, f"⚠️ {kind} not sent (no permission)")
                continue
            if is_image:
                if os.path.splitext(abs_path)[1].lower() not in img_exts:
                    continue
                try:
                    img_count += 1
                    msg_id = await feishu.send_image(session.conversation_id, reply_to, abs_path)
                    if msg_id:
                        session.last_bot_message_id = msg_id
                    text = text.replace(full_match, f'[pic{img_count}]')
                except Exception:
                    logger.warning("Failed to send image %s", abs_path, exc_info=True)
            else:
                try:
                    file_count += 1
                    with open(abs_path, "rb") as fh:
                        data = fh.read()
                    msg_id = await feishu.upload_file(
                        session.conversation_id, reply_to, data, os.path.basename(abs_path)
                    )
                    if msg_id:
                        session.last_bot_message_id = msg_id
                    text = text.replace(full_match, f'[file{file_count}]')
                except Exception:
                    logger.warning("Failed to send file %s", abs_path, exc_info=True)

        if text:
            msg_id = await feishu.send_message(session.conversation_id, reply_to, text)
            if msg_id:
                session.last_bot_message_id = msg_id

    if session_id in agent_thought_chunks and agent_thought_chunks[session_id]:
        text = agent_thought_chunks.pop(session_id)
        thought_text = "\n".join(f"> {line}" for line in text.splitlines())
        msg_id = await feishu.send_message(session.conversation_id, reply_to, thought_text)
        if msg_id:
            session.last_bot_message_id = msg_id


async def _send_tool_msg(
    feishu: FeishuConnection,
    session_manager: SessionManager,
    session_id: str,
    msg: str,
) -> None:
    """Send a tool/plan notification message to the session's conversation."""
    info = session_manager.find_by_session_id(session_id)
    if info is None:
        return
    key, s = info
    reply_to = s.reply_to_message_id or key
    msg_id = await feishu.send_message(s.conversation_id, reply_to, msg)
    if msg_id:
        s.last_bot_message_id = msg_id


def _format_plan(entries: list[dict]) -> str:
    lines = ["*Plan*"]
    for entry in entries:
        status = entry.get("status", "pending")
        marker = {"completed": "[x]", "in_progress": "[>]", "pending": "[ ]"}.get(status, "[?]")
        lines.append(f"{marker} {entry.get('content', '')}")
    return "\n".join(lines)


_RAW_DATA_MAX = 4000


def _format_raw_data(label: str, data: Any) -> str:
    """Format raw_input / raw_output as a fenced code block."""
    text = json.dumps(unescape_json_strings(data), indent=2, ensure_ascii=False) if not isinstance(data, str) else data
    if len(text) > _RAW_DATA_MAX:
        text = text[:_RAW_DATA_MAX] + "\n... (truncated)"
    fence = safe_backticks(text)
    return f"{label}:\n{fence}\n{text}\n{fence}"


def _format_tool_content(content: list) -> str:
    """Format tool call content items (file edits, terminal, text)."""
    parts: list[str] = []
    for item in content:
        if isinstance(item, FileEditToolCallContent):
            fence = safe_backticks(item.new_text)
            header = f"📝 {item.path}"
            parts.append(f"{header}\n{fence}diff\n{item.new_text}\n{fence}")
        elif isinstance(item, TerminalToolCallContent):
            parts.append(f"💻 Terminal: {item.terminal_id}")
        elif isinstance(item, ContentToolCallContent):
            if isinstance(item.content, TextContentBlock):
                parts.append(item.content.text or "")
    return "\n".join(parts)
