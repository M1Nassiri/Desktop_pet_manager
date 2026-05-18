#!/usr/bin/env bash
# uninstall.sh — Desktop Pet Manager
# Usage:
#   ./uninstall.sh                 — full removal (including config/state)
#   ./uninstall.sh --keep-config   — keep ~/.config/desktop-pet (your pet list)

set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${GREEN}[✓]${NC} $*"; }
warn()    { echo -e "${YELLOW}[!]${NC} $*"; }
skip()    { echo -e "    ${YELLOW}skipped${NC} — $*"; }
section() { echo -e "\n${CYAN}──── $* ────${NC}"; }

KEEP_CONFIG=false
for arg in "$@"; do [[ "$arg" == "--keep-config" ]] && KEEP_CONFIG=true; done

echo ""
echo -e "${CYAN}Desktop Pet Manager — Uninstaller${NC}"
echo ""

if [[ "$KEEP_CONFIG" == false ]]; then
    warn "This will remove all installed files AND your pet config/state."
else
    warn "This will remove all installed files. Your config will be kept."
fi
echo ""
read -rp "Continue? [y/N] " confirm
[[ "${confirm,,}" == "y" ]] || { echo "Aborted."; exit 0; }

# ── Stop services ─────────────────────────────────────────────────────────────
section "Systemd services"
SYSTEMD_DIR="$HOME/.config/systemd/user"
found=false
for f in "$SYSTEMD_DIR"/desktop-pet-*.service; do
    [[ -f "$f" ]] || continue; found=true
    name="$(basename "$f")"
    systemctl --user is-active  --quiet "$name" 2>/dev/null && \
        systemctl --user stop    "$name" && info "Stopped  $name"
    systemctl --user is-enabled --quiet "$name" 2>/dev/null && \
        systemctl --user disable "$name" && info "Disabled $name"
    rm -f "$f"; info "Removed  $f"
done
[[ "$found" == false ]] && skip "no systemd services found"
command -v systemctl &>/dev/null && systemctl --user daemon-reload && \
    info "Reloaded systemd user daemon"

# ── Kill processes ────────────────────────────────────────────────────────────
section "Running processes"
if pgrep -f "pet_desktop.py" &>/dev/null; then
    pkill -f "pet_desktop.py" && info "Killed all pet_desktop.py processes" \
        || warn "Could not kill processes — kill them manually"
else
    skip "no running pet_desktop.py processes"
fi

# ── Desktop files ─────────────────────────────────────────────────────────────
section "Desktop integration"
df="$HOME/.local/share/applications/desktop-pet.desktop"
[[ -f "$df" ]] && rm -f "$df" && info "Removed $df" || skip "$df not found"

for f in "$HOME/.config/autostart"/desktop-pet-*.desktop; do
    [[ -f "$f" ]] && rm -f "$f" && info "Removed $f"
done

# ── Binaries ──────────────────────────────────────────────────────────────────
section "Installed files"
BIN_DIR="$HOME/.local/bin"
for f in desktop-pet pet_desktop.py pet_manager.py pet_config.py; do
    fp="$BIN_DIR/$f"
    [[ -f "$fp" ]] && rm -f "$fp" && info "Removed $fp" || skip "$fp not found"
done

# ── Config ────────────────────────────────────────────────────────────────────
section "Config and state"
CONFIG_DIR="$HOME/.config/desktop-pet"
if [[ "$KEEP_CONFIG" == true ]]; then
    skip "keeping $CONFIG_DIR (--keep-config)"
else
    [[ -d "$CONFIG_DIR" ]] && rm -rf "$CONFIG_DIR" && info "Removed $CONFIG_DIR" \
        || skip "$CONFIG_DIR not found"
fi

# ── Shell RC ──────────────────────────────────────────────────────────────────
section "Shell config"
for rc in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.profile"; do
    [[ -f "$rc" ]] || continue
    if grep -q "Desktop Pet" "$rc" 2>/dev/null; then
        sed -i '/# Desktop Pet/d' "$rc"
        info "Cleaned $rc"
    else
        skip "nothing to clean in $(basename "$rc")"
    fi
done

echo ""
info "Uninstall complete."
[[ "$KEEP_CONFIG" == true ]] && echo -e "\n  Config preserved at: ${CYAN}$CONFIG_DIR${NC}"
echo ""
