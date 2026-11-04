from __future__ import annotations

import time
from typing import Any, Callable

_IDR_GATE_CAP_S = 4.0
_H264_KEYFRAME_HOOK: Callable[[], None] | None = None
_LAST_ARM_PLI_MONO = 0.0
_DECODER_GATE_ACTIVE = False
_PRESENTER_BRIDGE: Any = None

_H264_GATE_STATS: dict[str, int] = {
    "gated_frames": 0,
    "pli_requested": 0,
    "arm_pli_requested": 0,
    "cap_reached": 0,
    "incomplete_keyframes_dropped": 0,
    "vp8_gated_frames": 0,
    "forwarded_frames": 0,
    "forwarded_bytes": 0,
}

_NACK_STORM_STATS: dict[str, int] = {
    "storms_broken": 0,
    "packets_cleared": 0,
    "throttled": 0,
}

def is_decoder_gate_active() -> bool:
    return bool(_DECODER_GATE_ACTIVE)

def set_decoder_gate_active(active: bool) -> None:
    global _DECODER_GATE_ACTIVE
    _DECODER_GATE_ACTIVE = bool(active)

def set_h264_keyframe_hook(fn: Callable[[], None] | None) -> None:
    global _H264_KEYFRAME_HOOK
    _H264_KEYFRAME_HOOK = fn if callable(fn) else None

def request_h264_keyframe(on_arm: bool = False) -> None:
    global _LAST_ARM_PLI_MONO
    hook = _H264_KEYFRAME_HOOK
    if hook is None:
        return
    if on_arm:
        now_m = time.monotonic()
        if now_m - _LAST_ARM_PLI_MONO < 0.35:
            return
        _LAST_ARM_PLI_MONO = now_m
        _H264_GATE_STATS["arm_pli_requested"] = _H264_GATE_STATS.get("arm_pli_requested", 0) + 1
    else:
        _H264_GATE_STATS["pli_requested"] += 1
    try:
        hook()
    except Exception:
        pass

def vp8_is_keyframe(buf: bytes) -> bool:
    if not buf or len(buf) < 10:
        return False
    return (buf[0] & 0x01) == 0 and buf[3:6] == b"\x9d\x01\x2a"

def h264_has_idr_or_sps(buf: bytes) -> bool:
    idx = 0
    n = len(buf)
    while idx < n - 4:
        pos = buf.find(b"\x00\x00\x00\x01", idx)
        if pos == -1 or pos + 4 >= n:
            break
        nal_t = buf[pos + 4] & 0x1F
        if nal_t in (5, 7):
            return True
        idx = pos + 4
    return False

def set_presenter_bridge(bridge: Any) -> None:
    global _PRESENTER_BRIDGE
    _PRESENTER_BRIDGE = bridge

def get_presenter_bridge() -> Any:
    return _PRESENTER_BRIDGE
