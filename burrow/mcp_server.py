"""Burrow P2P MCP server — exposes peer capabilities as tools for Claude Code agents."""

import asyncio
import os
import socket

from mcp.server.fastmcp import FastMCP

from burrow.peer import Peer
from burrow.server import serve
from burrow.protocol import DEFAULT_PORT

mcp = FastMCP("burrow")

DEFAULT_REGISTRY = "wss://reg.ai-smith.net"

_peer: Peer | None = None
_listen_task: asyncio.Task | None = None
_server_task: asyncio.Task | None = None


# ── Core ────────────────────────────────────────────────────────────────────

@mcp.tool()
async def burrow_serve(host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> str:
    """Start a burrow registry server in the background."""
    global _server_task
    if _server_task and not _server_task.done():
        return f"Registry server already running on {host}:{port}."
    _server_task = asyncio.create_task(serve(host, port))
    return f"Registry server started on ws://{host}:{port}"


@mcp.tool()
async def burrow_connect(url: str = DEFAULT_REGISTRY, name: str | None = None,
                          token: str | None = None) -> str:
    """Connect to a burrow registry and register as a peer.
    Provide token if the server requires authentication.
    Auto-reconnects on connection loss."""
    global _peer, _listen_task
    if _peer and _peer.ws:
        return f"Already connected as '{_peer.name}' (id={_peer.id}). Disconnect first."
    if name is None:
        name = socket.gethostname()
    if token is None:
        token = os.environ.get("BURROW_TOKEN")
    _peer = Peer(url, name, token=token, auto_reconnect=True)
    try:
        await _peer.connect()
    except Exception as exc:
        _peer = None
        return f"Connection failed: {exc}"
    _listen_task = asyncio.create_task(_peer.run())
    peer_count = len(_peer.peers)
    return f"Connected to {url} as '{_peer.name}' (id={_peer.id}). {peer_count} other peer(s) online."


@mcp.tool()
async def burrow_disconnect() -> str:
    """Disconnect from the burrow registry."""
    global _peer, _listen_task
    if not _peer or not _peer.ws:
        return "Not connected."
    name = _peer.name
    await _peer.stop()
    _peer = None
    _listen_task = None
    return f"Disconnected '{name}' from registry."


@mcp.tool()
async def burrow_list_peers() -> str:
    """List all peers currently connected to the registry, including their status and capabilities."""
    if not _peer or not _peer.ws:
        return "Not connected. Call burrow_connect first."
    try:
        await _peer.request_peers()
    except Exception as exc:
        return f"Failed to request peers: {exc}"
    if not _peer.peers:
        return "No other peers online."
    lines = []
    for pid, pname in _peer.peers.items():
        status = _peer.peer_status.get(pid, {}).get("status", "?")
        task = _peer.peer_status.get(pid, {}).get("task", "")
        caps = _peer.peer_capabilities.get(pid, {})
        cap_str = ""
        if caps:
            skills = caps.get("skills", [])
            tools = caps.get("tools", [])
            if skills:
                cap_str += f" skills={skills}"
            if tools:
                cap_str += f" tools={tools}"
        task_str = f" ({task})" if task else ""
        lines.append(f"  {pname} ({pid}) [{status}{task_str}]{cap_str}")
    return f"{len(lines)} peer(s) online:\n" + "\n".join(lines)


# ── Messaging ───────────────────────────────────────────────────────────────

@mcp.tool()
async def burrow_send_message(to: str, body: str) -> str:
    """Send a text message to a peer by name or id. Returns delivery status."""
    if not _peer or not _peer.ws:
        return "Not connected. Call burrow_connect first."
    try:
        result = await _peer.send_message(to, body)
    except Exception as exc:
        return f"Failed to send message: {exc}"
    if result == "delivered":
        return f"Message delivered to '{to}'."
    elif result == "queued":
        return f"'{to}' is offline. Message queued for delivery when they reconnect."
    return f"Message to '{to}': {result}"


@mcp.tool()
async def burrow_send_file(to: str, filepath: str) -> str:
    """Send a file to a peer by name or id."""
    if not _peer or not _peer.ws:
        return "Not connected. Call burrow_connect first."
    try:
        await _peer.send_file(to, filepath)
    except FileNotFoundError:
        return f"File not found: {filepath}"
    except Exception as exc:
        return f"Failed to send file: {exc}"
    return f"File '{filepath}' sent to '{to}'."


@mcp.tool()
async def burrow_open_tunnel(to: str, local_port: int, remote_port: int) -> str:
    """Open a TCP tunnel: localhost:local_port forwards to peer's localhost:remote_port."""
    if not _peer or not _peer.ws:
        return "Not connected. Call burrow_connect first."
    try:
        await _peer.open_tunnel(to, local_port, remote_port)
    except Exception as exc:
        return f"Failed to open tunnel: {exc}"
    return f"Tunnel open: localhost:{local_port} -> {to}:{remote_port}"


# ── Capabilities & Presence ─────────────────────────────────────────────────

@mcp.tool()
async def burrow_announce_capabilities(
    tools: str = "", model: str = "", skills: str = "",
    tags: str = "", status: str = "idle"
) -> str:
    """Announce this agent's capabilities to the swarm.
    Comma-separated lists for tools, skills, tags."""
    if not _peer or not _peer.ws:
        return "Not connected."
    caps = {"status": status}
    if tools:
        caps["tools"] = [t.strip() for t in tools.split(",")]
    if model:
        caps["model"] = model
    if skills:
        caps["skills"] = [s.strip() for s in skills.split(",")]
    if tags:
        caps["tags"] = [t.strip() for t in tags.split(",")]
    await _peer.announce_capabilities(caps)
    return f"Capabilities announced: {caps}"


@mcp.tool()
async def burrow_find_peers(
    required_tools: str = "", required_skills: str = "", required_tags: str = ""
) -> str:
    """Find peers matching capability requirements. Comma-separated lists."""
    if not _peer or not _peer.ws:
        return "Not connected."
    tools = [t.strip() for t in required_tools.split(",") if t.strip()] or None
    skills = [s.strip() for s in required_skills.split(",") if s.strip()] or None
    tags = [t.strip() for t in required_tags.split(",") if t.strip()] or None
    matches = await _peer.query_capabilities(tools, skills, tags)
    if not matches:
        return "No matching peers found."
    lines = []
    for m in matches:
        caps = m.get("capabilities", {})
        status = m.get("status", "?")
        lines.append(f"  {m['name']} ({m['id']}) [{status}]: {caps}")
    return f"{len(matches)} matching peer(s):\n" + "\n".join(lines)


@mcp.tool()
async def burrow_update_status(status: str, task: str = "") -> str:
    """Update presence status. Status: idle, busy, working. Task: description of current work."""
    if not _peer or not _peer.ws:
        return "Not connected."
    await _peer.update_status(status, task)
    return f"Status updated: {status}" + (f" ({task})" if task else "")


# ── Groups / Channels ──────────────────────────────────────────────────────

@mcp.tool()
async def burrow_join_group(group: str) -> str:
    """Join a named group/channel. Messages can be broadcast to all group members."""
    if not _peer or not _peer.ws:
        return "Not connected."
    await _peer.join_group(group)
    return f"Joined group '{group}'."


@mcp.tool()
async def burrow_leave_group(group: str) -> str:
    """Leave a group/channel."""
    if not _peer or not _peer.ws:
        return "Not connected."
    await _peer.leave_group(group)
    return f"Left group '{group}'."


@mcp.tool()
async def burrow_group_message(group: str, body: str) -> str:
    """Send a message to all members of a group."""
    if not _peer or not _peer.ws:
        return "Not connected."
    try:
        await _peer.send_group_message(group, body, wait_ack=True)
    except Exception as exc:
        return f"Failed: {exc}"
    return f"Message sent to group '{group}'."


@mcp.tool()
async def burrow_list_groups() -> str:
    """List all active groups and their member counts."""
    if not _peer or not _peer.ws:
        return "Not connected."
    groups = await _peer.list_groups()
    if not groups:
        return "No active groups."
    lines = [f"  {name}: {count} member(s)" for name, count in groups.items()]
    return f"{len(groups)} group(s):\n" + "\n".join(lines)


@mcp.tool()
async def burrow_group_members(group: str) -> str:
    """List members of a specific group."""
    if not _peer or not _peer.ws:
        return "Not connected."
    members = await _peer.get_group_members(group)
    if not members:
        return f"No members in group '{group}' (or group doesn't exist)."
    lines = [f"  {m['name']} ({m['id']}) [{m.get('status', '?')}]" for m in members]
    return f"{len(members)} member(s) in '{group}':\n" + "\n".join(lines)


# ── Shared State ────────────────────────────────────────────────────────────

@mcp.tool()
async def burrow_state_set(key: str, value: str, group: str = "") -> str:
    """Set a shared key-value pair visible to all peers (or group members if group specified)."""
    if not _peer or not _peer.ws:
        return "Not connected."
    await _peer.set_state(key, value, group or None)
    scope = f"group '{group}'" if group else "global"
    return f"State set: {key} = {value} ({scope})"


@mcp.tool()
async def burrow_state_get(key: str, group: str = "") -> str:
    """Get a shared state value by key."""
    if not _peer or not _peer.ws:
        return "Not connected."
    value = await _peer.get_state(key, group or None)
    if value is None:
        return f"Key '{key}' not found."
    return f"{key} = {value}"


@mcp.tool()
async def burrow_state_sync(group: str = "") -> str:
    """Sync all shared state from the server. Returns the full key-value store."""
    if not _peer or not _peer.ws:
        return "Not connected."
    state = await _peer.sync_state(group or None)
    if not state:
        return "No shared state."
    lines = [f"  {k} = {v}" for k, v in state.items()]
    scope = f"group '{group}'" if group else "global"
    return f"Shared state ({scope}, {len(state)} keys):\n" + "\n".join(lines)


# ── Task Coordination ───────────────────────────────────────────────────────

@mcp.tool()
async def burrow_broadcast_task(task: str, timeout_s: float = 30.0,
                                 required_skills: str = "") -> str:
    """Broadcast a task to all peers (optionally filtered by skills) and collect responses."""
    if not _peer or not _peer.ws:
        return "Not connected."
    skills = [s.strip() for s in required_skills.split(",") if s.strip()] or None
    responses = await _peer.broadcast_task(task, timeout_s, skills)
    if not responses:
        return "No responses received."
    lines = [f"  {r['from']}: {r['response']}" for r in responses]
    return f"{len(responses)} response(s):\n" + "\n".join(lines)


@mcp.tool()
async def burrow_delegate_task(to: str, task: str, context: str = "",
                                timeout_s: float = 120.0) -> str:
    """Delegate a task to a specific peer and wait for result."""
    if not _peer or not _peer.ws:
        return "Not connected."
    ctx = {"description": context} if context else {}
    result = await _peer.delegate_task(to, task, context=ctx, timeout_s=timeout_s)
    status = result.get("status", "unknown")
    if status == "timeout":
        return f"Task timed out after {timeout_s}s."
    return f"Task {status}: {result.get('result', 'no result')}"


@mcp.tool()
async def burrow_return_result(to: str, task_id: str, result: str,
                                success: bool = True) -> str:
    """Return a result for a delegated task back to the assigning peer."""
    if not _peer or not _peer.ws:
        return "Not connected."
    await _peer.return_task_result(to, task_id, result, success)
    return f"Result sent for task {task_id}."


@mcp.tool()
async def burrow_get_pending_tasks() -> str:
    """Get pending tasks that have been assigned or broadcast to this agent."""
    if not _peer or not _peer.ws:
        return "Not connected."
    if not _peer.pending_tasks:
        return "No pending tasks."
    lines = []
    for t in _peer.pending_tasks:
        lines.append(f"  [{t['type']}] {t['task_id']} from {t['from_name']}: {t['task']}")
    return f"{len(_peer.pending_tasks)} pending task(s):\n" + "\n".join(lines)


# ── Voting / Consensus ──────────────────────────────────────────────────────

@mcp.tool()
async def burrow_propose_vote(proposal: str, options: str = "approve,reject,abstain",
                               deadline_s: float = 30.0) -> str:
    """Propose a vote to all peers. Options are comma-separated."""
    if not _peer or not _peer.ws:
        return "Not connected."
    opt_list = [o.strip() for o in options.split(",")]
    result = await _peer.propose_vote(proposal, opt_list, deadline_s)
    tally_str = ", ".join(f"{k}: {v}" for k, v in result["tally"].items())
    return (f"Vote complete. Outcome: {result['outcome']}\n"
            f"Tally ({result['total']} votes): {tally_str}")


@mcp.tool()
async def burrow_cast_vote(to: str, vote_id: str, choice: str,
                            reason: str = "") -> str:
    """Cast a vote on a proposal from another peer."""
    if not _peer or not _peer.ws:
        return "Not connected."
    await _peer.cast_vote(to, vote_id, choice, reason)
    return f"Vote '{choice}' cast on {vote_id}."


# ── Leader Election ─────────────────────────────────────────────────────────

@mcp.tool()
async def burrow_elect_leader() -> str:
    """Trigger a leader election in the swarm. Returns the elected leader."""
    if not _peer or not _peer.ws:
        return "Not connected."
    result = await _peer.start_election()
    if result["leader_id"] == _peer.id:
        return f"This agent was elected leader (id={_peer.id})."
    return f"Leader elected: {result['leader_name']} (id={result['leader_id']})"


@mcp.tool()
async def burrow_get_leader() -> str:
    """Return the current swarm leader."""
    if not _peer or not _peer.ws:
        return "Not connected."
    if not _peer.leader_id:
        return "No leader elected yet. Call burrow_elect_leader."
    is_me = " (this agent)" if _peer.is_leader else ""
    return f"Leader: {_peer.leader_name} (id={_peer.leader_id}){is_me}"


# ── Distributed Jobs ───────────────────────────────────────────────────────

@mcp.tool()
async def burrow_submit_job(to: str, func: str, args: str = "[]",
                             kwargs: str = "{}", runtime: str = "builtin",
                             timeout_s: float = 120.0) -> str:
    """Submit a distributed job to a peer. func is 'module.function' path.
    Runtime: 'builtin' (in-process), 'ray', or 'dask'.
    Args/kwargs as JSON strings. Returns job result."""
    if not _peer or not _peer.ws:
        return "Not connected. Call burrow_connect first."
    import json as _json
    try:
        parsed_args = _json.loads(args)
        parsed_kwargs = _json.loads(kwargs)
    except _json.JSONDecodeError as e:
        return f"Invalid JSON: {e}"
    try:
        result = await _peer.submit_job(
            to, func, args=parsed_args, kwargs=parsed_kwargs,
            runtime=runtime, timeout=timeout_s)
        status = result.get("status", "unknown")
        if status in ("completed", "finished"):
            return f"Job completed: {result.get('result')}"
        elif status == "failed":
            return f"Job failed: {result.get('error', 'unknown error')}"
        elif status == "timeout":
            return f"Job timed out after {timeout_s}s. Job ID: {result.get('job_id')}"
        return f"Job status: {status}. Result: {result}"
    except Exception as exc:
        return f"Failed to submit job: {exc}"


@mcp.tool()
async def burrow_job_status(to: str, job_id: str) -> str:
    """Check the status of a previously submitted job."""
    if not _peer or not _peer.ws:
        return "Not connected."
    result = await _peer.check_job_status(to, job_id)
    return f"Job {job_id}: {result.get('status', 'unknown')}"


@mcp.tool()
async def burrow_cancel_job(to: str, job_id: str) -> str:
    """Cancel a running job on a peer."""
    if not _peer or not _peer.ws:
        return "Not connected."
    await _peer.cancel_job(to, job_id)
    return f"Cancel request sent for job {job_id}."


@mcp.tool()
async def burrow_list_jobs() -> str:
    """List all jobs tracked by the server."""
    if not _peer or not _peer.ws:
        return "Not connected."
    jobs = await _peer.list_all_jobs()
    if not jobs:
        return "No jobs tracked."
    lines = []
    for j in jobs:
        if j:
            lines.append(f"  {j.get('job_id','?')} [{j.get('status','?')}] "
                         f"{j.get('func', j.get('payload','?'))}")
    return f"{len(lines)} job(s):\n" + "\n".join(lines)


@mcp.tool()
async def burrow_init_runtime(runtime: str, address: str = "") -> str:
    """Initialize a distributed runtime: 'ray' or 'dask'.
    Optionally provide scheduler/cluster address."""
    if not _peer or not _peer.ws:
        return "Not connected."
    addr = address or None
    if runtime == "ray":
        ok = _peer.init_ray(addr)
        return f"Ray {'connected' if ok else 'failed to connect'}."
    elif runtime == "dask":
        ok = _peer.init_dask(addr)
        return f"Dask {'connected' if ok else 'failed to connect'}."
    return f"Unknown runtime: {runtime}. Use 'ray' or 'dask'."


@mcp.tool()
async def burrow_available_runtimes() -> str:
    """List available distributed runtimes on this peer."""
    if not _peer or not _peer.ws:
        return "Not connected."
    runtimes = _peer.available_runtimes
    return f"Available runtimes: {', '.join(runtimes)}"


@mcp.tool()
async def burrow_submit_script(to: str, script_path: str, args: str = "[]",
                                timeout_s: float = 300.0) -> str:
    """Upload a local script file to a remote peer and execute it.

    Supports .py (Python), .sh (Bash), or any executable.
    The script is transferred inline and run in a temp directory on the peer.
    Args is a JSON array of command-line arguments.
    Returns stdout/stderr from the script execution."""
    if not _peer or not _peer.ws:
        return "Not connected. Call burrow_connect first."
    import json as _json
    try:
        parsed_args = _json.loads(args)
    except _json.JSONDecodeError as e:
        return f"Invalid JSON args: {e}"
    try:
        result = await _peer.submit_script(
            to, script_path, args=parsed_args, timeout=timeout_s)
        status = result.get("status", "unknown")
        if status in ("completed", "finished"):
            return f"Script completed:\n{result.get('result', '(no output)')}"
        elif status == "failed":
            err = result.get("error", "unknown error")
            out = result.get("result", "")
            msg = f"Script failed: {err}"
            if out:
                msg += f"\nOutput:\n{out}"
            return msg
        elif status == "timeout":
            return f"Script timed out after {timeout_s}s. Job ID: {result.get('job_id')}"
        return f"Script status: {status}"
    except FileNotFoundError:
        return f"Script not found: {script_path}"
    except Exception as exc:
        return f"Failed to submit script: {exc}"


@mcp.tool()
async def burrow_submit_batch(to: str, func: str, args_list: str,
                               runtime: str = "builtin",
                               max_retries: int = 0) -> str:
    """Submit a batch of jobs to a peer. args_list is JSON array of arg arrays.
    Example: '[[1],[2],[3]]' submits 3 jobs with different args.
    Returns batch_id and per-job status."""
    if not _peer or not _peer.ws:
        return "Not connected."
    import json as _json
    try:
        parsed = _json.loads(args_list)
    except _json.JSONDecodeError as e:
        return f"Invalid JSON: {e}"
    results = []
    for i, args in enumerate(parsed):
        try:
            r = await _peer.submit_job(to, func, args=args, runtime=runtime, timeout=120.0)
            results.append(f"  [{i}] {r.get('status','?')}: {r.get('result', r.get('error',''))}")
        except Exception as exc:
            results.append(f"  [{i}] error: {exc}")
    return f"Batch of {len(parsed)} jobs:\n" + "\n".join(results)


@mcp.tool()
async def burrow_map_job(to: str, func: str, inputs: str,
                          runtime: str = "builtin") -> str:
    """Map a function over a list of inputs on a remote peer.
    inputs is a JSON array. Each element is passed as a single arg.
    Example: func='math.factorial', inputs='[5,10,20]'"""
    if not _peer or not _peer.ws:
        return "Not connected."
    import json as _json
    try:
        parsed = _json.loads(inputs)
    except _json.JSONDecodeError as e:
        return f"Invalid JSON: {e}"
    results = []
    for inp in parsed:
        try:
            r = await _peer.submit_job(to, func, args=[inp], runtime=runtime, timeout=60.0)
            results.append(r.get("result", r.get("error", "?")))
        except Exception as exc:
            results.append(f"error: {exc}")
    return f"Map results ({len(results)} items):\n" + "\n".join(
        f"  {inp} -> {res}" for inp, res in zip(parsed, results))


@mcp.tool()
async def burrow_job_logs(job_id: str) -> str:
    """Get execution logs for a local job."""
    if not _peer or not _peer.ws:
        return "Not connected."
    logs = _peer._executor.get_job_logs(job_id)
    if not logs:
        return f"No logs for job {job_id}."
    return f"Logs for {job_id}:\n" + "\n".join(f"  {l}" for l in logs)


@mcp.tool()
async def burrow_job_stats() -> str:
    """Get aggregate statistics for all local jobs."""
    if not _peer or not _peer.ws:
        return "Not connected."
    import json as _json
    stats = _peer._executor.stats()
    return f"Job statistics:\n{_json.dumps(stats, indent=2)}"


@mcp.tool()
async def burrow_purge_jobs(status: str = "") -> str:
    """Remove completed/failed/cancelled jobs from local tracking."""
    if not _peer or not _peer.ws:
        return "Not connected."
    count = _peer._executor.purge(status=status or None)
    return f"Purged {count} jobs."


# ── Server-Side Work Queue ─────────────────────────────────────────────────

@mcp.tool()
async def burrow_queue_push(queue: str, payload: str, priority: int = 0) -> str:
    """Push a job to a named server-side work queue. Payload is JSON.
    Workers pull jobs from the queue and report results."""
    if not _peer or not _peer.ws:
        return "Not connected."
    import json as _json
    try:
        data = _json.loads(payload)
    except _json.JSONDecodeError as e:
        return f"Invalid JSON payload: {e}"
    job_id = await _peer.queue_push(queue, data, priority)
    return f"Job {job_id} pushed to queue '{queue}' (priority={priority})."


@mcp.tool()
async def burrow_queue_pull(queue: str) -> str:
    """Pull the next available job from a server-side work queue."""
    if not _peer or not _peer.ws:
        return "Not connected."
    item = await _peer.queue_pull(queue)
    if not item:
        return f"No jobs available in queue '{queue}'."
    import json as _json
    return (f"Job {item['job_id']} from queue '{queue}':\n"
            f"  Payload: {_json.dumps(item.get('payload', {}))}\n"
            f"  Priority: {item.get('priority', 0)}")


@mcp.tool()
async def burrow_queue_ack(queue: str, job_id: str, result: str = "",
                            success: bool = True) -> str:
    """Acknowledge completion of a queue job. Reports result back to submitter."""
    if not _peer or not _peer.ws:
        return "Not connected."
    await _peer.queue_ack(queue, job_id, result=result or None, success=success)
    status = "completed" if success else "failed"
    return f"Job {job_id} acknowledged as {status}."


@mcp.tool()
async def burrow_queue_status(queue: str = "") -> str:
    """Get status of server-side work queues."""
    if not _peer or not _peer.ws:
        return "Not connected."
    status = await _peer.queue_status(queue or None)
    if not status:
        return "No queues active."
    import json as _json
    return f"Queue status:\n{_json.dumps(status, indent=2)}"


@mcp.tool()
async def burrow_register_worker(queues: str = "", capabilities: str = "") -> str:
    """Register this agent as a worker for server-side queues.
    Comma-separated queue names and capabilities."""
    if not _peer or not _peer.ws:
        return "Not connected."
    q_list = [q.strip() for q in queues.split(",") if q.strip()] or None
    caps = {}
    if capabilities:
        for cap in capabilities.split(","):
            cap = cap.strip()
            if cap:
                caps[cap] = True
    await _peer.register_worker(q_list, caps or None)
    return f"Registered as worker for queues: {q_list or 'all'}"


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
