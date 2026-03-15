"""Unit tests for the burrow protocol module."""

import json

import pytest

from burrow import protocol


# ---------------------------------------------------------------------------
# 1. Builder functions return correct structure
# ---------------------------------------------------------------------------

class TestRegister:
    def test_structure(self):
        result = protocol.register("laptop")
        assert result == {"type": "register", "name": "laptop"}

    def test_different_name(self):
        result = protocol.register("server-01")
        assert result["name"] == "server-01"
        assert result["type"] == "register"

    def test_with_token(self):
        result = protocol.register("laptop", token="secret")
        assert result["token"] == "secret"

    def test_with_reconnect_id(self):
        result = protocol.register("laptop", reconnect_id="abc123")
        assert result["reconnect_id"] == "abc123"

    def test_with_capabilities(self):
        caps = {"tools": ["bash"], "skills": ["coding"]}
        result = protocol.register("laptop", capabilities=caps)
        assert result["capabilities"] == caps


class TestPeers:
    def test_structure(self):
        assert protocol.peers() == {"type": "peers"}

    def test_with_req_id(self):
        result = protocol.peers(req_id="r123")
        assert result == {"type": "peers", "req_id": "r123"}


class TestMsg:
    def test_structure(self):
        result = protocol.msg("abc", "hello")
        assert result == {"type": "msg", "to": "abc", "body": "hello"}

    def test_empty_body(self):
        result = protocol.msg("peer1", "")
        assert result["body"] == ""

    def test_with_msg_id(self):
        result = protocol.msg("abc", "hello", msg_id="m1")
        assert result["msg_id"] == "m1"


class TestFileStart:
    def test_structure(self):
        result = protocol.file_start(
            to="peer1", name="report.pdf", size=1024, transfer_id="tx-001"
        )
        assert result == {
            "type": "file_start",
            "to": "peer1",
            "name": "report.pdf",
            "size": 1024,
            "transfer_id": "tx-001",
        }


class TestFileChunk:
    def test_structure(self):
        result = protocol.file_chunk(
            to="peer1", transfer_id="tx-001", seq=0, data="AAAA", final=False
        )
        assert result == {
            "type": "file_chunk",
            "to": "peer1",
            "transfer_id": "tx-001",
            "seq": 0,
            "data": "AAAA",
            "final": False,
        }

    def test_final_chunk(self):
        result = protocol.file_chunk(
            to="peer1", transfer_id="tx-001", seq=5, data="ZZ", final=True
        )
        assert result["final"] is True
        assert result["seq"] == 5


class TestTunnelOpen:
    def test_structure(self):
        result = protocol.tunnel_open(
            to="peer2", tunnel_id="tun-1", remote_port=8080
        )
        assert result == {
            "type": "tunnel_open",
            "to": "peer2",
            "tunnel_id": "tun-1",
            "remote_port": 8080,
        }


class TestTunnelAccept:
    def test_structure(self):
        result = protocol.tunnel_accept(to="peer2", tunnel_id="tun-1")
        assert result == {
            "type": "tunnel_accept",
            "to": "peer2",
            "tunnel_id": "tun-1",
        }


class TestTunnelData:
    def test_structure(self):
        result = protocol.tunnel_data(
            to="peer2", tunnel_id="tun-1", data="payload"
        )
        assert result == {
            "type": "tunnel_data",
            "to": "peer2",
            "tunnel_id": "tun-1",
            "data": "payload",
        }


class TestTunnelClose:
    def test_structure(self):
        result = protocol.tunnel_close(to="peer2", tunnel_id="tun-1")
        assert result == {
            "type": "tunnel_close",
            "to": "peer2",
            "tunnel_id": "tun-1",
        }


class TestError:
    def test_structure(self):
        result = protocol.error("bad")
        assert result == {"type": "error", "message": "bad"}


class TestPing:
    def test_structure(self):
        assert protocol.ping() == {"type": "ping"}


class TestPong:
    def test_structure(self):
        assert protocol.pong() == {"type": "pong"}


class TestAck:
    def test_structure(self):
        assert protocol.ack("m1") == {"type": "ack", "msg_id": "m1"}


class TestNack:
    def test_structure(self):
        result = protocol.nack("m1", "not found")
        assert result == {"type": "nack", "msg_id": "m1", "reason": "not found"}


class TestCapabilities:
    def test_announce(self):
        caps = {"tools": ["bash"], "model": "opus"}
        result = protocol.capability_announce(caps)
        assert result["type"] == "capability_announce"
        assert result["capabilities"] == caps

    def test_query(self):
        result = protocol.capability_query(required_skills=["coding"])
        assert result["type"] == "capability_query"
        assert result["required_skills"] == ["coding"]


