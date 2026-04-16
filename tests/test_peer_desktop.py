import asyncio

import pytest

from burrow.peer import Peer


@pytest.mark.asyncio
async def test_open_desktop_session_sends_protocol_and_opens_tunnel(monkeypatch):
    peer = Peer("ws://example", "tester")
    sent = []

    async def fake_send(msg):
        sent.append(msg)
        if msg["type"] == "desktop_session_open":
            peer._handle_desktop_session_ready({
                "type": "desktop_session_ready",
                "session_id": msg["session_id"],
                "session": {
                    "session_id": msg["session_id"],
                    "peer": "peer-1",
                    "backend": "xpra",
                    "state": "ready",
                    "owner": "local",
                    "controller": peer.id or "",
                    "created_at": 1.0,
                    "updated_at": 1.0,
                    "capabilities": {"protocol": "xpra", "clipboard": True},
                    "viewer": {"protocol": "xpra", "remote_port": 14500, "viewer_path": ""},
                    "computer_use": {"frame_request": True, "input": True, "control_plane_only": True},
                    "permissions": {"view": True, "control": True, "clipboard": True},
                    "reconnect": {"supported": True, "resume_token": "resume-abc", "epoch": 2, "strategy": "resume"},
                    "privacy": {"supported": True, "enabled": False, "mode": "query", "stubbed": True},
                    "target": None,
                },
            })

    async def fake_open_tunnel(to, local_port, remote_port):
        assert to == "peer-1"
        assert local_port == 24500
        assert remote_port == 14500
        return "server-handle"

    monkeypatch.setattr(peer, "_send", fake_send)
    monkeypatch.setattr(peer, "open_tunnel", fake_open_tunnel)

    result = await peer.open_desktop_session(
        "peer-1",
        backend="xpra",
        local_port=24500,
        remote_port=14500,
        readonly=False,
        display=":0",
        permissions={"view": True, "control": True, "clipboard": True},
        privacy={"supported": True, "enabled": False},
        resume_token="resume-abc",
        resume_epoch=2,
    )

    assert sent[0]["type"] == "desktop_session_open"
    assert sent[0]["backend"] == "xpra"
    assert sent[0]["remote_port"] == 14500
    assert sent[0]["display"] == ":0"
    assert sent[0]["permissions"] == {"view": True, "control": True, "clipboard": True}
    assert sent[0]["privacy"] == {"supported": True, "enabled": False}
    assert sent[0]["resume_token"] == "resume-abc"
    assert sent[0]["resume_epoch"] == 2
    assert result["viewer"]["local_port"] == 24500
    assert result["viewer"]["viewer_url"] == "tcp://127.0.0.1:24500"
    assert result["reconnect"]["resume_token"] == "resume-abc"
    assert result["privacy"]["supported"] is True
    assert peer._desktop_sessions[result["session_id"]]["tunnel_server"] == "server-handle"


@pytest.mark.asyncio
async def test_open_desktop_session_rolls_back_remote_when_tunnel_fails(monkeypatch):
    peer = Peer("ws://example", "tester")
    sent = []

    async def fake_send(msg):
        sent.append(msg)
        if msg["type"] == "desktop_session_open":
            peer._handle_desktop_session_ready({
                "type": "desktop_session_ready",
                "session_id": msg["session_id"],
                "session": {
                    "session_id": msg["session_id"],
                    "peer": "peer-1",
                    "backend": "x11vnc",
                    "state": "ready",
                    "capabilities": {"protocol": "vnc"},
                    "viewer": {"protocol": "vnc", "remote_port": 5901, "viewer_path": ""},
                    "computer_use": {"frame_request": True, "input": True, "control_plane_only": True},
                    "permissions": {"view": True, "control": True, "clipboard": False},
                    "target": None,
                },
            })

    async def fake_open_tunnel(to, local_port, remote_port):
        raise OSError("port already in use")

    monkeypatch.setattr(peer, "_send", fake_send)
    monkeypatch.setattr(peer, "open_tunnel", fake_open_tunnel)

    with pytest.raises(OSError, match="port already in use"):
        await peer.open_desktop_session("peer-1")

    assert [msg["type"] for msg in sent] == ["desktop_session_open", "desktop_session_close"]
    assert peer._desktop_sessions == {}


