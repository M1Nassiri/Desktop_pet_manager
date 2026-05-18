#!/usr/bin/env python3
# pet_manager.py — Desktop Pet Manager TUI
# Copyright (C) 2026 — GPL v3
#
# Terminal UI for managing desktop pet instances.
# Launch with:  desktop-pet   or   python3 pet_manager.py

import curses
import os
import sys
from pathlib import Path

try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import pet_config as cfg
except ImportError:
    print("Error: pet_config.py not found.", file=sys.stderr)
    sys.exit(1)


# ── UI Constants ──────────────────────────────────────────────────────────────
CH_V  = "│"
CH_H  = "─"
CH_TL = "╭"
CH_TR = "╮"
CH_BL = "╰"
CH_BR = "╯"

(C_DEFAULT, C_PRIMARY, C_ACCENT, C_RUNNING, C_STOPPED,
 C_DIM, C_BORDER, C_SELECT, C_HEADER, C_WARN) = range(1, 11)


def init_colors():
    curses.start_color()
    curses.use_default_colors()
    if curses.can_change_color() and curses.COLORS >= 256:
        curses.init_pair(C_DIM,    244, -1)
        curses.init_pair(C_BORDER, 239, -1)
        curses.init_pair(C_HEADER, 232, 248)
        curses.init_pair(C_WARN,   214, -1)
    else:
        curses.init_pair(C_DIM,    curses.COLOR_WHITE, -1)
        curses.init_pair(C_BORDER, curses.COLOR_WHITE, -1)
        curses.init_pair(C_HEADER, curses.COLOR_BLACK, curses.COLOR_WHITE)
        curses.init_pair(C_WARN,   curses.COLOR_YELLOW, -1)

    curses.init_pair(C_PRIMARY, curses.COLOR_CYAN,    -1)
    curses.init_pair(C_ACCENT,  curses.COLOR_MAGENTA, -1)
    curses.init_pair(C_RUNNING, curses.COLOR_GREEN,   -1)
    curses.init_pair(C_STOPPED, curses.COLOR_RED,     -1)
    curses.init_pair(C_SELECT,  curses.COLOR_BLACK,   curses.COLOR_CYAN)


# ── Drawing helpers ───────────────────────────────────────────────────────────
def safe_add(win, y, x, s, attr=0):
    h, w = win.getmaxyx()
    if 0 <= y < h and 0 <= x < w:
        try:
            win.addstr(y, x, s[: w - x - 1], attr)
        except curses.error:
            pass


def draw_box(win, y, x, h, w, title="", color=C_BORDER):
    attr = curses.color_pair(color)
    # Fill background
    for i in range(h):
        safe_add(win, y + i, x, " " * w, attr)
    # Borders
    safe_add(win, y,       x, CH_TL + CH_H * (w - 2) + CH_TR, attr)
    for i in range(1, h - 1):
        safe_add(win, y + i, x,     CH_V, attr)
        safe_add(win, y + i, x + w - 1, CH_V, attr)
    safe_add(win, y + h - 1, x, CH_BL + CH_H * (w - 2) + CH_BR, attr)
    if title:
        safe_add(win, y, x + 2, f" {title} ", attr | curses.A_BOLD)


def prompt_field(win, y, x, label, default="", max_len=50):
    safe_add(win, y,     x, label, curses.color_pair(C_DIM))
    safe_add(win, y + 1, x, "> ",  curses.color_pair(C_ACCENT))
    if default:
        safe_add(win, y + 1, x + 2, default, curses.color_pair(C_DIM))
    curses.echo()
    curses.curs_set(1)
    win.refresh()
    try:
        val = win.getstr(y + 1, x + 2, max_len).decode("utf-8").strip()
    except Exception:
        val = ""
    curses.noecho()
    curses.curs_set(0)
    return val if val else default


