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


class TestPeers:
    def test_structure(self):
        assert protocol.peers() == {"type": "peers"}


class TestMsg:
    def test_structure(self):
        result = protocol.msg("abc", "hello")
        assert result == {"type": "msg", "to": "abc", "body": "hello"}

    def test_empty_body(self):
        result = protocol.msg("peer1", "")
        assert result["body"] == ""


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


# ---------------------------------------------------------------------------
# 2. All messages are JSON-serializable
# ---------------------------------------------------------------------------

_ALL_BUILDERS = [
    lambda: protocol.register("laptop"),
    lambda: protocol.peers(),
    lambda: protocol.msg("abc", "hello"),
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
        assert protocol.VERSION == "0.2.0"


# ---------------------------------------------------------------------------
# 4. All message type constants are strings
# ---------------------------------------------------------------------------

_TYPE_CONSTANTS = [
    protocol.REGISTER,
    protocol.REGISTERED,
    protocol.PEERS,
    protocol.PEER_JOINED,
    protocol.PEER_LEFT,
    protocol.MSG,
    protocol.FILE_START,
    protocol.FILE_CHUNK,
    protocol.TUNNEL_OPEN,
    protocol.TUNNEL_ACCEPT,
    protocol.TUNNEL_DATA,
    protocol.TUNNEL_CLOSE,
    protocol.PING,
    protocol.PONG,
    protocol.ERROR,
]


@pytest.mark.parametrize("const", _TYPE_CONSTANTS)
def test_message_type_is_string(const):
    assert isinstance(const, str)
    assert len(const) > 0
