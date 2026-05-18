#!/usr/bin/env python3
# pet_desktop.py — Desktop Pet overlay for Linux (KDE Wayland + X11)
# Ported from gif_desktop.py by Nassiri — same window-management approach,
# extended with physics, state machine, and animation pack support.
#
# GPL v3 — see <https://www.gnu.org/licenses/>

"""
Key fixes over the previous version:
  - Drag uses windowHandle().startSystemMove()  → works on both X11 and Wayland
  - Floor = screen BOTTOM (screen_h - char_h), not top
  - Window flags copied verbatim from gif_desktop: FramelessWindowHint +
    WindowStaysOnTopHint only (Tool flag removed — it caused taskbar issues on
    some DEs; skip-taskbar is handled by KWin rules + X11 hints instead)
  - WA_ShowWithoutActivating prevents stealing focus
  - KWin script + X11 xprop/wmctrl hints applied with staggered delays
  - setDesktopFileName(APP_ID) sets Wayland app_id for KWin rule matching
  - Taskbar icon: skipped via _NET_WM_STATE_SKIP_TASKBAR + KWin rules
  - Always on top: _NET_WM_STATE_ABOVE + KWin keepAbove rule (force)
"""

import sys, os, io, json, argparse, subprocess, shutil, configparser, tempfile
from pathlib import Path
from enum import Enum, auto

try:
    from PIL import Image
except ImportError:
    print("ERROR: Pillow required.  pip3 install Pillow", file=sys.stderr); sys.exit(1)

try:
    from PyQt6.QtWidgets import QApplication, QWidget, QMenu
    from PyQt6.QtCore    import Qt, QTimer, QPoint
    from PyQt6.QtGui     import QPixmap, QImage, QPainter, QColor
    def _global_pos(e): return e.globalPosition().toPoint()
    def _local_pos(e):  return e.position().toPoint()
except ImportError:
    try:
        from PySide6.QtWidgets import QApplication, QWidget, QMenu
        from PySide6.QtCore    import Qt, QTimer, QPoint
        from PySide6.QtGui     import QPixmap, QImage, QPainter, QColor
        def _global_pos(e): return e.globalPosition().toPoint()
        def _local_pos(e):  return e.position().toPoint()
    except ImportError:
        print("ERROR: PyQt6 or PySide6 required.", file=sys.stderr); sys.exit(1)

APP_ID    = "desktop-pet"
STATE_DIR = os.path.expanduser("~/.config/desktop-pet/instances")
KWIN_RULES = Path.home() / ".config" / "kwinrulesrc"


# ── State ─────────────────────────────────────────────────────────────────────
class S(Enum):
    IDLE  = auto()
    WALK  = auto()
    RUN   = auto()
    JUMP  = auto()
    FALL  = auto()
    REACT = auto()
    DRAG  = auto()
    SLEEP = auto()
    LAND  = auto()
    DEATH = auto()
    CLIMB = auto()

STATE_NAMES = {
    S.IDLE:  ["idle","stand","standing","default","neutral"],
    S.WALK:  ["walk","walking","walk_left","move","stroll"],
    S.RUN:   ["run","running","sprint","dash","rush"],
    S.JUMP:  ["jump","jumping","leap","spring","hop"],
    S.FALL:  ["fall","falling","drop","descend"],
    S.REACT: ["react","pet","petting","happy","excited","joy","yay"],
    S.DRAG:  ["drag","grabbed","held","carry","picked","pickup_ground","pickup_wall"],
    S.SLEEP: ["sleep","sleeping","snore","rest","zzz","nap","wait"],
    S.LAND:  ["land","landing","thud","bounce"],
    S.DEATH: ["death","die","kill","dead"],
    S.CLIMB: ["climb","climbing","wall"],
}
ONE_SHOT = {S.REACT, S.LAND, S.DEATH}
STICKY   = {S.REACT, S.DRAG, S.LAND, S.DEATH, S.CLIMB}


# ── Persistence helpers ───────────────────────────────────────────────────────
def _state_path(iid): return os.path.join(STATE_DIR, f"{iid}.state.json")

def load_state(iid):
    try:
        with open(_state_path(iid)) as f: return json.load(f)
    except: return {}

def save_state(iid, data):
    os.makedirs(STATE_DIR, exist_ok=True)
    existing = load_state(iid)
    existing.update(data)
    with open(_state_path(iid), "w") as f: json.dump(existing, f, indent=2)


# ── Desktop file (Wayland app_id matching) ────────────────────────────────────
def ensure_desktop_file():
    d = Path.home() / ".local" / "share" / "applications"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{APP_ID}.desktop"
    if not f.exists():
        f.write_text(
            f"[Desktop Entry]\nName=Desktop Pet\n"
            f"Exec={sys.executable} %F\nIcon=face-smile\n"
            "Type=Application\nCategories=Utility;\n"
            "StartupNotify=false\nNoDisplay=true\n"
        )


