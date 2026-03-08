"""Burrow P2P protocol — message types, builders, and constants."""

# Protocol version
VERSION = "0.4.0"

# Network defaults
DEFAULT_PORT = 7654
CHUNK_SIZE = 524288  # 512 KB

# Keepalive defaults
DEFAULT_KEEPALIVE_INTERVAL = 15  # seconds
DEFAULT_KEEPALIVE_TIMEOUT = 10   # seconds

# --- Message types ---
REGISTER = "register"
REGISTERED = "registered"
PEERS = "peers"
PEER_JOINED = "peer_joined"
PEER_LEFT = "peer_left"
MSG = "msg"
FILE_START = "file_start"
FILE_CHUNK = "file_chunk"
TUNNEL_OPEN = "tunnel_open"
TUNNEL_ACCEPT = "tunnel_accept"
TUNNEL_DATA = "tunnel_data"
TUNNEL_CLOSE = "tunnel_close"
PING = "ping"
PONG = "pong"
ERROR = "error"

# Delivery confirmation
ACK = "ack"
NACK = "nack"
QUEUED = "queued"

# Capabilities
CAPABILITY_ANNOUNCE = "capability_announce"
CAPABILITY_QUERY = "capability_query"
CAPABILITY_RESPONSE = "capability_response"

# Groups / channels
GROUP_JOIN = "group_join"
GROUP_LEAVE = "group_leave"
GROUP_MSG = "group_msg"
GROUP_LIST = "group_list"
GROUP_MEMBERS = "group_members"

# Shared state (key-value store)
STATE_SET = "state_set"
STATE_GET = "state_get"
STATE_VALUE = "state_value"
STATE_DELETE = "state_delete"
STATE_SYNC = "state_sync"

# Presence / status
STATUS_UPDATE = "status_update"

# Task coordination
TASK_BROADCAST = "task_broadcast"
TASK_RESPONSE = "task_response"
TASK_ASSIGN = "task_assign"
TASK_STATUS = "task_status"
TASK_RESULT = "task_result"

# Consensus / voting
VOTE_PROPOSE = "vote_propose"
VOTE_CAST = "vote_cast"
VOTE_RESULT = "vote_result"

# Leader election
ELECTION_START = "election_start"
ELECTION_ALIVE = "election_alive"
ELECTION_VICTORY = "election_victory"

# Distributed jobs (Ray / Dask / built-in queue)
JOB_SUBMIT = "job_submit"
JOB_STATUS = "job_status"
JOB_RESULT = "job_result"
JOB_CANCEL = "job_cancel"
JOB_LIST = "job_list"
JOB_UPDATE = "job_update"

# Built-in work queue
QUEUE_PUSH = "queue_push"
QUEUE_PULL = "queue_pull"
QUEUE_ACK = "queue_ack"
QUEUE_STATUS = "queue_status"
WORKER_REGISTER = "worker_register"
WORKER_HEARTBEAT = "worker_heartbeat"


# --- Builder functions ---

def register(name: str, *, token: str | None = None,
             reconnect_id: str | None = None,
             capabilities: dict | None = None) -> dict:
    d = {"type": REGISTER, "name": name}
    if token:
        d["token"] = token
    if reconnect_id:
        d["reconnect_id"] = reconnect_id
    if capabilities:
        d["capabilities"] = capabilities
    return d


def peers(req_id: str | None = None) -> dict:
    d = {"type": PEERS}
    if req_id:
        d["req_id"] = req_id
    return d


def msg(to: str, body: str, msg_id: str | None = None) -> dict:
    d = {"type": MSG, "to": to, "body": body}
    if msg_id:
        d["msg_id"] = msg_id
    return d


def file_start(to: str, name: str, size: int, transfer_id: str) -> dict:
    return {"type": FILE_START, "to": to, "name": name, "size": size,
            "transfer_id": transfer_id}


def file_chunk(to: str, transfer_id: str, seq: int, data: str,
               final: bool) -> dict:
    return {"type": FILE_CHUNK, "to": to, "transfer_id": transfer_id,
            "seq": seq, "data": data, "final": final}


def tunnel_open(to: str, tunnel_id: str, remote_port: int) -> dict:
    return {"type": TUNNEL_OPEN, "to": to, "tunnel_id": tunnel_id,
            "remote_port": remote_port}


