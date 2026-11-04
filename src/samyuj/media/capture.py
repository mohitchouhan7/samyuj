from __future__ import annotations
import tempfile

import asyncio
import threading
import concurrent.futures
import contextlib
import ctypes
import fractions
import os
import shutil
import subprocess
import time
from pathlib import Path

try:
    from aiortc import MediaStreamTrack
    from av import VideoFrame
    HAVE_TRACK = True
except Exception:
    HAVE_TRACK = False
    MediaStreamTrack = object

_LIBSC_FAST = None
with contextlib.suppress(Exception):
    import ctypes
    for _so_cand in (
        Path(__file__).resolve().parents[3] / "libsc_fast.so",
        Path(__file__).resolve().parents[2] / "libsc_fast.so",
        Path(__file__).resolve().parent / "libsc_fast.so",
        Path("/usr/lib/samyuj/libsc_fast.so"),
    ):
        if _so_cand.is_file():
            _lib = ctypes.CDLL(str(_so_cand))
            _lib.sc_swap_buffers.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
            _lib.sc_swap_buffers.restype = None
            _lib.sc_copy_swap_uv.argtypes = [
                ctypes.c_void_p, ctypes.c_int,
                ctypes.c_void_p, ctypes.c_int,
                ctypes.c_void_p, ctypes.c_int,
                ctypes.c_void_p, ctypes.c_int,
                ctypes.c_int, ctypes.c_int,
            ]
            _lib.sc_copy_swap_uv.restype = None
            _lib.sc_resize_rgb24.argtypes = [
                ctypes.c_char_p, ctypes.c_int, ctypes.c_int,
                ctypes.c_char_p, ctypes.c_int, ctypes.c_int,
            ]
            _lib.sc_resize_rgb24.restype = None
            _lib.sc_ws_xor_mask.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_char_p]
            _lib.sc_ws_xor_mask.restype = None
            _lib.sc_xdr_pack_u32_2.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
            _lib.sc_xdr_pack_u32_2.restype = None
            _lib.sc_crc32_ieee.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
            _lib.sc_crc32_ieee.restype = ctypes.c_uint32
            if hasattr(_lib, "sc_copy_strided_rows"):
                _lib.sc_copy_strided_rows.argtypes = [
                    ctypes.c_void_p, ctypes.c_int,
                    ctypes.c_void_p, ctypes.c_int,
                    ctypes.c_int, ctypes.c_int,
                ]
                _lib.sc_copy_strided_rows.restype = None
            if hasattr(_lib, "sc_rgb24_to_bgrx32"):
                _lib.sc_rgb24_to_bgrx32.argtypes = [
                    ctypes.c_void_p, ctypes.c_int,
                    ctypes.c_void_p, ctypes.c_int,
                    ctypes.c_int, ctypes.c_int,
                ]
                _lib.sc_rgb24_to_bgrx32.restype = None
            if hasattr(_lib, "sc_bgrx32_to_rgb24_resize"):
                _lib.sc_bgrx32_to_rgb24_resize.argtypes = [
                    ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                    ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                ]
                _lib.sc_bgrx32_to_rgb24_resize.restype = None
            _LIBSC_FAST = _lib
            break

_SS_GRAB_EXECUTOR = None

def _ss_grab_executor():
    global _SS_GRAB_EXECUTOR
    if _SS_GRAB_EXECUTOR is None:
        _SS_GRAB_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="sc-ssgrab")
    return _SS_GRAB_EXECUTOR

_WAYLAND_TOOL_FAILS: dict = {}

def _wayland_tool_blocked(name: str) -> bool:
    rec = _WAYLAND_TOOL_FAILS.get(name)
    if not rec:
        return False
    fails, last = rec
    if time.monotonic() - last >= 30.0:
        _WAYLAND_TOOL_FAILS.pop(name, None)
        return False
    return fails >= 3

