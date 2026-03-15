"""Burrow P2P MCP server — exposes peer capabilities as tools for Claude Code agents."""

import asyncio
import importlib
import logging
import os
import socket
import sys

from mcp.server.fastmcp import FastMCP

from burrow.peer import Peer
from burrow.server import serve
from burrow.protocol import DEFAULT_PORT

log = logging.getLogger("burrow.mcp")

mcp = FastMCP("burrow")

DEFAULT_REGISTRY = "wss://reg.ai-smith.net"
AUTO_UPDATE = os.environ.get("BURROW_AUTO_UPDATE", "1") != "0"

_peer: Peer | None = None
_listen_task: asyncio.Task | None = None
_server_task: asyncio.Task | None = None
_update_task: asyncio.Task | None = None
_last_reconnect_id: str | None = None  # Preserved across disconnect/connect cycles
_connect_url: str = ""
_connect_name: str | None = None
_connect_token: str | None = None

NOT_CONNECTED = (
    "Not connected to the burrow swarm. "
    "Call burrow_connect() first — no arguments needed, it auto-connects to wss://reg.ai-smith.net. "
    "Typical workflow: burrow_connect() → burrow_list_peers() → burrow_join_group('agent-pool') → collaborate."
)


async def _startup_update():
    """Check for updates on startup and auto-apply if available."""
    if not AUTO_UPDATE:
        return
    try:
        from burrow.updater import check_remote_version, self_update, current_version
        info = await check_remote_version()
        if info.get("available"):
            old_ver = info["local_version"]
            new_ver = info["remote_version"]
            log.info("Update available: %s -> %s. Auto-updating...", old_ver, new_ver)
            result = await self_update(force=False)
            if result["success"]:
                log.info("Updated to %s (sha=%s)", result["new_version"], result.get("sha"))
                import burrow.protocol
                importlib.reload(burrow.protocol)
                from burrow.protocol import VERSION
                log.info("Running with version %s", VERSION)
            else:
                log.warning("Auto-update failed: %s", result.get("error"))
    except Exception as e:
        log.debug("Startup update check failed: %s", e)


async def _auto_connect() -> Peer | None:
    """Auto-connect if not connected. Returns peer or None."""
    global _peer, _listen_task, _last_reconnect_id
    if _peer and _peer.ws:
        # Check if listen task died and restart it
        if _listen_task and _listen_task.done():
            _listen_task = asyncio.create_task(_peer.run())
            await asyncio.sleep(0.1)
        return _peer
    # Auto-connect
    name = socket.gethostname()
    token = os.environ.get("BURROW_TOKEN")
    _peer = Peer(DEFAULT_REGISTRY, name, token=token, auto_reconnect=True)
    if _last_reconnect_id:
        _peer._reconnect_id = _last_reconnect_id
    try:
        await _peer.connect()
    except Exception:
        _peer = None
        return None
    _listen_task = asyncio.create_task(_peer.run())
    try:
        await _peer.request_peers(timeout=3.0)
    except Exception:
        pass
    return _peer


def _validate_to(to: str) -> str | None:
    """Validate 'to' parameter. Returns error message or None if valid."""
    if not to or not to.strip():
        return (
            "Error: 'to' parameter is empty. You must specify a peer name or ID. "
            "Use burrow_list_peers() to see who is online and get their name/ID."
        )
    return None


# ── Core ────────────────────────────────────────────────────────────────────

