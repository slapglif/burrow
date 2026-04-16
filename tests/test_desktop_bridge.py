import json
import os
from pathlib import Path

import pytest

from burrow import desktop_bridge


_NATIVE_SIDE_CAR_BINARY = Path("/home/mikeb/burrow/native/target/debug/burrow-rd-host")


def _binary_available() -> bool:
    return _NATIVE_SIDE_CAR_BINARY.is_file() and os.access(_NATIVE_SIDE_CAR_BINARY, os.X_OK)



class _FakeStdout:
    def __init__(self):
        self._lines = []

    def push(self, payload):
        if isinstance(payload, str):
            self._lines.append(payload)
        else:
            self._lines.append(json.dumps(payload) + "\n")

    def readline(self):
        if self._lines:
            return self._lines.pop(0)
        return ""

    def close(self):
        return None


class _FakeStderr:
    def __init__(self, text=""):
        self._text = text

    def read(self):
        return self._text

    def close(self):
        return None


class _FakeStdin:
    def __init__(self, process):
        self.process = process
        self._buffer = ""
        self.closed = False

    def write(self, data):
        self._buffer += data
        return len(data)

    def flush(self):
        line = self._buffer.strip()
        self._buffer = ""
        if line:
            self.process.handle_request(json.loads(line))

    def close(self):
        self.closed = True


class _FakeProcess:
    def __init__(self, handler, *, pid=4321, stderr_text=""):
        self.handler = handler
        self.pid = pid
        self.stdout = _FakeStdout()
        self.stderr = _FakeStderr(stderr_text)
        self.stdin = _FakeStdin(self)
        self._poll = None
        self.terminated = False
        self.requests = []

    def handle_request(self, request):
        self.requests.append(request)
        payload = self.handler(request)
        if payload is not None:
            self.stdout.push(payload)

    def poll(self):
        return self._poll

    def terminate(self):
        self.terminated = True
        self._poll = 0


def _success_response(request, result=None, *, warnings=None):
    payload = {
        "id": request["id"],
        "protocol_version": 2,
        "min_compatible_protocol_version": 1,
        "ok": True,
        "result": result or {"type": request["command"]},
    }
    if warnings is not None:
        payload["warnings"] = warnings
    return payload


def _error_response(request, *, code, message, warnings=None):
    payload = {
        "id": request["id"],
        "protocol_version": 2,
        "min_compatible_protocol_version": 1,
        "ok": False,
        "error": {"code": code, "message": message},
    }
    if warnings is not None:
        payload["warnings"] = warnings
    return payload


def test_find_sidecar_binary_prefers_env_path(tmp_path, monkeypatch):
    env_binary = tmp_path / "burrow-rd-host"
    env_binary.write_text("#!/bin/sh\n")
    env_binary.chmod(0o755)

    monkeypatch.setenv("BURROW_RD_HOST_PATH", str(env_binary))

    resolved = desktop_bridge.find_sidecar_binary(candidates=[tmp_path / "missing-host"])

    assert resolved == str(env_binary)


def test_bridge_request_round_trip_includes_protocol_and_client_metadata():
    seen = {}

    def fake_popen(*args, **kwargs):
        assert args[0] == ["/tmp/burrow-rd-host"]

        def handler(request):
            seen.update(request)
            return _success_response(
                request,
                {
                    "type": "capabilities",
                    "transport": "jsonl-stdio",
                    "session_scoped": True,
                    "supported_commands": ["capabilities", "session_status"],
                    "recovery": {"stale_session_error_codes": ["unknown_session", "session_closed"]},
                },
                warnings=["capability warning"],
            )

        return _FakeProcess(handler)

    bridge = desktop_bridge.DesktopBridge("/tmp/burrow-rd-host", popen_factory=fake_popen)

    result = bridge.capabilities()

    assert seen["protocol_version"] == 2
    assert seen["client"] == {"name": "burrow-python"}
    assert result["type"] == "capabilities"
    assert result["transport"] == "jsonl-stdio"
    assert bridge.pid == 4321
    assert bridge.last_warnings == ["capability warning"]
    assert bridge.supported_commands == {"capabilities", "session_status"}
    assert bridge.stale_session_codes == {"unknown_session", "session_closed"}


def test_bridge_raises_for_error_response():
    def fake_popen(*args, **kwargs):
        def handler(request):
            return _error_response(
                request,
                code="unknown_session",
                message="unknown session: session-1",
                warnings=["session warning"],
            )

        return _FakeProcess(handler)

    bridge = desktop_bridge.DesktopBridge("/tmp/burrow-rd-host", popen_factory=fake_popen)

    with pytest.raises(desktop_bridge.DesktopBridgeError, match="unknown session: session-1") as exc:
        bridge.snapshot("session-1")

    assert exc.value.code == "unknown_session"
    assert exc.value.warnings == ["session warning"]


def test_get_bridge_reuses_singleton_and_reset_closes_process(monkeypatch):
    created = []

    def fake_popen(*args, **kwargs):
        def handler(request):
            return _success_response(request)

        process = _FakeProcess(handler, pid=9876)
        created.append(process)
        return process

    monkeypatch.setattr(desktop_bridge, "find_sidecar_binary", lambda **kwargs: "/tmp/burrow-rd-host")
    monkeypatch.setattr(desktop_bridge.subprocess, "Popen", fake_popen)
    desktop_bridge.reset_bridge()

    first = desktop_bridge.get_bridge()
    second = desktop_bridge.get_bridge()

    assert first is second
    assert len(created) == 1

    desktop_bridge.reset_bridge()

    assert created[0].terminated is True


