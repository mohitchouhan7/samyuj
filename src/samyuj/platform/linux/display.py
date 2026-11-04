from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field

def _run(cmd: list, timeout: float = 6.0) -> tuple[int, str, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()
    except FileNotFoundError:
        return 127, "", f"{cmd[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, "", f"{cmd[0]}: timed out after {timeout}s"
    except Exception as exc:
        return 1, "", repr(exc)

def session_type() -> str:
    xdg = (os.environ.get("XDG_SESSION_TYPE") or "").lower()
    if xdg in ("x11", "wayland"):
        return xdg
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return "unknown"

@dataclass
class VirtualDisplay:
    ok: bool = False
    backend: str = "none"
    name: str = ""
    width: int = 0
    height: int = 0
    region: dict | None = None
    display: str | None = None
    output: str | None = None
    position: str = ""
    attached: bool = False
    root_size: tuple | None = None
    outputs_moved: list = field(default_factory=list)
    detail: str = ""
    hints: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"ok": self.ok, "backend": self.backend, "name": self.name,
                "width": self.width, "height": self.height,
                "region": dict(self.region) if self.region else None,
                "display": self.display, "output": self.output,
                "position": self.position, "attached": self.attached,
                "root_size": list(self.root_size) if self.root_size else None,
                "outputs_moved": list(self.outputs_moved),
                "detail": self.detail, "hints": list(self.hints)}

def _parse_xrandr_screen(txt: str) -> tuple[int, int, int, int]:
    cur_w = cur_h = max_w = max_h = 0
    for line in txt.splitlines():
        if not line.startswith("Screen"):
            continue
        try:
            for part in line.split(","):
                part = part.strip()
                if part.startswith("current"):
                    nums = [int(x) for x in part.split()[1:] if x.isdigit()]
                    if len(nums) >= 2:
                        cur_w, cur_h = nums[0], nums[1]
                elif part.startswith("maximum"):
                    nums = [int(x) for x in part.split()[1:] if x.isdigit()]
                    if len(nums) >= 2:
                        max_w, max_h = nums[0], nums[1]
        except (ValueError, IndexError):
            continue
        break
    return cur_w, cur_h, max_w, max_h

_XRANDR_OUTPUT_RE = None

def _parse_xrandr_outputs(txt: str) -> list:
    global _XRANDR_OUTPUT_RE
    if _XRANDR_OUTPUT_RE is None:
        import re as _re
        _XRANDR_OUTPUT_RE = _re.compile(
            r"^(?P<name>\S+)\s+connected(?P<primary>\s+primary)?\s+"
            r"(?P<w>\d+)x(?P<h>\d+)\+(?P<x>\d+)\+(?P<y>\d+)")
    out = []
    for line in txt.splitlines():
        m = _XRANDR_OUTPUT_RE.match(line)
        if m:
            out.append({"name": m.group("name"),
                        "w": int(m.group("w")), "h": int(m.group("h")),
                        "x": int(m.group("x")), "y": int(m.group("y")),
                        "primary": bool(m.group("primary"))})
    return out

def _root_size(txt: str) -> tuple:
    for line in txt.splitlines():
        line = line.strip()
        if line.startswith("dimensions:"):
            try:
                wh = line.split()[1].split("x")
                return (int(wh[0]), int(wh[1]))
            except (IndexError, ValueError):
                continue
    return (0, 0)

def _xrandr_monitor_exists(txt: str, name: str) -> bool:
    for line in txt.splitlines():
        s = line.strip()
        if s.startswith(f"{name} ") or s == name:
            return True
    return False

