import json
import sys
import types

import pytest

if "mcp.server.fastmcp" not in sys.modules:
    fastmcp_module = types.ModuleType("mcp.server.fastmcp")

    class FastMCP:
        def __init__(self, name):
            self.name = name

        def tool(self):
            def decorator(func):
                return func
            return decorator

    fastmcp_module.FastMCP = FastMCP
    sys.modules["mcp"] = types.ModuleType("mcp")
    sys.modules["mcp.server"] = types.ModuleType("mcp.server")
    sys.modules["mcp.server.fastmcp"] = fastmcp_module

from burrow import mcp_server


class FakePeer:
    def __init__(self):
        self._desktop_sessions = {
            "sess-1": {"session_id": "sess-1", "local_port": 9000, "viewer_url": "tcp://127.0.0.1:9000"}
        }
        self.helper_calls = []
        self.started = []
        self.stopped = []
        self.capabilities_calls = []

    async def get_desktop_capabilities(self, to):
        self.capabilities_calls.append(to)
        return {
            "available_backends": ["native", "xpra"],
            "preferred_backend": "native",
            "native": {
                "healthy": True,
                "display_targets": [{"kind": "display", "id": "xrandr:eDP-1", "title": "eDP-1 1920x1080", "primary": True, "backend": "xrandr"}],
                "clipboard_surface": {"native_backend": False, "stubbed": True, "note": "sidecar clipboard stubbed"},
            },
        }

    async def open_desktop_session(self, to, **kwargs):
        self.started.append((to, kwargs))
        return {
            "session_id": "sess-1",
            "backend": kwargs["backend"],
            "local_port": kwargs.get("local_port") or 14500,
            "remote_port": kwargs.get("remote_port", 0),
            "local_connect_hint": "Attach with: xpra attach tcp:127.0.0.1:14500",
            "capabilities": {"clipboard": True},
            "clipboard_details": {"available": True, "direction": "viewer-dependent", "stubbed": False},
            "viewer": {"display": kwargs.get("display") or ":0"},
        }

    async def stop_desktop_session(self, to, session_id):
        self.stopped.append((to, session_id))
        return {"stopped": True, "session_id": session_id}

    async def _run_desktop_script(self, to, args, timeout=30.0):
        self.helper_calls.append((to, args, timeout))
        if args == ["list-sessions"]:
            return {"sessions": [{"session_id": "sess-1", "backend": "xpra", "viewer": {"display": ":1"}}]}
        if args == ["snapshot", "--session-id", "sess-1"]:
            return {"session_id": "sess-1", "mime_type": "image/png", "image_base64": "cG5n"}
        if args == ["input", "--session-id", "sess-1", "--action-json", '{"type":"click","x":1,"y":2}']:
            return {"session_id": "sess-1", "ok": True, "action": {"type": "click", "x": 1, "y": 2}}
        if args == ["input", "--session-id", "sess-1", "--action-json", '{"clipboard_intent": "paste", "keys": ["ctrl", "v"], "type": "hotkey"}']:
            return {"session_id": "sess-1", "ok": True, "action": {"type": "hotkey", "keys": ["ctrl", "v"]}}
        raise AssertionError(f"unexpected helper args: {args}")


async def _return_peer(peer):
    return peer


@pytest.mark.asyncio
async def test_burrow_desktop_capabilities_returns_json(monkeypatch):
    peer = FakePeer()
    monkeypatch.setattr(mcp_server, "_auto_connect", lambda: _return_peer(peer))

    result = await mcp_server.burrow_desktop_capabilities("peer-a")

    payload = json.loads(result)
    assert payload["available_backends"] == ["native", "xpra"]
    assert payload["surface"]["display_targeting"]["open_parameter"] == "display"
    assert payload["surface"]["display_targeting"]["enumeration_available"] is True
    assert payload["surface"]["clipboard"]["control_plane_read_write"] is False
    assert peer.capabilities_calls == ["peer-a"]


