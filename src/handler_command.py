"""Command handler for bot commands (messages starting with #)."""

import logging
import os
import subprocess
from typing import Optional

from src.utils import expand_path, safe_backticks

logger = logging.getLogger(__name__)

HELP_MESSAGE = """Available commands:
- `#new <agent> [workspace] [-- <comment>]` - Start a new agent session
- `#agents` - List available agents
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
- `#help` - Show this help message
- `!<command>` - Execute shell command"""


def _parse_workspace_and_comment(args: list[str]) -> tuple[Optional[str], Optional[str]]:
    """Parse workspace and comment from command arguments."""
    if "--" in args:
        sep = args.index("--")
        ws = args[0] if sep > 0 else None
        cmt = " ".join(args[sep + 1:]) if sep + 1 < len(args) else None
        return ws, cmt
    return (args[0] if args else None), None


async def handle_command(
    text: str,
    channel: str,
    ts: str,
    thread_ts: Optional[str],
    feishu,
    config,
    agent_manager,
    session_manager,
):
    parts = text.strip().split()
    command = parts[0]

    in_thread = thread_ts is not None
    reply_to = thread_ts or ts

    if command == "#new":
        if in_thread:
            feishu.send_message(channel, reply_to, "Cannot create agent in a thread. Use #new in the main channel.")
            return

        if len(parts) < 2:
            feishu.send_message(channel, ts, "Usage: #new <agent_name> [workspace] [-- <comment>]")
            return

        agent_name = parts[1]
        workspace, comment = _parse_workspace_and_comment(parts[2:])

        agent_config = next((a for a in config.agents if a.name == agent_name), None)
        if agent_config is None:
            feishu.send_message(channel, ts, f"Agent not found: {agent_name}")
            return

        workspace_path = expand_path(workspace or config.bridge.default_workspace)
        if not os.path.isdir(workspace_path):
            feishu.send_message(channel, ts, f"Workspace does not exist: {workspace_path}")
            return

        try:
            result = await agent_manager.new_session(
                agent_name, workspace_path, agent_config.auto_approve
            )
        except Exception as e:
            feishu.send_message(channel, ts, f"Failed to create ACP session: {e}")
            return

        session_id = result.get("sessionId", "")

        try:
            session = await session_manager.create_session(
                ts, agent_name, workspace, channel, session_id
            )
        except Exception as e:
            feishu.send_message(channel, ts, f"Failed to create session: {e}")
            return

        # Store config options/modes/models from agent response
        if result.get("configOptions"):
            await session_manager.update_config_options(ts, result["configOptions"])
        if result.get("modes"):
            await session_manager.update_modes(ts, result["modes"])
        if result.get("models"):
            await session_manager.update_models(ts, result["models"])

        # Set default mode/model if configured
        if agent_config.default_mode:
            await _try_set_default_mode(
                agent_config.default_mode, session_id, session, agent_manager, session_manager, ts
            )
        if agent_config.default_model:
            await _try_set_default_model(
                agent_config.default_model, session_id, session, agent_manager, session_manager, ts
            )

        display_ws = workspace or config.bridge.default_workspace
        msg = f"Session started with agent: `{agent_name}`\nWorking directory: `{display_ws}`"
        if comment:
            msg += f"\nComment: {comment}"
        if agent_config.default_mode:
            msg += f"\nDefault mode: `{agent_config.default_mode.rstrip('!')}`"
        if agent_config.default_model:
            msg += f"\nDefault model: `{agent_config.default_model}`"
        msg += f"\nSend messages in this thread to interact with it.\n\n{HELP_MESSAGE}"
        feishu.send_message(channel, ts, msg)

    elif command == "#agents":
        lines = [f"• {a.name} - {a.description}" for a in config.agents]
        feishu.send_message(channel, reply_to, f"Available agents:\n" + "\n".join(lines))

    elif command == "#session":
        session = await session_manager.get_session(reply_to)
        if session is None:
            feishu.send_message(channel, reply_to, "No active session in this thread.")
            return
        status = "busy" if session.busy else "idle"
        msg = (
            f"Current session:\n• Agent: {session.agent_name}\n"
            f"• Workspace: {session.workspace}\n• Auto-approve: {session.auto_approve}\n• Status: {status}"
        )
        feishu.send_message(channel, reply_to, msg)

    elif command == "#sessions":
        sessions = session_manager.sessions
        if not sessions:
            feishu.send_message(channel, reply_to, "No active sessions.")
            return
        lines = []
        for s in sessions.values():
            status = "busy" if s.busy else "idle"
            lines.append(f"• Agent: {s.agent_name} | Workspace: {s.workspace} | Status: {status}")
        feishu.send_message(channel, reply_to, f"Active sessions ({len(sessions)}):\n" + "\n".join(lines))

    elif command == "#end":
        session = await session_manager.get_session(reply_to)
        if session:
            await agent_manager.end_session(session.session_id)
        try:
            await session_manager.end_session(reply_to)
            feishu.send_message(channel, reply_to, "Session ended.")
        except ValueError as e:
            feishu.send_message(channel, reply_to, f"Error: {e}")

    elif command == "#cancel":
        session = await session_manager.get_session(reply_to)
        if session is None:
            feishu.send_message(channel, reply_to, "No active session in this thread.")
            return
        if not session.busy:
            feishu.send_message(channel, reply_to, "No ongoing operation to cancel.")
            return
        try:
            await agent_manager.cancel(session.session_id)
            await session_manager.set_busy(reply_to, False)
            feishu.send_message(channel, reply_to, "Operation cancelled.")
        except Exception as e:
            feishu.send_message(channel, reply_to, f"Error: {e}")

    elif command == "#read":
        if len(parts) < 2:
            feishu.send_message(channel, reply_to, "Usage: #read <file_path>")
            return

        file_path = parts[1]
        session = await session_manager.get_session(reply_to)
        ws = expand_path(session.workspace if session else config.bridge.default_workspace)

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
                feishu.send_message(channel, reply_to, f"{file_path}:\n{ticks}\n{listing}\n{ticks}")
            except OSError as e:
                feishu.send_message(channel, reply_to, f"Error reading directory: {e}")
        else:
            try:
                with open(full_path, "r") as f:
                    content = f.read()
                ticks = safe_backticks(content)
                feishu.send_message(channel, reply_to, f"📄 {file_path}:\n{ticks}\n{content}\n{ticks}")
            except UnicodeDecodeError:
                # Binary file — upload instead
                with open(full_path, "rb") as f:
                    data = f.read()
                feishu.upload_file(channel, reply_to, data, os.path.basename(full_path))
            except OSError as e:
                feishu.send_message(channel, reply_to, f"Error reading file: {e}")

    elif command == "#diff":
        session = await session_manager.get_session(reply_to)
        if session is None:
            feishu.send_message(channel, reply_to, "No active session in this thread.")
            return

        ws = expand_path(session.workspace)
        extra_args = parts[1:]
        try:
            result = subprocess.run(
                ["git", "diff"] + extra_args,
                cwd=ws, capture_output=True, text=True, timeout=30,
            )
            diff = result.stdout.strip()
            if not diff:
                feishu.send_message(channel, reply_to, "No changes to show.")
            else:
                ticks = safe_backticks(diff)
                label = " ".join(extra_args) if extra_args else "(whole repo)"
                feishu.send_message(channel, reply_to, f"📝 Diff: {label}\n{ticks}\n{diff}\n{ticks}")
        except Exception as e:
            feishu.send_message(channel, reply_to, f"Error running git diff: {e}")

    elif command == "#mode":
        session = await session_manager.get_session(reply_to)
        if session is None:
            feishu.send_message(channel, reply_to, "No active session in this thread.")
            return

        if len(parts) < 2:
            msg = _format_config_options(session, "mode")
            feishu.send_message(channel, reply_to, msg)
        else:
            mode_value = parts[1].strip("`").rstrip("!")
            await _switch_config_option(session, "mode", mode_value, agent_manager, feishu, channel, reply_to)

    elif command == "#model":
        session = await session_manager.get_session(reply_to)
        if session is None:
            feishu.send_message(channel, reply_to, "No active session in this thread.")
            return

        if len(parts) < 2:
            msg = _format_config_options(session, "model")
            feishu.send_message(channel, reply_to, msg)
        else:
            model_value = parts[1].strip("`").rstrip("!")
            await _switch_config_option(session, "model", model_value, agent_manager, feishu, channel, reply_to)

    else:  # #help or unknown
        feishu.send_message(channel, reply_to, HELP_MESSAGE)


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


