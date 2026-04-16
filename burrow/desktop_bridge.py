from __future__ import annotations

import json
import os
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Any


_REPO_ROOT = Path(__file__).resolve().parent.parent
_CLIENT_NAME = "burrow-python"
_PROTOCOL_VERSION = 2
_DEFAULT_CANDIDATES = (
    _REPO_ROOT / "native" / "target" / "debug" / "burrow-rd-host",
    _REPO_ROOT / "native" / "target" / "release" / "burrow-rd-host",
)
_DEFAULT_STALE_SESSION_CODES = frozenset({"unknown_session", "session_closed"})
_RESULT_TYPE_BY_COMMAND = {
    "capabilities": "capabilities",
    "open_session": "open_session",
    "session_status": "session_status",
    "snapshot": "snapshot",
    "input": "input",
    "clipboard": "clipboard",
    "stream": "stream",
    "privacy": "privacy",
    "close_session": "close_session",
}


class DesktopBridgeError(RuntimeError):
    """Raised when the native desktop sidecar cannot be used."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
        warnings: list[str] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.details = details or {}
        self.warnings = list(warnings or [])


class DesktopBridge:
    def __init__(
        self,
        executable: str | os.PathLike[str],
        *,
        popen_factory: Any | None = None,
    ) -> None:
        self.executable = str(executable)
        self._popen_factory = subprocess.Popen if popen_factory is None else popen_factory
        self._process: subprocess.Popen[str] | Any | None = None
        self._lock = threading.Lock()
        self._stale_session_codes = set(_DEFAULT_STALE_SESSION_CODES)
        self._supported_commands: set[str] = set()
        self._last_warnings: list[str] = []
        self._client = {"name": _CLIENT_NAME}

    @property
    def last_warnings(self) -> list[str]:
        return list(self._last_warnings)

    @property
    def stale_session_codes(self) -> set[str]:
        return set(self._stale_session_codes)

    @property
    def supported_commands(self) -> set[str]:
        return set(self._supported_commands)

    def _clear_process(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        for stream_name in ("stdin", "stdout", "stderr"):
            stream = getattr(process, stream_name, None)
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass

    def _stderr_text(self, process: subprocess.Popen[str] | Any | None = None) -> str:
        process = self._process if process is None else process
        if process is None:
            return ""
        stderr_pipe = getattr(process, "stderr", None)
        if stderr_pipe is None:
            return ""
        try:
            return str(stderr_pipe.read() or "").strip()
        except OSError:
            return ""

    @property
    def process(self) -> subprocess.Popen[str] | Any:
        if self._process is None:
            raise DesktopBridgeError("Native desktop sidecar is not running")
        return self._process

    @property
    def pid(self) -> int | None:
        process = self._process
        return None if process is None else getattr(process, "pid", None)

    def start(self) -> "DesktopBridge":
        if self._process is not None and self._process.poll() is not None:
            self._clear_process()
        if self._process is not None and self._process.poll() is None:
            return self
        self._process = self._popen_factory(
            [self.executable],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        if self._process.stdin is None or self._process.stdout is None:
            raise DesktopBridgeError("Native desktop sidecar did not provide stdio pipes")
        return self

    def _build_envelope(self, command: str, request_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": request_id,
            "protocol_version": _PROTOCOL_VERSION,
            "client": self._client,
            "command": command,
            **payload,
        }

    def _validate_response(self, response: dict[str, Any], request_id: str) -> list[str]:
        response_id = response.get("id")
        if response_id is not None and response_id != request_id:
            raise DesktopBridgeError(
                f"Mismatched response id from native desktop sidecar: expected {request_id}, got {response_id}"
            )

        warnings = response.get("warnings")
        if warnings is None:
            normalized_warnings: list[str] = []
        elif isinstance(warnings, list) and all(isinstance(item, str) for item in warnings):
            normalized_warnings = list(warnings)
        else:
            raise DesktopBridgeError("Native desktop sidecar returned invalid warnings payload")

        protocol_version = response.get("protocol_version")
        min_compatible = response.get("min_compatible_protocol_version")
        if isinstance(min_compatible, int) and min_compatible > _PROTOCOL_VERSION:
            raise DesktopBridgeError(
                "Native desktop sidecar requires a newer bridge protocol version",
                details={
                    "protocol_version": protocol_version,
                    "min_compatible_protocol_version": min_compatible,
                },
                warnings=normalized_warnings,
            )
        return normalized_warnings

    def _update_capability_state(self, result: dict[str, Any]) -> None:
        recovery = result.get("recovery")
        if isinstance(recovery, dict):
            codes = recovery.get("stale_session_error_codes")
            if isinstance(codes, list) and all(isinstance(item, str) for item in codes):
                self._stale_session_codes = set(codes) or set(_DEFAULT_STALE_SESSION_CODES)
        commands = result.get("supported_commands")
        if isinstance(commands, list) and all(isinstance(item, str) for item in commands):
            self._supported_commands = set(commands)

    def _validate_result(self, command: str, result: dict[str, Any]) -> dict[str, Any]:
        expected_type = _RESULT_TYPE_BY_COMMAND.get(command)
        result_type = result.get("type")
        if expected_type is not None and result_type is not None and result_type != expected_type:
            raise DesktopBridgeError(
                f"Native desktop sidecar returned unexpected result type for {command}: {result_type}"
            )
        if command == "capabilities":
            self._update_capability_state(result)
        return result

    def close(self) -> None:
        process = self._process
        if process is None:
            return
        stdin = getattr(process, "stdin", None)
        if stdin is not None:
            try:
                stdin.close()
            except OSError:
                pass
        if process.poll() is None:
            terminate = getattr(process, "terminate", None)
            if callable(terminate):
                try:
                    terminate()
                except OSError:
                    pass
        self._clear_process()

    def request(self, command: str, *, session_scoped: bool = False, **payload: Any) -> dict[str, Any]:
        with self._lock:
            for attempt in range(2):
                self.start()
                process = self.process
                request_id = uuid.uuid4().hex
                envelope = self._build_envelope(command, request_id, payload)
                try:
                    process.stdin.write(json.dumps(envelope) + "\n")
                    process.stdin.flush()
                except (BrokenPipeError, OSError) as exc:
                    self._clear_process()
                    if attempt == 0:
                        continue
                    raise DesktopBridgeError("Native desktop sidecar stdin closed unexpectedly") from exc

                try:
                    line = process.stdout.readline()
                except OSError as exc:
                    self._clear_process()
                    if attempt == 0:
                        continue
                    raise DesktopBridgeError("Native desktop sidecar stdout closed unexpectedly") from exc
                if not line:
                    stderr = self._stderr_text(process)
                    self._clear_process()
                    if attempt == 0:
                        continue
                    message = stderr or "Native desktop sidecar closed without sending a response"
                    raise DesktopBridgeError(message)

                try:
                    response = json.loads(line)
                except json.JSONDecodeError as exc:
                    self._clear_process()
                    if attempt == 0:
                        continue
                    raise DesktopBridgeError(f"Invalid JSON from native desktop sidecar: {line.strip()}") from exc

                if not isinstance(response, dict):
                    self._clear_process()
                    if attempt == 0:
                        continue
                    raise DesktopBridgeError("Native desktop sidecar returned a non-object response")

                warnings = self._validate_response(response, request_id)
                self._last_warnings = warnings

                if not response.get("ok"):
                    error = response.get("error") or {}
                    code = error.get("code")
                    message = error.get("message") or "Native desktop sidecar request failed"
                    if session_scoped and code in self._stale_session_codes:
                        self._clear_process()
                    raise DesktopBridgeError(message, code=code, details=error, warnings=warnings)

                result = response.get("result")
                if not isinstance(result, dict):
                    raise DesktopBridgeError("Native desktop sidecar returned no result payload")
                return self._validate_result(command, result)
            raise DesktopBridgeError("Native desktop sidecar request failed after restart")

    def capabilities(self) -> dict[str, Any]:
        return self.request("capabilities")

    def open_session(self, *, display_id: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if display_id is not None:
            payload["display_id"] = display_id
        return self.request("open_session", **payload)

    def session_status(self, session_id: str) -> dict[str, Any]:
        return self.request("session_status", session_scoped=True, session_id=session_id)

    def snapshot(self, session_id: str) -> dict[str, Any]:
        return self.request("snapshot", session_scoped=True, session_id=session_id)

    def input(self, session_id: str, action: dict[str, Any]) -> dict[str, Any]:
        return self.request("input", session_scoped=True, session_id=session_id, action=action)

    def clipboard(self, session_id: str, operation: dict[str, Any]) -> dict[str, Any]:
        return self.request("clipboard", session_scoped=True, session_id=session_id, operation=operation)

    def stream(self, session_id: str, operation: dict[str, Any]) -> dict[str, Any]:
        return self.request("stream", session_scoped=True, session_id=session_id, operation=operation)

    def privacy(self, session_id: str, mode: dict[str, Any]) -> dict[str, Any]:
        return self.request("privacy", session_scoped=True, session_id=session_id, mode=mode)

    def close_session(self, session_id: str) -> dict[str, Any]:
        return self.request("close_session", session_scoped=True, session_id=session_id)


def find_sidecar_binary(*, candidates: list[str | os.PathLike[str]] | None = None) -> str | None:
    env_path = os.environ.get("BURROW_RD_HOST_PATH")
    search_list = [env_path] if env_path else []
    search_list.extend(candidates or _DEFAULT_CANDIDATES)
    for candidate in search_list:
        if not candidate:
            continue
        path = Path(candidate)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


_bridge_lock = threading.Lock()
_bridge_singleton: DesktopBridge | None = None


def sidecar_available() -> bool:
    return find_sidecar_binary() is not None


def get_bridge(*, executable: str | os.PathLike[str] | None = None) -> DesktopBridge:
    global _bridge_singleton
    with _bridge_lock:
        if _bridge_singleton is not None:
            return _bridge_singleton.start()
        resolved = str(executable) if executable is not None else find_sidecar_binary()
        if not resolved:
            raise DesktopBridgeError("Native desktop sidecar binary not found")
        _bridge_singleton = DesktopBridge(resolved).start()
        return _bridge_singleton


def reset_bridge() -> None:
    global _bridge_singleton
    with _bridge_lock:
        if _bridge_singleton is not None:
            _bridge_singleton.close()
        _bridge_singleton = None
