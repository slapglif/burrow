# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.1] - 2026-03-06

### Fixed
- macOS x64 runner uses `macos-13` (last Intel runner) instead of ARM64
- `asyncio.get_event_loop()` replaced with `get_running_loop()` (deprecation fix)
- `writer.close()` now awaits `wait_closed()` for proper tunnel cleanup
- Shadowed `raw` variable renamed to `peer_list` in peers handler
- Removed unused `import sys` and `import os`
- Transfers and tunnels cleaned up on disconnect (memory leak fix)
- Duplicate name check uses `any()` instead of building a list

### Added
- MIT LICENSE file
- `license = "MIT"` in pyproject.toml
- `fail-fast: false` in CI matrix
- Version bump workflow (manual dispatch)
- 9 server integration tests (55 total)

## [0.1.0] - 2026-03-06

### Added
- Central registry server with WebSocket relay
- Peer client library with async connect/listen
- Interactive CLI with /peers, /msg, /send, /tunnel commands
- JSON-over-WebSocket protocol with 15 message types
- Peer discovery via registry (automatic join/leave notifications)
- P2P text messaging through relay
- Chunked file transfer with base64 encoding (512KB chunks)
- TCP port tunneling through WebSocket relay
- Name resolution (by ID or case-insensitive name)
- Protocol unit tests (46 passing)

[0.1.1]: https://github.com/slapglif/burrow/releases/tag/v0.1.1
[0.1.0]: https://github.com/slapglif/burrow/releases/tag/v0.1.0
