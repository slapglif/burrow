# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.1.0]: https://github.com/slapglif/burrow/releases/tag/v0.1.0
