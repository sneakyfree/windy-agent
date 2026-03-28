#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# 🪰 Windy Fly Installer
#
# Usage:
#   curl -fsSL https://get.windyfly.com | bash
#   — or —
#   bash <(curl -fsSL https://raw.githubusercontent.com/sneakyfree/windy-agent/main/scripts/install.sh)
#
# Zero-prompt install (have your key ready):
#   WINDY_KEY=sk-abc123 curl -fsSL https://get.windyfly.com | bash
#
# What it does:
#   1. Checks/installs Python 3.12+, uv, and Bun
#   2. Clones the windy-agent repo (or updates if already cloned)
#   3. Installs all dependencies
#   4. Launches quickstart (or auto-configures if WINDY_KEY is set)
# ═══════════════════════════════════════════════════════════════════════

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
RESET='\033[0m'

REPO_URL="https://github.com/sneakyfree/windy-agent.git"
INSTALL_DIR="${WINDY_INSTALL_DIR:-$HOME/windy-agent}"

# ── Helpers ────────────────────────────────────────────────────────────

info()  { echo -e "  ${CYAN}→${RESET} $1"; }
ok()    { echo -e "  ${GREEN}✓${RESET} $1"; }
warn()  { echo -e "  ${YELLOW}⚠${RESET} $1"; }
fail()  { echo -e "  ${RED}✗${RESET} $1"; exit 1; }

banner() {
    echo ""
    echo -e "${CYAN}╔══════════════════════════════════════════════════╗${RESET}"
    echo -e "${CYAN}║${RESET}  ${BOLD}🪰 Windy Fly Installer${RESET}                          ${CYAN}║${RESET}"
    echo -e "${CYAN}║${RESET}  ${DIM}Your AI. Your Rules. Your Ecosystem.${RESET}             ${CYAN}║${RESET}"
    echo -e "${CYAN}╚══════════════════════════════════════════════════╝${RESET}"
    echo ""
}

# ── Prerequisite checks ───────────────────────────────────────────────

check_python() {
    if command -v python3 &>/dev/null; then
        PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
        PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
        if [ "$PY_MAJOR" -ge 3 ] && [ "$PY_MINOR" -ge 12 ]; then
            ok "Python $PY_VERSION"
            return 0
        else
            warn "Python $PY_VERSION found (need 3.12+)"
            return 1
        fi
    else
        warn "Python not found"
        return 1
    fi
}

check_uv() {
    if command -v uv &>/dev/null; then
        ok "uv $(uv --version 2>/dev/null | head -1 || echo 'installed')"
        return 0
    else
        return 1
    fi
}

check_bun() {
    if command -v bun &>/dev/null; then
        ok "Bun $(bun --version 2>/dev/null || echo 'installed')"
        return 0
    else
        return 1
    fi
}

check_git() {
    if command -v git &>/dev/null; then
        ok "Git $(git --version | awk '{print $3}')"
        return 0
    else
        fail "Git is required. Install it first: https://git-scm.com"
    fi
}

# ── Auto-installers ───────────────────────────────────────────────────

install_uv() {
    info "Installing uv (Python package manager)..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # Source the env so uv is available in this session
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    if command -v uv &>/dev/null; then
        ok "uv installed"
    else
        fail "uv installation failed. Install manually: https://docs.astral.sh/uv/"
    fi
}

install_bun() {
    info "Installing Bun (JavaScript runtime)..."
    curl -fsSL https://bun.sh/install | bash
    # Source the env so bun is available in this session
    export PATH="$HOME/.bun/bin:$PATH"
    if command -v bun &>/dev/null; then
        ok "Bun installed"
    else
        fail "Bun installation failed. Install manually: https://bun.sh"
    fi
}

install_python() {
    info "Python 3.12+ required. Attempting install via uv..."
    if command -v uv &>/dev/null; then
        uv python install 3.12
        ok "Python 3.12 installed via uv"
    else
        fail "Cannot auto-install Python. Install Python 3.12+: https://python.org/downloads/"
    fi
}

# ── Main ──────────────────────────────────────────────────────────────

main() {
    banner

    echo -e "${BOLD}Checking prerequisites...${RESET}"
    echo ""

    check_git

    # Check and install uv first (needed to install Python)
    if ! check_uv; then
        install_uv
    fi

    # Check Python (uv can install it if missing)
    if ! check_python; then
        install_python
    fi

    # Check Bun
    if ! check_bun; then
        install_bun
    fi

    echo ""
    echo -e "${GREEN}${BOLD}All prerequisites satisfied!${RESET}"
    echo ""

    # Clone or update repo
    if [ -d "$INSTALL_DIR/.git" ]; then
        info "Updating existing installation at $INSTALL_DIR..."
        cd "$INSTALL_DIR"
        git pull --quiet
        ok "Updated to latest"
    else
        info "Cloning windy-agent to $INSTALL_DIR..."
        git clone --quiet "$REPO_URL" "$INSTALL_DIR"
        cd "$INSTALL_DIR"
        ok "Cloned"
    fi

    echo ""

    # Install Python dependencies
    info "Installing Python dependencies..."
    uv sync --quiet 2>/dev/null || uv sync
    ok "Python dependencies installed"

    # Install gateway dependencies
    if [ -d "gateway" ]; then
        info "Installing gateway dependencies..."
        cd gateway && bun install --silent 2>/dev/null || bun install && cd ..
        ok "Gateway dependencies installed"
    fi

    echo ""
    echo -e "${GREEN}${BOLD}🪰 Installation complete!${RESET}"
    echo ""

    # Launch the quickstart — one key, one paste, done
    echo -e "  ${CYAN}Launching quickstart...${RESET}"
    echo ""
    if [ -n "${WINDY_KEY:-}" ]; then
        # Zero-prompt install: WINDY_KEY=sk-abc123 curl ... | bash
        uv run windy go --key "$WINDY_KEY"
    else
        uv run windy go
    fi

    # Show reference commands after setup completes
    echo ""
    echo -e "  ${BOLD}Quick reference:${RESET}"
    echo -e "    ${CYAN}cd $INSTALL_DIR${RESET}"
    echo -e "    ${CYAN}windy go${RESET}               ${DIM}— Quickstart (one key, done)${RESET}"
    echo -e "    ${CYAN}windy start${RESET}            ${DIM}— Brain + Gateway + Dashboard${RESET}"
    echo -e "    ${CYAN}windy stop${RESET}             ${DIM}— Stop everything${RESET}"
    echo -e "    ${CYAN}windy doctor${RESET}           ${DIM}— Diagnose problems${RESET}"
    echo -e "    ${CYAN}windy init${RESET}             ${DIM}— Full setup wizard${RESET}"
    echo ""
}

main "$@"
