#!/usr/bin/env bash
set -euo pipefail

# Deploy/update the burrow registry server locally.
# Usage: bash scripts/deploy-registry.sh [--install]
#
# --install: First-time setup (creates user, installs systemd service, cloudflared)
# Without flags: Just pulls latest code and restarts.

BURROW_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_DIR="${REGISTRY_PATH:-/opt/burrow}"

if [ "${1:-}" = "--install" ]; then
    echo "=== First-time registry setup ==="

    # Create burrow user if needed
    if ! id burrow &>/dev/null; then
        sudo useradd -r -m -s /bin/bash burrow
    fi

    # Clone or link
    if [ ! -d "$INSTALL_DIR" ]; then
        sudo mkdir -p "$INSTALL_DIR"
        sudo chown burrow:burrow "$INSTALL_DIR"
        sudo -u burrow git clone https://github.com/slapglif/burrow.git "$INSTALL_DIR"
    fi

    # Install uv + deps
    sudo -u burrow bash -c "
        cd $INSTALL_DIR
        if ! command -v uv &>/dev/null; then
            curl -LsSf https://astral.sh/uv/install.sh | sh
            export PATH=\$HOME/.local/bin:\$PATH
        fi
        uv venv --python 3.12
        uv pip install -e .
    "

    # Install systemd service
    sudo cp -f "$BURROW_DIR/scripts/burrow-registry.service" /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable burrow-registry
    sudo systemctl start burrow-registry

    echo "Registry installed and running on port 7654."
    echo "Set up Cloudflare tunnel to expose as wss://reg.ai-smith.net"
    exit 0
fi

echo "=== Updating registry ==="
cd "$INSTALL_DIR"

# Pull latest
sudo -u burrow git fetch origin master
sudo -u burrow git reset --hard origin/master

# Update deps
sudo -u burrow bash -c "
    cd $INSTALL_DIR
    export PATH=\$HOME/.local/bin:\$PATH
    uv pip install -e .
"

# Restart
sudo systemctl restart burrow-registry
echo "Registry restarted: $(git log --oneline -1)"