class TestGroups:
    def test_join(self):
        result = protocol.group_join("dev-team")
        assert result == {"type": "group_join", "group": "dev-team"}

    def test_leave(self):
        result = protocol.group_leave("dev-team")
        assert result == {"type": "group_leave", "group": "dev-team"}

    def test_msg(self):
        result = protocol.group_msg("dev-team", "hello all")
        assert result["type"] == "group_msg"
        assert result["group"] == "dev-team"
        assert result["body"] == "hello all"


class TestSharedState:
    def test_set(self):
        result = protocol.state_set("counter", 42, "team")
        assert result == {"type": "state_set", "key": "counter", "value": 42, "group": "team"}

    def test_get(self):
        result = protocol.state_get("counter")
        assert result == {"type": "state_get", "key": "counter"}

    def test_delete(self):
        result = protocol.state_delete("counter", "team")
        assert result["type"] == "state_delete"
        assert result["group"] == "team"


class TestTaskCoordination:
    def test_broadcast(self):
        result = protocol.task_broadcast("t1", "do something", 30.0, ["coding"])
        assert result["type"] == "task_broadcast"
        assert result["task_id"] == "t1"
        assert result["required_skills"] == ["coding"]

    def test_assign(self):
        result = protocol.task_assign("peer1", "t2", "do this", 1, {"key": "val"})
        assert result["type"] == "task_assign"
        assert result["to"] == "peer1"
        assert result["priority"] == 1

    def test_result(self):
        result = protocol.task_result("peer1", "t2", "done", True, ["file.txt"])
        assert result["type"] == "task_result"
        assert result["artifacts"] == ["file.txt"]


class TestVoting:
    def test_propose(self):
        result = protocol.vote_propose("v1", "merge?", ["yes", "no"], 10.0)
        assert result["type"] == "vote_propose"
        assert result["options"] == ["yes", "no"]

    def test_cast(self):
        result = protocol.vote_cast("peer1", "v1", "yes", "looks good")
        assert result["type"] == "vote_cast"
        assert result["choice"] == "yes"

    def test_result(self):
        result = protocol.vote_result("v1", "merge?", {"yes": 2}, "yes", [])
        assert result["type"] == "vote_result"
        assert result["outcome"] == "yes"


class TestJobSubmit:
    def test_structure(self):
        result = protocol.job_submit("peer1", "j1", "builtin", "math.factorial",
                                      args=[10], kwargs={"key": "val"})
        assert result["type"] == "job_submit"
        assert result["to"] == "peer1"
        assert result["job_id"] == "j1"
        assert result["runtime"] == "builtin"
        assert result["func"] == "math.factorial"
        assert result["args"] == [10]

    def test_defaults(self):
        result = protocol.job_submit("p", "j2", "ray", "mod.fn")
        assert result["args"] == []
        assert result["kwargs"] == {}
        assert result["resources"] == {}

    def test_with_script(self):
        result = protocol.job_submit("p", "j3", "builtin", "__script__:run.py",
                                      script="c2NyaXB0", script_name="run.py")
        assert result["script"] == "c2NyaXB0"
        assert result["script_name"] == "run.py"

    def test_without_script(self):
        result = protocol.job_submit("p", "j4", "builtin", "math.factorial")
        assert "script" not in result
        assert "script_name" not in result


class TestJobResult:
    def test_structure(self):
        result = protocol.job_result("peer1", "j1", "completed", result=42)
        assert result["type"] == "job_result"
        assert result["result"] == 42

    def test_with_error(self):
        result = protocol.job_result("peer1", "j1", "failed", error="boom")
        assert result["error"] == "boom"


class TestJobCancel:
    def test_structure(self):
        result = protocol.job_cancel("peer1", "j1")
        assert result == {"type": "job_cancel", "to": "peer1", "job_id": "j1"}


class TestJobList:
    def test_structure(self):
        result = protocol.job_list()
        assert result == {"type": "job_list"}

    def test_with_req_id(self):
        result = protocol.job_list(req_id="r1")
        assert result["req_id"] == "r1"


class TestQueuePush:
    def test_structure(self):
        result = protocol.queue_push("tasks", "j1", {"action": "build"}, priority=5)
        assert result["type"] == "queue_push"
        assert result["queue"] == "tasks"
        assert result["priority"] == 5

    def test_defaults(self):
        result = protocol.queue_push("q", "j2", {})
        assert result["priority"] == 0


