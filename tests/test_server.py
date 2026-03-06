"""Integration tests for the burrow relay server."""

import asyncio
import json

import pytest
import pytest_asyncio
import websockets
from websockets.asyncio.client import connect

from burrow import server as srv


@pytest.fixture(autouse=True)
def _reset_server_state():
    """Clear module-level peer state between tests."""
    srv.peers.clear()
    srv.by_id.clear()
    yield
    srv.peers.clear()
    srv.by_id.clear()


@pytest_asyncio.fixture()
async def server_url():
    """Start the burrow server on a random free port, yield the ws:// URL."""
    server = await websockets.serve(srv.handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    url = f"ws://127.0.0.1:{port}"
    yield url
    server.close()
    await server.wait_closed()


async def register_client(url: str, name: str):
    """Connect and register, return (ws, registered_response)."""
    ws = await connect(url)
    await ws.send(json.dumps({"type": "register", "name": name}))
    resp = json.loads(await ws.recv())
    return ws, resp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register(server_url):
    """Connect a client, send register, verify registered response."""
    ws, resp = await register_client(server_url, "alpha")
    try:
        assert resp["type"] == "registered"
        assert resp["name"] == "alpha"
        assert "id" in resp
        assert isinstance(resp["id"], str)
    finally:
        await ws.close()


@pytest.mark.asyncio
async def test_msg_delivery(server_url):
    """Connect two clients, send msg from one to other, verify delivery."""
    ws_a, resp_a = await register_client(server_url, "alice")
    ws_b, resp_b = await register_client(server_url, "bob")

    # Drain the peer_joined notification that bob triggers for alice
    _notif = json.loads(await ws_a.recv())
    assert _notif["type"] == "peer_joined"

    try:
        # Alice sends a message to bob (by name)
        await ws_a.send(json.dumps({"type": "msg", "to": "bob", "body": "hi bob"}))

        msg = json.loads(await ws_b.recv())
        assert msg["type"] == "msg"
        assert msg["body"] == "hi bob"
        assert msg["from"] == resp_a["id"]
        assert msg["from_name"] == "alice"
    finally:
        await ws_a.close()
        await ws_b.close()


@pytest.mark.asyncio
async def test_peer_left_broadcast(server_url):
    """Connect a client, disconnect, verify peer_left is broadcast."""
    ws_a, _ = await register_client(server_url, "alpha")
    ws_b, _ = await register_client(server_url, "bravo")

    # Drain the peer_joined that alpha receives when bravo joins
    _notif = json.loads(await ws_a.recv())
    assert _notif["type"] == "peer_joined"

    try:
        # Close bravo; alpha should get peer_left
        await ws_b.close()

        msg = json.loads(await asyncio.wait_for(ws_a.recv(), timeout=2.0))
        assert msg["type"] == "peer_left"
        assert msg["name"] == "bravo"
    finally:
        await ws_a.close()


@pytest.mark.asyncio
async def test_msg_to_nonexistent_peer(server_url):
    """Send to a nonexistent peer, verify error response."""
    ws, _ = await register_client(server_url, "solo")
    try:
        await ws.send(json.dumps({"type": "msg", "to": "ghost", "body": "hello?"}))
        resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=2.0))
        assert resp["type"] == "error"
        assert "ghost" in resp["message"]
    finally:
        await ws.close()


@pytest.mark.asyncio
async def test_peers_request(server_url):
    """Peers request returns connected peers (excluding self)."""
    ws_a, resp_a = await register_client(server_url, "alice")
    ws_b, resp_b = await register_client(server_url, "bob")

    # Drain peer_joined on alice
    _ = await ws_a.recv()

    try:
        # Ask for peers from alice's perspective
        await ws_a.send(json.dumps({"type": "peers"}))
        resp = json.loads(await asyncio.wait_for(ws_a.recv(), timeout=2.0))

        assert resp["type"] == "peers"
        assert isinstance(resp["peers"], list)
        assert len(resp["peers"]) == 1
        assert resp["peers"][0]["name"] == "bob"
        assert resp["peers"][0]["id"] == resp_b["id"]
    finally:
        await ws_a.close()
        await ws_b.close()
