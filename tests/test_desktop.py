import base64
import json
import os
import socket
import subprocess

import pytest

from burrow import desktop
from burrow import desktop_bridge


class _Completed:
    def __init__(self, stderr: bytes = b""):
        self.stderr = stderr


def test_choose_backend_prefers_xpra_for_auto():
    info = desktop.describe_environment(
        commands={"x11vnc", "xpra", "python3"},
        env={"DISPLAY": ":0"},
        system_name="Linux",
    )

    selected = desktop.choose_backend("auto", info)

    assert selected.name == "xpra"
    assert selected.protocol == "xpra"
    assert selected.seamless is True


def test_describe_environment_prefers_native_sidecar_when_available(monkeypatch):
    monkeypatch.setattr(desktop_bridge, "find_sidecar_binary", lambda **kwargs: "/tmp/burrow-rd-host")

    class _Bridge:
        def capabilities(self):
            return {"clipboard": {"available": False}, "displays": []}

    monkeypatch.setattr(desktop_bridge, "get_bridge", lambda **kwargs: _Bridge())

    info = desktop.describe_environment(
        commands={"xpra", "burrow-rd-host", "python3"},
        env={"DISPLAY": ":0"},
        system_name="Linux",
    )

    assert "native" in info["available_backends"]
    assert info["preferred_backend"] == "native"
    assert info["native_sidecar_path"] == "/tmp/burrow-rd-host"


def test_describe_environment_surfaces_native_capability_state(monkeypatch):
    monkeypatch.setattr(desktop_bridge, "find_sidecar_binary", lambda **kwargs: "/tmp/burrow-rd-host")

    class _Bridge:
        def capabilities(self):
            return {
                "transport": "jsonl-stdio",
                "session_scoped": True,
                "displays": [{"id": "xrandr:eDP-1", "name": "eDP-1", "width": 1920, "height": 1080, "origin_x": 0, "origin_y": 0, "primary": True, "backend": "xrandr"}],
                "clipboard": {"available": False, "direction": "none", "stubbed": True},
            }

    monkeypatch.setattr(desktop_bridge, "get_bridge", lambda **kwargs: _Bridge())

    info = desktop.describe_environment(commands={"burrow-rd-host"}, env={}, system_name="Linux")

    assert info["native"]["healthy"] is True
    assert info["display_targets"][0]["id"] == "xrandr:eDP-1"
    assert info["clipboard_surface"]["native_backend"] is False


def test_choose_backend_falls_back_to_x11vnc_when_xpra_missing():
    info = desktop.describe_environment(
        commands={"x11vnc", "python3"},
        env={"DISPLAY": ":0"},
        system_name="Linux",
    )

    selected = desktop.choose_backend("auto", info)

    assert selected.name == "x11vnc"
    assert selected.protocol == "vnc"


def test_build_launch_command_for_x11vnc_readonly():
    command = desktop.build_launch_command(
        desktop.BACKENDS["x11vnc"],
        remote_port=5905,
        env_info=desktop.describe_environment(
            commands={"x11vnc"},
            env={"DISPLAY": ":2"},
            system_name="Linux",
        ),
        readonly=True,
        display=":2",
    )

    assert command[:4] == ["x11vnc", "-localhost", "-display", ":2"]
    assert "-rfbport" in command
    assert "5905" in command
    assert "-viewonly" in command


def test_build_launch_command_for_wayvnc_uses_wayland_display():
    command = desktop.build_launch_command(
        desktop.BACKENDS["wayvnc"],
        remote_port=5901,
        env_info=desktop.describe_environment(
            commands={"wayvnc"},
            env={"WAYLAND_DISPLAY": "wayland-1"},
            system_name="Linux",
        ),
        readonly=False,
        display=None,
    )

    assert command[0] == "wayvnc"
    assert command[1:3] == ["127.0.0.1", "5901"]
    assert "--render-cursor" in command