def _wayland_tool_result(name: str, ok: bool) -> None:
    fails = 0 if ok else (_WAYLAND_TOOL_FAILS.get(name, (0, 0.0))[0] + 1)
    _WAYLAND_TOOL_FAILS[name] = (fails, time.monotonic())

_SS_GRAB_TLS = threading.local()

def _bgrx_to_rgb_resize(raw_bgrx, sw: int, sh: int, dw: int, dh: int,
                        src_stride: int = 0) -> bytes:
    if sw <= 0 or sh <= 0 or dw <= 0 or dh <= 0:
        return bytes(max(0, dw) * max(0, dh) * 3)
    if src_stride <= 0:
        src_stride = sw * 4
    if _LIBSC_FAST is not None and hasattr(_LIBSC_FAST, "sc_bgrx32_to_rgb24_resize"):
        with contextlib.suppress(Exception):
            import ctypes
            mv = memoryview(raw_bgrx)
            need_src = (sh - 1) * src_stride + sw * 4
            if len(mv) >= need_src:
                dst_buf = ctypes.create_string_buffer(dw * dh * 3)
                try:
                    src_c = (ctypes.c_uint8 * len(mv)).from_buffer(mv)
                except TypeError:
                    src_c = (ctypes.c_uint8 * len(mv)).from_buffer_copy(mv)
                _LIBSC_FAST.sc_bgrx32_to_rgb24_resize(
                    ctypes.addressof(src_c), int(sw), int(sh), int(src_stride),
                    ctypes.addressof(dst_buf), int(dw), int(dh),
                )
                return dst_buf.raw
    with contextlib.suppress(Exception):
        from PIL import Image
        img = Image.frombytes("RGB", (sw, sh), bytes(raw_bgrx), "raw", "BGRX", src_stride, 1)
        if (sw, sh) != (dw, dh):
            resample = getattr(getattr(Image, "Resampling", Image), "NEAREST", 0)
            img = img.resize((dw, dh), resample)
        return img.tobytes()
    return bytes(dw * dh * 3)

def _extract_mss_shot_rgb(shot, w: int, h: int) -> bytes:
    raw = getattr(shot, "raw", None)
    if raw is not None and len(raw) >= int(shot.width) * int(shot.height) * 4:
        return _bgrx_to_rgb_resize(raw, int(shot.width), int(shot.height), int(w), int(h))
    if shot.width == w and shot.height == h:
        return bytes(shot.rgb)
    return _resize_rgb(shot.rgb, shot.width, shot.height, w, h)

def _parse_ppm_p6_rgb(blob: bytes, w: int, h: int) -> bytes | None:
    if not blob or not blob.startswith(b"P6"):
        return None
    idx = 2
    n = len(blob)
    tokens: list[int] = []
    while len(tokens) < 3 and idx < n:
        while idx < n and blob[idx] in b" \t\r\n":
            idx += 1
        if idx < n and blob[idx] == ord("#"):
            while idx < n and blob[idx] not in b"\r\n":
                idx += 1
            continue
        start = idx
        while idx < n and blob[idx] not in b" \t\r\n#":
            idx += 1
        if idx > start:
            try:
                tokens.append(int(blob[start:idx]))
            except ValueError:
                return None
    if len(tokens) != 3 or tokens[2] != 255 or idx >= n:
        return None
    idx += 1
    sw, sh = tokens[0], tokens[1]
    if not (0 < sw <= 16384 and 0 < sh <= 16384):
        return None
    need = sw * sh * 3
    if (n - idx) < need:
        return None
    raw_rgb = blob[idx:idx + need]
    if sw == w and sh == h:
        return raw_rgb
    return _resize_rgb(raw_rgb, sw, sh, w, h)

