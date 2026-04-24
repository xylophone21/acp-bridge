# ACP Bridge

[![CI](https://img.shields.io/github/actions/workflow/status/xylophone21/acp-bridge/ci.yml?branch=main&label=CI)](https://github.com/xylophone21/acp-bridge/actions/workflows/ci.yml)
[![Version](https://img.shields.io/github/v/release/xylophone21/acp-bridge?label=version)](https://github.com/xylophone21/acp-bridge/releases)
[![License](https://img.shields.io/github/license/xylophone21/acp-bridge)](https://github.com/xylophone21/acp-bridge/blob/main/LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://github.com/xylophone21/acp-bridge)
[![Last Commit](https://img.shields.io/github/last-commit/xylophone21/acp-bridge/main)](https://github.com/xylophone21/acp-bridge/commits/main)

ACP Bridge is a bridge service that connects [Feishu (Lark)](https://www.feishu.cn/) group chats to ACP-compatible AI agents (such as [Kiro CLI](https://kiro.dev/cli/)).

Core features:

- Auto session creation — @mention the bot in group chats, or just send a message in DM
- Message threading via reply chains — reply to any message in a thread to continue the conversation
- LRU + TTL session management — automatic eviction of idle sessions to control resource usage
- Message buffering — messages sent while the agent is busy are queued and delivered in order
- Evaluator quality gate — optional independent agent review of responses before sending to users

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
   default_workspace = "~"          # Agent working directory (cwd)
   attachment_dir = "tmp/attachments" # Where user attachments from Feishu are saved
   output_dir = "tmp/output"        # Where agent output files (images, scripts) are saved
   auto_approve = false             # Auto-approve all tool permission requests
   max_sessions = 10                # Max concurrent agent sessions (LRU eviction)
   session_ttl_minutes = 720        # Idle session timeout
   show_thinking = false            # Forward agent thinking/reasoning to user
   show_intermediate = false        # Forward intermediate tool output to user

   [agent]
   name = "kiro"                    # Agent identifier
   description = "Kiro CLI - https://kiro.dev/cli/"
   # command = "kiro-cli"           # Command to spawn the agent process (default: kiro-cli)
   args = ["acp"]                   # Command arguments
   auto_approve = true              # Auto-approve at agent level

   [[evaluator]]                    # Optional: quality gate for agent responses
   name = "my-evaluator"            # Evaluator identifier
   trigger_pattern = "Conclusion|Result|Finding" # Regex to match agent response, triggers evaluation
   # command = "kiro-cli"           # Inherits from [agent].command if empty
   args = ["acp", "--agent", "my-evaluator"] # Spawns a separate agent process
   # workspace = ""                 # Inherits from bridge.default_workspace if empty
   # auto_approve = true            # Inherits from [agent].auto_approve if not set
   # Prepended to agent text sent to evaluator
   prompt = """Please evaluate the following report.
   End the final text response with a standalone line: RESULT: PASS or RESULT: FAIL.""" 
   pass_pattern = "(?mi)^\\s*RESULT\\s*:\\s*PASS\\s*$" # Regex to match evaluator PASS verdict
   max_retries = 2                  # Retry count on FAIL before sending with warning
   retry_prompt = """The evaluator found issues with your previous response
   Please revise and output the complete response again from the beginning
   (do not only output the changed parts):

   {feedback}"""
   ```

4. Start the service:

   ```bash
   uv run python -m acp_bridge.main run
   ```

> **Tip**: It's recommended to run in tmux so it persists in the background:
> ```bash
> tmux new -s acp-bridge "uv run python -m acp_bridge.main run"
> ```

## Image & File Support

The bridge automatically detects markdown links in agent responses and uploads them to Feishu:

- `![description](path)` — uploaded as image messages
- `[description](path)` — uploaded as file messages

For security, only files under `output_dir` and `attachment_dir` (relative to `default_workspace`) are allowed to be uploaded. Files outside these directories are blocked with a warning.

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
  "prompt": "When you need to create temp files (scripts, debug output, test data, etc.), always save them under bridge/tmp/output/.\nWhen you need to visualize data (trends, comparisons, etc.), always use matplotlib to save charts to bridge/tmp/output/ and reference them as ![description](bridge/tmp/output/xxx.png).\nWhen you need to share generated files (scripts, configs, logs, etc.), save them under bridge/tmp/output/ and reference them as [description](bridge/tmp/output/xxx.sh).\nNever copy external files into the output directory to send them. Only send files you generated yourself."
}
```

The agent generates a chart → saves to `bridge/tmp/output/trend.png` → responds with `![trend](bridge/tmp/output/trend.png)` → bridge uploads the image to Feishu and replaces the markdown with `[pic1]` in the text message.

Similarly, `[deploy script](bridge/tmp/output/deploy.sh)` → bridge uploads the file and replaces the markdown with `[file1]`.

## Evaluator

The bridge supports an optional evaluator quality gate. When configured, agent responses are reviewed by a separate evaluator agent before being sent to the user.

Flow:

1. Agent completes a response
2. Bridge checks `trigger_pattern` against the response text
3. If matched, spawns (or reuses) an evaluator agent session and sends the response for review
4. Evaluator responds with a verdict — if it matches `pass_pattern`, the response is sent to the user
5. If FAIL and retries remain, the evaluator's feedback is sent back to the original agent for revision
6. If the final allowed evaluation still FAILs, the bridge sends that same evaluated response with a warning plus the final evaluator feedback

Evaluator sessions are persistent per main session, so retries share context. The evaluator agent is a separate process with its own system prompt and tools — it can use read-only tools to spot-check evidence in the report.

The bridge applies `pass_pattern` to the evaluator's final text response. Keep the evaluator prompt aligned with that pattern; for example, the config above expects a standalone `RESULT: PASS` line. Verdicts written only in tool calls, task context, plans, or other intermediate state are not matched.

Notification routing: the bridge distinguishes main agent sessions from evaluator sessions via `SessionManager.find_by_session_id()` — main sessions are tracked in the session manager, everything else is routed to the evaluator notification handler.

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
