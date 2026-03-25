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
from typing import Callable, Optional

import httpx
import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    PatchMessageRequest,
    PatchMessageRequestBody,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)

logger = logging.getLogger(__name__)


@dataclass
class FeishuFile:
    file_key: str
    file_name: str
    file_type: str


@dataclass
class FeishuEvent:
    chat_id: str
    message_id: str
    parent_id: Optional[str]
    text: str
    files: list[FeishuFile] = field(default_factory=list)


EventCallback = Callable[[FeishuEvent], None]


class FeishuConnection:
    """Feishu client for WebSocket long-connection and API calls."""

    def __init__(self, app_id: str, app_secret: str):
        self._app_id = app_id
        self._app_secret = app_secret
        self._event_callback: Optional[EventCallback] = None
        self._client = lark.Client.builder().app_id(app_id).app_secret(app_secret).build()
        self._rate_limit = asyncio.Semaphore(1)
        self._rate_limit_interval = 0.8  # seconds between API calls

    def connect(self, callback: EventCallback) -> None:
        """Start WebSocket long-connection. Blocks until disconnected."""
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
            log_level=lark.LogLevel.DEBUG,
        )
        ws_client.start()

    def _on_message_receive(self, data: lark.im.v1.P2ImMessageReceiveV1) -> None:
        """Handle im.message.receive_v1 event from SDK."""
        if self._event_callback is None:
            return

        msg = data.event.message
        chat_id = msg.chat_id
        message_id = msg.message_id
        parent_id = msg.parent_id if msg.parent_id else None

        # Extract text from content JSON
        text = ""
        if msg.content:
            try:
                content = json.loads(msg.content)
                text = content.get("text", "")
            except (json.JSONDecodeError, TypeError):
                pass

        event = FeishuEvent(
            chat_id=chat_id,
            message_id=message_id,
            parent_id=parent_id,
            text=text,
        )
        self._event_callback(event)

    async def _rate_limited(self):
        """Enforce rate limiting between API calls."""
        async with self._rate_limit:
            await asyncio.sleep(self._rate_limit_interval)

    def send_message(
        self, chat_id: str, thread_id: Optional[str], text: str
    ) -> Optional[str]:
        """Send a message. Returns message_id of sent message."""
        if thread_id:
            # Reply in thread
            body = ReplyMessageRequestBody.builder().msg_type("text").content(
                json.dumps({"text": text})
            ).build()
            req = (
                ReplyMessageRequest.builder()
                .message_id(thread_id)
                .request_body(body)
                .build()
            )
            resp = self._client.im.v1.message.reply(req)
        else:
            body = CreateMessageRequestBody.builder().msg_type("text").receive_id(
                chat_id
            ).content(json.dumps({"text": text})).build()
            req = (
                CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(body)
                .build()
            )
            resp = self._client.im.v1.message.create(req)

        if not resp.success():
            logger.error("Failed to send message: %s %s", resp.code, resp.msg)
            return None

        return resp.data.message_id

    def update_message(self, message_id: str, text: str) -> bool:
        """Update an existing message."""
        body = PatchMessageRequestBody.builder().content(
            json.dumps({"text": text})
        ).build()
        req = (
            PatchMessageRequest.builder()
            .message_id(message_id)
            .request_body(body)
            .build()
        )
        resp = self._client.im.v1.message.patch(req)
        if not resp.success():
            logger.error("Failed to update message: %s %s", resp.code, resp.msg)
            return False
        return True

    def download_file(self, message_id: str, file_key: str) -> Optional[bytes]:
        """Download a file from Feishu."""
        token = self._get_tenant_token()
        if not token:
            return None

        url = f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/resources/{file_key}"
        with httpx.Client() as client:
            resp = client.get(
                url,
                params={"type": "file"},
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code == 200:
                return resp.content
            logger.error("Failed to download file: %s", resp.status_code)
            return None

    def upload_file(
        self,
        chat_id: str,
        thread_id: Optional[str],
        content: bytes,
        filename: str,
    ) -> Optional[str]:
        """Upload a file as a message."""
        token = self._get_tenant_token()
        if not token:
            return None

        # Upload file to get file_key
        url = "https://open.feishu.cn/open-apis/im/v1/files"
        with httpx.Client() as client:
            resp = client.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                data={"file_type": "stream", "file_name": filename},
                files={"file": (filename, content)},
            )
            if resp.status_code != 200:
                logger.error("Failed to upload file: %s", resp.status_code)
                return None
            data = resp.json()
            if data.get("code") != 0:
                logger.error("Failed to upload file: %s", data.get("msg"))
                return None
            file_key = data["data"]["file_key"]

        # Send file message
        msg_content = json.dumps({"file_key": file_key})
        if thread_id:
            body = ReplyMessageRequestBody.builder().msg_type("file").content(
                msg_content
            ).build()
            req = (
                ReplyMessageRequest.builder()
                .message_id(thread_id)
                .request_body(body)
                .build()
            )
            resp = self._client.im.v1.message.reply(req)
        else:
            body = CreateMessageRequestBody.builder().msg_type("file").receive_id(
                chat_id
            ).content(msg_content).build()
            req = (
                CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(body)
                .build()
            )
            resp = self._client.im.v1.message.create(req)

        if not resp.success():
            logger.error("Failed to send file message: %s %s", resp.code, resp.msg)
            return None
        return resp.data.message_id

    def _get_tenant_token(self) -> Optional[str]:
        """Get tenant access token."""
        url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
        with httpx.Client() as client:
            resp = client.post(
                url,
                json={"app_id": self._app_id, "app_secret": self._app_secret},
            )
            data = resp.json()
            if data.get("code") == 0:
                return data["tenant_access_token"]
            logger.error("Failed to get tenant token: %s", data.get("msg"))
            return None
