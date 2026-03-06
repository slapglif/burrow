---
name: doctor
description: "Use when diagnosing burrow plugin issues, troubleshooting connection failures, checking plugin health, or when the user says 'burrow doctor', 'fix burrow', 'burrow not working', 'debug burrow', 'check burrow'. Runs all diagnostic checks and fixes problems automatically."
---

# Burrow Doctor

Diagnose and fix all known issues with the burrow plugin installation and connectivity.

## Diagnostic Checklist

Run ALL checks in order. Fix each issue before proceeding to the next.

### 1. Python Environment

```bash
# Check Python version (need 3.11+)
python3 --version  # Must be >= 3.11

# Check uv is available
which uv || echo "PROBLEM: uv not installed — run: curl -LsSf https://astral.sh/uv/install.sh | sh"
```

**Fix**: If Python < 3.11, install via `uv python install 3.11`

### 2. Burrow Source Location

```bash
# Find burrow installation
BURROW_DIR=""
for dir in \
    "${CLAUDE_PLUGIN_ROOT:-}" \
    "$HOME/.claude/plugins/burrow" \
    "/workspace/burrow"; do
    [ -f "$dir/burrow/protocol.py" ] && BURROW_DIR="$dir" && break
done

[ -z "$BURROW_DIR" ] && echo "PROBLEM: burrow source not found"
```

**Fix**: `git clone https://github.com/slapglif/burrow.git ~/.claude/plugins/burrow`

### 3. Virtual Environment

```bash
# Check venv exists and has dependencies
ls "$BURROW_DIR/.venv/bin/python" 2>/dev/null || echo "PROBLEM: no venv"
"$BURROW_DIR/.venv/bin/python" -c "import websockets; import mcp" 2>/dev/null || echo "PROBLEM: missing deps"
```

**Fix**: `cd "$BURROW_DIR" && uv venv && uv pip install -e .`

**Gotcha**: Never use `uv pip install --system` — externally managed Python will reject it. Always create a venv first.

### 4. MCP Server Import

```bash
cd "$BURROW_DIR" && .venv/bin/python -c "
from burrow.mcp_server import mcp
tools = list(mcp._tool_manager._tools.keys())
assert len(tools) == 7, f'Expected 7 tools, got {len(tools)}: {tools}'
print(f'OK: {len(tools)} tools registered')
" 2>&1
```

**Expected**: `OK: 7 tools registered`

**Fix if import fails**:
- `ModuleNotFoundError: mcp` → `uv pip install "mcp>=1.0"`
- `ModuleNotFoundError: burrow` → `uv pip install -e .` (editable install)

### 5. Plugin Structure

```bash
# All required files must exist
for f in \
    ".claude-plugin/plugin.json" \
    ".mcp.json" \
    "burrow/mcp_server.py" \
    "hooks/hooks.json" \
    "skills/connect/SKILL.md" \
    "skills/swarm-status/SKILL.md" \
    "skills/install/SKILL.md" \
    "skills/doctor/SKILL.md" \
    "agents/burrow-agent.md"; do
    [ -f "$BURROW_DIR/$f" ] || echo "MISSING: $f"
done

# Validate JSON files
python3 -c "import json; json.load(open('$BURROW_DIR/.claude-plugin/plugin.json'))" || echo "INVALID: plugin.json"
python3 -c "import json; json.load(open('$BURROW_DIR/.mcp.json'))" || echo "INVALID: .mcp.json"
python3 -c "import json; json.load(open('$BURROW_DIR/hooks/hooks.json'))" || echo "INVALID: hooks.json"
```

### 6. Claude Code Registration

```bash
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"

# Check symlink
ls -la "$CLAUDE_DIR/plugins/burrow" 2>/dev/null || echo "PROBLEM: no symlink in plugins dir"

# Check installed_plugins.json
python3 -c "
import json
with open('$CLAUDE_DIR/plugins/installed_plugins.json') as f:
    data = json.load(f)
if 'burrow@local' in data.get('plugins', {}):
    entry = data['plugins']['burrow@local'][0]
    print(f'Registered: {entry[\"installPath\"]}')
else:
    print('PROBLEM: burrow@local not in installed_plugins.json')
" 2>&1

# Check settings.json
python3 -c "
import json
with open('$CLAUDE_DIR/settings.json') as f:
    data = json.load(f)
enabled = data.get('enabledPlugins', {}).get('burrow@local')
print(f'Enabled: {enabled}')
if not enabled:
    print('PROBLEM: burrow@local not enabled in settings.json')
" 2>&1
```