def test_bridge_restarts_after_sidecar_closes_before_response():
    attempts = []

    def fake_popen(*args, **kwargs):
        attempts.append(True)
        if len(attempts) == 1:
            process = _FakeProcess(lambda request: _success_response(request), stderr_text="first process died")

            def ignore_request(request):
                process.requests.append(request)
                return None

            process.handle_request = ignore_request
            return process

        def handler(request):
            return _success_response(request)

        return _FakeProcess(handler)

    bridge = desktop_bridge.DesktopBridge("/tmp/burrow-rd-host", popen_factory=fake_popen)

    assert bridge.capabilities() == {"type": "capabilities"}
    assert len(attempts) == 2


def test_bridge_restarts_after_invalid_json_response():
    attempts = []

    def fake_popen(*args, **kwargs):
        attempts.append(True)
        if len(attempts) == 1:
            return _FakeProcess(lambda request: "{not json\n")

        def handler(request):
            return _success_response(request, {"type": "clipboard", "operation": "paste", "supported": False})

        return _FakeProcess(handler)

    bridge = desktop_bridge.DesktopBridge("/tmp/burrow-rd-host", popen_factory=fake_popen)

    result = bridge.clipboard("session-1", {"type": "paste"})

    assert result == {"type": "clipboard", "operation": "paste", "supported": False}
    assert len(attempts) == 2


def test_expanded_command_helpers_send_expected_payloads():
    created = []

    def fake_popen(*args, **kwargs):
        def handler(request):
            return _success_response(request, {"type": request["command"]})

        process = _FakeProcess(handler)
        created.append(process)
        return process

    bridge = desktop_bridge.DesktopBridge("/tmp/burrow-rd-host", popen_factory=fake_popen)

    assert bridge.session_status("session-1") == {"type": "session_status"}
    assert bridge.stream("session-1", {"type": "poll"}) == {"type": "stream"}
    assert bridge.privacy("session-1", {"type": "query"}) == {"type": "privacy"}
    assert bridge.close_session("session-1") == {"type": "close_session"}

    requests = created[0].requests
    assert requests[0]["command"] == "session_status"
    assert requests[0]["session_id"] == "session-1"
    assert requests[1]["command"] == "stream"
    assert requests[1]["operation"] == {"type": "poll"}
    assert requests[2]["command"] == "privacy"
    assert requests[2]["mode"] == {"type": "query"}
    assert requests[3]["command"] == "close_session"


def test_session_scoped_stale_error_clears_process_after_capability_discovery():
    created = []

    def fake_popen(*args, **kwargs):
        def handler(request):
            if request["command"] == "capabilities":
                return _success_response(
                    request,
                    {
                        "type": "capabilities",
                        "supported_commands": ["capabilities", "snapshot"],
                        "recovery": {"stale_session_error_codes": ["expired_session"]},
                    },
                )
            return _error_response(request, code="expired_session", message="session expired")

        process = _FakeProcess(handler)
        created.append(process)
        return process

    bridge = desktop_bridge.DesktopBridge("/tmp/burrow-rd-host", popen_factory=fake_popen)
    bridge.capabilities()

    with pytest.raises(desktop_bridge.DesktopBridgeError, match="session expired") as exc:
        bridge.snapshot("session-1")

    assert exc.value.code == "expired_session"
    assert bridge.pid is None
    assert created[0].stdin.closed is True


def test_smoke_open_stream_input_flow_against_native_sidecar_binary():
    if not _binary_available():
        pytest.skip("Native sidecar binary not present: /home/mikeb/burrow/native/target/debug/burrow-rd-host")

    bridge = desktop_bridge.DesktopBridge(str(_NATIVE_SIDE_CAR_BINARY))
    session_id = None

    try:
        capabilities = bridge.capabilities()
        assert capabilities["type"] == "capabilities"
        assert capabilities["session_scoped"] is True
        assert capabilities.get("snapshot_formats", [])

        supports_stream = "stream" in capabilities.get("supported_commands", [])
        if not supports_stream:
            pytest.skip("Native sidecar does not yet expose stream command")

        opened = bridge.open_session()
        assert opened["type"] == "open_session"
        session_id = opened["session_id"]
        assert isinstance(session_id, str) and session_id

        stream_started = bridge.stream(
            session_id,
            {
                "type": "start",
                "format": capabilities["snapshot_formats"][0],
            },
        )
        assert stream_started["type"] == "stream"
        assert stream_started["session_id"] == session_id
        assert stream_started["operation"] == "start"
        assert isinstance(stream_started["accepted"], bool)

        for _ in range(2):
            polled = bridge.stream(session_id, {"type": "poll"})
            assert polled["type"] == "stream"
            assert polled["session_id"] == session_id
            assert polled["operation"] == "poll"
            assert isinstance(polled["accepted"], bool)
            assert isinstance(polled["active"], bool)
            assert isinstance(polled["stubbed"], bool)
            assert isinstance(polled["note"], str)

        input_result = bridge.input(session_id, {"type": "key_press", "key": "a"})
        assert input_result["type"] == "input"
        assert input_result["session_id"] == session_id
        assert isinstance(input_result["accepted"], bool)
        assert isinstance(input_result["stubbed"], bool)
        assert isinstance(input_result["note"], str)
    finally:
        if session_id is not None:
            try:
                closed = bridge.close_session(session_id)
            except desktop_bridge.DesktopBridgeError:
                closed = None
            else:
                assert closed["type"] == "close_session"
                assert closed["session_id"] == session_id
                assert closed["closed"] is True
        bridge.close()


@pytest.fixture(autouse=True)
def _cleanup_bridge():
    desktop_bridge.reset_bridge()
    yield
    desktop_bridge.reset_bridge()