def test_parse_script_json_uses_last_json_line():
    payload = desktop.parse_json_output("log line\n{\"session_id\": \"abc\", \"ok\": true}\n")

    assert payload == {"session_id": "abc", "ok": True}


def test_session_metadata_round_trip(tmp_path):
    session = {"session_id": "sess-1", "pid": 1234, "remote_port": 5900}

    path = desktop.write_session_metadata(session, session_dir=tmp_path)
    loaded = desktop.load_session_metadata("sess-1", session_dir=tmp_path)

    assert path.exists()
    assert json.loads(path.read_text()) == session
    assert loaded == session


def test_list_sessions_marks_running_state(tmp_path, monkeypatch):
    desktop.write_session_metadata({"session_id": "a", "pid": 111, "remote_port": 1}, session_dir=tmp_path)
    desktop.write_session_metadata({"session_id": "b", "pid": 222, "remote_port": 2}, session_dir=tmp_path)
    monkeypatch.setattr(desktop, "_pid_is_running", lambda pid: pid == 111)

    result = desktop.list_sessions(session_dir=tmp_path)

    assert [item["session_id"] for item in result["sessions"]] == ["a", "b"]
    assert result["sessions"][0]["running"] is True
    assert result["sessions"][1]["running"] is False
    assert result["sessions"][1]["stale"] is True
    assert result["sessions"][0]["status"] == "running"


def test_build_connect_hint_for_xpra_and_vnc():
    xpra_hint = desktop.build_connect_hint({"protocol": "xpra", "local_port": 14500})
    vnc_hint = desktop.build_connect_hint({"protocol": "vnc", "local_port": 5900})

    assert "xpra attach tcp:127.0.0.1:14500" in xpra_hint
    assert "127.0.0.1:5900" in vnc_hint


def test_choose_snapshot_tool_prefers_x11_tools_for_x11_session():
    tool = desktop.choose_snapshot_tool(
        {"display_server": "x11", "display": ":0"},
        commands={"import", "scrot", "gnome-screenshot"},
        env_info={"display": ":0", "wayland_display": None},
    )

    assert tool == "gnome-screenshot"


def test_choose_snapshot_tool_prefers_wayland_tools_for_wayland_session():
    tool = desktop.choose_snapshot_tool(
        {"display_server": "wayland", "display": "wayland-1"},
        commands={"grim", "gnome-screenshot", "wtype"},
        env_info={"display": None, "wayland_display": "wayland-1"},
    )

    assert tool == "grim"


def test_choose_input_tools_prefers_xdotool_on_x11():
    tools = desktop.choose_input_tools(
        {"display_server": "x11", "display": ":0"},
        commands={"xdotool", "wtype", "python3"},
        env_info={"display": ":0", "wayland_display": None},
    )

    assert tools == {"display_server": "x11", "pointer": "xdotool", "keyboard": "xdotool"}


def test_choose_input_tools_prefers_wayland_stack():
    tools = desktop.choose_input_tools(
        {"display_server": "wayland", "display": "wayland-1"},
        commands={"ydotool", "wtype", "python3"},
        env_info={"display": None, "wayland_display": "wayland-1"},
    )

    assert tools == {"display_server": "wayland", "pointer": "ydotool", "keyboard": "wtype"}


def test_snapshot_session_returns_base64_payload(tmp_path, monkeypatch):
    desktop.write_session_metadata({"session_id": "sess-1", "pid": os.getpid(), "display_server": "x11", "display": ":0"}, session_dir=tmp_path)

    def fake_run(command, check, stdout, stderr):
        output_path = command[-1]
        with open(output_path, "wb") as handle:
            handle.write(b"png-bytes")
        return _Completed()

    monkeypatch.setattr(desktop.subprocess, "run", fake_run)

    result = desktop.snapshot_session(
        "sess-1",
        session_dir=tmp_path,
        commands={"scrot"},
        env_info={"display": ":0", "wayland_display": None},
    )

    assert result["tool"] == "scrot"
    assert result["mime_type"] == "image/png"
    assert base64.b64decode(result["image_base64"]) == b"png-bytes"