# ── KWin rules (persistent fallback) ─────────────────────────────────────────
def setup_kwin_rules():
    cfg = configparser.RawConfigParser(); cfg.optionxform = str
    if KWIN_RULES.exists(): cfg.read(KWIN_RULES)
    sec = next((s for s in cfg.sections()
                if s.isdigit() and cfg.get(s,"wmclass",fallback="")==APP_ID), None)
    if sec is None:
        nums = [int(s) for s in cfg.sections() if s.isdigit()]
        sec  = str(max(nums, default=0)+1); cfg.add_section(sec)
    for k,v in {
        "Description":"Desktop Pet Overlay",
        # Match by window class (XWayland)
        "wmclass":APP_ID,"wmclasscomplete":"false","wmclassmatch":"1",
        # Match by window title as fallback
        "title":APP_ID,"titlematch":"1",
        # Force window type to UTILITY (taskbars usually ignore these)
        "type":"2","typerule":"2",
        # Force always on top
        "above":"true","aboverule":"2",
        # Force skip taskbar, pager, switcher
        "skiptaskbar":"true","skiptaskbarrule":"2",
        "skippager":"true","skippagerrule":"2",
        "skipswitcher":"true","skipswitcherrule":"2",
        # Force on all desktops
        "desktop":"-1","desktoprule":"2",
        # No window decorations
        "noborder":"true","noborderrule":"2",
    }.items(): cfg.set(sec,k,v)
    if not cfg.has_section("General"): cfg.add_section("General")
    cfg.set("General","count",str(len([s for s in cfg.sections() if s.isdigit()])))
    KWIN_RULES.parent.mkdir(parents=True,exist_ok=True)
    with open(KWIN_RULES,"w") as f: cfg.write(f, space_around_delimiters=False)


# ── KWin D-Bus helper (copied from gif_desktop.py) ────────────────────────────
def _dbus(dest, path, method, *args, print_reply=False):
    cmd = ["dbus-send","--session",f"--dest={dest}",path,method]+list(args)
    if print_reply: cmd.insert(2,"--print-reply")
    try: return subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    except: return None

def reconfigure_kwin():
    _dbus("org.kde.KWin","/KWin","org.kde.KWin.reconfigure")

def set_kwin_window_props(wid):
    """Explicitly set KWin window properties via D-Bus script."""
    if not wid: return
    js = r"""
    (function(){{
        var wid = "{wid}";
        var found = false;
        // Plasma 6 renamed clientList → windowList; support both.
        var listFn = workspace.windowList || workspace.clientList;
        listFn.call(workspace).forEach(function(c) {{
            if (c && String(c.internalId || c.id) === wid) {{
                c.skipTaskbar   = true;
                c.skipPager     = true;
                c.skipSwitcher  = true;
                c.keepAbove     = true;
                c.onAllDesktops = true;
                c.noBorder      = true;
                found = true;
                print("[desktop-pet] KWin props set for id=" + wid +
                      " class=" + (c.resourceClass||"?") +
                      " name="  + (c.resourceName ||"?"));
            }}
        }});
        if (!found) {{
            print("[desktop-pet] Window id=" + wid + " not found in KWin");
        }}
    }})();
    """.format(wid=wid)
    fd, path = tempfile.mkstemp(suffix=".js", prefix="desktop-pet-props-")
    try:
        os.write(fd, js.encode()); os.close(fd)
        r = _dbus("org.kde.KWin","/Scripting",
                  "org.kde.kwin.Scripting.loadScript",
                  f"string:{path}","string:desktop-pet-props",
                  print_reply=True)
        if r and r.returncode == 0:
            sid = next((ln.strip().split()[-1]
                        for ln in (r.stdout or "").splitlines() if "int32" in ln), None)
            if sid:
                sp = f"/Scripting/Script{sid}"
                _dbus("org.kde.KWin", sp, "org.kde.kwin.Script.run")
                _dbus("org.kde.KWin", sp, "org.kde.kwin.Script.stop")
    except Exception as e:
        print(f"[kwin-props] {e}", file=sys.stderr)
    finally:
        try: os.unlink(path)
        except: pass

# KWin JS (same template as gif_desktop.py, just APP_ID differs)
# Supports both Plasma 5 (clientList / clientAdded) and
# Plasma 6 (windowList / windowAdded) — the old names were silently absent
# on Plasma 6, so the script did nothing and the taskbar icon leaked through.
_KWIN_JS = r"""
(function(){{
  var APP="{app_id}"; var FIXED=false;
  function fix(c){{
    if(!c) return;
    var rc  = (c.resourceClass  || "").toLowerCase();
    var rn  = (c.resourceName   || "").toLowerCase();
    var cap = (c.caption         || "").toLowerCase();
    var df  = (c.desktopFileName || c.desktopFile || "")
                .replace(/\.desktop$/i,"").toLowerCase();
    var m = df===APP || rc===APP || rn===APP || cap.indexOf(APP)!==-1;
    if(m){{
      c.keepAbove     = true;
      c.skipTaskbar   = true;
      c.skipPager     = true;
      c.skipSwitcher  = true;
      c.onAllDesktops = true;
      c.noBorder      = true;
      print("[desktop-pet] Fixed: rc="+rc+" rn="+rn+" cap="+cap+" df="+df);
      FIXED = true;
    }}
  }}
  var listFn   = workspace.windowList   || workspace.clientList;
  var addedSig = workspace.windowAdded  || workspace.clientAdded;
  listFn.call(workspace).forEach(fix);
  if(!FIXED){{
    print("[desktop-pet] Waiting for window via windowAdded/clientAdded...");
    var conn = addedSig.connect(function(c){{
      fix(c);
      if(FIXED){{ addedSig.disconnect(conn); }}
    }});
  }}
}})();
"""