**Fix**: Run the `install` skill — it handles all registration dynamically.

### 7. MCP Server Launch

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"doctor","version":"1.0"}}}' \
  | timeout 5 uv --directory "$BURROW_DIR" run burrow-mcp 2>/dev/null \
  | python3 -c "
import json,sys
line = sys.stdin.readline()
if line:
    d = json.loads(line)
    print(f'MCP server: {d[\"result\"][\"serverInfo\"][\"name\"]} — OK')
else:
    print('PROBLEM: MCP server produced no output')
"
```

**Gotcha**: The MCP server uses stdio transport. It reads JSON-RPC from stdin and writes to stdout. It will appear to "hang" if you run it without input — this is normal.

### 8. Registry Connectivity

```bash
cd "$BURROW_DIR" && timeout 10 .venv/bin/python -c "
import asyncio
from burrow.peer import Peer

async def test():
    p = Peer('wss://reg.ai-smith.net', 'doctor-check')
    try:
        await p.connect()
        print(f'Registry: ONLINE (id={p.id})')
        await p.ws.close()
    except Exception as e:
        print(f'Registry: OFFLINE — {e}')

asyncio.run(test())
" 2>&1
```

**If registry is offline**:
- Check if you can reach `reg.ai-smith.net` at all: `curl -s -o /dev/null -w '%{http_code}' https://reg.ai-smith.net`
- If behind a corporate proxy, set `https_proxy` env var
- As fallback, start a local registry: `burrow serve` then connect to `ws://localhost:7654`

### 9. Hooks Validation

```bash
python3 -c "
import json
with open('$BURROW_DIR/hooks/hooks.json') as f:
    hooks = json.load(f)

# Check SessionStart hook exists
ss = hooks.get('SessionStart', [])
if ss and ss[0].get('hooks', [{}])[0].get('type') == 'prompt':
    print('SessionStart hook: OK (auto-connect)')
else:
    print('PROBLEM: SessionStart hook missing or misconfigured')

# Check PreToolUse hook
ptu = hooks.get('PreToolUse', [])
tunnel_hook = [h for h in ptu if h.get('matcher') == 'burrow_open_tunnel']
if tunnel_hook:
    print('Tunnel safety hook: OK')
else:
    print('PROBLEM: tunnel safety hook missing')
"
```

### 10. Version Consistency

```bash
cd "$BURROW_DIR" && python3 -c "
import json

# Check all version sources match
versions = {}

# pyproject.toml
with open('pyproject.toml') as f:
    for line in f:
        if line.startswith('version'):
            versions['pyproject.toml'] = line.split('\"')[1]

# __init__.py
with open('burrow/__init__.py') as f:
    for line in f:
        if '__version__' in line:
            versions['__init__.py'] = line.split('\"')[1]

# protocol.py
with open('burrow/protocol.py') as f:
    for line in f:
        if 'VERSION' in line and '=' in line:
            versions['protocol.py'] = line.split('\"')[1]

# plugin.json
with open('.claude-plugin/plugin.json') as f:
    versions['plugin.json'] = json.load(f)['version']

unique = set(versions.values())
if len(unique) == 1:
    print(f'Versions: all {unique.pop()} — OK')
else:
    for k, v in versions.items():
        print(f'  {k}: {v}')
    print('PROBLEM: version mismatch — run version-bump workflow')
"
```

## Summary Output

After all checks, print:

```
Burrow Doctor Report
━━━━━━━━━━━━━━━━━━━
✓ Python 3.11+
✓ uv installed
✓ Source found at <path>
✓ Venv + dependencies
✓ MCP server (7 tools)
✓ Plugin structure (all files present)
✓ Claude Code registration
✓ MCP handshake
✓ Registry connectivity (wss://reg.ai-smith.net)
✓ Hooks configured
✓ Versions consistent (0.2.0)

Status: HEALTHY
```

For any failures, show the fix command and offer to run it automatically.
