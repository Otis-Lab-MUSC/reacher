#!/bin/sh
# REACHER install script
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/otis-lab-musc/reacher/main/scripts/install.sh | bash
#
# What it does:
#   1. Checks Python >= 3.10
#   2. Installs pipx if missing
#   3. Installs/upgrades REACHER via pipx
#   4. Adds user to dialout group (serial port access)
#   5. Installs systemd services (Linux only)
#   6. Opens firewall port 6229 if ufw is active
#
# Safe to re-run (idempotent).

set -e

REPO_URL="https://raw.githubusercontent.com/otis-lab-musc/reacher/main"
REACHER_PORT=6229

# ── Helpers ─────────────────────────────────────────────────────────

info()  { printf '\033[1;32m==>\033[0m %s\n' "$1"; }
warn()  { printf '\033[1;33m  !\033[0m %s\n' "$1"; }
error() { printf '\033[1;31mERR\033[0m %s\n' "$1" >&2; exit 1; }

need_sudo() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
    else
        info "Requesting sudo for: $*"
        sudo "$@"
    fi
}

# ── Detect OS ───────────────────────────────────────────────────────

detect_os() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        OS_ID="${ID}"
        OS_NAME="${PRETTY_NAME}"
    elif [ "$(uname)" = "Darwin" ]; then
        OS_ID="macos"
        OS_NAME="macOS $(sw_vers -productVersion)"
    else
        OS_ID="unknown"
        OS_NAME="Unknown OS"
    fi
    info "Detected: ${OS_NAME}"
}

# ── Check Python ────────────────────────────────────────────────────

check_python() {
    if command -v python3 >/dev/null 2>&1; then
        PY="python3"
    elif command -v python >/dev/null 2>&1; then
        PY="python"
    else
        PY=""
    fi

    if [ -n "$PY" ]; then
        PY_VER=$($PY -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        PY_MAJOR=$($PY -c "import sys; print(sys.version_info.major)")
        PY_MINOR=$($PY -c "import sys; print(sys.version_info.minor)")
        if [ "$PY_MAJOR" -ge 3 ] && [ "$PY_MINOR" -ge 10 ]; then
            info "Python ${PY_VER} found at $(command -v $PY)"
            return 0
        fi
        warn "Python ${PY_VER} found but REACHER requires >= 3.10"
    fi

    # Try to install Python on Debian-based systems
    case "$OS_ID" in
        debian|ubuntu|raspbian)
            info "Installing Python 3 via apt..."
            need_sudo apt-get update -qq
            need_sudo apt-get install -y -qq python3 python3-pip python3-venv
            PY="python3"
            ;;
        *)
            error "Python >= 3.10 is required. Install it manually and re-run this script."
            ;;
    esac
}

# ── Install pipx ───────────────────────────────────────────────────

install_pipx() {
    if command -v pipx >/dev/null 2>&1; then
        info "pipx already installed"
        return 0
    fi

    info "Installing pipx..."
    case "$OS_ID" in
        debian|ubuntu|raspbian)
            need_sudo apt-get install -y -qq pipx 2>/dev/null || {
                $PY -m pip install --user pipx 2>/dev/null || {
                    need_sudo apt-get install -y -qq python3-pip
                    $PY -m pip install --user pipx
                }
            }
            ;;
        macos)
            if command -v brew >/dev/null 2>&1; then
                brew install pipx
            else
                $PY -m pip install --user pipx
            fi
            ;;
        *)
            $PY -m pip install --user pipx
            ;;
    esac

    # Ensure pipx is on PATH
    if ! command -v pipx >/dev/null 2>&1; then
        $PY -m pipx ensurepath 2>/dev/null || true
        export PATH="$HOME/.local/bin:$PATH"
    fi

    if ! command -v pipx >/dev/null 2>&1; then
        error "pipx installation failed. Install it manually: https://pipx.pypa.io"
    fi
}

# ── Install REACHER ─────────────────────────────────────────────────

install_reacher() {
    if command -v reacher >/dev/null 2>&1; then
        CURRENT=$(reacher --version 2>/dev/null || echo "unknown")
        info "Upgrading REACHER (current: ${CURRENT})..."
        pipx upgrade reacher || pipx install reacher --force
    else
        info "Installing REACHER..."
        pipx install reacher
    fi

    # Verify
    if ! command -v reacher >/dev/null 2>&1; then
        export PATH="$HOME/.local/bin:$PATH"
    fi
    INSTALLED=$(reacher --version 2>/dev/null || echo "unknown")
    info "REACHER ${INSTALLED} installed at $(command -v reacher)"
}

# ── Serial port permissions ─────────────────────────────────────────

setup_serial() {
    [ "$OS_ID" = "macos" ] && return 0  # macOS doesn't use dialout group

    USER_NAME=$(whoami)
    if id -nG "$USER_NAME" | grep -qw dialout; then
        info "User '${USER_NAME}' already in dialout group"
    else
        info "Adding '${USER_NAME}' to dialout group for serial port access..."
        need_sudo usermod -aG dialout "$USER_NAME"
        warn "You may need to log out and back in for serial access to take effect"
    fi
}

# ── Systemd services ───────────────────────────────────────────────

setup_systemd() {
    # Only on Linux with systemd
    if [ "$OS_ID" = "macos" ] || ! command -v systemctl >/dev/null 2>&1; then
        return 0
    fi

    USER_NAME=$(whoami)
    info "Installing systemd services..."

    # Download and install service files
    for SVC in "reacher@.service" "reacher-monitor@.service"; do
        TMPFILE=$(mktemp)
        if curl -fsSL "${REPO_URL}/systemd/${SVC}" -o "$TMPFILE" 2>/dev/null; then
            need_sudo cp "$TMPFILE" "/etc/systemd/system/${SVC}"
            info "  Installed ${SVC}"
        else
            warn "  Could not download ${SVC} — skipping"
        fi
        rm -f "$TMPFILE"
    done

    need_sudo systemctl daemon-reload

    # Enable the API service for the current user
    need_sudo systemctl enable "reacher@${USER_NAME}" 2>/dev/null || true
    info "Enabled reacher@${USER_NAME} (starts on boot)"
    info "  Start now:  sudo systemctl start reacher@${USER_NAME}"
    info "  View logs:  journalctl -u reacher@${USER_NAME} -f"
}

# ── Firewall ────────────────────────────────────────────────────────

setup_firewall() {
    if ! command -v ufw >/dev/null 2>&1; then
        return 0
    fi

    UFW_STATUS=$(need_sudo ufw status 2>/dev/null | head -1)
    if echo "$UFW_STATUS" | grep -q "inactive"; then
        return 0
    fi

    if need_sudo ufw status | grep -q "${REACHER_PORT}"; then
        info "Firewall already allows port ${REACHER_PORT}"
    else
        info "Opening firewall port ${REACHER_PORT}..."
        need_sudo ufw allow "${REACHER_PORT}/tcp" comment "REACHER API"
    fi
}

# ── Main ────────────────────────────────────────────────────────────

main() {
    echo ""
    echo "  ╔═══════════════════════════════════════╗"
    echo "  ║     REACHER Installer                 ║"
    echo "  ║     Otis Lab — MUSC                   ║"
    echo "  ╚═══════════════════════════════════════╝"
    echo ""

    detect_os
    check_python
    install_pipx
    install_reacher
    setup_serial
    setup_systemd
    setup_firewall

    echo ""
    info "Installation complete!"
    echo ""
    echo "  Verify:   reacher --version"
    echo "  Start:    reacher"
    echo "  Health:   curl http://localhost:${REACHER_PORT}/health"
    echo ""
}

main
