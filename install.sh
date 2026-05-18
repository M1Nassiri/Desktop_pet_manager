#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
#  install.sh — Desktop Pet Manager installer
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

BOLD='\033[1m'; DIM='\033[2m'
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'
CYAN='\033[0;36m'; MAGENTA='\033[0;35m'; NC='\033[0m'

info()    { echo -e "${GREEN}✓${NC} $*"; }
warn()    { echo -e "${YELLOW}⚠${NC} $*"; }
err()     { echo -e "${RED}✗${NC} $*"; }
step()    { echo -e "${CYAN}▶${NC} $*"; }
detail()  { echo -e "${DIM}  $*${NC}"; }
header()  { echo -e "\n${BOLD}${CYAN}$*${NC}"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$HOME/.local/bin"
CONF_DIR="$HOME/.config/desktop-pet"
SYSTEMD_DIR="$HOME/.config/systemd/user"
APPS_DIR="$HOME/.local/share/applications"
BACKUP_DIR="$CONF_DIR/.backup/$(date +%Y%m%d-%H%M%S)"

CORE_FILES=("pet_desktop.py" "pet_manager.py" "pet_config.py")

ROLLBACK_NEEDED=false
ROLLBACK_ACTIONS=()
register_rollback() { ROLLBACK_ACTIONS+=("$1"); ROLLBACK_NEEDED=true; }
rollback() {
    [[ "$ROLLBACK_NEEDED" == false ]] && return
    header "Rolling back…"
    for a in "${ROLLBACK_ACTIONS[@]}"; do eval "$a" 2>/dev/null || true; done
    err "Installation failed. Changes rolled back."
}
trap rollback EXIT

# ── Banner ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${CYAN}╔═══════════════════════════════════════╗${NC}"
echo -e "${BOLD}${CYAN}║   Desktop Pet Manager — Installer     ║${NC}"
echo -e "${BOLD}${CYAN}╚═══════════════════════════════════════╝${NC}"
echo ""

# ── Pre-flight ────────────────────────────────────────────────────────────────
header "Pre-flight checks"

command -v python3 >/dev/null || { err "python3 required"; exit 1; }
detail "python3: $(python3 --version 2>&1)"

if ! python3 -c 'import sys; exit(0 if sys.version_info >= (3,10) else 1)'; then
    warn "Python 3.10+ recommended (you have $(python3 --version 2>&1))"
fi

command -v systemctl >/dev/null || warn "systemctl not found — autostart will not work"

[[ -n "${WAYLAND_DISPLAY:-}" ]] && info "Display: Wayland" || info "Display: X11 / ${DISPLAY:-unknown}"

# ── Python deps ───────────────────────────────────────────────────────────────
header "Python dependencies"

if python3 -c "from PIL import Image" 2>/dev/null; then
    info "Pillow: already installed"
else
    step "Installing Pillow…"
    pip3 install --user Pillow || { err "Pillow install failed"; exit 1; }
    info "Pillow: installed"
fi

QT_BACKEND=""
if python3 -c "from PyQt6.QtWidgets import QApplication" 2>/dev/null; then
    QT_BACKEND="PyQt6"; info "PyQt6: already installed"
elif python3 -c "from PySide6.QtWidgets import QApplication" 2>/dev/null; then
    QT_BACKEND="PySide6"; info "PySide6: already installed (fallback)"
else
    step "Installing PyQt6…"
    pip3 install --user PyQt6 || { err "PyQt6 install failed"; exit 1; }
    QT_BACKEND="PyQt6"; info "PyQt6: installed"
fi
detail "Qt backend: $QT_BACKEND"

# Optional tools
command -v dbus-send &>/dev/null && info "dbus-send: available (KWin OK)" \
    || warn "dbus-send missing — KWin Wayland integration disabled"
command -v xprop &>/dev/null && info "xprop: available (X11 hints OK)" \
    || warn "xprop missing — X11 window hints disabled"

# ── Backup ────────────────────────────────────────────────────────────────────
if [[ -d "$CONF_DIR" ]] || [[ -f "$BIN_DIR/pet_desktop.py" ]]; then
    header "Backing up existing installation"
    mkdir -p "$BACKUP_DIR"
    for f in "${CORE_FILES[@]}"; do
        [[ -f "$BIN_DIR/$f" ]] && cp "$BIN_DIR/$f" "$BACKUP_DIR/" && detail "backed up $f"
    done
    [[ -f "$BIN_DIR/desktop-pet" ]] && cp "$BIN_DIR/desktop-pet" "$BACKUP_DIR/"
    info "Backup: $BACKUP_DIR"
fi

# ── Directories ───────────────────────────────────────────────────────────────
header "Creating directories"
mkdir -p "$BIN_DIR" "$CONF_DIR" "$CONF_DIR/instances" "$SYSTEMD_DIR" "$APPS_DIR"
detail "BIN:    $BIN_DIR"
detail "CONF:   $CONF_DIR"
detail "SYSTEMD: $SYSTEMD_DIR"

# ── Core files ────────────────────────────────────────────────────────────────
header "Installing core files"
MISSING=()
for f in "${CORE_FILES[@]}"; do
    src="$SCRIPT_DIR/$f"
    if [[ -f "$src" ]]; then
        cp "$src" "$BIN_DIR/$f"; chmod +x "$BIN_DIR/$f"
        info "Installed: $f"
        register_rollback "rm -f '$BIN_DIR/$f'"
    else
        MISSING+=("$f"); err "Missing: $src"
    fi
done
[[ ${#MISSING[@]} -gt 0 ]] && { err "Required files missing: ${MISSING[*]}"; exit 1; }

# ── Wrapper ───────────────────────────────────────────────────────────────────
header "Installing launcher"
cat > "$BIN_DIR/desktop-pet" <<WRAPPER
#!/usr/bin/env bash
BIN_DIR="\$(cd "\$(dirname "\${BASH_SOURCE[0]}")" && pwd)"
exec python3 "\$BIN_DIR/pet_manager.py" "\$@"
WRAPPER
chmod +x "$BIN_DIR/desktop-pet"
register_rollback "rm -f '$BIN_DIR/desktop-pet'"
info "Installed: desktop-pet launcher"

# ── .desktop ─────────────────────────────────────────────────────────────────
header "Desktop integration"
cat > "$APPS_DIR/desktop-pet.desktop" <<EOF
[Desktop Entry]
Name=Desktop Pet
Comment=Animated character overlay for the desktop
Exec=$BIN_DIR/desktop-pet %F
Icon=face-smile
Type=Application
Categories=Utility;
StartupNotify=false
NoDisplay=true
EOF
register_rollback "rm -f '$APPS_DIR/desktop-pet.desktop'"
info "Installed: desktop-pet.desktop"

# ── KWin rules ────────────────────────────────────────────────────────────────
step "Configuring KWin window rules…"
python3 - <<'PYEOF' 2>/dev/null && info "KWin rules: configured" || warn "KWin rules: skipped"
import configparser
from pathlib import Path

KWIN  = Path.home() / ".config" / "kwinrulesrc"
APP   = "desktop-pet"
cfg   = configparser.RawConfigParser()
cfg.optionxform = str
if KWIN.exists(): cfg.read(KWIN)

sec = next((s for s in cfg.sections()
            if s.isdigit() and cfg.get(s, "wmclass", fallback="") == APP), None)
if sec is None:
    nums = [int(s) for s in cfg.sections() if s.isdigit()]
    sec  = str(max(nums, default=0) + 1)
    cfg.add_section(sec)

for k, v in {
    "Description":    "Desktop Pet Overlay",
    "wmclass":        APP,  "wmclasscomplete": "false", "wmclassmatch": "1",
    "desktopfile":    APP,  "desktopfilematch": "1",
    "above":          "true", "aboverule":      "2",
    "skiptaskbar":    "true", "skiptaskbarrule": "2",
    "skippager":      "true", "skippagerrule":   "2",
    "skipswitcher":   "true", "skipswitcherrule": "2",
}.items():
    cfg.set(sec, k, v)

nums = [s for s in cfg.sections() if s.isdigit()]
if not cfg.has_section("General"): cfg.add_section("General")
cfg.set("General", "count", str(len(nums)))
KWIN.parent.mkdir(parents=True, exist_ok=True)
with open(KWIN, "w") as f: cfg.write(f, space_around_delimiters=False)
PYEOF

# ── PATH ─────────────────────────────────────────────────────────────────────
header "Updating shell PATH"
for rc in "$HOME/.bashrc" "$HOME/.zshrc"; do
    if [[ -f "$rc" ]] && ! grep -q "$BIN_DIR" "$rc" 2>/dev/null; then
        echo 'export PATH="$HOME/.local/bin:$PATH"  # Desktop Pet' >> "$rc"
        info "Added to PATH: $(basename "$rc")"
    fi
done

# ── systemd reload ────────────────────────────────────────────────────────────
command -v systemctl &>/dev/null && {
    header "Reloading systemd"
    systemctl --user daemon-reload && info "systemd: reloaded" || warn "daemon-reload failed"
}

# ── Done ──────────────────────────────────────────────────────────────────────
ROLLBACK_NEEDED=false
echo ""
echo -e "${BOLD}${GREEN}╔═══════════════════════════════════════╗${NC}"
echo -e "${BOLD}${GREEN}║   Installation complete  ✓            ║${NC}"
echo -e "${BOLD}${GREEN}╚═══════════════════════════════════════╝${NC}"
echo ""
echo -e "${BOLD}Launch:${NC}  desktop-pet"
echo ""
echo -e "${BOLD}Pack structure expected:${NC}"
echo "  ~/my_character/"
echo "    idle.gif       walk.gif      run.gif"
echo "    jump.gif       fall.gif      react.gif"
echo "    sleep.gif      drag.gif      land.gif"
echo ""
echo -e "${DIM}All states are optional — missing ones fall back to idle.${NC}"
echo ""
echo -e "${DIM}Uninstall: ./uninstall.sh${NC}"
echo ""