@pytest.mark.asyncio
async def test_open_desktop_session_allows_native_control_plane_only(monkeypatch):
    peer = Peer("ws://example", "tester")

    async def fake_send(msg):
        if msg["type"] == "desktop_session_open":
            peer._handle_desktop_session_ready({
                "type": "desktop_session_ready",
                "session_id": msg["session_id"],
                "session": {
                    "session_id": msg["session_id"],
                    "peer": "peer-1",
                    "backend": "native",
                    "state": "ready",
                    "capabilities": {"protocol": "jsonl-stdio", "clipboard": False},
                    "viewer": {"protocol": "jsonl-stdio", "remote_port": 0, "connect_hint": "use snapshot/input"},
                    "computer_use": {"frame_request": True, "input": True, "control_plane_only": True},
                    "permissions": {"view": True, "control": True, "clipboard": False},
                    "target": {"kind": "display", "id": "xrandr:eDP-1", "title": "eDP-1"},
                },
            })

    monkeypatch.setattr(peer, "_send", fake_send)

    result = await peer.open_desktop_session("peer-1", backend="native")

    assert result["backend"] == "native"
    assert result["viewer"]["protocol"] == "jsonl-stdio"
    assert result["session_id"] in peer._desktop_sessions


@pytest.mark.asyncio
async def test_list_desktop_sessions_returns_local_public_records():
    peer = Peer("ws://example", "tester")
    peer._desktop_sessions["sess-1"] = {
        "session_id": "sess-1",
        "peer": "peer-1",
        "backend": "xpra",
        "state": "ready",
        "viewer": {"remote_port": 14500},
        "permissions": {"view": True, "control": True, "clipboard": True},
        "permission_revision": 1,
        "reconnect": {"supported": True, "resume_token": "resume-1", "epoch": 1, "strategy": "resume"},
        "privacy": {"supported": True, "enabled": False, "mode": "query", "stubbed": True},
        "tunnel_server": object(),
        "raw_session": {"pid": 123},
    }

    result = await peer.list_desktop_sessions()

    assert result == [{
        "session_id": "sess-1",
        "peer": "peer-1",
        "backend": "xpra",
        "state": "ready",
        "viewer": {"remote_port": 14500},
        "permissions": {"view": True, "control": True, "clipboard": True},
        "permission_revision": 1,
        "reconnect": {"supported": True, "resume_token": "resume-1", "epoch": 1, "strategy": "resume"},
        "privacy": {"supported": True, "enabled": False, "mode": "query", "stubbed": True},
    }]


@pytest.mark.asyncio
async def test_list_desktop_sessions_queries_remote_peer(monkeypatch):
    peer = Peer("ws://example", "tester")

    async def fake_send(msg):
        if msg["type"] == "desktop_session_list":
            fut = peer._pending_requests[msg["req_id"]]
            fut.set_result({
                "type": "desktop_session_list",
                "req_id": msg["req_id"],
                "sessions": [{"session_id": "sess-2", "peer": "peer-2", "state": "ready"}],
            })

    monkeypatch.setattr(peer, "_send", fake_send)

    result = await peer.list_desktop_sessions("peer-2")

    assert result == [{"session_id": "sess-2", "peer": "peer-2", "state": "ready"}]


@pytest.mark.asyncio
async def test_close_desktop_session_closes_local_tunnel_and_sends_protocol(monkeypatch):
    peer = Peer("ws://example", "tester")
    sent = []

    class DummyServer:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    server = DummyServer()
    peer._desktop_sessions["sess-9"] = {
        "session_id": "sess-9",
        "peer": "peer-2",
        "tunnel_server": server,
    }

    async def fake_send(msg):
        sent.append(msg)

    monkeypatch.setattr(peer, "_send", fake_send)

    result = await peer.close_desktop_session("peer-2", "sess-9")

    assert result["closed"] is True
    assert sent == [{"type": "desktop_session_close", "to": "peer-2", "session_id": "sess-9"}]
    assert result["remote_close_error"] == ""
    assert server.closed is True
    assert "sess-9" not in peer._desktop_sessions


