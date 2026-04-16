"""burrow CLI — interactive P2P networking client."""

import argparse
import asyncio
import json
import socket

from burrow.computer_use import normalize_action
from burrow.protocol import DEFAULT_PORT


def _print_json(payload):
    print(json.dumps(payload, indent=2, sort_keys=True))


async def _ainput(prompt: str) -> str:
    """Async stdin input using executor (no extra deps)."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: input(prompt))


async def _desktop_helper(peer, target: str, args: list[str], timeout: float = 60.0):
    return await peer._run_desktop_script(target, args, timeout=timeout)


def _desktop_target_from_args(display: str = "", target_json: str = ""):
    if target_json:
        payload = json.loads(target_json)
        if not isinstance(payload, dict):
            raise ValueError("target_json must decode to an object")
        return payload
    if display:
        return {"kind": "display", "id": display, "title": display}
    return None


def _clipboard_action_json(action: str, text: str = "") -> str:
    payload = {
        "copy": {"type": "clipboard_copy"},
        "cut": {"type": "clipboard_cut"},
        "paste": {"type": "clipboard_paste"},
        "select_all": {"type": "select_all"},
        "paste_text": {"type": "clipboard_paste_text", "text": text},
    }.get(action)
    if payload is None:
        raise ValueError("clipboard action must be one of: copy, cut, paste, paste_text, select_all")
    if action == "paste_text" and text == "":
        raise ValueError("clipboard paste_text requires non-empty text")
    return json.dumps(normalize_action(payload), sort_keys=True)


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

        elif line.startswith("/desktop-cap "):
            parts = line.split()
            if len(parts) != 2:
                print("usage: /desktop-cap <peer>")
                continue
            info = await peer.get_desktop_capabilities(parts[1])
            _print_json(info)

        elif line.startswith("/desktop-list "):
            parts = line.split()
            if len(parts) != 2:
                print("usage: /desktop-list <peer>")
                continue
            result = await _desktop_helper(peer, parts[1], ["list-sessions"])
            _print_json(result)

        elif line.startswith("/desktop-snap "):
            parts = line.split()
            if len(parts) != 3:
                print("usage: /desktop-snap <peer> <session_id>")
                continue
            result = await _desktop_helper(peer, parts[1], ["snapshot", "--session-id", parts[2]])
            _print_json(result)

        elif line.startswith("/desktop-click "):
            parts = line.split()
            if len(parts) != 5:
                print("usage: /desktop-click <peer> <session_id> <x> <y>")
                continue
            action = json.dumps({"type": "click", "x": int(parts[3]), "y": int(parts[4])})
            result = await _desktop_helper(peer, parts[1], ["input", "--session-id", parts[2], "--action-json", action])
            _print_json(result)

        elif line.startswith("/desktop-type "):
            parts = line.split(None, 3)
            if len(parts) != 4:
                print("usage: /desktop-type <peer> <session_id> <text>")
                continue
            action = json.dumps({"type": "type_text", "text": parts[3]})
            result = await _desktop_helper(peer, parts[1], ["input", "--session-id", parts[2], "--action-json", action])
            _print_json(result)

        elif line.startswith("/desktop-key "):
            parts = line.split(None, 3)
            if len(parts) != 4:
                print("usage: /desktop-key <peer> <session_id> <key>")
                continue
            action = json.dumps({"type": "key", "key": parts[3]})
            result = await _desktop_helper(peer, parts[1], ["input", "--session-id", parts[2], "--action-json", action])
            _print_json(result)

        elif line.startswith("/desktop-clip "):
            parts = line.split(None, 4)
            if len(parts) < 4:
                print("usage: /desktop-clip <peer> <session_id> <copy|cut|paste|paste_text|select_all> [text]")
                continue
            try:
                action = _clipboard_action_json(parts[3], parts[4] if len(parts) == 5 else "")
            except ValueError as exc:
                print(exc)
                continue
            result = await _desktop_helper(peer, parts[1], ["input", "--session-id", parts[2], "--action-json", action])
            _print_json(result)

        elif line.startswith("/desktop-close ") or line.startswith("/desktop-stop "):
            parts = line.split()
            if len(parts) != 3:
                print("usage: /desktop-close <peer> <session_id>")
                continue
            result = await peer.stop_desktop_session(parts[1], parts[2])
            _print_json(result)

        elif line.startswith("/desktop-open ") or line.startswith("/desktop "):
            parts = line.split()
            if len(parts) not in (2, 3, 4):
                print("usage: /desktop-open <peer> [backend] [display]")
                continue
            backend = parts[2] if len(parts) >= 3 else "auto"
            display = parts[3] if len(parts) >= 4 else None
            session = await peer.open_desktop_session(
                parts[1],
                backend=backend,
                display=display,
                target=_desktop_target_from_args(display or "", ""),
            )
            _print_json(session)

        elif line in ("/quit", "/q", "/exit"):
            print("bye.")
            break

        elif line == "/help":
            print("  /peers                          — list connected peers")
            print("  /msg <peer> <text>              — send message")
            print("  /send <peer> <filepath>         — send file")
            print("  /tunnel <peer> <lport>:<rport>  — forward port")
            print("  /desktop-cap <peer>             — inspect remote desktop backends")
            print("  /desktop-open <peer> [backend]  — start tunneled remote desktop")
            print("  /desktop-list <peer>            — list remote desktop sessions")
            print("  /desktop-snap <peer> <sid>      — capture snapshot")
            print("  /desktop-click <peer> <sid> x y — send click")
            print("  /desktop-type <peer> <sid> text — type text")
            print("  /desktop-key <peer> <sid> key   — send key")
            print("  /desktop-clip <peer> <sid> act  — thin clipboard action (copy/cut/paste/paste_text/select_all)")
            print("  /desktop-close <peer> <sid>     — stop remote desktop session")
            print("  /quit                           — disconnect")

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


async def _run_once(args):
    from burrow.peer import Peer

    name = getattr(args, "name", None) or socket.gethostname()
    peer = Peer(args.url, name)
    await peer.connect()
    listener = asyncio.create_task(peer.run())
    await asyncio.sleep(0.05)
    try:
        if args.command == "desktop-capabilities":
            _print_json(await peer.get_desktop_capabilities(args.peer))
        elif args.command == "desktop-open":
            display = args.display or None
            _print_json(await peer.open_desktop_session(
                args.peer,
                backend=args.backend,
                local_port=args.local_port or None,
                remote_port=args.remote_port,
                readonly=args.readonly,
                display=display,
                target=_desktop_target_from_args(args.display, args.target_json),
            ))
        elif args.command == "desktop-list":
            _print_json(await _desktop_helper(peer, args.peer, ["list-sessions"]))
        elif args.command == "desktop-snapshot":
            _print_json(await _desktop_helper(peer, args.peer, ["snapshot", "--session-id", args.session_id]))
        elif args.command == "desktop-input":
            _print_json(await _desktop_helper(
                peer,
                args.peer,
                ["input", "--session-id", args.session_id, "--action-json", args.action_json],
            ))
        elif args.command == "desktop-clipboard":
            _print_json(await _desktop_helper(
                peer,
                args.peer,
                ["input", "--session-id", args.session_id, "--action-json", _clipboard_action_json(args.action, args.text)],
            ))
        elif args.command == "desktop-close":
            _print_json(await peer.stop_desktop_session(args.peer, args.session_id))
    finally:
        listener.cancel()
        if peer.ws:
            await peer.ws.close()


def main():
    parser = argparse.ArgumentParser(prog="burrow", description="Zero-config P2P networking")
    sub = parser.add_subparsers(dest="command")

    srv = sub.add_parser("serve", help="Start registry server")
    srv.add_argument("--host", default="0.0.0.0")
    srv.add_argument("--port", type=int, default=DEFAULT_PORT)

    conn = sub.add_parser("connect", help="Connect to registry")
    conn.add_argument("url", nargs="?", default="wss://reg.ai-smith.net",
                      help="Registry WebSocket URL (default: wss://reg.ai-smith.net)")
    conn.add_argument("--name", "-n", default=None,
                      help="Peer name (default: hostname)")

    for command_name, help_text in [
        ("desktop-capabilities", "Inspect remote desktop helper capabilities"),
        ("desktop-open", "Open a remote desktop session"),
        ("desktop-list", "List remote desktop sessions"),
        ("desktop-snapshot", "Capture a remote desktop snapshot"),
        ("desktop-input", "Send normalized input JSON to a desktop session"),
        ("desktop-clipboard", "Send a thin clipboard-oriented action to a desktop session"),
        ("desktop-close", "Close a remote desktop session"),
    ]:
        cmd = sub.add_parser(command_name, help=help_text)
        cmd.add_argument("peer")
        cmd.add_argument("--url", default="wss://reg.ai-smith.net")
        cmd.add_argument("--name", "-n", default=None)
        if command_name == "desktop-open":
            cmd.add_argument("--backend", default="auto")
            cmd.add_argument("--local-port", type=int, default=0)
            cmd.add_argument("--remote-port", type=int, default=0)
            cmd.add_argument("--readonly", action="store_true")
            cmd.add_argument("--display", default="")
            cmd.add_argument("--target-json", default="")
        elif command_name in {"desktop-snapshot", "desktop-close"}:
            cmd.add_argument("session_id")
        elif command_name == "desktop-input":
            cmd.add_argument("session_id")
            cmd.add_argument("action_json")
        elif command_name == "desktop-clipboard":
            cmd.add_argument("session_id")
            cmd.add_argument("action", choices=["copy", "cut", "paste", "paste_text", "select_all"])
            cmd.add_argument("--text", default="")

    args = parser.parse_args()

    if args.command == "serve":
        from burrow.server import serve
        asyncio.run(serve(args.host, args.port))
    elif args.command == "connect":
        name = args.name or socket.gethostname()
        try:
            asyncio.run(client_main(args.url, name))
        except KeyboardInterrupt:
            pass
    elif args.command in {
        "desktop-capabilities",
        "desktop-open",
        "desktop-list",
        "desktop-snapshot",
        "desktop-input",
        "desktop-clipboard",
        "desktop-close",
    }:
        try:
            asyncio.run(_run_once(args))
        except KeyboardInterrupt:
            pass
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