async def _switch_config_option(session, category, value, agent_manager, feishu, channel, reply_to):
    """Switch a config option (mode/model)."""
    if session.config_options:
        for opt in session.config_options:
            if opt.get("category") == category:
                try:
                    await agent_manager.set_config_option(session.session_id, opt["id"], value)
                    feishu.send_message(channel, reply_to, f"{category.title()} switched to: `{value}`")
                    return
                except Exception as e:
                    logger.debug("Failed to set %s via config_options: %s", category, e)

    # Fallback to deprecated API
    try:
        if category == "mode":
            await agent_manager.set_mode(session.session_id, value)
        else:
            await agent_manager.set_model(session.session_id, value)
        feishu.send_message(channel, reply_to, f"{category.title()} switched to: `{value}`")
    except Exception as e:
        feishu.send_message(channel, reply_to, f"Failed to switch {category}: {e}")


async def _try_set_default_mode(default_mode, session_id, session, agent_manager, session_manager, ts):
    mode_value = default_mode.rstrip("!")
    s = await session_manager.get_session(ts)
    if s and s.config_options:
        for opt in s.config_options:
            if opt.get("category") == "mode":
                try:
                    await agent_manager.set_config_option(session_id, opt["id"], mode_value)
                    return
                except Exception:
                    pass
    try:
        await agent_manager.set_mode(session_id, mode_value)
    except Exception:
        pass


async def _try_set_default_model(default_model, session_id, session, agent_manager, session_manager, ts):
    s = await session_manager.get_session(ts)
    if s and s.config_options:
        for opt in s.config_options:
            if opt.get("category") == "model":
                try:
                    await agent_manager.set_config_option(session_id, opt["id"], default_model)
                    return
                except Exception:
                    pass
    try:
        await agent_manager.set_model(session_id, default_model)
    except Exception:
        pass
