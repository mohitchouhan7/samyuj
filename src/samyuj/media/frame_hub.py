from __future__ import annotations

import collections
import contextlib
import ctypes
import io
import math
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

RENDER_PIXEL_BUDGET = 4_200_000
RENDER_MAX_UPSCALE = 1.0

_LIBSC_FAST = None
for _cand in (
    Path(__file__).resolve().parents[3] / "libsc_fast.so",
    Path(__file__).resolve().parents[2] / "libsc_fast.so",
    Path("/usr/lib/samyuj/libsc_fast.so"),
):
    if _cand.is_file():
        with contextlib.suppress(Exception):
            _lib = ctypes.CDLL(str(_cand))
            _lib.sc_rgb24_to_bgrx32.argtypes = [
                ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int
            ]
            _lib.sc_rgb24_to_bgrx32.restype = None
            if hasattr(_lib, "sc_copy_strided_rows"):
                _lib.sc_copy_strided_rows.argtypes = [
                    ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int
                ]
                _lib.sc_copy_strided_rows.restype = None
            _LIBSC_FAST = _lib
            break

_PREPARED_BGRX_CACHE: "collections.OrderedDict[int, tuple[object, dict[str, Any]]]" = collections.OrderedDict()
_PREPARED_BGRX_LOCK = threading.Lock()

_UV_SWAPPED_CACHE: "collections.OrderedDict[int, object]" = collections.OrderedDict()
_UV_SWAPPED_LOCK = threading.Lock()

def store_prepared_frame_bgrx(av_frame: Any, prep: dict[str, Any]) -> None:
    if av_frame is None:
        return
    with contextlib.suppress(Exception):
        av_frame._sc_prepared_bgrx = prep
    with _PREPARED_BGRX_LOCK:
        fid = id(av_frame)
        _PREPARED_BGRX_CACHE[fid] = (av_frame, prep)
        _PREPARED_BGRX_CACHE.move_to_end(fid)
        while len(_PREPARED_BGRX_CACHE) > 16:
            _PREPARED_BGRX_CACHE.popitem(last=False)

def get_prepared_frame_bgrx(av_frame: Any) -> dict[str, Any] | None:
    if av_frame is None:
        return None
    prep = getattr(av_frame, "_sc_prepared_bgrx", None)
    if isinstance(prep, dict):
        return prep
    with _PREPARED_BGRX_LOCK:
        entry = _PREPARED_BGRX_CACHE.get(id(av_frame))
        if entry is not None and entry[0] is av_frame:
            return entry[1]
    return None

def mark_uv_swapped(av_frame: Any) -> None:
    if av_frame is None:
        return
    with contextlib.suppress(Exception):
        av_frame.uv_swapped = True
    with _UV_SWAPPED_LOCK:
        fid = id(av_frame)
        _UV_SWAPPED_CACHE[fid] = av_frame
        _UV_SWAPPED_CACHE.move_to_end(fid)
        while len(_UV_SWAPPED_CACHE) > 512:
            _UV_SWAPPED_CACHE.popitem(last=False)

def unmark_uv_swapped(av_frame: Any) -> None:
    if av_frame is None:
        return
    with contextlib.suppress(Exception):
        av_frame.uv_swapped = False
    with _UV_SWAPPED_LOCK:
        _UV_SWAPPED_CACHE.pop(id(av_frame), None)

def is_uv_swapped(av_frame: Any) -> bool:
    if av_frame is None:
        return False
    if bool(getattr(av_frame, "uv_swapped", False)):
        return True
    with _UV_SWAPPED_LOCK:
        return _UV_SWAPPED_CACHE.get(id(av_frame)) is av_frame

_mark_uv_swapped = mark_uv_swapped
_unmark_uv_swapped = unmark_uv_swapped

def _plane_copy(src, rows: int, dst_line_size: int, dst_buf_size: int = 0) -> bytes:
    sb = bytes(src)
    sl = abs(int(getattr(src, "line_size", dst_line_size) or dst_line_size))
    dl = abs(int(dst_line_size))
    target_sz = int(dst_buf_size) if int(dst_buf_size) > 0 else (dl * int(rows))
    if target_sz <= 0:
        target_sz = dl * int(rows)
    if sl == dl and len(sb) == target_sz:
        return sb
    if sl == dl and len(sb) > target_sz and target_sz == dl * int(rows):
        return sb[:target_sz]
    w = min(sl, dl)
    out = bytearray(max(target_sz, dl * int(rows)))
    if _LIBSC_FAST is not None and hasattr(_LIBSC_FAST, "sc_copy_strided_rows") and len(sb) >= sl * int(rows):
        with contextlib.suppress(Exception):
            src_c = (ctypes.c_uint8 * len(sb)).from_buffer_copy(sb)
            dst_c = (ctypes.c_uint8 * len(out)).from_buffer(out)
            _LIBSC_FAST.sc_copy_strided_rows(
                ctypes.addressof(src_c), sl,
                ctypes.addressof(dst_c), dl,
                w, int(rows),
            )
            return bytes(out[:target_sz])
    for r in range(int(rows)):
        s0 = r * sl
        if s0 >= len(sb):
            break
        chunk = sb[s0:min(len(sb), s0 + w)]
        d0 = r * dl
        out[d0:d0 + len(chunk)] = chunk
    return bytes(out[:target_sz])

