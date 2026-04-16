"""RustDesk-inspired remote desktop orchestration for burrow.

This module intentionally does not implement a raw pixel transport inside burrow.
Instead, it uses burrow as the control plane and tunnel layer while launching a
native desktop backend on the remote peer. The design borrows RustDesk's split
between discovery/control and the media plane, but keeps the hot path in mature
system tools like xpra, x11vnc, and wayvnc.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from burrow.computer_use import normalize_action_json
from burrow import desktop_bridge


DEFAULT_PORTS = {
    "xpra": 14500,
    "x11vnc": 5900,
    "wayvnc": 5900,
    "rustdesk": 21118,
}

SESSION_DIR = Path.home() / ".burrow-desktop" / "sessions"

_X11_SNAPSHOT_TOOLS = ("gnome-screenshot", "scrot", "import")
_WAYLAND_SNAPSHOT_TOOLS = ("grim", "gnome-screenshot")
_X11_INPUT_TOOLS = ("xdotool",)
_WAYLAND_POINTER_TOOLS = ("ydotool",)
_WAYLAND_KEYBOARD_TOOLS = ("wtype", "ydotool")
_BUTTON_CODES = {"left": 1, "middle": 2, "right": 3}
_CLIPBOARD_THIN_ACTIONS = ["copy", "cut", "paste", "paste_text", "select_all"]


class DesktopConfigError(RuntimeError):
    """Raised when desktop orchestration cannot be configured safely."""


@dataclass(frozen=True)
class DesktopBackend:
    name: str
    command: str
    protocol: str
    description: str
    seamless: bool = False
    clipboard: bool = True
    audio: bool = False
    viewer_path: str = ""


BACKENDS: dict[str, DesktopBackend] = {
    "native": DesktopBackend(
        name="native",
        command="burrow-rd-host",
        protocol="jsonl-stdio",
        description="Native burrow desktop sidecar over stdio JSONL",
        seamless=False,
        clipboard=False,
        audio=False,
    ),
    "xpra": DesktopBackend(
        name="xpra",
        command="xpra",
        protocol="xpra",
        description="Xpra desktop shadowing or app-forwarding with clipboard support",
        seamless=True,
        clipboard=True,
        audio=False,
    ),
    "x11vnc": DesktopBackend(
        name="x11vnc",
        command="x11vnc",
        protocol="vnc",
        description="X11 VNC server tunneled through burrow",
        seamless=False,
        clipboard=False,
        audio=False,
    ),
    "wayvnc": DesktopBackend(
        name="wayvnc",
        command="wayvnc",
        protocol="vnc",
        description="Wayland VNC server tunneled through burrow",
        seamless=False,
        clipboard=False,
        audio=False,
    ),
    "rustdesk": DesktopBackend(
        name="rustdesk",
        command="rustdesk",
        protocol="rustdesk",
        description="Detected for preference signaling only; burrow currently tunnels native backends directly",
        seamless=True,
        clipboard=True,
        audio=True,
    ),
}


def _normalize_commands(commands: set[str] | None = None) -> set[str]:
    if commands is not None:
        return set(commands)
    available: set[str] = set()
    for name in BACKENDS:
        if name == "native":
            if desktop_bridge.sidecar_available():
                available.add(BACKENDS[name].command)
            continue
        cmd = BACKENDS[name].command
        if shutil.which(cmd):
            available.add(cmd)
    for extra in ("python3", "python", "bash"):
        if shutil.which(extra):
            available.add(extra)
    return available



def describe_environment(*, commands: set[str] | None = None,
                         env: dict[str, str] | None = None,
                         system_name: str | None = None) -> dict[str, Any]:
    env = dict(os.environ if env is None else env)
    commands = _normalize_commands(commands)
    system_name = system_name or os.uname().sysname if hasattr(os, "uname") else (system_name or sys.platform)
    available = [name for name, backend in BACKENDS.items() if backend.command in commands]
    sidecar_path = desktop_bridge.find_sidecar_binary() if BACKENDS["native"].command in commands else None
    native = _native_runtime_state(sidecar_path=sidecar_path)
    return {
        "system": system_name,
        "commands": sorted(commands),
        "display": env.get("DISPLAY"),
        "wayland_display": env.get("WAYLAND_DISPLAY"),
        "available_backends": available,
        "native_sidecar_available": "native" in available,
        "native_sidecar_path": sidecar_path,
        "native": native,
        "display_targets": list(native.get("display_targets", [])),
        "clipboard_surface": native.get("clipboard_surface"),
        "preferred_backend": choose_backend_name("auto", {
            "commands": sorted(commands),
            "display": env.get("DISPLAY"),
            "wayland_display": env.get("WAYLAND_DISPLAY"),
            "available_backends": available,
        }),
    }


def _display_label(display: dict[str, Any] | None) -> str:
    if not display:
        return ""
    name = str(display.get("name") or display.get("id") or "display")
    size = f" {display['width']}x{display['height']}" if display.get("width") and display.get("height") else ""
    origin = ""
    if display.get("origin_x") is not None and display.get("origin_y") is not None:
        origin = f" @({display['origin_x']},{display['origin_y']})"
    suffixes = []
    if display.get("primary"):
        suffixes.append("primary")
    if display.get("backend"):
        suffixes.append(str(display["backend"]))
    suffix = f" [{' '.join(suffixes)}]" if suffixes else ""
    return f"{name}{size}{origin}{suffix}".strip()


def _display_targets(displays: list[dict[str, Any]]) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    for display in displays:
        targets.append({
            "kind": "display",
            "id": display.get("id") or display.get("name") or "display",
            "title": _display_label(display),
            "primary": bool(display.get("primary")),
            "backend": display.get("backend"),
        })
    return targets


def _native_runtime_state(*, sidecar_path: str | None) -> dict[str, Any]:
    state: dict[str, Any] = {
        "available": bool(sidecar_path),
        "path": sidecar_path,
        "healthy": False,
        "capabilities": None,
        "display_targets": [],
        "clipboard_surface": {
            "native_backend": False,
            "control_plane_read_write": False,
            "thin_actions": list(_CLIPBOARD_THIN_ACTIONS),
            "synchronized": False,
            "note": "Burrow only exposes clipboard-oriented desktop actions from Python today; true clipboard sync depends on sidecar support.",
        },
    }
    if not sidecar_path:
        state["status"] = "missing"
        state["error"] = "Native desktop sidecar binary not found"
        return state
    try:
        capabilities = desktop_bridge.get_bridge(executable=sidecar_path).capabilities()
    except desktop_bridge.DesktopBridgeError as exc:
        state["status"] = "error"
        state["error"] = str(exc)
        return state
    displays = [dict(item) for item in capabilities.get("displays", []) if isinstance(item, dict)]
    clipboard = dict(capabilities.get("clipboard") or {})
    state.update({
        "status": "ready",
        "healthy": True,
        "capabilities": capabilities,
        "display_targets": _display_targets(displays),
        "clipboard_surface": {
            "native_backend": bool(clipboard.get("available")),
            "direction": clipboard.get("direction", "none"),
            "stubbed": bool(clipboard.get("stubbed", False)),
            "control_plane_read_write": False,
            "thin_actions": list(_CLIPBOARD_THIN_ACTIONS),
            "synchronized": False,
            "note": "Sidecar clipboard support is capability-gated. Burrow does not read or synchronize clipboard contents over MCP/CLI yet.",
        },
    })
    return state



def choose_backend_name(preferred: str, env_info: dict[str, Any]) -> str | None:
    available = set(env_info.get("available_backends", []))
    if preferred and preferred != "auto":
        return preferred if preferred in available else None
    if "native" in available:
        return "native"
    if "xpra" in available:
        return "xpra"
    if "wayvnc" in available and env_info.get("wayland_display"):
        return "wayvnc"
    if "x11vnc" in available and env_info.get("display"):
        return "x11vnc"
    if "rustdesk" in available:
        return "rustdesk"
    if "wayvnc" in available:
        return "wayvnc"
    if "x11vnc" in available:
        return "x11vnc"
    return None



def choose_backend(preferred: str, env_info: dict[str, Any]) -> DesktopBackend:
    backend_name = choose_backend_name(preferred, env_info)
    if not backend_name:
        raise DesktopConfigError(
            "No supported desktop backend is available. Install xpra, x11vnc, or wayvnc on the remote peer."
        )
    if backend_name == "rustdesk":
        raise DesktopConfigError(
            "RustDesk was detected, but burrow currently launches tunneled xpra/VNC-style backends directly instead of embedding RustDesk's host runtime."
        )
    return BACKENDS[backend_name]



def pick_port(preferred: int) -> int:
    if preferred > 0:
        return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])



def build_launch_command(backend: DesktopBackend, *, remote_port: int,
                         env_info: dict[str, Any], readonly: bool,
                         display: str | None) -> list[str]:
    if backend.name == "xpra":
        resolved_display = display or env_info.get("display") or ":0"
        command = [
            "xpra",
            "shadow",
            resolved_display,
            f"--bind-tcp=127.0.0.1:{remote_port}",
            "--daemon=no",
            "--notifications=no",
            "--printing=no",
            "--speaker=off",
            "--microphone=off",
            "--webcam=no",
            "--clipboard=yes",
        ]
        if readonly:
            command.append("--readonly=yes")
        return command

    if backend.name == "x11vnc":
        resolved_display = display or env_info.get("display")
        if not resolved_display:
            raise DesktopConfigError("x11vnc requires DISPLAY to be set or passed via --display")
        command = [
            "x11vnc",
            "-localhost",
            "-display",
            resolved_display,
            "-rfbport",
            str(remote_port),
            "-forever",
            "-shared",
            "-nopw",
        ]
        if readonly:
            command.append("-viewonly")
        return command

    if backend.name == "wayvnc":
        resolved_display = display or env_info.get("wayland_display")
        if not resolved_display:
            raise DesktopConfigError("wayvnc requires WAYLAND_DISPLAY to be set or passed via --display")
        command = [
            "wayvnc",
            "127.0.0.1",
            str(remote_port),
            "--render-cursor",
        ]
        if readonly:
            command.extend(["--disable-input"])
        return command

    raise DesktopConfigError(f"Unsupported backend: {backend.name}")



def build_connect_hint(session: dict[str, Any]) -> str:
    protocol = session.get("protocol")
    local_port = session.get("local_port", session.get("remote_port"))
    if protocol == "xpra":
        return f"Attach with: xpra attach tcp:127.0.0.1:{local_port}"
    if protocol == "vnc":
        return f"Connect any VNC viewer to 127.0.0.1:{local_port}"
    if protocol == "http":
        return f"Open http://127.0.0.1:{local_port}{session.get('viewer_path', '')}"
    return f"Connect to 127.0.0.1:{local_port}"



def parse_json_output(output: str) -> dict[str, Any]:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    for line in reversed(lines):
        if line.startswith("{") and line.endswith("}"):
            return json.loads(line)
    raise ValueError("No JSON object found in script output")



def write_session_metadata(session: dict[str, Any], *, session_dir: Path = SESSION_DIR) -> Path:
    session_dir.mkdir(parents=True, exist_ok=True)
    path = session_dir / f"{session['session_id']}.json"
    path.write_text(json.dumps(session, indent=2, sort_keys=True))
    return path


def _cleanup_session_metadata(session_id: str, *, session_dir: Path = SESSION_DIR) -> None:
    path = session_dir / f"{session_id}.json"
    if path.exists():
        path.unlink()


def load_session_metadata(session_id: str, *, session_dir: Path = SESSION_DIR) -> dict[str, Any] | None:

    path = session_dir / f"{session_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def list_sessions(*, session_dir: Path = SESSION_DIR) -> dict[str, Any]:
    sessions: list[dict[str, Any]] = []
    if not session_dir.exists():
        return {"sessions": sessions}
    for path in sorted(session_dir.glob("*.json")):
        try:
            session = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        pid = session.get("pid")
        session["running"] = bool(pid and _pid_is_running(int(pid)))
        session["stale"] = not session["running"]
        session["display_label"] = session.get("display_label") or str(session.get("display") or session.get("display_id") or "")
        session["status"] = "stale" if session["stale"] else session.get("status", "running")
        sessions.append(session)
    return {"sessions": sessions}


def _pid_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _require_session(session_id: str, *, session_dir: Path = SESSION_DIR) -> dict[str, Any]:
    session = load_session_metadata(session_id, session_dir=session_dir)
    if not session:
        raise DesktopConfigError(f"Unknown desktop session: {session_id}")
    return session


def _detect_display_server(session: dict[str, Any], env_info: dict[str, Any]) -> str:
    if session.get("display_server") in {"x11", "wayland"}:
        return str(session["display_server"])
    display = str(session.get("display") or "")
    if display.startswith("wayland") or (env_info.get("wayland_display") and not env_info.get("display")):
        return "wayland"
    if display.startswith(":") or env_info.get("display"):
        return "x11"
    if env_info.get("wayland_display"):
        return "wayland"
    return "unknown"


def choose_snapshot_tool(session: dict[str, Any], *,
                         commands: set[str] | None = None,
                         env_info: dict[str, Any] | None = None) -> str:
    commands = _normalize_commands(commands)
    env_info = env_info or describe_environment(commands=commands)
    display_server = _detect_display_server(session, env_info)
    candidates = _WAYLAND_SNAPSHOT_TOOLS if display_server == "wayland" else _X11_SNAPSHOT_TOOLS
    for tool in candidates:
        if tool in commands:
            return tool
    fallback = _X11_SNAPSHOT_TOOLS if display_server == "wayland" else _WAYLAND_SNAPSHOT_TOOLS
    for tool in fallback:
        if tool in commands:
            return tool
    raise DesktopConfigError(
        f"No screenshot tool available for {display_server or 'desktop'} sessions. Install one of: "
        f"{', '.join(list(candidates) + list(fallback))}."
    )


def _snapshot_command(tool: str, output_path: str) -> list[str]:
    if tool == "grim":
        return ["grim", output_path]
    if tool == "gnome-screenshot":
        return ["gnome-screenshot", "-f", output_path]
    if tool == "scrot":
        return ["scrot", output_path]
    if tool == "import":
        return ["import", "-window", "root", output_path]
    raise DesktopConfigError(f"Unsupported screenshot tool: {tool}")


def _native_connect_hint() -> str:
    return "Native desktop sidecar session opened; use burrow snapshot/input controls for this session."


def _native_bridge_failure(session_id: str, operation: str, exc: desktop_bridge.DesktopBridgeError,
                           *, session_dir: Path = SESSION_DIR) -> DesktopConfigError:
    if exc.code in {"unknown_session", "session_closed"}:
        _cleanup_session_metadata(session_id, session_dir=session_dir)
        desktop_bridge.reset_bridge()
        return DesktopConfigError(
            f"Native desktop session {session_id} is stale during {operation}; local metadata was removed. Re-open the session."
        )
    return DesktopConfigError(str(exc))


def _native_snapshot_result(payload: dict[str, Any]) -> dict[str, Any]:
    frame = payload.get("frame") or {}
    image_base64 = str(frame.get("data_base64") or "")
    byte_length = 0
    if image_base64:
        try:
            byte_length = len(base64.b64decode(image_base64))
        except ValueError:
            byte_length = 0
    return {
        "session_id": payload.get("session_id"),
        "display_id": frame.get("display_id"),
        "width": frame.get("width"),
        "height": frame.get("height"),
        "mime_type": frame.get("mime_type", "application/octet-stream"),
        "encoding": frame.get("encoding", "base64"),
        "image_base64": image_base64,
        "byte_length": byte_length,
        "sequence": frame.get("sequence"),
        "stubbed": frame.get("stubbed", False),
    }


def _native_input_actions(action: dict[str, Any]) -> list[dict[str, Any]]:
    if action["type"] == "move":
        return [{"type": "mouse_move", "x": action["x"], "y": action["y"]}]
    if action["type"] == "click":
        mapped: list[dict[str, Any]] = [{"type": "mouse_move", "x": action["x"], "y": action["y"]}]
        for _ in range(int(action.get("count", 1))):
            mapped.extend([
                {"type": "mouse_button", "button": action.get("button", "left"), "pressed": True},
                {"type": "mouse_button", "button": action.get("button", "left"), "pressed": False},
            ])
        return mapped
    if action["type"] == "drag":
        return [
            {"type": "mouse_move", "x": action["x"], "y": action["y"]},
            {"type": "mouse_button", "button": action.get("button", "left"), "pressed": True},
            {"type": "mouse_move", "x": action["to_x"], "y": action["to_y"]},
            {"type": "mouse_button", "button": action.get("button", "left"), "pressed": False},
        ]
    if action["type"] == "scroll":
        return [{"type": "scroll", "delta_x": action["dx"], "delta_y": action["dy"]}]
    if action["type"] == "type_text":
        return [{"type": "text", "text": action["text"]}]
    if action["type"] == "key":
        mapped = [{"type": "key_press", "key": key} for key in action.get("modifiers", [])]
        mapped.append({"type": "key_press", "key": action["key"]})
        mapped.append({"type": "key_release", "key": action["key"]})
        mapped.extend({"type": "key_release", "key": key} for key in reversed(action.get("modifiers", [])))
        return mapped
    if action["type"] == "hotkey":
        keys = list(action["keys"])
        mapped = [{"type": "key_press", "key": key} for key in keys]
        mapped.extend({"type": "key_release", "key": key} for key in reversed(keys))
        return mapped
    raise DesktopConfigError(f"Action {action['type']} is not supported by the native desktop sidecar bridge")


def _clipboard_action_details(session: dict[str, Any], action: dict[str, Any]) -> dict[str, Any] | None:
    intent = action.get("clipboard_intent")
    if not intent:
        return None
    clipboard_capabilities = dict(session.get("clipboard_details") or {})
    if intent == "paste_text":
        note = "paste_text types text into the session; it does not synchronize the remote system clipboard."
    else:
        note = (
            "Clipboard-oriented actions are sent as keyboard shortcuts only. "
            "Burrow does not read or synchronize clipboard contents over the control plane."
        )
    return {
        "requested_action": intent,
        "native_backend": bool(clipboard_capabilities.get("available", False)),
        "control_plane_read_write": False,
        "synchronized": False,
        "note": note,
    }


def snapshot_session(session_id: str, *, session_dir: Path = SESSION_DIR,
                     commands: set[str] | None = None,
                     env_info: dict[str, Any] | None = None) -> dict[str, Any]:
    session = _require_session(session_id, session_dir=session_dir)
    if session.get("backend") == "native":
        try:
            return _native_snapshot_result(desktop_bridge.get_bridge().snapshot(session_id))
        except desktop_bridge.DesktopBridgeError as exc:
            raise _native_bridge_failure(session_id, "snapshot", exc, session_dir=session_dir) from exc
    commands = _normalize_commands(commands)
    env_info = env_info or describe_environment(commands=commands)
    tool = choose_snapshot_tool(session, commands=commands, env_info=env_info)
    fd, output_path = tempfile.mkstemp(prefix=f"burrow-snapshot-{session_id}-", suffix=".png")
    os.close(fd)
    try:
        subprocess.run(
            _snapshot_command(tool, output_path),
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        image_bytes = Path(output_path).read_bytes()
    except FileNotFoundError as exc:
        raise DesktopConfigError(f"Screenshot tool not found: {tool}") from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode(errors="ignore").strip() if exc.stderr else ""
        raise DesktopConfigError(stderr or f"Snapshot command failed via {tool}") from exc
    finally:
        try:
            os.unlink(output_path)
        except OSError:
            pass
    return {
        "session_id": session_id,
        "tool": tool,
        "mime_type": "image/png",
        "image_base64": base64.b64encode(image_bytes).decode("ascii"),
        "byte_length": len(image_bytes),
    }


def choose_input_tools(session: dict[str, Any], *,
                       commands: set[str] | None = None,
                       env_info: dict[str, Any] | None = None) -> dict[str, str | None]:
    commands = _normalize_commands(commands)
    env_info = env_info or describe_environment(commands=commands)
    display_server = _detect_display_server(session, env_info)
    if display_server == "x11":
        for tool in _X11_INPUT_TOOLS:
            if tool in commands:
                return {"display_server": display_server, "pointer": tool, "keyboard": tool}
    if display_server == "wayland":
        pointer = next((tool for tool in _WAYLAND_POINTER_TOOLS if tool in commands), None)
        keyboard = next((tool for tool in _WAYLAND_KEYBOARD_TOOLS if tool in commands), None)
        if pointer or keyboard:
            return {"display_server": display_server, "pointer": pointer, "keyboard": keyboard}
    raise DesktopConfigError(
        f"No input tool available for {display_server or 'desktop'} sessions. Install xdotool, ydotool, or wtype."
    )


def _xdotool_commands(action: dict[str, Any]) -> list[list[str]]:
    button = str(_BUTTON_CODES.get(action.get("button", "left"), 1))
    if action["type"] == "move":
        return [["xdotool", "mousemove", str(action["x"]), str(action["y"])]]
    if action["type"] == "click":
        return [
            ["xdotool", "mousemove", str(action["x"]), str(action["y"])],
            ["xdotool", "click", "--repeat", str(action.get("count", 1)), button],
        ]
    if action["type"] == "drag":
        return [
            ["xdotool", "mousemove", str(action["x"]), str(action["y"])],
            ["xdotool", "mousedown", button],
            ["xdotool", "mousemove", str(action["to_x"]), str(action["to_y"])],
            ["xdotool", "mouseup", button],
        ]
    if action["type"] == "scroll":
        commands: list[list[str]] = []
        if action["dy"]:
            scroll_button = "4" if action["dy"] > 0 else "5"
            commands.append(["xdotool", "click", "--repeat", str(abs(action["dy"])), scroll_button])
        if action["dx"]:
            scroll_button = "7" if action["dx"] > 0 else "6"
            commands.append(["xdotool", "click", "--repeat", str(abs(action["dx"])), scroll_button])
        return commands or [["xdotool", "mousemove", "0", "0"]]
    if action["type"] == "key":
        combo = "+".join([*action.get("modifiers", []), action["key"]])
        return [["xdotool", "key", combo]]
    if action["type"] == "hotkey":
        return [["xdotool", "key", "+".join(action["keys"])]]
    if action["type"] == "type_text":
        return [["xdotool", "type", "--delay", "0", action["text"]]]
    raise DesktopConfigError(f"Unsupported X11 action: {action['type']}")


def _wtype_commands(action: dict[str, Any]) -> list[list[str]]:
    if action["type"] == "type_text":
        return [["wtype", action["text"]]]
    if action["type"] == "key":
        commands: list[list[str]] = [["wtype", "-M", modifier] for modifier in action.get("modifiers", [])]
        commands.append(["wtype", "-k", action["key"]])
        commands.extend([["wtype", "-m", modifier] for modifier in reversed(action.get("modifiers", []))])
        return commands
    if action["type"] == "hotkey":
        commands = [["wtype", "-M", modifier] for modifier in action["keys"][:-1]]
        commands.append(["wtype", "-k", action["keys"][-1]])
        commands.extend([["wtype", "-m", modifier] for modifier in reversed(action["keys"][:-1])])
        return commands
    raise DesktopConfigError(f"Action {action['type']} requires ydotool or X11 tooling")


def _ydotool_commands(action: dict[str, Any]) -> list[list[str]]:
    button = str(_BUTTON_CODES.get(action.get("button", "left"), 1))
    if action["type"] == "move":
        return [["ydotool", "mousemove", "--absolute", str(action["x"]), str(action["y"])]]
    if action["type"] == "click":
        return [
            ["ydotool", "mousemove", "--absolute", str(action["x"]), str(action["y"])],
            ["ydotool", "click", "--repeat", str(action.get("count", 1)), button],
        ]
    if action["type"] == "drag":
        return [
            ["ydotool", "mousemove", "--absolute", str(action["x"]), str(action["y"])],
            ["ydotool", "mousedown", button],
            ["ydotool", "mousemove", "--absolute", str(action["to_x"]), str(action["to_y"])],
            ["ydotool", "mouseup", button],
        ]
    if action["type"] == "scroll":
        return [["ydotool", "wheel", str(action["dx"]), str(action["dy"])]]
    if action["type"] == "type_text":
        return [["ydotool", "type", action["text"]]]
    raise DesktopConfigError(f"Action {action['type']} is not supported by ydotool mapping")


def build_input_commands(action: dict[str, Any], session: dict[str, Any], *,
                         commands: set[str] | None = None,
                         env_info: dict[str, Any] | None = None) -> list[list[str]]:
    tools = choose_input_tools(session, commands=commands, env_info=env_info)
    if tools["display_server"] == "x11":
        return _xdotool_commands(action)
    if action["type"] in {"key", "hotkey", "type_text"} and tools.get("keyboard") == "wtype":
        return _wtype_commands(action)
    if tools.get("pointer") == "ydotool" or tools.get("keyboard") == "ydotool":
        return _ydotool_commands(action)
    raise DesktopConfigError(f"No compatible input tool available for action {action['type']}")


def input_session(session_id: str, action_json: str, *, session_dir: Path = SESSION_DIR,
                  commands: set[str] | None = None,
                  env_info: dict[str, Any] | None = None) -> dict[str, Any]:
    session = _require_session(session_id, session_dir=session_dir)
    action = normalize_action_json(action_json)
    if action["type"] == "snapshot_request":
        return snapshot_session(session_id, session_dir=session_dir, commands=commands, env_info=env_info)
    if session.get("backend") == "native":
        if session.get("readonly"):
            raise DesktopConfigError(f"Desktop session {session_id} is read-only")
        try:
            results = [desktop_bridge.get_bridge().input(session_id, item) for item in _native_input_actions(action)]
        except desktop_bridge.DesktopBridgeError as exc:
            raise _native_bridge_failure(session_id, "input", exc, session_dir=session_dir) from exc
        payload = {
            "session_id": session_id,
            "action": action,
            "ok": all(item.get("accepted", True) for item in results),
            "results": results,
            "notes": [item.get("note") for item in results if item.get("note")],
        }
        clipboard = _clipboard_action_details(session, action)
        if clipboard:
            payload["clipboard"] = clipboard
        return payload
    command_list = build_input_commands(action, session, commands=commands, env_info=env_info)
    try:
        for command in command_list:
            subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except FileNotFoundError as exc:
        raise DesktopConfigError(f"Input tool not found: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode(errors="ignore").strip() if exc.stderr else ""
        raise DesktopConfigError(stderr or f"Input command failed via {command[0]}") from exc
    payload = {"session_id": session_id, "action": action, "ok": True}
    clipboard = _clipboard_action_details(session, action)
    if clipboard:
        payload["clipboard"] = clipboard
    return payload



def wait_for_port(port: int, *, timeout: float = 8.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.25)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.1)
    return False


def _is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _wait_for_process_bind(process: subprocess.Popen[bytes], port: int, *, timeout: float = 8.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            return False
        if _is_port_in_use(port):
            return True
        time.sleep(0.1)
    return False


def start_session(preferred_backend: str = "auto", remote_port: int = 0,

                  readonly: bool = False, display: str | None = None,
                  session_dir: Path = SESSION_DIR) -> dict[str, Any]:
    env_info = describe_environment()
    backend = choose_backend(preferred_backend, env_info)
    if backend.name == "native":
        try:
            bridge = desktop_bridge.get_bridge()
            capabilities = bridge.capabilities()
            payload = bridge.open_session(display_id=display)
        except desktop_bridge.DesktopBridgeError as exc:
            raise DesktopConfigError(str(exc)) from exc
        session = {
            "session_id": payload["session_id"],
            "backend": backend.name,
            "protocol": backend.protocol,
            "description": backend.description,
            "clipboard": bool((capabilities.get("clipboard") or {}).get("available")),
            "audio": backend.audio,
            "seamless": backend.seamless,
            "remote_port": 0,
            "viewer_path": backend.viewer_path,
            "pid": bridge.pid,
            "readonly": readonly,
            "display": payload.get("display_id"),
            "display_id": payload.get("display_id"),
            "display_server": "native",
            "width": payload.get("width"),
            "height": payload.get("height"),
            "stubbed": payload.get("stubbed", False),
            "transport": capabilities.get("transport"),
            "session_scoped": capabilities.get("session_scoped"),
            "input_actions": capabilities.get("input_actions", []),
            "snapshot_formats": capabilities.get("snapshot_formats", []),
            "notes": capabilities.get("notes", []),
            "display_details": next(
                (
                    dict(item)
                    for item in capabilities.get("displays", [])
                    if isinstance(item, dict) and item.get("id") == payload.get("display_id")
                ),
                None,
            ),
            "clipboard_details": capabilities.get("clipboard") or {},
        }
        session["display_label"] = _display_label(session.get("display_details")) or str(session.get("display") or "")
        session["connect_hint"] = _native_connect_hint()
        write_session_metadata(session, session_dir=session_dir)
        return session
    chosen_port = pick_port(remote_port or DEFAULT_PORTS.get(backend.name, 0))
    if remote_port and _is_port_in_use(chosen_port):
        raise DesktopConfigError(f"Port {chosen_port} is already in use on the remote host")
    command = build_launch_command(
        backend,
        remote_port=chosen_port,
        env_info=env_info,
        readonly=readonly,
        display=display,
    )

    env = dict(os.environ)
    if backend.name == "wayvnc":
        env.setdefault("WAYLAND_DISPLAY", display or env_info.get("wayland_display") or "")

    process = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        env=env,
    )
    if not _wait_for_process_bind(process, chosen_port):
        try:
            process.terminate()
        except OSError:
            pass
        raise DesktopConfigError(
            f"{backend.name} failed to start cleanly on port {chosen_port}. Check that the remote host has access to the current graphical session."
        )

    session = {
        "session_id": uuid.uuid4().hex[:12],
        "backend": backend.name,
        "protocol": backend.protocol,
        "description": backend.description,
        "clipboard": backend.clipboard,
        "audio": backend.audio,
        "seamless": backend.seamless,
        "remote_port": chosen_port,
        "viewer_path": backend.viewer_path,
        "pid": process.pid,
        "readonly": readonly,
        "display": display or env_info.get("display") or env_info.get("wayland_display"),
        "display_server": "wayland" if env_info.get("wayland_display") and not env_info.get("display") else "x11",
        "clipboard_details": {
            "available": backend.clipboard,
            "direction": "viewer-dependent" if backend.clipboard else "none",
            "stubbed": False,
        },
    }
    session["display_label"] = str(session.get("display") or "")
    session["connect_hint"] = build_connect_hint(session)
    write_session_metadata(session, session_dir=session_dir)
    return session



def stop_session(session_id: str, *, session_dir: Path = SESSION_DIR) -> dict[str, Any]:
    session = load_session_metadata(session_id, session_dir=session_dir)
    if not session:
        raise DesktopConfigError(f"Unknown desktop session: {session_id}")

    if session.get("backend") == "native":
        stale_cleanup = False
        try:
            desktop_bridge.get_bridge().close_session(session_id)
        except desktop_bridge.DesktopBridgeError as exc:
            if exc.code not in {"unknown_session", "session_closed"}:
                raise _native_bridge_failure(session_id, "close", exc, session_dir=session_dir) from exc
            stale_cleanup = True
        _cleanup_session_metadata(session_id, session_dir=session_dir)
        remaining_native = [
            item for item in list_sessions(session_dir=session_dir)["sessions"]
            if item.get("backend") == "native"
        ]
        if not remaining_native:
            desktop_bridge.reset_bridge()
        return {"stopped": True, "session_id": session_id, "pid": session.get("pid"), "stale_cleanup": stale_cleanup}

    pid = int(session["pid"])
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            break
        except OSError as exc:
            raise DesktopConfigError(f"Failed to stop desktop session {session_id}: {exc}") from exc
        time.sleep(0.2)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
    path = session_dir / f"{session_id}.json"
    if path.exists():
        path.unlink()
    return {"stopped": True, "session_id": session_id, "pid": pid}



def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, sort_keys=True))



def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Burrow desktop backend helper")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("capabilities", help="Print local desktop capability metadata")

    start_cmd = sub.add_parser("start", help="Start a local desktop backend")
    start_cmd.add_argument("--backend", default="auto")
    start_cmd.add_argument("--remote-port", type=int, default=0)
    start_cmd.add_argument("--readonly", action="store_true")
    start_cmd.add_argument("--display", default=None)

    sub.add_parser("list-sessions", help="List helper-managed desktop sessions")

    snapshot_cmd = sub.add_parser("snapshot", help="Capture a screenshot for a session")
    snapshot_cmd.add_argument("--session-id", required=True)

    input_cmd = sub.add_parser("input", help="Send normalized input to a session")
    input_cmd.add_argument("--session-id", required=True)
    input_cmd.add_argument("--action-json", required=True)

    stop_cmd = sub.add_parser("stop", help="Stop a previously-started desktop backend")
    stop_cmd.add_argument("--session-id", required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "capabilities":
            emit(describe_environment())
            return 0
        if args.command == "start":
            emit(start_session(
                preferred_backend=args.backend,
                remote_port=args.remote_port,
                readonly=args.readonly,
                display=args.display,
            ))
            return 0
        if args.command == "list-sessions":
            emit(list_sessions())
            return 0
        if args.command == "snapshot":
            emit(snapshot_session(args.session_id))
            return 0
        if args.command == "input":
            emit(input_session(args.session_id, args.action_json))
            return 0
        if args.command == "stop":
            emit(stop_session(args.session_id))
            return 0
    except DesktopConfigError as exc:
        emit({"error": str(exc)})
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