def tunnel_accept(to: str, tunnel_id: str) -> dict:
    return {"type": TUNNEL_ACCEPT, "to": to, "tunnel_id": tunnel_id}


def tunnel_data(to: str, tunnel_id: str, data: str) -> dict:
    return {"type": TUNNEL_DATA, "to": to, "tunnel_id": tunnel_id,
            "data": data}


def tunnel_close(to: str, tunnel_id: str) -> dict:
    return {"type": TUNNEL_CLOSE, "to": to, "tunnel_id": tunnel_id}


def error(message: str) -> dict:
    return {"type": ERROR, "message": message}


def ping() -> dict:
    return {"type": PING}


def pong() -> dict:
    return {"type": PONG}


def ack(msg_id: str) -> dict:
    return {"type": ACK, "msg_id": msg_id}


def nack(msg_id: str, reason: str) -> dict:
    return {"type": NACK, "msg_id": msg_id, "reason": reason}


def queued(msg_id: str, queue_size: int) -> dict:
    return {"type": QUEUED, "msg_id": msg_id, "queue_size": queue_size}


# Capabilities

def capability_announce(capabilities: dict) -> dict:
    return {"type": CAPABILITY_ANNOUNCE, "capabilities": capabilities}


def capability_query(required_tools: list[str] | None = None,
                     required_skills: list[str] | None = None,
                     required_tags: list[str] | None = None) -> dict:
    return {"type": CAPABILITY_QUERY,
            "required_tools": required_tools or [],
            "required_skills": required_skills or [],
            "required_tags": required_tags or []}


# Groups

def group_join(group: str) -> dict:
    return {"type": GROUP_JOIN, "group": group}


def group_leave(group: str) -> dict:
    return {"type": GROUP_LEAVE, "group": group}


def group_msg(group: str, body: str, msg_id: str | None = None) -> dict:
    d = {"type": GROUP_MSG, "group": group, "body": body}
    if msg_id:
        d["msg_id"] = msg_id
    return d


def group_list() -> dict:
    return {"type": GROUP_LIST}


def group_members(group: str) -> dict:
    return {"type": GROUP_MEMBERS, "group": group}


# Shared state

def state_set(key: str, value, group: str | None = None) -> dict:
    d = {"type": STATE_SET, "key": key, "value": value}
    if group:
        d["group"] = group
    return d


def state_get(key: str, group: str | None = None) -> dict:
    d = {"type": STATE_GET, "key": key}
    if group:
        d["group"] = group
    return d


def state_delete(key: str, group: str | None = None) -> dict:
    d = {"type": STATE_DELETE, "key": key}
    if group:
        d["group"] = group
    return d


def state_sync(group: str | None = None) -> dict:
    d = {"type": STATE_SYNC}
    if group:
        d["group"] = group
    return d


# Presence

def status_update(status: str, task: str = "", metadata: dict | None = None) -> dict:
    d = {"type": STATUS_UPDATE, "status": status, "task": task}
    if metadata:
        d["metadata"] = metadata
    return d


# Task coordination

def task_broadcast(task_id: str, task: str, timeout_s: float = 30.0,
                   required_skills: list[str] | None = None) -> dict:
    return {"type": TASK_BROADCAST, "task_id": task_id, "task": task,
            "timeout_s": timeout_s,
            "required_skills": required_skills or []}


def task_response(to: str, task_id: str, response: str,
                  accepted: bool = True) -> dict:
    return {"type": TASK_RESPONSE, "to": to, "task_id": task_id,
            "response": response, "accepted": accepted}


def task_assign(to: str, task_id: str, task: str,
                priority: int = 0, context: dict | None = None) -> dict:
    return {"type": TASK_ASSIGN, "to": to, "task_id": task_id,
            "task": task, "priority": priority,
            "context": context or {}}


def task_status(to: str, task_id: str, status: str,
                progress: float | None = None) -> dict:
    return {"type": TASK_STATUS, "to": to, "task_id": task_id,
            "status": status, "progress": progress}


def task_result(to: str, task_id: str, result: str,
                success: bool = True, artifacts: list[str] | None = None) -> dict:
    return {"type": TASK_RESULT, "to": to, "task_id": task_id,
            "result": result, "success": success,
            "artifacts": artifacts or []}


# Consensus

