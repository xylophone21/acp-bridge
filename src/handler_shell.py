"""Shell command handler for messages starting with !."""

import logging
import subprocess

from src.utils import expand_path, safe_backticks

logger = logging.getLogger(__name__)


async def handle_shell_command(
    text: str,
    channel: str,
    thread_ts: str | None,
    feishu,
    config,
    session_manager,
):
    cmd = text.strip().lstrip("!").strip()
    if not cmd:
        feishu.send_message(channel, thread_ts, "Usage: !<command>")
        return

    # Get workspace from session or default
    workspace = config.bridge.default_workspace
    if thread_ts:
        session = await session_manager.get_session(thread_ts)
        if session:
            workspace = session.workspace
    workspace = expand_path(workspace)

    try:
        result = subprocess.run(
            cmd, shell=True, cwd=workspace,
            capture_output=True, text=True, timeout=60,
        )

        parts = []
        if result.returncode != 0:
            parts.append(f"Exit code: {result.returncode}")
        if result.stdout.strip():
            ticks = safe_backticks(result.stdout.strip())
            parts.append(f"{ticks}\n{result.stdout.strip()}\n{ticks}")
        if result.stderr.strip():
            ticks = safe_backticks(result.stderr.strip())
            parts.append(f"Stderr:\n{ticks}\n{result.stderr.strip()}\n{ticks}")

        response = "\n\n".join(parts) if parts else "Command executed successfully (no output)"
    except subprocess.TimeoutExpired:
        response = "Command timed out (60s limit)"
    except Exception as e:
        response = f"Failed to execute command: {e}"

    feishu.send_message(channel, thread_ts, response)
