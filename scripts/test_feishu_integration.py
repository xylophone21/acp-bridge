#!/usr/bin/env python3 -u
"""
Interactive integration test for FeishuConnection.

Starts the WebSocket listener and guides you through each test step.
Follow the prompts — send messages in Feishu, and the script will
verify the event and exercise the API methods automatically.

Usage:
    uv run python -m scripts.test_feishu_integration
"""

import asyncio
import functools
import os

from acp_bridge.config import Config
from acp_bridge.feishu import FeishuConnection, FeishuEvent

# Force unbuffered output
print = functools.partial(print, flush=True)

CONFIG_PATH = os.environ.get("BRIDGE_CONFIG", "bridge.toml")


def _print_event(event: FeishuEvent):
    print(f"  conversation_id : {event.conversation_id}")
    print(f"  message_id      : {event.message_id}")
    print(f"  parent_id       : {event.parent_id}")
    print(f"  root_id         : {event.root_id}")
    print(f"  text            : {event.text!r}")
    print(f"  is_mention_bot  : {event.is_mention_bot}")
    print(f"  sender_id       : {event.sender_id}")
    print(f"  chat_type       : {event.chat_type}")
    print(f"  files           : {event.files}")


def _step(n: int, title: str):
    print(f"\n{'=' * 60}")
    print(f"Step {n}: {title}")
    print("=" * 60)


