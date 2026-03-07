"""Burrow P2P client — async Peer that connects to a registry server."""

import asyncio
import json
import base64
import random
import time
import uuid
from pathlib import Path

import websockets
from websockets.asyncio.client import connect

from burrow import protocol


RECEIVE_DIR = Path("./burrow-received")


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
        """Connect + listen with auto-reconnect."""
        delay = self.BACKOFF_BASE
        while not self._stop_event.is_set():
            try:
                await self.connect()
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

    def _cleanup(self):
        self._transfers.clear()
        for tunnel in self._tunnels.values():
            if tunnel.get("writer"):
                tunnel["writer"].close()
        self._tunnels.clear()

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
                    RECEIVE_DIR.mkdir(parents=True, exist_ok=True)
                    safe_name = Path(entry["name"]).name or f"unnamed-{tid}"
                    dest = RECEIVE_DIR / safe_name
                    dest.write_bytes(raw_bytes)
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
        raw_bytes = path.read_bytes()
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
        await self._send(protocol.task_broadcast(task_id, task, timeout_s, required_skills))
        try:
            await asyncio.wait_for(self._broadcast_events[task_id].wait(), timeout=timeout_s)
        except asyncio.TimeoutError:
            pass
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
        await self._send(protocol.task_assign(target, task_id, task, priority, context))
        try:
            await asyncio.wait_for(self._task_events[task_id].wait(), timeout=timeout_s)
        except asyncio.TimeoutError:
            self._delegated_tasks[task_id]["status"] = "timeout"
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
        await self._send(protocol.vote_propose(vote_id, proposal, options, deadline_s))
        try:
            await asyncio.wait_for(self._active_votes[vote_id]["event"].wait(), timeout=deadline_s)
        except asyncio.TimeoutError:
            pass
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

    # --- Internal helpers ---

    def _resolve(self, name_or_id: str) -> str:
        if name_or_id in self.peers:
            return name_or_id
        for pid, pname in self.peers.items():
            if pname.lower() == name_or_id.lower():
                return pid
        return name_or_id

    async def _send(self, msg: dict):
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
            await self._send(protocol.tunnel_close(target, tunnel_id))
            tunnel = self._tunnels.pop(tunnel_id, None)
            if tunnel and tunnel.get("writer"):
                tunnel["writer"].close()
                await tunnel["writer"].wait_closed()

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
