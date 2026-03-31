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
