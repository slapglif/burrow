"""Burrow P2P client — async Peer that connects to a registry server."""

import asyncio
import json
import base64
import os
import uuid
from pathlib import Path

from websockets.asyncio.client import connect

from burrow import protocol


RECEIVE_DIR = Path("./burrow-received")


class Peer:
    def __init__(self, uri: str, name: str):
        self.uri = uri
        self.name = name
        self.id = None
        self.ws = None
        self.peers = {}          # id -> name
        self.on_message = None   # callback(from_name, body)
        self.on_file = None      # callback(from_name, filepath)
        self._transfers = {}     # transfer_id -> {name, size, chunks, path}
        self._tunnels = {}       # tunnel_id -> {reader, writer}

    # --- Core lifecycle ---

    async def connect(self):
        """Connect to the registry and register this peer."""
        self.ws = await connect(self.uri)
        await self._send(protocol.register(self.name))
        raw = await self.ws.recv()
        resp = json.loads(raw)
        if resp.get("type") == protocol.REGISTERED:
            self.id = resp["id"]
            self.peers = resp.get("peers", {})
        else:
            raise RuntimeError(f"Registration failed: {resp}")

    async def listen(self):
        """Main receive loop — dispatch incoming messages by type."""
        async for raw in self.ws:
            data = json.loads(raw)
            kind = data.get("type")

            if kind == protocol.MSG:
                if self.on_message:
                    self.on_message(data.get("from_name", "?"), data["body"])
                else:
                    print(f"[{data.get('from_name', '?')}] {data['body']}")

            elif kind == protocol.PEER_JOINED:
                self.peers[data["id"]] = data["name"]
                print(f"+ {data['name']} joined")

            elif kind == protocol.PEER_LEFT:
                name = self.peers.pop(data["id"], data.get("name", "?"))
                print(f"- {name} left")

            elif kind == protocol.PEERS:
                raw = data.get("peers", [])
                if isinstance(raw, list):
                    self.peers = {p["id"]: p["name"] for p in raw}
                else:
                    self.peers = raw

            elif kind == protocol.FILE_START:
                tid = data["transfer_id"]
                self._transfers[tid] = {
                    "name": data["name"],
                    "size": data["size"],
                    "chunks": [],
                    "from_name": data.get("from_name", "?"),
                }

            elif kind == protocol.FILE_CHUNK:
                tid = data["transfer_id"]
                entry = self._transfers.get(tid)
                if not entry:
                    continue
                entry["chunks"].append(data["data"])
                if data.get("final"):
                    raw_bytes = b"".join(base64.b64decode(c) for c in entry["chunks"])
                    RECEIVE_DIR.mkdir(parents=True, exist_ok=True)
                    # Sanitise filename: strip path separators to prevent traversal
                    safe_name = Path(entry["name"]).name
                    if not safe_name:
                        safe_name = f"unnamed-{tid}"
                    dest = RECEIVE_DIR / safe_name
                    dest.write_bytes(raw_bytes)
                    print(f"Received file {safe_name} from {entry['from_name']}")
                    if self.on_file:
                        self.on_file(entry["from_name"], str(dest))
                    del self._transfers[tid]

            elif kind == protocol.TUNNEL_OPEN:
                asyncio.create_task(
                    self._handle_tunnel_open(data)
                )

            elif kind == protocol.TUNNEL_DATA:
                tid = data["tunnel_id"]
                tunnel = self._tunnels.get(tid)
                if tunnel and tunnel.get("writer"):
                    tunnel["writer"].write(base64.b64decode(data["data"]))
                    await tunnel["writer"].drain()

            elif kind == protocol.TUNNEL_CLOSE:
                tid = data["tunnel_id"]
                tunnel = self._tunnels.pop(tid, None)
                if tunnel and tunnel.get("writer"):
                    tunnel["writer"].close()

            elif kind == protocol.ERROR:
                print(f"Error: {data.get('message', '?')}")

            elif kind == protocol.PONG:
                pass  # keepalive response

    # --- Sending helpers ---

    async def send_message(self, to: str, body: str):
        """Send a text message to a peer (by name or id)."""
        await self._send(protocol.msg(self._resolve(to), body))

    async def send_file(self, to: str, filepath: str):
        """Read a file, chunk it, and send via the registry."""
        target = self._resolve(to)
        path = Path(filepath)
        raw_bytes = path.read_bytes()
        size = len(raw_bytes)
        name = path.name
        transfer_id = uuid.uuid4().hex[:8]

        await self._send(protocol.file_start(target, name, size, transfer_id))

        offset = 0
        seq = 0
        while offset < size:
            chunk = raw_bytes[offset : offset + protocol.CHUNK_SIZE]
            offset += len(chunk)
            b64 = base64.b64encode(chunk).decode()
            final = offset >= size
            await self._send(
                protocol.file_chunk(target, transfer_id, seq, b64, final)
            )
            seq += 1
            if not final:
                pct = int(offset / size * 100)
                print(f"  sending {name}: {pct}%", end="\r")

        print(f"  sent {name} ({size} bytes)")

    async def open_tunnel(self, to: str, local_port: int, remote_port: int):
        """Start a local TCP server that tunnels connections to a remote peer."""
        target = self._resolve(to)

        async def on_tcp_connect(reader, writer):
            tid = uuid.uuid4().hex[:8]
            self._tunnels[tid] = {"reader": reader, "writer": writer}
            await self._send(protocol.tunnel_open(target, tid, remote_port))
            asyncio.create_task(self._relay_tcp_to_ws(tid, target, reader))

        server = await asyncio.start_server(on_tcp_connect, "127.0.0.1", local_port)
        print(f"Tunnel listening on 127.0.0.1:{local_port} -> {to}:{remote_port}")
        return server

    async def request_peers(self):
        """Ask the registry for the current peer list."""
        await self._send(protocol.peers())

    # --- Internal helpers ---

    def _resolve(self, name_or_id: str) -> str:
        """Resolve a peer name to its ID. Pass through if already an ID."""
        if name_or_id in self.peers:
            return name_or_id
        for pid, pname in self.peers.items():
            if pname == name_or_id:
                return pid
        return name_or_id  # assume it is an id the registry can route

    async def _send(self, msg: dict):
        await self.ws.send(json.dumps(msg))

    async def _relay_tcp_to_ws(self, tunnel_id: str, target: str,
                               reader: asyncio.StreamReader):
        """Read from a local TCP socket and forward over WebSocket."""
        try:
            while True:
                data = await reader.read(protocol.CHUNK_SIZE)
                if not data:
                    break
                b64 = base64.b64encode(data).decode()
                await self._send(protocol.tunnel_data(target, tunnel_id, b64))
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            await self._send(protocol.tunnel_close(target, tunnel_id))
            tunnel = self._tunnels.pop(tunnel_id, None)
            if tunnel and tunnel.get("writer"):
                tunnel["writer"].close()

    async def _handle_tunnel_open(self, data: dict):
        """Respond to an incoming tunnel request by connecting locally."""
        tid = data["tunnel_id"]
        port = data["remote_port"]
        from_id = data.get("from", data.get("from_id", ""))
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
        except OSError as exc:
            print(f"Tunnel {tid}: cannot connect to localhost:{port} — {exc}")
            await self._send(protocol.tunnel_close(from_id, tid))
            return

        self._tunnels[tid] = {"reader": reader, "writer": writer}
        await self._send(protocol.tunnel_accept(from_id, tid))

        # Relay local TCP back over WebSocket
        asyncio.create_task(self._relay_tcp_to_ws(tid, from_id, reader))
