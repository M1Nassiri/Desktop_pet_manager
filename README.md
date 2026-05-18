# Desktop Pet Manager

Animated character overlays for your Linux desktop — characters that **walk, run, jump, sleep, and react** to your interactions. The bottom of the screen is their floor.

---

## Table of Contents

- [What it does](#what-it-does)
- [Animation Pack structure](#animation-pack-structure)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Manager TUI](#manager-tui)
- [Interacting with your pet](#interacting-with-your-pet)
- [Behavior AI](#behavior-ai)
- [Architecture](#architecture)
- [Configuration](#configuration)
- [Troubleshooting](#troubleshooting)
- [Uninstallation](#uninstallation)
- [License](#license)

---

## What it does

- Load any number of animated characters simultaneously, each from their own animation pack
- Characters walk along the bottom of your screen, react when you interact with them, jump, run, and fall asleep when left alone
- Drag a character anywhere — release and they fall back to the floor with physics
- Double-click to pet them (triggers the react animation)
- Position, scale, and speed all persist across restarts
- Per-pet autostart via systemd user services
- Always-on-top, hidden from taskbar/pager/alt-tab
- Full X11 support; KDE Wayland support via KWin scripting

---

## Animation Pack structure

A pack is a **directory** containing image files named after the state they represent.

```
my_character/
├── idle.gif        ← standing still, looping
├── walk.gif        ← walking (automatically mirrored for direction)
├── run.gif         ← running fast
├── jump.gif        ← going up
├── fall.gif        ← coming down
├── react.gif       ← petted / double-clicked  (plays once)
├── drag.gif        ← being dragged
├── sleep.gif       ← idle too long
└── land.gif        ← landing after a drop     (plays once)
```

**All files are optional.** Any missing state falls back to `idle`. You can start with just `idle.gif` and add more later.

### Accepted file formats

| Format | Extension |
|--------|-----------|
| Animated GIF | `.gif` |
| PNG (static or animated) | `.png` |
| WebP | `.webp` |
| JPEG | `.jpg` / `.jpeg` |

### Recognized file name variants

Each state accepts several synonyms — the first match wins:

| State | Accepted names |
|-------|---------------|
| IDLE | idle, stand, standing, default, neutral |
| WALK | walk, walking, walk_left, move, stroll |
| RUN | run, running, sprint, dash, rush |
| JUMP | jump, jumping, leap, spring, hop |
| FALL | fall, falling, drop, descend |
| REACT | react, pet, petting, happy, excited, joy, yay |
| DRAG | drag, grabbed, held, carry, picked |
| SLEEP | sleep, sleeping, snore, rest, zzz, nap |
| LAND | land, landing, thud, bounce |

### Where to get packs

- **Itch.io** — search "pixel character sprite sheet" (export individual states as GIFs)
- **OpenGameArt** — free sprite sheets under CC0/CC-BY
- **GifCities / Tenor** — animated GIFs that work as single-state packs
- **Make your own** — any pixel art tool (Aseprite, LibreSprite) can export GIFs per animation

### Splitting a sprite sheet into GIFs

If you have a horizontal sprite sheet, tools like [Aseprite](https://www.aseprite.org/) or `ffmpeg` can slice it. A quick Python snippet:

```python
from PIL import Image
sheet = Image.open("sheet.png")
fw, fh = 32, 32          # frame width / height
frames = [sheet.crop((i*fw, 0, (i+1)*fw, fh)) for i in range(8)]
frames[0].save("idle.gif", save_all=True, append_images=frames[1:],
               loop=0, duration=80)
```

---

## Requirements

### Required

| Package | Purpose | Install |
|---------|---------|---------|
| Python ≥ 3.10 | Runtime | `sudo apt install python3` |
| Pillow | GIF/image decoding | `pip3 install --user Pillow` |
| PyQt6 **or** PySide6 | Qt GUI | `pip3 install --user PyQt6` |

### Recommended

| Package | Purpose | Install |
|---------|---------|---------|
| `dbus-send` | KWin scripting (Wayland) | `sudo apt install dbus-bin` |
| `xprop` | X11 window hints | `sudo apt install x11-utils` |
| `systemd` | Autostart | usually pre-installed |

---

## Installation

```bash
git clone https://github.com/yourname/desktop-pet.git
cd desktop-pet
chmod +x install.sh
./install.sh
```

The installer checks Python/Qt/Pillow, backs up any existing install, copies files to `~/.local/bin/`, writes KWin window rules, and reloads systemd.

---

## Quick Start

```bash
# 1. Launch the manager
desktop-pet

# 2. Press  A  to add a pet
#    Enter a name, the path to your pack directory,
#    scale (try 0.15), speed (1.0), autostart (y/n)

# 3. Your pet appears on screen immediately
```

To run a pet directly without the manager:

```bash
python3 ~/.local/bin/pet_desktop.py ~/my_character/ \
    --scale 0.15 \
    --speed 1.0 \
    --instance-id mypet
```

---

## Manager TUI

Launch with `desktop-pet` or `python3 pet_manager.py`.

### Keybindings

| Key | Action |
|-----|--------|
| `↑` / `k` | Move cursor up |
| `↓` / `j` | Move cursor down |
| `a` | Add a new pet |
| `s` | Start / stop selected pet |
| `e` | Toggle autostart |
| `d` | Delete selected pet |
| `i` | Show pet info (pack contents, state) |
| `r` | Refresh list |
| `q` / `Esc` | Quit |

The list shows: **Name**, **Status**, **Auto** (autostart), **Scale**, **Speed**, and the **pack folder name**.

---

## Interacting with your pet

| Input | Action |
|-------|--------|
| Left-click + drag | Pick up and move the pet |
| Release after dragging | Pet falls back to floor with physics |
| Throw (drag fast + release) | Pet flies and lands |
| Double-click | Pet reacts (plays react animation) |
| Right-click | Context menu |

### Context menu (right-click)

| Option | Effect |
|--------|--------|
| Jump | Force a jump |
| React / Pet | Trigger react animation |
| Wake up | Wake from sleep state |
| Reset position | Move pet to screen center, floor level |
| Quit | Close this pet |

---

## Behavior AI

Left alone, your pet follows an autonomous behavior loop:

| Behavior | Probability | Description |
|----------|-------------|-------------|
| Idle | 30% | Stand still for 1.5–4 s |
| Walk | 25% | Walk in a random direction for 1–3.5 s |
| Run | 15% | Run in a random direction for 0.6–2 s |
| Jump | 12% | Jump, with random horizontal kick |
| Turn + walk | 8% | Turn around and walk briefly |
| Long idle | 10% | Stand still for 3–6 s |

After **18 seconds** of complete inactivity the pet falls asleep. Any interaction wakes it up.

### Physics

- Floor = bottom edge of the screen
- Gravity = 900 px/s²
- Jump velocity = −430 px/s (upward)
- Screen edges cause velocity reflection (bouncing)
- Dropping from a height triggers the `land` animation if the pack has one

---

## Architecture

```
pet_manager.py    curses TUI — list, add, start/stop, autostart, delete
pet_desktop.py    Qt overlay — state machine, physics, animation rendering
pet_config.py     shared bridge — instance I/O, service generation, helpers
```

Each pet is a standalone `pet_desktop.py` process managed as a systemd user service. The manager reads only from JSON state files on disk — no in-memory state is shared.

### Window-management (Wayland / KDE)

1. `app.setDesktopFileName("desktop-pet")` sets the Wayland `app_id`
2. A KWin JavaScript snippet sets `keepAbove`, `skipTaskbar`, `skipPager`, `skipSwitcher` via D-Bus — injected at 0 / 200 / 600 / 1500 / 3000 ms after launch
3. A persistent `kwinrulesrc` entry covers XWayland and compositor restarts

---

## Configuration

Instance state file: `~/.config/desktop-pet/instances/<id>.state.json`

```json
{
  "name": "nyancat",
  "pack_path": "/home/user/pets/nyancat",
  "scale": 0.15,
  "speed": 1.2,
  "autostart": true,
  "x": 840,
  "y": 980
}
```

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Human-readable identifier |
| `pack_path` | string | Absolute path to animation pack directory |
| `scale` | float | Character height as fraction of screen height (0.05–1.0) |
| `speed` | float | Animation speed multiplier (0.1–10.0) |
| `autostart` | bool | Enable systemd service at login |
| `x`, `y` | int | Last saved window position |

---

## Troubleshooting

### Pet not staying on top (Wayland)

1. Check `dbus-send` is installed: `which dbus-send`
2. Check `.desktop` is in place: `ls ~/.local/share/applications/desktop-pet.desktop`
3. Run directly and watch stderr: `python3 ~/.local/bin/pet_desktop.py ~/pack/ --instance-id test`
4. Reload KWin: `dbus-send --session --dest=org.kde.KWin /KWin org.kde.KWin.reconfigure`

### Pet appears in taskbar (X11)

Check `xprop` is installed and KWin rules are written:
```bash
grep -A8 desktop-pet ~/.config/kwinrulesrc
```

### No animations found

```bash
ls ~/my_character/
# Should show files named idle.gif, walk.gif, etc.
```
Check that at least one file matches a recognized name (see table above).

### High CPU usage

- Use smaller GIFs (fewer frames, smaller canvas)
- Reduce `--scale` so less compositing is needed
- Lower `--speed` slightly

### Autostart not working

```bash
systemctl --user status desktop-pet-<name>-<id>
journalctl --user -u desktop-pet-<name>-<id> -n 50
```

---

## Uninstallation

```bash
./uninstall.sh                  # full removal
./uninstall.sh --keep-config    # keep your pet list
```

KWin rules in `~/.config/kwinrulesrc` are not removed automatically. Remove the `[desktop-pet]` section manually if desired.

---

## License

Desktop Pet Manager is free software: you can redistribute it and/or modify it under the terms of the **GNU General Public License v3.0** or later.

See [LICENSE](LICENSE) or <https://www.gnu.org/licenses/> for the full text.
