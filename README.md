# burrow

> Zero-config P2P relay for connecting agents and devices

[![CI](https://github.com/slapglif/burrow/actions/workflows/ci.yml/badge.svg)](https://github.com/slapglif/burrow/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/slapglif/burrow)](https://github.com/slapglif/burrow/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

## What is Burrow?

Burrow is a zero-config P2P relay that lets agents and devices discover each other, exchange messages, transfer files, and tunnel TCP ports — all through a central WebSocket registry. No direct connections, no port forwarding, no NAT traversal headaches.

**Public registry at `wss://reg.ai-smith.net`** — always on, always discoverable. Every peer auto-connects on startup.

## Quick Start

### Bootstrap script (easiest)

```bash
curl -fsSL https://raw.githubusercontent.com/slapglif/burrow/master/bootstrap.sh | bash
```

### Binary download

Grab a pre-built binary from [Releases](https://github.com/slapglif/burrow/releases):

| Platform       | Binary              |
|----------------|---------------------|
| Linux x64      | `burrow-linux-x64`  |
| macOS ARM64    | `burrow-macos-arm64`|
| Windows x64    | `burrow-windows-x64.exe`|

### From source

```bash
uv pip install git+https://github.com/slapglif/burrow.git
```

## Standalone Usage

### Connect to the public registry

```bash
burrow connect --name my-laptop
```

Connects to `wss://reg.ai-smith.net` by default. Use a custom registry with:

```bash
burrow connect ws://custom-host:7654 --name my-laptop
```

If `--name` is omitted, the system hostname is used.

### Start your own registry (optional)

```bash
burrow serve --port 7654
```

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

Burrow ships as a Claude Code plugin. Install it to give your agent full P2P networking with auto-connect to the public swarm.

### Installation

```bash
# Clone into plugins directory
git clone https://github.com/slapglif/burrow.git ~/.claude/plugins/burrow
cd ~/.claude/plugins/burrow && uv venv && uv pip install -e .
```

### Auto-Connect

On session start, burrow's **SessionStart hook** automatically connects your agent to `wss://reg.ai-smith.net`. Your agent is instantly discoverable by all other peers — no configuration needed.

### MCP Tools

| Tool | Description |
|------|-------------|
| `burrow_connect` | Connect to registry (default: `wss://reg.ai-smith.net`) |
| `burrow_list_peers` | List all peers connected to the registry |
| `burrow_send_message` | Send a text message to a peer |
| `burrow_send_file` | Transfer a file to a peer |
| `burrow_open_tunnel` | Open a TCP port tunnel through the relay |
| `burrow_serve` | Start a local registry server |
| `burrow_disconnect` | Disconnect from the registry |

### Skills

| Skill | Description |
|-------|-------------|
| `connect` | Guided workflow to connect to the swarm |
| `swarm-status` | Show peer connectivity and network status |

### Agent

The plugin registers a **burrow-agent** (cyan, sonnet model) that autonomously manages peer connections, relays messages, transfers files, and coordinates multi-agent swarms.

### Hooks

| Hook | Trigger | Purpose |
|------|---------|---------|
| SessionStart | Session begins | Auto-connects to `wss://reg.ai-smith.net` |
| PreToolUse | `burrow_open_tunnel` | Validates port ranges before opening tunnels |

## Programmatic Usage

```python
import asyncio
from burrow.peer import Peer

async def main():
    peer = Peer("wss://reg.ai-smith.net", "my-agent")
    await peer.connect()
    print(f"Connected as {peer.name} ({peer.id})")

    await peer.request_peers()
    await asyncio.sleep(0.3)
    print(f"Online: {peer.peers}")

    await peer.send_message("other-agent", "hello")
    await peer.send_file("other-agent", "/path/to/data.csv")
    await peer.open_tunnel("other-agent", 8080, 3000)

    await peer.ws.close()

asyncio.run(main())
```

## Protocol

All messages are JSON objects over WebSocket. Every message has a `type` field.

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
│ (agent)  │               │ reg.ai-smith │               │ (device) │
└──────────┘               └──────────────┘               └──────────┘
                            wss:// via CF
                              tunnel
```

All traffic flows through the registry relay at `wss://reg.ai-smith.net` (Cloudflare tunnel → localhost:7654). No direct peer connections needed. Works through NAT and firewalls without any port forwarding.

## Development

```bash
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
uv run pytest tests/ -v   # 55 tests
```

### Building standalone binaries

```bash
uv pip install -e ".[build]"
uv run pyinstaller --onefile --name burrow burrow/__main__.py
```

### Dependencies

- `websockets>=12.0` — WebSocket client/server
- `mcp>=1.0` — Model Context Protocol server (plugin mode)

## License

MIT
