# AgentBridge Testing

## Automated Tests

```bash
# Unit tests (170)
uv run pytest tests/

# E2E tests (26) — requires kiro-cli logged in
uv run pytest tests_e2e/

# Feishu integration tests (16-step guided) — requires bridge running
uv run python -m scripts.test_feishu_integration

# Lint + type check
uv run ruff check agent_bridge/ tests/ && uv run pyright agent_bridge/
```

## Smoke Tests (manual, verify in Feishu after each release)

### Basic Conversation

| # | Action | Expected |
|---|---|---|
| 1 | In group, send `@bot hello` | Typing appears immediately → reply received → Typing disappears |
| 2 | Reply to bot's response with `what did I just say` | Has context, recalls previous message |
| 3 | In group, send `test` without @bot | No response |
| 4 | DM the bot with `hello` | Replies without needing @ |
| 5 | In group, send `@bot @someone hello` | Normal reply, unaffected by other mentions |

### Commands

| # | Action | Expected |
|---|---|---|
| 6 | In group, send `@bot #help` | Returns command list |
| 7 | In group, send `@bot #sessions` | Shows session list |
| 8 | In a conversation thread, reply `#session` | Shows current session details (status/summary/last active) |
| 9 | In a conversation thread, reply `#end` | "Session ended." + DONE reaction on trigger message |
| 10 | Send `@bot write a 500-word essay`, while busy reply `#cancel` | "Operation cancelled." |
| 11 | In a conversation thread, reply `#mode` | Shows available modes with current marked |
| 12 | In a conversation thread, reply `#mode kiro_default` | "Mode switched to: kiro_default" |
| 13 | In a conversation thread, reply `#read /etc/hostname` | Returns file content |
| 14 | In a conversation thread, reply `#diff` | Shows git diff or "No changes to show." |

### Session Lifecycle

| # | Action | Expected |
|---|---|---|
| 15 | `#end` to close session, then reply `hello` in same thread | New session created automatically, normal reply |
| 16 | Send `@bot write a 500-word essay`, while busy reply `also mention spring` | 2nd message buffered, sent automatically after 1st completes |
| 17 | Create a session and wait for TTL to expire (default 60 min) | Session auto-cleaned, DONE reaction added |

### Error Handling

| # | Action | Expected |
|---|---|---|
| 18 | Send `@bot hello`, then run `pkill -f "kiro-cli-chat acp"`, send `@bot #sessions` | Shows ⚠️ zombie marker |
| 19 | Reply `are you there` in the same thread | Error message received; send new `@bot hello` to auto-recover |

### Permission (set `auto_approve = false` in bridge.toml first)

| # | Action | Expected |
|---|---|---|
| 20 | Send `@bot create /tmp/test.txt`, when bot asks permission reply `1` | ✅ Approved, file created |
| 21 | Send `@bot create /tmp/test2.txt`, when bot asks permission reply `deny` | ❌ Permission denied, file not created |
