"""Command handler for bot commands (messages starting with #)."""

import logging
import os
import subprocess
import time

from src.agent import AgentManager
from src.config import Config
from src.feishu import FeishuConnection, FeishuEvent
from src.session import SessionManager
from src.utils import expand_path, safe_backticks

logger = logging.getLogger(__name__)

HELP_MESSAGE = """Available commands:
- `#mode` - Show available modes and current mode
- `#mode <value>` - Switch to a different mode
- `#model` - Show available models and current model
- `#model <value>` - Switch to a different model
- `#cancel` - Cancel ongoing agent operation
- `#end` - End current agent session
- `#read <file_path>` - Read local file content
- `#diff [args]` - Show git diff
- `#session` - Show current agent session info
- `#sessions` - Show all active sessions
- `#help` - Show this help message"""


def _format_relative_time(timestamp: float) -> str:
    """Format a timestamp as relative time from now (e.g., '2m ago', '1h ago')."""
    delta = int(time.time() - timestamp)
    if delta < 60:
        return f"{delta}s ago"
    elif delta < 3600:
        return f"{delta // 60}m ago"
    elif delta < 86400:
        return f"{delta // 3600}h ago"
    else:
        return f"{delta // 86400}d ago"


async def handle_command(
    event: FeishuEvent,
    feishu: FeishuConnection,
    config: Config,
    agent_manager: AgentManager,
    session_manager: SessionManager,
):
    text = event.text
    conversation_id = event.conversation_id
    root_message_id = event.root_id
    reply_id = event.message_id
    parts = text.strip().split()
    command = parts[0]

    if command == "#session":
        session = session_manager.get_session_by_root(root_message_id)
        if session is None:
            await feishu.send_message(conversation_id, reply_id, "No active conversation.")
            return
        status = "busy" if session.busy else "idle"
        last_active = _format_relative_time(session.last_active) if session.last_active else "unknown"
        auto_approve = agent_manager.is_auto_approve(session.session_id)
        msg = (
            f"Current session:\n"
            f"• Status: {status}\n"
            f"• Summary: {session.summary}\n"
            f"• Last active: {last_active}\n"
            f"• Auto-approve: {'on' if auto_approve else 'off'}"
        )
        await feishu.send_message(conversation_id, reply_id, msg)

    elif command == "#sessions":
        sessions = session_manager.list_sessions()
        if not sessions:
            await feishu.send_message(conversation_id, reply_id, "No active sessions.")
        else:
            lines = []
            for s in sessions:
                status = "busy" if s.busy else "idle"
                summary = s.summary or "(no summary)"
                last_active = _format_relative_time(s.last_active) if s.last_active else "unknown"
                lines.append(f"• {summary} | {status} | {last_active}")
            await feishu.send_message(
                conversation_id, reply_id,
                f"Active sessions ({len(lines)}):\n" + "\n".join(lines),
            )

    elif command == "#end":
        session = session_manager.get_session_by_root(root_message_id)
        if session is None:
            await feishu.send_message(conversation_id, reply_id, "No active conversation.")
            return
        await agent_manager.end_session(session.session_id)
        try:
            session_manager.end_session(root_message_id)
            await feishu.add_reaction(session.trigger_message_id, "DONE")
            if session.last_bot_message_id:
                await feishu.add_reaction(session.last_bot_message_id, "DONE")
            await feishu.send_message(conversation_id, reply_id, "Session ended.")
        except ValueError as e:
            await feishu.send_message(conversation_id, reply_id, f"Error: {e}")

    elif command == "#cancel":
        session = session_manager.get_session_by_root(root_message_id)
        if session is None:
            await feishu.send_message(conversation_id, reply_id, "No active conversation.")
            return
        if not session.busy:
            await feishu.send_message(conversation_id, reply_id, "No ongoing operation to cancel.")
            return
        try:
            await agent_manager.cancel(session.session_id)
            session_manager.set_busy(root_message_id, False)
            await feishu.send_message(conversation_id, reply_id, "Operation cancelled.")
        except Exception as e:
            await feishu.send_message(conversation_id, reply_id, f"Error: {e}")

    elif command == "#read":
        if len(parts) < 2:
            await feishu.send_message(conversation_id, reply_id, "Usage: #read <file_path>")
            return

        file_path = parts[1]
        ws = expand_path(config.bridge.default_workspace)

        if file_path.startswith("~") or os.path.isabs(file_path):
            full_path = expand_path(file_path)
        else:
            full_path = os.path.join(ws, file_path)

        if os.path.isdir(full_path):
            try:
                entries = sorted(os.listdir(full_path))
                items = []
                for e in entries:
                    if os.path.isdir(os.path.join(full_path, e)):
                        items.append(f"{e}/")
                    else:
                        items.append(e)
                listing = "\n".join(items)
                ticks = safe_backticks(listing)
                await feishu.send_message(conversation_id, reply_id, f"{file_path}:\n{ticks}\n{listing}\n{ticks}")
            except OSError as e:
                await feishu.send_message(conversation_id, reply_id, f"Error reading directory: {e}")
        else:
            try:
                with open(full_path, "r") as f:
                    content = f.read()
                ticks = safe_backticks(content)
                await feishu.send_message(conversation_id, reply_id, f"📄 {file_path}:\n{ticks}\n{content}\n{ticks}")
            except UnicodeDecodeError:
                with open(full_path, "rb") as f:
                    data = f.read()
                await feishu.upload_file(conversation_id, reply_id, data, os.path.basename(full_path))
            except OSError as e:
                await feishu.send_message(conversation_id, reply_id, f"Error reading file: {e}")

    elif command == "#diff":
        session = session_manager.get_session_by_root(root_message_id)
        if session is None:
            await feishu.send_message(conversation_id, reply_id, "No active conversation.")
            return

        ws = expand_path(config.bridge.default_workspace)
        extra_args = parts[1:]
        try:
            result = subprocess.run(
                ["git", "diff"] + extra_args,
                cwd=ws, capture_output=True, text=True, timeout=30,
            )
            diff = result.stdout.strip()
            if not diff:
                await feishu.send_message(conversation_id, reply_id, "No changes to show.")
            else:
                ticks = safe_backticks(diff)
                label = " ".join(extra_args) if extra_args else "(whole repo)"
                await feishu.send_message(conversation_id, reply_id, f"📝 Diff: {label}\n{ticks}\n{diff}\n{ticks}")
        except Exception as e:
            await feishu.send_message(conversation_id, reply_id, f"Error running git diff: {e}")

    elif command == "#mode":
        session = session_manager.get_session_by_root(root_message_id)
        if session is None:
            await feishu.send_message(conversation_id, reply_id, "No active conversation.")
            return

        if len(parts) < 2:
            msg = _format_config_options(session, "mode")
            await feishu.send_message(conversation_id, reply_id, msg)
        else:
            mode_value = parts[1].strip("`").rstrip("!")
            await _switch_config_option(session, "mode", mode_value, agent_manager, feishu, conversation_id, reply_id)

    elif command == "#model":
        session = session_manager.get_session_by_root(root_message_id)
        if session is None:
            await feishu.send_message(conversation_id, reply_id, "No active conversation.")
            return

        if len(parts) < 2:
            msg = _format_config_options(session, "model")
            await feishu.send_message(conversation_id, reply_id, msg)
        else:
            model_value = parts[1].strip("`").rstrip("!")
            await _switch_config_option(session, "model", model_value, agent_manager, feishu, conversation_id, reply_id)

    else:  # #help or unknown
        await feishu.send_message(conversation_id, reply_id, HELP_MESSAGE)


