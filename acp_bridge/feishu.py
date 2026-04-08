"""Feishu/Lark client module for WebSocket long-connection and message handling.

Uses the official lark-oapi SDK for:
- WebSocket long-connection (protobuf frames handled by SDK)
- Event dispatching (im.message.receive_v1)
- REST API calls (send/update messages, file upload/download)
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional, TypeVar

import httpx
import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateFileRequest,
    CreateFileRequestBody,
    CreateImageRequest,
    CreateImageRequestBody,
    CreateMessageReactionRequest,
    CreateMessageReactionRequestBody,
    CreateMessageRequest,
    CreateMessageRequestBody,
    DeleteMessageReactionRequest,
    GetMessageRequest,
    GetMessageResourceRequest,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)
from lark_oapi.api.im.v1.model.emoji import Emoji

logger = logging.getLogger(__name__)


@dataclass
class FeishuFile:
    file_key: str
    file_name: str
    file_type: str


@dataclass
class FeishuEvent:
    """Represents an incoming message event from Feishu.

    Field mapping from ``P2ImMessageReceiveV1``:

        conversation_id  <- event.message.chat_id
        message_id       <- event.message.message_id
        parent_id        <- event.message.parent_id
        root_id          <- event.message.root_id (fallback: parent_id, then message_id)
        is_mention_bot   <- event.message.mentions[].id.open_id == bot_open_id
        sender_id        <- event.sender.sender_id.open_id
        chat_type        <- event.message.chat_type ("p2p" | "group")

    Attributes:
        conversation_id: Unique ID of the conversation (group chat or DM).
        message_id: Unique ID of this message. Use as ``reply_to`` in
            ``send_message`` / ``upload_file`` to reply to this specific message.
        parent_id: ID of the message this one replies to, or None if top-level.
        text: Plain text content extracted from the message.
        files: List of file attachments on the message.
        root_id: ID of the root message in the reply chain. For top-level
            messages, equals message_id. Always set. Used as session lookup key.
        is_mention_bot: True if the bot was @mentioned in this message.
        sender_id: open_id of the message sender.
        chat_type: "p2p" for DM, "group" for group chat.
    """

    conversation_id: str
    message_id: str
    parent_id: Optional[str]
    text: str
    clean_text: str = ""
    files: list[FeishuFile] = field(default_factory=list)
    root_id: str = ""
    is_mention_bot: bool = False
    has_other_mentions: bool = False
    sender_id: str = ""
    chat_type: str = ""

    def __post_init__(self):
        if not self.clean_text:
            self.clean_text = self.text


def _detect_image_ext(data: bytes) -> Optional[str]:
    """Return correct extension based on image magic bytes, or None to keep original."""
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    return None


def _parse_content(msg_type: str, content_str: str) -> tuple[str, list[FeishuFile]]:
    """Parse message content JSON into (text, files) based on msg_type."""
    text = ""
    files: list[FeishuFile] = []
    try:
        content = json.loads(content_str)
    except (json.JSONDecodeError, TypeError):
        return text, files

    if msg_type == "text":
        text = content.get("text", "")
    elif msg_type == "image":
        ik = content.get("image_key", "")
        if ik:
            files.append(FeishuFile(file_key=ik, file_name=f"{ik}.png", file_type="image"))
            text = f"{{{{attachment:{ik}}}}}"
    elif msg_type == "file":
        fk = content.get("file_key", "")
        fn = content.get("file_name", "file")
        if fk:
            files.append(FeishuFile(file_key=fk, file_name=f"{fk}_{fn}", file_type="file"))
            text = f"{{{{attachment:{fk}}}}}"
    elif msg_type == "post":
        parts = []
        for paragraph in content.get("content", []):
            for elem in paragraph:
                tag = elem.get("tag", "")
                if tag == "text":
                    parts.append(elem.get("text", ""))
                elif tag == "a":
                    parts.append(elem.get("text", "") or elem.get("href", ""))
                elif tag == "img":
                    ik = elem.get("image_key", "")
                    if ik:
                        files.append(FeishuFile(file_key=ik, file_name=f"{ik}.png", file_type="image"))
                        parts.append(f"{{{{attachment:{ik}}}}}")
                elif tag == "media":
                    fk = elem.get("file_key", "")
                    fn = elem.get("file_name", "media")
                    if fk:
                        files.append(FeishuFile(file_key=fk, file_name=f"{fk}_{fn}", file_type="file"))
                        parts.append(f"{{{{attachment:{fk}}}}}")
            parts.append("\n")
        title = content.get("title", "")
        text = (title + "\n" if title else "") + "".join(parts).strip()

    return text, files


@dataclass
class _ApiResult:
    """Unified result for both SDK and httpx API calls."""

    code: int = 0
    data: Optional[str] = None

    def success(self) -> bool:
        return self.code == 0


EventCallback = Callable[[FeishuEvent], None]

T = TypeVar("T")

_RATE_LIMIT_CODE = 99991400
_RETRYABLE_CODES = {
    99991400,  # request trigger frequency limit
    1500,  # internal error
    5000,  # internal error, reduce frequency
    10101,  # internal error
    55001,  # server internal error
    90217,  # too many requests
    90235,  # server busy
    1000004,  # method rate limited
    1000005,  # app rate limited
    190005,  # app rate limited
    11232,  # create message trigger rate limit
    11233,  # create message chat trigger rate limit
    11247,  # internal send message trigger rate limit
    18121,  # create request is being processed
}
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 1.0  # seconds


async def _retry_on_rate_limit(fn: Callable[[], Awaitable[T]], retries: int = _MAX_RETRIES) -> T:
    """Call async fn and retry with exponential backoff on retryable errors or network failures."""
    for attempt in range(retries + 1):
        try:
            result: T = await fn()
            code = getattr(result, "code", None)
            if code not in _RETRYABLE_CODES:
                return result
            # Retryable error — retry
            if attempt < retries:
                delay = _RETRY_BASE_DELAY * (2**attempt)
                logger.warning(
                    "Retryable error %s (attempt %d/%d), retrying in %.1fs",
                    code,
                    attempt + 1,
                    retries,
                    delay,
                )
                await asyncio.sleep(delay)
        except Exception as e:
            if attempt < retries:
                delay = _RETRY_BASE_DELAY * (2**attempt)
                logger.warning(
                    "Network error (attempt %d/%d): %s, retrying in %.1fs",
                    attempt + 1,
                    retries,
                    e,
                    delay,
                )
                await asyncio.sleep(delay)
            else:
                raise
    # Final retryable response after all retries
    return result  # type: ignore[possibly-undefined]


class FeishuConnection:
    """Feishu client for WebSocket long-connection and API calls."""

    def __init__(self, app_id: str, app_secret: str):
        self._app_id = app_id
        self._app_secret = app_secret
        self._event_callback: Optional[EventCallback] = None
        self._client = lark.Client.builder().app_id(app_id).app_secret(app_secret).build()
        self._bot_open_id: Optional[str] = None
        self._user_info_cache: dict[str, tuple[Optional[str], Optional[str]]] = {}

    async def init(self) -> None:
        """Fetch and cache bot's open_id. Call before connect().

        Retries on transient errors (rate limit, server busy, network).
        Raises RuntimeError on permanent failure.
        """
        result = await _retry_on_rate_limit(self._get_bot_open_id)
        if not result.success():
            raise RuntimeError(
                f"Failed to fetch bot open_id (code={result.code}). Check app_id/app_secret and network."
            )

    def connect(self, callback: EventCallback) -> None:
        """Start WebSocket long-connection. Blocks **indefinitely** on success.

        Under normal operation this method never returns — it keeps the
        WebSocket alive until the process is killed. If it returns or raises,
        it means the connection failed or was lost.

        This is intentionally sync because ``lark.ws.Client.start()`` is a
        blocking call that runs the SDK's own event loop. The caller should
        run this in a background thread via ``run_in_executor``.
        """
        self._event_callback = callback

        event_handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._on_message_receive)
            .build()
        )

        ws_client = lark.ws.Client(
            self._app_id,
            self._app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.INFO,
        )
        ws_client.start()

    def _on_message_receive(self, data: lark.im.v1.P2ImMessageReceiveV1) -> None:
        """Handle im.message.receive_v1 event from SDK.

        Called synchronously by the SDK from its WebSocket thread — must not
        be async. Builds a ``FeishuEvent`` and passes it to the callback,
        which should use ``call_soon_threadsafe`` to hand off to the asyncio
        event loop.
        """
        try:
            self._on_message_receive_inner(data)
        except Exception:
            logger.exception("Error in _on_message_receive")

    def _on_message_receive_inner(self, data: lark.im.v1.P2ImMessageReceiveV1) -> None:
        if self._event_callback is None:
            return

        if data.event is None or data.event.message is None:
            return

        msg = data.event.message
        chat_id = msg.chat_id or ""
        message_id = msg.message_id or ""
        parent_id = msg.parent_id if msg.parent_id else None

        # Resolve root_id: the session lookup key.
        # Feishu provides root_id for reply-chain messages; fall back to
        # parent_id (defensive), then message_id (top-level message).
        root_id = msg.root_id or msg.parent_id or message_id

        # Extract text and files from content JSON
        msg_type = msg.message_type if isinstance(msg.message_type, str) else "text"
        text, files = _parse_content(msg_type, msg.content or "")

        # Strip bot @mention from text
        if msg.mentions and self._bot_open_id:
            for mention in msg.mentions:
                if mention.id and mention.id.open_id == self._bot_open_id:
                    key = getattr(mention, "key", None)
                    if isinstance(key, str):
                        text = text.replace(key, "").strip()

        # Build clean_text with all @mention placeholders removed
        clean_text = text
        if msg.mentions:
            for mention in msg.mentions:
                key = getattr(mention, "key", None)
                if isinstance(key, str):
                    clean_text = clean_text.replace(key, "")
            clean_text = clean_text.strip()

        # Determine if bot was @mentioned
        is_mention_bot = False
        has_other_mentions = False
        if msg.mentions and self._bot_open_id:
            for mention in msg.mentions:
                if mention.id and mention.id.open_id == self._bot_open_id:
                    is_mention_bot = True
                else:
                    has_other_mentions = True

        # Extract sender ID
        sender_id = ""
        sender = data.event.sender
        if sender and sender.sender_id:
            sender_id = sender.sender_id.open_id or ""

        event = FeishuEvent(
            conversation_id=chat_id,
            message_id=message_id,
            parent_id=parent_id,
            text=text,
            clean_text=clean_text,
            files=files,
            root_id=root_id,
            is_mention_bot=is_mention_bot,
            has_other_mentions=has_other_mentions,
            sender_id=sender_id,
            chat_type=msg.chat_type or "",
        )
        self._event_callback(event)

    async def send_message(self, conversation_id: str, reply_to: Optional[str], text: str) -> Optional[str]:
        """Send a text message. Returns message_id of the sent message.

        Args:
            conversation_id: Target conversation (group or DM). Only used when
                ``reply_to`` is None to send a standalone message.
            reply_to: message_id to reply to (typically ``event.message_id``).
                When provided, the message is sent as a reply in the same thread.
                When None, a new standalone message is sent to the conversation.
        """
        if reply_to:
            # Reply to a specific message
            body = ReplyMessageRequestBody.builder().msg_type("text").content(json.dumps({"text": text})).build()
            req = ReplyMessageRequest.builder().message_id(reply_to).request_body(body).build()
            resp = await _retry_on_rate_limit(lambda: self._client.im.v1.message.areply(req))  # type: ignore[union-attr]
        else:
            body = (
                CreateMessageRequestBody.builder()
                .msg_type("text")
                .receive_id(conversation_id)
                .content(json.dumps({"text": text}))
                .build()
            )
            req = CreateMessageRequest.builder().receive_id_type("chat_id").request_body(body).build()
            resp = await _retry_on_rate_limit(lambda: self._client.im.v1.message.acreate(req))  # type: ignore[union-attr]

        if not resp.success():
            logger.error("Failed to send message: %s %s", resp.code, resp.msg)
            return None

        return resp.data.message_id  # type: ignore[union-attr]

    async def resolve_attachments(
        self, event: FeishuEvent, workspace: str, attachment_dir: str, resolve_parent: bool = True,
    ) -> str:
        """Download all attachments (inline + quoted parent) and return resolved text.

        Args:
            workspace: Absolute workspace root path.
            attachment_dir: Relative path under workspace for saving/referencing files.
        """
        import os
        text = event.text
        abs_dir = os.path.join(workspace, attachment_dir)

        if event.files or event.parent_id:
            os.makedirs(abs_dir, exist_ok=True)

        # Resolve inline attachment placeholders
        for f in event.files:
            placeholder = f"{{{{attachment:{f.file_key}}}}}"
            path = await self._save_file(event.message_id, f, abs_dir)
            if path:
                ref = os.path.join(attachment_dir, f.file_name)
                text = text.replace(placeholder, f"\n[Attached {f.file_type}: {ref}]\n")
            else:
                text = text.replace(placeholder, "")

        # Fetch and download attachments from quoted parent message
        if event.parent_id and resolve_parent:
            parent_text, parent_files = await self._get_parent_content(event.parent_id)
            for f in parent_files:
                placeholder = f"{{{{attachment:{f.file_key}}}}}"
                path = await self._save_file(event.parent_id, f, abs_dir)
                if path:
                    ref = os.path.join(attachment_dir, f.file_name)
                    tag = f"\n[Attached {f.file_type} from quoted message: {ref}]\n"
                    parent_text = parent_text.replace(placeholder, tag)
                else:
                    parent_text = parent_text.replace(placeholder, "")
            if parent_text.strip():
                text += f"\n[Quoted message: {parent_text.strip()}]\n"

        return text

    async def _save_file(self, message_id: str, f: FeishuFile, dest_dir: str) -> Optional[str]:
        """Download a single file and save to dest_dir. Skips if already exists. Returns path or None."""
        import os
        path = os.path.join(dest_dir, f.file_name)
        if os.path.exists(path):
            logger.debug("Already exists, skipping download: %s", path)
            return path
        try:
            data = await self._download_file(
                message_id, f.file_key,
                resource_type="image" if f.file_type == "image" else "file",
            )
            if data:
                # Fix extension based on actual content (Feishu always returns .png name but may serve JPEG)
                if f.file_type == "image":
                    ext = _detect_image_ext(data)
                    if ext:
                        base, _ = os.path.splitext(path)
                        path = base + ext
                        f.file_name = os.path.basename(path)
                with open(path, "wb") as fh:
                    fh.write(data)
                logger.debug("Downloaded %s -> %s", f.file_key, path)
                return path
        except Exception:
            logger.warning("Failed to download %s %s", f.file_type, f.file_key, exc_info=True)
        return None

    async def _get_parent_content(self, parent_id: str) -> tuple[str, list[FeishuFile]]:
        """Fetch a message by ID and extract text and file/image attachments."""
        req = GetMessageRequest.builder().message_id(parent_id).build()
        resp = await _retry_on_rate_limit(lambda: self._client.im.v1.message.aget(req))  # type: ignore[union-attr]
        if not resp.success() or not resp.data or not resp.data.items:
            return "", []
        msg = resp.data.items[0]
        msg_type = msg.msg_type or ""
        content_str = msg.body.content if msg.body else ""
        return _parse_content(msg_type, content_str or "")

    async def _download_file(self, message_id: str, file_key: str, resource_type: str = "file") -> Optional[bytes]:
        """Download a file or image from Feishu.

        Args:
            resource_type: "file" for file attachments, "image" for images.
        """
        req = GetMessageResourceRequest.builder().message_id(message_id).file_key(file_key).type(resource_type).build()
        resp = await _retry_on_rate_limit(lambda: self._client.im.v1.message_resource.aget(req))  # type: ignore[union-attr]
        if not resp.success():
            logger.error("Failed to download file: %s %s", resp.code, resp.msg)
            return None
        if resp.file:
            return resp.file.read()
        return None

    async def upload_file(
        self,
        conversation_id: str,
        reply_to: Optional[str],
        content: bytes,
        filename: str,
    ) -> Optional[str]:
        """Upload a file and send it as a message. Returns message_id.

        Args:
            conversation_id: Target conversation. Only used when ``reply_to``
                is None.
            reply_to: message_id to reply to (typically ``event.message_id``).
            content: Raw file bytes.
            filename: Display name for the file.
        """
        import io

        # Upload file to get file_key
        file_body = (
            CreateFileRequestBody.builder().file_type("stream").file_name(filename).file(io.BytesIO(content)).build()
        )
        file_req = CreateFileRequest.builder().request_body(file_body).build()
        file_resp = await _retry_on_rate_limit(lambda: self._client.im.v1.file.acreate(file_req))  # type: ignore[union-attr]
        if not file_resp.success():
            logger.error("Failed to upload file: %s %s", file_resp.code, file_resp.msg)
            return None
        file_key = file_resp.data.file_key  # type: ignore[union-attr]

        # Send file message
        msg_content = json.dumps({"file_key": file_key})
        if reply_to:
            # Reply to a specific message
            body = ReplyMessageRequestBody.builder().msg_type("file").content(msg_content).build()
            req = ReplyMessageRequest.builder().message_id(reply_to).request_body(body).build()
            resp = await _retry_on_rate_limit(lambda: self._client.im.v1.message.areply(req))  # type: ignore[union-attr]
        else:
            body = (
                CreateMessageRequestBody.builder()
                .msg_type("file")
                .receive_id(conversation_id)
                .content(msg_content)
                .build()
            )
            req = CreateMessageRequest.builder().receive_id_type("chat_id").request_body(body).build()
            resp = await _retry_on_rate_limit(lambda: self._client.im.v1.message.acreate(req))  # type: ignore[union-attr]

        if not resp.success():
            logger.error("Failed to send file message: %s %s", resp.code, resp.msg)
            return None
        return resp.data.message_id  # type: ignore[union-attr]

    async def send_image(
        self,
        conversation_id: str,
        reply_to: Optional[str],
        image_path: str,
    ) -> Optional[str]:
        """Upload a local image and send it as an image message. Returns message_id."""
        import io

        with open(image_path, "rb") as f:
            image_bytes = f.read()

        # Upload image to get image_key
        img_body = CreateImageRequestBody.builder().image_type("message").image(io.BytesIO(image_bytes)).build()
        img_req = CreateImageRequest.builder().request_body(img_body).build()
        img_resp = await _retry_on_rate_limit(lambda: self._client.im.v1.image.acreate(img_req))  # type: ignore[union-attr]
        if not img_resp.success():
            logger.error("Failed to upload image: %s %s", img_resp.code, img_resp.msg)
            return None
        image_key = img_resp.data.image_key  # type: ignore[union-attr]

        # Send image message
        msg_content = json.dumps({"image_key": image_key})
        if reply_to:
            body = ReplyMessageRequestBody.builder().msg_type("image").content(msg_content).build()
            req = ReplyMessageRequest.builder().message_id(reply_to).request_body(body).build()
            resp = await _retry_on_rate_limit(lambda: self._client.im.v1.message.areply(req))  # type: ignore[union-attr]
        else:
            body = (
                CreateMessageRequestBody.builder()
                .msg_type("image")
                .receive_id(conversation_id)
                .content(msg_content)
                .build()
            )
            req = CreateMessageRequest.builder().receive_id_type("chat_id").request_body(body).build()
            resp = await _retry_on_rate_limit(lambda: self._client.im.v1.message.acreate(req))  # type: ignore[union-attr]

        if not resp.success():
            logger.error("Failed to send image message: %s %s", resp.code, resp.msg)
            return None
        return resp.data.message_id  # type: ignore[union-attr]

    async def add_reaction(self, message_id: str, emoji_type: str) -> Optional[str]:
        """Add a reaction emoji to a message. Returns reaction_id or None."""
        body = (
            CreateMessageReactionRequestBody.builder()
            .reaction_type(Emoji.builder().emoji_type(emoji_type).build())
            .build()
        )
        req = CreateMessageReactionRequest.builder().message_id(message_id).request_body(body).build()
        resp = await _retry_on_rate_limit(lambda: self._client.im.v1.message_reaction.acreate(req))  # type: ignore[union-attr]
        if not resp.success():
            logger.error("Failed to add reaction: %s %s", resp.code, resp.msg)
            return None
        return resp.data.reaction_id if resp.data else None  # type: ignore[union-attr]

    async def remove_reaction(self, message_id: str, reaction_id: str) -> bool:
        """Remove a reaction from a message."""
        req = DeleteMessageReactionRequest.builder().message_id(message_id).reaction_id(reaction_id).build()
        resp = await _retry_on_rate_limit(lambda: self._client.im.v1.message_reaction.adelete(req))  # type: ignore[union-attr]
        if not resp.success():
            logger.error("Failed to remove reaction: %s %s", resp.code, resp.msg)
            return False
        return True

    async def _get_bot_open_id(self) -> _ApiResult:
        """Get the bot's open_id via the bot info API. Caches the result.

        Returns _ApiResult with code=0 and data=open_id on success,
        or the Feishu error code on failure (for retry classification).
        """
        if self._bot_open_id:
            return _ApiResult(code=0, data=self._bot_open_id)

        token_result = await self._get_tenant_token()
        if not token_result.success():
            return token_result

        url = "https://open.feishu.cn/open-apis/bot/v3/info"
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                url,
                headers={"Authorization": f"Bearer {token_result.data}"},
            )
            if resp.status_code == 200:
                data = resp.json()
                code = data.get("code", -1)
                if code == 0:
                    self._bot_open_id = data.get("bot", {}).get("open_id")
                    return _ApiResult(code=0, data=self._bot_open_id)
                logger.error("Failed to get bot info: code=%s msg=%s", code, data.get("msg"))
                return _ApiResult(code=code)
            logger.error("Failed to get bot info: HTTP %s", resp.status_code)
            return _ApiResult(code=resp.status_code)

    async def _get_tenant_token(self) -> _ApiResult:
        """Get tenant access token for APIs not covered by the SDK.

        Returns _ApiResult with code=0 and data=token on success.
        """
        url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                json={"app_id": self._app_id, "app_secret": self._app_secret},
            )
            data = resp.json()
            code = data.get("code", -1)
            if code == 0:
                return _ApiResult(code=0, data=data["tenant_access_token"])
            logger.error("Failed to get tenant token: %s", data.get("msg"))
            return _ApiResult(code=code)

    async def get_user_info(self, open_id: str) -> tuple[Optional[str], Optional[str]]:
        """Get (name, email) by open_id via SDK. Returns (None, None) on failure."""
        if not open_id:
            return None, None
        if open_id in self._user_info_cache:
            return self._user_info_cache[open_id]

        from lark_oapi.api.contact.v3 import GetUserRequest

        try:
            req = GetUserRequest.builder().user_id(open_id).user_id_type("open_id").build()
            resp = await _retry_on_rate_limit(lambda: self._client.contact.v3.user.aget(req))  # type: ignore[union-attr]
            if resp.success() and resp.data and resp.data.user:
                user = resp.data.user
                result = (user.name, user.email)
                self._user_info_cache[open_id] = result
                return result
            logger.warning("Cannot get user info for %s: code=%s msg=%s", open_id, resp.code, resp.msg)
        except Exception:
            logger.warning("Cannot get user info for %s", open_id, exc_info=True)
        return None, None
