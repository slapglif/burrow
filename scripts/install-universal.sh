#!/usr/bin/env bash
set -euo pipefail

# Burrow Universal Installer
# Auto-detects Claude Code, OpenCode, Gemini CLI, Cursor, Windsurf, Cline
# and installs the MCP server config in the correct format/location for each.
#
# Usage: bash scripts/install-universal.sh [--uninstall]

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
BURROW_ABS="$(readlink -f "$BURROW_DIR")"

if [ ! -f "$BURROW_DIR/burrow/protocol.py" ]; then
    fail "Not a burrow repo at $BURROW_DIR"
    exit 1
fi
ok "Burrow source: $BURROW_ABS"

# --- Check uv ---
if ! command -v uv &>/dev/null; then
    info "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

# --- Create venv + install ---
cd "$BURROW_DIR"
if [ ! -f ".venv/bin/python" ]; then
    uv venv
fi
uv pip install -e . -q
ok "Dependencies installed"

# --- MCP entry point ---
MCP_CMD="uv"
MCP_ARGS_JSON="[\"--directory\", \"$BURROW_ABS\", \"run\", \"burrow-mcp\"]"

# Track what we installed
INSTALLED=()

# =========================================================================
# Claude Code
# =========================================================================
install_claude_code() {
    info "Detecting Claude Code..."

    # Project-level .mcp.json (in burrow dir)
    cat > "$BURROW_DIR/.mcp.json" <<EOFJ
{
  "mcpServers": {
    "burrow": {
      "command": "$MCP_CMD",
      "args": ["--directory", "$BURROW_ABS", "run", "burrow-mcp"],
      "env": {}
    }
  }
}
EOFJ

    # Also write to parent dir if burrow is a subdirectory
    PARENT_DIR="$(dirname "$BURROW_ABS")"
    if [ "$PARENT_DIR" != "$BURROW_ABS" ]; then
        PARENT_MCP="$PARENT_DIR/.mcp.json"
        if [ -f "$PARENT_MCP" ]; then
            python3 -c "
import json
with open('$PARENT_MCP') as f: data = json.load(f)
data.setdefault('mcpServers', {})['burrow'] = {
    'command': '$MCP_CMD',
    'args': ['--directory', '$BURROW_ABS', 'run', 'burrow-mcp'],
    'env': {}
}
with open('$PARENT_MCP', 'w') as f: json.dump(data, f, indent=2)
" 2>/dev/null
        else
            cp -f "$BURROW_DIR/.mcp.json" "$PARENT_MCP"
        fi
    fi

    # Register via CLI if available
    if command -v claude &>/dev/null; then
        claude mcp remove burrow 2>/dev/null || true
        claude mcp add burrow -s project -- uv --directory "$BURROW_ABS" run burrow-mcp 2>/dev/null || true
    fi

    INSTALLED+=("Claude Code")
    ok "Claude Code: .mcp.json written"
}

# =========================================================================
# OpenCode
# =========================================================================
install_opencode() {
    local config_dir="${OPENCODE_CONFIG_DIR:-$HOME/.config/opencode}"
    local config_file="$config_dir/opencode.json"

    # Check if opencode exists
    if ! command -v opencode &>/dev/null && [ ! -d "$config_dir" ]; then
        return
    fi
    info "Detecting OpenCode..."

    mkdir -p "$config_dir"

    if [ -f "$config_file" ]; then
        # Merge into existing config
        python3 -c "
import json
with open('$config_file') as f: data = json.load(f)
data.setdefault('mcp', {})['burrow'] = {
    'type': 'local',
    'command': ['$MCP_CMD', '--directory', '$BURROW_ABS', 'run', 'burrow-mcp'],
    'enabled': True,
    'environment': {}
}
with open('$config_file', 'w') as f: json.dump(data, f, indent=2)
" 2>/dev/null
    else
        cat > "$config_file" <<EOFJ
{
  "mcp": {
    "burrow": {
      "type": "local",
      "command": ["$MCP_CMD", "--directory", "$BURROW_ABS", "run", "burrow-mcp"],
      "enabled": true,
      "environment": {}
    }
  }
}
EOFJ
    fi

    INSTALLED+=("OpenCode")
    ok "OpenCode: $config_file updated"
}

