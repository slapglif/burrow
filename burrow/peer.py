"""Burrow P2P client — async Peer that connects to a registry server."""

import asyncio
import json
import base64
import os
import random
import time
import uuid
from pathlib import Path
from inspect import isawaitable

from burrow.desktop_session import (
    DesktopFrame,
    DesktopSession,
    DesktopTarget,
    PermissionState,
    PermissionTransition,
    PrivacyState,
    ReconnectState,
)

from burrow import desktop

import websockets
from websockets.asyncio.client import connect

from burrow import protocol
from burrow.distributed import JobExecutor, JobState


RECEIVE_DIR = Path(__file__).parent.parent / "burrow-received"

# Safe environment variables to pass to exec/script execution
_SAFE_ENV_KEYS = {"PATH", "HOME", "USER", "LANG", "LC_ALL", "TERM", "SHELL",
                  "PYTHONPATH", "VIRTUAL_ENV", "UV_CACHE_DIR"}


class Peer:
    BACKOFF_BASE = 1.0
    BACKOFF_MAX = 60.0
    BACKOFF_FACTOR = 2.0

    def __init__(self, uri: str, name: str, *,
                 token: str | None = None,
                 capabilities: dict | None = None,
                 auto_reconnect: bool = False):
        self.uri = uri
        self.name = name
        self.token = token
        self.id = None
        self.ws = None
        self.capabilities = capabilities or {}
        self.auto_reconnect = auto_reconnect

        # Peer tracking
        self.peers = {}               # id -> name
        self.peer_capabilities = {}   # id -> capabilities dict
        self.peer_status = {}         # id -> {"status", "task"}

        # Callbacks
        self.on_message = None        # (from_name, body)
        self.on_file = None           # (from_name, filepath)
        self.on_task_broadcast = None # (from_name, task_id, task) -> str|None
        self.on_task_assigned = None  # (from_name, task_id, task, context)
        self.on_vote_request = None   # (from_name, vote_id, proposal, options) -> str
        self.on_leader_elected = None # (leader_id, leader_name, is_self)
        self.on_group_message = None  # (group, from_name, body)
        self.on_state_change = None   # (key, value, group)
        self.on_desktop_session = None  # (event, session_dict, context) -> None
        self.on_desktop_frame_request = None  # (session_dict, context) -> frame dict | DesktopFrame | None
        self.on_desktop_input = None   # (session_dict, action, context) -> None

        # Internal state
        self._transfers = {}          # transfer_id -> {name, size, chunks, from_name}
        self._tunnels = {}            # tunnel_id -> {reader, writer}
        self._pending_acks = {}       # msg_id -> Future
        self._pending_requests = {}   # req_id -> Future
        self._broadcast_responses = {} # task_id -> list
        self._broadcast_events = {}   # task_id -> Event
        self._delegated_tasks = {}    # task_id -> {to, task, status, result}
        self._task_events = {}        # task_id -> Event
        self._active_votes = {}       # vote_id -> {votes, event, proposal}
        self._reconnect_id = None
        self._stop_event = asyncio.Event()
        self._keepalive_task = None
        self._last_pong = 0.0

        # Leader election
        self.leader_id = None
        self.leader_name = None
        self.is_leader = False
        self._election_suppressed = False
        self._election_event = None

        # Groups and state
        self.groups = set()
        self.shared_state = {}        # scope -> {key: value}

        # Incoming task queue (for MCP)
        self.pending_tasks = []       # [{from, task_id, task, context}]

        # Distributed job execution
        self._executor = JobExecutor()
        self._job_results = {}        # job_id -> Future (for submitted jobs awaiting results)
        self._job_callbacks = {}      # job_id -> callback
        self.on_job_received = None   # (from_name, job_id, func, args, kwargs) -> auto-execute if None

        # Remote execution
        self._exec_results = {}       # exec_id -> Future
        self.exec_enabled = True      # whether to accept incoming exec requests
        self.on_exec_request = None   # (from_name, exec_id, command) -> allow/deny

        # Remote desktop orchestration (control plane only; media stays in tunneled backend)
        self._desktop_sessions = {}   # session_id -> metadata
        self._desktop_open_waiters = {}   # session_id -> Future
        self._desktop_frame_waiters = {}  # session_id -> Future

        # Update notifications
        self.on_update_available = None  # (version, changelog) -> None

    # --- Core lifecycle ---

    async def connect(self):
        """Connect to the registry and register this peer."""
        self.ws = await connect(self.uri)
        await self._send(protocol.register(
            self.name, token=self.token,
            reconnect_id=self._reconnect_id,
            capabilities=self.capabilities or None,
        ))
        raw = await self.ws.recv()
        resp = json.loads(raw)
        if resp.get("type") == protocol.REGISTERED:
            self.id = resp["id"]
            self._reconnect_id = self.id
            peer_list = resp.get("peers", [])
            if isinstance(peer_list, list):
                self.peers = {p["id"]: p["name"] for p in peer_list}
                for p in peer_list:
                    self.peer_capabilities[p["id"]] = p.get("capabilities", {})
                    self.peer_status[p["id"]] = {
                        "status": p.get("status", "idle"),
                        "task": p.get("task", ""),
                    }
            else:
                self.peers = peer_list
            self._last_pong = time.monotonic()
        else:
            raise RuntimeError(f"Registration failed: {resp}")

    async def listen(self):
        """Main receive loop — dispatch incoming messages by type."""
        try:
            self._keepalive_task = asyncio.create_task(self._keepalive_loop())
            await self._listen_loop()
        finally:
            if self._keepalive_task:
                self._keepalive_task.cancel()
            self._cleanup()

    async def run(self):
        """Connect + listen with auto-reconnect and state restoration."""
        delay = self.BACKOFF_BASE
        first_run = True
        while not self._stop_event.is_set():
            try:
                if first_run and self.ws:
                    # Already connected (caller did connect() before run())
                    first_run = False
                else:
                    first_run = False
                    await self.connect()
                    # Restore state after reconnect
                    await self._restore_state()
                delay = self.BACKOFF_BASE
                await self.listen()
            except (websockets.ConnectionClosed, ConnectionError, OSError) as exc:
                if not self.auto_reconnect or self._stop_event.is_set():
                    raise
                jitter = random.uniform(0, delay * 0.1)
                wait = min(delay + jitter, self.BACKOFF_MAX)
                print(f"Connection lost ({exc}). Reconnecting in {wait:.1f}s...")
                await asyncio.sleep(wait)
                delay = min(delay * self.BACKOFF_FACTOR, self.BACKOFF_MAX)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Catch ALL exceptions so the loop doesn't die silently
                if not self.auto_reconnect or self._stop_event.is_set():
                    raise
                print(f"Listen loop error ({type(exc).__name__}: {exc}). Reconnecting in {delay:.1f}s...")
                await asyncio.sleep(delay)
                delay = min(delay * self.BACKOFF_FACTOR, self.BACKOFF_MAX)
            finally:
                self._cleanup()

    async def stop(self):
        """Signal the run loop to stop."""
        self._stop_event.set()
        self.auto_reconnect = False
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass

    async def _restore_state(self):
        """Re-join groups, re-announce capabilities, and refresh peers after reconnect."""
        # Re-join all groups we were in
        for group in list(self.groups):
            try:
                await self._send(protocol.group_join(group))
            except Exception:
                pass
        # Re-announce capabilities if we had any
        if self.capabilities:
            try:
                await self._send(protocol.capability_announce(self.capabilities))
            except Exception:
                pass
        # Refresh peer list
        try:
            await self.request_peers(timeout=3.0)
        except Exception:
            pass
        print(f"Reconnected as '{self.name}' (id={self.id})")

    def _cleanup(self):
        """Clean up transient state — preserve groups/capabilities for reconnect."""
        self._transfers.clear()
        for tunnel in self._tunnels.values():
            if tunnel.get("writer"):
                tunnel["writer"].close()
            if tunnel.get("server"):
                tunnel["server"].close()
        self._tunnels.clear()
        # Cancel all pending futures so callers don't hang
        for fut in self._pending_acks.values():
            if not fut.done():
                fut.set_exception(ConnectionError("disconnected"))
        self._pending_acks.clear()
        for fut in self._pending_requests.values():
            if not fut.done():
                fut.set_exception(ConnectionError("disconnected"))
        self._pending_requests.clear()
        for fut in self._job_results.values():
            if not fut.done():
                fut.set_exception(ConnectionError("disconnected"))
        self._job_results.clear()
        for fut in self._exec_results.values():
            if not fut.done():
                fut.set_exception(ConnectionError("disconnected"))
        self._exec_results.clear()
        for fut in self._desktop_open_waiters.values():
            if not fut.done():
                fut.set_exception(ConnectionError("disconnected"))
        self._desktop_open_waiters.clear()
        for fut in self._desktop_frame_waiters.values():
            if not fut.done():
                fut.set_exception(ConnectionError("disconnected"))
        self._desktop_frame_waiters.clear()
        for session in self._desktop_sessions.values():
            server = session.get("tunnel_server")
            if server:
                server.close()
        self._desktop_sessions.clear()

    async def _keepalive_loop(self):
        while True:
            await asyncio.sleep(protocol.DEFAULT_KEEPALIVE_INTERVAL)
            try:
                await self._send(protocol.ping())
            except Exception:
                break
            if time.monotonic() - self._last_pong > (
                protocol.DEFAULT_KEEPALIVE_INTERVAL + protocol.DEFAULT_KEEPALIVE_TIMEOUT
            ):
                print("Keepalive timeout.")
                if self.ws:
                    await self.ws.close()
                break

    async def _listen_loop(self):
        async for raw in self.ws:
            data = json.loads(raw)
            kind = data.get("type")

            # Request-response correlation
            req_id = data.get("req_id")
            if req_id and req_id in self._pending_requests:
                fut = self._pending_requests.pop(req_id)
                if not fut.done():
                    fut.set_result(data)
                continue

            # --- Delivery confirmations ---
            if kind == protocol.ACK:
                fut = self._pending_acks.pop(data.get("msg_id"), None)
                if fut and not fut.done():
                    fut.set_result("delivered")

            elif kind == protocol.NACK:
                fut = self._pending_acks.pop(data.get("msg_id"), None)
                if fut and not fut.done():
                    fut.set_result(f"nack: {data.get('reason', 'unknown')}")

            elif kind == protocol.QUEUED:
                fut = self._pending_acks.pop(data.get("msg_id"), None)
                if fut and not fut.done():
                    fut.set_result("queued")

            # --- Messages ---
            elif kind == protocol.MSG:
                if self.on_message:
                    self.on_message(data.get("from_name", "?"), data["body"])
                else:
                    print(f"[{data.get('from_name', '?')}] {data['body']}")

            # --- Peer events ---
            elif kind == protocol.PEER_JOINED:
                if "group" not in data:
                    self.peers[data["id"]] = data["name"]
                    self.peer_capabilities[data["id"]] = data.get("capabilities", {})
                    self.peer_status[data["id"]] = {"status": "idle", "task": ""}
                    print(f"+ {data['name']} joined")

            elif kind == protocol.PEER_LEFT:
                if "group" not in data:
                    name = self.peers.pop(data["id"], data.get("name", "?"))
                    self.peer_capabilities.pop(data["id"], None)
                    self.peer_status.pop(data["id"], None)
                    print(f"- {name} left")
                    # Auto-election if leader left
                    if data["id"] == self.leader_id:
                        asyncio.create_task(self.start_election())

            elif kind == protocol.PEERS:
                peer_list = data.get("peers", [])
                if isinstance(peer_list, list):
                    self.peers = {p["id"]: p["name"] for p in peer_list}
                    for p in peer_list:
                        self.peer_capabilities[p["id"]] = p.get("capabilities", {})
                        self.peer_status[p["id"]] = {
                            "status": p.get("status", "idle"),
                            "task": p.get("task", ""),
                        }
                else:
                    self.peers = peer_list

            # --- Capabilities ---
            elif kind == protocol.CAPABILITY_ANNOUNCE:
                pid = data.get("id")
                if pid:
                    self.peer_capabilities[pid] = data.get("capabilities", {})

            elif kind == protocol.CAPABILITY_RESPONSE:
                pass  # handled via req_id correlation

            # --- Status / Presence ---
            elif kind == protocol.STATUS_UPDATE:
                pid = data.get("id")
                if pid:
                    self.peer_status[pid] = {
                        "status": data.get("status", "idle"),
                        "task": data.get("task", ""),
                    }

            # --- Groups ---
            elif kind == protocol.GROUP_MSG:
                group = data.get("group", "")
                from_name = data.get("from_name", "?")
                body = data.get("body", "")
                if self.on_group_message:
                    self.on_group_message(group, from_name, body)
                else:
                    print(f"[{group}/{from_name}] {body}")

            elif kind == protocol.GROUP_LIST:
                pass  # handled via req_id

            elif kind == protocol.GROUP_MEMBERS:
                pass  # handled via req_id

            # --- Shared State ---
            elif kind == protocol.STATE_SET:
                key = data["key"]
                value = data["value"]
                scope = data.get("group", "_global")
                if scope not in self.shared_state:
                    self.shared_state[scope] = {}
                self.shared_state[scope][key] = value
                if self.on_state_change:
                    self.on_state_change(key, value, scope if scope != "_global" else None)

            elif kind == protocol.STATE_DELETE:
                key = data["key"]
                scope = data.get("group", "_global")
                if scope in self.shared_state:
                    self.shared_state[scope].pop(key, None)
                if self.on_state_change:
                    self.on_state_change(key, None, scope if scope != "_global" else None)

            elif kind == protocol.STATE_SYNC:
                scope = data.get("group", "_global")
                self.shared_state[scope] = data.get("state", {})

            elif kind == protocol.STATE_VALUE:
                pass  # handled via req_id

            # --- File transfer ---
            elif kind == protocol.FILE_START:
                tid = data["transfer_id"]
                self._transfers[tid] = {
                    "name": data["name"], "size": data["size"],
                    "chunks": [], "from_name": data.get("from_name", "?"),
                }

            elif kind == protocol.FILE_CHUNK:
                tid = data["transfer_id"]
                entry = self._transfers.get(tid)
                if not entry:
                    continue
                entry["chunks"].append(data["data"])
                if data.get("final"):
                    raw_bytes = b"".join(base64.b64decode(c) for c in entry["chunks"])
                    RECEIVE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
                    safe_name = Path(entry["name"]).name or f"unnamed-{tid}"
                    dest = RECEIVE_DIR / safe_name
                    # Verify path stays within RECEIVE_DIR
                    if not dest.resolve().is_relative_to(RECEIVE_DIR.resolve()):
                        print(f"Rejected file with unsafe name: {entry['name']}")
                        del self._transfers[tid]
                        continue
                    await asyncio.to_thread(dest.write_bytes, raw_bytes)
                    dest.chmod(0o600)  # Owner read/write only
                    print(f"Received file {safe_name} from {entry['from_name']}")
                    if self.on_file:
                        self.on_file(entry["from_name"], str(dest))
                    del self._transfers[tid]

            # --- Tunnels ---
            elif kind == protocol.TUNNEL_OPEN:
                asyncio.create_task(self._handle_tunnel_open(data))

            elif kind == protocol.TUNNEL_DATA:
                tid = data["tunnel_id"]
                tunnel = self._tunnels.get(tid)
                if tunnel and tunnel.get("writer"):
                    tunnel["writer"].write(base64.b64decode(data["data"]))
                    await tunnel["writer"].drain()

            elif kind == protocol.TUNNEL_CLOSE:
                tid = data["tunnel_id"]
                tunnel = self._tunnels.pop(tid, None)
                if tunnel and tunnel.get("writer"):
                    tunnel["writer"].close()
                    await tunnel["writer"].wait_closed()

            # --- Task broadcast ---
            elif kind == protocol.TASK_BROADCAST:
                if self.on_task_broadcast:
                    result = self.on_task_broadcast(
                        data.get("from_name", "?"), data["task_id"], data["task"])
                    if result is not None:
                        await self._send(protocol.task_response(
                            data["from"], data["task_id"], result))
                else:
                    self.pending_tasks.append({
                        "type": "broadcast",
                        "from": data.get("from", ""),
                        "from_name": data.get("from_name", "?"),
                        "task_id": data["task_id"],
                        "task": data["task"],
                    })

            elif kind == protocol.TASK_RESPONSE:
                tid = data["task_id"]
                if tid in self._broadcast_responses:
                    self._broadcast_responses[tid].append({
                        "from": data.get("from_name", data.get("from", "?")),
                        "response": data.get("response", ""),
                        "accepted": data.get("accepted", True),
                    })
                    if len(self._broadcast_responses[tid]) >= len(self.peers):
                        if tid in self._broadcast_events:
                            self._broadcast_events[tid].set()

            # --- Task delegation ---
            elif kind == protocol.TASK_ASSIGN:
                self.pending_tasks.append({
                    "type": "assigned",
                    "from": data.get("from", ""),
                    "from_name": data.get("from_name", "?"),
                    "task_id": data["task_id"],
                    "task": data["task"],
                    "context": data.get("context", {}),
                    "priority": data.get("priority", 0),
                })
                if self.on_task_assigned:
                    self.on_task_assigned(
                        data.get("from_name", "?"), data["task_id"],
                        data["task"], data.get("context", {}))
                # Auto-acknowledge
                await self._send(protocol.task_status(
                    data["from"], data["task_id"], "accepted"))

            elif kind == protocol.TASK_STATUS:
                tid = data["task_id"]
                if tid in self._delegated_tasks:
                    self._delegated_tasks[tid]["status"] = data["status"]

            elif kind == protocol.TASK_RESULT:
                tid = data["task_id"]
                if tid in self._delegated_tasks:
                    self._delegated_tasks[tid]["status"] = "completed" if data.get("success") else "failed"
                    self._delegated_tasks[tid]["result"] = data.get("result")
                    self._delegated_tasks[tid]["artifacts"] = data.get("artifacts", [])
                    if tid in self._task_events:
                        self._task_events[tid].set()

            # --- Voting ---
            elif kind == protocol.VOTE_PROPOSE:
                if self.on_vote_request:
                    choice = self.on_vote_request(
                        data.get("from_name", "?"), data["vote_id"],
                        data["proposal"], data.get("options", []))
                    if choice:
                        await self.cast_vote(data["from"], data["vote_id"], choice)

            elif kind == protocol.VOTE_CAST:
                vid = data["vote_id"]
                if vid in self._active_votes:
                    self._active_votes[vid]["votes"].append({
                        "from": data.get("from_name", data.get("from", "?")),
                        "choice": data["choice"],
                        "reason": data.get("reason", ""),
                    })
                    if len(self._active_votes[vid]["votes"]) >= len(self.peers):
                        self._active_votes[vid]["event"].set()

            elif kind == protocol.VOTE_RESULT:
                pass  # informational

            # --- Leader election ---
            elif kind == protocol.ELECTION_START:
                sender_id = data.get("from", "")
                if self.id and self.id > sender_id:
                    await self._send(protocol.election_alive(data["from"], data["election_id"]))
                    asyncio.create_task(self.start_election())

            elif kind == protocol.ELECTION_ALIVE:
                self._election_suppressed = True
                if self._election_event:
                    self._election_event.set()

            elif kind == protocol.ELECTION_VICTORY:
                if hasattr(self, "_victory_event") and self._victory_event:
                    self._victory_event.set()
                self.leader_id = data.get("from")
                self.leader_name = data.get("from_name", "?")
                self.is_leader = (self.leader_id == self.id)
                if self.on_leader_elected:
                    self.on_leader_elected(self.leader_id, self.leader_name, self.is_leader)

            # --- Distributed jobs ---
            elif kind == protocol.JOB_SUBMIT:
                asyncio.create_task(self._handle_job_submit(data))

            elif kind == protocol.JOB_RESULT:
                jid = data.get("job_id")
                fut = self._job_results.pop(jid, None)
                if fut and not fut.done():
                    fut.set_result(data)

            elif kind == protocol.JOB_UPDATE:
                jid = data.get("job_id")
                # Update local tracking
                job = self._executor.jobs.get(jid)
                if job:
                    job.status = data.get("status", job.status)
                    if "progress" in data:
                        job.progress = data["progress"]

            elif kind == protocol.JOB_STATUS:
                # Someone asking us about a job status
                jid = data.get("job_id")
                job = self._executor.check_job(jid)
                if job:
                    await self._send(protocol.job_result(
                        data.get("from", ""), jid, job.status,
                        result=job.result, error=job.error))

            elif kind == protocol.JOB_CANCEL:
                jid = data.get("job_id")
                self._executor.cancel_job(jid)

            elif kind == protocol.JOB_LIST:
                pass  # handled via req_id

            # --- Queue responses ---
            elif kind == protocol.QUEUE_PULL:
                pass  # handled via req_id

            elif kind == protocol.QUEUE_STATUS:
                pass  # handled via req_id

            # --- Update notifications ---
            elif kind == protocol.UPDATE_AVAILABLE:
                ver = data.get("version", "?")
                cur = data.get("current", "?")
                from_name = data.get("from_name", "?")
                print(f"Update available: {cur} -> {ver} (from {from_name})")
                if self.on_update_available:
                    self.on_update_available(ver, data.get("changelog", ""))

            elif kind == protocol.UPDATE_STATUS:
                ver = data.get("version", "?")
                status = data.get("status", "?")
                from_name = data.get("from_name", "?")
                print(f"Update status from {from_name}: {status} (v{ver})")

            # --- Remote execution ---
            elif kind == protocol.EXEC_REQUEST:
                asyncio.create_task(self._handle_exec_request(data))

            elif kind == protocol.EXEC_RESPONSE:
                eid = data.get("exec_id")
                fut = self._exec_results.pop(eid, None)
                if fut and not fut.done():
                    fut.set_result(data)

            # --- Reverse tunnel ---
            elif kind == protocol.REVERSE_TUNNEL_REQUEST:
                asyncio.create_task(self._handle_reverse_tunnel_request(data))

            elif kind == protocol.REVERSE_TUNNEL_ACCEPT:
                pass  # handled via tunnel_id tracking

            # --- Desktop sessions ---
            elif kind == protocol.DESKTOP_SESSION_OPEN:
                asyncio.create_task(self._handle_desktop_session_open(data))

            elif kind == protocol.DESKTOP_SESSION_READY:
                self._handle_desktop_session_ready(data)

            elif kind == protocol.DESKTOP_SESSION_CLOSE:
                asyncio.create_task(self._handle_desktop_session_close(data))

            elif kind == protocol.DESKTOP_SESSION_LIST:
                asyncio.create_task(self._handle_desktop_session_list(data))

            elif kind == protocol.DESKTOP_FRAME_REQUEST:
                asyncio.create_task(self._handle_desktop_frame_request(data))

            elif kind == protocol.DESKTOP_FRAME:
                self._handle_desktop_frame(data)

            elif kind == protocol.DESKTOP_INPUT:
                asyncio.create_task(self._handle_desktop_input(data))

            elif kind == protocol.DESKTOP_PERMISSION:
                self._handle_desktop_permission(data)

            # --- Errors / keepalive ---
            elif kind == protocol.ERROR:
                print(f"Error: {data.get('message', '?')}")

            elif kind == protocol.PONG:
                self._last_pong = time.monotonic()

    # --- Messaging ---

    async def send_message(self, to: str, body: str, *, wait_ack: bool = True,
                           timeout: float = 5.0) -> str:
        msg_id = uuid.uuid4().hex[:8]
        if wait_ack:
            fut = asyncio.get_running_loop().create_future()
            self._pending_acks[msg_id] = fut
        await self._send(protocol.msg(self._resolve(to), body, msg_id=msg_id))
        if wait_ack:
            try:
                return await asyncio.wait_for(fut, timeout)
            except asyncio.TimeoutError:
                return "timeout"
            finally:
                self._pending_acks.pop(msg_id, None)
        return msg_id

    async def send_file(self, to: str, filepath: str):
        target = self._resolve(to)
        path = Path(filepath)
        raw_bytes = await asyncio.to_thread(path.read_bytes)
        size = len(raw_bytes)
        name = path.name
        transfer_id = uuid.uuid4().hex[:8]

        await self._send(protocol.file_start(target, name, size, transfer_id))

        offset = 0
        seq = 0
        while offset < size:
            chunk = raw_bytes[offset:offset + protocol.CHUNK_SIZE]
            offset += len(chunk)
            b64 = base64.b64encode(chunk).decode()
            final = offset >= size
            await self._send(protocol.file_chunk(target, transfer_id, seq, b64, final))
            seq += 1
            if not final:
                pct = int(offset / size * 100)
                print(f"  sending {name}: {pct}%", end="\r")

        print(f"  sent {name} ({size} bytes)")

    async def open_tunnel(self, to: str, local_port: int, remote_port: int):
        target = self._resolve(to)

        async def on_tcp_connect(reader, writer):
            tid = uuid.uuid4().hex[:8]
            self._tunnels[tid] = {"reader": reader, "writer": writer}
            await self._send(protocol.tunnel_open(target, tid, remote_port))
            asyncio.create_task(self._relay_tcp_to_ws(tid, target, reader))

        server = await asyncio.start_server(on_tcp_connect, "127.0.0.1", local_port)
        print(f"Tunnel listening on 127.0.0.1:{local_port} -> {to}:{remote_port}")
        return server

    # --- Peer discovery ---

    async def request_peers(self, timeout: float = 5.0) -> dict:
        req_id = uuid.uuid4().hex[:8]
        fut = asyncio.get_running_loop().create_future()
        self._pending_requests[req_id] = fut
        await self._send(protocol.peers(req_id=req_id))
        try:
            response = await asyncio.wait_for(fut, timeout)
            peer_list = response.get("peers", [])
            if isinstance(peer_list, list):
                self.peers = {p["id"]: p["name"] for p in peer_list}
                for p in peer_list:
                    self.peer_capabilities[p["id"]] = p.get("capabilities", {})
                    self.peer_status[p["id"]] = {
                        "status": p.get("status", "idle"),
                        "task": p.get("task", ""),
                    }
            return response
        except asyncio.TimeoutError:
            return {"peers": []}
        finally:
            self._pending_requests.pop(req_id, None)

    # --- Capabilities ---

    async def announce_capabilities(self, capabilities: dict):
        self.capabilities = capabilities
        await self._send(protocol.capability_announce(capabilities))

    async def query_capabilities(self, tools=None, skills=None, tags=None,
                                 timeout: float = 5.0) -> list[dict]:
        req_id = uuid.uuid4().hex[:8]
        fut = asyncio.get_running_loop().create_future()
        self._pending_requests[req_id] = fut
        msg = protocol.capability_query(tools, skills, tags)
        msg["req_id"] = req_id
        await self._send(msg)
        try:
            response = await asyncio.wait_for(fut, timeout)
            return response.get("matches", [])
        except asyncio.TimeoutError:
            return []
        finally:
            self._pending_requests.pop(req_id, None)

    # --- Status / Presence ---

    async def update_status(self, status: str, task: str = "", metadata: dict | None = None):
        await self._send(protocol.status_update(status, task, metadata))

    # --- Groups ---

    async def join_group(self, group: str):
        self.groups.add(group)
        await self._send(protocol.group_join(group))

    async def leave_group(self, group: str):
        self.groups.discard(group)
        await self._send(protocol.group_leave(group))

    async def send_group_message(self, group: str, body: str, *, wait_ack: bool = False) -> str:
        msg_id = uuid.uuid4().hex[:8]
        if wait_ack:
            fut = asyncio.get_running_loop().create_future()
            self._pending_acks[msg_id] = fut
        await self._send(protocol.group_msg(group, body, msg_id=msg_id))
        if wait_ack:
            try:
                return await asyncio.wait_for(fut, 5.0)
            except asyncio.TimeoutError:
                return "timeout"
            finally:
                self._pending_acks.pop(msg_id, None)
        return msg_id

    async def list_groups(self, timeout: float = 5.0) -> dict:
        req_id = uuid.uuid4().hex[:8]
        fut = asyncio.get_running_loop().create_future()
        self._pending_requests[req_id] = fut
        msg = protocol.group_list()
        msg["req_id"] = req_id
        await self._send(msg)
        try:
            response = await asyncio.wait_for(fut, timeout)
            return response.get("groups", {})
        except asyncio.TimeoutError:
            return {}
        finally:
            self._pending_requests.pop(req_id, None)

    async def get_group_members(self, group: str, timeout: float = 5.0) -> list[dict]:
        req_id = uuid.uuid4().hex[:8]
        fut = asyncio.get_running_loop().create_future()
        self._pending_requests[req_id] = fut
        msg = protocol.group_members(group)
        msg["req_id"] = req_id
        await self._send(msg)
        try:
            response = await asyncio.wait_for(fut, timeout)
            return response.get("members", [])
        except asyncio.TimeoutError:
            return []
        finally:
            self._pending_requests.pop(req_id, None)

    # --- Shared State ---

    async def set_state(self, key: str, value, group: str | None = None):
        await self._send(protocol.state_set(key, value, group))
        scope = group or "_global"
        if scope not in self.shared_state:
            self.shared_state[scope] = {}
        self.shared_state[scope][key] = value

    async def get_state(self, key: str, group: str | None = None, timeout: float = 5.0):
        req_id = uuid.uuid4().hex[:8]
        fut = asyncio.get_running_loop().create_future()
        self._pending_requests[req_id] = fut
        msg = protocol.state_get(key, group)
        msg["req_id"] = req_id
        await self._send(msg)
        try:
            response = await asyncio.wait_for(fut, timeout)
            return response.get("value")
        except asyncio.TimeoutError:
            return None
        finally:
            self._pending_requests.pop(req_id, None)

    async def delete_state(self, key: str, group: str | None = None):
        await self._send(protocol.state_delete(key, group))
        scope = group or "_global"
        if scope in self.shared_state:
            self.shared_state[scope].pop(key, None)

    async def sync_state(self, group: str | None = None, timeout: float = 5.0) -> dict:
        req_id = uuid.uuid4().hex[:8]
        fut = asyncio.get_running_loop().create_future()
        self._pending_requests[req_id] = fut
        msg = protocol.state_sync(group)
        msg["req_id"] = req_id
        await self._send(msg)
        try:
            response = await asyncio.wait_for(fut, timeout)
            scope = group or "_global"
            self.shared_state[scope] = response.get("state", {})
            return self.shared_state[scope]
        except asyncio.TimeoutError:
            return {}
        finally:
            self._pending_requests.pop(req_id, None)

    # --- Task broadcasting ---

    async def broadcast_task(self, task: str, timeout_s: float = 30.0,
                             required_skills: list[str] | None = None) -> list[dict]:
        task_id = uuid.uuid4().hex[:8]
        self._broadcast_responses[task_id] = []
        self._broadcast_events[task_id] = asyncio.Event()
        try:
            await self._send(protocol.task_broadcast(task_id, task, timeout_s, required_skills))
            await asyncio.wait_for(self._broadcast_events[task_id].wait(), timeout=timeout_s)
        except asyncio.TimeoutError:
            pass
        finally:
            responses = self._broadcast_responses.pop(task_id, [])
            self._broadcast_events.pop(task_id, None)
        return responses

    # --- Task delegation ---

    async def delegate_task(self, to: str, task: str, priority: int = 0,
                            context: dict | None = None,
                            timeout_s: float = 120.0) -> dict:
        task_id = uuid.uuid4().hex[:8]
        target = self._resolve(to)
        self._delegated_tasks[task_id] = {
            "to": to, "task": task, "status": "pending", "result": None}
        self._task_events[task_id] = asyncio.Event()
        try:
            await self._send(protocol.task_assign(target, task_id, task, priority, context))
            await asyncio.wait_for(self._task_events[task_id].wait(), timeout=timeout_s)
        except asyncio.TimeoutError:
            self._delegated_tasks[task_id]["status"] = "timeout"
        finally:
            result = self._delegated_tasks.pop(task_id, {})
            self._task_events.pop(task_id, None)
        return result

    async def report_task_status(self, to: str, task_id: str, status: str,
                                  progress: float | None = None):
        await self._send(protocol.task_status(self._resolve(to), task_id, status, progress))

    async def return_task_result(self, to: str, task_id: str, result: str,
                                  success: bool = True, artifacts: list[str] | None = None):
        await self._send(protocol.task_result(
            self._resolve(to), task_id, result, success, artifacts))

    # --- Voting ---

    async def propose_vote(self, proposal: str, options: list[str] | None = None,
                           deadline_s: float = 30.0) -> dict:
        vote_id = uuid.uuid4().hex[:8]
        self._active_votes[vote_id] = {
            "votes": [], "event": asyncio.Event(), "proposal": proposal}
        try:
            await self._send(protocol.vote_propose(vote_id, proposal, options, deadline_s))
            await asyncio.wait_for(self._active_votes[vote_id]["event"].wait(), timeout=deadline_s)
        except asyncio.TimeoutError:
            pass
        finally:
            votes = self._active_votes.pop(vote_id, {}).get("votes", [])
        tally = {}
        for v in votes:
            tally[v["choice"]] = tally.get(v["choice"], 0) + 1
        outcome = max(tally, key=tally.get) if tally else "no_votes"
        await self._send(protocol.vote_result(vote_id, proposal, tally, outcome, votes))
        return {"tally": tally, "outcome": outcome, "votes": votes, "total": len(votes)}

    async def cast_vote(self, to: str, vote_id: str, choice: str, reason: str = ""):
        await self._send(protocol.vote_cast(self._resolve(to), vote_id, choice, reason))

    # --- Leader election ---

    async def start_election(self) -> dict:
        election_id = uuid.uuid4().hex[:8]
        self._election_suppressed = False
        self._election_event = asyncio.Event()
        self._victory_event = asyncio.Event()
        await self._send(protocol.election_start(election_id))
        try:
            await asyncio.wait_for(self._election_event.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            pass
        if not self._election_suppressed:
            self.leader_id = self.id
            self.leader_name = self.name
            self.is_leader = True
            await self._send(protocol.election_victory(election_id))
            if self.on_leader_elected:
                self.on_leader_elected(self.id, self.name, True)
        else:
            # Wait for the winner's VICTORY broadcast
            try:
                await asyncio.wait_for(self._victory_event.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                pass
        return {"leader_id": self.leader_id, "leader_name": self.leader_name}

    # --- Distributed job submission ---

    async def submit_job(self, to: str, func: str, args: list | None = None,
                         kwargs: dict | None = None, runtime: str = "builtin",
                         resources: dict | None = None,
                         timeout: float = 120.0) -> dict:
        """Submit a job to a remote peer and wait for result."""
        job_id = uuid.uuid4().hex[:8]
        target = self._resolve(to)
        fut = asyncio.get_running_loop().create_future()
        self._job_results[job_id] = fut
        try:
            await self._send(protocol.job_submit(
                target, job_id, runtime, func,
                args=args, kwargs=kwargs, resources=resources))
            result = await asyncio.wait_for(fut, timeout)
            return result
        except asyncio.TimeoutError:
            return {"job_id": job_id, "status": "timeout"}
        finally:
            self._job_results.pop(job_id, None)

    async def submit_script(self, to: str, script_path: str,
                             args: list | None = None,
                             runtime: str = "builtin",
                             timeout: float = 120.0) -> dict:
        """Upload a local script file to a remote peer and execute it.

        The script is base64-encoded and sent inline with the job_submit message.
        On the remote peer, it's written to a temp file and executed.
        For Python scripts, the script is run as a subprocess.
        """
        path = Path(script_path)
        if not path.exists():
            raise FileNotFoundError(f"Script not found: {script_path}")
        raw = await asyncio.to_thread(path.read_bytes)
        script_content = base64.b64encode(raw).decode()
        script_name = path.name

        job_id = uuid.uuid4().hex[:8]
        target = self._resolve(to)
        fut = asyncio.get_running_loop().create_future()
        self._job_results[job_id] = fut
        try:
            await self._send(protocol.job_submit(
                target, job_id, runtime, f"__script__:{script_name}",
                args=args or [], script=script_content, script_name=script_name))
            result = await asyncio.wait_for(fut, timeout)
            return result
        except asyncio.TimeoutError:
            return {"job_id": job_id, "status": "timeout"}
        finally:
            self._job_results.pop(job_id, None)

    async def submit_batch(self, to: str, func: str, args_list: list[list],
                           runtime: str = "builtin",
                           timeout: float = 120.0) -> list[dict]:
        """Submit multiple jobs as a batch. Returns list of results."""
        tasks = []
        for args in args_list:
            tasks.append(self.submit_job(to, func, args=args, runtime=runtime, timeout=timeout))
        return await asyncio.gather(*tasks, return_exceptions=True)

    async def map_func(self, to: str, func: str, inputs: list,
                       runtime: str = "builtin",
                       timeout: float = 60.0) -> list[dict]:
        """Map a function over inputs on a remote peer."""
        return await self.submit_batch(to, func, [[x] for x in inputs],
                                        runtime=runtime, timeout=timeout)

    async def check_job_status(self, to: str, job_id: str,
                                timeout: float = 5.0) -> dict:
        """Query a remote peer for job status."""
        req_id = uuid.uuid4().hex[:8]
        target = self._resolve(to)
        fut = asyncio.get_running_loop().create_future()
        self._pending_requests[req_id] = fut
        msg = protocol.job_status(target, job_id, req_id=req_id)
        await self._send(msg)
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            return {"job_id": job_id, "status": "unknown"}
        finally:
            self._pending_requests.pop(req_id, None)

    async def cancel_job(self, to: str, job_id: str):
        """Cancel a job on a remote peer."""
        target = self._resolve(to)
        await self._send(protocol.job_cancel(target, job_id))

    async def list_all_jobs(self, timeout: float = 5.0) -> list[dict]:
        """List all jobs tracked by the server."""
        req_id = uuid.uuid4().hex[:8]
        fut = asyncio.get_running_loop().create_future()
        self._pending_requests[req_id] = fut
        msg = protocol.job_list(req_id=req_id)
        await self._send(msg)
        try:
            response = await asyncio.wait_for(fut, timeout)
            return response.get("jobs", [])
        except asyncio.TimeoutError:
            return []
        finally:
            self._pending_requests.pop(req_id, None)

    def init_ray(self, address: str | None = None) -> bool:
        """Initialize local Ray runtime."""
        return self._executor.init_ray(address)

    def init_dask(self, scheduler: str | None = None) -> bool:
        """Initialize local Dask runtime."""
        return self._executor.init_dask(scheduler)

    @property
    def available_runtimes(self) -> list[str]:
        return self._executor.available_runtimes

    async def _handle_job_submit(self, data: dict):
        """Execute a job submitted by another peer.

        If `script` field is present, the job contains an inline script file
        (base64-encoded). It's written to a temp directory and executed as a
        subprocess. Otherwise, func is treated as a module.function path.
        """
        job_id = data["job_id"]
        runtime = data.get("runtime", "builtin")
        func = data["func"]
        args = data.get("args", [])
        kwargs = data.get("kwargs", {})
        from_id = data.get("from", "")
        from_name = data.get("from_name", "?")
        script_b64 = data.get("script")
        script_name = data.get("script_name")

        if self.on_job_received:
            self.on_job_received(from_name, job_id, func, args, kwargs)
            return

        try:
            # Handle inline script execution
            if script_b64 and func.startswith("__script__:"):
                await self._run_script_job(
                    job_id, script_b64, script_name or "script.py",
                    args, from_id)
                return

            job = await self._executor.submit(
                job_id, runtime, func, args=args, kwargs=kwargs,
                submitted_by=from_id)

            # Wait for completion and send result back
            for _ in range(1200):  # up to 120 seconds
                await asyncio.sleep(0.1)
                self._executor.check_job(job_id)
                if job.status in (JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED):
                    break

            await self._send(protocol.job_result(
                from_id, job_id, job.status,
                result=job.result, error=job.error))
        except Exception as e:
            await self._send(protocol.job_result(
                from_id, job_id, "failed", error=str(e)))

    async def _run_script_job(self, job_id: str, script_b64: str,
                               script_name: str, args: list, from_id: str):
        """Write a script to temp dir and execute it as a subprocess."""
        import shutil
        import tempfile

        # Sanitize script name — prevent path traversal
        script_name = Path(script_name).name
        if not script_name or "/" in script_name or "\\" in script_name:
            script_name = "script.py"

        script_dir = None
        proc = None
        try:
            script_bytes = base64.b64decode(script_b64)
            script_dir = Path(tempfile.mkdtemp(prefix="burrow-scripts-"))
            script_path = script_dir / script_name
            await asyncio.to_thread(script_path.write_bytes, script_bytes)
            script_path.chmod(0o700)  # Owner-only execute

            # Determine how to run the script
            if script_name.endswith(".py"):
                cmd = ["python3", str(script_path)] + [str(a) for a in args]
            elif script_name.endswith(".sh"):
                cmd = ["bash", str(script_path)] + [str(a) for a in args]
            else:
                cmd = [str(script_path)] + [str(a) for a in args]

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(script_dir),
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=300.0)

            output = stdout.decode(errors="replace")
            err_output = stderr.decode(errors="replace")

            if proc.returncode == 0:
                await self._send(protocol.job_result(
                    from_id, job_id, "completed",
                    result=output.strip() or "(no output)"))
            else:
                await self._send(protocol.job_result(
                    from_id, job_id, "failed",
                    error=f"exit code {proc.returncode}: {err_output.strip()}",
                    result=output.strip()))
        except asyncio.TimeoutError:
            if proc and proc.returncode is None:
                try:
                    proc.kill()
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except Exception:
                    pass
            await self._send(protocol.job_result(
                from_id, job_id, "failed", error="script timed out (300s)"))
        except Exception as e:
            if proc and proc.returncode is None:
                try:
                    proc.kill()
                except Exception:
                    pass
            await self._send(protocol.job_result(
                from_id, job_id, "failed", error=str(e)))
        finally:
            # Always clean up temp directory
            if script_dir and script_dir.exists():
                shutil.rmtree(script_dir, ignore_errors=True)

    # --- Server-side queue ---

    async def queue_push(self, queue_name: str, payload: dict,
                         priority: int = 0) -> str:
        """Push a job to the server-side work queue."""
        job_id = uuid.uuid4().hex[:8]
        fut = asyncio.get_running_loop().create_future()
        self._pending_acks[job_id] = fut
        await self._send(protocol.queue_push(queue_name, job_id, payload, priority))
        try:
            await asyncio.wait_for(fut, 5.0)
        except asyncio.TimeoutError:
            pass
        finally:
            self._pending_acks.pop(job_id, None)
        return job_id

    async def queue_pull(self, queue_name: str, timeout: float = 5.0) -> dict | None:
        """Pull the next job from the server-side work queue."""
        req_id = uuid.uuid4().hex[:8]
        fut = asyncio.get_running_loop().create_future()
        self._pending_requests[req_id] = fut
        msg = protocol.queue_pull(queue_name, worker_id=self.id)
        msg["req_id"] = req_id
        await self._send(msg)
        try:
            response = await asyncio.wait_for(fut, timeout)
            if response.get("job_id"):
                return response
            return None
        except asyncio.TimeoutError:
            return None
        finally:
            self._pending_requests.pop(req_id, None)

    async def queue_ack(self, queue_name: str, job_id: str, result=None,
                        success: bool = True, error: str | None = None):
        """Acknowledge completion of a queue job."""
        await self._send(protocol.queue_ack(queue_name, job_id, result, success, error))

    async def queue_status(self, queue_name: str | None = None,
                           timeout: float = 5.0) -> dict:
        """Get status of server-side work queues."""
        req_id = uuid.uuid4().hex[:8]
        fut = asyncio.get_running_loop().create_future()
        self._pending_requests[req_id] = fut
        msg = protocol.queue_status(queue_name, req_id=req_id)
        await self._send(msg)
        try:
            response = await asyncio.wait_for(fut, timeout)
            return response.get("status", {})
        except asyncio.TimeoutError:
            return {}
        finally:
            self._pending_requests.pop(req_id, None)

    async def register_worker(self, queues: list[str] | None = None,
                               capabilities: dict | None = None):
        """Register as a worker for server-side queues."""
        await self._send(protocol.worker_register(
            self.id, queues=queues, capabilities=capabilities))

    async def worker_heartbeat(self, status: str = "idle",
                                current_job: str | None = None):
        """Send a heartbeat to the server."""
        await self._send(protocol.worker_heartbeat(self.id, status, current_job))

    # --- Remote execution ---

    async def exec_command(self, to: str, command: str, *,
                           timeout: float = 60.0, cwd: str | None = None,
                           env: dict | None = None) -> dict:
        """Execute a command on a remote peer. Returns {exit_code, stdout, stderr}."""
        target = self._resolve(to)
        exec_id = uuid.uuid4().hex[:8]
        fut = asyncio.get_running_loop().create_future()
        self._exec_results[exec_id] = fut
        await self._send(protocol.exec_request(target, exec_id, command,
                                                timeout_s=timeout, cwd=cwd, env=env))
        try:
            result = await asyncio.wait_for(fut, timeout + 5.0)
            return {
                "exit_code": result.get("exit_code", -1),
                "stdout": result.get("stdout", ""),
                "stderr": result.get("stderr", ""),
                "error": result.get("error"),
            }
        except asyncio.TimeoutError:
            return {"exit_code": -1, "stdout": "", "stderr": "",
                    "error": f"exec timed out after {timeout}s"}
        finally:
            self._exec_results.pop(exec_id, None)

    async def _handle_exec_request(self, data: dict):
        """Handle incoming exec request from a peer."""
        exec_id = data.get("exec_id", "")
        from_id = data.get("from", "")
        from_name = data.get("from_name", "?")
        command = data.get("command", "")
        timeout_s = data.get("timeout_s", 60.0)
        cwd = data.get("cwd")
        env_override = data.get("env")

        if not self.exec_enabled:
            await self._send(protocol.exec_response(
                from_id, exec_id, exit_code=-1,
                error="exec disabled on this peer"))
            return

        if self.on_exec_request:
            allowed = self.on_exec_request(from_name, exec_id, command)
            if not allowed:
                await self._send(protocol.exec_response(
                    from_id, exec_id, exit_code=-1,
                    error="exec denied by policy"))
                return

        print(f"Exec [{exec_id}] from {from_name}: {command[:80]}")
        proc = None
        try:
            # Whitelist safe env vars only — don't leak secrets
            env = {k: v for k, v in os.environ.items() if k in _SAFE_ENV_KEYS}
            if env_override:
                env.update(env_override)

            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_s)

            await self._send(protocol.exec_response(
                from_id, exec_id,
                exit_code=proc.returncode,
                stdout=stdout.decode(errors="replace"),
                stderr=stderr.decode(errors="replace")))

        except asyncio.TimeoutError:
            # Kill the process to prevent zombies
            if proc:
                try:
                    proc.kill()
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except Exception:
                    pass
            await self._send(protocol.exec_response(
                from_id, exec_id, exit_code=-1,
                error=f"command timed out ({timeout_s}s)"))
        except Exception as e:
            if proc and proc.returncode is None:
                try:
                    proc.kill()
                except Exception:
                    pass
            await self._send(protocol.exec_response(
                from_id, exec_id, exit_code=-1, error=str(e)))

    # --- Remote desktop orchestration ---

    async def _run_desktop_script(self, to: str, args: list[str], *, timeout: float = 60.0) -> dict:
        script_path = Path(__file__).with_name("desktop.py")
        result = await self.submit_script(to, str(script_path), args=args, timeout=timeout)
        status = result.get("status", "")
        if status not in ("completed", "finished"):
            error = result.get("error") or result.get("result") or "desktop helper failed"
            raise RuntimeError(error)
        output = result.get("result", "")
        try:
            return desktop.parse_json_output(output)
        except Exception as exc:
            raise RuntimeError(f"Failed to parse desktop helper output: {output!r}") from exc

    async def get_desktop_capabilities(self, to: str, *, timeout: float = 30.0) -> dict:
        """Inspect which remote desktop backends are available on a peer."""
        return await self._run_desktop_script(to, ["capabilities"], timeout=timeout)

    def _coerce_desktop_frame(self, payload: dict | DesktopFrame) -> dict:
        if isinstance(payload, DesktopFrame):
            return payload.to_dict()
        return dict(payload)

    def _record_to_public_session(self, record: dict) -> dict:
        public = {
            k: v for k, v in record.items()
            if k not in {"tunnel_server", "host_pid", "last_input", "last_frame", "raw_session"}
        }
        return public

    def _normalize_desktop_session_payload(self, payload: dict | None) -> dict:
        payload = dict(payload or {})
        if not payload:
            return payload
        return DesktopSession.from_dict(payload).to_dict()

    def _classify_desktop_action(self, action: dict) -> str:
        action_type = str(action.get("type", "")).lower()
        clipboard_intent = action.get("clipboard_intent")
        if clipboard_intent or action_type.startswith("clipboard"):
            return "clipboard"
        if action_type in {"copy", "cut", "paste"}:
            return "clipboard"
        return "control"

    def _permission_transition_payload(self, previous: PermissionState,
                                       current: PermissionState, *, actor: str = "",
                                       reason: str = "",
                                       requested: dict | None = None) -> dict:
        return PermissionTransition(
            previous=previous,
            current=current,
            actor=actor,
            reason=reason,
            requested=dict(requested or {}),
            at=time.time(),
        ).to_dict()

    def _build_desktop_session_record(self, *, peer: str, owner: str,
                                      controller: str = "",
                                      session: dict | None = None,
                                      session_id: str | None = None,
                                      backend: str = "auto",
                                      readonly: bool = False,
                                      display: str | None = None,
                                      state: str = "ready",
                                      target: dict | None = None) -> dict:
        session = dict(session or {})
        session_id = session_id or session.get("session_id") or uuid.uuid4().hex[:12]
        now = time.time()
        protocol_name = session.get("protocol")
        remote_port = session.get("remote_port")
        viewer = {
            "protocol": protocol_name,
            "remote_port": remote_port,
            "viewer_path": session.get("viewer_path", ""),
            "connect_hint": session.get("connect_hint", ""),
            "display": session.get("display", display),
        }
        if session.get("local_port") is not None:
            viewer["local_port"] = session.get("local_port")
        if session.get("viewer_url"):
            viewer["viewer_url"] = session["viewer_url"]
        if session.get("local_connect_hint"):
            viewer["local_connect_hint"] = session["local_connect_hint"]
        permissions = PermissionState.from_dict({
            "view": True,
            "control": not (readonly or bool(session.get("readonly", False))),
            "clipboard": bool(session.get("clipboard", False)),
            **dict(session.get("permissions", {})),
            "readonly": readonly or bool(session.get("readonly", False)),
        })
        privacy = PrivacyState.from_dict({
            "supported": False,
            "enabled": False,
            "mode": "disabled",
            "stubbed": session.get("backend") == "native",
            **dict(session.get("privacy", {})),
        })
        reconnect = ReconnectState.from_dict({
            "supported": bool(session.get("resume_token") or session.get("reconnect", {}).get("supported")),
            "resume_token": session.get("resume_token", session.get("reconnect", {}).get("resume_token", "")),
            "epoch": session.get("resume_epoch", session.get("reconnect", {}).get("epoch", 1)),
            "strategy": session.get("reconnect", {}).get("strategy", "reopen"),
        })
        model = DesktopSession(
            session_id=session_id,
            peer=peer,
            backend=session.get("backend", backend),
            state=state,
            owner=owner,
            controller=controller,
            created_at=now,
            updated_at=now,
            capabilities={
                "protocol": protocol_name,
                "clipboard": bool(session.get("clipboard", False)),
                "audio": bool(session.get("audio", False)),
                "seamless": bool(session.get("seamless", False)),
                "description": session.get("description", ""),
            },
            viewer=viewer,
            computer_use={
                "frame_request": True,
                "input": True,
                "control_plane_only": True,
            },
            permissions=permissions,
            reconnect=reconnect,
            privacy=privacy,
            target=DesktopTarget.from_dict(target or session.get("target")),
        )
        record = model.to_dict()
        record["readonly"] = readonly or bool(session.get("readonly", False))
        record["raw_session"] = session
        if session.get("pid") is not None:
            record["host_pid"] = session["pid"]
        return record

    def _touch_desktop_session(self, session_id: str, **updates) -> dict | None:
        record = self._desktop_sessions.get(session_id)
        if not record:
            return None
        record.update(updates)
        record["updated_at"] = time.time()
        return record

    def _desktop_owned_by(self, record: dict, peer_id: str) -> bool:
        controller = record.get("controller")
        owner = record.get("owner")
        if owner == "hosted" and controller:
            return controller == peer_id
        return True

    async def _close_desktop_session_local(self, session_id: str) -> dict | None:
        record = self._desktop_sessions.pop(session_id, None)
        if not record:
            return None
        server = record.get("tunnel_server")
        if server:
            server.close()
        waiter = self._desktop_frame_waiters.pop(session_id, None)
        if waiter and not waiter.done():
            waiter.set_exception(RuntimeError(f"desktop session closed: {session_id}"))
        return record

    async def _maybe_call_desktop_callback(self, callback, *args):
        if callback is None:
            return None
        result = callback(*args)
        if isawaitable(result):
            result = await result
        return result

    async def open_desktop_session(self, to: str, *, backend: str = "auto",
                                   local_port: int | None = None,
                                   remote_port: int = 0,
                                   readonly: bool = False,
                                   display: str | None = None,
                                   target: dict | None = None,
                                   permissions: dict | None = None,
                                   privacy: dict | None = None,
                                   resume_token: str | None = None,
                                   resume_epoch: int | None = None,
                                   timeout: float = 60.0) -> dict:
        """Open a first-class desktop session on a remote peer and tunnel it locally."""
        session_id = uuid.uuid4().hex[:12]
        peer_id = self._resolve(to)
        fut = asyncio.get_running_loop().create_future()
        self._desktop_open_waiters[session_id] = fut
        await self._send(protocol.desktop_session_open(
            peer_id,
            session_id,
            backend=backend,
            readonly=readonly,
            remote_port=remote_port,
            display=display,
            target=target,
            permissions=permissions,
            privacy=privacy,
            resume_token=resume_token,
            resume_epoch=resume_epoch,
        ))
        try:
            session = await asyncio.wait_for(fut, timeout)
            if session.get("state") in {"error", "failed"}:
                raise RuntimeError(session.get("last_error") or session.get("error") or "desktop session open failed")
            remote_port = session.get("viewer", {}).get("remote_port")
            if remote_port in {None, 0}:
                session["updated_at"] = time.time()
                self._desktop_sessions[session_id] = session
                return self._record_to_public_session(session)
            chosen_local_port = local_port or remote_port
            try:
                tunnel_server = await self.open_tunnel(peer_id, chosen_local_port, remote_port)
            except Exception:
                try:
                    await self._send(protocol.desktop_session_close(peer_id, session_id))
                except Exception:
                    pass
                await self._close_desktop_session_local(session_id)
                raise
            viewer = session.setdefault("viewer", {})
            viewer["local_port"] = chosen_local_port
            viewer["viewer_url"] = f"tcp://127.0.0.1:{chosen_local_port}"
            viewer["local_connect_hint"] = desktop.build_connect_hint({
                "protocol": viewer.get("protocol"),
                "remote_port": remote_port,
                "local_port": chosen_local_port,
                "viewer_path": viewer.get("viewer_path", ""),
            })
            session["tunnel_server"] = tunnel_server
            session["updated_at"] = time.time()
            self._desktop_sessions[session_id] = session
            return self._record_to_public_session(session)
        finally:
            self._desktop_open_waiters.pop(session_id, None)

    async def list_desktop_sessions(self, to: str | None = None, *, timeout: float = 5.0) -> list[dict]:
        """List local desktop sessions or query a remote peer for sessions you own."""
        if to is None:
            return [self._record_to_public_session(record) for record in self._desktop_sessions.values()]
        req_id = uuid.uuid4().hex[:8]
        fut = asyncio.get_running_loop().create_future()
        self._pending_requests[req_id] = fut
        await self._send(protocol.desktop_session_list(self._resolve(to), req_id=req_id))
        try:
            response = await asyncio.wait_for(fut, timeout)
            return response.get("sessions", [])
        except asyncio.TimeoutError:
            return []
        finally:
            self._pending_requests.pop(req_id, None)

    async def close_desktop_session(self, to: str, session_id: str, *, timeout: float = 30.0) -> dict:
        """Close a remote desktop session and clean up local tunnel state."""
        error: Exception | None = None
        try:
            await self._send(protocol.desktop_session_close(self._resolve(to), session_id))
        except Exception as exc:
            error = exc
        existing = await self._close_desktop_session_local(session_id)
        if error is not None and existing is None:
            raise error
        return {
            "session_id": session_id,
            "closed": True,
            "peer": to,
            "state": "closed",
            "had_local_session": existing is not None,
            "remote_close_error": str(error) if error is not None else "",
        }

    async def request_desktop_frame(self, to: str, session_id: str, *, timeout: float = 30.0) -> dict:
        """Request a single desktop frame through the control plane."""
        fut = asyncio.get_running_loop().create_future()
        self._desktop_frame_waiters[session_id] = fut
        try:
            await self._send(protocol.desktop_frame_request(self._resolve(to), session_id))
            frame = await asyncio.wait_for(fut, timeout)
            return frame
        except asyncio.TimeoutError as exc:
            raise TimeoutError(f"timed out waiting for desktop frame: {session_id}") from exc
        finally:
            self._desktop_frame_waiters.pop(session_id, None)

    async def send_desktop_input(self, to: str, session_id: str, action: dict) -> dict:
        """Send a normalized desktop input action through the control plane."""
        await self._send(protocol.desktop_input(self._resolve(to), session_id, action))
        return {"session_id": session_id, "sent": True, "action": action}

    async def _handle_desktop_session_open(self, data: dict):
        from_id = data.get("from", "")
        session_id = data["session_id"]
        backend = data.get("backend", "auto")
        readonly = bool(data.get("readonly", False))
        display = data.get("display")
        target = data.get("target")
        permissions = data.get("permissions")
        privacy = data.get("privacy")
        resume_token = data.get("resume_token")
        resume_epoch = data.get("resume_epoch")
        remote_port = int(data.get("remote_port", 0) or 0)
        try:
            started = desktop.start_session(
                preferred_backend=backend,
                remote_port=remote_port,
                readonly=readonly,
                display=display,
            )
            if permissions is not None:
                started["permissions"] = permissions
            if privacy is not None:
                started["privacy"] = privacy
            if resume_token:
                started["resume_token"] = resume_token
            if resume_epoch is not None:
                started["resume_epoch"] = resume_epoch
            record = self._build_desktop_session_record(
                peer=from_id,
                owner="hosted",
                controller=from_id,
                session=started,
                session_id=session_id,
                backend=backend,
                readonly=readonly,
                display=display,
                state="ready",
                target=target,
            )
            self._desktop_sessions[session_id] = record
        except Exception as exc:
            failed_session = {"backend": backend, "display": display}
            if permissions is not None:
                failed_session["permissions"] = permissions
            if privacy is not None:
                failed_session["privacy"] = privacy
            if resume_token:
                failed_session["resume_token"] = resume_token
            if resume_epoch is not None:
                failed_session["resume_epoch"] = resume_epoch
            record = self._build_desktop_session_record(
                peer=from_id,
                owner="hosted",
                controller=from_id,
                session=failed_session,
                session_id=session_id,
                backend=backend,
                readonly=readonly,
                display=display,
                state="error",
                target=target,
            )
            record["last_error"] = str(exc)
        await self._send(protocol.desktop_session_ready(
            from_id,
            session_id,
            self._record_to_public_session(record),
        ))
        if self.on_desktop_session:
            await self._maybe_call_desktop_callback(
                self.on_desktop_session,
                "opened",
                self._record_to_public_session(record),
                {"from": from_id},
            )

    def _handle_desktop_session_ready(self, data: dict):
        session_id = data["session_id"]
        session_payload = self._normalize_desktop_session_payload(data.get("session", {}))
        self._desktop_sessions[session_id] = session_payload
        fut = self._desktop_open_waiters.get(session_id)
        if fut and not fut.done():
            fut.set_result(session_payload)

    async def _handle_desktop_session_close(self, data: dict):
        session_id = data["session_id"]
        from_id = data.get("from", "")
        record = self._desktop_sessions.get(session_id)
        if record and record.get("owner") == "hosted":
            if not self._desktop_owned_by(record, from_id):
                await self._send(protocol.desktop_permission(from_id, session_id, {
                    "view": True,
                    "control": False,
                    "clipboard": False,
                    "error": "session is owned by another peer",
                }))
                return
            host_session_id = record.get("raw_session", {}).get("session_id", session_id)
            try:
                desktop.stop_session(host_session_id)
            except Exception as exc:
                record["last_error"] = str(exc)
            await self._close_desktop_session_local(session_id)
        else:
            await self._close_desktop_session_local(session_id)
        if self.on_desktop_session:
            await self._maybe_call_desktop_callback(
                self.on_desktop_session,
                "closed",
                {"session_id": session_id},
                {"from": from_id},
            )

    async def _handle_desktop_session_list(self, data: dict):
        target = data.get("to")
        if target and target not in {self.id, self.name}:
            return
        req_id = data.get("req_id")
        if not req_id:
            return
        from_id = data.get("from", "")
        sessions = []
        for record in self._desktop_sessions.values():
            if record.get("owner") == "hosted" and not self._desktop_owned_by(record, from_id):
                continue
            sessions.append(self._record_to_public_session(record))
        await self._send(protocol.desktop_session_list(from_id, req_id=req_id, sessions=sessions))

    async def _handle_desktop_frame_request(self, data: dict):
        session_id = data["session_id"]
        from_id = data.get("from", "")
        record = self._desktop_sessions.get(session_id)
        if not record:
            await self._send(protocol.desktop_permission(from_id, session_id, {
                "view": False,
                "control": False,
                "clipboard": False,
                "error": "unknown desktop session",
            }))
            return
        if record.get("owner") == "hosted" and not self._desktop_owned_by(record, from_id):
            await self._send(protocol.desktop_permission(from_id, session_id, {
                "view": False,
                "control": False,
                "clipboard": False,
                "error": "session is owned by another peer",
            }))
            return
        frame = record.get("last_frame")
        if frame is None and self.on_desktop_frame_request:
            frame = await self._maybe_call_desktop_callback(
                self.on_desktop_frame_request,
                self._record_to_public_session(record),
                {"from": from_id, "session_id": session_id},
            )
        if frame is None:
            return
        frame_payload = self._coerce_desktop_frame(frame)
        record["last_frame"] = frame_payload
        await self._send(protocol.desktop_frame(from_id, session_id, frame_payload))

    def _handle_desktop_frame(self, data: dict):
        session_id = data["session_id"]
        frame = self._coerce_desktop_frame(data.get("frame", {}))
        record = self._desktop_sessions.get(session_id)
        if record is not None:
            record["last_frame"] = frame
            record["updated_at"] = time.time()
        fut = self._desktop_frame_waiters.get(session_id)
        if fut and not fut.done():
            fut.set_result(frame)

    async def _handle_desktop_input(self, data: dict):
        session_id = data["session_id"]
        from_id = data.get("from", "")
        action = data.get("action", {})
        record = self._desktop_sessions.get(session_id)
        if not record:
            await self._send(protocol.desktop_permission(from_id, session_id, {
                "view": False,
                "control": False,
                "clipboard": False,
                "error": "unknown desktop session",
            }))
            return
        if record.get("owner") == "hosted" and not self._desktop_owned_by(record, from_id):
            await self._send(protocol.desktop_permission(from_id, session_id, {
                "view": True,
                "control": False,
                "clipboard": False,
                "error": "session is owned by another peer",
            }))
            return
        permissions = PermissionState.from_dict(record.get("permissions"))
        action_class = self._classify_desktop_action(action)
        if action_class == "clipboard" and not permissions.clipboard:
            await self._send(protocol.desktop_permission(
                from_id,
                session_id,
                {
                    **permissions.to_dict(),
                    "error": "clipboard access is disabled",
                },
                transition=self._permission_transition_payload(
                    permissions,
                    permissions,
                    actor=from_id,
                    reason="clipboard access is disabled",
                    requested={"action": action, "kind": action_class},
                ),
            ))
            return
        if action_class == "control" and not permissions.control:
            await self._send(protocol.desktop_permission(
                from_id,
                session_id,
                {
                    **permissions.to_dict(),
                    "error": "desktop session is read-only",
                },
                transition=self._permission_transition_payload(
                    permissions,
                    permissions,
                    actor=from_id,
                    reason="desktop session is read-only",
                    requested={"action": action, "kind": action_class},
                ),
            ))
            return
        record["last_input"] = action
        record["updated_at"] = time.time()
        if self.on_desktop_input:
            await self._maybe_call_desktop_callback(
                self.on_desktop_input,
                self._record_to_public_session(record),
                action,
                {"from": from_id, "session_id": session_id},
            )

    def _handle_desktop_permission(self, data: dict):
        session_id = data["session_id"]
        record = self._desktop_sessions.get(session_id)
        permission = data.get("permission", {})
        if record is not None:
            previous = PermissionState.from_dict(record.get("permissions"))
            current = PermissionState.from_dict(permission)
            record["permissions"] = current.to_dict()
            transition = data.get("transition")
            if transition is None and previous.to_dict() != current.to_dict():
                transition = self._permission_transition_payload(
                    previous,
                    current,
                    actor=data.get("from", ""),
                    reason=permission.get("error", "permission update"),
                )
            if transition is not None:
                record["permission_transition"] = PermissionTransition.from_dict(transition).to_dict()
            record["permission_revision"] = int(record.get("permission_revision", 0) or 0) + 1
            if permission.get("error"):
                record["last_error"] = permission["error"]
            record["updated_at"] = time.time()
        waiter = self._desktop_frame_waiters.get(session_id)
        if waiter and not waiter.done() and permission.get("error"):
            waiter.set_exception(PermissionError(str(permission["error"])))

    async def start_desktop_session(self, to: str, *, backend: str = "auto",
                                    local_port: int | None = None,
                                    remote_port: int = 0,
                                    readonly: bool = False,
                                    display: str | None = None,
                                    timeout: float = 60.0) -> dict:
        """Backward-compatible shim for open_desktop_session()."""
        return await self.open_desktop_session(
            to,
            backend=backend,
            local_port=local_port,
            remote_port=remote_port,
            readonly=readonly,
            display=display,
            timeout=timeout,
        )

    async def stop_desktop_session(self, to: str, session_id: str, *, timeout: float = 30.0) -> dict:
        """Backward-compatible shim for close_desktop_session()."""
        return await self.close_desktop_session(to, session_id, timeout=timeout)

    # --- Reverse tunnel ---

    async def reverse_tunnel(self, to: str, remote_port: int,
                              local_port: int) -> str:
        """Request a reverse tunnel: remote peer listens on remote_port
        and forwards traffic back to our local_port."""
        target = self._resolve(to)
        tunnel_id = uuid.uuid4().hex[:8]
        await self._send(protocol.reverse_tunnel_request(
            target, tunnel_id, remote_port, local_port))
        self._tunnels[tunnel_id] = {"local_port": local_port, "type": "reverse"}
        return tunnel_id

    async def _handle_reverse_tunnel_request(self, data: dict):
        """Handle incoming reverse tunnel request — start listening on remote_port
        and relay traffic back to the requester's local_port."""
        tunnel_id = data["tunnel_id"]
        remote_port = data["remote_port"]
        local_port = data.get("local_port", remote_port)
        from_id = data.get("from", "")

        async def on_connect(reader, writer):
            """Each TCP connection on remote_port gets relayed back."""
            conn_tid = f"{tunnel_id}-{uuid.uuid4().hex[:4]}"
            self._tunnels[conn_tid] = {"reader": reader, "writer": writer}
            # Open a regular tunnel back to the requester's local_port
            await self._send(protocol.tunnel_open(from_id, conn_tid, local_port))
            asyncio.create_task(self._relay_tcp_to_ws(conn_tid, from_id, reader))

        try:
            server = await asyncio.start_server(
                on_connect, "127.0.0.1", remote_port)
            self._tunnels[tunnel_id] = {"server": server, "type": "reverse_listener"}
            await self._send(protocol.reverse_tunnel_accept(from_id, tunnel_id))
            print(f"Reverse tunnel {tunnel_id}: listening on :{remote_port} -> {from_id}:{local_port}")
        except OSError as exc:
            print(f"Reverse tunnel {tunnel_id}: cannot listen on :{remote_port} — {exc}")
            await self._send(protocol.tunnel_close(from_id, tunnel_id))

    # --- Internal helpers ---

    def _resolve(self, name_or_id: str) -> str:
        if name_or_id in self.peers:
            return name_or_id
        for pid, pname in self.peers.items():
            if pname.lower() == name_or_id.lower():
                return pid
        return name_or_id

    async def _send(self, msg: dict):
        if not self.ws:
            raise ConnectionError("Not connected")
        await self.ws.send(json.dumps(msg))

    async def _relay_tcp_to_ws(self, tunnel_id: str, target: str,
                               reader: asyncio.StreamReader):
        try:
            while True:
                data = await reader.read(protocol.CHUNK_SIZE)
                if not data:
                    break
                b64 = base64.b64encode(data).decode()
                await self._send(protocol.tunnel_data(target, tunnel_id, b64))
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            # Always close the writer, even if _send fails
            tunnel = self._tunnels.pop(tunnel_id, None)
            if tunnel and tunnel.get("writer"):
                tunnel["writer"].close()
                try:
                    await tunnel["writer"].wait_closed()
                except Exception:
                    pass
            try:
                await self._send(protocol.tunnel_close(target, tunnel_id))
            except Exception:
                pass

    async def _handle_tunnel_open(self, data: dict):
        tid = data["tunnel_id"]
        port = data["remote_port"]
        from_id = data.get("from", data.get("from_id", ""))
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
        except OSError as exc:
            print(f"Tunnel {tid}: cannot connect to localhost:{port} — {exc}")
            await self._send(protocol.tunnel_close(from_id, tid))
            return
        self._tunnels[tid] = {"reader": reader, "writer": writer}
        await self._send(protocol.tunnel_accept(from_id, tid))
        asyncio.create_task(self._relay_tcp_to_ws(tid, from_id, reader))
