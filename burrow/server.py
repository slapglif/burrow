"""Burrow registry + relay server."""

import argparse
import asyncio
import collections
import hmac
import json
import os
import time

import websockets

from burrow.protocol import (
    DEFAULT_PORT, REGISTER, REGISTERED, PEERS, PEER_JOINED, PEER_LEFT,
    MSG, FILE_START, FILE_CHUNK, PING, PONG, ERROR,
    TUNNEL_OPEN, TUNNEL_ACCEPT, TUNNEL_DATA, TUNNEL_CLOSE,
    ACK, NACK, QUEUED,
    CAPABILITY_ANNOUNCE, CAPABILITY_QUERY, CAPABILITY_RESPONSE,
    GROUP_JOIN, GROUP_LEAVE, GROUP_MSG, GROUP_LIST, GROUP_MEMBERS,
    STATE_SET, STATE_GET, STATE_VALUE, STATE_DELETE, STATE_SYNC,
    STATUS_UPDATE,
    TASK_BROADCAST, TASK_RESPONSE, TASK_ASSIGN, TASK_STATUS, TASK_RESULT,
    VOTE_PROPOSE, VOTE_CAST, VOTE_RESULT,
    ELECTION_START, ELECTION_ALIVE, ELECTION_VICTORY,
    JOB_SUBMIT, JOB_STATUS, JOB_RESULT, JOB_CANCEL, JOB_LIST, JOB_UPDATE,
    QUEUE_PUSH, QUEUE_PULL, QUEUE_ACK, QUEUE_STATUS,
    WORKER_REGISTER, WORKER_HEARTBEAT,
    EXEC_REQUEST, EXEC_RESPONSE,
    REVERSE_TUNNEL_REQUEST, REVERSE_TUNNEL_ACCEPT,
)
from burrow.distributed import BuiltinQueue

# --- Server state ---

peers: dict = {}                    # ws -> {"id", "name", "capabilities", "status", "task", "groups"}
by_id: dict[str, object] = {}      # id -> ws
groups: dict[str, set] = {}         # group_name -> set of ws
shared_state: dict[str, dict] = {"_global": {}}  # scope -> {key: {value, set_by, ts}}
message_queues: dict[str, collections.deque] = {}  # peer_id -> deque of (payload, timestamp)
last_seen: dict[str, tuple] = {}    # peer_id -> (name, monotonic_time)
name_to_id: dict[str, str] = {}    # name -> peer_id (for offline resolution)
work_queue: BuiltinQueue = BuiltinQueue()  # server-side job queue
job_registry: dict[str, dict] = {}  # job_id -> {submitted_by, target, runtime, status}

# --- Configuration ---

AUTH_TOKEN: str | None = None
RATE_MESSAGES_PER_SEC = 30.0
RATE_BURST = 50
RATE_FILE_CHUNK_COST = 3
MAX_MESSAGE_SIZE = 2 * 1024 * 1024  # 2 MB
MAX_CONNECTIONS = 500
MAX_QUEUE_PER_PEER = 100
MAX_QUEUE_AGE = 300.0               # 5 minutes
RECENTLY_SEEN_TTL = 600.0           # 10 minutes
MAX_HISTORY_PER_GROUP = 50

# Messages relayed point-to-point (have "to" field)
RELAY_TYPES = {
    MSG, FILE_START, FILE_CHUNK,
    TUNNEL_OPEN, TUNNEL_ACCEPT, TUNNEL_DATA, TUNNEL_CLOSE,
    TASK_RESPONSE, TASK_ASSIGN, TASK_STATUS, TASK_RESULT,
    VOTE_CAST, ELECTION_ALIVE,
    JOB_SUBMIT, JOB_STATUS, JOB_RESULT, JOB_CANCEL, JOB_UPDATE,
    EXEC_REQUEST, EXEC_RESPONSE,
    REVERSE_TUNNEL_REQUEST, REVERSE_TUNNEL_ACCEPT,
}

# Messages broadcast to all peers
BROADCAST_TYPES = {
    TASK_BROADCAST, VOTE_PROPOSE, VOTE_RESULT,
    ELECTION_START, ELECTION_VICTORY,
}