def _screen_grab_rgb(w: int, h: int, region: dict | None = None,
                     display: str | None = None,
                     output: str | None = None) -> bytes | None:
    if output and shutil.which("grim"):
        with contextlib.suppress(Exception):
            r = subprocess.run(["grim", "-t", "ppm", "-o", output, "-"],
                               capture_output=True, timeout=2.0)
            if r.returncode == 0 and r.stdout:
                ppm_rgb = _parse_ppm_p6_rgb(r.stdout, w, h)
                if ppm_rgb is not None:
                    return ppm_rgb
        with contextlib.suppress(Exception):
            from PIL import Image
            import io as _io
            r = subprocess.run(["grim", "-o", output, "-"], capture_output=True,
                               timeout=3.0)
            if r.returncode == 0 and r.stdout:
                with Image.open(_io.BytesIO(r.stdout)) as img:
                    if img.size != (w, h):
                        resample = getattr(getattr(Image, "Resampling", Image), "NEAREST", 0)
                        img = img.resize((w, h), resample)
                    return img.convert("RGB").tobytes()
    if display:
        try:
            import mss
            with mss.mss(display=display) as sct:
                mon = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
                shot = sct.grab(mon)
                return _extract_mss_shot_rgb(shot, w, h)
        except Exception:
            pass
        with contextlib.suppress(Exception):
            from PIL import Image
            if hasattr(Image, "core") and hasattr(Image.core, "grabscreen_x11"):
                (sw, sh), raw_bgrx = Image.core.grabscreen_x11(display)
                if sw > 0 and sh > 0 and raw_bgrx:
                    return _bgrx_to_rgb_resize(raw_bgrx, sw, sh, w, h)
        with contextlib.suppress(Exception):
            from PIL import Image, ImageGrab as _IG
            img = _IG.grab(xdisplay=display)
            if img.size != (w, h):
                resample = getattr(getattr(Image, "Resampling", Image), "NEAREST", 0)
                img = img.resize((w, h), resample)
            return img.convert("RGB").tobytes()
    if region:
        try:
            import mss
            with mss.mss() as sct:
                shot = sct.grab(dict(region))
                return _extract_mss_shot_rgb(shot, w, h)
        except Exception:
            pass
    _on_wayland = bool(os.environ.get("WAYLAND_DISPLAY")) and \
        os.environ.get("XDG_SESSION_TYPE", "").lower() != "x11"
    if not (_on_wayland and not display and not output and not region):
        try:
            import mss
            sct_map = getattr(_SS_GRAB_TLS, "sct_map", None)
            if sct_map is None:
                sct_map = _SS_GRAB_TLS.sct_map = {}
            sct = sct_map.get("")
            if sct is not None and getattr(sct, "_sc_cls", None) is mss.mss:
                try:
                    mon = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
                    shot = sct.grab(mon)
                    return _extract_mss_shot_rgb(shot, w, h)
                except Exception:
                    with contextlib.suppress(Exception):
                        sct.close()
                    sct_map.pop("", None)
            with mss.mss() as sct_ctx:
                mon = sct_ctx.monitors[1] if len(sct_ctx.monitors) > 1 else sct_ctx.monitors[0]
                shot = sct_ctx.grab(mon)
                return _extract_mss_shot_rgb(shot, w, h)
        except Exception:
            pass
        with contextlib.suppress(Exception):
            from PIL import Image
            if hasattr(Image, "core") and hasattr(Image.core, "grabscreen_x11"):
                (sw, sh), raw_bgrx = Image.core.grabscreen_x11(None)
                if sw > 0 and sh > 0 and raw_bgrx:
                    return _bgrx_to_rgb_resize(raw_bgrx, sw, sh, w, h)
        try:
            from PIL import Image, ImageGrab
            img = ImageGrab.grab() if hasattr(ImageGrab, "grab") else Image.grab()
            if img.size != (w, h):
                resample = getattr(getattr(Image, "Resampling", Image), "NEAREST", 0)
                img = img.resize((w, h), resample)
            return img.convert("RGB").tobytes()
        except Exception:
            pass
    if os.environ.get("WAYLAND_DISPLAY"):
        if not _wayland_tool_blocked("grim") and shutil.which("grim"):
            with contextlib.suppress(Exception):
                r = subprocess.run(["grim", "-t", "ppm", "-"], capture_output=True, timeout=1.2)
                if r.returncode == 0 and r.stdout:
                    ppm_rgb = _parse_ppm_p6_rgb(r.stdout, w, h)
                    if ppm_rgb is not None:
                        _wayland_tool_result("grim", True)
                        return ppm_rgb
        from PIL import Image
        for wl_name, wl_cmd_fn in (
            ("grim", lambda tmp_f: ["grim", tmp_f] if shutil.which("grim") else None),
            ("spectacle", lambda tmp_f: ["spectacle", "-b", "-n", "-o", tmp_f] if shutil.which("spectacle") else None),
            ("gnome-screenshot", lambda tmp_f: ["gnome-screenshot", "-f", tmp_f] if shutil.which("gnome-screenshot") else None),
        ):
            if _wayland_tool_blocked(wl_name):
                continue
            with contextlib.suppress(Exception):
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
                    with contextlib.suppress(OSError):
                        os.fchmod(tf.fileno(), 0o600)
                    tmp_png = tf.name
                try:
                    cmd = wl_cmd_fn(tmp_png)
                    if not cmd:
                        continue
                    r = subprocess.run(cmd, capture_output=True, timeout=1.5)
                    if r.returncode == 0 and Path(tmp_png).is_file() and Path(tmp_png).stat().st_size > 64:
                        with Image.open(tmp_png) as img:
                            if img.size != (w, h):
                                resample = getattr(getattr(Image, "Resampling", Image), "NEAREST", 0)
                                img = img.resize((w, h), resample)
                            _wayland_tool_result(wl_name, True)
                            return img.convert("RGB").tobytes()
                    _wayland_tool_result(wl_name, False)
                except subprocess.TimeoutExpired:
                    _wayland_tool_result(wl_name, False)
                finally:
                    with contextlib.suppress(OSError):
                        os.unlink(tmp_png)
    return None