def run_kwin_script():
    import time
    js = _KWIN_JS.format(app_id=APP_ID)
    fd, path = tempfile.mkstemp(suffix=".js", prefix="desktop-pet-")
    script_name = f"desktop-pet-fix-{int(time.time()*1000)%100000}"
    try:
        os.write(fd, js.encode()); os.close(fd)
        r = _dbus("org.kde.KWin","/Scripting",
                  "org.kde.kwin.Scripting.loadScript",
                  f"string:{path}",f"string:{script_name}",
                  print_reply=True)
        if not r or r.returncode != 0:
            print(f"[kwin] loadScript failed: {r.stderr if r else 'no reply'}", file=sys.stderr)
            return False
        sid = next((ln.strip().split()[-1]
                    for ln in (r.stdout or "").splitlines() if "int32" in ln), None)
        if not sid:
            print(f"[kwin] Could not parse script id from: {r.stdout}", file=sys.stderr)
            return False
        sp = f"/Scripting/Script{sid}"
        r2 = _dbus("org.kde.KWin", sp, "org.kde.kwin.Script.run")
        if r2 and r2.returncode == 0:
            print(f"[kwin] Script {script_name} (id={sid}) executed", file=sys.stderr)
        else:
            print(f"[kwin] Script run failed: {r2.stderr if r2 else 'no reply'}", file=sys.stderr)
        _dbus("org.kde.KWin", sp, "org.kde.kwin.Script.stop")
        return r2 is not None and r2.returncode == 0
    except Exception as e:
        print(f"[kwin] {e}", file=sys.stderr); return False
    finally:
        try: os.unlink(path)
        except: pass


# ── X11 / XWayland hints (copied from gif_desktop.py) ────────────────────────
def _run(*cmd):
    try: return subprocess.run(list(cmd), capture_output=True, timeout=3).returncode == 0
    except: return False

def _get_current_desktop():
    """Return the current virtual desktop index (0-based) via xprop."""
    try:
        r = subprocess.run(
            ["xprop", "-root", "_NET_CURRENT_DESKTOP"],
            capture_output=True, text=True, timeout=3
        )
        if r.returncode == 0:
            # output looks like: _NET_CURRENT_DESKTOP(CARDINAL) = 0
            parts = r.stdout.strip().split("=")
            if len(parts) == 2:
                return int(parts[1].strip())
    except Exception:
        pass
    return 0

def apply_x11_hints(wid):
    if not wid: return
    wh = hex(wid)
    if shutil.which("xprop"):
        _run("xprop","-id",wh,"-f","_NET_WM_STATE","32a","-set",
             "_NET_WM_STATE",
             "_NET_WM_STATE_SKIP_TASKBAR, _NET_WM_STATE_SKIP_PAGER, _NET_WM_STATE_ABOVE")
        _run("xprop","-id",wh,"-f","_NET_WM_WINDOW_TYPE","32a","-set",
             "_NET_WM_WINDOW_TYPE","_NET_WM_WINDOW_TYPE_UTILITY")
        # Set WM_CLASS for KWin rule matching
        _run("xprop","-id",wh,"-f","WM_CLASS","8s","-set",
             "WM_CLASS",f"{APP_ID}\0{APP_ID}\0")
        # Pin to current desktop so the pet does NOT follow to other desktops.
        # _NET_WM_DESKTOP = 0xFFFFFFFF means "all desktops" (sticky);
        # any other value pins the window to that specific desktop.
        current_desk = _get_current_desktop()
        _run("xprop","-id",wh,"-f","_NET_WM_DESKTOP","32c","-set",
             "_NET_WM_DESKTOP", str(current_desk))
    if shutil.which("wmctrl"):
        # remove sticky, add above + skip flags
        _run("wmctrl","-i","-r",wh,"-b","remove,sticky")
        _run("wmctrl","-i","-r",wh,"-b","add,above,skip_taskbar,skip_pager")


# ── PIL → QPixmap ─────────────────────────────────────────────────────────────
def _pil_to_qpixmap(img):
    buf = io.BytesIO()
    img.convert("RGBA").save(buf, format="PNG")
    pm = QPixmap(); pm.loadFromData(buf.getvalue()); return pm


# ── Animation pack ────────────────────────────────────────────────────────────
class Anim:
    __slots__ = ("frames","delays")
    def __init__(self, frames, delays): self.frames=frames; self.delays=delays

