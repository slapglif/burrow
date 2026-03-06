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
    # IMPORTANT: clear server global state between tests
    from burrow import server as srv_mod
    srv_mod.peers.clear()
    srv_mod.by_id.clear()
    yield uri
    srv.close()
    await srv.wait_closed()


async def register_client(uri, name):
    ws = await websockets.connect(uri)
    await ws.send(json.dumps(protocol.register(name)))
    resp = json.loads(await asyncio.wait_for(ws.recv(), 2))
    return ws, resp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register(server):
    """Connect, send register, verify registered response with id and name."""
    ws, resp = await register_client(server, "Alice")
    try:
        assert resp["type"] == protocol.REGISTERED
        assert resp["name"] == "Alice"
        assert "id" in resp and len(resp["id"]) > 0
    finally:
        await ws.close()


@pytest.mark.asyncio
async def test_peers_empty(server):
    """Register one client, request peers, verify empty list."""
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
    """Connect 2 clients, register both, request peers from one, verify other appears."""
    ws_a, reg_a = await register_client(server, "Alice")
    ws_b, reg_b = await register_client(server, "Bob")
    try:
        # Drain the peer_joined notification that Alice received when Bob joined
        notif = json.loads(await asyncio.wait_for(ws_a.recv(), 2))
        assert notif["type"] == protocol.PEER_JOINED

        # Ask Alice for peer list -- should see Bob
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
async def test_msg_relay(server):
    """Register 2 clients, send msg from A to B by name, verify B receives with from/from_name."""
    ws_a, reg_a = await register_client(server, "Alice")
    ws_b, reg_b = await register_client(server, "Bob")
    try:
        # Drain peer_joined on Alice's side
        await asyncio.wait_for(ws_a.recv(), 2)

        # Alice sends a message to Bob by name
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
async def test_msg_to_unknown_peer(server):
    """Register, send msg to nonexistent peer, verify error response."""
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
    """Connect A, register A, then connect B, register B, verify A receives peer_joined."""
    ws_a, _ = await register_client(server, "Alice")
    try:
        # Now Bob joins -- Alice should receive a peer_joined broadcast
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
    """Connect A and B, register both, disconnect B, verify A receives peer_left."""
    ws_a, _ = await register_client(server, "Alice")
    ws_b, reg_b = await register_client(server, "Bob")
    try:
        # Drain peer_joined notification on Alice
        await asyncio.wait_for(ws_a.recv(), 2)

        # Disconnect Bob
        await ws_b.close()

        # Alice should receive peer_left
        notif = json.loads(await asyncio.wait_for(ws_a.recv(), 2))
        assert notif["type"] == protocol.PEER_LEFT
        assert notif["name"] == "Bob"
        assert notif["id"] == reg_b["id"]
    finally:
        await ws_a.close()


@pytest.mark.asyncio
async def test_ping_pong(server):
    """Register, send ping, verify pong."""
    ws, _ = await register_client(server, "Alice")
    try:
        await ws.send(json.dumps(protocol.ping()))
        resp = json.loads(await asyncio.wait_for(ws.recv(), 2))
        assert resp["type"] == protocol.PONG
    finally:
        await ws.close()


@pytest.mark.asyncio
async def test_name_resolution_case_insensitive(server):
    """Register as 'Alice', send msg to 'alice', verify delivery."""
    ws_a, reg_a = await register_client(server, "Alice")
    ws_b, reg_b = await register_client(server, "Bob")
    try:
        # Drain peer_joined on Alice
        await asyncio.wait_for(ws_a.recv(), 2)

        # Send to "alice" (lowercase) from Bob -- should reach Alice
        await ws_b.send(json.dumps(protocol.msg("alice", "hi alice")))
        relayed = json.loads(await asyncio.wait_for(ws_a.recv(), 2))
        assert relayed["type"] == protocol.MSG
        assert relayed["body"] == "hi alice"
        assert relayed["from"] == reg_b["id"]
        assert relayed["from_name"] == "Bob"
    finally:
        await ws_a.close()
        await ws_b.close()