_RESIZE_CACHE: dict[tuple[int, int, int, int], tuple[list[int], list[int]]] = {}
_PATTERN_RGB_CACHE: dict[tuple[int, int], bytes] = {}

def _resize_rgb(src: bytes, sw: int, sh: int, dw: int, dh: int) -> bytes:
    if sw <= 0 or sh <= 0 or dw <= 0 or dh <= 0:
        return bytes(max(0, dw) * max(0, dh) * 3)
    if _LIBSC_FAST is not None:
        with contextlib.suppress(Exception):
            import ctypes
            dst_buf = ctypes.create_string_buffer(dw * dh * 3)
            _LIBSC_FAST.sc_resize_rgb24(bytes(src), sw, sh, dst_buf, dw, dh)
            return dst_buf.raw
    key = (sw, sh, dw, dh)
    xs_ys = _RESIZE_CACHE.get(key)
    if xs_ys is None:
        xs = [min(sw - 1, (x * sw) // dw) * 3 for x in range(dw)]
        ys = [min(sh - 1, (y * sh) // dh) * sw * 3 for y in range(dh)]
        if len(_RESIZE_CACHE) > 16:
            _RESIZE_CACHE.clear()
        _RESIZE_CACHE[key] = xs_ys = (xs, ys)
    xs, ys = xs_ys
    out = bytearray(dw * dh * 3)
    o = 0
    for ry in ys:
        for rx in xs:
            i = ry + rx
            out[o] = src[i]
            out[o + 1] = src[i + 1]
            out[o + 2] = src[i + 2]
            o += 3
    return bytes(out)

def _pattern_rgb(w: int, h: int, tick: int) -> bytes:
    base_row = _PATTERN_RGB_CACHE.get(w)
    if base_row is None or len(base_row) != w * 3:
        bars = ((192, 192, 192), (192, 192, 0), (0, 192, 192), (0, 192, 0),
                (192, 0, 192), (192, 0, 0), (0, 0, 192), (16, 16, 16))
        row = bytearray(w * 3)
        bw = max(1, w // len(bars))
        for x in range(w):
            r, g, b = bars[min(len(bars) - 1, x // bw)]
            row[x * 3] = r
            row[x * 3 + 1] = g
            row[x * 3 + 2] = b
        base_row = bytes(row)
        if len(_PATTERN_RGB_CACHE) > 16:
            _PATTERN_RGB_CACHE.clear()
        _PATTERN_RGB_CACHE[w] = base_row
    barw = max(4, min(w, w // 24))
    bx = (tick * max(2, w // 60)) % max(1, w - barw)
    actual_barw = min(w, bx + barw) - bx
    bx3 = bx * 3
    bx3_end = bx3 + actual_barw * 3
    row_bar_ba = bytearray(base_row)
    row_bar_ba[bx3:bx3_end] = b"\xeb" * (actual_barw * 3)
    row_bar = bytes(row_bar_ba)
    secs = tick % 60
    cw, ch = max(8, w // 12), max(8, h // 12)
    x0, y0 = (w - cw) // 2, (h - ch) // 2
    y1 = min(h, y0 + ch)
    fillw = min(w - x0, max(1, int(cw * secs / 60)))
    x03 = x0 * 3
    x03_end = x03 + fillw * 3
    row_mid_ba = bytearray(row_bar_ba)
    row_mid_ba[x03:x03_end] = bytes((30, 200, 90)) * fillw
    row_mid = bytes(row_mid_ba)
    return (row_bar * y0) + (row_mid * max(0, y1 - y0)) + (row_bar * max(0, h - y1))

if HAVE_TRACK:

    class ScreenShareTrack(MediaStreamTrack):

        kind = "video"

        def __init__(self, width: int = 1280, height: int = 720, fps: float = 30.0,
                     source: str = "auto", label: str = "pc-screen",
                     region: dict | None = None, display: str | None = None,
                     output: str | None = None) -> None:
            super().__init__()
            self.width = max(64, int(width) // 2 * 2)
            self.height = max(64, int(height) // 2 * 2)
            self.fps = max(1.0, float(fps))
            self.source = source
            self.label = label
            self.backend = "pattern"
            self.frames_sent = 0
            self._tick = 0
            self._next_ts = 0.0
            self.region = dict(region) if region else None
            self.display = display
            self.output = output
            self._last_rgb: tuple | None = None
            self._grab_future = None
            self._probe_future = None
            self._last_probe_mono = 0.0
            self._grab_fail_streak = 0
            self.capture_reuse = 0
            self.set_source(region=region, display=display, output=output,
                            probe=True)

        def set_source(self, region: dict | None = None, display: str | None = None,
                       output: str | None = None, probe: bool = True) -> str:
            self.region = dict(region) if region else None
            self.display = display
            self.output = output
            self.backend = "pattern"
            self._last_rgb = None
            self._grab_fail_streak = 0
            if probe and self.source in ("auto", "screen"):
                self._start_probe(force=True)
            elif self.source in ("auto", "screen"):
                self.backend = "screen"
            return self.backend

        def _start_probe(self, force: bool = False) -> None:
            if self._probe_future is not None and not self._probe_future.done():
                return
            if not force and (time.monotonic() - self._last_probe_mono) < 2.0:
                return
            self._last_probe_mono = time.monotonic()
            _reg, _disp, _out = self.region, self.display, self.output
            with contextlib.suppress(Exception):
                self._probe_future = _ss_grab_executor().submit(
                    lambda: _screen_grab_rgb(64, 64, region=_reg,
                                             display=_disp, output=_out))

        def _poll_probe(self) -> None:
            fut = self._probe_future
            if fut is None or not fut.done():
                return
            self._probe_future = None
            ok = False
            with contextlib.suppress(Exception):
                ok = fut.result() is not None
            if ok and self.backend != "screen":
                self.backend = "screen"
                self._grab_fail_streak = 0

        def set_resolution(self, width: int, height: int, fps: float | None = None) -> None:
            self.width = max(64, int(width) // 2 * 2)
            self.height = max(64, int(height) // 2 * 2)
            if fps is not None and float(fps) >= 1.0:
                self.fps = float(fps)
            self._last_rgb = None

        async def recv(self):
            if hasattr(self, "_timestamp"):
                pts = self._timestamp
                self._timestamp += int(90000 / self.fps)
            else:
                pts = 0
                self._timestamp = int(90000 / self.fps)
            now = time.time()
            if self._next_ts and now < self._next_ts:
                await asyncio.sleep(max(0.0, self._next_ts - now))
            self._next_ts = max(now, self._next_ts) + 1.0 / self.fps
            self._poll_probe()
            rgb = None
            if self.backend == "screen":
                fut = self._grab_future
                if fut is not None and fut.done():
                    self._grab_future = None
                    with contextlib.suppress(Exception):
                        rgb = fut.result()
                    if rgb is None:
                        self._grab_fail_streak += 1
                        if self._grab_fail_streak >= 5:
                            self.backend = "pattern"
                    else:
                        self._grab_fail_streak = 0
                if self._grab_future is None and self.backend == "screen":
                    _reg, _disp, _out = self.region, self.display, self.output
                    with contextlib.suppress(Exception):
                        self._grab_future = _ss_grab_executor().submit(
                            lambda _w=self.width, _h=self.height: _screen_grab_rgb(
                                _w, _h, region=_reg, display=_disp, output=_out))
                if rgb is not None:
                    self._last_rgb = (self.width, self.height, rgb)
            elif self.source in ("auto", "screen"):
                self._start_probe()
            if (rgb is None and self._last_rgb is not None
                    and self._last_rgb[0] == self.width
                    and self._last_rgb[1] == self.height):
                rgb = self._last_rgb[2]
                self.capture_reuse += 1
            if rgb is None:
                rgb = _pattern_rgb(self.width, self.height, self._tick)
            self._tick += 1
            self.frames_sent += 1
            frame = _rgb_frame(rgb, self.width, self.height)
            frame.pts = pts
            frame.time_base = fractions.Fraction(1, 90000)
            return frame

else:

    class ScreenShareTrack:

        kind = "video"

        def __init__(self, *a, **k) -> None:
            self.width, self.height = int(k.get("width", 1280)), int(k.get("height", 720))
            self.fps = float(k.get("fps", 30.0))
            self.backend = "unavailable"
            self.frames_sent = 0
            self.region = dict(k["region"]) if k.get("region") else None
            self.display = k.get("display")
            self.output = k.get("output")

        def set_source(self, region=None, display=None, output=None,
                       probe: bool = True) -> str:
            self.region = dict(region) if region else None
            self.display = display
            self.output = output
            return self.backend

        def set_resolution(self, width: int, height: int, fps: float | None = None) -> None:
            self.width = max(64, int(width) // 2 * 2)
            self.height = max(64, int(height) // 2 * 2)
            if fps is not None and float(fps) >= 1.0:
                self.fps = float(fps)

        async def recv(self):
            raise RuntimeError("aiortc/av not installed")

def _rgb_frame(rgb: bytes, w: int, h: int):
    frame = VideoFrame(w, h, "rgb24")
    plane = frame.planes[0]
    stride = plane.line_size
    if stride == w * 3:
        plane.update(rgb)
    else:
        buf = bytearray(stride * h)
        for y in range(h):
            buf[y * stride:y * stride + w * 3] = rgb[y * w * 3:(y + 1) * w * 3]
        plane.update(bytes(buf))
    return frame

_ScreenShareTrack = ScreenShareTrack