async def main():
    config = Config.load(CONFIG_PATH)
    conn = FeishuConnection(config.feishu.app_id, config.feishu.app_secret)

    print("Starting WebSocket connection...")
    await conn.init()
    print(f"  bot_open_id: {conn._bot_open_id}")

    event_queue: asyncio.Queue[FeishuEvent] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def on_event(event: FeishuEvent):
        print(f"  [EVENT] {event.message_id} text={event.text!r}")
        loop.call_soon_threadsafe(event_queue.put_nowait, event)

    connect_future = loop.run_in_executor(None, conn.connect, on_event)
    # Wait for SDK to establish WebSocket, or fail fast if connect() returns/raises
    done, _ = await asyncio.wait({connect_future}, timeout=5)
    if done:
        # connect() returned or raised — means connection failed
        connect_future.result()  # raises the exception if any
        raise RuntimeError("connect() returned unexpectedly")
    print("✅ Connected!\n")

    # Drain any stale events from previous connections
    drained = 0
    while not event_queue.empty():
        event_queue.get_nowait()
        drained += 1
    if drained:
        print(f"   (drained {drained} stale events)")
    print()

    async def wait_event() -> FeishuEvent:
        return await event_queue.get()

    # ================================================================
    # GROUP TESTS
    # ================================================================

    # ── Step 1: Group @mention (new thread root) ────────────────
    _step(1, "Group @mention")
    print("👉 In a GROUP chat, send: @bot 1")

    event = await wait_event()
    _print_event(event)
    assert event.chat_type == "group", f"Expected group, got {event.chat_type!r}"
    assert event.is_mention_bot, "Expected is_mention_bot=True"
    assert event.parent_id is None, "Expected no parent_id (new message)"
    step1_root_id = event.root_id
    group_chat_id = event.conversation_id
    print("✅ Verified")

    bot_reply_id = await conn.send_message(group_chat_id, event.message_id, "✅ 1")
    assert bot_reply_id, "send_message failed"
    print(f"📤 Bot replied '✅ 1' (message_id: {bot_reply_id})")

    # ── Step 2: Group without @mention (independent message) ────
    _step(2, "Group message without @mention")
    print("👉 In the SAME group, send a NEW message:")
    print("   ⚠️  Do NOT @ the bot")
    print("   ⚠️  Do NOT reply to any message")
    print("   Just type: 2")

    event = await wait_event()
    _print_event(event)
    assert event.chat_type == "group", f"Expected group, got {event.chat_type!r}"
    assert not event.is_mention_bot, "Expected is_mention_bot=False"
    assert event.parent_id is None, "Expected no parent_id"
    assert event.root_id != step1_root_id, "Expected different root_id (independent message)"
    print("✅ Verified")

    # ── Step 3: Reply to bot's message ──────────────────────────
    _step(3, "Group reply to bot's message")
    print("👉 REPLY to the bot's '✅ 1' message, send: 3")
    print("   ⚠️  If Feishu auto-adds @bot, DELETE it before sending")

    event = await wait_event()
    _print_event(event)
    assert event.chat_type == "group", f"Expected group, got {event.chat_type!r}"
    assert event.parent_id is not None, "Expected parent_id"
    assert not event.is_mention_bot, "Expected is_mention_bot=False"
    assert event.root_id == step1_root_id, \
        f"Expected root_id={step1_root_id}, got {event.root_id} (should be same thread as Step 1)"
    print("✅ Verified (same thread as Step 1)")

    # ── Step 4: Reply to your own Step 1 message ────────────────
    _step(4, "Group reply to your own message")
    print("👉 REPLY to YOUR OWN '@bot 1' message (Step 1), send: 4")

    event = await wait_event()
    _print_event(event)
    assert event.chat_type == "group", f"Expected group, got {event.chat_type!r}"
    assert event.parent_id is not None, "Expected parent_id"
    assert event.root_id == step1_root_id, \
        f"Expected root_id={step1_root_id}, got {event.root_id} (should be same thread as Step 1)"
    print("✅ Verified (same thread as Step 1)")

    # ── Step 5: Reply to Step 3 (deeper in thread) ──────────────
    _step(5, "Group deeper thread reply")
    print("👉 REPLY to YOUR Step 3 message ('3'), send: 5")

    event = await wait_event()
    _print_event(event)
    assert event.chat_type == "group", f"Expected group, got {event.chat_type!r}"
    assert event.parent_id is not None, "Expected parent_id"
    assert event.root_id == step1_root_id, \
        f"Expected root_id={step1_root_id}, got {event.root_id} (deeper reply, same thread)"
    print("✅ Verified (same thread as Step 1, deeper reply)")

    # ── Step 6: Group reaction (auto) ───────────────────────────
    _step(6, "Group reaction (auto)")
    ok = await conn.add_reaction(bot_reply_id, "OK")
    assert ok, "add_reaction failed"
    print("📤 Check Feishu: '✅ 1' message now has an 👌 reaction")
    print("✅ Verified")

    # ── Step 7: Group file upload (auto) ────────────────────────
    _step(7, "Group file upload (auto)")
    test_content = b"group integration test file content"
    group_file_msg_id = await conn.upload_file(group_chat_id, bot_reply_id, test_content, "group_test.txt")
    assert group_file_msg_id, "upload_file failed"
    print("📤 Check Feishu: 'group_test.txt' appeared as a reply to '✅ 1' in group")
    print("✅ Verified")

    # ── Step 8: Group file download ─────────────────────────────
    _step(8, "Group file download")
    print("👉 In the SAME group, send a file (any small file)")

    event = await wait_event()
    _print_event(event)
    assert event.chat_type == "group", f"Expected group, got {event.chat_type!r}"
    if event.files:
        f = event.files[0]
        print(f"  Downloading: {f.file_name}")
        data = await conn._download_file(event.message_id, f.file_key)
        assert data is not None, "download_file failed"
        print(f"  Downloaded {len(data)} bytes")
        print("✅ Verified")
    else:
        print("⚠️  No files detected. Check _on_message_receive: file content not parsed.")
        print(f"     Raw event text: {event.text!r}")

    # ================================================================
    # DM TESTS
    # ================================================================

    # ── Step 9: DM text (new thread root) ──────────────────────
    _step(9, "DM text")
    print("👉 Open a DM with the bot, send: 9")

    event = await wait_event()
    _print_event(event)
    assert event.chat_type == "p2p", f"Expected p2p, got {event.chat_type!r}"
    assert event.parent_id is None, "Expected no parent_id"
    step9_root_id = event.root_id
    dm_chat_id = event.conversation_id
    print("✅ Verified")

    dm_bot_msg_id = await conn.send_message(dm_chat_id, event.message_id, "✅ 9")
    assert dm_bot_msg_id, "send_message failed"
    print("📤 Bot replied '✅ 9'")

    # ── Step 10: Reply to bot's DM message ──────────────────────
    _step(10, "DM reply to bot's message")
    print("👉 REPLY to the bot's '✅ 9' message, send: 10")

    event = await wait_event()
    _print_event(event)
    assert event.chat_type == "p2p", f"Expected p2p, got {event.chat_type!r}"
    assert event.parent_id is not None, "Expected parent_id"
    assert event.root_id == step9_root_id, \
        f"Expected root_id={step9_root_id}, got {event.root_id} (should be same thread as Step 9)"
    print("✅ Verified (same thread as Step 9)")

    # ── Step 11: Reply to your own Step 9 message ───────────────
    _step(11, "DM reply to your own message")
    print("👉 REPLY to YOUR OWN '9' message (Step 9), send: 11")

    event = await wait_event()
    _print_event(event)
    assert event.chat_type == "p2p", f"Expected p2p, got {event.chat_type!r}"
    assert event.parent_id is not None, "Expected parent_id"
    assert event.root_id == step9_root_id, \
        f"Expected root_id={step9_root_id}, got {event.root_id} (should be same thread as Step 9)"
    print("✅ Verified (same thread as Step 9)")

    # ── Step 12: Reply to Step 10 (deeper in DM thread) ─────────
    _step(12, "DM deeper thread reply")
    print("👉 REPLY to YOUR Step 10 message ('10'), send: 12")

    event = await wait_event()
    _print_event(event)
    assert event.chat_type == "p2p", f"Expected p2p, got {event.chat_type!r}"
    assert event.parent_id is not None, "Expected parent_id"
    assert event.root_id == step9_root_id, \
        f"Expected root_id={step9_root_id}, got {event.root_id} (deeper reply, same thread)"
    print("✅ Verified (same thread as Step 9, deeper reply)")

    # ── Step 13: New DM message (different thread) ──────────────
    _step(13, "DM new message (different thread)")
    print("👉 Send a NEW message in DM (don't reply to anything): 13")

    event = await wait_event()
    _print_event(event)
    assert event.chat_type == "p2p", f"Expected p2p, got {event.chat_type!r}"
    assert event.parent_id is None, "Expected no parent_id"
    assert event.root_id != step9_root_id, "Expected different root_id (new thread)"
    print("✅ Verified (different thread from Step 9)")

    # ── Step 14: DM reaction (auto) ─────────────────────────────
    _step(14, "DM reaction (auto)")
    ok = await conn.add_reaction(dm_bot_msg_id, "OK")
    assert ok, "add_reaction failed"
    print("📤 Check Feishu: '✅ 9' message now has an 👌 reaction")
    print("✅ Verified")

    # ── Step 15: DM file upload (auto) ──────────────────────────
    _step(15, "DM file upload (auto)")
    test_content = b"integration test file content"
    file_msg_id = await conn.upload_file(dm_chat_id, dm_bot_msg_id, test_content, "test.txt")
    assert file_msg_id, "upload_file failed"
    print("📤 Check Feishu: 'test.txt' appeared as a reply to '✅ 9' in DM")
    print("✅ Verified")

    # ── Step 16: DM file download ───────────────────────────────
    _step(16, "DM file download")
    print("👉 Send a file (any small file) to the bot in DM")

    event = await wait_event()
    _print_event(event)
    assert event.chat_type == "p2p", f"Expected p2p, got {event.chat_type!r}"
    if event.files:
        f = event.files[0]
        print(f"  Downloading: {f.file_name}")
        data = await conn._download_file(event.message_id, f.file_key)
        assert data is not None, "download_file failed"
        print(f"  Downloaded {len(data)} bytes")
        print("✅ Verified")
    else:
        print("⚠️  No files detected. Check _on_message_receive: file content not parsed.")
        print(f"     Raw event text: {event.text!r}")

    # ── Done ────────────────────────────────────────────────────
    print(f"\n{'=' * 60}")
    print("🎉 All 16 steps completed!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