def test_input_session_normalizes_and_executes_click(tmp_path, monkeypatch):
    desktop.write_session_metadata({"session_id": "sess-2", "pid": os.getpid(), "display_server": "x11", "display": ":0"}, session_dir=tmp_path)
    calls = []

    def fake_run(command, check, stdout, stderr):
        calls.append(command)
        return _Completed()

    monkeypatch.setattr(desktop.subprocess, "run", fake_run)

    result = desktop.input_session(
        "sess-2",
        json.dumps({"type": "double_click", "x": 10, "y": 20}),
        session_dir=tmp_path,
        commands={"xdotool"},
        env_info={"display": ":0", "wayland_display": None},
    )

    assert result["ok"] is True
    assert result["action"] == {"type": "click", "x": 10, "y": 20, "button": "left", "count": 2}
    assert calls == [
        ["xdotool", "mousemove", "10", "20"],
        ["xdotool", "click", "--repeat", "2", "1"],
    ]


def test_input_session_snapshot_request_delegates_to_snapshot(tmp_path, monkeypatch):
    desktop.write_session_metadata({"session_id": "sess-3", "pid": os.getpid(), "display_server": "x11", "display": ":0"}, session_dir=tmp_path)
    monkeypatch.setattr(desktop, "snapshot_session", lambda session_id, **kwargs: {"session_id": session_id, "tool": "scrot"})

    result = desktop.input_session(
        "sess-3",
        json.dumps({"type": "snapshot_request"}),
        session_dir=tmp_path,
        commands={"scrot"},
        env_info={"display": ":0", "wayland_display": None},
    )

    assert result == {"session_id": "sess-3", "tool": "scrot"}


def test_start_session_rejects_already_occupied_port(monkeypatch):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    occupied_port = listener.getsockname()[1]

    monkeypatch.setattr(
        desktop,
        "describe_environment",
        lambda *args, **kwargs: {
            "system": "Linux",
            "commands": ["x11vnc"],
            "display": ":0",
            "wayland_display": None,
            "available_backends": ["x11vnc"],
            "preferred_backend": "x11vnc",
            "native": {"status": "missing"},
            "display_targets": [],
            "clipboard_surface": {"native_backend": False},
        },
    )

    try:
        with pytest.raises(desktop.DesktopConfigError, match="already in use"):
            desktop.start_session(preferred_backend="x11vnc", remote_port=occupied_port)
    finally:
        listener.close()


@pytest.mark.parametrize(
    ("backend_name", "env_vars", "expected_error"),
    [
        ("x11vnc", {}, "DISPLAY"),
        ("wayvnc", {}, "WAYLAND_DISPLAY"),
    ],
)
def test_build_launch_command_requires_display_context(backend_name, env_vars, expected_error):
    env_info = desktop.describe_environment(commands={backend_name}, env=env_vars, system_name="Linux")

    with pytest.raises(desktop.DesktopConfigError) as exc:
        desktop.build_launch_command(
            desktop.BACKENDS[backend_name],
            remote_port=5900,
            env_info=env_info,
            readonly=False,
            display=None,
        )

    assert expected_error in str(exc.value)


def test_input_session_raises_when_no_tool_available(tmp_path):
    desktop.write_session_metadata({"session_id": "sess-4", "pid": os.getpid(), "display_server": "wayland", "display": "wayland-1"}, session_dir=tmp_path)

    with pytest.raises(desktop.DesktopConfigError, match="No input tool available"):
        desktop.input_session(
            "sess-4",
            json.dumps({"type": "click", "x": 1, "y": 2}),
            session_dir=tmp_path,
            commands={"python3"},
            env_info={"display": None, "wayland_display": "wayland-1"},
        )


