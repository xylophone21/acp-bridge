"""Permission response handler."""

import logging
from typing import Optional

from src.feishu import FeishuConnection

logger = logging.getLogger(__name__)


async def handle_permission_response(
    text: str,
    options: list[dict],
    feishu: FeishuConnection,
    conversation_id: str,
    thread_key: str,
) -> Optional[str]:
    """Handle user's response to a permission request. Returns selected option_id or None."""
    text = text.strip()

    if text.lower() == "deny":
        await feishu.send_message(conversation_id, thread_key, "❌ Permission denied")
        return None

    try:
        choice = int(text)
        if 1 <= choice <= len(options):
            selected = options[choice - 1]
            name = selected.get("name", "")
            await feishu.send_message(conversation_id, thread_key, f"✅ Approved: {name}")
            return selected.get("optionId")
    except ValueError:
        pass

    await feishu.send_message(conversation_id, thread_key, "❌ Invalid response. Permission denied.")
    return None