class TokenBucket:
    def __init__(self, rate: float, burst: int):
        self.rate = rate
        self.burst = burst
        self.tokens = float(burst)
        self.last_refill = time.monotonic()

    def consume(self, n: int = 1) -> bool:
        now = time.monotonic()
        self.tokens = min(self.burst, self.tokens + (now - self.last_refill) * self.rate)
        self.last_refill = now
        if self.tokens >= n:
            self.tokens -= n
            return True
        return False


async def broadcast(msg: dict, exclude=None, targets=None):
    raw = json.dumps(msg)
    ws_list = targets if targets is not None else list(peers)
    for ws in ws_list:
        if ws is not exclude:
            try:
                await ws.send(raw)
            except Exception:
                pass


async def broadcast_to_group(group_name: str, msg: dict, exclude=None):
    if group_name not in groups:
        return
    await broadcast(msg, exclude=exclude, targets=list(groups[group_name]))


def resolve(target: str):
    """Resolve a peer by exact ID first, then case-insensitive name."""
    if target in by_id:
        return by_id[target]
    low = target.lower()
    for ws, info in peers.items():
        if info["name"].lower() == low:
            return ws
    return None


def resolve_id(target: str) -> str | None:
    """Resolve target string to a peer ID."""
    if target in by_id:
        return target
    low = target.lower()
    for info in peers.values():
        if info["name"].lower() == low:
            return info["id"]
    # Check offline peers
    low = target.lower()
    for name, pid in name_to_id.items():
        if name.lower() == low:
            return pid
    return None


def inject_sender(msg: dict, ws, peer_id: str):
    sender = peers.get(ws, {})
    msg["from"] = sender.get("id", peer_id)
    msg["from_name"] = sender.get("name", peer_id)


