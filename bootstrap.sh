#!/usr/bin/env bash
# bootstrap.sh — zero-friction setup for burrow P2P networking
# Usage: curl -sSL <url>/bootstrap.sh | bash
#    or: git clone <repo> && cd burrow && ./bootstrap.sh
set -euo pipefail

# ---------------------------------------------------------------------------
# Color output (with non-TTY fallback)
# ---------------------------------------------------------------------------
if [ -t 1 ] && command -v tput >/dev/null 2>&1 && [ "$(tput colors 2>/dev/null || echo 0)" -ge 8 ]; then
    BOLD=$(tput bold)
    GREEN=$(tput setaf 2)
    YELLOW=$(tput setaf 3)
    RED=$(tput setaf 1)
    CYAN=$(tput setaf 6)
    RESET=$(tput sgr0)
else
    BOLD="" GREEN="" YELLOW="" RED="" CYAN="" RESET=""
fi

info()  { printf '%s[info]%s  %s\n'  "$GREEN"  "$RESET" "$*"; }
warn()  { printf '%s[warn]%s  %s\n'  "$YELLOW" "$RESET" "$*"; }
err()   { printf '%s[error]%s %s\n'  "$RED"    "$RESET" "$*"; }
step()  { printf '\n%s==> %s%s\n'    "$CYAN"   "$*"     "$RESET"; }
ok()    { printf '%s[ok]%s    %s\n'  "$GREEN"  "$RESET" "$*"; }

die() { err "$@"; exit 1; }

# ---------------------------------------------------------------------------
# 1. Detect OS and architecture
# ---------------------------------------------------------------------------
step "Detecting platform"

OS="$(uname -s)"
ARCH="$(uname -m)"

case "$OS" in
    Linux*)
        # Detect WSL
        if grep -qiE '(microsoft|wsl)' /proc/version 2>/dev/null; then
            PLATFORM="wsl"
            info "Detected Windows (WSL) — $ARCH"
        else
            PLATFORM="linux"
            info "Detected Linux — $ARCH"
        fi
        ;;
    Darwin*)
        PLATFORM="macos"
        info "Detected macOS — $ARCH"
        ;;
    *)
        die "Unsupported OS: $OS. Burrow supports Linux, macOS, and Windows (via WSL)."
        ;;
esac

case "$ARCH" in
    x86_64|amd64)   ARCH_LABEL="x64" ;;
    aarch64|arm64)   ARCH_LABEL="arm64" ;;
    *)               warn "Uncommon architecture: $ARCH — proceeding anyway" ; ARCH_LABEL="$ARCH" ;;
esac

ok "Platform: $PLATFORM / $ARCH_LABEL"

# ---------------------------------------------------------------------------
# 2. Check Python 3.11+
# ---------------------------------------------------------------------------
step "Checking Python"

PYTHON=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        PY_VERSION=$("$candidate" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || true)
        PY_MAJOR=$("$candidate" -c 'import sys; print(sys.version_info.major)' 2>/dev/null || echo 0)
        PY_MINOR=$("$candidate" -c 'import sys; print(sys.version_info.minor)' 2>/dev/null || echo 0)
        if [ "$PY_MAJOR" -ge 3 ] && [ "$PY_MINOR" -ge 11 ]; then
            PYTHON="$candidate"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    err "Python 3.11+ is required but was not found."
    echo ""
    echo "  Install Python 3.11+ for your platform:"
    echo ""
    case "$PLATFORM" in
        macos)  echo "    brew install python@3.12" ;;
        linux)  echo "    sudo apt install python3.12   # Debian/Ubuntu"
                echo "    sudo dnf install python3.12   # Fedora" ;;
        wsl)    echo "    sudo apt install python3.12" ;;
    esac
    echo ""
    echo "  Then re-run this script."
    exit 1
fi

ok "Found $PYTHON ($PY_VERSION)"

# ---------------------------------------------------------------------------
# 3. Check / install uv
# ---------------------------------------------------------------------------
step "Checking uv"

if command -v uv >/dev/null 2>&1; then
    UV_VER=$(uv --version 2>/dev/null | head -1)
    ok "uv already installed ($UV_VER)"
