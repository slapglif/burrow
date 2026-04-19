# burrow

> Zero-config P2P relay for connecting agents and devices — with distributed computing

[![CI](https://github.com/slapglif/burrow/actions/workflows/ci.yml/badge.svg)](https://github.com/slapglif/burrow/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/slapglif/burrow)](https://github.com/slapglif/burrow/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

## What is Burrow?

Burrow is a zero-config P2P relay that lets agents and devices discover each other, exchange messages, transfer files, tunnel TCP ports, coordinate tasks, and run distributed computations — all through a central WebSocket registry. No direct connections, no port forwarding, no NAT traversal headaches.

**Public registry at `wss://reg.ai-smith.net`** — always on, always discoverable. Every peer auto-connects on startup.

### Key Features

- **Peer Discovery** — find peers by name, capabilities, skills, or tags
- **Messaging** — text messages with ACK/NACK delivery confirmation, offline queuing
- **File Transfer** — chunked base64 transfer of any size
- **TCP Tunneling** — forward local ports through the relay
- **Remote Desktop Orchestration** — prefer the native sidecar when present, otherwise launch tunneled xpra/x11vnc/wayvnc sessions on remote peers with a RustDesk-inspired control/media split
- **Groups & Channels** — named channels with scoped messaging and state
- **Shared State** — distributed key-value store (global or group-scoped)
- **Task Coordination** — broadcast tasks, delegate work, collect results
- **Voting & Consensus** — propose votes, collect ballots, tally results
- **Leader Election** — bully algorithm for swarm coordination
- **Distributed Jobs** — submit jobs via Ray, Dask, or built-in executor
- **Work Queue** — server-side priority queue with worker registration
- **Batch & Map** — submit batches, map functions over inputs in parallel
- **Auto-Retry** — configurable retry with exponential backoff
- **Auto-Reconnect** — exponential backoff reconnection on connection loss

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

### Local two-way smoketest

When the public registry is down or blocked, verify Burrow itself with a local
round-trip between Diogi and FoxBoi-style peers:

```bash
python scripts/local_two_way_smoketest.py
```

Expected result: a JSON payload with `"ok": true` and a transcript showing
Diogi sent a handshake and FoxBoi replied over the local registry.

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

### Remote desktop support matrix

What is real today:

| Surface | Current state |
|---------|---------------|
| Native sidecar selection | Preferred automatically when `burrow-rd-host` is installed and starts successfully |
| Native display enumeration | Real Linux enumeration via sidecar capability probe (`xrandr`-backed today) when available |
| Native snapshot/input transport | Routed through the Python bridge and MCP/CLI session model |
| Native capture pixels | Sidecar-dependent; current in-repo sidecar may still report stubbed frames |
| Native input execution | Sidecar-dependent; current sidecar may still accept actions with stubbed acknowledgements |
| Clipboard sync | Not exposed as control-plane read/write yet; MCP/CLI only send clipboard-oriented shortcuts or typed text, and true clipboard behavior depends on backend/sidecar support |
| xpra/x11vnc/wayvnc backends | Still available as honest fallbacks when native is missing or unsuitable |

Truthfulness note: Burrow now surfaces sidecar capability state, display targets, and stub/unsupported clipboard details to CLI/MCP responses. Do not treat clipboard, capture, or input as fully native unless the reported capability payload says they are available.

## Claude Code Plugin

Burrow ships as a Claude Code plugin with **43 MCP tools**. Install it to give your agent full P2P networking, task coordination, and distributed computing.

### Installation

```bash
# Automated (recommended) — works from any GitHub clone
git clone https://github.com/slapglif/burrow.git && cd burrow
bash scripts/install-plugin.sh
```

The install script handles everything: venv creation (`uv venv`), dependency install, symlink into `~/.claude/plugins/`, MCP registration with absolute paths, and connectivity verification.

```bash
# Verify after install
claude mcp list    # Should show: burrow ... ✓ Connected
```

> **Note**: `claude plugin list` may show "failed to load" for `burrow@local` — this is expected for locally-installed plugins and does **not** affect MCP tool availability. The MCP server is the primary integration.

### Auto-Connect

On session start, burrow's **SessionStart hook** automatically connects your agent to `wss://reg.ai-smith.net`. Your agent is instantly discoverable by all other peers — no configuration needed.

### MCP Tools (43 tools)

#### Core
| Tool | Description |
|------|-------------|
| `burrow_connect` | Connect to registry (default: `wss://reg.ai-smith.net`) |
| `burrow_disconnect` | Disconnect from the registry |
| `burrow_serve` | Start a local registry server |
| `burrow_list_peers` | List all online peers with status and capabilities |

#### Messaging & Files
| Tool | Description |
|------|-------------|
| `burrow_send_message` | Send text message with delivery confirmation (ACK/NACK) |
| `burrow_send_file` | Transfer a file to a peer |
| `burrow_open_tunnel` | Open a TCP port tunnel through the relay |

#### Capabilities & Presence
| Tool | Description |
|------|-------------|
| `burrow_announce_capabilities` | Announce tools, skills, model, tags to the swarm |
| `burrow_find_peers` | Find peers matching capability requirements |
| `burrow_update_status` | Update presence status (idle/busy/working) |

#### Groups & Channels
| Tool | Description |
|------|-------------|
| `burrow_join_group` | Join a named group/channel |
| `burrow_leave_group` | Leave a group |
| `burrow_group_message` | Broadcast message to group members |
| `burrow_list_groups` | List active groups with member counts |
| `burrow_group_members` | List members of a group |

#### Shared State (Distributed KV Store)
| Tool | Description |
|------|-------------|
| `burrow_state_set` | Set a shared key-value pair (global or group-scoped) |
| `burrow_state_get` | Get a shared state value by key |
| `burrow_state_sync` | Sync all shared state from the server |

#### Task Coordination
| Tool | Description |
|------|-------------|
| `burrow_broadcast_task` | Broadcast a task to all peers, collect responses |
| `burrow_delegate_task` | Delegate a task to a specific peer, wait for result |
| `burrow_return_result` | Return result for a delegated task |
| `burrow_get_pending_tasks` | Get tasks assigned to this agent |

#### Voting & Consensus
| Tool | Description |
|------|-------------|
| `burrow_propose_vote` | Propose a vote to all peers |
| `burrow_cast_vote` | Cast a vote on a proposal |

#### Leader Election
| Tool | Description |
|------|-------------|
| `burrow_elect_leader` | Trigger a bully-algorithm leader election |
| `burrow_get_leader` | Get current swarm leader |

#### Distributed Jobs (Ray / Dask / Built-in)
| Tool | Description |
|------|-------------|
| `burrow_submit_job` | Submit a job to a peer (builtin/ray/dask runtime) |
| `burrow_submit_batch` | Submit a batch of jobs in parallel |
| `burrow_map_job` | Map a function over a list of inputs |
| `burrow_job_status` | Check status of a submitted job |
| `burrow_cancel_job` | Cancel a running job |
| `burrow_list_jobs` | List all tracked jobs |
| `burrow_job_logs` | Get execution logs for a job |
| `burrow_job_stats` | Get aggregate job statistics |
| `burrow_purge_jobs` | Remove completed/failed jobs |
| `burrow_init_runtime` | Initialize Ray or Dask runtime |
| `burrow_available_runtimes` | List available runtimes on this peer |
| `burrow_submit_script` | Submit a script for distributed execution |

#### Server-Side Work Queue
| Tool | Description |
|------|-------------|
| `burrow_queue_push` | Push a job to a named server-side priority queue |
| `burrow_queue_pull` | Pull next job from a queue |
| `burrow_queue_ack` | Acknowledge job completion with result |
| `burrow_queue_status` | Get queue statistics |
| `burrow_register_worker` | Register as a queue worker |

### Skills

| Skill | Description |
|-------|-------------|
| `connect` | Guided workflow to connect to the swarm |
| `swarm-status` | Show peer connectivity and network status |

### Agent

The plugin registers a **burrow-agent** (cyan, sonnet model) that autonomously manages peer connections, relays messages, transfers files, coordinates tasks, and runs distributed jobs across multi-agent swarms.

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
    peer = Peer("wss://reg.ai-smith.net", "my-agent",
                capabilities={"skills": ["coding"], "model": "opus"},
                auto_reconnect=True)
    await peer.connect()
    print(f"Connected as {peer.name} ({peer.id})")
    print(f"Online: {peer.peers}")

    # Messaging
    result = await peer.send_message("other-agent", "hello")
    print(f"Delivery: {result}")  # "delivered", "queued", or "nack: ..."

    # Groups
    await peer.join_group("dev-team")
    await peer.send_group_message("dev-team", "standup time!")

    # Shared state
    await peer.set_state("build_status", "passing")
    value = await peer.get_state("build_status")

    # Task delegation
    result = await peer.delegate_task("worker-1", "run tests", timeout_s=60)
    print(f"Task result: {result}")

    # Distributed job (builtin executor)
    job_result = await peer.submit_job("worker-1", "math.factorial",
                                        args=[100], runtime="builtin")
    print(f"Job: {job_result}")

    # Batch submission
    results = await peer.submit_batch("worker-1", "math.factorial",
                                       [[5], [10], [20]])

    # Server-side queue
    job_id = await peer.queue_push("tasks", {"action": "build", "target": "main"})
    item = await peer.queue_pull("tasks")
    await peer.queue_ack("tasks", item["job_id"], result="success")

    # Voting
    result = await peer.propose_vote("Ship v2?", ["yes", "no"])
    print(f"Vote outcome: {result['outcome']}")

    # Leader election
    leader = await peer.start_election()
    print(f"Leader: {leader['leader_name']}")

    await peer.stop()

asyncio.run(main())
```

## Protocol

All messages are JSON objects over WebSocket. Protocol version: `0.4.0`. 60+ message types organized into categories:

### Core Messages
| Type | Direction | Description |
|------|-----------|-------------|
| `register` | peer -> registry | Register with name, token, capabilities |
| `registered` | registry -> peer | Confirm registration, assign ID, list peers |
| `peers` | both | Request/response: list connected peers |
| `peer_joined` | registry -> peer | Notification: a new peer connected |
| `peer_left` | registry -> peer | Notification: a peer disconnected |
| `msg` | peer -> peer | Text message with ACK/NACK confirmation |
| `ping` / `pong` | either | Keepalive with configurable interval |
| `error` | registry -> peer | Error notification |
| `ack` / `nack` / `queued` | registry -> peer | Delivery confirmation |

### File Transfer & Tunneling
| Type | Description |
|------|-------------|
| `file_start` / `file_chunk` | Chunked base64 file transfer (512 KB chunks) |
| `tunnel_open` / `tunnel_accept` / `tunnel_data` / `tunnel_close` | TCP port forwarding |

### Capabilities, Groups, State, Tasks, Voting, Election
| Category | Types |
|----------|-------|
| Capabilities | `capability_announce`, `capability_query`, `capability_response` |
| Groups | `group_join`, `group_leave`, `group_msg`, `group_list`, `group_members` |
| Shared State | `state_set`, `state_get`, `state_value`, `state_delete`, `state_sync` |
| Presence | `status_update` |
| Tasks | `task_broadcast`, `task_response`, `task_assign`, `task_status`, `task_result` |
| Voting | `vote_propose`, `vote_cast`, `vote_result` |
| Election | `election_start`, `election_alive`, `election_victory` |

### Distributed Computing
| Category | Types |
|----------|-------|
| Jobs | `job_submit`, `job_status`, `job_result`, `job_cancel`, `job_list`, `job_update` |
| Queue | `queue_push`, `queue_pull`, `queue_ack`, `queue_status` |
| Workers | `worker_register`, `worker_heartbeat` |

## Architecture

```
                          ┌─────────────────┐
                          │   MCP Server    │
                          │  (43 tools)     │
                          └────────┬────────┘
                                   │ tools
                                   v
┌──────────┐   WebSocket   ┌──────────────┐   WebSocket   ┌──────────┐
│  Peer A  │ <==========>  │   Registry   │  <===========> │  Peer B  │
│ (agent)  │               │ reg.ai-smith │               │ (worker) │
└──────────┘               └──────┬───────┘               └──────────┘
                                  │
                          ┌───────┴───────┐
                          │  Work Queue   │
                          │  Shared State │
                          │  Groups       │
                          └───────────────┘
```

All traffic flows through the registry relay at `wss://reg.ai-smith.net` (Cloudflare tunnel). No direct peer connections needed. Works through NAT and firewalls without any port forwarding.

## Key Files

| File | Purpose |
|------|---------|
| `burrow/protocol.py` | 60+ message types + builders (v0.4.0) |
| `burrow/server.py` | Registry + relay server + work queue |
| `burrow/peer.py` | Async Peer client class |
| `burrow/distributed.py` | Ray, Dask, and built-in queue wrappers |
| `burrow/mcp_server.py` | MCP tools (43 tools) |
| `burrow/cli.py` | Interactive REPL |

## Development

```bash
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
uv run pytest tests/ -v   # 239 tests

# Optional distributed runtimes:
uv pip install -e ".[ray]"    # Ray support
uv pip install -e ".[dask]"   # Dask support
uv pip install -e ".[all]"    # Both
```

### Building standalone binaries

```bash
uv pip install -e ".[build]"
uv run pyinstaller --onefile --name burrow burrow/__main__.py
```

### Dependencies

- `websockets>=12.0` — WebSocket client/server
- `mcp>=1.0` — Model Context Protocol server (plugin mode)
- `ray[default]>=2.9` — (optional) Ray distributed computing
- `dask[distributed]>=2024.1` — (optional) Dask distributed computing

## License

MIT
