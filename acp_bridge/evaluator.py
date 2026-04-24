"""Evaluator — quality gate that reviews agent responses before sending to user.

Flow:
  1. Agent completes a response (text accumulated in bridge's agent_text_chunks)
  2. bridge checks trigger_pattern against the text → finds matching evaluator
  3. Spawns (or reuses) an evaluator agent session, sends text for review
  4. Parses evaluator response with pass_pattern
     - Match → PASS, response sent to user
     - No match → FAIL, evaluator's full response sent back to original agent as feedback
  5. Retry up to max_retries times, then send with warning

Evaluator sessions are persistent per main session (stored in SessionState),
so retries within the same conversation share context.

Notification routing: bridge uses SessionManager.is_main_session() to distinguish
main agent sessions from evaluator sessions, routing the latter to on_eval_notification.
"""

import asyncio
import logging
import re
from collections import defaultdict
from typing import Optional

from acp_bridge.agent import AgentManager
from acp_bridge.config import AgentConfig, Config, EvaluatorConfig
from acp_bridge.session import SessionState
from acp_bridge.utils import expand_path, pretty_raw

logger = logging.getLogger(__name__)

# Streaming text chunks from evaluator agents, keyed by evaluator session_id.
# Same pattern as bridge.py's agent_text_chunks / pending_text_clear:
# - Chunks accumulate per session
# - ToolCallStart marks pending clear (lazy: old text discarded on next chunk)
# - Only the last text segment (after final tool call) is kept as the verdict
_eval_chunks: dict[str, str] = defaultdict(str)
_eval_pending_clear: set[str] = set()


async def on_eval_notification(session_id: str, update) -> None:
    """Notification handler for evaluator agent sessions.

    Mirrors bridge.py's on_notification but only collects text and logs —
    no Feishu output, no plan handling.
    """
    from acp.schema import (
        AgentMessageChunk,
        AgentThoughtChunk,
        TextContentBlock,
        ToolCallProgress,
        ToolCallStart,
    )
    if isinstance(update, AgentMessageChunk):
        if isinstance(update.content, TextContentBlock):
            # Lazy clear: discard old text on first new chunk after a tool call
            if session_id in _eval_pending_clear:
                _eval_chunks.pop(session_id, None)
                _eval_pending_clear.discard(session_id)
            if not _eval_chunks[session_id]:
                logger.info("[EVAL] [RESP] [%s] Evaluator responding (first chunk)...", session_id[:8])
            _eval_chunks[session_id] += update.content.text or ""
    elif isinstance(update, AgentThoughtChunk):
        if isinstance(update.content, TextContentBlock):
            logger.debug("[EVAL] [THINK] [%s] Thinking...", session_id[:8])
    elif isinstance(update, ToolCallStart):
        # Mark for lazy clear — next text chunk will discard accumulated text
        _eval_pending_clear.add(session_id)
        title = update.title or ""
        if update.raw_input:
            raw = pretty_raw(update.raw_input)
            logger.info("[EVAL] [TOOL] [%s] %s\n  input: %s", session_id[:8], title[:80], raw[:2000])
        elif title:
            logger.info("[EVAL] [TOOL] [%s] %s", session_id[:8], title[:80])
    elif isinstance(update, ToolCallProgress):
        if update.status in ("completed", "failed"):
            icon = "[DONE]" if update.status == "completed" else "[FAIL]"
            if update.raw_output:
                out = pretty_raw(update.raw_output)
                logger.info("[EVAL] %s [%s] %s\n  output (%d chars): %s",
                            icon, session_id[:8], (update.title or "")[:80], len(out), out[:2000])
            else:
                logger.info("[EVAL] %s [%s] %s", icon, session_id[:8], (update.title or "")[:80])


def find_matching_evaluator(text: str, config: Config) -> Optional[EvaluatorConfig]:
    """Return the first evaluator whose trigger_pattern matches the text.

    Evaluators are checked in config order; first match wins.
    """
    for ev in config.evaluators:
        if ev.trigger_pattern and re.search(ev.trigger_pattern, text):
            return ev
    return None