class TestQueuePull:
    def test_structure(self):
        result = protocol.queue_pull("tasks", worker_id="w1")
        assert result["type"] == "queue_pull"
        assert result["worker_id"] == "w1"


class TestQueueAck:
    def test_structure(self):
        result = protocol.queue_ack("tasks", "j1", result="done", success=True)
        assert result["type"] == "queue_ack"
        assert result["result"] == "done"


class TestQueueStatus:
    def test_structure(self):
        result = protocol.queue_status("tasks", req_id="r1")
        assert result["type"] == "queue_status"
        assert result["req_id"] == "r1"


class TestWorkerRegister:
    def test_structure(self):
        result = protocol.worker_register("w1", queues=["tasks"], capabilities={"gpu": True})
        assert result["type"] == "worker_register"
        assert result["queues"] == ["tasks"]


class TestWorkerHeartbeat:
    def test_structure(self):
        result = protocol.worker_heartbeat("w1", status="busy", current_job="j1")
        assert result["type"] == "worker_heartbeat"
        assert result["current_job"] == "j1"


class TestElection:
    def test_start(self):
        result = protocol.election_start("e1")
        assert result == {"type": "election_start", "election_id": "e1"}

    def test_alive(self):
        result = protocol.election_alive("peer1", "e1")
        assert result == {"type": "election_alive", "to": "peer1", "election_id": "e1"}

    def test_victory(self):
        result = protocol.election_victory("e1")
        assert result == {"type": "election_victory", "election_id": "e1"}


# ---------------------------------------------------------------------------
# 2. All messages are JSON-serializable
# ---------------------------------------------------------------------------

_ALL_BUILDERS = [
    lambda: protocol.register("laptop"),
    lambda: protocol.register("laptop", token="s", capabilities={"tools": []}),
    lambda: protocol.peers(),
    lambda: protocol.peers(req_id="r1"),
    lambda: protocol.msg("abc", "hello"),
    lambda: protocol.msg("abc", "hello", msg_id="m1"),
    lambda: protocol.file_start("p", "f.txt", 99, "tx"),
    lambda: protocol.file_chunk("p", "tx", 0, "AA", False),
    lambda: protocol.file_chunk("p", "tx", 1, "BB", True),
    lambda: protocol.tunnel_open("p", "t1", 8080),
    lambda: protocol.tunnel_accept("p", "t1"),
    lambda: protocol.tunnel_data("p", "t1", "bytes"),
    lambda: protocol.tunnel_close("p", "t1"),
    lambda: protocol.error("oops"),
    lambda: protocol.ping(),
    lambda: protocol.pong(),
    lambda: protocol.ack("m1"),
    lambda: protocol.nack("m1", "gone"),
    lambda: protocol.queued("m1", 3),
    lambda: protocol.capability_announce({"tools": ["bash"]}),
    lambda: protocol.capability_query(required_skills=["coding"]),
    lambda: protocol.group_join("team"),
    lambda: protocol.group_leave("team"),
    lambda: protocol.group_msg("team", "hi"),
    lambda: protocol.group_list(),
    lambda: protocol.group_members("team"),
    lambda: protocol.state_set("k", "v"),
    lambda: protocol.state_get("k"),
    lambda: protocol.state_delete("k"),
    lambda: protocol.state_sync(),
    lambda: protocol.status_update("busy", "working"),
    lambda: protocol.task_broadcast("t1", "do it"),
    lambda: protocol.task_response("p", "t1", "done"),
    lambda: protocol.task_assign("p", "t1", "do it"),
    lambda: protocol.task_status("p", "t1", "in_progress"),
    lambda: protocol.task_result("p", "t1", "done"),
    lambda: protocol.vote_propose("v1", "yes?"),
    lambda: protocol.vote_cast("p", "v1", "yes"),
    lambda: protocol.vote_result("v1", "yes?", {"yes": 1}, "yes", []),
    lambda: protocol.election_start("e1"),
    lambda: protocol.election_alive("p", "e1"),
    lambda: protocol.election_victory("e1"),
    lambda: protocol.job_submit("p", "j1", "builtin", "math.factorial", [10]),
    lambda: protocol.job_status("p", "j1"),
    lambda: protocol.job_result("p", "j1", "completed", result=42),
    lambda: protocol.job_cancel("p", "j1"),
    lambda: protocol.job_list(),
    lambda: protocol.job_update("p", "j1", "running", progress=0.5),
    lambda: protocol.queue_push("q", "j1", {"a": 1}),
    lambda: protocol.queue_pull("q"),
    lambda: protocol.queue_ack("q", "j1", result="ok"),
    lambda: protocol.queue_status("q"),
    lambda: protocol.worker_register("w1", ["q"]),
    lambda: protocol.worker_heartbeat("w1"),
    lambda: protocol.exec_request("p", "e1", "ls -la"),
    lambda: protocol.exec_request("p", "e1", "ls", cwd="/tmp", env={"FOO": "bar"}),
    lambda: protocol.exec_response("p", "e1", 0, stdout="ok", stderr=""),
    lambda: protocol.exec_response("p", "e1", 1, error="failed"),
    lambda: protocol.reverse_tunnel_request("p", "t1", 2222, 22),
    lambda: protocol.reverse_tunnel_accept("p", "t1"),
    lambda: protocol.update_available("0.6.0", "0.5.0", changelog="fixes"),
    lambda: protocol.update_status("0.6.0", "updated"),
    lambda: protocol.update_status("0.6.0", "failed", error="git pull failed"),
]