@pytest.mark.asyncio
async def test_close_desktop_session_cleans_local_state_even_if_remote_send_fails(monkeypatch):
    peer = Peer("ws://example", "tester")

    class DummyServer:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    server = DummyServer()
    peer._desktop_sessions["sess-9"] = {"session_id": "sess-9", "peer": "peer-2", "tunnel_server": server}

    async def fake_send(msg):
        raise ConnectionError("disconnecting")

    monkeypatch.setattr(peer, "_send", fake_send)

    result = await peer.close_desktop_session("peer-2", "sess-9")

    assert result["remote_close_error"] == "disconnecting"
    assert server.closed is True
    assert "sess-9" not in peer._desktop_sessions


@pytest.mark.asyncio
async def test_request_desktop_frame_waits_for_frame(monkeypatch):
    peer = Peer("ws://example", "tester")

    async def fake_send(msg):
        if msg["type"] == "desktop_frame_request":
            peer._handle_desktop_frame({
                "type": "desktop_frame",
                "session_id": msg["session_id"],
                "frame": {
                    "session_id": msg["session_id"],
                    "mime_type": "image/png",
                    "data_base64": "YWJj",
                    "width": 100,
                    "height": 50,
                },
            })

    monkeypatch.setattr(peer, "_send", fake_send)

    result = await peer.request_desktop_frame("peer-1", "sess-frame")

    assert result["mime_type"] == "image/png"
    assert result["width"] == 100


@pytest.mark.asyncio
async def test_handle_desktop_session_open_starts_local_host_and_sends_ready(monkeypatch):
    peer = Peer("ws://example", "host")
    sent = []

    def fake_start_session(preferred_backend, remote_port, readonly, display):
        assert preferred_backend == "xpra"
        assert remote_port == 14500
        assert readonly is True
        assert display == ":1"
        return {
            "session_id": "host-local-id",
            "backend": "xpra",
            "protocol": "xpra",
            "remote_port": 14500,
            "viewer_path": "",
            "clipboard": True,
            "audio": False,
            "seamless": True,
            "description": "desktop",
            "pid": 999,
            "readonly": True,
            "display": ":1",
            "connect_hint": "Attach with: xpra attach tcp:127.0.0.1:14500",
        }

    async def fake_send(msg):
        sent.append(msg)

    monkeypatch.setattr("burrow.peer.desktop.start_session", fake_start_session)
    monkeypatch.setattr(peer, "_send", fake_send)

    await peer._handle_desktop_session_open({
        "type": "desktop_session_open",
        "from": "peer-remote",
        "session_id": "sess-remote",
        "backend": "xpra",
        "readonly": True,
        "remote_port": 14500,
        "display": ":1",
        "permissions": {"view": True, "control": True, "clipboard": False},
        "privacy": {"supported": True, "enabled": False, "mode": "query"},
        "resume_token": "resume-hosted",
        "resume_epoch": 7,
    })

    assert sent[0]["type"] == "desktop_session_ready"
    assert sent[0]["to"] == "peer-remote"
    assert sent[0]["session"]["session_id"] == "sess-remote"
    assert sent[0]["session"]["owner"] == "hosted"
    assert sent[0]["session"]["permissions"] == {"view": True, "control": False, "clipboard": False}
    assert sent[0]["session"]["privacy"]["supported"] is True
    assert sent[0]["session"]["reconnect"]["resume_token"] == "resume-hosted"
    assert peer._desktop_sessions["sess-remote"]["controller"] == "peer-remote"


