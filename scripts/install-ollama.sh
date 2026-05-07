#!/usr/bin/env bash
# Install Ollama as a user-mode service on Windy 0 (or any
# bot host). After this runs, auto-resurrect (PR #145) can
# actually fire end-to-end: when paid creds hit a rate limit,
# the bot auto-switches to the local Ollama model and keeps
# talking.
#
# Why user-mode: no sudo required, fully reversible, scoped to
# one user. Run as the same user the bot runs as so the bot's
# is_ollama_available() probe (port 11434) sees it.
#
# What this does:
#   1. Download Ollama tarball from GitHub releases
#   2. Extract binary to ~/.local/bin/ollama
#   3. Create systemd USER service for `ollama serve`
#   4. Pull llama3.2:3b (the auto-resurrect default model)
#   5. Verify the bot's is_ollama_available() probe sees it
#
# Usage:
#   bash scripts/install-ollama.sh
#
# To uninstall:
#   systemctl --user stop ollama && systemctl --user disable ollama
#   rm ~/.local/bin/ollama ~/.config/systemd/user/ollama.service
#   rm -rf ~/.ollama   # the model cache (~3GB)
#
# Disk requirement: ~3-5 GB (Ollama runtime + model weights).
#
# Bandwidth requirement: ~1.2 GB Ollama tarball + ~2 GB model.
# Run on a fast connection or expect 5-15 min download time.

set -euo pipefail

# ── Tunables (override via env) ───────────────────────────────────
INSTALL_DIR="${INSTALL_DIR:-$HOME/.local/bin}"
OLLAMA_VERSION="${OLLAMA_VERSION:-v0.23.1}"
OLLAMA_HOST_BIND="${OLLAMA_HOST_BIND:-127.0.0.1:11434}"
DEFAULT_MODEL="${WINDY_OLLAMA_DEFAULT_MODEL:-llama3.2:3b}"
RESUME_DOWNLOAD="${RESUME_DOWNLOAD:-1}"

cd_or_die() { cd "$1" || { echo "FATAL: cd $1 failed" >&2; exit 1; }; }

# ── Step 1: Download tarball ─────────────────────────────────────
echo "── Step 1: download Ollama ${OLLAMA_VERSION} tarball"
TMP=/tmp/ollama-install
mkdir -p "$TMP"
cd_or_die "$TMP"
TARBALL="ollama-${OLLAMA_VERSION}.tar.zst"
URL="https://github.com/ollama/ollama/releases/download/${OLLAMA_VERSION}/ollama-linux-amd64.tar.zst"

if [[ -f "$TARBALL" && "$RESUME_DOWNLOAD" == "1" ]]; then
    echo "  resuming partial $TARBALL ($(du -h "$TARBALL" | cut -f1))"
    curl -fL -C - --max-time 600 -o "$TARBALL" "$URL"
else
    curl -fL --max-time 600 -o "$TARBALL" "$URL"
fi
echo "  downloaded $(du -h "$TARBALL" | cut -f1)"

# ── Step 2: Extract ──────────────────────────────────────────────
echo "── Step 2: extract to $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
which zstd > /dev/null 2>&1 || { echo "FATAL: zstd not installed (apt/dnf install zstd)"; exit 2; }
zstd -d -c "$TARBALL" | tar -xf - -C /tmp/ollama-install/
# Tarball layout: bin/ollama + lib/ollama/*. Move bin to install
# dir; library files stay alongside.
if [[ -f /tmp/ollama-install/bin/ollama ]]; then
    install -m 0755 /tmp/ollama-install/bin/ollama "$INSTALL_DIR/ollama"
elif [[ -f /tmp/ollama-install/ollama ]]; then
    install -m 0755 /tmp/ollama-install/ollama "$INSTALL_DIR/ollama"
else
    echo "FATAL: ollama binary not found in extracted tarball"
    find /tmp/ollama-install -name ollama -type f | head -5
    exit 3
fi
echo "  ✓ ollama binary at $INSTALL_DIR/ollama"

# Copy lib/ alongside the binary if present (Ollama looks for libs
# in the binary's directory or a sibling lib/ in newer versions).
if [[ -d /tmp/ollama-install/lib ]]; then
    OLLAMA_LIB_DIR="$HOME/.local/lib/ollama"
    mkdir -p "$OLLAMA_LIB_DIR"
    cp -r /tmp/ollama-install/lib/* "$OLLAMA_LIB_DIR/"
    echo "  ✓ ollama libs at $OLLAMA_LIB_DIR"
fi

# ── Step 3: User systemd service ─────────────────────────────────
echo "── Step 3: install systemd user service"
mkdir -p "$HOME/.config/systemd/user"
cat > "$HOME/.config/systemd/user/ollama.service" <<EOF
[Unit]
Description=Ollama LLM runtime (Windy Fly auto-resurrect bridge)
After=network-online.target

[Service]
Type=simple
ExecStart=$INSTALL_DIR/ollama serve
Environment=OLLAMA_HOST=$OLLAMA_HOST_BIND
Environment=OLLAMA_MODELS=$HOME/.ollama/models
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now ollama
sleep 3

# ── Step 4: Verify the server is up ──────────────────────────────
echo "── Step 4: verify Ollama API"
if ! curl -fsS --max-time 5 "http://${OLLAMA_HOST_BIND}/api/tags" > /dev/null 2>&1; then
    echo "  ⚠  API not responding yet at http://${OLLAMA_HOST_BIND}/api/tags"
    echo "  Check: systemctl --user status ollama"
    exit 4
fi
echo "  ✓ Ollama responding at http://${OLLAMA_HOST_BIND}/api/tags"

# ── Step 5: Pull the auto-resurrect default model ────────────────
echo "── Step 5: pull $DEFAULT_MODEL (~2GB, may take a few minutes)"
"$INSTALL_DIR/ollama" pull "$DEFAULT_MODEL"
echo "  ✓ $DEFAULT_MODEL pulled"

# ── Step 6: Sanity check from the bot's perspective ──────────────
echo "── Step 6: bot probe sanity check"
WINDY_AGENT_DIR="${WINDY_AGENT_DIR:-/home/grantwhitmer/Desktop/Grant\'s Folder/windy-agent}"
VENV_PY="${WINDY_AGENT_DIR}/.venv/bin/python"
if [[ -x "$VENV_PY" ]]; then
    "$VENV_PY" -c "
from windyfly.agent.offline import is_ollama_available
from windyfly.agent.resurrect import list_installed_ollama_models, pick_best_model
print('is_ollama_available():', is_ollama_available())
models = list_installed_ollama_models()
print(f'installed models: {[m[\"name\"] for m in models]}')
best = pick_best_model(models)
print(f'pick_best_model(): {best}')
" || echo "  (sanity check failed but Ollama itself is up)"
fi

echo
echo "✅ Ollama installed."
echo
echo "Auto-resurrect (PR #145) will now actually engage when paid"
echo "creds hit a rate limit. Test it by:"
echo "  1. Disable your ANTHROPIC_API_KEY (set to 'wk_broken_xyz')"
echo "  2. Restart the bot: systemctl --user restart windy-0"
echo "  3. Send a Telegram message — should get the 🚨 auto-switch"
echo "     notification + an Ollama-served reply."
