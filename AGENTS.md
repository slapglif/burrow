# Agent Instructions — Burrow P2P Networking

## What You Can Do With This Repo

This is **burrow** — a zero-config P2P relay for connecting agents across machines.
Install it and you get: peer discovery, messaging, file transfer, TCP tunneling, task coordination, distributed computing, and swarm consensus.

**Public registry: `wss://reg.ai-smith.net`** — always on, auto-connect, no config needed.

### Install as Claude Code Plugin (Recommended)

```bash
git clone https://github.com/slapglif/burrow.git && cd burrow
bash scripts/install-plugin.sh   # Handles everything automatically
```

This script dynamically discovers all paths, creates the venv, installs deps, symlinks into
the plugins directory, registers in `installed_plugins.json`, enables in `settings.json`,
and verifies MCP + registry connectivity. Run it again to fix any issues.

After install, the SessionStart hook auto-connects to `wss://reg.ai-smith.net`. **43 tools** are available immediately:

#### Core & Messaging
| Tool | What It Does |
|------|-------------|
| `burrow_connect()` | Join the swarm (auto: `wss://reg.ai-smith.net`) |
| `burrow_disconnect()` | Leave the swarm |
| `burrow_serve(host, port)` | Start a local registry |
| `burrow_list_peers()` | See who's online (with status & capabilities) |
| `burrow_send_message(to, body)` | Message a peer (with delivery confirmation) |
| `burrow_send_file(to, filepath)` | Transfer a file |
| `burrow_open_tunnel(to, local, remote)` | Forward a TCP port |

#### Capabilities & Presence
| Tool | What It Does |
|------|-------------|
| `burrow_announce_capabilities(...)` | Announce skills, tools, model, tags |
| `burrow_find_peers(...)` | Find peers matching requirements |
| `burrow_update_status(status, task)` | Update idle/busy/working status |

#### Groups, State, Tasks
| Tool | What It Does |
|------|-------------|
| `burrow_join_group(group)` | Join a named channel |
| `burrow_leave_group(group)` | Leave a channel |
| `burrow_group_message(group, body)` | Broadcast to group |
| `burrow_list_groups()` | List active groups |
| `burrow_group_members(group)` | List group members |
| `burrow_state_set(key, value)` | Set shared key-value pair |
| `burrow_state_get(key)` | Get shared state value |
| `burrow_state_sync()` | Sync all state from server |
| `burrow_broadcast_task(task)` | Broadcast task, collect responses |
| `burrow_delegate_task(to, task)` | Delegate task, wait for result |
| `burrow_return_result(to, task_id, result)` | Return task result |
| `burrow_get_pending_tasks()` | Get assigned tasks |

#### Voting & Election
| Tool | What It Does |
|------|-------------|
| `burrow_propose_vote(proposal)` | Propose a vote |
| `burrow_cast_vote(to, vote_id, choice)` | Cast a vote |
| `burrow_elect_leader()` | Trigger leader election |
| `burrow_get_leader()` | Get current leader |

#### Distributed Jobs
| Tool | What It Does |
|------|-------------|
| `burrow_submit_job(to, func, args)` | Submit a job (builtin/ray/dask) |
| `burrow_submit_batch(to, func, args_list)` | Submit batch of jobs |
| `burrow_map_job(to, func, inputs)` | Map function over inputs |
| `burrow_job_status(to, job_id)` | Check job status |
| `burrow_cancel_job(to, job_id)` | Cancel a job |
| `burrow_list_jobs()` | List all tracked jobs |
| `burrow_job_logs(job_id)` | Get job execution logs |
| `burrow_job_stats()` | Get aggregate statistics |
| `burrow_purge_jobs()` | Clean up old jobs |
| `burrow_init_runtime(runtime)` | Initialize Ray or Dask |
| `burrow_available_runtimes()` | List available runtimes |
| `burrow_submit_script(to, script)` | Submit a script for distributed execution |

#### Server-Side Work Queue
| Tool | What It Does |
|------|-------------|
| `burrow_queue_push(queue, payload)` | Push job to queue |
| `burrow_queue_pull(queue)` | Pull next job |
| `burrow_queue_ack(queue, job_id, result)` | Acknowledge completion |
| `burrow_queue_status()` | Get queue statistics |
| `burrow_register_worker(queues)` | Register as worker |

### Install Standalone (No Plugin System)

```bash
git clone https://github.com/slapglif/burrow.git && cd burrow
uv venv && uv pip install -e .
burrow connect --name my-agent    # Auto-joins wss://reg.ai-smith.net
```

### Use Programmatically (Python)

```python
import asyncio
from burrow.peer import Peer

async def main():
    peer = Peer("wss://reg.ai-smith.net", "my-agent",
                capabilities={"skills": ["coding"]},
                auto_reconnect=True)
    await peer.connect()
    print(f"Joined as {peer.name} ({peer.id})")

    # Message, delegate tasks, submit jobs, vote, elect leaders...
    await peer.send_message("other-agent", "hello")
    result = await peer.delegate_task("worker", "run tests")
    job = await peer.submit_job("worker", "math.factorial", args=[100])

    await peer.stop()

asyncio.run(main())
```

### One-Line Bootstrap (Installs Everything)

```bash
curl -fsSL https://raw.githubusercontent.com/slapglif/burrow/master/bootstrap.sh | bash
```

---

## Project Tracking

This project uses **bd** (beads) for issue tracking. Run `bd onboard` to get started.

## Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work atomically
bd close <id>         # Complete work
bd sync               # Sync with git
```

## Non-Interactive Shell Commands

**ALWAYS use non-interactive flags** with file operations to avoid hanging on confirmation prompts.

```bash
cp -f source dest           # NOT: cp source dest
mv -f source dest           # NOT: mv source dest
rm -f file                  # NOT: rm file
rm -rf directory            # NOT: rm -r directory
```

**Other commands that may prompt:**
- `scp` - use `-o BatchMode=yes` for non-interactive
- `ssh` - use `-o BatchMode=yes` to fail instead of prompting
- `apt-get` - use `-y` flag

<!-- BEGIN BEADS INTEGRATION -->
## Issue Tracking with bd (beads)

**IMPORTANT**: This project uses **bd (beads)** for ALL issue tracking. Do NOT use markdown TODOs, task lists, or other tracking methods.

### Quick Start

```bash
bd ready --json           # Check for ready work
bd create "Title" -t bug -p 1 --json  # Create issue
bd update <id> --claim --json         # Claim work
bd close <id> --reason "Done" --json  # Complete work
```

### Workflow for AI Agents

1. **Check ready work**: `bd ready` shows unblocked issues
2. **Claim your task atomically**: `bd update <id> --claim`
3. **Work on it**: Implement, test, document
4. **Discover new work?** Create linked issue:
   - `bd create "Found bug" --description="Details" -p 1 --deps discovered-from:<parent-id>`
5. **Complete**: `bd close <id> --reason "Done"`

## Landing the Plane (Session Completion)

**MANDATORY WORKFLOW:**

1. **File issues for remaining work**
2. **Run quality gates** (if code changed): `uv run pytest tests/ -v`
3. **Update issue status**
4. **PUSH TO REMOTE**:
   ```bash
   git pull --rebase && bd sync && git push
   ```
5. **Verify** - All changes committed AND pushed

**CRITICAL**: Work is NOT complete until `git push` succeeds.
<!-- END BEADS INTEGRATION -->