class Pack:
    def __init__(self, path):
        self.path   = Path(path).expanduser().resolve()
        self._data  = {}
        self._real  = set()
        self.nat_w  = 0
        self.nat_h  = 0
        self._load()

    def _load(self):
        if not self.path.exists():
            raise FileNotFoundError(self.path)
        exts = {".gif",".png",".webp",".jpg",".jpeg"}
        idx  = {}
        for f in self.path.iterdir():
            if f.is_file() and f.suffix.lower() in exts:
                clean_name = f.stem.lower().replace("female_", "").replace("male_", "").replace("female", "").strip()
                idx[clean_name] = f

        raw_anims = {}
        min_l, min_t, max_r, max_b = 9999, 9999, 0, 0
        has_bbox = False

        for state, names in STATE_NAMES.items():
            for nm in names:
                if nm in idx:
                    try:
                        img = Image.open(idx[nm])
                        n = getattr(img, "n_frames", 1)
                        frames, delays = [], []
                        for i in range(n):
                            try: img.seek(i)
                            except EOFError: break
                            rgba = img.convert("RGBA")
                            frames.append(rgba)
                            delays.append(max(img.info.get("duration",100), 20))
                            bbox = rgba.split()[-1].getbbox()
                            if bbox:
                                min_l = min(min_l, bbox[0])
                                min_t = min(min_t, bbox[1])
                                max_r = max(max_r, bbox[2])
                                max_b = max(max_b, bbox[3])
                                has_bbox = True
                        if frames:
                            raw_anims[state] = (frames, delays)
                            self._real.add(state)
                            print(f"[pack] {state.name:<8} ← {idx[nm].name}", file=sys.stderr)
                    except Exception as e:
                        print(f"[pack] {idx[nm].name}: {e}", file=sys.stderr)
                    break

        if not raw_anims:
            raise ValueError(f"No usable images in {self.path}")

        if not has_bbox:
            first_frame = next(iter(raw_anims.values()))[0][0]
            min_l, min_t = 0, 0
            max_r, max_b = first_frame.size

        self.nat_w = max_r - min_l
        self.nat_h = max_b - min_t

        for state, (frames, delays) in raw_anims.items():
            qframes = []
            for rgba in frames:
                cropped = rgba.crop((min_l, min_t, max_r, max_b))
                qframes.append(_pil_to_qpixmap(cropped))
            self._data[state] = Anim(qframes, delays)

        fb = self._data.get(S.IDLE) or next(iter(self._data.values()))
        for s in S:
            if s not in self._data: self._data[s] = fb

    def get(self, s): return self._data[s]
    def has(self, s): return s in self._real


# ── Overlay widget ────────────────────────────────────────────────────────────
import random, math

