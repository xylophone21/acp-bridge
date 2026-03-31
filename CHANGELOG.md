# Changelog

## [0.3.0] - 2026-03-31

### Changed

- Rename package from `src` to `agent_bridge` (flat layout) for PyPI publishing
- Add hatchling build-system config

### Fixed

- Fix e2e test permission callback missing `tool_call` parameter

### Added

- CI workflow (test + lint) on push/PR to main

## [0.2.0] - 2026-03-31

### Fixed

- Fix agent stdio buffer limit too small (64KB default) causing `LimitOverrunError` on large responses, increased to 50MB

## [0.1.0] - 2026-03-30

Initial release with core Feishu-to-ACP agent bridging.

### Features

- ACP-based agent process management (spawn, communication, lifecycle)
- Feishu message event handling and reply
- Automatic session management with LRU/TTL eviction
- Tool call buffering and intermediate output formatting
- Show tool call details in permission request messages
- Credential management via keyring
- Zombie process detection and session cleanup
