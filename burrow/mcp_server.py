"""Burrow P2P MCP server — exposes peer capabilities as tools for Claude Code agents."""

import asyncio
import socket

from mcp.server.fastmcp import FastMCP

from burrow.peer import Peer
from burrow.server import serve
from burrow.protocol import DEFAULT_PORT

mcp = FastMCP("burrow")

# Singleton peer instance
_peer: Peer | None = None
_listen_task: asyncio.Task | None = None
_server_task: asyncio.Task | None = None


@mcp.tool()
async def burrow_serve(host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> str:
    """Start a burrow registry server in the background."""
    global _server_task
    if _server_task and not _server_task.done():
        return f"Registry server already running on {host}:{port}."
    _server_task = asyncio.create_task(serve(host, port))
    return f"Registry server started on ws://{host}:{port}"


@mcp.tool()
async def burrow_connect(url: str = "ws://localhost:7654", name: str | None = None) -> str:
    """Connect to a burrow registry and register as a peer."""
    global _peer, _listen_task
    if _peer and _peer.ws:
        return f"Already connected as '{_peer.name}' (id={_peer.id}). Disconnect first."
    if name is None:
        name = socket.gethostname()
    _peer = Peer(url, name)
    try:
        await _peer.connect()
    except Exception as exc:
        _peer = None
        return f"Connection failed: {exc}"
    _listen_task = asyncio.create_task(_peer.listen())
    peer_count = len(_peer.peers)
    return f"Connected to {url} as '{_peer.name}' (id={_peer.id}). {peer_count} other peer(s) online."


@mcp.tool()
async def burrow_list_peers() -> str:
    """List all peers currently connected to the registry."""
    if not _peer or not _peer.ws:
        return "Not connected. Call burrow_connect first."
    try:
        await _peer.request_peers()
        await asyncio.sleep(0.3)  # brief wait for the PEERS response
    except Exception as exc:
        return f"Failed to request peers: {exc}"
    if not _peer.peers:
        return "No other peers online."
    lines = [f"  {pid}: {pname}" for pid, pname in _peer.peers.items()]
    return f"{len(lines)} peer(s) online:\n" + "\n".join(lines)


@mcp.tool()
async def burrow_send_message(to: str, body: str) -> str:
    """Send a text message to a peer by name or id."""
    if not _peer or not _peer.ws:
        return "Not connected. Call burrow_connect first."
    try:
        await _peer.send_message(to, body)
    except Exception as exc:
        return f"Failed to send message: {exc}"
    return f"Message sent to '{to}'."


@mcp.tool()
async def burrow_send_file(to: str, filepath: str) -> str:
    """Send a file to a peer by name or id."""
    if not _peer or not _peer.ws:
        return "Not connected. Call burrow_connect first."
    try:
        await _peer.send_file(to, filepath)
    except FileNotFoundError:
        return f"File not found: {filepath}"
    except Exception as exc:
        return f"Failed to send file: {exc}"
    return f"File '{filepath}' sent to '{to}'."


@mcp.tool()
async def burrow_open_tunnel(to: str, local_port: int, remote_port: int) -> str:
    """Open a TCP tunnel: localhost:local_port forwards to peer's localhost:remote_port."""
    if not _peer or not _peer.ws:
        return "Not connected. Call burrow_connect first."
    try:
        await _peer.open_tunnel(to, local_port, remote_port)
    except Exception as exc:
        return f"Failed to open tunnel: {exc}"
    return f"Tunnel open: localhost:{local_port} -> {to}:{remote_port}"


@mcp.tool()
async def burrow_disconnect() -> str:
    """Disconnect from the burrow registry."""
    global _peer, _listen_task
    if not _peer or not _peer.ws:
        return "Not connected."
    name = _peer.name
    try:
        if _listen_task and not _listen_task.done():
            _listen_task.cancel()
        await _peer.ws.close()
    except Exception:
        pass
    _peer = None
    _listen_task = None
    return f"Disconnected '{name}' from registry."


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