# =========================================================================
# Gemini CLI
# =========================================================================
install_gemini_cli() {
    local config_dir="$HOME/.gemini"
    local config_file="$config_dir/settings.json"

    if ! command -v gemini &>/dev/null && [ ! -d "$config_dir" ]; then
        return
    fi
    info "Detecting Gemini CLI..."

    mkdir -p "$config_dir"

    if [ -f "$config_file" ]; then
        python3 -c "
import json
with open('$config_file') as f: data = json.load(f)
data.setdefault('mcpServers', {})['burrow'] = {
    'command': '$MCP_CMD',
    'args': ['--directory', '$BURROW_ABS', 'run', 'burrow-mcp'],
    'env': {},
    'trust': True
}
with open('$config_file', 'w') as f: json.dump(data, f, indent=2)
" 2>/dev/null
    else
        cat > "$config_file" <<EOFJ
{
  "mcpServers": {
    "burrow": {
      "command": "$MCP_CMD",
      "args": ["--directory", "$BURROW_ABS", "run", "burrow-mcp"],
      "env": {},
      "trust": true
    }
  }
}
EOFJ
    fi

    # Also write project-level config
    local project_gemini="$BURROW_DIR/.gemini"
    mkdir -p "$project_gemini"
    cat > "$project_gemini/settings.json" <<EOFJ
{
  "mcpServers": {
    "burrow": {
      "command": "$MCP_CMD",
      "args": ["--directory", "$BURROW_ABS", "run", "burrow-mcp"],
      "env": {},
      "trust": true
    }
  }
}
EOFJ

    INSTALLED+=("Gemini CLI")
    ok "Gemini CLI: $config_file updated"
}

# =========================================================================
# Cursor
# =========================================================================
install_cursor() {
    local config_dir="$HOME/.cursor"
    local config_file="$config_dir/mcp.json"

    if ! command -v cursor &>/dev/null && [ ! -d "$config_dir" ]; then
        return
    fi
    info "Detecting Cursor..."

    mkdir -p "$config_dir"

    if [ -f "$config_file" ]; then
        python3 -c "
import json
with open('$config_file') as f: data = json.load(f)
data.setdefault('mcpServers', {})['burrow'] = {
    'command': '$MCP_CMD',
    'args': ['--directory', '$BURROW_ABS', 'run', 'burrow-mcp'],
    'env': {}
}
with open('$config_file', 'w') as f: json.dump(data, f, indent=2)
" 2>/dev/null
    else
        cat > "$config_file" <<EOFJ
{
  "mcpServers": {
    "burrow": {
      "command": "$MCP_CMD",
      "args": ["--directory", "$BURROW_ABS", "run", "burrow-mcp"],
      "env": {}
    }
  }
}
EOFJ
    fi

    # Also write project-level config
    local project_cursor="$BURROW_DIR/.cursor"
    mkdir -p "$project_cursor"
    cp -f "$config_file" "$project_cursor/mcp.json"

    INSTALLED+=("Cursor")
    ok "Cursor: $config_file updated"
}

# =========================================================================
# Windsurf
# =========================================================================
install_windsurf() {
    local config_dir="$HOME/.codeium/windsurf"
    local config_file="$config_dir/mcp_config.json"

    if [ ! -d "$HOME/.codeium" ] && ! command -v windsurf &>/dev/null; then
        return
    fi
    info "Detecting Windsurf..."

    mkdir -p "$config_dir"

    if [ -f "$config_file" ]; then
        python3 -c "
import json
with open('$config_file') as f: data = json.load(f)
data.setdefault('mcpServers', {})['burrow'] = {
    'command': '$MCP_CMD',
    'args': ['--directory', '$BURROW_ABS', 'run', 'burrow-mcp'],
    'env': {}
}
with open('$config_file', 'w') as f: json.dump(data, f, indent=2)
" 2>/dev/null
    else
        cat > "$config_file" <<EOFJ
{
  "mcpServers": {
    "burrow": {
      "command": "$MCP_CMD",
      "args": ["--directory", "$BURROW_ABS", "run", "burrow-mcp"],
      "env": {}
    }
  }
}
EOFJ
    fi

    INSTALLED+=("Windsurf")
    ok "Windsurf: $config_file updated"
}

