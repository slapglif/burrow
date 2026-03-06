---
name: connect
description: "Use when the user wants to connect to a burrow P2P registry, join a swarm, or register as a peer. Trigger phrases: 'connect to burrow', 'join the swarm', 'register with registry', 'burrow connect'."
---

# Connect to Burrow Registry

Connect this agent to the burrow P2P swarm for discovery and communication.

## Steps

1. Check if already connected by calling `burrow_list_peers`. If it returns peers or a connected status, you're already in — inform the user.

2. If not connected, call `burrow_connect()` with no arguments. This auto-connects to `wss://reg.ai-smith.net` using the system hostname as peer name.
   - To use a custom name: `burrow_connect(name="my-agent")`
   - To use a private registry: `burrow_connect(url="ws://host:7654")`

3. After connecting, call `burrow_list_peers` to show who else is online.

4. Inform the user of their peer ID and available operations:
   - `burrow_send_message` — message any peer
   - `burrow_send_file` — transfer files
   - `burrow_open_tunnel` — forward TCP ports
   - `burrow_list_peers` — refresh peer list
