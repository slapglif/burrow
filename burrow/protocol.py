"""Burrow P2P protocol — message types, builders, and constants."""

# Protocol version
VERSION = "0.2.0"

# Network defaults
DEFAULT_PORT = 7654
CHUNK_SIZE = 524288  # 512 KB

# --- Message types ---
REGISTER = "register"
REGISTERED = "registered"
PEERS = "peers"
PEER_JOINED = "peer_joined"
PEER_LEFT = "peer_left"
MSG = "msg"
FILE_START = "file_start"
FILE_CHUNK = "file_chunk"
TUNNEL_OPEN = "tunnel_open"
TUNNEL_ACCEPT = "tunnel_accept"
TUNNEL_DATA = "tunnel_data"
TUNNEL_CLOSE = "tunnel_close"
PING = "ping"
PONG = "pong"
ERROR = "error"


# --- Builder functions (return dicts; caller serialises with json.dumps) ---

def register(name: str) -> dict:
    return {"type": REGISTER, "name": name}


def peers() -> dict:
    return {"type": PEERS}


def msg(to: str, body: str) -> dict:
    return {"type": MSG, "to": to, "body": body}


def file_start(to: str, name: str, size: int, transfer_id: str) -> dict:
    return {"type": FILE_START, "to": to, "name": name, "size": size,
            "transfer_id": transfer_id}


def file_chunk(to: str, transfer_id: str, seq: int, data: str,
               final: bool) -> dict:
    return {"type": FILE_CHUNK, "to": to, "transfer_id": transfer_id,
            "seq": seq, "data": data, "final": final}


def tunnel_open(to: str, tunnel_id: str, remote_port: int) -> dict:
    return {"type": TUNNEL_OPEN, "to": to, "tunnel_id": tunnel_id,
            "remote_port": remote_port}


def tunnel_accept(to: str, tunnel_id: str) -> dict:
    return {"type": TUNNEL_ACCEPT, "to": to, "tunnel_id": tunnel_id}


def tunnel_data(to: str, tunnel_id: str, data: str) -> dict:
    return {"type": TUNNEL_DATA, "to": to, "tunnel_id": tunnel_id,
            "data": data}


def tunnel_close(to: str, tunnel_id: str) -> dict:
    return {"type": TUNNEL_CLOSE, "to": to, "tunnel_id": tunnel_id}


def error(message: str) -> dict:
    return {"type": ERROR, "message": message}


def ping() -> dict:
    return {"type": PING}


def pong() -> dict:
    return {"type": PONG}