else
    info "uv not found — installing..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # Source the env so uv is available in this session
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    if command -v uv >/dev/null 2>&1; then
        UV_VER=$(uv --version 2>/dev/null | head -1)
        ok "uv installed ($UV_VER)"
    else
        die "uv installation succeeded but binary not found on PATH. Add ~/.local/bin to your PATH and re-run."
    fi
fi

# ---------------------------------------------------------------------------
# 4. Ensure we are inside a burrow repo
# ---------------------------------------------------------------------------
step "Locating burrow project"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# If pyproject.toml exists next to this script, use that directory
if [ -f "$SCRIPT_DIR/pyproject.toml" ] && grep -q 'name = "burrow"' "$SCRIPT_DIR/pyproject.toml" 2>/dev/null; then
    BURROW_DIR="$SCRIPT_DIR"
    ok "Using existing checkout: $BURROW_DIR"
elif [ -f "./pyproject.toml" ] && grep -q 'name = "burrow"' "./pyproject.toml" 2>/dev/null; then
    BURROW_DIR="$(pwd)"
    ok "Using current directory: $BURROW_DIR"
else
    info "Not inside a burrow checkout — cloning..."
    CLONE_TARGET="${BURROW_CLONE_DIR:-./burrow}"
    if [ -d "$CLONE_TARGET" ] && [ -f "$CLONE_TARGET/pyproject.toml" ]; then
        ok "Directory $CLONE_TARGET already exists, reusing"
        BURROW_DIR="$(cd "$CLONE_TARGET" && pwd)"
    else
        REPO_URL="${BURROW_REPO_URL:-https://github.com/user/burrow.git}"
        info "Cloning from $REPO_URL ..."
        git clone "$REPO_URL" "$CLONE_TARGET"
        BURROW_DIR="$(cd "$CLONE_TARGET" && pwd)"
        ok "Cloned to $BURROW_DIR"
    fi
fi

cd "$BURROW_DIR"

# ---------------------------------------------------------------------------
# 5. Create venv and install
# ---------------------------------------------------------------------------
step "Setting up virtual environment"

if [ -d ".venv" ] && [ -f ".venv/bin/python" ]; then
    info "Existing .venv found — reusing"
else
    info "Creating .venv with uv..."
    uv venv --python "$PYTHON"
fi

ok "venv at $BURROW_DIR/.venv"

step "Installing burrow + dev dependencies"

uv pip install -e ".[dev]"

ok "burrow installed (editable mode with dev extras)"

# ---------------------------------------------------------------------------
# 6. Optionally install as Claude Code plugin
# ---------------------------------------------------------------------------
step "Claude Code plugin setup"

CLAUDE_PLUGIN_DIR="$HOME/.claude/plugins/burrow"

if command -v claude >/dev/null 2>&1; then
    info "Claude CLI detected"

    INSTALL_PLUGIN="n"
    if [ -t 0 ]; then
        printf '  Install burrow as a Claude Code MCP plugin? [Y/n] '
        read -r INSTALL_PLUGIN_INPUT </dev/tty || true
        INSTALL_PLUGIN="${INSTALL_PLUGIN_INPUT:-y}"
    else
        info "Non-interactive mode — skipping plugin install (run with BURROW_CLAUDE_PLUGIN=1 to force)"
        if [ "${BURROW_CLAUDE_PLUGIN:-}" = "1" ]; then
            INSTALL_PLUGIN="y"
        fi
    fi

    case "$INSTALL_PLUGIN" in
        [yY]|[yY][eE][sS])
            mkdir -p "$CLAUDE_PLUGIN_DIR"
            # Write the plugin manifest
            cat > "$CLAUDE_PLUGIN_DIR/manifest.json" <<MANIFEST
{
  "name": "burrow",
  "description": "Zero-config P2P networking: discovery, messaging, file transfer, tunneling",
  "transport": "stdio",
  "command": "$BURROW_DIR/.venv/bin/burrow-mcp"
}
MANIFEST
            ok "Plugin manifest written to $CLAUDE_PLUGIN_DIR/manifest.json"
            info "Claude Code will discover burrow tools on next session start"
            ;;
        *)
            info "Skipping Claude Code plugin installation"
            ;;
    esac