async def handler(ws):
    if len(peers) >= MAX_CONNECTIONS:
        await ws.send(json.dumps({"type": ERROR, "message": "server full"}))
        await ws.close()
        return

    peer_id = os.urandom(4).hex()
    name = None
    bucket = TokenBucket(RATE_MESSAGES_PER_SEC, RATE_BURST)

    try:
        async for raw in ws:
            msg = json.loads(raw)
            t = msg.get("type")

            # Rate limiting
            cost = RATE_FILE_CHUNK_COST if t == FILE_CHUNK else 1
            if not bucket.consume(cost):
                await ws.send(json.dumps({"type": ERROR, "message": "rate limited"}))
                continue

            match t:

                # ---- Registration ----
                case "register":
                    name = msg.get("name", peer_id)

                    # Auth check
                    if AUTH_TOKEN is not None:
                        client_token = msg.get("token", "")
                        if not hmac.compare_digest(client_token, AUTH_TOKEN):
                            await ws.send(json.dumps({"type": ERROR, "message": "authentication failed"}))
                            await ws.close()
                            return

                    # Reconnect support
                    reconnect_id = msg.get("reconnect_id")
                    if reconnect_id and reconnect_id in by_id:
                        old_ws = by_id[reconnect_id]
                        peers.pop(old_ws, None)
                        by_id.pop(reconnect_id, None)
                    if reconnect_id:
                        peer_id = reconnect_id
                        # Allow name reuse on reconnect — also evict stale entry
                        stale_ws = None
                        for w, v in peers.items():
                            if v["name"] == name and w is not ws:
                                stale_ws = w
                                break
                        if stale_ws:
                            stale_info = peers.pop(stale_ws)
                            by_id.pop(stale_info["id"], None)
                            name_to_id.pop(stale_info["name"], None)
                    else:
                        # Check for name collision — but also detect stale connections
                        conflict_ws = None
                        for w, v in peers.items():
                            if v["name"] == name and w is not ws:
                                conflict_ws = w
                                break
                        if conflict_ws:
                            # Check if the conflicting connection is still alive
                            try:
                                await conflict_ws.ping()
                                # Still alive — reject the new registration
                                await ws.send(json.dumps({"type": ERROR, "message": f"name already taken: {name}"}))
                                continue
                            except Exception:
                                # Stale connection — evict it
                                stale_info = peers.pop(conflict_ws, None)
                                if stale_info:
                                    by_id.pop(stale_info["id"], None)
                                    name_to_id.pop(stale_info["name"], None)
                                    print(f"- {stale_info['name']} ({stale_info['id']}) [stale, evicted]")

                    caps = msg.get("capabilities", {})
                    peers[ws] = {
                        "id": peer_id, "name": name,
                        "capabilities": caps,
                        "status": "idle", "task": "",
                        "groups": set(),
                    }
                    by_id[peer_id] = ws
                    name_to_id[name] = peer_id
                    last_seen.pop(peer_id, None)

                    others = [
                        {"id": v["id"], "name": v["name"],
                         "capabilities": v.get("capabilities", {}),
                         "status": v.get("status", "idle")}
                        for w, v in peers.items() if w is not ws
                    ]
                    await ws.send(json.dumps({
                        "type": REGISTERED, "id": peer_id, "name": name, "peers": others
                    }))
                    await broadcast({"type": PEER_JOINED, "id": peer_id, "name": name,
                                     "capabilities": caps}, exclude=ws)
                    print(f"+ {name} ({peer_id})")

                    # Drain queued messages
                    if peer_id in message_queues:
                        queue = message_queues.pop(peer_id)
                        now = time.monotonic()
                        for payload, ts in queue:
                            if now - ts < MAX_QUEUE_AGE:
                                try:
                                    await ws.send(payload)
                                except Exception:
                                    break

                # ---- Peers ----
                case "peers":
                    others = [
                        {"id": v["id"], "name": v["name"],
                         "capabilities": v.get("capabilities", {}),
                         "status": v.get("status", "idle"),
                         "task": v.get("task", "")}
                        for w, v in peers.items() if w is not ws
                    ]
                    resp = {"type": PEERS, "peers": others}
                    if "req_id" in msg:
                        resp["req_id"] = msg["req_id"]
                    await ws.send(json.dumps(resp))

                # ---- Ping ----
                case "ping":
                    await ws.send(json.dumps({"type": PONG}))

                # ---- Capabilities ----
                case "capability_announce":
                    if ws in peers:
                        peers[ws]["capabilities"] = msg.get("capabilities", {})
                        await broadcast({
                            "type": CAPABILITY_ANNOUNCE,
                            "id": peers[ws]["id"],
                            "name": peers[ws]["name"],
                            "capabilities": msg.get("capabilities", {})
                        }, exclude=ws)

                case "capability_query":
                    req_tools = set(msg.get("required_tools", []))
                    req_skills = set(msg.get("required_skills", []))
                    req_tags = set(msg.get("required_tags", []))
                    matches = []
                    for w, info in peers.items():
                        if w is ws:
                            continue
                        caps = info.get("capabilities", {})
                        if req_tools and not req_tools.issubset(set(caps.get("tools", []))):
                            continue
                        if req_skills and not req_skills.issubset(set(caps.get("skills", []))):
                            continue
                        if req_tags and not req_tags.issubset(set(caps.get("tags", []))):
                            continue
                        matches.append({
                            "id": info["id"], "name": info["name"],
                            "capabilities": caps,
                            "status": info.get("status", "idle"),
                        })
                    resp = {"type": CAPABILITY_RESPONSE, "matches": matches}
                    if "req_id" in msg:
                        resp["req_id"] = msg["req_id"]
                    await ws.send(json.dumps(resp))

                # ---- Status / Presence ----
                case "status_update":
                    if ws in peers:
                        peers[ws]["status"] = msg.get("status", "idle")
                        peers[ws]["task"] = msg.get("task", "")
                        await broadcast({
                            "type": STATUS_UPDATE,
                            "id": peers[ws]["id"],
                            "name": peers[ws]["name"],
                            "status": msg.get("status", "idle"),
                            "task": msg.get("task", ""),
                            "metadata": msg.get("metadata", {}),
                        }, exclude=ws)

                # ---- Groups ----
                case "group_join":
                    group_name = msg["group"]
                    if group_name not in groups:
                        groups[group_name] = set()
                    groups[group_name].add(ws)
                    if ws in peers:
                        peers[ws]["groups"].add(group_name)
                    await broadcast_to_group(group_name, {
                        "type": PEER_JOINED, "group": group_name,
                        "id": peers.get(ws, {}).get("id", peer_id),
                        "name": peers.get(ws, {}).get("name", name or peer_id),
                    }, exclude=ws)
                    # Send recent state to joiner
                    scope = group_name
                    if scope in shared_state:
                        await ws.send(json.dumps({
                            "type": STATE_SYNC, "group": group_name,
                            "state": {k: v["value"] for k, v in shared_state[scope].items()},
                        }))

                case "group_leave":
                    group_name = msg["group"]
                    if group_name in groups:
                        groups[group_name].discard(ws)
                        if not groups[group_name]:
                            del groups[group_name]
                    if ws in peers:
                        peers[ws]["groups"].discard(group_name)
                    await broadcast_to_group(group_name, {
                        "type": PEER_LEFT, "group": group_name,
                        "id": peers.get(ws, {}).get("id", peer_id),
                        "name": peers.get(ws, {}).get("name", name or peer_id),
                    })

                case "group_msg":
                    group_name = msg["group"]
                    if group_name not in groups or ws not in groups[group_name]:
                        await ws.send(json.dumps({"type": ERROR, "message": f"not in group: {group_name}"}))
                        continue
                    out = {
                        "type": GROUP_MSG, "group": group_name,
                        "body": msg["body"],
                    }
                    inject_sender(out, ws, peer_id)
                    if "msg_id" in msg:
                        out["msg_id"] = msg["msg_id"]
                    await broadcast_to_group(group_name, out, exclude=ws)
                    # Ack to sender
                    if "msg_id" in msg:
                        await ws.send(json.dumps({"type": ACK, "msg_id": msg["msg_id"]}))

                case "group_list":
                    result = {g: len(members) for g, members in groups.items()}
                    resp = {"type": GROUP_LIST, "groups": result}
                    if "req_id" in msg:
                        resp["req_id"] = msg["req_id"]
                    await ws.send(json.dumps(resp))

                case "group_members":
                    group_name = msg["group"]
                    members = []
                    for w in groups.get(group_name, set()):
                        info = peers.get(w, {})
                        if info:
                            members.append({"id": info["id"], "name": info["name"],
                                            "status": info.get("status", "idle")})
                    resp = {"type": GROUP_MEMBERS, "group": group_name, "members": members}
                    if "req_id" in msg:
                        resp["req_id"] = msg["req_id"]
                    await ws.send(json.dumps(resp))

                # ---- Shared State ----
                case "state_set":
                    scope = msg.get("group", "_global")
                    key = msg["key"]
                    if scope not in shared_state:
                        shared_state[scope] = {}
                    sender_info = peers.get(ws, {})
                    shared_state[scope][key] = {
                        "value": msg["value"],
                        "set_by": sender_info.get("id", peer_id),
                        "ts": time.time(),
                    }
                    # Broadcast to group or all
                    notify = {
                        "type": STATE_SET, "key": key, "value": msg["value"],
                        "set_by": sender_info.get("name", peer_id),
                    }
                    if scope != "_global":
                        notify["group"] = scope
                        await broadcast_to_group(scope, notify, exclude=ws)
                    else:
                        await broadcast(notify, exclude=ws)

                case "state_get":
                    scope = msg.get("group", "_global")
                    key = msg["key"]
                    entry = shared_state.get(scope, {}).get(key)
                    resp = {
                        "type": STATE_VALUE, "key": key,
                        "value": entry["value"] if entry else None,
                        "exists": entry is not None,
                    }
                    if scope != "_global":
                        resp["group"] = scope
                    if "req_id" in msg:
                        resp["req_id"] = msg["req_id"]
                    await ws.send(json.dumps(resp))

                case "state_delete":
                    scope = msg.get("group", "_global")
                    key = msg["key"]
                    if scope in shared_state:
                        shared_state[scope].pop(key, None)
                    notify = {"type": STATE_DELETE, "key": key}
                    if scope != "_global":
                        notify["group"] = scope
                        await broadcast_to_group(scope, notify, exclude=ws)
                    else:
                        await broadcast(notify, exclude=ws)

                case "state_sync":
                    scope = msg.get("group", "_global")
                    state = {k: v["value"] for k, v in shared_state.get(scope, {}).items()}
                    resp = {"type": STATE_SYNC, "state": state}
                    if scope != "_global":
                        resp["group"] = scope
                    if "req_id" in msg:
                        resp["req_id"] = msg["req_id"]
                    await ws.send(json.dumps(resp))

                # ---- Server-side work queue ----
                case "queue_push":
                    queue_name = msg["queue"]
                    job_id = msg.get("job_id", os.urandom(4).hex())
                    sender_info = peers.get(ws, {})
                    item = work_queue.push(
                        queue_name, job_id, msg.get("payload", {}),
                        priority=msg.get("priority", 0),
                        submitted_by=sender_info.get("id", peer_id),
                    )
                    await ws.send(json.dumps({
                        "type": ACK, "msg_id": job_id,
                        "queue": queue_name, "status": "queued",
                    }))

                case "queue_pull":
                    queue_name = msg["queue"]
                    worker_id = msg.get("worker_id", peer_id)
                    item = work_queue.pull(queue_name, worker_id)
                    if item:
                        resp = {
                            "type": QUEUE_PULL, "queue": queue_name,
                            "job_id": item.job_id, "payload": item.payload,
                            "priority": item.priority,
                            "submitted_by": item.submitted_by,
                        }
                    else:
                        resp = {
                            "type": QUEUE_PULL, "queue": queue_name,
                            "job_id": None, "payload": None,
                        }
                    if "req_id" in msg:
                        resp["req_id"] = msg["req_id"]
                    await ws.send(json.dumps(resp))

                case "queue_ack":
                    job_id = msg["job_id"]
                    success = work_queue.ack(
                        job_id,
                        result=msg.get("result"),
                        success=msg.get("success", True),
                        error=msg.get("error"),
                    )
                    # Notify the submitter
                    job_info = work_queue.get_job(job_id)
                    if job_info and job_info["submitted_by"]:
                        submitter_ws = by_id.get(job_info["submitted_by"])
                        if submitter_ws:
                            try:
                                await submitter_ws.send(json.dumps({
                                    "type": JOB_RESULT,
                                    "job_id": job_id,
                                    "status": "completed" if msg.get("success", True) else "failed",
                                    "result": msg.get("result"),
                                    "error": msg.get("error"),
                                    "from": peer_id,
                                    "from_name": peers.get(ws, {}).get("name", peer_id),
                                }))
                            except Exception:
                                pass

                case "queue_status":
                    queue_name = msg.get("queue")
                    status = work_queue.status(queue_name)
                    resp = {"type": QUEUE_STATUS, "status": status}
                    if "req_id" in msg:
                        resp["req_id"] = msg["req_id"]
                    await ws.send(json.dumps(resp))

                case "worker_register":
                    worker_id = msg.get("worker_id", peer_id)
                    work_queue.register_worker(
                        worker_id,
                        queues=msg.get("queues", []),
                        capabilities=msg.get("capabilities", {}),
                    )
                    await ws.send(json.dumps({
                        "type": ACK, "msg_id": worker_id,
                        "status": "registered",
                    }))

                case "worker_heartbeat":
                    worker_id = msg.get("worker_id", peer_id)
                    work_queue.worker_heartbeat(
                        worker_id,
                        status=msg.get("status", "idle"),
                        current_job=msg.get("current_job"),
                    )

                # ---- Job list (server-tracked jobs) ----
                case "job_list":
                    jobs = []
                    for jid, jinfo in job_registry.items():
                        jobs.append(jinfo)
                    # Also include queue jobs
                    for jid, item in work_queue.jobs.items():
                        if jid not in job_registry:
                            jobs.append(work_queue.get_job(jid))
                    resp = {"type": JOB_LIST, "jobs": jobs}
                    if "req_id" in msg:
                        resp["req_id"] = msg["req_id"]
                    await ws.send(json.dumps(resp))

                # ---- Task broadcast (fan-out) ----
                case "task_broadcast":
                    inject_sender(msg, ws, peer_id)
                    req_skills = set(msg.get("required_skills", []))
                    for w, info in list(peers.items()):
                        if w is ws:
                            continue
                        if req_skills:
                            peer_skills = set(info.get("capabilities", {}).get("skills", []))
                            if not req_skills.issubset(peer_skills):
                                continue
                        try:
                            await w.send(json.dumps(msg))
                        except Exception:
                            pass

                # ---- Broadcast types (vote_propose, vote_result, election) ----
                case _ if t in BROADCAST_TYPES:
                    inject_sender(msg, ws, peer_id)
                    for w in list(peers):
                        if w is not ws:
                            try:
                                await w.send(json.dumps(msg))
                            except Exception:
                                pass

                # ---- Relay types (point-to-point with "to" field) ----
                case _ if "to" in msg and t in RELAY_TYPES:
                    msg_id = msg.get("msg_id")
                    target_ws = resolve(msg["to"])

                    if target_ws is None:
                        # Try to queue for offline peer
                        target_id = resolve_id(msg["to"])
                        if target_id and target_id in last_seen:
                            ts_name, ts_time = last_seen[target_id]
                            if time.monotonic() - ts_time < RECENTLY_SEEN_TTL:
                                inject_sender(msg, ws, peer_id)
                                if target_id not in message_queues:
                                    message_queues[target_id] = collections.deque(maxlen=MAX_QUEUE_PER_PEER)
                                message_queues[target_id].append((json.dumps(msg), time.monotonic()))
                                if msg_id:
                                    await ws.send(json.dumps({"type": QUEUED, "msg_id": msg_id,
                                                              "queue_size": len(message_queues[target_id])}))
                                continue

                        if msg_id:
                            await ws.send(json.dumps({"type": NACK, "msg_id": msg_id,
                                                      "reason": f"peer not found: {msg['to']}"}))
                        else:
                            await ws.send(json.dumps({"type": ERROR,
                                                      "message": f"peer not found: {msg['to']}"}))
                        continue

                    inject_sender(msg, ws, peer_id)
                    try:
                        await target_ws.send(json.dumps(msg))
                        if msg_id:
                            await ws.send(json.dumps({"type": ACK, "msg_id": msg_id}))
                    except Exception:
                        if msg_id:
                            await ws.send(json.dumps({"type": NACK, "msg_id": msg_id,
                                                      "reason": "delivery failed"}))

                case _:
                    await ws.send(json.dumps({"type": ERROR, "message": f"unknown type: {t}"}))

    except websockets.ConnectionClosed:
        pass
    finally:
        if ws in peers:
            info = peers.pop(ws)
            by_id.pop(info["id"], None)
            # Clean up name reservation so the name can be reused immediately
            if name_to_id.get(info["name"]) == info["id"]:
                name_to_id.pop(info["name"], None)
            last_seen[info["id"]] = (info["name"], time.monotonic())
            # Remove from groups
            for g in list(info.get("groups", set())):
                if g in groups:
                    groups[g].discard(ws)
                    if not groups[g]:
                        del groups[g]
                    else:
                        await broadcast_to_group(g, {
                            "type": PEER_LEFT, "group": g,
                            "id": info["id"], "name": info["name"],
                        })
            await broadcast({"type": PEER_LEFT, "id": info["id"], "name": info["name"]})
            print(f"- {info['name']} ({info['id']})")


async def _queue_cleanup():
    while True:
        await asyncio.sleep(60)
        now = time.monotonic()
        expired = [pid for pid, (_, ts) in last_seen.items()
                   if now - ts > RECENTLY_SEEN_TTL]
        for pid in expired:
            last_seen.pop(pid, None)
            message_queues.pop(pid, None)


async def serve(host="0.0.0.0", port=DEFAULT_PORT):
    print(f"burrow registry · ws://{host}:{port}")
    cleanup_task = asyncio.create_task(_queue_cleanup())
    try:
        async with websockets.serve(
            handler, host, port,
            max_size=MAX_MESSAGE_SIZE,
            ping_interval=15,
            ping_timeout=10,
        ):
            await asyncio.Future()
    finally:
        cleanup_task.cancel()


def run():
    ap = argparse.ArgumentParser(description="Burrow registry server")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--token", default=None, help="Shared auth token")
    args = ap.parse_args()
    global AUTH_TOKEN
    AUTH_TOKEN = args.token
    asyncio.run(serve(args.host, args.port))


if __name__ == "__main__":
    run()
