---
name: patterns
description: "Use when coordinating multiple agents across systems, orchestrating distributed tasks, designing swarm workflows, batching file transfers, queuing messages, or setting up heartbeat/polling patterns. Trigger phrases: 'coordinate agents', 'swarm pattern', 'multi-system', 'batch transfer', 'message queue', 'heartbeat', 'spawn agents', 'horizontal scaling', 'distributed workflow', 'agent coordination'."
---

# Burrow Usage Patterns

Patterns for multi-system coordination, agent orchestration, and distributed workflows using the burrow P2P relay.

## Core Concepts

- **Every peer is equal** — any peer can message, file-transfer, or tunnel to any other
- **Registry is the hub** — `wss://reg.ai-smith.net` is always available, all peers auto-discover each other
- **Names are addresses** — peers are addressed by name (case-insensitive) or hex ID
- **Fire and forget** — messages are relayed instantly, no ack required (but you can build ack patterns)

---

## Pattern 1: Spawn + Coordinate Remote Agents

Deploy CLI agents on remote machines (Cloudflare containers, AWS EC2, etc.) and coordinate them through burrow.

### Scenario: Spin up 3 horizontal workers

```bash
# On each remote machine (container, EC2, etc.):
git clone https://github.com/slapglif/burrow.git && cd burrow
bash scripts/install-plugin.sh

# Start a Claude/Gemini CLI instance with burrow:
claude --plugin-dir ./  # or: gemini-cli --plugin ./
```

Each agent auto-connects to `wss://reg.ai-smith.net` via the SessionStart hook.

### Coordinator pattern (from your main machine):

```
1. burrow_connect(name="coordinator")
2. burrow_list_peers()
   → worker-1, worker-2, worker-3

3. burrow_send_message("worker-1", json.dumps({
       "task": "analyze",
       "data": "dataset-chunk-1.csv",
       "report_to": "coordinator"
   }))

4. burrow_send_message("worker-2", json.dumps({
       "task": "analyze",
       "data": "dataset-chunk-2.csv",
       "report_to": "coordinator"
   }))

5. # Workers process and reply:
   # worker-1 → coordinator: {"status": "done", "result": "..."}
```

### Worker bootstrap script:

```bash
#!/bin/bash
# deploy-worker.sh — run on each remote machine
git clone https://github.com/slapglif/burrow.git /opt/burrow
cd /opt/burrow && bash bootstrap.sh

# Launch Claude CLI with burrow + task instructions
claude --plugin-dir /opt/burrow -p "
You are worker-$(hostname). You are connected to the burrow swarm.
Wait for tasks from 'coordinator'. When you receive a task message:
1. Parse the JSON task
2. Execute it
3. Send the result back: burrow_send_message('coordinator', json.dumps(result))
4. Wait for the next task
Poll for messages every 30 seconds by calling burrow_list_peers().
"
```

---

## Pattern 2: Heartbeat / Health Monitoring

Keep track of which agents are alive with periodic polling.

### Monitor pattern:

```
Every 60 seconds:
1. burrow_list_peers()
2. Compare current peer list to expected peers
3. If a peer is missing:
   - Alert (send message to admin peer or log)
   - Optionally trigger re-deployment
4. If a new peer appears:
   - Send welcome/config message
```

### Agent heartbeat (worker side):

```
Every 30 seconds:
1. burrow_send_message("monitor", json.dumps({
       "type": "heartbeat",
       "name": hostname,
       "status": "alive",
       "load": cpu_percent,
       "tasks_completed": count
   }))
```

### Using peer_joined/peer_left events:

The registry broadcasts `peer_joined` and `peer_left` automatically. The Peer client's `listen()` loop handles these — peers appear/disappear from `peer.peers` dict in real-time. No polling needed for presence detection.

---

## Pattern 3: Batched File Transfer

Transfer multiple files to one or more peers efficiently.

### Send a directory of files:

```python
import os
from pathlib import Path

files = list(Path("./output").glob("*.csv"))

for f in files:
    await peer.send_file("data-collector", str(f))
    # Files are chunked at 512KB and base64-encoded
    # No need to wait — the relay handles ordering
```

### Via MCP tools:

```
# Send multiple files sequentially
burrow_send_file("worker-1", "/data/chunk-001.csv")
burrow_send_file("worker-1", "/data/chunk-002.csv")
burrow_send_file("worker-1", "/data/chunk-003.csv")

# Fan-out: same file to all workers
for peer in ["worker-1", "worker-2", "worker-3"]:
    burrow_send_file(peer, "/config/settings.json")
```

### Receive side:

Files land in `./burrow-received/` with their original filename (sanitized for path traversal). The `on_file` callback fires when complete.

---

## Pattern 4: Message Bus / Task Queue

Use burrow messages as a simple task queue.

### Producer (coordinator):

```
tasks = [
    {"id": 1, "action": "scrape", "url": "https://example.com/page1"},
    {"id": 2, "action": "scrape", "url": "https://example.com/page2"},
    {"id": 3, "action": "scrape", "url": "https://example.com/page3"},
]

# Round-robin distribute
workers = ["worker-1", "worker-2", "worker-3"]
for i, task in enumerate(tasks):
    target = workers[i % len(workers)]
    burrow_send_message(target, json.dumps(task))
```

### Consumer (worker):

```
# In the Peer.on_message callback or listen loop:
# Messages arrive as: {"type": "msg", "from_name": "coordinator", "body": "..."}

Parse body as JSON → execute task → send result back:
burrow_send_message("coordinator", json.dumps({
    "task_id": task["id"],
    "status": "complete",
    "result": scraped_data
}))
```

### Acknowledgment pattern:

```
Coordinator sends:  {"task_id": 1, "action": "..."}
Worker replies:     {"task_id": 1, "status": "ack"}     # received
Worker replies:     {"task_id": 1, "status": "done", "result": "..."} # completed
Coordinator tracks: pending_tasks[task_id] = "ack" → "done"
```

---

## Pattern 5: Port Tunnel for Service Access

Give remote agents access to local services (databases, APIs, dashboards).

### Expose a local database to a remote worker:

```
# On coordinator (has PostgreSQL on port 5432):
burrow_open_tunnel("worker-1", 15432, 5432)
# worker-1 can now access coordinator's PostgreSQL at localhost:15432
```

### Expose a remote service locally:

```
# Ask the remote peer to open a tunnel back:
burrow_send_message("worker-1", json.dumps({
    "action": "open_tunnel",
    "local_port": 8080,
    "remote_port": 3000,
    "target": "coordinator"
}))
# worker-1 runs: burrow_open_tunnel("coordinator", 8080, 3000)
# Now coordinator can access worker-1's port 3000 at localhost:8080
```

---

## Pattern 6: Multi-System Build/Deploy Pipeline

Coordinate a deploy across multiple environments.

### Scenario: Deploy to staging + production

```
1. burrow_connect(name="deploy-controller")
2. burrow_list_peers()
   → staging-1, staging-2, prod-1, prod-2

3. # Phase 1: Deploy to staging
   for peer in ["staging-1", "staging-2"]:
       burrow_send_file(peer, "./deploy/app-v2.tar.gz")
       burrow_send_message(peer, json.dumps({
           "action": "deploy",
           "artifact": "app-v2.tar.gz",
           "env": "staging"
       }))

4. # Wait for staging confirmation
   # (Workers reply with {"status": "deployed", "health": "ok"})

5. # Phase 2: Deploy to production
   for peer in ["prod-1", "prod-2"]:
       burrow_send_file(peer, "./deploy/app-v2.tar.gz")
       burrow_send_message(peer, json.dumps({
           "action": "deploy",
           "artifact": "app-v2.tar.gz",
           "env": "production"
       }))
```

---

## Pattern 7: Agent Swarm with Poll/Heartbeat

Full swarm pattern with coordinator, workers, and health monitoring.

### Architecture:

```
                    ┌─────────────┐
                    │ Coordinator │
                    │  (you/CLI)  │
                    └──────┬──────┘
                           │ burrow relay
              ┌────────────┼────────────┐
              v            v            v
        ┌──────────┐ ┌──────────┐ ┌──────────┐
        │ Worker-1 │ │ Worker-2 │ │ Worker-3 │
        │ (CF/AWS) │ │ (CF/AWS) │ │ (CF/AWS) │
        └──────────┘ └──────────┘ └──────────┘
```

### Coordinator instructions:

```
1. Connect: burrow_connect(name="coordinator")
2. Wait for workers to appear (poll burrow_list_peers every 15s)
3. Once all expected workers are online:
   a. Assign tasks via burrow_send_message (JSON)
   b. Monitor heartbeats (workers send status every 30s)
   c. If a worker goes silent for 90s → reassign its tasks
   d. Collect results as workers complete
4. When all tasks done → send "shutdown" to all workers
5. Disconnect: burrow_disconnect()
```

### Worker instructions (pass to Claude/Gemini CLI):

```
You are a burrow swarm worker. On startup:
1. burrow_connect() — auto-joins wss://reg.ai-smith.net
2. Send heartbeat to "coordinator" every 30 seconds:
   burrow_send_message("coordinator", '{"type":"heartbeat","worker":"HOSTNAME","status":"idle"}')
3. When you receive a task message from "coordinator":
   a. Parse the JSON body
   b. Update status: burrow_send_message("coordinator", '{"type":"status","worker":"HOSTNAME","task_id":ID,"status":"running"}')
   c. Execute the task
   d. Send result: burrow_send_message("coordinator", '{"type":"result","worker":"HOSTNAME","task_id":ID,"result":"..."}')
   e. Return to idle, resume heartbeats
4. If you receive {"action":"shutdown"} → burrow_disconnect() and exit
```

### Spawning workers on Cloudflare:

```bash
# Using wrangler to deploy containers
for i in 1 2 3; do
    wrangler containers deploy \
        --name "worker-$i" \
        --image "python:3.12-slim" \
        --command "bash -c 'pip install uv && git clone https://github.com/slapglif/burrow.git /app && cd /app && bash bootstrap.sh && claude --plugin-dir /app -p \"You are worker-$i. Follow the worker instructions...\"'"
done
```

### Spawning workers on AWS:

```bash
for i in 1 2 3; do
    aws ec2 run-instances \
        --image-id ami-xxx \
        --instance-type t3.micro \
        --user-data "#!/bin/bash
curl -fsSL https://raw.githubusercontent.com/slapglif/burrow/master/bootstrap.sh | bash
cd /root/burrow
claude --plugin-dir . -p 'You are worker-$i...'"
done
```

---

## Anti-Patterns (What NOT to Do)

- **Don't poll for messages** — the listen loop handles incoming messages automatically via WebSocket events. You only need to poll `burrow_list_peers()` for presence changes.
- **Don't open tunnels for messaging** — tunnels are for TCP services (databases, APIs). Use `burrow_send_message` for communication.
- **Don't send huge files without chunking** — burrow auto-chunks at 512KB, but be aware of base64 overhead (33% size increase over the wire).
- **Don't hardcode peer IDs** — IDs change on reconnect. Always use peer names.
- **Don't start a local registry when using the public one** — `wss://reg.ai-smith.net` is always available. Only start local registries for air-gapped networks.