@pytest.mark.parametrize("builder", _ALL_BUILDERS, ids=range(len(_ALL_BUILDERS)))
def test_json_serializable(builder):
    result = builder()
    serialized = json.dumps(result)
    assert isinstance(serialized, str)
    roundtrip = json.loads(serialized)
    assert roundtrip == result


# ---------------------------------------------------------------------------
# 3. Constants exist and are correct
# ---------------------------------------------------------------------------

class TestConstants:
    def test_default_port(self):
        assert protocol.DEFAULT_PORT == 7654

    def test_chunk_size(self):
        assert protocol.CHUNK_SIZE == 524288

    def test_version(self):
        assert protocol.VERSION == "0.6.1"

    def test_keepalive_defaults(self):
        assert protocol.DEFAULT_KEEPALIVE_INTERVAL == 15
        assert protocol.DEFAULT_KEEPALIVE_TIMEOUT == 10


# ---------------------------------------------------------------------------
# 4. All message type constants are strings
# ---------------------------------------------------------------------------

_TYPE_CONSTANTS = [
    protocol.REGISTER, protocol.REGISTERED, protocol.PEERS,
    protocol.PEER_JOINED, protocol.PEER_LEFT,
    protocol.MSG, protocol.FILE_START, protocol.FILE_CHUNK,
    protocol.TUNNEL_OPEN, protocol.TUNNEL_ACCEPT,
    protocol.TUNNEL_DATA, protocol.TUNNEL_CLOSE,
    protocol.PING, protocol.PONG, protocol.ERROR,
    protocol.ACK, protocol.NACK, protocol.QUEUED,
    protocol.CAPABILITY_ANNOUNCE, protocol.CAPABILITY_QUERY,
    protocol.CAPABILITY_RESPONSE,
    protocol.GROUP_JOIN, protocol.GROUP_LEAVE, protocol.GROUP_MSG,
    protocol.GROUP_LIST, protocol.GROUP_MEMBERS,
    protocol.STATE_SET, protocol.STATE_GET, protocol.STATE_VALUE,
    protocol.STATE_DELETE, protocol.STATE_SYNC,
    protocol.STATUS_UPDATE,
    protocol.TASK_BROADCAST, protocol.TASK_RESPONSE,
    protocol.TASK_ASSIGN, protocol.TASK_STATUS, protocol.TASK_RESULT,
    protocol.VOTE_PROPOSE, protocol.VOTE_CAST, protocol.VOTE_RESULT,
    protocol.ELECTION_START, protocol.ELECTION_ALIVE, protocol.ELECTION_VICTORY,
    protocol.JOB_SUBMIT, protocol.JOB_STATUS, protocol.JOB_RESULT,
    protocol.JOB_CANCEL, protocol.JOB_LIST, protocol.JOB_UPDATE,
    protocol.QUEUE_PUSH, protocol.QUEUE_PULL, protocol.QUEUE_ACK,
    protocol.QUEUE_STATUS, protocol.WORKER_REGISTER, protocol.WORKER_HEARTBEAT,
    protocol.EXEC_REQUEST, protocol.EXEC_RESPONSE,
    protocol.REVERSE_TUNNEL_REQUEST, protocol.REVERSE_TUNNEL_ACCEPT,
    protocol.UPDATE_AVAILABLE, protocol.UPDATE_STATUS,
]


@pytest.mark.parametrize("const", _TYPE_CONSTANTS)
def test_message_type_is_string(const):
    assert isinstance(const, str)
    assert len(const) > 0