def _format_config_options(session, category: str) -> str:
    """Format config options (mode/model) for display."""
    if session.config_options:
        for opt in session.config_options:
            if opt.get("category") == category:
                kind = opt.get("kind", {})
                current = kind.get("currentValue", "")
                options = kind.get("options", [])
                if isinstance(options, list):
                    lines = []
                    for o in options:
                        marker = " (current)" if o.get("value") == current else ""
                        lines.append(f"- `{o.get('value')}`{marker} - {o.get('description', o.get('name', ''))}")
                    return f"Available {category}s:\n" + "\n".join(lines)
    return f"No {category} configuration available."


async def _switch_config_option(session, category, value, agent_manager, feishu, channel, reply_id):
    """Switch a config option (mode/model)."""
    if session.config_options:
        for opt in session.config_options:
            if opt.get("category") == category:
                try:
                    await agent_manager.set_config_option(session.session_id, opt["id"], value)
                    await feishu.send_message(channel, reply_id, f"{category.title()} switched to: `{value}`")
                    return
                except Exception as e:
                    logger.debug("Failed to set %s via config_options: %s", category, e)

    # Fallback to deprecated API
    try:
        if category == "mode":
            await agent_manager.set_mode(session.session_id, value)
        else:
            await agent_manager.set_model(session.session_id, value)
        await feishu.send_message(channel, reply_id, f"{category.title()} switched to: `{value}`")
    except Exception as e:
        await feishu.send_message(channel, reply_id, f"Failed to switch {category}: {e}")