class VirtualDisplayManager:

    XVFB_DISPLAY_CANDIDATES = tuple(range(97, 90, -1))

    def __init__(self, monitor_name: str = "SCPHONE", log=None,
                 position: str = "right") -> None:
        self.monitor_name = str(monitor_name or "SCPHONE")[:12]
        self.position = str(position or "right").lower()
        self._moved_outputs: list = []
        self._log = log or (lambda evt, msg: None)
        self._lock = threading.RLock()
        self._cur: VirtualDisplay = VirtualDisplay(detail="not created")
        self._xvfb_proc: subprocess.Popen | None = None
        self._xvfb_display: str | None = None
        self._saved_fb: tuple[int, int] | None = None
        self.last_probe: dict = {}

    def status(self) -> dict:
        with self._lock:
            return self._cur.as_dict()

    @property
    def active(self) -> bool:
        with self._lock:
            return bool(self._cur.ok)

    def probe(self) -> dict:
        st = session_type()
        info = {
            "session": st,
            "xrandr": bool(shutil.which("xrandr")) and st != "wayland",
            "swaymsg": bool(shutil.which("swaymsg")) and st == "wayland",
            "grim": bool(shutil.which("grim")),
            "Xvfb": bool(shutil.which("Xvfb")),
            "Xephyr": bool(shutil.which("Xephyr")),
            "DISPLAY": os.environ.get("DISPLAY") or "",
            "WAYLAND_DISPLAY": os.environ.get("WAYLAND_DISPLAY") or "",
        }
        info["order"] = [b for b, avail in (("xrandr", info["xrandr"]),
                                            ("sway", info["swaymsg"]),
                                            ("xvfb", info["Xvfb"])) if avail]
        self.last_probe = info
        return info

    def set_position(self, position: str) -> None:
        pos = str(position or "right").lower()
        if pos in ("right", "left", "above", "below", "none"):
            self.position = pos

    def ensure(self, width: int, height: int, backend: str = "auto") -> VirtualDisplay:
        w = max(160, int(width) // 2 * 2)
        h = max(160, int(height) // 2 * 2)
        with self._lock:
            if (self._cur.ok and self._cur.width == w and self._cur.height == h
                    and self._cur.position == self.position):
                return self._cur
            if self._cur.ok:
                self._release_locked(why="resize")
            want = (backend or "auto").lower()
            probe = self.probe()
            attempts: list = []
            order = [want] if want in ("xrandr", "sway", "xvfb") else list(probe["order"])
            for b in order:
                if b == "xrandr" and probe["xrandr"]:
                    vd = self._try_xrandr(w, h)
                elif b == "sway" and probe["swaymsg"]:
                    vd = self._try_sway(w, h)
                elif b == "xvfb" and probe["Xvfb"]:
                    vd = self._try_xvfb(w, h)
                else:
                    continue
                attempts.append(f"{b}: {vd.detail}")
                self._log("SS_VIRTUAL_DISPLAY_TRY", f"backend={b} {w}x{h} -> {vd.detail}")
                if vd.ok:
                    if not vd.position:
                        vd.position = self.position
                    self._cur = vd
                    self._log("SS_VIRTUAL_DISPLAY_UP",
                              f"backend={vd.backend} name={vd.name} {vd.width}x{vd.height} "
                              f"region={vd.region} display={vd.display} output={vd.output}")
                    return vd
            hints = []
            if not probe["xrandr"] and probe["session"] == "x11":
                hints.append("install xrandr (sudo apt install x11-xserver-utils) for a real extended monitor")
            if probe["session"] == "wayland" and not probe["swaymsg"]:
                hints.append("on Wayland only wlroots compositors (sway/hyprland) can create a headless output; "
                             "install Xvfb for a nested virtual display instead")
            if not probe["Xvfb"]:
                hints.append("install Xvfb (sudo apt install xvfb) for a nested virtual display")
            vd = VirtualDisplay(ok=False, backend="none", width=w, height=h,
                                detail="; ".join(attempts) or "no virtual-display backend available",
                                hints=hints)
            self._cur = vd
            self._log("SS_VIRTUAL_DISPLAY_FAIL", f"{vd.detail} hints={vd.hints}")
            return vd

    def release(self, why: str = "") -> None:
        with self._lock:
            self._release_locked(why=why)

    def _release_locked(self, why: str = "") -> None:
        cur = self._cur
        if cur.ok and cur.backend == "xrandr":
            _run(["xrandr", "--delmonitor", cur.name])
            if self._moved_outputs:
                restore = ["xrandr"]
                for o in self._moved_outputs:
                    restore += ["--output", o["name"], "--pos", f"{o['x']}x{o['y']}"]
                _run(restore, timeout=12.0)
                self._log("SS_VIRTUAL_DISPLAY_DOWN",
                          f"restored {len(self._moved_outputs)} displaced output(s): "
                          f"{[o['name'] for o in self._moved_outputs]}")
            if self._saved_fb:
                _run(["xrandr", "--fb", f"{self._saved_fb[0]}x{self._saved_fb[1]}"])
            self._moved_outputs = []
            self._log("SS_VIRTUAL_DISPLAY_DOWN", f"xrandr monitor {cur.name} removed ({why})")
        if self._xvfb_proc is not None:
            with contextlib.suppress(Exception):
                self._xvfb_proc.terminate()
                self._xvfb_proc.wait(timeout=4)
            with contextlib.suppress(Exception):
                if self._xvfb_proc.poll() is None:
                    self._xvfb_proc.kill()
            self._log("SS_VIRTUAL_DISPLAY_DOWN",
                      f"Xvfb {self._xvfb_display} terminated ({why})")
        self._xvfb_proc = None
        self._xvfb_display = None
        self._saved_fb = None
        self._cur = VirtualDisplay(detail=f"released ({why})" if why else "not created")

    def _try_xrandr(self, w: int, h: int) -> VirtualDisplay:
        rc, out, err = _run(["xrandr", "--current"])
        if rc != 0:
            return VirtualDisplay(ok=False, backend="xrandr", width=w, height=h,
                                  position=self.position,
                                  detail=f"xrandr --current failed: {err or out}")
        cur_w, cur_h, max_w, max_h = _parse_xrandr_screen(out)
        if cur_w <= 0 or cur_h <= 0:
            return VirtualDisplay(ok=False, backend="xrandr", width=w, height=h,
                                  position=self.position,
                                  detail="could not parse the current framebuffer size")
        outputs = _parse_xrandr_outputs(out)
        if _xrandr_monitor_exists(out, self.monitor_name):
            _run(["xrandr", "--delmonitor", self.monitor_name])
            rc, out, err = _run(["xrandr", "--current"])
            outputs = _parse_xrandr_outputs(out)
        max_w = max_w or 16384
        max_h = max_h or 16384

        pos = self.position if self.position in ("right", "left", "above", "below") else "right"
        dx = dy = 0
        if pos == "right":
            vx, vy = cur_w, 0
            new_w, new_h = cur_w + w, max(cur_h, h)
        elif pos == "below":
            vx, vy = 0, cur_h
            new_w, new_h = max(cur_w, w), cur_h + h
        elif pos == "left":
            vx, vy, dx = 0, 0, w
            new_w, new_h = cur_w + w, max(cur_h, h)
        else:
            vx, vy, dy = 0, 0, h
            new_w, new_h = max(cur_w, w), cur_h + h
        if new_w > max_w or new_h > max_h:
            return VirtualDisplay(ok=False, backend="xrandr", width=w, height=h,
                                  position=pos,
                                  detail=f"framebuffer {new_w}x{new_h} exceeds the X "
                                         f"maximum {max_w}x{max_h}")

        self._saved_fb = (cur_w, cur_h)
        self._moved_outputs = [{"name": o["name"], "x": o["x"], "y": o["y"]}
                               for o in outputs] if (dx or dy) else []

        cmd = ["xrandr", "--fb", f"{new_w}x{new_h}",
               "--setmonitor", f"{self.monitor_name} {w}/0x{h}/0+{vx}+{vy} none"]
        for o in self._moved_outputs:
            cmd += ["--output", o["name"], "--pos", f"{o['x'] + dx}x{o['y'] + dy}"]
        rc, out, err = _run(cmd, timeout=12.0)
        if rc != 0:
            self._saved_fb = None
            self._moved_outputs = []
            return VirtualDisplay(ok=False, backend="xrandr", width=w, height=h,
                                  position=pos,
                                  detail=f"xrandr --setmonitor failed: {err or out}")

        rc2, mon_txt, _e2 = _run(["xrandr", "--listmonitors"])
        listed = (rc2 == 0 and self.monitor_name in mon_txt)
        root = (new_w, new_h)
        with contextlib.suppress(Exception):
            if shutil.which("xdpyinfo"):
                rc3, dpi_txt, _e3 = _run(["xdpyinfo"], timeout=4.0)
                if rc3 == 0:
                    rs = _root_size(dpi_txt)
                    if rs[0] and rs[1]:
                        root = rs
        attached = listed and root[0] >= vx + w and root[1] >= vy + h
        hints = []
        if attached:
            hints.append(f"move the mouse past the {pos} edge of your display, or "
                         f"place a window with: wmctrl -r :ACTIVE: -e 0,{vx},{vy},{w},{h}")
        else:
            hints.append("the framebuffer was resized but the compositor did not keep "
                         "the output-less monitor, so the pointer may not travel onto "
                         "it - try --ss-virtual-backend xvfb, or a wlroots compositor")
        if dx or dy:
            hints.append(f"your real display(s) were shifted by +{dx}+{dy} to make "
                         f"room on the {pos} edge; teardown restores them exactly")
        return VirtualDisplay(
            ok=True, backend="xrandr", name=self.monitor_name, width=w, height=h,
            region={"left": vx, "top": vy, "width": w, "height": h},
            position=pos, attached=attached, root_size=root,
            outputs_moved=[o["name"] for o in self._moved_outputs],
            detail=(f"xrandr monitor {self.monitor_name} attached at the {pos} edge "
                    f"(+{vx}+{vy}), framebuffer {cur_w}x{cur_h} -> {root[0]}x{root[1]}"
                    + ("" if attached else " [NOT traversable]")),
            hints=hints,
        )

    def _try_sway(self, w: int, h: int) -> VirtualDisplay:
        rc, _out, err = _run(["swaymsg", "-t", "create_output"])
        if rc != 0:
            return VirtualDisplay(ok=False, backend="sway", width=w, height=h,
                                  detail=f"swaymsg create_output failed: {err or 'unsupported'}")
        time.sleep(0.35)
        rc, out, err = _run(["swaymsg", "-t", "get_outputs"])
        if rc != 0:
            return VirtualDisplay(ok=False, backend="sway", width=w, height=h,
                                  detail=f"swaymsg get_outputs failed: {err}")
        name = ""
        try:
            import json as _json
            for o in _json.loads(out):
                if not isinstance(o, dict):
                    continue
                nm = str(o.get("name") or "")
                if nm.startswith("Headless") or o.get("make") == "Headless":
                    name = nm
        except Exception:
            pass
        if not name:
            return VirtualDisplay(ok=False, backend="sway", width=w, height=h,
                                  detail="sway created an output but it could not be identified")
        _run(["swaymsg", "output", name, "mode", f"{w}x{h}"])
        pos = self.position if self.position in ("right", "left", "above", "below") else "right"
        px = py = 0
        with contextlib.suppress(Exception):
            import json as _json
            rc_o, out_o, _e = _run(["swaymsg", "-t", "get_outputs"])
            if rc_o == 0:
                others = [o for o in _json.loads(out_o)
                          if isinstance(o, dict) and o.get("active")
                          and str(o.get("name")) != name]
                if others:
                    mx = max(int(o["rect"]["x"] + o["rect"]["width"]) for o in others)
                    my = max(int(o["rect"]["y"] + o["rect"]["height"]) for o in others)
                    mn_x = min(int(o["rect"]["x"]) for o in others)
                    mn_y = min(int(o["rect"]["y"]) for o in others)
                    px, py = {"right": (mx, mn_y), "below": (mn_x, my),
                              "left": (mn_x - w, mn_y),
                              "above": (mn_x, mn_y - h)}.get(pos, (mx, mn_y))
        _run(["swaymsg", "output", name, "position", str(px), str(py)])
        if not shutil.which("grim"):
            return VirtualDisplay(ok=False, backend="sway", width=w, height=h,
                                  detail=f"headless output {name} created but `grim` is "
                                         f"missing, so it cannot be captured",
                                  hints=["sudo apt install grim"])
        return VirtualDisplay(ok=True, backend="sway", name=name, width=w, height=h,
                              output=name, position=pos, attached=True,
                              detail=(f"sway headless output {name} {w}x{h} attached "
                                      f"at the {pos} edge (+{px}+{py})"),
                              hints=[f"move a window there with "
                                     f"`swaymsg move container to output {name}`"])

    def _try_xvfb(self, w: int, h: int) -> VirtualDisplay:
        for n in self.XVFB_DISPLAY_CANDIDATES:
            disp = f":{n}"
            lock = f"/tmp/.X{n}-lock"
            if os.path.exists(lock):
                continue
            try:
                proc = subprocess.Popen(
                    ["Xvfb", disp, "-screen", "0", f"{w}x{h}x24", "-nolisten", "tcp"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as exc:
                return VirtualDisplay(ok=False, backend="xvfb", width=w, height=h,
                                      detail=f"could not start Xvfb: {exc!r}")
            deadline = time.monotonic() + 4.0
            up = False
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    break
                rc, _o, _e = _run(["xdpyinfo", "-display", disp], timeout=2.0) \
                    if shutil.which("xdpyinfo") else (0, "", "")
                if rc == 0:
                    up = True
                    break
                time.sleep(0.15)
            if not up:
                with contextlib.suppress(Exception):
                    proc.terminate()
                continue
            self._xvfb_proc = proc
            self._xvfb_display = disp
            hints = [f"launch apps into it with: DISPLAY={disp} <app>",
                     "install a small WM (openbox) for movable windows: "
                     f"DISPLAY={disp} openbox &"]
            if shutil.which("openbox"):
                with contextlib.suppress(Exception):
                    subprocess.Popen(["openbox"], env={**os.environ, "DISPLAY": disp},
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    hints[1] = f"openbox started on {disp}"
            return VirtualDisplay(ok=True, backend="xvfb", name=disp, width=w, height=h,
                                  display=disp, position="none", attached=False,
                                  detail=(f"Xvfb nested display {disp} at {w}x{h} - a "
                                          f"SEPARATE X screen, so the pointer cannot "
                                          f"travel onto it and it is not attached to "
                                          f"an edge"),
                                  hints=hints + [
                                      "for a display the pointer can cross onto, use "
                                      "--ss-virtual-backend xrandr (X11) or sway "
                                      "(wlroots); Xvfb is the portable fallback only"])
        return VirtualDisplay(ok=False, backend="xvfb", width=w, height=h,
                              detail="no free X display number for Xvfb (97..91 all locked)")