def test_start_session_uses_native_sidecar_when_available(tmp_path, monkeypatch):
    class _Bridge:
        pid = 2468

        def capabilities(self):
            return {
                "type": "capabilities",
                "transport": "jsonl-stdio",
                "session_scoped": True,
                "clipboard": {"available": False, "direction": "none", "stubbed": True},
                "displays": [{"id": "display-1", "name": "Display 1", "width": 1280, "height": 720, "origin_x": 0, "origin_y": 0, "primary": True, "backend": "stub"}],
                "input_actions": ["mouse_move", "mouse_button"],
                "snapshot_formats": ["application/octet-stream;base64"],
                "notes": ["Wave 1 scaffold only"],
            }

        def open_session(self, *, display_id=None):
            assert display_id == "display-1"
            return {
                "type": "open_session",
                "session_id": "native-1",
                "display_id": "display-1",
                "width": 1280,
                "height": 720,
                "stubbed": True,
            }

    monkeypatch.setattr(
        desktop,
        "describe_environment",
        lambda: {
            "system": "Linux",
            "commands": ["burrow-rd-host", "xpra"],
            "display": ":0",
            "wayland_display": None,
            "available_backends": ["native", "xpra"],
            "preferred_backend": "native",
        },
    )
    monkeypatch.setattr(desktop_bridge, "get_bridge", lambda: _Bridge())

    session = desktop.start_session(display="display-1", session_dir=tmp_path)

    assert session["backend"] == "native"
    assert session["session_id"] == "native-1"
    assert session["pid"] == 2468
    assert session["transport"] == "jsonl-stdio"
    assert session["display_details"]["name"] == "Display 1"
    assert session["clipboard_details"]["stubbed"] is True
    assert "snapshot/input controls" in session["connect_hint"]
    assert desktop.load_session_metadata("native-1", session_dir=tmp_path)["backend"] == "native"


def test_snapshot_session_uses_native_sidecar_payload_shape(tmp_path, monkeypatch):
    desktop.write_session_metadata({"session_id": "native-2", "backend": "native", "pid": 111}, session_dir=tmp_path)

    class _Bridge:
        def snapshot(self, session_id):
            assert session_id == "native-2"
            return {
                "type": "snapshot",
                "session_id": session_id,
                "frame": {
                    "display_id": "display-1",
                    "width": 1280,
                    "height": 720,
                    "mime_type": "application/octet-stream",
                    "encoding": "base64",
                    "data_base64": "c3R1Yi1mcmFtZQ==",
                    "sequence": 3,
                    "stubbed": True,
                },
            }

    monkeypatch.setattr(desktop_bridge, "get_bridge", lambda: _Bridge())

    result = desktop.snapshot_session("native-2", session_dir=tmp_path)

    assert result["session_id"] == "native-2"
    assert result["image_base64"] == "c3R1Yi1mcmFtZQ=="
    assert result["byte_length"] == len(b"stub-frame")
    assert result["stubbed"] is True


def test_input_session_uses_native_sidecar_action_mapping(tmp_path, monkeypatch):
    desktop.write_session_metadata({"session_id": "native-3", "backend": "native", "pid": 222, "readonly": False}, session_dir=tmp_path)
    calls = []

    class _Bridge:
        def input(self, session_id, action):
            calls.append((session_id, action))
            return {"type": "input", "session_id": session_id, "accepted": True, "stubbed": True}

    monkeypatch.setattr(desktop_bridge, "get_bridge", lambda: _Bridge())

    result = desktop.input_session(
        "native-3",
        json.dumps({"type": "double_click", "x": 10, "y": 20}),
        session_dir=tmp_path,
    )

    assert result["session_id"] == "native-3"
    assert result["action"] == {"type": "click", "x": 10, "y": 20, "button": "left", "count": 2}
    assert result["ok"] is True
    assert calls == [
        ("native-3", {"type": "mouse_move", "x": 10, "y": 20}),
        ("native-3", {"type": "mouse_button", "button": "left", "pressed": True}),
        ("native-3", {"type": "mouse_button", "button": "left", "pressed": False}),
        ("native-3", {"type": "mouse_button", "button": "left", "pressed": True}),
        ("native-3", {"type": "mouse_button", "button": "left", "pressed": False}),
    ]


