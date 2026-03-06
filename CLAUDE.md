# burrow — Zero-Config P2P Networking for Agents

## Quick Install (for agents)

```bash
# One-line bootstrap (installs uv if needed, sets up venv, runs smoke test)
curl -fsSL https://raw.githubusercontent.com/slapglif/burrow/master/bootstrap.sh | bash

# Or manually:
git clone https://github.com/slapglif/burrow.git && cd burrow
uv venv && uv pip install -e .
```

### As a Claude Code Plugin
```bash
# Symlink into your plugins directory
ln -s /path/to/burrow ~/.claude/plugins/burrow
# Or clone directly there
git clone https://github.com/slapglif/burrow.git ~/.claude/plugins/burrow
```
Once installed as a plugin, all `burrow_*` tools are available automatically via MCP.

## What This Does

Burrow connects agents and devices through a central WebSocket relay. Any agent on any machine can:
- **Register** with a registry and discover other peers
- **Message** other peers by name
- **Transfer files** to other peers (chunked, any size)
- **Tunnel TCP ports** through the relay (NAT-friendly)

All traffic goes through the relay — no direct connections, no port forwarding, no firewall config.

## Available Tools (MCP)

| Tool | Purpose |
|------|---------|
| `burrow_serve` | Start a registry server (default: localhost:7654) |
| `burrow_connect` | Connect + register with a registry |
| `burrow_list_peers` | List all online peers |
| `burrow_send_message` | Send text message to a peer |
| `burrow_send_file` | Send a file to a peer |
| `burrow_open_tunnel` | Forward a local TCP port to a peer's port |
| `burrow_disconnect` | Disconnect from the registry |

## Typical Agent Workflow

```
1. burrow_serve()                              # Start registry (or connect to existing)
2. burrow_connect("ws://host:7654", "my-name") # Join the swarm
3. burrow_list_peers()                         # See who's online
4. burrow_send_message("peer-name", "hello")   # Communicate
5. burrow_send_file("peer-name", "/path/file") # Share files
6. burrow_open_tunnel("peer-name", 8080, 3000) # Forward ports
```

## Standalone CLI (no plugin needed)

```bash
burrow serve                                    # Start registry
burrow connect ws://host:7654 --name my-agent   # Join as peer
# Interactive: /peers, /msg, /send, /tunnel, /quit
```

## Key Files

| File | Purpose |
|------|---------|
| `burrow/protocol.py` | 15 message types + builders |
| `burrow/server.py` | Registry + relay server |
| `burrow/peer.py` | Async Peer client class |
| `burrow/cli.py` | Interactive REPL |
| `burrow/mcp_server.py` | MCP tools (7 tools) |
| `bootstrap.sh` | One-line setup script |
| `.claude-plugin/plugin.json` | Claude Code plugin manifest |
| `.mcp.json` | MCP server config |
| `skills/connect/` | Quick-connect skill |
| `skills/swarm-status/` | Network status skill |
| `agents/burrow-agent.md` | Autonomous networking agent |
| `hooks/hooks.json` | Tunnel safety + session awareness |

## Development

```bash
uv venv && uv pip install -e ".[dev]"
uv run pytest tests/ -v   # 55 tests
```

## Protocol

WebSocket + JSON, 15 message types: register, registered, peers, peer_joined, peer_left, msg, file_start, file_chunk, tunnel_open, tunnel_accept, tunnel_data, tunnel_close, ping, pong, error. Default port: 7654.
