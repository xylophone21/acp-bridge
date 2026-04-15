# CHANGELOG


## v1.3.2 (2026-04-15)

### Bug Fixes

- Messages without @bot dropped during session creation
  ([`1ad8827`](https://github.com/xylophone21/acp-bridge/commit/1ad88276afff0322625ee899e922ad7d8d396c52))

- Preserve agent text when prompt ends with plan tool calls
  ([`633bca3`](https://github.com/xylophone21/acp-bridge/commit/633bca3449743e274ac0479a25a474468a26d930))

- Reply to trigger message instead of root message
  ([`fffd6dc`](https://github.com/xylophone21/acp-bridge/commit/fffd6dc602dc7ad8d6186c8e0437b4736325014e))

### Refactoring

- Improve logging readability and reduce truncation
  ([`9afa779`](https://github.com/xylophone21/acp-bridge/commit/9afa77906e4a16967e92a869f310a24b97d4d9b2))


## v1.3.1 (2026-04-08)

### Bug Fixes

- Buffered message sender shows open_id instead of username
  ([`537dd21`](https://github.com/xylophone21/acp-bridge/commit/537dd21fb4aa76f838e5ab54e5602ef1d8ceff60))

- Config startup error on unknown toml fields
  ([`b2ed832`](https://github.com/xylophone21/acp-bridge/commit/b2ed832e14a7951e066f1d33424816bf0d846834))

- Quoted message text lost when replying with attachment reference
  ([`0f14c73`](https://github.com/xylophone21/acp-bridge/commit/0f14c7392fa58855192a1eea65a73b318a9983b7))

### Documentation

- Add status badges to README
  ([`2ab7b57`](https://github.com/xylophone21/acp-bridge/commit/2ab7b572aa6acb05732618bab3f6ea5d752bc7dc))

- Update Image & File Support section, remove unimplemented allowed_users
  ([`742e939`](https://github.com/xylophone21/acp-bridge/commit/742e939a588abd6418c9ce686b9ea1a8aa9b0af9))


## v1.3.0 (2026-04-03)

### Documentation

- Add config field descriptions in README
  ([`32bb95c`](https://github.com/xylophone21/acp-bridge/commit/32bb95c2072874c9a66b9b8e02077c67792bb75a))

### Features

- Add file upload support and unify image/file link handling
  ([`da436d9`](https://github.com/xylophone21/acp-bridge/commit/da436d914f7dd4c5ac093faa2b6f6fe94d876683))

- Add output_dir config and image upload path validation
  ([`649aa96`](https://github.com/xylophone21/acp-bridge/commit/649aa961c9837b2d7cf46432a81a537ac88c5549))


## v1.2.1 (2026-04-03)

### Bug Fixes

- Correct image extension by magic bytes
  ([`84bb526`](https://github.com/xylophone21/acp-bridge/commit/84bb5262340802727135723cdc4ad46b90d55f85))


## v1.2.0 (2026-04-02)

### Bug Fixes

- Graceful shutdown via signal handlers
  ([`2b91deb`](https://github.com/xylophone21/acp-bridge/commit/2b91deb2df2bdd34b56aedb17eac981697e9f60e))

- Register SIGTERM/SIGINT handlers with asyncio event loop - Replace unreliable except
  CancelledError/KeyboardInterrupt with cooperative shutdown via asyncio.Event - Unify duplicate
  shutdown paths into single finally block - Add 5s timeout per agent end_session to prevent hanging
  - Keep os._exit(0) as feishu SDK ws_client.start() blocks a thread forever with no stop API

### Documentation

- Add kiro-cli prerequisite and attachment_dir to README
  ([`c1f9a7b`](https://github.com/xylophone21/acp-bridge/commit/c1f9a7be37302c5fd6e163be42dc0d39259fb756))

### Features

- Ignore messages that @others but not @bot in reply chain
  ([`5d53bde`](https://github.com/xylophone21/acp-bridge/commit/5d53bde663d0f7f8eb47a8c7b2bb03f63a873b7d))

- Support image, file, and rich-text attachments with quoted parent message
  ([`3cc6fda`](https://github.com/xylophone21/acp-bridge/commit/3cc6fda1a6769bb8e1f0aff8be2fde8287316196))


## v1.1.0 (2026-03-31)

### Documentation

- Add data permission scope note for contact API
  ([`641b722`](https://github.com/xylophone21/acp-bridge/commit/641b72225ff57afd3d2edc7bc436fa4a1906613b))

### Features

- Add --log-dir for built-in daily log rotation
  ([`c0ba3af`](https://github.com/xylophone21/acp-bridge/commit/c0ba3af2fecaf7329a8a2e46329898b1c5b83a27))

- Auto-detect markdown image refs and send as Feishu image messages
  ([`a55f75d`](https://github.com/xylophone21/acp-bridge/commit/a55f75dbdf2629d57d4226785e3e1f2145895678))

- Add send_image method to FeishuConnection (upload via im.v1.image.create) - Detect ![alt](path) in
  agent text, upload and send as image message - Resolve relative paths using
  config.bridge.default_workspace - Add matplotlib dependency for chart generation

- Resolve sender identity from Feishu and inject into prompt
  ([`f4ca481`](https://github.com/xylophone21/acp-bridge/commit/f4ca481c289313b6536631be13f52c95ef25fd3a))


## v1.0.0 (2026-03-31)

### Continuous Integration

- Add semantic-release with manual trigger and PyPI publish
  ([`b75cb91`](https://github.com/xylophone21/acp-bridge/commit/b75cb91200fe08790e13678d6c7845b01a277cf7))

- Add test and lint workflow
  ([`b0f1481`](https://github.com/xylophone21/acp-bridge/commit/b0f148129610dd481e43884150d788cb8936d286))

### Features

- Rename package from src to agent_bridge for PyPI publishing
  ([`fa00198`](https://github.com/xylophone21/acp-bridge/commit/fa001983280b848cc39faaa220e8875dce511505))

- Restructure to flat layout (src/*.py -> agent_bridge/*.py) - Add build-system config (hatchling)
  to pyproject.toml - Fix stdio buffer limit (64KB -> 50MB) preventing LimitOverrunError - Fix e2e
  test permission callback missing tool_call parameter - Add CHANGELOG.md - Update all imports,
  docs, and test commands

BREAKING CHANGE: package renamed from src to agent_bridge. Run command changed from 'python -m
  src.main' to 'python -m agent_bridge.main'.

- Rename package to acp-bridge and unify import name to acp_bridge
  ([`df9ed6f`](https://github.com/xylophone21/acp-bridge/commit/df9ed6f3961956afcce2d3502d4069ce9dd1d4ae))


## v0.2.0 (2026-03-31)

### Features

- **agent**: Increase agent process transport buffer limit to 50MB
  ([`3911b20`](https://github.com/xylophone21/acp-bridge/commit/3911b206e8778335f16ffa82926396f480683529))


## v0.1.0 (2026-03-30)

### Bug Fixes

- **agent**: Add resilience to agent process failures and session cleanup
  ([`7ade9c6`](https://github.com/xylophone21/acp-bridge/commit/7ade9c6e6bf4fb14ee12994c681e30d64028027a))

- **agent**: 修复自实现的acp协议不工作的问题,替换为开源版本
  ([`67edbcc`](https://github.com/xylophone21/acp-bridge/commit/67edbcc35b66643f861aaa176e205868d1e6cc0b))

- Add ruff linter configuration with E, F, W, I rule sets

- **agent,bridge**: Improve session lifecycle and zombie process detection
  ([`47a08e2`](https://github.com/xylophone21/acp-bridge/commit/47a08e26dac44e5c01a72489d996c90c9beee1b7))

- **bridge**: Improve debug logging with emoji indicators and concise formatting
  ([`4d9f8ac`](https://github.com/xylophone21/acp-bridge/commit/4d9f8aca3afd8d56c82272df268b514f42e45935))

- **handler**: Improve error handling and reaction logic in session end
  ([`2c2e5c4`](https://github.com/xylophone21/acp-bridge/commit/2c2e5c4b3aaa2bffc360c800cd8d95d1e6c881f1))

- **handler**: Remove duplicate reaction on bot message at session end
  ([`b1938aa`](https://github.com/xylophone21/acp-bridge/commit/b1938aaecce726d0c23985463a6686a44972b820))

- **logging**: Add debug logging for session flush and prompt operations
  ([`cb8ac66`](https://github.com/xylophone21/acp-bridge/commit/cb8ac6644b029274cb7a94eb201b28e25b05faa4))

- **logging**: Adjust log levels and improve event tracing for better observability
  ([`4e8cbba`](https://github.com/xylophone21/acp-bridge/commit/4e8cbbadf3438a1819d1d23ba3b1d89bce6c185b))

### Chores

- **dev**: Add type checking and improve test configuration
  ([`62a1f74`](https://github.com/xylophone21/acp-bridge/commit/62a1f74bb8914e56cd8187b42e949be2f391955b))

### Documentation

- Update README with improved project description
  ([`bf505ce`](https://github.com/xylophone21/acp-bridge/commit/bf505ce0fd85e02ec9d1a600b8018f88eb8f9744))

- **testing**: Add comprehensive testing guide for AgentBridge
  ([`117e46b`](https://github.com/xylophone21/acp-bridge/commit/117e46b79f87b4aa7a6a48ad99f9296d638b7420))

### Features

- Show tool details in permission request messages
  ([`2aa8747`](https://github.com/xylophone21/acp-bridge/commit/2aa87477f847c6f868fd788fa792c38f86fcb4ca))

- Show tool title and raw_input in Feishu permission prompts - Pass tool_call from ACP permission
  callback to on_permission handler - Also: spawn agent process with cwd=workspace, log session mode
  on creation

- **bridge**: Add credential management and improve debug logging
  ([`e4b4cf1`](https://github.com/xylophone21/acp-bridge/commit/e4b4cf1e52cfb31f8cc461ac4411a76df0b1c02b))

- Add keyring ,keyrings-alt and pyyaml dependencies for secure credential storage

- **bridge**: Add tool call buffering and improved intermediate output formatting
  ([`8f07b7a`](https://github.com/xylophone21/acp-bridge/commit/8f07b7ad0e7c670a9cd68c2f900479183fe3f3c0))

- **project**: Initialize Python project structure with agent bridge implementation(AI code, not
  reviewed yet)
  ([`c4fa5a9`](https://github.com/xylophone21/acp-bridge/commit/c4fa5a95663e268b6b01d3a72a143ef4d9d66972))

- **session-refactor**: Implement automatic session management with LRU/TTL eviction ( reviewed but
  not tested)
  ([`6173a12`](https://github.com/xylophone21/acp-bridge/commit/6173a1241c3681fd616101cca729e9eaa17dd5fb))

### Testing

- **e2e**: Add end-to-end tests for agent bridge with Feishu mocking
  ([`2619d4f`](https://github.com/xylophone21/acp-bridge/commit/2619d4f4e055c8cc5556bae5d6f99c45b1825293))