async def _get_or_create_eval_session(
    evaluator_cfg: EvaluatorConfig,
    session_state: SessionState,
    agent_manager: AgentManager,
) -> str:
    """Return existing evaluator session_id or create a new one.

    Evaluator sessions are persisted in session_state.evaluator_session_ids
    (keyed by evaluator name) so retries reuse the same session with context.
    """
    async with session_state.eval_lock:
        existing = session_state.evaluator_session_ids.get(evaluator_cfg.name)
        if existing and agent_manager.has_session(existing):
            return existing

        agent_cfg = AgentConfig(
            name=evaluator_cfg.name,
            description=f"Evaluator: {evaluator_cfg.name}",
            command=evaluator_cfg.command,
            args=list(evaluator_cfg.args),
            env=dict(evaluator_cfg.env),
            auto_approve=bool(evaluator_cfg.auto_approve),
        )
        agent_manager.register_agents([agent_cfg])

        workspace = expand_path(evaluator_cfg.workspace)
        result = await asyncio.wait_for(
            agent_manager.new_session(agent_cfg.name, workspace, agent_cfg.auto_approve),
            timeout=30,
        )
        session_id = result.get("sessionId", "")
        session_state.evaluator_session_ids[evaluator_cfg.name] = session_id
        logger.info("[EVAL] Created evaluator session %s for %s", session_id[:8], evaluator_cfg.name)
        return session_id


async def run_evaluation(
    agent_text: str,
    evaluator_cfg: EvaluatorConfig,
    session_state: SessionState,
    agent_manager: AgentManager,
) -> tuple[bool, str]:
    """Send agent_text to evaluator for review.

    Args:
        agent_text: The main agent's response to evaluate.
        evaluator_cfg: Matched evaluator config (trigger, pass_pattern, command, etc.).
        session_state: Main session state — evaluator session_id stored here for reuse.
        agent_manager: Shared AgentManager for spawning/prompting evaluator sessions.

    Returns:
        (True, "") if evaluator response matches pass_pattern.
        (False, full_response) if not — full response is used as feedback for
        either the next retry or the final warning sent to the user.
    """
    try:
        eval_session_id = await _get_or_create_eval_session(
            evaluator_cfg, session_state, agent_manager
        )

        # If evaluator_cfg.prompt is set, prepend it as instruction;
        # otherwise just send the raw text (relying on agent profile's system prompt)
        prompt_text = agent_text
        if evaluator_cfg.prompt:
            prompt_text = f"{evaluator_cfg.prompt}\n\n---\n\n{agent_text}"
        content = [{"type": "text", "text": prompt_text}]

        logger.info("[EVAL] Sending to evaluator %s (%d chars)", evaluator_cfg.name, len(prompt_text))
        resp = await agent_manager.prompt(eval_session_id, content)

        # Prefer streamed chunks (accumulated via on_eval_notification);
        # fall back to resp.content for agents that don't stream
        eval_text = _eval_chunks.pop(eval_session_id, "") or _extract_response_text(resp)
        logger.info("[EVAL] Evaluator response (%d chars):\n%s", len(eval_text), eval_text[:2000])

        return _parse_verdict(eval_text, evaluator_cfg.pass_pattern)
    except Exception as e:
        logger.warning("[EVAL] Evaluation failed: %s", e)
        # On error, let the original response through
        return True, ""


def _extract_response_text(resp: dict) -> str:
    """Extract text from ACP prompt response content blocks."""
    content = resp.get("content", [])
    return "\n".join(
        b.get("text", "") for b in content
        if isinstance(b, dict) and b.get("type") == "text"
    )


def _parse_verdict(text: str, pass_pattern: str) -> tuple[bool, str]:
    """Check if evaluator response matches pass_pattern.

    Bridge doesn't parse evaluator output structure — just regex match.
    If pass_pattern matches anywhere in the response, it's a PASS.
    Otherwise FAIL, and the full response text becomes feedback for the original agent.
    """
    if not text.strip():
        return True, ""
    if re.search(pass_pattern, text):
        return True, ""
    return False, text
