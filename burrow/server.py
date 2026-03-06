"""Burrow registry + relay server."""

import argparse, asyncio, json, os
import websockets

from burrow.protocol import (
    DEFAULT_PORT, REGISTER, REGISTERED, PEERS, PEER_JOINED, PEER_LEFT,
    MSG, FILE_START, FILE_CHUNK, PING, PONG, ERROR,
    TUNNEL_OPEN, TUNNEL_ACCEPT, TUNNEL_DATA, TUNNEL_CLOSE,
)

peers: dict = {}          # ws -> {"id": str, "name": str}
by_id: dict[str, object] = {}  # id -> ws

RELAY_TYPES = {MSG, FILE_START, FILE_CHUNK,
               TUNNEL_OPEN, TUNNEL_ACCEPT, TUNNEL_DATA, TUNNEL_CLOSE}


async def broadcast(msg: dict, exclude=None):
    raw = json.dumps(msg)
    for ws in list(peers):
        if ws is not exclude:
            try:
                await ws.send(raw)
            except Exception:
                pass


def resolve(target: str):
    """Resolve a peer by exact ID first, then case-insensitive name."""
    if target in by_id:
        return by_id[target]
    low = target.lower()
    for ws, info in peers.items():
        if info["name"].lower() == low:
            return ws
    return None


async def handler(ws):
    peer_id = os.urandom(4).hex()
    name = None
    try:
        async for raw in ws:
            msg = json.loads(raw)
            t = msg.get("type")

            match t:
                case "register":
                    name = msg.get("name", peer_id)
                    peers[ws] = {"id": peer_id, "name": name}
                    by_id[peer_id] = ws
                    await ws.send(json.dumps({"type": REGISTERED, "id": peer_id, "name": name}))
                    await broadcast({"type": PEER_JOINED, "id": peer_id, "name": name}, exclude=ws)
                    print(f"+ {name} ({peer_id})")

                case "peers":
                    others = [v for w, v in peers.items() if w is not ws]
                    await ws.send(json.dumps({"type": PEERS, "peers": others}))

                case "ping":
                    await ws.send(json.dumps({"type": PONG}))

                case _ if "to" in msg and t in RELAY_TYPES:
                    target_ws = resolve(msg["to"])
                    if target_ws is None:
                        await ws.send(json.dumps({"type": ERROR, "message": f"peer not found: {msg['to']}"}))
                        continue
                    sender = peers.get(ws, {})
                    msg["from"] = sender.get("id", peer_id)
                    msg["from_name"] = sender.get("name", peer_id)
                    await target_ws.send(json.dumps(msg))

                case _:
                    await ws.send(json.dumps({"type": ERROR, "message": f"unknown type: {t}"}))
    except websockets.ConnectionClosed:
        pass
    finally:
        if ws in peers:
            info = peers.pop(ws)
            by_id.pop(info["id"], None)
            await broadcast({"type": PEER_LEFT, "id": info["id"], "name": info["name"]})
            print(f"- {info['name']} ({info['id']})")


async def serve(host="0.0.0.0", port=DEFAULT_PORT):
    print(f"burrow registry \u00b7 ws://{host}:{port}")
    async with websockets.serve(handler, host, port):
        await asyncio.Future()


def run():
    ap = argparse.ArgumentParser(description="Burrow registry server")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args()
    asyncio.run(serve(args.host, args.port))


if __name__ == "__main__":
    run()
