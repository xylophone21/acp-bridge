# ACP Bridge

ACP Bridge is a bridge service that connects [Feishu (Lark)](https://www.feishu.cn/) group chats to ACP-compatible AI agents (such as [Kiro CLI](https://kiro.dev/cli/)).

Core features:

- Auto session creation — @mention the bot in group chats, or just send a message in DM
- Message threading via reply chains — reply to any message in a thread to continue the conversation
- LRU + TTL session management — automatic eviction of idle sessions to control resource usage
- Message buffering — messages sent while the agent is busy are queued and delivered in order

## Installation

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager
- [Kiro CLI](https://kiro.dev/cli/) — the default ACP agent

## Feishu App Setup

1. Go to the [Feishu Open Platform](https://open.feishu.cn/app) and create a new app.
2. Under **Credentials**, copy the App ID and App Secret.
3. Under **Event Subscriptions**, enable **WebSocket** (long connection) mode, then add the `im.message.receive_v1` event.
4. Under **Permissions**, enable:
   - `im:message` — send and update messages
   - `im:message:send_as_bot` — send messages as bot
   - `im:message.group_at_msg:readonly` — receive group chat messages with @mention
   - `im:message.group_msg` — receive all group messages (required for reply-chain routing)
   - `im:message.p2p_msg:readonly` — receive P2P messages
   - `im:resource` — download file resources from messages
   - `im:chat:readonly` — read chat info
   - `contact:contact.base:readonly` — get user name (optional, for identifying sender in prompts)
   - `contact:user.email:readonly` — get user email (optional, for matching accounts in external systems)

   For contact permissions to work, ensure the **Data Permission** (数据权限) contact scope includes all bot users.

### Steps

1. Install dependencies:

   ```bash
   uv sync
   ```

2. Generate a config file:

   ```bash
   uv run python -m acp_bridge.main init
   ```

3. Edit `bridge.toml` with your Feishu app credentials:

   ```toml
   [feishu]
   app_id = "your_app_id"
   app_secret = "your_app_secret"

   [bridge]
   default_workspace = "~"
   attachment_dir = "tmp/attachments"
   output_dir = "tmp/output"
   auto_approve = false
   max_sessions = 10
   session_ttl_minutes = 60
   show_thinking = false
   show_intermediate = false

   [agent]
   name = "kiro"
   description = "Kiro CLI - https://kiro.dev/cli/"
   command = "kiro-cli"
   args = ["acp"]
   auto_approve = false
   ```

4. Start the service:

   ```bash
   uv run python -m acp_bridge.main run
   ```

> **Tip**: It's recommended to run in tmux so it persists in the background:
> ```bash
> tmux new -s acp-bridge "uv run python -m acp_bridge.main run"
> ```

## Image Support

The bridge automatically detects markdown image references (`![description](path)`) in agent responses and uploads them to Feishu as image messages.

For security, only images under `output_dir` and `attachment_dir` (relative to `default_workspace`) are allowed to be uploaded. Images outside these directories are blocked with a warning.

To enable this, configure your ACP client's system prompt to instruct the agent to save images to the `output_dir` and reference them in markdown format. Each client has its own system prompt mechanism:

- **Kiro CLI**: `.kiro/agents/<name>.json` — `prompt` field
- **Claude Code**: `CLAUDE.md`
- **Cursor**: `.cursorrules`

### Example

Given `bridge.toml`:

```toml
[bridge]
default_workspace = "~/code/ops-copilot"
attachment_dir = "bridge/tmp/attachments"
output_dir = "bridge/tmp/output"
```

For Kiro CLI, add to `.kiro/agents/cli.json`:

```json
{
  "prompt": "When you need to create temp files (scripts, debug output, test data, etc.), always save them under bridge/tmp/output/.\nWhen you need to visualize data (trends, comparisons, etc.), always use matplotlib to save charts to bridge/tmp/output/ and reference them as ![description](bridge/tmp/output/xxx.png).\nNever copy external files into the output directory to send them. Only send files you generated yourself."
}
```

The agent generates a chart → saves to `bridge/tmp/output/trend.png` → responds with `![trend](bridge/tmp/output/trend.png)` → bridge uploads the image to Feishu and replaces the markdown with `[pic1]` in the text message.

## Commands

| Command | Description |
|---------|-------------|
| `#mode` | Show available modes and current mode |
| `#mode <value>` | Switch to a different mode |
| `#model` | Show available models and current model |
| `#model <value>` | Switch to a different model |
| `#cancel` | Cancel ongoing agent operation |
| `#end` | End current agent session |
| `#read <file_path>` | Read local file content |
| `#diff [args]` | Show git diff |
| `#session` | Show current agent session info |
| `#sessions` | Show all active sessions |
| `#help` | Show help message |

## License

MIT License. See [LICENSE](LICENSE) for details.

This project is derived from [Juan](https://github.com/DiscreteTom/juan) by DiscreteTom.