def test_stop_session_closes_native_sidecar_and_resets_bridge(tmp_path, monkeypatch):
    desktop.write_session_metadata({"session_id": "native-4", "backend": "native", "pid": 333}, session_dir=tmp_path)
    close_calls = []
    reset_calls = []

    class _Bridge:
        def close_session(self, session_id):
            close_calls.append(session_id)
            return {"type": "close_session", "session_id": session_id, "closed": True}

    monkeypatch.setattr(desktop_bridge, "get_bridge", lambda: _Bridge())
    monkeypatch.setattr(desktop_bridge, "reset_bridge", lambda: reset_calls.append(True))

    result = desktop.stop_session("native-4", session_dir=tmp_path)

    assert result == {"stopped": True, "session_id": "native-4", "pid": 333, "stale_cleanup": False}
    assert close_calls == ["native-4"]
    assert reset_calls == [True]
    assert desktop.load_session_metadata("native-4", session_dir=tmp_path) is None


def test_input_session_native_unknown_session_cleans_up_metadata(tmp_path, monkeypatch):
    desktop.write_session_metadata({"session_id": "sess-native", "backend": "native", "pid": 99}, session_dir=tmp_path)

    class _Bridge:
        def input(self, session_id, action):
            raise desktop_bridge.DesktopBridgeError("unknown session", code="unknown_session")

    monkeypatch.setattr(desktop_bridge, "get_bridge", lambda: _Bridge())
    reset_calls = []
    monkeypatch.setattr(desktop_bridge, "reset_bridge", lambda: reset_calls.append(True))

    with pytest.raises(desktop.DesktopConfigError, match="stale"):
        desktop.input_session("sess-native", json.dumps({"type": "click", "x": 1, "y": 2}), session_dir=tmp_path)

    assert desktop.load_session_metadata("sess-native", session_dir=tmp_path) is None
    assert reset_calls == [True]


def test_input_session_reports_clipboard_truthfully(tmp_path, monkeypatch):
    desktop.write_session_metadata(
        {
            "session_id": "sess-clip",
            "pid": os.getpid(),
            "display_server": "x11",
            "display": ":0",
            "clipboard_details": {"available": False, "direction": "none", "stubbed": False},
        },
        session_dir=tmp_path,
    )
    monkeypatch.setattr(desktop.subprocess, "run", lambda *args, **kwargs: _Completed())

    result = desktop.input_session(
        "sess-clip",
        json.dumps({"type": "clipboard_paste"}),
        session_dir=tmp_path,
        commands={"xdotool"},
        env_info={"display": ":0", "wayland_display": None},
    )

    assert result["clipboard"]["requested_action"] == "paste"
    assert result["clipboard"]["synchronized"] is False


def test_stop_session_native_treats_unknown_session_as_stale_cleanup(tmp_path, monkeypatch):
    desktop.write_session_metadata({"session_id": "sess-native", "backend": "native", "pid": 12}, session_dir=tmp_path)

    class _Bridge:
        def close_session(self, session_id):
            raise desktop_bridge.DesktopBridgeError("gone", code="unknown_session")

    monkeypatch.setattr(desktop_bridge, "get_bridge", lambda: _Bridge())
    reset_calls = []
    monkeypatch.setattr(desktop_bridge, "reset_bridge", lambda: reset_calls.append(True))

    result = desktop.stop_session("sess-native", session_dir=tmp_path)

    assert result["stale_cleanup"] is True
    assert desktop.load_session_metadata("sess-native", session_dir=tmp_path) is None
    assert reset_calls == [True]