@mcp.tool()
async def burrow_serve(host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> str:
    """Start a local burrow registry server in the background.
    Only needed if you want your own private registry — most users should just call burrow_connect() to join the public swarm."""
    global _server_task
    if _server_task and not _server_task.done():
        return f"Registry server already running on {host}:{port}."
    _server_task = asyncio.create_task(serve(host, port))
    return f"Registry server started on ws://{host}:{port}. Connect with: burrow_connect(url='ws://{host}:{port}')"


@mcp.tool()
async def burrow_connect(url: str = DEFAULT_REGISTRY, name: str | None = None,
                          token: str | None = None) -> str:
    """Connect to the burrow P2P swarm. No arguments needed — auto-connects to the public registry.
    After connecting, use burrow_list_peers() to see who's online, and burrow_join_group('agent-pool') to join the coordination group."""
    global _peer, _listen_task, _last_reconnect_id, _connect_url, _connect_name, _connect_token
    if _peer and _peer.ws:
        peers = len(_peer.peers)
        groups = list(_peer.groups)
        msg = f"Already connected as '{_peer.name}' (id={_peer.id}). {peers} peer(s) online."
        if groups:
            msg += f" In groups: {', '.join(groups)}."
        else:
            msg += " Tip: join a group with burrow_join_group('agent-pool')."
        return msg
    if name is None:
        name = socket.gethostname()
    if token is None:
        token = os.environ.get("BURROW_TOKEN")
    _connect_url = url
    _connect_name = name
    _connect_token = token
    _peer = Peer(url, name, token=token, auto_reconnect=True)
    if _last_reconnect_id:
        _peer._reconnect_id = _last_reconnect_id
    try:
        await _peer.connect()
    except Exception as exc:
        _peer = None
        return f"Connection failed: {exc}. Check your network or try again."
    _listen_task = asyncio.create_task(_peer.run())
    try:
        await _peer.request_peers(timeout=3.0)
    except Exception:
        pass
    peer_count = len(_peer.peers)
    msg = f"Connected to {url} as '{_peer.name}' (id={_peer.id}). {peer_count} other peer(s) online."
    if peer_count > 0:
        names = list(_peer.peers.values())[:5]
        msg += f"\nPeers: {', '.join(names)}"
    msg += "\nNext: burrow_list_peers() to see details, or burrow_join_group('agent-pool') to join the coordination group."
    return msg


@mcp.tool()
async def burrow_disconnect() -> str:
    """Disconnect from the burrow registry."""
    global _peer, _listen_task, _last_reconnect_id
    if not _peer or not _peer.ws:
        return "Not connected (nothing to disconnect)."
    name = _peer.name
    _last_reconnect_id = _peer._reconnect_id or _peer.id
    await _peer.stop()
    _peer = None
    _listen_task = None
    return f"Disconnected '{name}' from registry. Call burrow_connect() to reconnect."


@mcp.tool()
async def burrow_list_peers() -> str:
    """List all peers currently connected to the registry, with their status and capabilities.
    Auto-connects if not already connected."""
    peer = await _auto_connect()
    if not peer:
        return NOT_CONNECTED
    try:
        await peer.request_peers()
    except Exception as exc:
        return f"Failed to request peers: {exc}. Try burrow_disconnect() then burrow_connect()."
    if not peer.peers:
        return "No other peers online. You're the only one connected. Share the join instructions (prompt.md) with other agents."
    lines = []
    for pid, pname in peer.peers.items():
        status = peer.peer_status.get(pid, {}).get("status", "?")
        task = peer.peer_status.get(pid, {}).get("task", "")
        caps = peer.peer_capabilities.get(pid, {})
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
    """Send a text message to a peer by name or ID. Returns delivery status.
    Auto-connects if not already connected."""
    err = _validate_to(to)
    if err:
        return err
    peer = await _auto_connect()
    if not peer:
        return NOT_CONNECTED
    try:
        result = await peer.send_message(to, body)
    except Exception as exc:
        return f"Failed to send message: {exc}"
    if result == "delivered":
        return f"Message delivered to '{to}'."
    elif result == "queued":
        return f"'{to}' is offline. Message queued for delivery when they reconnect (up to 5 min)."
    return f"Message to '{to}': {result}"


@mcp.tool()
async def burrow_send_file(to: str, filepath: str) -> str:
    """Send a file to a peer by name or ID.
    Auto-connects if not already connected."""
    err = _validate_to(to)
    if err:
        return err
    peer = await _auto_connect()
    if not peer:
        return NOT_CONNECTED
    try:
        await peer.send_file(to, filepath)
    except FileNotFoundError:
        return f"File not found: {filepath}"
    except Exception as exc:
        return f"Failed to send file: {exc}"
    return f"File '{filepath}' sent to '{to}'."


@mcp.tool()
async def burrow_open_tunnel(to: str, local_port: int, remote_port: int) -> str:
    """Open a TCP tunnel: localhost:local_port forwards to peer's localhost:remote_port.
    Auto-connects if not already connected."""
    err = _validate_to(to)
    if err:
        return err
    peer = await _auto_connect()
    if not peer:
        return NOT_CONNECTED
    try:
        await peer.open_tunnel(to, local_port, remote_port)
    except Exception as exc:
        return f"Failed to open tunnel: {exc}"
    return f"Tunnel open: localhost:{local_port} -> {to}:{remote_port}"


# ── Capabilities & Presence ─────────────────────────────────────────────────

@mcp.tool()
async def burrow_announce_capabilities(
    tools: str = "", model: str = "", skills: str = "",
    tags: str = "", status: str = "idle"
) -> str:
    """Announce this agent's capabilities to the swarm so other peers can discover you.
    Comma-separated lists for tools, skills, tags. Auto-connects if not already connected."""
    peer = await _auto_connect()
    if not peer:
        return NOT_CONNECTED
    caps = {"status": status}
    if tools:
        caps["tools"] = [t.strip() for t in tools.split(",")]
    if model:
        caps["model"] = model
    if skills:
        caps["skills"] = [s.strip() for s in skills.split(",")]
    if tags:
        caps["tags"] = [t.strip() for t in tags.split(",")]
    await peer.announce_capabilities(caps)
    return f"Capabilities announced: {caps}. Other peers can now find you with burrow_find_peers()."


@mcp.tool()
async def burrow_find_peers(
    required_tools: str = "", required_skills: str = "", required_tags: str = ""
) -> str:
    """Find peers matching capability requirements. Comma-separated lists.
    Auto-connects if not already connected."""
    peer = await _auto_connect()
    if not peer:
        return NOT_CONNECTED
    tools = [t.strip() for t in required_tools.split(",") if t.strip()] or None
    skills = [s.strip() for s in required_skills.split(",") if s.strip()] or None
    tags = [t.strip() for t in required_tags.split(",") if t.strip()] or None
    matches = await peer.query_capabilities(tools, skills, tags)
    if not matches:
        return "No matching peers found. Peers must call burrow_announce_capabilities() to be discoverable."
    lines = []
    for m in matches:
        caps = m.get("capabilities", {})
        status = m.get("status", "?")
        lines.append(f"  {m['name']} ({m['id']}) [{status}]: {caps}")
    return f"{len(matches)} matching peer(s):\n" + "\n".join(lines)


@mcp.tool()
async def burrow_update_status(status: str, task: str = "") -> str:
    """Update presence status. Status: idle, busy, working. Task: description of current work.
    Auto-connects if not already connected."""
    peer = await _auto_connect()
    if not peer:
        return NOT_CONNECTED
    await peer.update_status(status, task)
    return f"Status updated: {status}" + (f" ({task})" if task else "")


# ── Groups / Channels ──────────────────────────────────────────────────────

@mcp.tool()
async def burrow_join_group(group: str) -> str:
    """Join a named group/channel. Groups are created automatically when the first peer joins.
    Messages can be broadcast to all group members. Auto-connects if not already connected."""
    peer = await _auto_connect()
    if not peer:
        return NOT_CONNECTED
    if not group or not group.strip():
        return "Error: group name cannot be empty. Common groups: 'agent-pool', 'dev', 'tasks'."
    await peer.join_group(group)
    return (f"Joined group '{group}'. "
            f"Use burrow_group_message('{group}', 'hello') to message the group, "
            f"or burrow_group_members('{group}') to see who's in it.")


@mcp.tool()
async def burrow_leave_group(group: str) -> str:
    """Leave a group/channel."""
    peer = await _auto_connect()
    if not peer:
        return NOT_CONNECTED
    await peer.leave_group(group)
    return f"Left group '{group}'."


@mcp.tool()
async def burrow_group_message(group: str, body: str) -> str:
    """Send a message to all members of a group. You must join the group first with burrow_join_group().
    Auto-connects if not already connected."""
    peer = await _auto_connect()
    if not peer:
        return NOT_CONNECTED
    if group not in peer.groups:
        await peer.join_group(group)
    try:
        await peer.send_group_message(group, body, wait_ack=True)
    except Exception as exc:
        return f"Failed: {exc}"
    return f"Message sent to group '{group}'."


@mcp.tool()
async def burrow_list_groups() -> str:
    """List all active groups and their member counts. Groups only exist while they have members —
    if this returns empty, create one with burrow_join_group('agent-pool').
    Auto-connects if not already connected."""
    peer = await _auto_connect()
    if not peer:
        return NOT_CONNECTED
    groups = await peer.list_groups()
    if not groups:
        return ("No active groups. Groups are created when the first peer joins. "
                "Create one with: burrow_join_group('agent-pool')")
    lines = [f"  {name}: {count} member(s)" for name, count in groups.items()]
    msg = f"{len(groups)} group(s):\n" + "\n".join(lines)
    # Check which ones we're in
    my_groups = peer.groups
    if my_groups:
        msg += f"\nYou are in: {', '.join(my_groups)}"
    else:
        msg += "\nYou haven't joined any groups yet. Use burrow_join_group() to join one."
    return msg


@mcp.tool()
async def burrow_group_members(group: str) -> str:
    """List members of a specific group.
    Auto-connects if not already connected."""
    peer = await _auto_connect()
    if not peer:
        return NOT_CONNECTED
    members = await peer.get_group_members(group)
    if not members:
        return (f"No members in group '{group}'. Either the group doesn't exist yet, "
                f"or everyone left. Join it with: burrow_join_group('{group}')")
    lines = [f"  {m['name']} ({m['id']}) [{m.get('status', '?')}]" for m in members]
    in_group = group in peer.groups
    msg = f"{len(members)} member(s) in '{group}':\n" + "\n".join(lines)
    if not in_group:
        msg += f"\nNote: You are NOT in this group. Join with: burrow_join_group('{group}')"
    return msg


# ── Shared State ────────────────────────────────────────────────────────────

@mcp.tool()
async def burrow_state_set(key: str, value: str, group: str = "") -> str:
    """Set a shared key-value pair visible to all peers (or group members if group specified).
    Auto-connects if not already connected."""
    peer = await _auto_connect()
    if not peer:
        return NOT_CONNECTED
    await peer.set_state(key, value, group or None)
    scope = f"group '{group}'" if group else "global"
    return f"State set: {key} = {value} ({scope})"


@mcp.tool()
async def burrow_state_get(key: str, group: str = "") -> str:
    """Get a shared state value by key.
    Auto-connects if not already connected."""
    peer = await _auto_connect()
    if not peer:
        return NOT_CONNECTED
    value = await peer.get_state(key, group or None)
    if value is None:
        return f"Key '{key}' not found. Use burrow_state_set() to create it, or burrow_state_sync() to see all keys."
    return f"{key} = {value}"


@mcp.tool()
async def burrow_state_sync(group: str = "") -> str:
    """Sync all shared state from the server. Returns the full key-value store.
    Auto-connects if not already connected."""
    peer = await _auto_connect()
    if not peer:
        return NOT_CONNECTED
    state = await peer.sync_state(group or None)
    if not state:
        return "No shared state. Use burrow_state_set(key, value) to create entries."
    lines = [f"  {k} = {v}" for k, v in state.items()]
    scope = f"group '{group}'" if group else "global"
    return f"Shared state ({scope}, {len(state)} keys):\n" + "\n".join(lines)


# ── Task Coordination ───────────────────────────────────────────────────────

@mcp.tool()
async def burrow_broadcast_task(task: str, timeout_s: float = 30.0,
                                 required_skills: str = "") -> str:
    """Broadcast a task to all peers (optionally filtered by skills) and collect responses.
    Auto-connects if not already connected."""
    peer = await _auto_connect()
    if not peer:
        return NOT_CONNECTED
    if not peer.peers:
        return "No peers online to broadcast to. Wait for peers to connect, or check with burrow_list_peers()."
    skills = [s.strip() for s in required_skills.split(",") if s.strip()] or None
    responses = await peer.broadcast_task(task, timeout_s, skills)
    if not responses:
        return "No responses received. Peers may not have a task handler registered. Try burrow_delegate_task() to target a specific peer."
    lines = [f"  {r['from']}: {r['response']}" for r in responses]
    return f"{len(responses)} response(s):\n" + "\n".join(lines)


@mcp.tool()
async def burrow_delegate_task(to: str, task: str, context: str = "",
                                timeout_s: float = 120.0) -> str:
    """Delegate a task to a specific peer and wait for result.
    Auto-connects if not already connected."""
    err = _validate_to(to)
    if err:
        return err
    peer = await _auto_connect()
    if not peer:
        return NOT_CONNECTED
    ctx = {"description": context} if context else {}
    result = await peer.delegate_task(to, task, context=ctx, timeout_s=timeout_s)
    status = result.get("status", "unknown")
    if status == "timeout":
        return f"Task timed out after {timeout_s}s. The peer may be busy or not handling tasks. Check with burrow_list_peers()."
    return f"Task {status}: {result.get('result', 'no result')}"


@mcp.tool()
async def burrow_return_result(to: str, task_id: str, result: str,
                                success: bool = True) -> str:
    """Return a result for a delegated task back to the assigning peer.
    Auto-connects if not already connected."""
    err = _validate_to(to)
    if err:
        return err
    peer = await _auto_connect()
    if not peer:
        return NOT_CONNECTED
    await peer.return_task_result(to, task_id, result, success)
    return f"Result sent for task {task_id}."


@mcp.tool()
async def burrow_get_pending_tasks() -> str:
    """Get pending tasks that have been assigned or broadcast to this agent.
    Auto-connects if not already connected."""
    peer = await _auto_connect()
    if not peer:
        return NOT_CONNECTED
    if not peer.pending_tasks:
        return "No pending tasks. Tasks arrive when another peer calls burrow_delegate_task() or burrow_broadcast_task() targeting you."
    tasks = peer.pending_tasks[:]
    peer.pending_tasks.clear()
    lines = []
    for t in tasks:
        lines.append(f"  [{t['type']}] {t['task_id']} from {t['from_name']}: {t['task']}")
    return (f"{len(tasks)} pending task(s):\n" + "\n".join(lines) +
            "\nUse burrow_return_result(to, task_id, result) to respond.")


# ── Voting / Consensus ──────────────────────────────────────────────────────

@mcp.tool()
async def burrow_propose_vote(proposal: str, options: str = "approve,reject,abstain",
                               deadline_s: float = 30.0) -> str:
    """Propose a vote to all peers. Options are comma-separated.
    Auto-connects if not already connected."""
    peer = await _auto_connect()
    if not peer:
        return NOT_CONNECTED
    if not peer.peers:
        return "No peers online to vote. Wait for peers to connect first."
    opt_list = [o.strip() for o in options.split(",")]
    result = await peer.propose_vote(proposal, opt_list, deadline_s)
    tally_str = ", ".join(f"{k}: {v}" for k, v in result["tally"].items())
    return (f"Vote complete. Outcome: {result['outcome']}\n"
            f"Tally ({result['total']} votes): {tally_str}")


@mcp.tool()
async def burrow_cast_vote(to: str, vote_id: str, choice: str,
                            reason: str = "") -> str:
    """Cast a vote on a proposal from another peer.
    Auto-connects if not already connected."""
    err = _validate_to(to)
    if err:
        return err
    peer = await _auto_connect()
    if not peer:
        return NOT_CONNECTED
    await peer.cast_vote(to, vote_id, choice, reason)
    return f"Vote '{choice}' cast on {vote_id}."


# ── Leader Election ─────────────────────────────────────────────────────────

@mcp.tool()
async def burrow_elect_leader() -> str:
    """Trigger a leader election in the swarm. Returns the elected leader.
    Auto-connects if not already connected."""
    peer = await _auto_connect()
    if not peer:
        return NOT_CONNECTED
    result = await peer.start_election()
    if result["leader_id"] == peer.id:
        return f"This agent was elected leader (id={peer.id})."
    return f"Leader elected: {result['leader_name']} (id={result['leader_id']})"


@mcp.tool()
async def burrow_get_leader() -> str:
    """Return the current swarm leader.
    Auto-connects if not already connected."""
    peer = await _auto_connect()
    if not peer:
        return NOT_CONNECTED
    if not peer.leader_id:
        return "No leader elected yet. Call burrow_elect_leader() to start an election."
    is_me = " (this agent)" if peer.is_leader else ""
    return f"Leader: {peer.leader_name} (id={peer.leader_id}){is_me}"


# ── Distributed Jobs ───────────────────────────────────────────────────────

@mcp.tool()
async def burrow_submit_job(to: str, func: str, args: str = "[]",
                             kwargs: str = "{}", runtime: str = "builtin",
                             timeout_s: float = 120.0) -> str:
    """Submit a distributed job to a peer. func is 'module.function' path (e.g. 'math.factorial').
    Runtime: 'builtin' (default), 'ray', or 'dask'. Args/kwargs as JSON strings.
    Auto-connects if not already connected."""
    err = _validate_to(to)
    if err:
        return err
    peer = await _auto_connect()
    if not peer:
        return NOT_CONNECTED
    import json as _json
    try:
        parsed_args = _json.loads(args)
        parsed_kwargs = _json.loads(kwargs)
    except _json.JSONDecodeError as e:
        return f"Invalid JSON: {e}. Args must be a JSON array like '[1, 2]', kwargs a JSON object like '{{}}'."
    try:
        result = await peer.submit_job(
            to, func, args=parsed_args, kwargs=parsed_kwargs,
            runtime=runtime, timeout=timeout_s)
        status = result.get("status", "unknown")
        if status in ("completed", "finished"):
            return f"Job completed: {result.get('result')}"
        elif status == "failed":
            return f"Job failed: {result.get('error', 'unknown error')}"
        elif status == "timeout":
            return f"Job timed out after {timeout_s}s. Job ID: {result.get('job_id')}. The peer may be overloaded."
        return f"Job status: {status}. Result: {result}"
    except Exception as exc:
        return f"Failed to submit job: {exc}"


@mcp.tool()
async def burrow_job_status(to: str, job_id: str) -> str:
    """Check the status of a previously submitted job.
    Auto-connects if not already connected."""
    err = _validate_to(to)
    if err:
        return err
    peer = await _auto_connect()
    if not peer:
        return NOT_CONNECTED
    result = await peer.check_job_status(to, job_id)
    return f"Job {job_id}: {result.get('status', 'unknown')}"


@mcp.tool()
async def burrow_cancel_job(to: str, job_id: str) -> str:
    """Cancel a running job on a peer.
    Auto-connects if not already connected."""
    err = _validate_to(to)
    if err:
        return err
    peer = await _auto_connect()
    if not peer:
        return NOT_CONNECTED
    await peer.cancel_job(to, job_id)
    return f"Cancel request sent for job {job_id}."


@mcp.tool()
async def burrow_list_jobs() -> str:
    """List all jobs tracked by the server.
    Auto-connects if not already connected."""
    peer = await _auto_connect()
    if not peer:
        return NOT_CONNECTED
    jobs = await peer.list_all_jobs()
    if not jobs:
        return "No jobs tracked. Submit one with burrow_submit_job()."
    lines = []
    for j in jobs:
        if j:
            lines.append(f"  {j.get('job_id','?')} [{j.get('status','?')}] "
                         f"{j.get('func', j.get('payload','?'))}")
    return f"{len(lines)} job(s):\n" + "\n".join(lines)


@mcp.tool()
async def burrow_init_runtime(runtime: str, address: str = "") -> str:
    """Initialize a distributed runtime: 'ray' or 'dask'. Optionally provide scheduler/cluster address.
    Auto-connects if not already connected."""
    peer = await _auto_connect()
    if not peer:
        return NOT_CONNECTED
    addr = address or None
    if runtime == "ray":
        ok = peer.init_ray(addr)
        return f"Ray {'initialized successfully' if ok else 'failed — is ray installed? pip install ray[default]'}."
    elif runtime == "dask":
        ok = peer.init_dask(addr)
        return f"Dask {'initialized successfully' if ok else 'failed — is dask installed? pip install dask[distributed]'}."
    return f"Unknown runtime: {runtime}. Use 'ray' or 'dask'."


@mcp.tool()
async def burrow_available_runtimes() -> str:
    """List available distributed runtimes on this peer.
    Auto-connects if not already connected."""
    peer = await _auto_connect()
    if not peer:
        return NOT_CONNECTED
    runtimes = peer.available_runtimes
    return f"Available runtimes: {', '.join(runtimes)}"


@mcp.tool()
async def burrow_submit_script(to: str, script_path: str, args: str = "[]",
                                timeout_s: float = 300.0) -> str:
    """Upload a local script file to a remote peer and execute it.
    Supports .py (Python), .sh (Bash), or any executable.
    Args is a JSON array of command-line arguments.
    Auto-connects if not already connected."""
    err = _validate_to(to)
    if err:
        return err
    peer = await _auto_connect()
    if not peer:
        return NOT_CONNECTED
    import json as _json
    try:
        parsed_args = _json.loads(args)
    except _json.JSONDecodeError as e:
        return f"Invalid JSON args: {e}. Must be a JSON array like '[\"--verbose\"]'."
    try:
        result = await peer.submit_script(
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
        return f"Script not found: {script_path}. Provide an absolute path to the script file."
    except Exception as exc:
        return f"Failed to submit script: {exc}"


@mcp.tool()
async def burrow_submit_batch(to: str, func: str, args_list: str,
                               runtime: str = "builtin",
                               max_retries: int = 0) -> str:
    """Submit a batch of jobs to a peer. args_list is JSON array of arg arrays.
    Example: '[[1],[2],[3]]' submits 3 jobs with different args.
    Auto-connects if not already connected."""
    err = _validate_to(to)
    if err:
        return err
    peer = await _auto_connect()
    if not peer:
        return NOT_CONNECTED
    import json as _json
    try:
        parsed = _json.loads(args_list)
    except _json.JSONDecodeError as e:
        return f"Invalid JSON: {e}. Must be a JSON array of arrays like '[[1],[2],[3]]'."
    results = []
    for i, args in enumerate(parsed):
        try:
            r = await peer.submit_job(to, func, args=args, runtime=runtime, timeout=120.0)
            results.append(f"  [{i}] {r.get('status','?')}: {r.get('result', r.get('error',''))}")
        except Exception as exc:
            results.append(f"  [{i}] error: {exc}")
    return f"Batch of {len(parsed)} jobs:\n" + "\n".join(results)


@mcp.tool()
async def burrow_map_job(to: str, func: str, inputs: str,
                          runtime: str = "builtin") -> str:
    """Map a function over a list of inputs on a remote peer.
    inputs is a JSON array. Each element is passed as a single arg.
    Example: func='math.factorial', inputs='[5,10,20]'
    Auto-connects if not already connected."""
    err = _validate_to(to)
    if err:
        return err
    peer = await _auto_connect()
    if not peer:
        return NOT_CONNECTED
    import json as _json
    try:
        parsed = _json.loads(inputs)
    except _json.JSONDecodeError as e:
        return f"Invalid JSON: {e}. Must be a JSON array like '[5, 10, 20]'."
    results = []
    for inp in parsed:
        try:
            r = await peer.submit_job(to, func, args=[inp], runtime=runtime, timeout=60.0)
            results.append(r.get("result", r.get("error", "?")))
        except Exception as exc:
            results.append(f"error: {exc}")
    return f"Map results ({len(results)} items):\n" + "\n".join(
        f"  {inp} -> {res}" for inp, res in zip(parsed, results))


@mcp.tool()
async def burrow_job_logs(job_id: str) -> str:
    """Get execution logs for a local job.
    Auto-connects if not already connected."""
    peer = await _auto_connect()
    if not peer:
        return NOT_CONNECTED
    logs = peer._executor.get_job_logs(job_id)
    if not logs:
        return f"No logs for job {job_id}. The job may not exist or may have been purged."
    return f"Logs for {job_id}:\n" + "\n".join(f"  {l}" for l in logs)


@mcp.tool()
async def burrow_job_stats() -> str:
    """Get aggregate statistics for all local jobs.
    Auto-connects if not already connected."""
    peer = await _auto_connect()
    if not peer:
        return NOT_CONNECTED
    import json as _json
    stats = peer._executor.stats()
    return f"Job statistics:\n{_json.dumps(stats, indent=2)}"


@mcp.tool()
async def burrow_purge_jobs(status: str = "") -> str:
    """Remove completed/failed/cancelled jobs from local tracking.
    Auto-connects if not already connected."""
    peer = await _auto_connect()
    if not peer:
        return NOT_CONNECTED
    count = peer._executor.purge(status=status or None)
    return f"Purged {count} jobs."


# ── Server-Side Work Queue ─────────────────────────────────────────────────

@mcp.tool()
async def burrow_queue_push(queue: str, payload: str, priority: int = 0) -> str:
    """Push a job to a named server-side work queue. Payload is JSON.
    Workers pull jobs from the queue and report results.
    Auto-connects if not already connected."""
    peer = await _auto_connect()
    if not peer:
        return NOT_CONNECTED
    import json as _json
    try:
        data = _json.loads(payload)
    except _json.JSONDecodeError as e:
        return f"Invalid JSON payload: {e}. Must be valid JSON like '{{\"task\": \"build\"}}'."
    job_id = await peer.queue_push(queue, data, priority)
    return f"Job {job_id} pushed to queue '{queue}' (priority={priority}). Workers can pull it with burrow_queue_pull('{queue}')."


@mcp.tool()
async def burrow_queue_pull(queue: str) -> str:
    """Pull the next available job from a server-side work queue.
    Auto-connects if not already connected."""
    peer = await _auto_connect()
    if not peer:
        return NOT_CONNECTED
    item = await peer.queue_pull(queue)
    if not item:
        return f"No jobs available in queue '{queue}'. Push one with burrow_queue_push('{queue}', '{{\"task\":\"...\"}}')"
    import json as _json
    return (f"Job {item['job_id']} from queue '{queue}':\n"
            f"  Payload: {_json.dumps(item.get('payload', {}))}\n"
            f"  Priority: {item.get('priority', 0)}\n"
            f"When done, acknowledge with: burrow_queue_ack('{queue}', '{item['job_id']}', 'result here')")


@mcp.tool()
async def burrow_queue_ack(queue: str, job_id: str, result: str = "",
                            success: bool = True) -> str:
    """Acknowledge completion of a queue job. Reports result back to submitter.
    Auto-connects if not already connected."""
    peer = await _auto_connect()
    if not peer:
        return NOT_CONNECTED
    await peer.queue_ack(queue, job_id, result=result or None, success=success)
    status = "completed" if success else "failed"
    return f"Job {job_id} acknowledged as {status}."


@mcp.tool()
async def burrow_queue_status(queue: str = "") -> str:
    """Get status of server-side work queues.
    Auto-connects if not already connected."""
    peer = await _auto_connect()
    if not peer:
        return NOT_CONNECTED
    status = await peer.queue_status(queue or None)
    if not status:
        return "No queues active. Create one by pushing a job: burrow_queue_push('my-queue', '{\"task\":\"...\"}')"
    import json as _json
    return f"Queue status:\n{_json.dumps(status, indent=2)}"


@mcp.tool()
async def burrow_register_worker(queues: str = "", capabilities: str = "") -> str:
    """Register this agent as a worker for server-side queues.
    Comma-separated queue names and capabilities.
    Auto-connects if not already connected."""
    peer = await _auto_connect()
    if not peer:
        return NOT_CONNECTED
    q_list = [q.strip() for q in queues.split(",") if q.strip()] or None
    caps = {}
    if capabilities:
        for cap in capabilities.split(","):
            cap = cap.strip()
            if cap:
                caps[cap] = True
    await peer.register_worker(q_list, caps or None)
    return (f"Registered as worker for queues: {q_list or 'all'}. "
            f"Pull work with burrow_queue_pull('queue-name').")


# ── Remote Execution ────────────────────────────────────────────────────────

@mcp.tool()
async def burrow_exec(to: str, command: str, timeout_s: float = 60.0,
                       cwd: str = "", env: str = "{}") -> str:
    """Execute a shell command on a remote peer. Returns stdout, stderr, exit code.
    Like SSH but over the P2P relay — no port forwarding needed.
    Auto-connects if not already connected."""
    err = _validate_to(to)
    if err:
        return err
    peer = await _auto_connect()
    if not peer:
        return NOT_CONNECTED
    import json as _json
    try:
        env_dict = _json.loads(env) if env and env != "{}" else None
    except _json.JSONDecodeError:
        env_dict = None
    result = await peer.exec_command(
        to, command, timeout=timeout_s,
        cwd=cwd or None, env=env_dict)
    if result.get("error"):
        return f"Exec error: {result['error']}"
    parts = [f"Exit code: {result['exit_code']}"]
    if result.get("stdout"):
        parts.append(f"stdout:\n{result['stdout']}")
    if result.get("stderr"):
        parts.append(f"stderr:\n{result['stderr']}")
    return "\n".join(parts)


@mcp.tool()
async def burrow_reverse_tunnel(to: str, remote_port: int,
                                  local_port: int) -> str:
    """Open a reverse tunnel: the remote peer listens on remote_port
    and forwards all TCP traffic back to your local_port.
    Example for SSH: burrow_reverse_tunnel('peer', 2222, 22)
    Auto-connects if not already connected."""
    err = _validate_to(to)
    if err:
        return err
    peer = await _auto_connect()
    if not peer:
        return NOT_CONNECTED
    try:
        tid = await peer.reverse_tunnel(to, remote_port, local_port)
        return (f"Reverse tunnel {tid}: {to} listening on :{remote_port} "
                f"-> your localhost:{local_port}")
    except Exception as exc:
        return f"Failed to open reverse tunnel: {exc}"


# ── Self-Update ─────────────────────────────────────────────────────────────

@mcp.tool()
async def burrow_check_update() -> str:
    """Check if a newer version of burrow is available on GitHub."""
    from burrow.updater import check_remote_version
    info = await check_remote_version()
    if info.get("error"):
        return f"Check failed: {info['error']}"
    if info["available"]:
        lines = [f"Update available: {info['local_version']} -> {info['remote_version']}"]
        if info.get("changelog"):
            lines.append(f"Changes:\n{info['changelog']}")
        lines.append("Run burrow_self_update() to apply.")
        return "\n".join(lines)
    return (f"Up to date: v{info['local_version']} "
            f"(sha={info.get('sha', '?')}, branch={info.get('branch', '?')})")


@mcp.tool()
async def burrow_self_update(force: bool = False) -> str:
    """Pull latest code from GitHub and reinstall. Use force=True to override local changes.
    After update, the new version is hot-reloaded."""
    from burrow.updater import self_update
    result = await self_update(force=force)
    if not result["success"]:
        return f"Update failed: {result['error']}"
    msg = f"Updated: {result['old_version']} -> {result['new_version']} (sha={result.get('sha', '?')})"
    if result.get("needs_restart"):
        import burrow.protocol
        importlib.reload(burrow.protocol)
        msg += f"\nHot-reloaded to v{burrow.protocol.VERSION}."
    if _peer and _peer.ws:
        try:
            from burrow import protocol as proto
            await _peer._send(proto.update_status(
                result["new_version"], "updated"))
        except Exception:
            pass
    return msg


@mcp.tool()
async def burrow_version() -> str:
    """Show the current burrow version, git SHA, and branch."""
    from burrow.updater import current_version, git_current_sha, git_current_branch
    return (f"burrow v{current_version()} "
            f"(sha={git_current_sha()}, branch={git_current_branch()})")


def main():
    if AUTO_UPDATE:
        import asyncio as _asyncio
        try:
            loop = _asyncio.new_event_loop()
            loop.run_until_complete(_startup_update())
            loop.close()
        except Exception as e:
            log.debug("Startup update skipped: %s", e)

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
