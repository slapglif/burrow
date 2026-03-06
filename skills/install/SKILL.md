---
name: install
description: "Use when installing burrow as a Claude Code plugin, setting up burrow on a new system, or when the user says 'install burrow', 'set up burrow', 'add burrow plugin'. Handles all registration, symlinking, venv creation, and verification automatically."
---

# Install Burrow Plugin

Fully automated installation of burrow as a Claude Code plugin. Discovers all paths dynamically — no hardcoded assumptions.

## Steps

### 1. Find the burrow source

Locate the burrow repo on this system. Check in order:

```bash
# Check common locations dynamically
BURROW_DIR=""
for candidate in \
    "$(pwd)" \
    "$(dirname "${CLAUDE_PLUGIN_ROOT:-/dev/null}")" \
    "$HOME/.claude/plugins/burrow" \
    "/workspace/burrow" \
    "$(find / -maxdepth 4 -name 'pyproject.toml' -path '*/burrow/*' 2>/dev/null | head -1 | xargs dirname 2>/dev/null)"; do
    if [ -f "$candidate/burrow/protocol.py" ] && [ -f "$candidate/.claude-plugin/plugin.json" ]; then
        BURROW_DIR="$candidate"
        break
    fi
done
```

If not found, clone it:
```bash
git clone https://github.com/slapglif/burrow.git ~/.claude/plugins/burrow
BURROW_DIR="$HOME/.claude/plugins/burrow"
```

### 2. Set up Python environment

```bash
cd "$BURROW_DIR"
uv venv 2>/dev/null || python3 -m venv .venv
uv pip install -e . 2>/dev/null || .venv/bin/pip install -e .
```

Verify the MCP server imports:
```bash
cd "$BURROW_DIR" && .venv/bin/python -c "from burrow.mcp_server import mcp; print('OK')"
```

### 3. Discover Claude Code paths

Dynamically find the Claude config directory:
```bash
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
PLUGINS_DIR="$CLAUDE_DIR/plugins"
SETTINGS="$CLAUDE_DIR/settings.json"
INSTALLED="$PLUGINS_DIR/installed_plugins.json"
```

### 4. Create symlink (if not already in plugins dir)

```bash
if [ "$(readlink -f "$BURROW_DIR")" != "$(readlink -f "$PLUGINS_DIR/burrow" 2>/dev/null)" ]; then
    ln -sfn "$BURROW_DIR" "$PLUGINS_DIR/burrow"
fi
```

### 5. Register in installed_plugins.json

Use Python to add the entry dynamically:
```python
import json, os
from datetime import datetime, timezone

installed_path = os.path.expanduser("~/.claude/plugins/installed_plugins.json")
burrow_dir = os.path.realpath(BURROW_DIR)

with open(installed_path) as f:
    data = json.load(f)

now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

# Get git SHA if available
sha = ""
try:
    import subprocess
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=burrow_dir).decode().strip()
except Exception:
    pass

data.setdefault("plugins", {})["burrow@local"] = [{
    "scope": "user",
    "installPath": burrow_dir,
    "version": "0.2.0",
    "installedAt": now,
    "lastUpdated": now,
    "gitCommitSha": sha
}]

with open(installed_path, "w") as f:
    json.dump(data, f, indent=2)
```

### 6. Enable in settings.json

```python
import json, os

settings_path = os.path.expanduser("~/.claude/settings.json")
with open(settings_path) as f:
    data = json.load(f)

data.setdefault("enabledPlugins", {})["burrow@local"] = True

with open(settings_path, "w") as f:
    json.dump(data, f, indent=2)
```

### 7. Verify MCP server launches

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}' \
  | timeout 5 uv --directory "$BURROW_DIR" run burrow-mcp 2>/dev/null \
  | python3 -c "import json,sys; d=json.loads(sys.stdin.readline()); print('MCP OK:', d['result']['serverInfo']['name'])"
```

### 8. Test registry connectivity

```bash
cd "$BURROW_DIR" && .venv/bin/python -c "
import asyncio
from burrow.peer import Peer
async def test():
    p = Peer('wss://reg.ai-smith.net', 'install-test')
    await p.connect()
    print(f'Registry OK: connected as {p.id}')
    await p.ws.close()
asyncio.run(test())
"
```

### 9. Report result

Print a summary:
- Plugin path and symlink status
- Registration in installed_plugins.json
- Enabled in settings.json
- MCP server launch status
- Registry connectivity
- Instruction to restart Claude Code session to pick up the new plugin

## Known Gotchas

- **`uv` not installed**: Install with `curl -LsSf https://astral.sh/uv/install.sh | sh`
- **Externally managed Python**: Always use `uv venv` + `uv pip install`, never `--system`
- **Symlink already exists**: Use `ln -sfn` (force + no-deref) to overwrite safely
- **installed_plugins.json missing**: Create `{"version": 2, "plugins": {}}` first
- **settings.json missing**: Create `{"enabledPlugins": {}}` first
- **Port 7654 in use**: The registry is remote at `wss://reg.ai-smith.net` — no local port needed for clients
- **MCP server hangs**: The listen task keeps the event loop open — this is normal. Claude Code manages the lifecycle.
- **Plugin not loading after install**: Must start a new Claude Code session — plugins aren't hot-reloaded
