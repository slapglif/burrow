#!/usr/bin/env python3
"""Local Burrow two-peer smoketest.

Starts a local registry, connects Diogi and FoxBoi peers, performs a verified
round-trip exchange, prints a JSON transcript, and exits non-zero on failure.
Useful when the public registry is unavailable and you need a local proof that
Burrow itself still works.
"""

import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from burrow.peer import Peer  # noqa: E402


def wait_for_port(port: int, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = socket.socket()
        s.settimeout(0.5)
        try:
            s.connect(("127.0.0.1", port))
            return
        except OSError:
            time.sleep(0.1)
        finally:
            s.close()
    raise TimeoutError(f"Registry did not open on 127.0.0.1:{port}")


async def main() -> int:
    port = int(os.environ.get("BURROW_LOCAL_SMOKETEST_PORT", "7654"))
    transcript: list[dict] = []
    server = subprocess.Popen(
        [sys.executable, "-m", "burrow.server", "--port", str(port)],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        preexec_fn=os.setsid,
    )
    try:
        wait_for_port(port)
        uri = f"ws://127.0.0.1:{port}"
        got_reply = asyncio.Event()
        pending: set[asyncio.Task] = set()

        fox = Peer(uri, "FoxBoi-smoketest", auto_reconnect=False)
        diogi = Peer(uri, "Diogi-smoketest", auto_reconnect=False)

        async def fox_reply(to: str, body: str):
            await fox.send_message(to, body)
            transcript.append({"event": "fox_message_out", "to": to, "body": body})

        def fox_on_message(from_name: str, body: str):
            transcript.append({"event": "fox_message_in", "from_name": from_name, "body": body})
            task = asyncio.create_task(fox_reply(from_name, f"ack from FoxBoi: {body}"))
            pending.add(task)
            task.add_done_callback(lambda t: pending.discard(t))

        def diogi_on_message(from_name: str, body: str):
            transcript.append({"event": "diogi_message_in", "from_name": from_name, "body": body})
            got_reply.set()

        fox.on_message = fox_on_message
        diogi.on_message = diogi_on_message

        await fox.connect()
        transcript.append({"event": "fox_connected", "id": fox.id})
        fox_listener = asyncio.create_task(fox.listen())

        await diogi.connect()
        transcript.append({"event": "diogi_connected", "id": diogi.id})
        diogi_listener = asyncio.create_task(diogi.listen())
        await asyncio.sleep(0.2)

        payload = "handshake from Diogi"
        await diogi.send_message("FoxBoi-smoketest", payload)
        transcript.append({"event": "diogi_message_out", "to": "FoxBoi-smoketest", "body": payload})

        await asyncio.wait_for(got_reply.wait(), timeout=5)
        if pending:
            await asyncio.gather(*list(pending), return_exceptions=True)
        print(json.dumps({"ok": True, "uri": uri, "transcript": transcript}, ensure_ascii=False))

        fox_listener.cancel()
        diogi_listener.cancel()
        await fox.stop()
        await diogi.stop()
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}", "transcript": transcript}, ensure_ascii=False))
        return 1
    finally:
        try:
            os.killpg(os.getpgid(server.pid), signal.SIGTERM)
        except Exception:
            pass
        try:
            server.wait(timeout=3)
        except Exception:
            try:
                os.killpg(os.getpgid(server.pid), signal.SIGKILL)
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
