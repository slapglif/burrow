# burrow

> Zero-config P2P relay for connecting agents and devices

[![CI](https://github.com/slapglif/burrow/actions/workflows/ci.yml/badge.svg)](https://github.com/slapglif/burrow/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/slapglif/burrow)](https://github.com/slapglif/burrow/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

## What is Burrow?

Burrow is a zero-config P2P relay that lets agents and devices discover each other, exchange messages, transfer files, and tunnel TCP ports -- all through a central WebSocket registry. No direct connections, no port forwarding, no NAT traversal headaches. It works as a standalone CLI tool, a Python library, or as a Claude Code plugin that gives your agent full networking capabilities via MCP tools.

## Quick Start

### Bootstrap script (easiest)

```bash
curl -fsSL https://raw.githubusercontent.com/slapglif/burrow/master/bootstrap.sh | bash
```

This installs the binary, starts a local registry, and drops you into an interactive peer session.

### Binary download

Grab a pre-built binary for your platform from [Releases](https://github.com/slapglif/burrow/releases):

| Platform       | Binary              |
|----------------|---------------------|
| Linux x64      | `burrow-linux-x64`  |
| macOS ARM64    | `burrow-macos-arm64`|
| Windows x64    | `burrow-win-x64.exe`|

### From source

```bash
uv pip install git+https://github.com/slapglif/burrow.git
```

## Standalone Usage

### Start a registry server

```bash
burrow serve --port 7654
```

The registry listens on `0.0.0.0:7654` by default.

### Connect as a peer

```bash
burrow connect ws://registry-host:7654 --name my-laptop
```

If `--name` is omitted, the system hostname is used.

### Interactive commands

| Command | Description |
|---------|-------------|
| `/peers` | List connected peers |
| `/msg <peer> <message>` | Send a text message |
| `/send <peer> <filepath>` | Send a file |
| `/tunnel <peer> <lport>:<rport>` | Forward a TCP port |
| `/help` | Show help |
| `/quit` | Disconnect |

Peers can be referenced by name (case-insensitive) or by ID.

## Claude Code Plugin

Burrow ships as a Claude Code plugin. Install it to give your agent P2P networking tools, skills, hooks, and a dedicated sub-agent.

### Installation

```bash
claude plugin install slapglif/burrow
```

### MCP Tools

The plugin exposes 7 MCP tools through its built-in server:

| Tool | Description |
|------|-------------|
| `burrow_serve` | Start a registry server on a given port |
| `burrow_connect` | Connect to a registry as a named peer |
| `burrow_list_peers` | List all peers connected to the registry |
| `burrow_send_message` | Send a text message to a peer |
| `burrow_send_file` | Transfer a file to a peer |
| `burrow_open_tunnel` | Open a TCP port tunnel through the relay |
| `burrow_disconnect` | Disconnect from the registry |

### Skills

| Skill | Description |
|-------|-------------|
| `connect` | Guided workflow to connect to a registry |
| `swarm-status` | Show peer connectivity and tunnel status |

### Agent

The plugin registers a **burrow-agent** (cyan-colored, sonnet model) that can autonomously manage peer connections, relay messages, and coordinate multi-agent swarms over the network.

### Hooks

| Hook | Trigger | Purpose |
|------|---------|---------|
| SessionStart | Session begins | Injects capability awareness so the agent knows burrow is available |
| PreToolUse | `burrow_open_tunnel` called | Tunnel safety check -- validates port ranges and confirms intent before opening tunnels |

## Programmatic Usage

Use burrow directly from Python:

```python
import asyncio
from burrow.peer import Peer

async def main():
    peer = Peer("ws://localhost:7654", name="my-agent")
    await peer.connect()

    # List other connected peers
    peers = await peer.list_peers()
    print(peers)

    # Send a message
    await peer.send_message("other-peer", "hello from Python")

    # Transfer a file
    await peer.send_file("other-peer", "/path/to/data.csv")

    # Open a tunnel (local:8080 -> remote:3000)
    await peer.open_tunnel("other-peer", 8080, 3000)

    await peer.disconnect()

asyncio.run(main())
```

## Protocol

All messages are JSON objects sent over a WebSocket connection. Every message contains a `type` field.

| Type | Direction | Description |
|------|-----------|-------------|
| `register` | peer -> registry | Register with a display name |
| `registered` | registry -> peer | Confirm registration, assign peer ID |
| `peers` | both | Request/response: list connected peers |
| `peer_joined` | registry -> peer | Notification: a new peer connected |
| `peer_left` | registry -> peer | Notification: a peer disconnected |
| `msg` | peer -> peer | Text message (relayed through registry) |
| `file_start` | peer -> peer | Begin a file transfer (name, size, ID) |
| `file_chunk` | peer -> peer | Base64-encoded file chunk (512 KB) |
| `tunnel_open` | peer -> peer | Request to open a TCP tunnel |
| `tunnel_accept` | peer -> peer | Accept a tunnel request |
| `tunnel_data` | peer -> peer | Relay TCP data through the tunnel |
| `tunnel_close` | peer -> peer | Close an active tunnel |
| `ping` | either | Keepalive ping |
| `pong` | either | Keepalive pong |
| `error` | registry -> peer | Error notification |

Protocol version: `0.2.0`

## Architecture

```
                          ┌─────────────────┐
                          │   MCP Server    │
                          │  (Claude Code)  │
                          └────────┬────────┘
                                   │ tools
                                   v
┌──────────┐   WebSocket   ┌──────────────┐   WebSocket   ┌──────────┐
│  Peer A  │ <==========>  │   Registry   │  <===========> │  Peer B  │
│ (agent)  │               │  (relay srv) │               │ (device) │
└──────────┘               └──────────────┘               └──────────┘
```

All traffic flows through the registry relay. No direct peer connections are needed. This means burrow works through NAT and firewalls without any port forwarding.

When used as a Claude Code plugin, the MCP server sits above the peer layer, translating tool calls into peer operations against the registry.

## Development

### Setup

```bash
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
```

### Testing

```bash
pytest tests/ -v
```

### Building standalone binaries

```bash
uv pip install -e ".[build]"
pyinstaller --onefile --name burrow burrow/cli.py
```

### Dependencies

- `websockets>=12.0` -- WebSocket client/server
- `mcp>=1.0` -- Model Context Protocol server (plugin mode)

## License

MIT
