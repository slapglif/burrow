"""Integration tests for the burrow registry/relay server."""

import asyncio
import json

import pytest
import pytest_asyncio
import websockets

from burrow import protocol
from burrow.server import handler


@pytest_asyncio.fixture()
async def server():
    srv = await websockets.serve(handler, "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]
    uri = f"ws://127.0.0.1:{port}"
    # Clear server global state between tests
    from burrow import server as srv_mod
    srv_mod.peers.clear()
    srv_mod.by_id.clear()
    srv_mod.groups.clear()
    srv_mod.shared_state.clear()
    srv_mod.shared_state["_global"] = {}
    srv_mod.message_queues.clear()
    srv_mod.last_seen.clear()
    srv_mod.name_to_id.clear()
    srv_mod.work_queue = srv_mod.BuiltinQueue()
    srv_mod.job_registry.clear()
    yield uri
    srv.close()
    await srv.wait_closed()


async def register_client(uri, name, **kwargs):
    ws = await websockets.connect(uri)
    await ws.send(json.dumps(protocol.register(name, **kwargs)))
    resp = json.loads(await asyncio.wait_for(ws.recv(), 2))
    return ws, resp


# ---------------------------------------------------------------------------
# Core tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_register(server):
    ws, resp = await register_client(server, "Alice")
    try:
        assert resp["type"] == protocol.REGISTERED
        assert resp["name"] == "Alice"
        assert "id" in resp and len(resp["id"]) > 0
        assert "peers" in resp
    finally:
        await ws.close()


@pytest.mark.asyncio
async def test_register_includes_peers(server):
    ws_a, _ = await register_client(server, "Alice")
    ws_b, resp_b = await register_client(server, "Bob")
    try:
        assert len(resp_b["peers"]) == 1
        assert resp_b["peers"][0]["name"] == "Alice"
    finally:
        await ws_a.close()
        await ws_b.close()


@pytest.mark.asyncio
async def test_peers_empty(server):
    ws, _ = await register_client(server, "Alice")
    try:
        await ws.send(json.dumps(protocol.peers()))
        resp = json.loads(await asyncio.wait_for(ws.recv(), 2))
        assert resp["type"] == protocol.PEERS
        assert resp["peers"] == []
    finally:
        await ws.close()


@pytest.mark.asyncio
async def test_peers_lists_others(server):
    ws_a, reg_a = await register_client(server, "Alice")
    ws_b, reg_b = await register_client(server, "Bob")
    try:
        await asyncio.wait_for(ws_a.recv(), 2)  # drain peer_joined
        await ws_a.send(json.dumps(protocol.peers()))
        resp = json.loads(await asyncio.wait_for(ws_a.recv(), 2))
        assert resp["type"] == protocol.PEERS
        assert len(resp["peers"]) == 1
        assert resp["peers"][0]["name"] == "Bob"
        assert resp["peers"][0]["id"] == reg_b["id"]
    finally:
        await ws_a.close()
        await ws_b.close()


@pytest.mark.asyncio
async def test_peers_req_id_correlation(server):
    ws, _ = await register_client(server, "Alice")
    try:
        await ws.send(json.dumps(protocol.peers(req_id="r42")))
        resp = json.loads(await asyncio.wait_for(ws.recv(), 2))
        assert resp["req_id"] == "r42"
    finally:
        await ws.close()


@pytest.mark.asyncio
async def test_msg_relay(server):
    ws_a, reg_a = await register_client(server, "Alice")
    ws_b, reg_b = await register_client(server, "Bob")
    try:
        await asyncio.wait_for(ws_a.recv(), 2)
        await ws_a.send(json.dumps(protocol.msg("Bob", "hello Bob")))
        relayed = json.loads(await asyncio.wait_for(ws_b.recv(), 2))
        assert relayed["type"] == protocol.MSG
        assert relayed["body"] == "hello Bob"
        assert relayed["from"] == reg_a["id"]
        assert relayed["from_name"] == "Alice"
    finally:
        await ws_a.close()
        await ws_b.close()


@pytest.mark.asyncio
async def test_msg_ack(server):
    ws_a, _ = await register_client(server, "Alice")
    ws_b, _ = await register_client(server, "Bob")
    try:
        await asyncio.wait_for(ws_a.recv(), 2)
        await ws_a.send(json.dumps(protocol.msg("Bob", "hi", msg_id="m1")))
        # Alice should get ACK
        ack = json.loads(await asyncio.wait_for(ws_a.recv(), 2))
        assert ack["type"] == protocol.ACK
        assert ack["msg_id"] == "m1"
        # Bob should get the message
        relayed = json.loads(await asyncio.wait_for(ws_b.recv(), 2))
        assert relayed["body"] == "hi"
    finally:
        await ws_a.close()
        await ws_b.close()


@pytest.mark.asyncio
async def test_msg_nack_unknown_peer(server):
    ws, _ = await register_client(server, "Alice")
    try:
        await ws.send(json.dumps(protocol.msg("nobody", "hello", msg_id="m2")))
        resp = json.loads(await asyncio.wait_for(ws.recv(), 2))
        assert resp["type"] == protocol.NACK
        assert resp["msg_id"] == "m2"
    finally:
        await ws.close()


@pytest.mark.asyncio
async def test_msg_to_unknown_peer(server):
    ws, _ = await register_client(server, "Alice")
    try:
        await ws.send(json.dumps(protocol.msg("nobody", "hello")))
        resp = json.loads(await asyncio.wait_for(ws.recv(), 2))
        assert resp["type"] == protocol.ERROR
        assert "peer not found" in resp["message"]
    finally:
        await ws.close()


@pytest.mark.asyncio
async def test_peer_joined_broadcast(server):
    ws_a, _ = await register_client(server, "Alice")
    try:
        ws_b, reg_b = await register_client(server, "Bob")
        try:
            notif = json.loads(await asyncio.wait_for(ws_a.recv(), 2))
            assert notif["type"] == protocol.PEER_JOINED
            assert notif["name"] == "Bob"
            assert notif["id"] == reg_b["id"]
        finally:
            await ws_b.close()
    finally:
        await ws_a.close()


@pytest.mark.asyncio
async def test_peer_left_broadcast(server):
    ws_a, _ = await register_client(server, "Alice")
    ws_b, reg_b = await register_client(server, "Bob")
    try:
        await asyncio.wait_for(ws_a.recv(), 2)
        await ws_b.close()
        notif = json.loads(await asyncio.wait_for(ws_a.recv(), 2))
        assert notif["type"] == protocol.PEER_LEFT
        assert notif["name"] == "Bob"
        assert notif["id"] == reg_b["id"]
    finally:
        await ws_a.close()


@pytest.mark.asyncio
async def test_ping_pong(server):
    ws, _ = await register_client(server, "Alice")
    try:
        await ws.send(json.dumps(protocol.ping()))
        resp = json.loads(await asyncio.wait_for(ws.recv(), 2))
        assert resp["type"] == protocol.PONG
    finally:
        await ws.close()


@pytest.mark.asyncio
async def test_name_resolution_case_insensitive(server):
    ws_a, reg_a = await register_client(server, "Alice")
    ws_b, reg_b = await register_client(server, "Bob")
    try:
        await asyncio.wait_for(ws_a.recv(), 2)
        await ws_b.send(json.dumps(protocol.msg("alice", "hi alice")))
        relayed = json.loads(await asyncio.wait_for(ws_a.recv(), 2))
        assert relayed["type"] == protocol.MSG
        assert relayed["body"] == "hi alice"
        assert relayed["from"] == reg_b["id"]
        assert relayed["from_name"] == "Bob"
    finally:
        await ws_a.close()
        await ws_b.close()


# ---------------------------------------------------------------------------
# Capability tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_capability_announce(server):
    ws_a, _ = await register_client(server, "Alice")
    ws_b, _ = await register_client(server, "Bob")
    try:
        await asyncio.wait_for(ws_a.recv(), 2)  # drain peer_joined
        caps = {"tools": ["bash", "python"], "skills": ["coding"]}
        await ws_b.send(json.dumps(protocol.capability_announce(caps)))
        notif = json.loads(await asyncio.wait_for(ws_a.recv(), 2))
        assert notif["type"] == protocol.CAPABILITY_ANNOUNCE
        assert notif["capabilities"]["tools"] == ["bash", "python"]
    finally:
        await ws_a.close()
        await ws_b.close()


@pytest.mark.asyncio
async def test_capability_query(server):
    ws_a, _ = await register_client(server, "Alice",
                                     capabilities={"tools": ["bash"], "skills": ["coding"]})
    ws_b, _ = await register_client(server, "Bob")
    try:
        await asyncio.wait_for(ws_a.recv(), 2)
        msg = protocol.capability_query(required_skills=["coding"])
        msg["req_id"] = "cq1"
        await ws_b.send(json.dumps(msg))
        resp = json.loads(await asyncio.wait_for(ws_b.recv(), 2))
        assert resp["type"] == protocol.CAPABILITY_RESPONSE
        assert len(resp["matches"]) == 1
        assert resp["matches"][0]["name"] == "Alice"
    finally:
        await ws_a.close()
        await ws_b.close()


@pytest.mark.asyncio
async def test_capability_query_no_match(server):
    ws_a, _ = await register_client(server, "Alice",
                                     capabilities={"tools": ["bash"]})
    ws_b, _ = await register_client(server, "Bob")
    try:
        await asyncio.wait_for(ws_a.recv(), 2)
        msg = protocol.capability_query(required_skills=["machine-learning"])
        msg["req_id"] = "cq2"
        await ws_b.send(json.dumps(msg))
        resp = json.loads(await asyncio.wait_for(ws_b.recv(), 2))
        assert resp["matches"] == []
    finally:
        await ws_a.close()
        await ws_b.close()


# ---------------------------------------------------------------------------
# Group tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_group_join_and_message(server):
    ws_a, _ = await register_client(server, "Alice")
    ws_b, _ = await register_client(server, "Bob")
    try:
        await asyncio.wait_for(ws_a.recv(), 2)  # drain peer_joined

        # Alice joins first
        await ws_a.send(json.dumps(protocol.group_join("team")))
        await asyncio.sleep(0.1)

        # Bob joins — gets state_sync first, then Alice gets peer_joined
        await ws_b.send(json.dumps(protocol.group_join("team")))
        # Drain any state_sync or join notifications Bob receives
        await asyncio.sleep(0.1)

        # Alice should have received Bob's join notification
        notif = json.loads(await asyncio.wait_for(ws_a.recv(), 2))
        assert notif["type"] == protocol.PEER_JOINED
        assert notif["group"] == "team"

        # Alice sends group message
        await ws_a.send(json.dumps(protocol.group_msg("team", "hello team")))
        msg = json.loads(await asyncio.wait_for(ws_b.recv(), 2))
        assert msg["type"] == protocol.GROUP_MSG
        assert msg["body"] == "hello team"
        assert msg["group"] == "team"
    finally:
        await ws_a.close()
        await ws_b.close()


@pytest.mark.asyncio
async def test_group_list(server):
    ws_a, _ = await register_client(server, "Alice")
    try:
        await ws_a.send(json.dumps(protocol.group_join("team1")))
        await ws_a.send(json.dumps(protocol.group_join("team2")))
        msg = protocol.group_list()
        msg["req_id"] = "gl1"
        await ws_a.send(json.dumps(msg))
        resp = json.loads(await asyncio.wait_for(ws_a.recv(), 2))
        assert resp["type"] == protocol.GROUP_LIST
        assert resp["groups"]["team1"] == 1
        assert resp["groups"]["team2"] == 1
    finally:
        await ws_a.close()


@pytest.mark.asyncio
async def test_group_leave(server):
    ws_a, _ = await register_client(server, "Alice")
    ws_b, _ = await register_client(server, "Bob")
    try:
        await asyncio.wait_for(ws_a.recv(), 2)
        await ws_a.send(json.dumps(protocol.group_join("team")))
        await asyncio.sleep(0.1)
        await ws_b.send(json.dumps(protocol.group_join("team")))
        await asyncio.sleep(0.2)
        # Drain all pending notifications
        for ws in [ws_a, ws_b]:
            while True:
                try:
                    await asyncio.wait_for(ws.recv(), 0.1)
                except (asyncio.TimeoutError, TimeoutError):
                    break

        await ws_a.send(json.dumps(protocol.group_leave("team")))
        notif = json.loads(await asyncio.wait_for(ws_b.recv(), 2))
        assert notif["type"] == protocol.PEER_LEFT
        assert notif["group"] == "team"
    finally:
        await ws_a.close()
        await ws_b.close()


# ---------------------------------------------------------------------------
# Shared state tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_state_set_and_get(server):
    ws_a, _ = await register_client(server, "Alice")
    try:
        await ws_a.send(json.dumps(protocol.state_set("counter", 42)))
        msg = protocol.state_get("counter")
        msg["req_id"] = "sg1"
        await ws_a.send(json.dumps(msg))
        resp = json.loads(await asyncio.wait_for(ws_a.recv(), 2))
        assert resp["type"] == protocol.STATE_VALUE
        assert resp["value"] == 42
        assert resp["exists"] is True
    finally:
        await ws_a.close()


@pytest.mark.asyncio
async def test_state_broadcast(server):
    ws_a, _ = await register_client(server, "Alice")
    ws_b, _ = await register_client(server, "Bob")
    try:
        await asyncio.wait_for(ws_a.recv(), 2)
        await ws_a.send(json.dumps(protocol.state_set("key", "value")))
        notif = json.loads(await asyncio.wait_for(ws_b.recv(), 2))
        assert notif["type"] == protocol.STATE_SET
        assert notif["key"] == "key"
        assert notif["value"] == "value"
    finally:
        await ws_a.close()
        await ws_b.close()


@pytest.mark.asyncio
async def test_state_sync(server):
    ws_a, _ = await register_client(server, "Alice")
    try:
        await ws_a.send(json.dumps(protocol.state_set("a", 1)))
        await ws_a.send(json.dumps(protocol.state_set("b", 2)))
        msg = protocol.state_sync()
        msg["req_id"] = "ss1"
        await ws_a.send(json.dumps(msg))
        resp = json.loads(await asyncio.wait_for(ws_a.recv(), 2))
        assert resp["type"] == protocol.STATE_SYNC
        assert resp["state"]["a"] == 1
        assert resp["state"]["b"] == 2
    finally:
        await ws_a.close()


# ---------------------------------------------------------------------------
# Status / presence tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_status_update_broadcast(server):
    ws_a, _ = await register_client(server, "Alice")
    ws_b, _ = await register_client(server, "Bob")
    try:
        await asyncio.wait_for(ws_a.recv(), 2)
        await ws_b.send(json.dumps(protocol.status_update("busy", "coding")))
        notif = json.loads(await asyncio.wait_for(ws_a.recv(), 2))
        assert notif["type"] == protocol.STATUS_UPDATE
        assert notif["status"] == "busy"
        assert notif["task"] == "coding"
    finally:
        await ws_a.close()
        await ws_b.close()


# ---------------------------------------------------------------------------
# Task broadcast tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_task_broadcast_relay(server):
    ws_a, reg_a = await register_client(server, "Alice")
    ws_b, _ = await register_client(server, "Bob")
    try:
        await asyncio.wait_for(ws_a.recv(), 2)
        await ws_a.send(json.dumps(protocol.task_broadcast("t1", "analyze data")))
        msg = json.loads(await asyncio.wait_for(ws_b.recv(), 2))
        assert msg["type"] == protocol.TASK_BROADCAST
        assert msg["task"] == "analyze data"
        assert msg["from_name"] == "Alice"
    finally:
        await ws_a.close()
        await ws_b.close()


@pytest.mark.asyncio
async def test_task_assign_and_result(server):
    ws_a, reg_a = await register_client(server, "Alice")
    ws_b, reg_b = await register_client(server, "Bob")
    try:
        await asyncio.wait_for(ws_a.recv(), 2)
        # Alice assigns task to Bob
        await ws_a.send(json.dumps(protocol.task_assign(reg_b["id"], "t2", "build feature")))
        msg = json.loads(await asyncio.wait_for(ws_b.recv(), 2))
        assert msg["type"] == protocol.TASK_ASSIGN
        assert msg["task"] == "build feature"

        # Bob returns result
        await ws_b.send(json.dumps(protocol.task_result(reg_a["id"], "t2", "done!", True)))
        result = json.loads(await asyncio.wait_for(ws_a.recv(), 2))
        assert result["type"] == protocol.TASK_RESULT
        assert result["result"] == "done!"
    finally:
        await ws_a.close()
        await ws_b.close()


@pytest.mark.asyncio
async def test_desktop_session_list_relay(server):
    ws_a, _ = await register_client(server, "Alice")
    ws_b, reg_b = await register_client(server, "Bob")
    try:
        await asyncio.wait_for(ws_a.recv(), 2)
        await ws_a.send(json.dumps(protocol.desktop_session_list(reg_b["id"], req_id="desk-1")))
        msg = json.loads(await asyncio.wait_for(ws_b.recv(), 2))
        assert msg["type"] == protocol.DESKTOP_SESSION_LIST
        assert msg["req_id"] == "desk-1"
        assert msg["from_name"] == "Alice"
    finally:
        await ws_a.close()
        await ws_b.close()


@pytest.mark.asyncio
async def test_desktop_input_relay(server):
    ws_a, _ = await register_client(server, "Alice")
    ws_b, reg_b = await register_client(server, "Bob")
    try:
        await asyncio.wait_for(ws_a.recv(), 2)
        await ws_a.send(json.dumps(protocol.desktop_input(reg_b["id"], "sess-1", {"type": "click"})))
        msg = json.loads(await asyncio.wait_for(ws_b.recv(), 2))
        assert msg["type"] == protocol.DESKTOP_INPUT
        assert msg["session_id"] == "sess-1"
        assert msg["action"] == {"type": "click"}
        assert msg["from_name"] == "Alice"
    finally:
        await ws_a.close()
        await ws_b.close()


# ---------------------------------------------------------------------------
# Vote tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_vote_propose_broadcast(server):
    ws_a, _ = await register_client(server, "Alice")
    ws_b, _ = await register_client(server, "Bob")
    try:
        await asyncio.wait_for(ws_a.recv(), 2)
        await ws_a.send(json.dumps(protocol.vote_propose("v1", "ship it?")))
        msg = json.loads(await asyncio.wait_for(ws_b.recv(), 2))
        assert msg["type"] == protocol.VOTE_PROPOSE
        assert msg["proposal"] == "ship it?"
    finally:
        await ws_a.close()
        await ws_b.close()


# ---------------------------------------------------------------------------
# Election tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_election_broadcast(server):
    ws_a, _ = await register_client(server, "Alice")
    ws_b, _ = await register_client(server, "Bob")
    try:
        await asyncio.wait_for(ws_a.recv(), 2)
        await ws_a.send(json.dumps(protocol.election_start("e1")))
        msg = json.loads(await asyncio.wait_for(ws_b.recv(), 2))
        assert msg["type"] == protocol.ELECTION_START
        assert msg["election_id"] == "e1"
    finally:
        await ws_a.close()
        await ws_b.close()


# ---------------------------------------------------------------------------
# Rate limiting test
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rate_limiting(server):
    ws, _ = await register_client(server, "Alice")
    try:
        # Send a burst of messages — eventually should get rate limited
        errors = []
        for i in range(100):
            await ws.send(json.dumps(protocol.ping()))
            try:
                resp = json.loads(await asyncio.wait_for(ws.recv(), 0.1))
                if resp["type"] == protocol.ERROR and "rate limited" in resp["message"]:
                    errors.append(resp)
                    break
            except asyncio.TimeoutError:
                pass
        assert len(errors) > 0, "Expected rate limiting to kick in"
    finally:
        await ws.close()


# ---------------------------------------------------------------------------
# Queue tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_queue_push_and_pull(server):
    ws_a, reg_a = await register_client(server, "Alice")
    ws_b, reg_b = await register_client(server, "Bob")
    try:
        await asyncio.wait_for(ws_a.recv(), 2)

        # Alice pushes a job
        await ws_a.send(json.dumps(protocol.queue_push("tasks", "j1", {"action": "build"}, priority=5)))
        ack = json.loads(await asyncio.wait_for(ws_a.recv(), 2))
        assert ack["type"] == protocol.ACK
        assert ack["status"] == "queued"

        # Bob pulls from queue
        pull_msg = protocol.queue_pull("tasks", worker_id=reg_b["id"])
        pull_msg["req_id"] = "pull-1"
        await ws_b.send(json.dumps(pull_msg))
        resp = json.loads(await asyncio.wait_for(ws_b.recv(), 2))
        assert resp["type"] == protocol.QUEUE_PULL
        assert resp["job_id"] == "j1"
        assert resp["payload"] == {"action": "build"}
        assert resp["req_id"] == "pull-1"
    finally:
        await ws_a.close()
        await ws_b.close()


@pytest.mark.asyncio
async def test_queue_pull_empty(server):
    ws, _ = await register_client(server, "Alice")
    try:
        pull_msg = protocol.queue_pull("empty-queue")
        pull_msg["req_id"] = "r1"
        await ws.send(json.dumps(pull_msg))
        resp = json.loads(await asyncio.wait_for(ws.recv(), 2))
        assert resp["type"] == protocol.QUEUE_PULL
        assert resp["job_id"] is None
    finally:
        await ws.close()


@pytest.mark.asyncio
async def test_queue_ack_notifies_submitter(server):
    ws_a, reg_a = await register_client(server, "Alice")
    ws_b, reg_b = await register_client(server, "Bob")
    try:
        await asyncio.wait_for(ws_a.recv(), 2)

        # Alice pushes
        await ws_a.send(json.dumps(protocol.queue_push("work", "j2", {"x": 1})))
        await asyncio.wait_for(ws_a.recv(), 2)  # ack

        # Bob pulls
        pull_msg = protocol.queue_pull("work", worker_id=reg_b["id"])
        pull_msg["req_id"] = "p1"
        await ws_b.send(json.dumps(pull_msg))
        await asyncio.wait_for(ws_b.recv(), 2)

        # Bob acks with result
        await ws_b.send(json.dumps(protocol.queue_ack("work", "j2", result="built!", success=True)))

        # Alice should get notified
        notif = json.loads(await asyncio.wait_for(ws_a.recv(), 2))
        assert notif["type"] == protocol.JOB_RESULT
        assert notif["job_id"] == "j2"
        assert notif["result"] == "built!"
        assert notif["status"] == "completed"
    finally:
        await ws_a.close()
        await ws_b.close()


@pytest.mark.asyncio
async def test_queue_status(server):
    ws, _ = await register_client(server, "Alice")
    try:
        # Push two jobs
        await ws.send(json.dumps(protocol.queue_push("q1", "a", {"x": 1})))
        await asyncio.wait_for(ws.recv(), 2)
        await ws.send(json.dumps(protocol.queue_push("q1", "b", {"x": 2})))
        await asyncio.wait_for(ws.recv(), 2)

        # Query status
        status_msg = protocol.queue_status("q1", req_id="s1")
        await ws.send(json.dumps(status_msg))
        resp = json.loads(await asyncio.wait_for(ws.recv(), 2))
        assert resp["type"] == protocol.QUEUE_STATUS
        assert resp["req_id"] == "s1"
        assert resp["status"]["pending"] == 2
        assert resp["status"]["total"] == 2
    finally:
        await ws.close()


@pytest.mark.asyncio
async def test_worker_register(server):
    ws, _ = await register_client(server, "Alice")
    try:
        await ws.send(json.dumps(protocol.worker_register("w1", queues=["tasks"])))
        resp = json.loads(await asyncio.wait_for(ws.recv(), 2))
        assert resp["type"] == protocol.ACK
        assert resp["status"] == "registered"
    finally:
        await ws.close()


@pytest.mark.asyncio
async def test_job_relay(server):
    """Job submit/result are relayed point-to-point like other relay types."""
    ws_a, reg_a = await register_client(server, "Alice")
    ws_b, reg_b = await register_client(server, "Bob")
    try:
        await asyncio.wait_for(ws_a.recv(), 2)

        # Alice submits job to Bob
        await ws_a.send(json.dumps(protocol.job_submit(
            reg_b["id"], "j1", "builtin", "math.factorial", args=[5])))
        msg = json.loads(await asyncio.wait_for(ws_b.recv(), 2))
        assert msg["type"] == protocol.JOB_SUBMIT
        assert msg["func"] == "math.factorial"
        assert msg["args"] == [5]

        # Bob sends result back
        await ws_b.send(json.dumps(protocol.job_result(
            reg_a["id"], "j1", "completed", result=120)))
        result = json.loads(await asyncio.wait_for(ws_a.recv(), 2))
        assert result["type"] == protocol.JOB_RESULT
        assert result["result"] == 120
    finally:
        await ws_a.close()
        await ws_b.close()


@pytest.mark.asyncio
async def test_job_list(server):
    ws, _ = await register_client(server, "Alice")
    try:
        list_msg = protocol.job_list(req_id="jl1")
        await ws.send(json.dumps(list_msg))
        resp = json.loads(await asyncio.wait_for(ws.recv(), 2))
        assert resp["type"] == protocol.JOB_LIST
        assert resp["req_id"] == "jl1"
        assert isinstance(resp["jobs"], list)
    finally:
        await ws.close()
