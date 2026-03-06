"""burrow CLI — interactive P2P networking client."""

import argparse
import asyncio
import sys
from burrow.protocol import DEFAULT_PORT


async def _ainput(prompt: str) -> str:
    """Async stdin input using executor (no extra deps)."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: input(prompt))


async def interactive(peer):
    """Read commands from stdin and dispatch."""
    while True:
        try:
            line = await _ainput("burrow> ")
        except (EOFError, KeyboardInterrupt):
            print("\nbye.")
            break

        line = line.strip()
        if not line:
            continue

        if line == "/peers":
            await peer.request_peers()
            await asyncio.sleep(0.3)
            if peer.peers:
                for pid, name in peer.peers.items():
                    print(f"  {name} ({pid})")
            else:
                print("  no peers connected")

        elif line.startswith("/msg "):
            parts = line.split(None, 2)
            if len(parts) < 3:
                print("usage: /msg <peer> <message>")
                continue
            await peer.send_message(parts[1], parts[2])

        elif line.startswith("/send "):
            parts = line.split(None, 2)
            if len(parts) < 3:
                print("usage: /send <peer> <filepath>")
                continue
            await peer.send_file(parts[1], parts[2])

        elif line.startswith("/tunnel "):
            parts = line.split()
            if len(parts) != 3 or ":" not in parts[2]:
                print("usage: /tunnel <peer> <local_port>:<remote_port>")
                continue
            target = parts[1]
            local_p, remote_p = parts[2].split(":", 1)
            await peer.open_tunnel(target, int(local_p), int(remote_p))

        elif line in ("/quit", "/q", "/exit"):
            print("bye.")
            break

        elif line == "/help":
            print("  /peers                       — list connected peers")
            print("  /msg <peer> <text>           — send message")
            print("  /send <peer> <filepath>      — send file")
            print("  /tunnel <peer> <lport>:<rport> — forward port")
            print("  /quit                        — disconnect")

        else:
            print(f"  unknown command: {line} (try /help)")


async def client_main(uri: str, name: str):
    from burrow.peer import Peer

    peer = Peer(uri, name)
    await peer.connect()
    print(f"connected as {peer.name} ({peer.id})")
    print("type /help for commands\n")

    listener = asyncio.create_task(peer.listen())
    try:
        await interactive(peer)
    finally:
        listener.cancel()
        if peer.ws:
            await peer.ws.close()


def main():
    parser = argparse.ArgumentParser(prog="burrow", description="Zero-config P2P networking")
    sub = parser.add_subparsers(dest="command")

    # burrow serve
    srv = sub.add_parser("serve", help="Start registry server")
    srv.add_argument("--host", default="0.0.0.0")
    srv.add_argument("--port", type=int, default=DEFAULT_PORT)

    # burrow connect
    conn = sub.add_parser("connect", help="Connect to registry")
    conn.add_argument("url", nargs="?", default=f"ws://localhost:{DEFAULT_PORT}",
                       help="Registry WebSocket URL")
    conn.add_argument("--name", "-n", default=None,
                       help="Peer name (default: hostname)")

    args = parser.parse_args()

    if args.command == "serve":
        from burrow.server import serve
        asyncio.run(serve(args.host, args.port))

    elif args.command == "connect":
        import socket
        name = args.name or socket.gethostname()
        try:
            asyncio.run(client_main(args.url, name))
        except KeyboardInterrupt:
            pass

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
