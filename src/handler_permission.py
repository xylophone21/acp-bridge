"""Permission response handler."""

import logging

logger = logging.getLogger(__name__)


async def handle_permission_response(
    text: str,
    options: list[dict],
    feishu,
    channel: str,
    thread_key: str,
) -> str | None:
    """Handle user's response to a permission request. Returns selected option_id or None."""
    text = text.strip()

    if text.lower() == "deny":
        feishu.send_message(channel, thread_key, "❌ Permission denied")
        return None

    try:
        choice = int(text)
        if 1 <= choice <= len(options):
            selected = options[choice - 1]
            name = selected.get("name", "")
            feishu.send_message(channel, thread_key, f"✅ Approved: {name}")
            return selected.get("optionId")
    except ValueError:
        pass

    feishu.send_message(channel, thread_key, "❌ Invalid response. Permission denied.")
    return None
