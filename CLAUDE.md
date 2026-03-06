# burrow -- Zero-Config P2P Networking

## What Is This?

Burrow is a P2P networking utility that connects devices through a central WebSocket relay registry. It enables:
- **Peer discovery** via a central registry
- **Messaging** between connected peers
- **File transfer** (chunked, base64-encoded)
- **TCP tunneling** (port forwarding through the relay)

## Architecture

```
Peers --WebSocket--> Registry Server --relay--> Peers
```

- All traffic relayed through central server (NAT-friendly, zero-config)
- Protocol: WebSocket + JSON, 15 message types
- Default port: 7654

## Key Files

| File | Purpose |
|------|---------|
| `burrow/protocol.py` | Message types, builders, constants |
| `burrow/server.py` | Registry + relay server |
| `burrow/peer.py` | Async Peer client class |
| `burrow/cli.py` | Interactive REPL client |
| `mcp_server.py` | MCP tools server for Claude Code |

## Running

```bash
# Start registry
burrow serve

# Connect as peer
burrow connect ws://host:7654 --name my-agent

# Or use MCP tools via Claude Code plugin
```

## Development

```bash
uv venv && uv pip install -e ".[dev]"
uv run pytest tests/ -v
```

## Protocol Message Types

register, registered, peers, peer_joined, peer_left, msg, file_start, file_chunk, tunnel_open, tunnel_accept, tunnel_data, tunnel_close, ping, pong, error
