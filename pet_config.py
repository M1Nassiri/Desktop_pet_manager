#!/usr/bin/env python3
# pet_config.py — Desktop Pet configuration bridge
# Shared between pet_manager.py (TUI) and pet_desktop.py (renderer).

import json
import os
import subprocess
import uuid
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
CONFIG_DIR    = Path.home() / ".config" / "desktop-pet"
INSTANCES_DIR = CONFIG_DIR / "instances"
SYSTEMD_DIR   = Path.home() / ".config" / "systemd" / "user"
AUTOSTART_DIR = Path.home() / ".config" / "autostart"
BIN_DIR       = Path.home() / ".local" / "bin"


# ── Directory helpers ─────────────────────────────────────────────────────────
def ensure_dirs():
    for d in (CONFIG_DIR, INSTANCES_DIR, SYSTEMD_DIR, AUTOSTART_DIR):
        d.mkdir(parents=True, exist_ok=True)


# ── Instance I/O ──────────────────────────────────────────────────────────────
def new_instance_id() -> str:
    return uuid.uuid4().hex[:8]


def instance_path(iid: str) -> Path:
    return INSTANCES_DIR / f"{iid}.state.json"


def save_instance(iid: str, data: dict):
    ensure_dirs()
    with open(instance_path(iid), "w") as f:
        json.dump(data, f, indent=2)


def load_instance(iid: str) -> dict:
    p = instance_path(iid)
    if p.exists():
        try:
            with open(p) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def list_instances() -> list[tuple[str, dict]]:
    ensure_dirs()
    result = []
    for p in sorted(INSTANCES_DIR.glob("*.state.json")):
        # stem is "abcd1234.state" → split on first dot
        iid = p.name.split(".")[0]
        try:
            with open(p) as f:
                data = json.load(f)
        except Exception:
            data = {}
        result.append((iid, data))
    return result


def delete_instance(iid: str):
    p = instance_path(iid)
    if p.exists():
        p.unlink()
    # Remove associated service file if present
    for svc in SYSTEMD_DIR.glob(f"desktop-pet-*-{iid}.service"):
        try:
            svc.unlink()
        except Exception:
            pass


def update_instance_field(iid: str, key: str, value):
    data = load_instance(iid)
    data[key] = value
    save_instance(iid, data)


# ── Systemd helpers ───────────────────────────────────────────────────────────
def service_name(iid: str, name: str) -> str:
    safe = name.replace(" ", "_").replace("/", "_").replace("\\", "_")
    return f"desktop-pet-{safe}-{iid}.service"


def write_service(iid: str, inst: dict) -> str:
    name      = inst.get("name", iid)
    pack      = inst.get("pack_path", "")
    scale     = inst.get("scale", 0.15)
    mode      = inst.get("mode", "neutral")
    sname     = service_name(iid, name)
    svc_path  = SYSTEMD_DIR / sname

    content = (
        "[Unit]\n"
        f"Description=Desktop Pet: {name}\n"
        "After=graphical-session.target\n"
        "PartOf=graphical-session.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        "ExecStartPre=/bin/sleep 3\n"
        f"ExecStart=python3 {BIN_DIR}/pet_desktop.py \\\n"
        f"    '{pack}' \\\n"
        f"    --instance-id {iid} \\\n"
        f"    --scale {scale} \\\n"
        f"    --mode {mode}\n"
        "Restart=on-failure\n"
        "RestartSec=5\n"
        "PassEnvironment=DISPLAY WAYLAND_DISPLAY XDG_RUNTIME_DIR "
        "DBUS_SESSION_BUS_ADDRESS QT_QPA_PLATFORM XDG_SESSION_TYPE\n"
        "Environment=PYTHONUNBUFFERED=1\n"
        "\n"
        "[Install]\n"
        "WantedBy=graphical-session.target\n"
    )

    ensure_dirs()
    with open(svc_path, "w") as f:
        f.write(content)
    return sname


def systemctl(args: list[str]) -> tuple[int, str]:
    try:
        r = subprocess.run(
            ["systemctl", "--user"] + args,
            capture_output=True, text=True, timeout=15,
        )
        return r.returncode, (r.stdout + r.stderr).strip()
    except FileNotFoundError:
        return 1, "systemctl not found"
    except Exception as e:
        return 1, str(e)


def is_service_active(iid: str, name: str) -> bool:
    sname = service_name(iid, name)
    code, _ = systemctl(["is-active", "--quiet", sname])
    return code == 0


def is_service_enabled(iid: str, name: str) -> bool:
    sname = service_name(iid, name)
    code, _ = systemctl(["is-enabled", "--quiet", sname])
    return code == 0


# ── Pack helpers ──────────────────────────────────────────────────────────────
def scan_pack(pack_path: str) -> dict:
    """
    Return basic metadata about an animation pack directory.
    Returns dict with 'animations' (list of found base names) and 'valid' bool.
    """
    p = Path(pack_path).expanduser().resolve()
    if not p.is_dir():
        return {"valid": False, "animations": [], "error": "Not a directory"}

    exts = {".gif", ".png", ".webp", ".jpg", ".jpeg"}
    files = [f.stem.lower() for f in p.iterdir()
             if f.is_file() and f.suffix.lower() in exts]

    return {
        "valid":      len(files) > 0,
        "animations": sorted(files),
        "path":       str(p),
    }