# =========================================================================
# Cline (VS Code extension)
# =========================================================================
install_cline() {
    local base_dir=""
    if [ "$(uname)" = "Darwin" ]; then
        base_dir="$HOME/Library/Application Support/Code/User/globalStorage/saoudrizwan.claude-dev/settings"
    elif [ -d "$HOME/.config/Code" ]; then
        base_dir="$HOME/.config/Code/User/globalStorage/saoudrizwan.claude-dev/settings"
    elif [ -d "$HOME/.vscode-server" ]; then
        # Remote / codespace
        base_dir="$HOME/.vscode-server/data/User/globalStorage/saoudrizwan.claude-dev/settings"
    fi

    if [ -z "$base_dir" ] || [ ! -d "$(dirname "$base_dir")" ]; then
        return
    fi
    info "Detecting Cline..."

    mkdir -p "$base_dir"
    local config_file="$base_dir/cline_mcp_settings.json"

    if [ -f "$config_file" ]; then
        python3 -c "
import json
with open('$config_file') as f: data = json.load(f)
data.setdefault('mcpServers', {})['burrow'] = {
    'command': '$MCP_CMD',
    'args': ['--directory', '$BURROW_ABS', 'run', 'burrow-mcp'],
    'env': {},
    'disabled': False,
    'alwaysAllow': []
}
with open('$config_file', 'w') as f: json.dump(data, f, indent=2)
" 2>/dev/null
    else
        cat > "$config_file" <<EOFJ
{
  "mcpServers": {
    "burrow": {
      "command": "$MCP_CMD",
      "args": ["--directory", "$BURROW_ABS", "run", "burrow-mcp"],
      "env": {},
      "disabled": false,
      "alwaysAllow": []
    }
  }
}
EOFJ
    fi

    INSTALLED+=("Cline")
    ok "Cline: $config_file updated"
}

# =========================================================================
# Uninstall mode
# =========================================================================
if [ "${1:-}" = "--uninstall" ]; then
    info "Uninstalling burrow from all detected agents..."

    # Claude Code
    rm -f "$BURROW_DIR/.mcp.json"
    PARENT_DIR="$(dirname "$BURROW_ABS")"
    [ -f "$PARENT_DIR/.mcp.json" ] && python3 -c "
import json
with open('$PARENT_DIR/.mcp.json') as f: d = json.load(f)
d.get('mcpServers', {}).pop('burrow', None)
with open('$PARENT_DIR/.mcp.json', 'w') as f: json.dump(d, f, indent=2)
" 2>/dev/null
    command -v claude &>/dev/null && claude mcp remove burrow 2>/dev/null || true

    # OpenCode
    OC="$HOME/.config/opencode/opencode.json"
    [ -f "$OC" ] && python3 -c "
import json
with open('$OC') as f: d = json.load(f)
d.get('mcp', {}).pop('burrow', None)
with open('$OC', 'w') as f: json.dump(d, f, indent=2)
" 2>/dev/null

    # Gemini CLI
    GC="$HOME/.gemini/settings.json"
    [ -f "$GC" ] && python3 -c "
import json
with open('$GC') as f: d = json.load(f)
d.get('mcpServers', {}).pop('burrow', None)
with open('$GC', 'w') as f: json.dump(d, f, indent=2)
" 2>/dev/null

    # Cursor
    CC="$HOME/.cursor/mcp.json"
    [ -f "$CC" ] && python3 -c "
import json
with open('$CC') as f: d = json.load(f)
d.get('mcpServers', {}).pop('burrow', None)
with open('$CC', 'w') as f: json.dump(d, f, indent=2)
" 2>/dev/null

    # Windsurf
    WC="$HOME/.codeium/windsurf/mcp_config.json"
    [ -f "$WC" ] && python3 -c "
import json
with open('$WC') as f: d = json.load(f)
d.get('mcpServers', {}).pop('burrow', None)
with open('$WC', 'w') as f: json.dump(d, f, indent=2)
" 2>/dev/null

    ok "Uninstalled from all detected agents."
    exit 0
fi

# =========================================================================
# Run all installers
# =========================================================================
install_claude_code
install_opencode
install_gemini_cli
install_cursor
install_windsurf
install_cline

# --- Test MCP handshake ---
RESULT=$(echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"installer","version":"1.0"}}}' \
  | timeout 5 uv --directory "$BURROW_DIR" run burrow-mcp 2>/dev/null \
  | python3 -c "import json,sys; d=json.loads(sys.stdin.readline()); print(d['result']['serverInfo']['name'])" 2>/dev/null || echo "FAIL")

if [ "$RESULT" = "burrow" ]; then
    ok "MCP handshake: OK"
else
    fail "MCP handshake failed"
fi

# --- Summary ---
echo ""
echo -e "${G}Burrow installed for: ${INSTALLED[*]}${N}"
echo ""
echo "Restart your agent to activate. All burrow_* tools (48) will be available."
echo "Verify with: claude mcp list (or equivalent for your agent)"
