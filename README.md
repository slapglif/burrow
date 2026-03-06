# burrow

> Zero-config P2P networking: discovery, messaging, file transfer, tunneling

## Features

- Peer discovery via central registry with automatic join/leave notifications
- P2P text messaging through relay
- Chunked file transfer with base64 encoding (512 KB chunks)
- TCP port tunneling through WebSocket relay
- WebSocket + JSON protocol (firewall-friendly)
- Cross-platform binaries (Linux, macOS, Windows)
- Python 3.11+, single dependency (`websockets`)

## Quick Start

### From binary (easiest)

Download from [Releases](https://github.com/slapglif/burrow/releases).

### From source

```bash
uv pip install git+https://github.com/slapglif/burrow.git
```

## Usage

### Start a registry

```bash
burrow serve --port 7654
```

The registry listens on `0.0.0.0:7654` by default.

### Connect as a peer

```bash
burrow connect ws://registry-host:7654 --name my-laptop
```

If `--name` is omitted, the system hostname is used.

### Interactive commands

```
/peers                          List connected peers
/msg <peer> <message>           Send a text message
/send <peer> <filepath>         Send a file
/tunnel <peer> <lport>:<rport>  Forward a TCP port
/help                           Show help
/quit                           Disconnect
```

Peers can be referenced by name (case-insensitive) or by ID.

## Protocol

All messages are JSON objects sent over a WebSocket connection. Every message contains a `type` field.

| Type             | Direction        | Description                              |
|------------------|------------------|------------------------------------------|
| `register`       | peer -> registry | Register with a display name             |
| `registered`     | registry -> peer | Confirm registration, assign peer ID     |
| `peers`          | both             | Request/response: list connected peers   |
| `peer_joined`    | registry -> peer | Notification: a new peer connected       |
| `peer_left`      | registry -> peer | Notification: a peer disconnected        |
| `msg`            | peer -> peer     | Text message (relayed through registry)  |
| `file_start`     | peer -> peer     | Begin a file transfer (name, size, ID)   |
| `file_chunk`     | peer -> peer     | Base64-encoded file chunk (512 KB)       |
| `tunnel_open`    | peer -> peer     | Request to open a TCP tunnel             |
| `tunnel_accept`  | peer -> peer     | Accept a tunnel request                  |
| `tunnel_data`    | peer -> peer     | Relay TCP data through the tunnel        |
| `tunnel_close`   | peer -> peer     | Close an active tunnel                   |
| `ping`           | either           | Keepalive ping                           |
| `pong`           | either           | Keepalive pong                           |
| `error`          | registry -> peer | Error notification                       |

Protocol version: `0.1.0`

## Architecture

```
┌──────────┐     WebSocket     ┌──────────┐     WebSocket     ┌──────────┐
│  Peer A  │ <===============> │ Registry │ <===============> │  Peer B  │
└──────────┘                   └──────────┘                   └──────────┘
```

All traffic flows through the registry relay. No direct peer connections are needed. This means burrow works through NAT and firewalls without any port forwarding.

## Development

```bash
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
pytest tests/ -v
```

### Building standalone binaries

```bash
uv pip install -e ".[build]"
pyinstaller --onefile --name burrow burrow/cli.py
```

## License

MIT