def yesno_field(win, y, x, label):
    safe_add(win, y, x, f"{label} [y/N]: ", curses.color_pair(C_DIM))
    win.refresh()
    curses.curs_set(1)
    ch = win.getch()
    curses.curs_set(0)
    ans = chr(ch).lower() if 0 < ch < 256 else "n"
    result = ans == "y"
    col = curses.color_pair(C_RUNNING if result else C_STOPPED) | curses.A_BOLD
    safe_add(win, y, x + len(label) + 8, "YES" if result else "NO ", col)
    win.refresh()
    return result


# ── Manager TUI ───────────────────────────────────────────────────────────────
class PetManagerTUI:
    def __init__(self, stdscr):
        self.s         = stdscr
        self.cursor    = 0
        self.offset    = 0
        self.msg       = ""
        self.instances = []
        self.refresh_data()

    def refresh_data(self):
        self.instances = cfg.list_instances()
        if self.cursor >= len(self.instances) and self.instances:
            self.cursor = len(self.instances) - 1

    # ── Draw ─────────────────────────────────────────────────────────────────
    def draw(self):
        self.s.erase()
        h, w = self.s.getmaxyx()

        # Header bar
        hdr = " DESKTOP PET MANAGER "
        info = " scale · mode · autostart "
        safe_add(self.s, 0, 0, " " * w, curses.color_pair(C_HEADER))
        safe_add(self.s, 0, 2, hdr,  curses.color_pair(C_HEADER) | curses.A_BOLD)
        safe_add(self.s, 0, w - len(info) - 2, info, curses.color_pair(C_HEADER))

        list_h = h - 6
        draw_box(self.s, 2, 1, list_h + 2, w - 2, " Pets ", C_BORDER)

        # Column headers
        head = (f"  {'NAME':<14} {'STATUS':<12} {'AUTO':<7} "
                f"{'SCALE':<8} {'MODE':<8} PACK")
        safe_add(self.s, 3, 3, head, curses.color_pair(C_DIM) | curses.A_BOLD)

        if not self.instances:
            msg = "No pets yet.  Press  A  to add one."
            safe_add(self.s, h // 2, (w - len(msg)) // 2, msg,
                     curses.color_pair(C_DIM))
        else:
            for idx, (iid, inst) in enumerate(self.instances):
                if idx < self.offset or idx >= self.offset + list_h:
                    continue
                row    = 4 + (idx - self.offset)
                is_sel = idx == self.cursor
                sel_a  = curses.color_pair(C_SELECT) if is_sel else 0

                if is_sel:
                    safe_add(self.s, row, 2, " " * (w - 4), sel_a)

                name    = inst.get("name",      f"ID:{iid[:6]}")
                pack    = inst.get("pack_path", "")
                scale   = inst.get("scale",     0.15)
                mode    = inst.get("mode",      "neutral")

                valid = "name" in inst and "pack_path" in inst
                if valid:
                    active = cfg.is_service_active(iid, inst["name"])
                    enabled = cfg.is_service_enabled(iid, inst["name"])
                    status_txt = "● RUNNING" if active  else "○ STOPPED"
                    status_col = C_RUNNING  if active  else C_STOPPED
                    auto_txt   = "✓ ON"     if enabled else "✗ OFF"
                    auto_col   = C_RUNNING  if enabled else C_STOPPED
                else:
                    status_txt, status_col = "✗ INVALID", C_DIM
                    auto_txt, auto_col = "─", C_DIM

                pack_name = Path(pack).name if pack else "[missing]"

                safe_add(self.s, row,  3, f" {name[:13]:<14}", sel_a)
                safe_add(self.s, row, 18, status_txt,
                         sel_a if is_sel else curses.color_pair(status_col))
                safe_add(self.s, row, 31, auto_txt,
                         sel_a if is_sel else curses.color_pair(auto_col))
                safe_add(self.s, row, 39, f"{int(scale * 100)}%", sel_a)
                safe_add(self.s, row, 48, f"{mode:<8}",           sel_a)
                safe_add(self.s, row, 57, pack_name[: w - 60],    sel_a)

        # Key bar
        keys = (" [A] Add  [S] Start/Stop  [E] Autostart  "
                "[D] Delete  [I] Info  [R] Refresh  [Q] Quit")
        safe_add(self.s, h - 2, 2, keys, curses.color_pair(C_PRIMARY))
        if self.msg:
            safe_add(self.s, h - 1, 2, f"» {self.msg}",
                     curses.color_pair(C_ACCENT))

    # ── Main loop ─────────────────────────────────────────────────────────────
    def run(self):
        init_colors()
        curses.curs_set(0)
        while True:
            self.draw()
            self.s.refresh()
            ch = self.s.getch()

            if ch in (ord("q"), 27):
                break
            elif ch in (curses.KEY_UP,   ord("k")):
                self.cursor = max(0, self.cursor - 1)
            elif ch in (curses.KEY_DOWN, ord("j")):
                self.cursor = min(len(self.instances) - 1, self.cursor + 1)
            elif ch == ord("r"):
                self.refresh_data()
                self.msg = "Refreshed."
            elif ch == ord("s"):
                self._toggle()
            elif ch == ord("e"):
                self._toggle_autostart()
            elif ch == ord("d"):
                self._delete()
            elif ch == ord("a"):
                self._add()
            elif ch == ord("i"):
                self._show_info()

            # Scroll
            lh = self.s.getmaxyx()[0] - 6
            if self.cursor < self.offset:
                self.offset = self.cursor
            elif self.cursor >= self.offset + lh:
                self.offset = self.cursor - lh + 1

    # ── Actions ───────────────────────────────────────────────────────────────
    def _toggle(self):
        if not self.instances:
            return
        iid, inst = self.instances[self.cursor]
        if "name" not in inst or "pack_path" not in inst:
            self.msg = f"Instance {iid} is invalid"
            return
        sname = cfg.service_name(iid, inst["name"])
        if cfg.is_service_active(iid, inst["name"]):
            cfg.systemctl(["stop", sname])
            self.msg = f"Stopped {inst['name']}"
        else:
            cfg.write_service(iid, inst)
            cfg.systemctl(["daemon-reload"])
            cfg.systemctl(["start", sname])
            self.msg = f"Started {inst['name']}"
        self.refresh_data()

    def _toggle_autostart(self):
        if not self.instances:
            return
        iid, inst = self.instances[self.cursor]
        if "name" not in inst:
            self.msg = f"Instance {iid} has no name"
            return
        sname   = cfg.service_name(iid, inst["name"])
        enabled = cfg.is_service_enabled(iid, inst["name"])
        if enabled:
            cfg.systemctl(["disable", sname])
            self.msg = f"Autostart OFF for {inst['name']}"
        else:
            cfg.write_service(iid, inst)
            cfg.systemctl(["daemon-reload"])
            cfg.systemctl(["enable", sname])
            self.msg = f"Autostart ON for {inst['name']}"
        self.refresh_data()

    def _delete(self):
        if not self.instances:
            return
        iid, inst = self.instances[self.cursor]
        if "name" not in inst:
            self.msg = f"Instance {iid} has no name"
            return
        sname = cfg.service_name(iid, inst["name"])
        cfg.systemctl(["stop",    sname])
        cfg.systemctl(["disable", sname])
        cfg.delete_instance(iid)
        self.msg = f"Deleted {inst['name']}"
        self.refresh_data()

    def _add(self):
        s = self.s
        h, w = s.getmaxyx()
        bw, bh = min(w - 8, 68), 23
        bx, by = (w - bw) // 2, (h - bh) // 2

        draw_box(s, by, bx, bh, bw, " Add New Desktop Pet ", C_PRIMARY)
        for i in range(1, bh - 1):
            safe_add(s, by + i, bx + 1, " " * (bw - 2), 0)

        # ── Fields ───────────────────────────────────────────────────────
        row = by + 1

        name = prompt_field(s, row, bx + 3,
                            "Pet name  (no spaces):", "my_pet", bw - 8)
        row += 3

        # Show hint about pack structure
        safe_add(s, row, bx + 3,
                 "Animation pack: a folder with idle.gif, walk.gif, run.gif …",
                 curses.color_pair(C_DIM))
        row += 1
        pack_path = prompt_field(s, row, bx + 3,
                                 "Pack directory path:", "~/pets/my_character", bw - 8)
        pack_path = str(Path(pack_path).expanduser().resolve())
        row += 3

        scale = prompt_field(s, row, bx + 3,
                             "Scale  (height as fraction of screen, e.g. 0.15):",
                             "0.15", 8)
        row += 3

        mode = prompt_field(s, row, bx + 3,
                             "AI Behavior Mode  (lazy / neutral / speeding / random):",
                             "neutral", 10).lower()
        if mode not in ("lazy", "neutral", "speeding", "random"):
            mode = "neutral"
        row += 3

        autostart = yesno_field(s, row, bx + 3, "Autostart on login")

        # ── Validate ─────────────────────────────────────────────────────
        if not Path(pack_path).is_dir():
            self.msg = f"Error: directory not found — {pack_path}"
            return

        try:
            scale_f = float(scale)
            scale_f = max(0.05, min(1.0, scale_f))
        except ValueError:
            self.msg = "Error: invalid scale value"
            return

        pack_info = cfg.scan_pack(pack_path)
        if not pack_info["valid"]:
            self.msg = f"Error: no image files in {pack_path}"
            return

        # ── Save and start ────────────────────────────────────────────────
        iid  = cfg.new_instance_id()
        inst = {
            "name":      name,
            "pack_path": pack_path,
            "scale":     scale_f,
            "mode":      mode,
            "autostart": autostart,
        }

        cfg.save_instance(iid, inst)
        cfg.write_service(iid, inst)
        cfg.systemctl(["daemon-reload"])

        sname = cfg.service_name(iid, name)
        if autostart:
            cfg.systemctl(["enable", sname])

        code, out = cfg.systemctl(["start", sname])
        auto_tag  = " + autostart" if autostart else ""

        if code == 0:
            self.msg = f"Added & started '{name}'{auto_tag}  [{len(pack_info['animations'])} anims found]"
        else:
            self.msg = f"Added '{name}' but start failed: {out[:60]}"

        self.refresh_data()

    def _show_info(self):
        if not self.instances:
            return
        iid, inst = self.instances[self.cursor]
        s = self.s
        h, w = s.getmaxyx()
        bw, bh = min(w - 8, 64), 18
        bx, by = (w - bw) // 2, (h - bh) // 2

        draw_box(s, by, bx, bh, bw,
                 f" Info: {inst.get('name', iid)} ", C_ACCENT)
        for i in range(1, bh - 1):
            safe_add(s, by + i, bx + 1, " " * (bw - 2), 0)

        row = by + 2
        dim = curses.color_pair(C_DIM)
        acc = curses.color_pair(C_ACCENT)

        def kv(k, v):
            nonlocal row
            safe_add(s, row, bx + 3, f"{k:<14}", dim)
            safe_add(s, row, bx + 3 + 14, str(v), acc)
            row += 1

        kv("ID",       iid)
        kv("Name",     inst.get("name", "—"))
        kv("Pack",     inst.get("pack_path", "—"))
        kv("Scale",    f"{inst.get('scale', 0.15):.0%}")
        kv("Mode",     inst.get("mode", "neutral"))

        pack_info = cfg.scan_pack(inst.get("pack_path", ""))
        if pack_info["valid"]:
            row += 1
            safe_add(s, row, bx + 3, "Animations found:", dim)
            row += 1
            anims = "  ".join(pack_info["animations"])
            safe_add(s, row, bx + 3, anims[: bw - 8], curses.color_pair(C_PRIMARY))
            row += 1

        row += 1
        safe_add(s, row, bx + 3, "Press any key to close", dim)
        s.refresh()
        s.getch()


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    curses.wrapper(lambda s: PetManagerTUI(s).run())
