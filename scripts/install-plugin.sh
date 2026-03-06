#!/usr/bin/env bash
set -euo pipefail

# Burrow Plugin Auto-Installer
# Discovers paths dynamically, creates venv, registers with Claude Code
# Usage: bash scripts/install-plugin.sh [--uninstall]

# --- Colors ---
if [ -t 1 ]; then
    G='\033[0;32m' R='\033[0;31m' Y='\033[0;33m' B='\033[0;34m' N='\033[0m'
else
    G='' R='' Y='' B='' N=''
fi
ok() { echo -e "${G}✓${N} $1"; }
fail() { echo -e "${R}✗${N} $1"; }
warn() { echo -e "${Y}!${N} $1"; }
info() { echo -e "${B}→${N} $1"; }

# --- Find burrow source ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BURROW_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ ! -f "$BURROW_DIR/burrow/protocol.py" ] || [ ! -f "$BURROW_DIR/.claude-plugin/plugin.json" ]; then
    fail "Not a burrow repo at $BURROW_DIR"
    exit 1
fi
ok "Source: $BURROW_DIR"

# --- Discover Claude Code paths ---
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
PLUGINS_DIR="$CLAUDE_DIR/plugins"
SETTINGS="$CLAUDE_DIR/settings.json"
INSTALLED="$PLUGINS_DIR/installed_plugins.json"

# --- Uninstall mode ---
if [ "${1:-}" = "--uninstall" ]; then
    info "Uninstalling burrow plugin..."
    rm -f "$PLUGINS_DIR/burrow"
    python3 -c "
import json, os
p = '$INSTALLED'
if os.path.exists(p):
    with open(p) as f: data = json.load(f)
    data.get('plugins', {}).pop('burrow@local', None)
    with open(p, 'w') as f: json.dump(data, f, indent=2)
s = '$SETTINGS'
if os.path.exists(s):
    with open(s) as f: data = json.load(f)
    data.get('enabledPlugins', {}).pop('burrow@local', None)
    with open(s, 'w') as f: json.dump(data, f, indent=2)
" 2>/dev/null
    ok "Uninstalled. Restart Claude Code to take effect."
    exit 0
fi

# --- Check uv ---
if ! command -v uv &>/dev/null; then
    warn "uv not found, installing..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi
ok "uv: $(uv --version 2>/dev/null || echo 'installed')"

# --- Create venv + install deps ---
cd "$BURROW_DIR"
if [ ! -f ".venv/bin/python" ]; then
    info "Creating virtual environment..."
    uv venv
fi
info "Installing dependencies..."
uv pip install -e . -q
ok "Venv + deps installed"

# --- Verify MCP server ---
.venv/bin/python -c "from burrow.mcp_server import mcp; assert len(mcp._tool_manager._tools) == 7" 2>/dev/null
ok "MCP server: 7 tools verified"

# --- Create plugins directory ---
mkdir -p "$PLUGINS_DIR"

# --- Symlink ---
REAL_BURROW="$(readlink -f "$BURROW_DIR")"
EXISTING_LINK="$(readlink -f "$PLUGINS_DIR/burrow" 2>/dev/null || echo '')"

if [ "$REAL_BURROW" != "$EXISTING_LINK" ]; then
    ln -sfn "$BURROW_DIR" "$PLUGINS_DIR/burrow"
    ok "Symlinked: $PLUGINS_DIR/burrow → $BURROW_DIR"
else
    ok "Symlink already correct"
fi

# --- Register in installed_plugins.json ---
if [ ! -f "$INSTALLED" ]; then
    echo '{"version": 2, "plugins": {}}' > "$INSTALLED"
fi

python3 -c "
import json, subprocess, os
from datetime import datetime, timezone

with open('$INSTALLED') as f:
    data = json.load(f)

now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')
sha = ''
try:
    sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd='$BURROW_DIR', stderr=subprocess.DEVNULL).decode().strip()
except Exception:
    pass

version = '0.2.0'
try:
    with open('$BURROW_DIR/.claude-plugin/plugin.json') as f:
        version = json.load(f).get('version', version)
except Exception:
    pass

data.setdefault('plugins', {})['burrow@local'] = [{
    'scope': 'user',
    'installPath': os.path.realpath('$BURROW_DIR'),
    'version': version,
    'installedAt': now,
    'lastUpdated': now,
    'gitCommitSha': sha
}]

with open('$INSTALLED', 'w') as f:
    json.dump(data, f, indent=2)
"
ok "Registered in installed_plugins.json"

# --- Enable in settings.json ---
if [ ! -f "$SETTINGS" ]; then
    echo '{"enabledPlugins": {}}' > "$SETTINGS"
fi

python3 -c "
import json
with open('$SETTINGS') as f:
    data = json.load(f)
data.setdefault('enabledPlugins', {})['burrow@local'] = True
with open('$SETTINGS', 'w') as f:
    json.dump(data, f, indent=2)
"
ok "Enabled in settings.json"

# --- Test MCP handshake ---
RESULT=$(echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"installer","version":"1.0"}}}' \
  | timeout 5 uv --directory "$BURROW_DIR" run burrow-mcp 2>/dev/null \
  | python3 -c "import json,sys; d=json.loads(sys.stdin.readline()); print(d['result']['serverInfo']['name'])" 2>/dev/null || echo "FAIL")

if [ "$RESULT" = "burrow" ]; then
    ok "MCP handshake: OK"
else
    fail "MCP handshake failed"
fi

# --- Test registry ---
REGISTRY_OK=$(.venv/bin/python -c "
import asyncio
from burrow.peer import Peer
async def t():
    p = Peer('wss://reg.ai-smith.net', 'install-verify')
    await p.connect()
    await p.ws.close()
    return True
try:
    asyncio.run(t())
    print('OK')
except:
    print('FAIL')
" 2>/dev/null)

if [ "$REGISTRY_OK" = "OK" ]; then
    ok "Registry: wss://reg.ai-smith.net reachable"
else
    warn "Registry unreachable (fallback: burrow serve + ws://localhost:7654)"
fi

# --- Done ---
echo ""
echo -e "${G}Burrow plugin installed successfully!${N}"
echo ""
echo "Restart Claude Code to activate. The SessionStart hook will"
echo "auto-connect you to wss://reg.ai-smith.net on next session."
echo ""
echo "Tools available: burrow_connect, burrow_list_peers,"
echo "burrow_send_message, burrow_send_file, burrow_open_tunnel,"
echo "burrow_serve, burrow_disconnect"
