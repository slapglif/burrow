---
name: swarm-status
description: "Use when the user asks about swarm status, connected peers, active tunnels, or burrow network health. Trigger phrases: 'swarm status', 'who is online', 'show peers', 'network status', 'burrow status'."
---

# Swarm Status

Show the current state of the burrow P2P swarm connection.

## Steps

1. Call `burrow_list_peers` to get the current peer list.

2. Present a clear status summary:
   - Connection status (connected/disconnected)
   - Registry: `wss://reg.ai-smith.net`
   - Your peer name and ID
   - Online peers (name and ID for each)
   - Peer count

3. If disconnected, call `burrow_connect()` to auto-join the public registry at `wss://reg.ai-smith.net`, then list peers again.

4. Format output as a clean, readable summary — not raw JSON.
