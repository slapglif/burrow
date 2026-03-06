---
name: connect
description: "Use when the user wants to connect to a burrow P2P registry, join a swarm, or register as a peer. Trigger phrases: 'connect to burrow', 'join the swarm', 'register with registry', 'burrow connect'."
---

# Connect to Burrow Registry

Connect this agent to a burrow P2P relay registry for swarm communication.

## Steps

1. Check if already connected by calling `burrow_list_peers`. If it returns peers, you're already connected — inform the user.

2. If not connected, determine the registry URL:
   - Ask the user for the registry URL if not provided
   - Default: `ws://localhost:7654`
   - Common pattern: `ws://<host>:7654`

3. Determine the peer name:
   - Use the system hostname by default
   - Let the user override with a custom name

4. Call `burrow_connect` with the URL and name.

5. After connecting, call `burrow_list_peers` to show who else is online.

6. Inform the user of their peer ID and available commands:
   - Send messages: `burrow_send_message`
   - Transfer files: `burrow_send_file`
   - Open tunnels: `burrow_open_tunnel`
   - List peers: `burrow_list_peers`