@pytest.mark.asyncio
async def test_burrow_desktop_open_returns_session_json(monkeypatch):
    peer = FakePeer()
    monkeypatch.setattr(mcp_server, "_auto_connect", lambda: _return_peer(peer))

    result = await mcp_server.burrow_desktop_open("peer-a", backend="xpra", local_port=9000, remote_port=14500, display=":2")

    payload = json.loads(result)
    assert payload["session_id"] == "sess-1"
    assert payload["backend"] == "xpra"
    assert payload["target"] == {"kind": "display", "id": ":2", "title": ":2"}
    assert payload["capabilities"]["clipboard_surface"]["native_backend"] is True
    assert payload["capabilities"]["clipboard_surface"]["stubbed"] is False
    assert peer.started == [("peer-a", {"backend": "xpra", "local_port": 9000, "remote_port": 14500, "readonly": False, "display": ":2", "target": {"kind": "display", "id": ":2", "title": ":2"}})]


@pytest.mark.asyncio
async def test_burrow_desktop_list_merges_local_tunnel_state(monkeypatch):
    peer = FakePeer()
    monkeypatch.setattr(mcp_server, "_auto_connect", lambda: _return_peer(peer))

    result = await mcp_server.burrow_desktop_list("peer-a")

    payload = json.loads(result)
    assert payload["peer"] == "peer-a"
    assert payload["sessions"][0]["session_id"] == "sess-1"
    assert payload["sessions"][0]["local_port"] == 9000
    assert payload["sessions"][0]["target"] == {"kind": "display", "id": ":1", "title": ":1"}
    assert payload["sessions"][0]["computer_use"]["clipboard_actions"] == ["copy", "cut", "paste", "paste_text", "select_all"]


@pytest.mark.asyncio
async def test_burrow_desktop_snapshot_calls_helper(monkeypatch):
    peer = FakePeer()
    monkeypatch.setattr(mcp_server, "_auto_connect", lambda: _return_peer(peer))

    result = await mcp_server.burrow_desktop_snapshot("peer-a", "sess-1")

    assert json.loads(result)["image_base64"] == "cG5n"
    assert peer.helper_calls[-1][1] == ["snapshot", "--session-id", "sess-1"]


@pytest.mark.asyncio
async def test_burrow_desktop_input_calls_helper(monkeypatch):
    peer = FakePeer()
    monkeypatch.setattr(mcp_server, "_auto_connect", lambda: _return_peer(peer))

    result = await mcp_server.burrow_desktop_input("peer-a", "sess-1", '{"type":"click","x":1,"y":2}')

    payload = json.loads(result)
    assert payload["ok"] is True
    assert peer.helper_calls[-1][1] == ["input", "--session-id", "sess-1", "--action-json", '{"type":"click","x":1,"y":2}']


@pytest.mark.asyncio
async def test_burrow_desktop_clipboard_uses_thin_surface(monkeypatch):
    peer = FakePeer()
    monkeypatch.setattr(mcp_server, "_auto_connect", lambda: _return_peer(peer))

    result = await mcp_server.burrow_desktop_clipboard("peer-a", "sess-1", action="paste")

    payload = json.loads(result)
    assert payload["ok"] is True
    assert payload["clipboard_surface"] == "thin-desktop-input"
    assert payload["requested_action"] == "paste"
    assert peer.helper_calls[-1][1] == ["input", "--session-id", "sess-1", "--action-json", '{"clipboard_intent": "paste", "keys": ["ctrl", "v"], "type": "hotkey"}']


@pytest.mark.asyncio
async def test_burrow_desktop_close_returns_json(monkeypatch):
    peer = FakePeer()
    monkeypatch.setattr(mcp_server, "_auto_connect", lambda: _return_peer(peer))

    result = await mcp_server.burrow_desktop_close("peer-a", "sess-1")

    assert json.loads(result) == {"session_id": "sess-1", "stopped": True}
    assert peer.stopped == [("peer-a", "sess-1")]


@pytest.mark.asyncio
async def test_burrow_desktop_tools_validate_target():
    result = await mcp_server.burrow_desktop_list("")

    assert result.startswith("Error: 'to' parameter is empty")