class PetOverlay(QWidget):
    GRAVITY    = 1300.0
    WALK_SPEED = 120.0
    RUN_SPEED  = 280.0
    JUMP_VEL   = -550.0
    SLEEP_AFTER = 20.0

    _REAL_MODES = ("lazy", "neutral", "speeding")

    def __init__(self, pack_path, scale, mode, instance_id):
        super().__init__()
        self.instance_id  = instance_id
        self.mode         = mode
        self.speed_mult   = 1.0
        self.pack         = Pack(pack_path)
        # _active_mode is what the AI actually uses; for "random" it is
        # re-rolled periodically, for all other modes it just equals self.mode.
        self._active_mode = self._roll_mode(announce=False)

        # ── Animation ─────────────────────────────────────────────────────
        self._state    = S.IDLE
        self._frame    = 0
        self._accum    = 0        # ms since last frame advance
        self._timer    = QTimer(self); self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._next_frame)

        # ── Physics ───────────────────────────────────────────────────────
        self.px = 0.0; self.py = 0.0
        self.vx = 0.0; self.vy = 0.0
        self.on_ground    = True
        self.facing_right = True

        # ── Behavior ──────────────────────────────────────────────────────
        self.idle_secs   = 0.0
        self.action_secs = 0.0
        self.action_dur  = 0.0

        # ── Drag ──────────────────────────────────────────────────────────
        self._dragging    = False
        self._drag_last_x = 0.0
        self._drag_last_y = 0.0
        self._drag_vx     = 0.0
        self._drag_vy     = 0.0

        # ── Screen & size ─────────────────────────────────────────────────
        self.cw = 0
        self.ch = 0
        self._update_screen()

        nw, nh   = self.pack.nat_w, self.pack.nat_h
        target_h = max(32, int(self.scr_h * scale))
        ratio    = target_h / nh if nh else 1.0
        self.cw  = max(32, int(nw * ratio))
        self.ch  = target_h
        self._update_screen() # update again now that ch is set

        st = load_state(instance_id)
        default_x = self.scr_x + (self.scr_w - self.cw) // 2
        default_y = self.scr_y - self.ch

        self.px = float(st.get("x", default_x))
        self.py = float(st.get("y", default_y))

        if self.py >= self.floor_y - 2:
            self.py = float(self.floor_y)
            self.on_ground = True
        else:
            self.on_ground = False

        self._clamp()
        self.move(int(self.px), int(self.py))
        print(f"[init] pos=({self.px},{self.py}) size=({self.cw},{self.ch}) "
              f"screen=({self.scr_w}x{self.scr_h}) floor_y={self.floor_y}", file=sys.stderr)

        # ── Window flags ───────────────────────────────────────────
        # X11BypassWindowManagerHint sets override_redirect=True, which makes
        # KWin (and the Plasma taskbar) completely ignore this window — no
        # taskbar entry, no pager, no Alt-Tab. Trade-off: the WM cannot assign
        # the window to a virtual desktop, so it would normally appear on all
        # desktops. We solve this ourselves: _home_desktop records which desktop
        # was active at launch, and _check_desktop() polls _NET_CURRENT_DESKTOP
        # every 300 ms to hide/show the pet as the user switches desktops.
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.X11BypassWindowManagerHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground,    True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent,      False)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        self.setWindowTitle(APP_ID)
        self.resize(self.cw, self.ch)
        self.move(int(self.px), int(self.py))
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)

        # Record which desktop was active at launch.
        self._home_desktop = _get_current_desktop()

        # ── Timers ────────────────────────────────────────────────────────
        self._phys_t = QTimer(self); self._phys_t.timeout.connect(self._tick_physics)
        self._phys_t.start(16)

        self._beh_t = QTimer(self); self._beh_t.timeout.connect(self._tick_behavior)
        self._beh_t.start(200)

        self._save_t = QTimer(self); self._save_t.timeout.connect(self._persist)
        self._save_t.start(5000)

        # override_redirect windows are not WM-managed, so raise_() ourselves.
        self._raise_t = QTimer(self); self._raise_t.timeout.connect(self.raise_)
        self._raise_t.start(500)

        # Watch for virtual desktop switches and hide/show accordingly.
        self._desk_t = QTimer(self); self._desk_t.timeout.connect(self._check_desktop)
        self._desk_t.start(300)

        # Random-mode re-roller: fires every 30–90 s and picks a new sub-mode.
        if self.mode == "random":
            self._mode_t = QTimer(self)
            self._mode_t.timeout.connect(self._reroll_mode)
            self._mode_t.start(int(random.uniform(30_000, 90_000)))
        else:
            self._mode_t = None

        self._start_anim()

    # ── showEvent ────────────────────────────────────────────────────
    def showEvent(self, event):
        super().showEvent(event)
        self.raise_()
        try:
            wid = int(self.winId())
            if wid:
                apply_x11_hints(wid)
        except Exception as e:
            print(f"[showEvent] x11 hints failed: {e}", file=sys.stderr)

    # ── Virtual desktop tracking ──────────────────────────────────────
    def _check_desktop(self):
        """Hide/show the pet based on which virtual desktop is active."""
        current = _get_current_desktop()
        if self._dragging:
            # While dragging, follow the user: if they switch desktop mid-drag,
            # update home so the pet stays visible on the new desktop.
            if current != self._home_desktop:
                self._home_desktop = current
            return
        if current == self._home_desktop:
            if not self.isVisible():
                self.show()
                self.raise_()
        else:
            if self.isVisible():
                self.hide()

    # ── Animation ─────────────────────────────────────────────────────────────
    def _start_anim(self):
        a = self.pack.get(self._state)
        if not a.frames: return
        self._frame = 0
        delay = max(10, int(a.delays[0] / self.speed_mult))
        self._timer.start(delay)
        self.update()

    def _set_state(self, s):
        if s == self._state: return
        self._state = s; self._frame = 0
        self._start_anim()

    def _next_frame(self):
        a = self.pack.get(self._state)
        n = len(a.frames)
        if not n: return
        self._frame = (self._frame + 1) % n
        # one-shot: played through → back to IDLE
        if self._frame == 0 and self._state in ONE_SHOT:
            if self._state == S.DEATH:
                QApplication.instance().quit()
                return
            self._set_state(S.IDLE); return
        self.update()
        delay = max(10, int(a.delays[self._frame] / self.speed_mult))
        self._timer.start(delay)

    # ── Physics tick ──────────────────────────────────────────────────────────
    def _tick_physics(self):
        if self._dragging: return
        dt = 0.016

        # Update screen geometry in case the window moved to another monitor.
        self._update_screen()

        if not self.on_ground:
            if self._state != S.CLIMB:
                self.vy += self.GRAVITY * dt
            if self._state not in STICKY:
                self._set_state(S.FALL if self.vy > 0 else S.JUMP)

        self.px += self.vx * dt
        self.py += self.vy * dt

        # Floor — BOTTOM of screen.  Snap with a small epsilon.
        if self.py >= self.floor_y - 1.0:
            was_falling = not self.on_ground and self.vy > 200
            self.py     = float(self.floor_y)
            self.vy     = 0.0
            self.on_ground = True
            if self._state != S.DEATH:
                if was_falling and self.pack.has(S.LAND):
                    self._set_state(S.LAND)
                elif self._state in (S.FALL, S.JUMP):
                    self._set_state(S.IDLE)
        else:
            self.on_ground = False

        # Get overall virtual desktop bounds
        v_min_x = min(s.geometry().x() for s in QApplication.screens())
        v_max_x = max(s.geometry().x() + s.geometry().width() for s in QApplication.screens()) - self.cw
        v_min_y = min(s.geometry().y() for s in QApplication.screens())

        # Side edges — bounce against the overall desktop edges
        if self.px <= v_min_x:
            self.px = float(v_min_x)
            if self._active_mode == "speeding" and self.pack.has(S.CLIMB) and self._state != S.CLIMB and self.on_ground and random.random() < 0.6:
                self._set_state(S.CLIMB)
                self.vx = 0.0; self.vy = -self.RUN_SPEED * 0.8
                self.on_ground = False; self.facing_right = False
            elif self._state != S.CLIMB:
                self.vx = abs(self.vx)*0.6; self.facing_right = True
        elif self.px >= v_max_x:
            self.px = float(v_max_x)
            if self._active_mode == "speeding" and self.pack.has(S.CLIMB) and self._state != S.CLIMB and self.on_ground and random.random() < 0.6:
                self._set_state(S.CLIMB)
                self.vx = 0.0; self.vy = -self.RUN_SPEED * 0.8
                self.on_ground = False; self.facing_right = True
            elif self._state != S.CLIMB:
                self.vx = -abs(self.vx)*0.6; self.facing_right = False

        if self._state == S.CLIMB:
            # Random chance to jump off wall, or jump if reached top of screen
            if self.py <= v_min_y or random.random() < 0.005:
                if self.py <= v_min_y: self.py = float(v_min_y)
                self.vy = self.JUMP_VEL * 0.5
                self.vx = self.RUN_SPEED * (1 if self.px <= v_min_x else -1)
                self._set_state(S.JUMP)
                self.facing_right = self.vx > 0

        # Friction when idle/sleeping on ground
        if self.on_ground and self._state in (S.IDLE, S.SLEEP, S.REACT, S.LAND):
            self.vx *= 0.75

        self.move(int(self.px), int(self.py))

    # ── Behavior AI ───────────────────────────────────────────────────────────
    def _tick_behavior(self):
        if self._dragging or self._state in STICKY or not self.on_ground: return
        dt = 0.2
        self.idle_secs   += dt
        self.action_secs += dt

        if self.idle_secs > self.SLEEP_AFTER and self._state != S.SLEEP:
            self._set_state(S.SLEEP); self.vx = 0.0; return

        if self.action_secs >= self.action_dur:
            self._pick_action()

    # ── Mode helpers ──────────────────────────────────────────────────────────
    def _roll_mode(self, announce=True):
        """Pick (or keep) the active sub-mode.  Returns the chosen mode string."""
        if self.mode == "random":
            chosen = random.choice(self._REAL_MODES)
        else:
            chosen = self.mode
        if announce:
            print(f"[mode] random → {chosen}", file=sys.stderr)
        return chosen

    def _reroll_mode(self):
        """Called by the periodic timer to switch to a new random sub-mode."""
        self._active_mode = self._roll_mode()
        # Re-schedule with a fresh random interval so switches aren't clockwork.
        if self._mode_t:
            self._mode_t.start(int(random.uniform(30_000, 90_000)))

    def _pick_action(self):
        self.action_secs = 0.0
        r = random.random()

        if self._active_mode == "lazy":
            if r < 0.40:
                self._set_state(S.IDLE); self.vx=0.0; self.action_dur=random.uniform(2.0,6.0)
            elif r < 0.60:
                self._set_state(S.SLEEP); self.vx=0.0; self.action_dur=random.uniform(5.0,15.0)
            elif r < 0.80:
                self.facing_right = random.random()>0.5
                self._set_state(S.WALK)
                self.vx = self.WALK_SPEED * 0.5 * (1 if self.facing_right else -1)
                self.action_dur=random.uniform(1.0,3.0); self.idle_secs=0.0
            else:
                self._set_state(S.IDLE); self.vx=0.0; self.action_dur=random.uniform(2.0,5.0)

        elif self._active_mode == "speeding":
            if r < 0.10:
                self._set_state(S.IDLE); self.vx=0.0; self.action_dur=random.uniform(0.5,1.5)
            elif r < 0.40:
                self.facing_right = random.random()>0.5
                self._set_state(S.RUN)
                self.vx = self.RUN_SPEED * 1.5 * (1 if self.facing_right else -1)
                self.action_dur=random.uniform(1.5,4.0); self.idle_secs=0.0
            elif r < 0.70:
                self._do_jump(vx_boost=random.choice([-1,1])*random.uniform(100,200))
                self.action_dur=2.5
            elif r < 0.90:
                self.facing_right = random.random()>0.5
                self._set_state(S.WALK)
                self.vx = self.WALK_SPEED * 1.2 * (1 if self.facing_right else -1)
                self.action_dur=random.uniform(1.0,2.0); self.idle_secs=0.0
            else:
                self.facing_right = not self.facing_right
                self._set_state(S.RUN)
                self.vx = self.RUN_SPEED * 1.5 * (1 if self.facing_right else -1)
                self.action_dur=random.uniform(1.0,2.0); self.idle_secs=0.0

        else: # neutral
            if r < 0.30:
                self._set_state(S.IDLE); self.vx=0.0; self.action_dur=random.uniform(1.5,4.0)
            elif r < 0.55:
                self.facing_right = random.random()>0.5
                self._set_state(S.WALK)
                self.vx = self.WALK_SPEED*(1 if self.facing_right else -1)
                self.action_dur=random.uniform(1.0,3.5); self.idle_secs=0.0
            elif r < 0.70:
                self.facing_right = random.random()>0.5
                self._set_state(S.RUN)
                self.vx = self.RUN_SPEED*(1 if self.facing_right else -1)
                self.action_dur=random.uniform(0.6,2.0); self.idle_secs=0.0
            elif r < 0.82:
                self._do_jump(); self.action_dur=2.5
            elif r < 0.90:
                self.facing_right = not self.facing_right
                self._set_state(S.WALK)
                self.vx = self.WALK_SPEED*(1 if self.facing_right else -1)
                self.action_dur=random.uniform(0.8,2.0); self.idle_secs=0.0
            else:
                self._set_state(S.IDLE); self.vx=0.0; self.action_dur=random.uniform(3.0,6.0)

    def _do_jump(self, vx_boost=0.0):
        if not self.on_ground: return
        self._set_state(S.JUMP)
        self.vy = self.JUMP_VEL
        self.vx = vx_boost or random.choice([-1,1])*random.uniform(50,120)
        self.facing_right = self.vx > 0
        self.on_ground    = False
        self.idle_secs    = 0.0

    # ── Paint ─────────────────────────────────────────────────────────────────
    def paintEvent(self, _):
        a = self.pack.get(self._state)
        if not a.frames: return
        pm = a.frames[self._frame % len(a.frames)]
        scaled = pm.scaled(self.cw, self.ch,
                           Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
        ox = (self.cw - scaled.width())  // 2
        oy = (self.ch - scaled.height()) // 2

        qp = QPainter(self)
        qp.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        qp.fillRect(self.rect(), QColor(0,0,0,0))
        qp.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        if not self.facing_right:
            qp.translate(self.cw, 0); qp.scale(-1.0, 1.0)
        qp.drawPixmap(ox, oy, scaled)
        qp.end()

    # ── Mouse — manual drag (override_redirect bypasses startSystemMove) ────
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._dragging      = True
            self._drag_vx       = 0.0
            self._drag_vy       = 0.0
            gpos = _global_pos(e)
            self._drag_start_x  = float(gpos.x())
            self._drag_start_y  = float(gpos.y())
            # Offset of cursor relative to window top-left.
            self._drag_offset_x = gpos.x() - self.pos().x()
            self._drag_offset_y = gpos.y() - self.pos().y()
            self._prev_poll_x   = self.pos().x()
            self._prev_poll_y   = self.pos().y()
            self._set_state(S.DRAG)
            self.vx = 0.0; self.vy = 0.0
            self.idle_secs = 0.0
            self._drag_t = QTimer(self)
            self._drag_t.timeout.connect(self._drag_poll)
            self._drag_t.start(20)
            self.grabMouse()   # capture events even when cursor leaves widget

    def mouseMoveEvent(self, e):
        if not self._dragging:
            return
        gpos  = _global_pos(e)
        new_x = gpos.x() - self._drag_offset_x
        new_y = gpos.y() - self._drag_offset_y
        self.px = float(new_x)
        self.py = float(new_y)
        self.move(new_x, new_y)

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or not self._dragging: return

        dx = abs(_global_pos(e).x() - self._drag_start_x)
        dy = abs(_global_pos(e).y() - self._drag_start_y)

        self._end_drag()

        # If it was just a quick click without moving much, react
        if dx < 5 and dy < 5:
            self._set_state(S.REACT)
            self.vx=0.0; self.idle_secs=0.0
            self.action_secs=0.0; self.action_dur=3.0

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._set_state(S.REACT)
            self.vx=0.0; self.idle_secs=0.0
            self.action_secs=0.0; self.action_dur=3.0

    def _drag_poll(self):
        """Poll physical mouse button state to detect drop reliably on XCB."""
        if not self._dragging:
            return

        curr_pos = self.pos()
        self._drag_vx = float(curr_pos.x() - getattr(self, '_prev_poll_x', curr_pos.x()))
        self._drag_vy = float(curr_pos.y() - getattr(self, '_prev_poll_y', curr_pos.y()))
        self._prev_poll_x = curr_pos.x()
        self._prev_poll_y = curr_pos.y()

        buttons = QApplication.mouseButtons()
        if not (buttons & Qt.MouseButton.LeftButton):
            self._end_drag()

    def _end_drag(self):
        """Clean up after drag ends — called by mouseReleaseEvent or poll."""
        if not self._dragging:
            return
        self._dragging = False
        self.releaseMouse()
        if hasattr(self, '_drag_t') and self._drag_t.isActive():
            self._drag_t.stop()
        # Sync final position from window manager (which handled startSystemMove)
        pos = self.pos()
        self.px = float(pos.x())
        self.py = float(pos.y())
        # Apply throw velocity (based on 20ms poll intervals)
        self.vx = self._drag_vx * 50.0
        self.vy = self._drag_vy * 50.0
        # Clamp to prevent flying away at lightspeed
        self.vx = max(-1500.0, min(1500.0, self.vx))
        self.vy = max(-1500.0, min(1500.0, self.vy))
        # Determine ground state using CURRENT screen floor
        self._update_screen()
        self.on_ground = (self.py >= self.floor_y - 2)
        if not self.on_ground:
            self._set_state(S.FALL if self.vy >= 0 else S.JUMP)
        else:
            self.py = float(self.floor_y)
            self.vy = 0.0
            self.vx = 0.0
            self._set_state(S.IDLE)
        self.idle_secs = 0.0
        self._persist()

    def _show_menu(self, pos):
        m = QMenu(self)
        m.addAction("Jump",         lambda: self._do_jump())
        m.addAction("React / Pet",  lambda: self.mouseDoubleClickEvent(
            type('E', (), {'button': lambda s: Qt.MouseButton.LeftButton})()))
        m.addAction("Wake up",      lambda: [self._set_state(S.IDLE),
                                              setattr(self,'idle_secs',0.0)])
        m.addSeparator()
        m.addAction("Reset position", self._reset_pos)
        m.addSeparator()
        m.addAction("Quit", QApplication.instance().quit)
        m.exec(self.mapToGlobal(pos))

    def _reset_pos(self):
        self.px = float(self.scr_x + (self.scr_w - self.cw) // 2)
        self.py = float(self.scr_y - self.ch)  # Fall from top
        self.vx = 0.0; self.vy = 0.0; self.on_ground = False
        self._set_state(S.FALL)
        self.move(int(self.px), int(self.py))

    def _update_screen(self):
        """Update scr_w/scr_h/floor_y from the screen the window is currently on."""
        screen = QApplication.screenAt(QPoint(int(self.px + self.cw / 2), int(self.py + self.ch / 2)))
        if not screen:
            wh = self.windowHandle()
            if wh and wh.screen():
                screen = wh.screen()
            else:
                screen = QApplication.primaryScreen()

        geo = screen.geometry()
        self.scr_x = geo.x()
        self.scr_y = geo.y()
        self.scr_w = geo.width()
        self.scr_h = geo.height()
        self.floor_y = self.scr_y + self.scr_h - getattr(self, 'ch', 0)

    def _clamp(self):
        self.px = max(float(self.scr_x), min(float(self.scr_x + self.scr_w - self.cw), self.px))
        self.py = max(float(self.scr_y - self.ch * 2), min(float(self.floor_y), self.py))

    def _persist(self):
        pos = self.pos()
        save_state(self.instance_id, {"x": pos.x(), "y": pos.y()})


    def die_and_quit(self):
        if self._state == S.DEATH: return
        self._set_state(S.DEATH)
        self.vx = 0.0
        self.vy = 0.0

# ── Entry point ───────────────────────────────────────────────────────────────
def _handle_sigterm(signum, frame):
    app = QApplication.instance()
    if not app: return
    for w in app.topLevelWidgets():
        if isinstance(w, PetOverlay):
            w.die_and_quit()
            return
    app.quit()

def main():
    import signal
    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)

    p = argparse.ArgumentParser(description="Desktop Pet overlay")
    p.add_argument("pack_path")
    p.add_argument("--instance-id",  default="default")
    p.add_argument("--scale",  type=float, default=0.15)
    p.add_argument("--mode",  default="neutral")
    a = p.parse_args()

    # Force XWayland backend when running in a Wayland session
    # (Wayland prevents global window positioning via self.move())
    if os.environ.get("WAYLAND_DISPLAY"):
        os.environ["QT_QPA_PLATFORM"] = "xcb"
        print("[init] Wayland session detected, forcing XWayland (xcb) to allow self.move()", file=sys.stderr)

    ensure_desktop_file()
    setup_kwin_rules()

    sys.argv[0] = APP_ID
    app = QApplication(sys.argv)
    app.setApplicationName(APP_ID)
    app.setApplicationDisplayName("Desktop Pet")
    app.setDesktopFileName(APP_ID)   # ← sets Wayland app_id

    print(f"[init] Creating pet: pack={a.pack_path} scale={a.scale} mode={a.mode}", file=sys.stderr)
    pet = PetOverlay(a.pack_path, a.scale, a.mode, a.instance_id)


    print(f"[init] Pet created, calling show()", file=sys.stderr)
    pet.show()
    print(f"[init] show() called, entering event loop", file=sys.stderr)
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