else
    info "Claude CLI not found — skipping plugin setup"
    info "To install later: mkdir -p $CLAUDE_PLUGIN_DIR && copy the manifest"
fi

# ---------------------------------------------------------------------------
# 7. Smoke test
# ---------------------------------------------------------------------------
step "Running smoke test"

SMOKE_OK=true
SMOKE_PORT=17654  # Use a high ephemeral port to avoid conflicts
SMOKE_VENV="$BURROW_DIR/.venv/bin"

info "Starting registry on port $SMOKE_PORT ..."
"$SMOKE_VENV/python" -c "
import asyncio, json
from burrow.server import serve, peers as server_peers, by_id
from burrow.protocol import DEFAULT_PORT

async def run():
    import websockets

    # Start the server
    server = await websockets.serve(
        __import__('burrow.server', fromlist=['handler']).handler,
        '127.0.0.1', $SMOKE_PORT
    )

    # Connect a peer
    ws = await websockets.connect('ws://127.0.0.1:$SMOKE_PORT')
    await ws.send(json.dumps({'type': 'register', 'name': 'smoke-test'}))
    resp = json.loads(await ws.recv())
    assert resp['type'] == 'registered', f'Expected registered, got {resp}'

    # List peers
    await ws.send(json.dumps({'type': 'peers'}))
    resp = json.loads(await ws.recv())
    assert resp['type'] == 'peers', f'Expected peers, got {resp}'

    # Ping
    await ws.send(json.dumps({'type': 'ping'}))
    resp = json.loads(await ws.recv())
    assert resp['type'] == 'pong', f'Expected pong, got {resp}'

    # Disconnect
    await ws.close()
    server.close()
    await server.wait_closed()

asyncio.run(run())
" 2>&1 && ok "Smoke test passed: registry, register, peers, ping/pong, disconnect" \
         || { err "Smoke test failed"; SMOKE_OK=false; }

# ---------------------------------------------------------------------------
# 8. Summary
# ---------------------------------------------------------------------------
echo ""
printf '%s' "$BOLD"
echo "============================================="
echo "         burrow is ready"
echo "============================================="
printf '%s' "$RESET"
echo ""
echo "  ${GREEN}Quick start:${RESET}"
echo ""
echo "    # Terminal 1 — start the registry"
echo "    cd $BURROW_DIR"
echo "    .venv/bin/burrow serve"
echo ""
echo "    # Terminal 2 — connect as a peer"
echo "    cd $BURROW_DIR"
echo "    .venv/bin/burrow connect --name alice"
echo ""
echo "    # Terminal 3 — connect another peer"
echo "    .venv/bin/burrow connect --name bob"
echo ""
echo "    # Inside a peer session, type:"
echo "    /peers              — list online peers"
echo "    /msg bob hello!     — send a message"
echo "    /send bob file.txt  — transfer a file"
echo "    /tunnel bob 8080:80 — forward a port"
echo "    /help               — all commands"
echo ""
echo "  ${GREEN}MCP server (for Claude Code / AI agents):${RESET}"
echo ""
echo "    .venv/bin/burrow-mcp"
echo ""
echo "  ${GREEN}Run tests:${RESET}"
echo ""
echo "    cd $BURROW_DIR && .venv/bin/pytest"
echo ""

if [ "$SMOKE_OK" = false ]; then
    warn "Smoke test had issues — check output above"
    exit 1
fi

# ---------------------------------------------------------------------------
# 9. Auto-install Claude Code plugin (if claude is available)
# ---------------------------------------------------------------------------
if command -v claude >/dev/null 2>&1 || [ -d "${CLAUDE_CONFIG_DIR:-$HOME/.claude}" ]; then
    step "Installing as Claude Code plugin..."
    if [ -f "$BURROW_DIR/scripts/install-plugin.sh" ]; then
        bash "$BURROW_DIR/scripts/install-plugin.sh"
    else
        warn "install-plugin.sh not found — skipping plugin registration"
    fi
fi