def vote_propose(vote_id: str, proposal: str,
                 options: list[str] | None = None,
                 deadline_s: float = 30.0) -> dict:
    return {"type": VOTE_PROPOSE, "vote_id": vote_id,
            "proposal": proposal,
            "options": options or ["approve", "reject", "abstain"],
            "deadline_s": deadline_s}


def vote_cast(to: str, vote_id: str, choice: str,
              reason: str = "") -> dict:
    return {"type": VOTE_CAST, "to": to, "vote_id": vote_id,
            "choice": choice, "reason": reason}


def vote_result(vote_id: str, proposal: str, tally: dict,
                outcome: str, votes: list[dict]) -> dict:
    return {"type": VOTE_RESULT, "vote_id": vote_id,
            "proposal": proposal, "tally": tally,
            "outcome": outcome, "votes": votes}


# Leader election

def election_start(election_id: str) -> dict:
    return {"type": ELECTION_START, "election_id": election_id}


def election_alive(to: str, election_id: str) -> dict:
    return {"type": ELECTION_ALIVE, "to": to, "election_id": election_id}


def election_victory(election_id: str) -> dict:
    return {"type": ELECTION_VICTORY, "election_id": election_id}


# Distributed jobs

def job_submit(to: str, job_id: str, runtime: str, func: str, args: list | None = None,
               kwargs: dict | None = None, resources: dict | None = None) -> dict:
    """Submit a job to a peer. runtime: 'ray', 'dask', or 'builtin'."""
    return {"type": JOB_SUBMIT, "to": to, "job_id": job_id, "runtime": runtime,
            "func": func, "args": args or [], "kwargs": kwargs or {},
            "resources": resources or {}}


def job_status(to: str, job_id: str, req_id: str | None = None) -> dict:
    d = {"type": JOB_STATUS, "to": to, "job_id": job_id}
    if req_id:
        d["req_id"] = req_id
    return d


def job_result(to: str, job_id: str, status: str, result=None,
               error: str | None = None) -> dict:
    d = {"type": JOB_RESULT, "to": to, "job_id": job_id, "status": status}
    if result is not None:
        d["result"] = result
    if error:
        d["error"] = error
    return d


def job_cancel(to: str, job_id: str) -> dict:
    return {"type": JOB_CANCEL, "to": to, "job_id": job_id}


def job_list(req_id: str | None = None) -> dict:
    d = {"type": JOB_LIST}
    if req_id:
        d["req_id"] = req_id
    return d


def job_update(to: str, job_id: str, status: str, progress: float | None = None,
               result=None, error: str | None = None) -> dict:
    d = {"type": JOB_UPDATE, "to": to, "job_id": job_id, "status": status}
    if progress is not None:
        d["progress"] = progress
    if result is not None:
        d["result"] = result
    if error:
        d["error"] = error
    return d


# Built-in work queue

def queue_push(queue_name: str, job_id: str, payload: dict,
               priority: int = 0) -> dict:
    return {"type": QUEUE_PUSH, "queue": queue_name, "job_id": job_id,
            "payload": payload, "priority": priority}


def queue_pull(queue_name: str, worker_id: str | None = None) -> dict:
    d = {"type": QUEUE_PULL, "queue": queue_name}
    if worker_id:
        d["worker_id"] = worker_id
    return d


def queue_ack(queue_name: str, job_id: str, result=None,
              success: bool = True, error: str | None = None) -> dict:
    d = {"type": QUEUE_ACK, "queue": queue_name, "job_id": job_id,
         "success": success}
    if result is not None:
        d["result"] = result
    if error:
        d["error"] = error
    return d


def queue_status(queue_name: str | None = None,
                 req_id: str | None = None) -> dict:
    d = {"type": QUEUE_STATUS}
    if queue_name:
        d["queue"] = queue_name
    if req_id:
        d["req_id"] = req_id
    return d


def worker_register(worker_id: str, queues: list[str] | None = None,
                     capabilities: dict | None = None) -> dict:
    return {"type": WORKER_REGISTER, "worker_id": worker_id,
            "queues": queues or [], "capabilities": capabilities or {}}


def worker_heartbeat(worker_id: str, status: str = "idle",
                      current_job: str | None = None) -> dict:
    d = {"type": WORKER_HEARTBEAT, "worker_id": worker_id, "status": status}
    if current_job:
        d["current_job"] = current_job
    return d