@pytest.mark.asyncio
async def test_handle_desktop_input_enforces_permissions_and_calls_callback(monkeypatch):
    peer = Peer("ws://example", "host")
    seen = []
    sent = []
    peer.on_desktop_input = lambda session, action, context: seen.append((session["session_id"], action, context["from"]))
    peer._desktop_sessions["sess-live"] = {
        "session_id": "sess-live",
        "owner": "hosted",
        "controller": "peer-a",
        "permissions": {"view": True, "control": True, "clipboard": False},
    }
    peer._desktop_sessions["sess-readonly"] = {
        "session_id": "sess-readonly",
        "owner": "hosted",
        "controller": "peer-a",
        "permissions": {"view": True, "control": False, "clipboard": False},
    }
    peer._desktop_sessions["sess-no-clipboard"] = {
        "session_id": "sess-no-clipboard",
        "owner": "hosted",
        "controller": "peer-a",
        "permissions": {"view": True, "control": True, "clipboard": False},
    }

    async def fake_send(msg):
        sent.append(msg)

    monkeypatch.setattr(peer, "_send", fake_send)

    await peer._handle_desktop_input({
        "type": "desktop_input",
        "from": "peer-a",
        "session_id": "sess-live",
        "action": {"type": "click", "x": 1, "y": 2},
    })
    await peer._handle_desktop_input({
        "type": "desktop_input",
        "from": "peer-a",
        "session_id": "sess-readonly",
        "action": {"type": "click"},
    })
    await peer._handle_desktop_input({
        "type": "desktop_input",
        "from": "peer-a",
        "session_id": "sess-no-clipboard",
        "action": {"type": "hotkey", "keys": ["ctrl", "v"], "clipboard_intent": "paste"},
    })

    assert seen == [("sess-live", {"type": "click", "x": 1, "y": 2}, "peer-a")]
    assert sent == [{
        "type": "desktop_permission",
        "to": "peer-a",
        "session_id": "sess-readonly",
        "permission": {
            "view": True,
            "control": False,
            "clipboard": False,
            "error": "desktop session is read-only",
        },
        "transition": {
            "previous": {"view": True, "control": False, "clipboard": False},
            "current": {"view": True, "control": False, "clipboard": False},
            "actor": "peer-a",
            "reason": "desktop session is read-only",
            "requested": {"action": {"type": "click"}, "kind": "control"},
            "at": pytest.approx(sent[0]["transition"]["at"]),
        },
    }, {
        "type": "desktop_permission",
        "to": "peer-a",
        "session_id": "sess-no-clipboard",
        "permission": {
            "view": True,
            "control": True,
            "clipboard": False,
            "error": "clipboard access is disabled",
        },
        "transition": {
            "previous": {"view": True, "control": True, "clipboard": False},
            "current": {"view": True, "control": True, "clipboard": False},
            "actor": "peer-a",
            "reason": "clipboard access is disabled",
            "requested": {
                "action": {"type": "hotkey", "keys": ["ctrl", "v"], "clipboard_intent": "paste"},
                "kind": "clipboard",
            },
            "at": pytest.approx(sent[1]["transition"]["at"]),
        },
    }]


@pytest.mark.asyncio
async def test_handle_desktop_permission_fails_waiting_frame_request():
    peer = Peer("ws://example", "tester")
    fut = asyncio.get_running_loop().create_future()
    peer._desktop_frame_waiters["sess-frame"] = fut
    peer._desktop_sessions["sess-frame"] = {
        "session_id": "sess-frame",
        "permissions": {"view": True, "control": False, "clipboard": False},
        "permission_revision": 0,
    }

    peer._handle_desktop_permission({
        "type": "desktop_permission",
        "session_id": "sess-frame",
        "permission": {"view": False, "control": False, "clipboard": False, "error": "unknown desktop session"},
        "transition": {
            "previous": {"view": True, "control": False, "clipboard": False},
            "current": {"view": False, "control": False, "clipboard": False},
            "actor": "peer-host",
            "reason": "unknown desktop session",
            "requested": {"kind": "frame_request"},
            "at": 10.0,
        },
    })

    assert peer._desktop_sessions["sess-frame"]["permission_revision"] == 1
    assert peer._desktop_sessions["sess-frame"]["permission_transition"]["actor"] == "peer-host"
    assert peer._desktop_sessions["sess-frame"]["last_error"] == "unknown desktop session"

    with pytest.raises(PermissionError, match="unknown desktop session"):
        await fut