def swap_uv(frame):
    if frame is None:
        return frame
    if hasattr(frame, "u") and hasattr(frame, "v") and not hasattr(frame, "planes"):
        frame.u, frame.v = frame.v, frame.u
        _mark_uv_swapped(frame)
        return frame
    try:
        fmt_obj = getattr(frame, "format", None)
        fmt_name = str(getattr(fmt_obj, "name", "") or "")
        src_frame = frame
        if fmt_name in ("nv12", "nv21") and hasattr(frame, "reformat"):
            src_frame = frame.reformat(format="yuv420p")
            fmt_name = "yuv420p"
        if fmt_obj is None or fmt_name not in ("yuv420p", "yuvj420p"):
            return frame
        import av
        wdt = int(src_frame.width)
        hgt = int(src_frame.height)
        out = av.VideoFrame(wdt, hgt, fmt_name)
        with contextlib.suppress(Exception):
            out.pts = frame.pts
            out.time_base = frame.time_base
        with contextlib.suppress(Exception):
            out.colorspace = src_frame.colorspace
            out.color_range = src_frame.color_range
        sp = src_frame.planes
        dp = out.planes
        dp[0].update(_plane_copy(sp[0], hgt, int(dp[0].line_size), int(getattr(dp[0], "buffer_size", 0) or 0)))
        dp[1].update(_plane_copy(sp[2], hgt // 2, int(dp[1].line_size), int(getattr(dp[1], "buffer_size", 0) or 0)))
        dp[2].update(_plane_copy(sp[1], hgt // 2, int(dp[2].line_size), int(getattr(dp[2], "buffer_size", 0) or 0)))
        _mark_uv_swapped(out)
        return out
    except Exception:
        return frame

def apply_uv_swap_state(frame: Any, want_swap: bool) -> tuple[Any, bool]:
    if frame is None:
        return frame, False
    want = bool(want_swap)
    cur = bool(is_uv_swapped(frame))
    if want == cur:
        return frame, cur
    out = swap_uv(frame)
    if out is None:
        return frame, cur
    if want:
        mark_uv_swapped(out)
    else:
        unmark_uv_swapped(out)
    return out, want

def target_render_size(w: int, h: int, max_w: int, max_h: int,
                       budget: int = RENDER_PIXEL_BUDGET,
                       max_upscale: float = RENDER_MAX_UPSCALE) -> tuple[int, int]:
    w, h = max(1, int(w)), max(1, int(h))
    scale = min(max_w / w, max_h / h)
    if scale > float(max_upscale):
        scale = float(max_upscale)
    tw = max(2, int(round(w * scale)))
    th = max(2, int(round(h * scale)))
    if tw * th > budget:
        k = math.sqrt(budget / float(tw * th))
        tw = max(2, int(round(tw * k)))
        th = max(2, int(round(th * k)))
    return tw, th

def encode_jpeg(frame, max_h: int = 1280) -> bytes | None:
    if frame is None:
        return None
    w, h = int(getattr(frame, "width", 0) or 0), int(getattr(frame, "height", 0) or 0)
    if w > 0 and h > 0:
        if h > max_h:
            w, h = int(round(w * max_h / h / 2.0)) * 2, max_h
        else:
            w, h = w - (w % 2), h - (h % 2)
        with contextlib.suppress(Exception):
            import av
            vf = frame.reformat(width=w, height=h, format="yuvj420p")
            codec = av.CodecContext.create("mjpeg", "w")
            codec.width, codec.height = vf.width, vf.height
            codec.pix_fmt = "yuvj420p"
            with contextlib.suppress(Exception):
                codec.qmin = 2
                codec.qmax = 10
            codec.open()
            data = b"".join(bytes(p) for p in codec.encode(vf) + codec.encode(None))
            if data[:2] == b"\xff\xd8":
                return data
    with contextlib.suppress(Exception):
        img = frame.to_image() if hasattr(frame, "to_image") else frame
        iw, ih = img.size
        if ih > max_h:
            nw = max(2, int(round(iw * max_h / ih / 2.0)) * 2)
            img = img.resize((nw, max_h))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=80)
        return buf.getvalue()
    return None

@dataclass
class StreamRecord:
    stream_id: str
    owner_service: str
    service_id: int
    session_id: int | str
    track_id: str = ""
    direction: str = "inbound"
    status: str = "negotiating"
    width: int = 0
    height: int = 0
    fps: float = 0.0
    frames: int = 0
    dropped_frames: int = 0
    seq: int = 0
    first_ts: float = 0.0
    last_ts: float = 0.0
    uv_swap_applied: bool = False
    last_frame: object = None
    latest_jpeg: bytes | None = None
    jpeg_seq: int = -1
    drawn_seq: int = -1
    _fps_window: collections.deque = field(
        default_factory=lambda: collections.deque(maxlen=60)
    )

    def push_frame(self, frame, *, uv_swapped: bool = False) -> int:
        now = time.monotonic()
        if not self.first_ts:
            self.first_ts = now
        self.last_ts = now
        self.status = "live"
        self.frames += 1
        self.seq += 1
        self.uv_swap_applied = bool(uv_swapped)
        self.width = int(getattr(frame, "width", self.width) or 0)
        self.height = int(getattr(frame, "height", self.height) or 0)
        if self.last_frame is not None and self.jpeg_seq != (self.seq - 1) and self.frames > 1:
            self.dropped_frames += 1
        self.last_frame = frame
        self._fps_window.append(now)
        if len(self._fps_window) >= 2:
            dt = self._fps_window[-1] - self._fps_window[0]
            if dt > 1e-6:
                self.fps = round((len(self._fps_window) - 1) / dt, 1)
        return self.seq

    def to_dict(self) -> dict:
        return {
            "stream_id": self.stream_id,
            "owner_service": self.owner_service,
            "service_id": self.service_id,
            "session_id": self.session_id,
            "track_id": self.track_id,
            "direction": self.direction,
            "status": self.status,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "frames": self.frames,
            "dropped_frames": self.dropped_frames,
            "seq": self.seq,
            "uv_swap_applied": self.uv_swap_applied,
        }

class MediaStreamRegistry:

    def __init__(self) -> None:
        self.streams: dict[str, StreamRecord] = {}
        self.selected_stream_id: str | None = None

    def register(self, stream_id: str, owner_service: str, service_id: int,
                 session_id: int | str, track_id: str = "",
                 direction: str = "inbound") -> StreamRecord:
        rec = self.streams.get(stream_id)
        if rec is None:
            rec = StreamRecord(
                stream_id=stream_id,
                owner_service=owner_service,
                service_id=service_id,
                session_id=session_id,
                track_id=track_id,
                direction=direction,
            )
            self.streams[stream_id] = rec
        else:
            rec.owner_service = owner_service
            rec.service_id = service_id
            rec.session_id = session_id
            rec.track_id = track_id or rec.track_id
            rec.direction = direction
            rec.status = "negotiating"
        if direction == "inbound":
            cur_sel = self.streams.get(self.selected_stream_id) if self.selected_stream_id else None
            if (
                self.selected_stream_id is None
                or cur_sel is None
                or cur_sel.status == "stopped"
                or cur_sel.owner_service == owner_service
            ):
                self.selected_stream_id = stream_id
        return rec

    def get(self, stream_id: str | None) -> StreamRecord | None:
        if not stream_id:
            return None
        return self.streams.get(stream_id)

    def get_selected(self) -> StreamRecord | None:
        if self.selected_stream_id and self.selected_stream_id in self.streams:
            rec = self.streams[self.selected_stream_id]
            if rec.status == "live" and rec.last_frame is not None:
                return rec
        for rec in reversed(list(self.streams.values())):
            if rec.direction == "inbound" and rec.status == "live" and rec.last_frame is not None:
                return rec
        if self.selected_stream_id and self.selected_stream_id in self.streams:
            rec = self.streams[self.selected_stream_id]
            if rec.last_frame is not None:
                return rec
        for rec in reversed(list(self.streams.values())):
            if rec.last_frame is not None:
                return rec
        return None

    def select(self, stream_id: str | None) -> bool:
        if not stream_id:
            self.selected_stream_id = None
            return True
        if stream_id in self.streams:
            self.selected_stream_id = stream_id
            return True
        return False

    def mark_stopped(self, stream_id: str) -> None:
        rec = self.streams.get(stream_id)
        if rec is not None:
            rec.status = "stopped"
            rec.last_frame = None
            rec.latest_jpeg = None
            rec.jpeg_seq = -1
            rec.drawn_seq = -1
        if self.selected_stream_id == stream_id:
            nxt = self.get_selected()
            self.selected_stream_id = nxt.stream_id if (nxt and nxt.stream_id != stream_id) else None

    def remove(self, stream_id: str) -> None:
        self.streams.pop(stream_id, None)
        if self.selected_stream_id == stream_id:
            nxt = self.get_selected()
            self.selected_stream_id = nxt.stream_id if nxt else None

    def list_dicts(self) -> list[dict]:
        return [r.to_dict() for r in self.streams.values()]
