from __future__ import annotations

import threading
from typing import Any

_DEFAULT_GAMMA_MAX = 1.9
_DEFAULT_ENTER_LUMA = 84.0
_HYSTERESIS_RATIO = 1.35
_LUMA_EMA_ALPHA = 0.15
_GAMMA_RAMP_UP = 0.10
_GAMMA_RAMP_DOWN = 0.22
_GAMMA_EPS = 0.02
_MAX_LUT_CACHE = 64

_lock = threading.Lock()
_config = {
    "enabled": True,
    "gamma_max": _DEFAULT_GAMMA_MAX,
    "enter_luma": _DEFAULT_ENTER_LUMA,
}
_lut_cache: "dict[float, bytes]" = {}
_enhancers: "dict[str, LumaAdaptiveGamma]" = {}

def set_lowlight_config(enabled: bool | None = None,
                        gamma_max: float | None = None,
                        enter_luma: float | None = None) -> dict[str, Any]:
    with _lock:
        if enabled is not None:
            _config["enabled"] = bool(enabled)
        if gamma_max is not None:
            try:
                _config["gamma_max"] = min(4.0, max(1.0, float(gamma_max)))
            except (TypeError, ValueError):
                pass
        if enter_luma is not None:
            try:
                _config["enter_luma"] = min(200.0, max(8.0, float(enter_luma)))
            except (TypeError, ValueError):
                pass
        _lut_cache.clear()
        return dict(_config)

def get_lowlight_config() -> dict[str, Any]:
    with _lock:
        return dict(_config)

def build_gamma_lut(gamma: float) -> bytes:
    key = round(float(gamma), 2)
    with _lock:
        cached = _lut_cache.get(key)
        if cached is not None:
            return cached
    if key <= 1.0 + _GAMMA_EPS:
        lut = bytes(range(256))
    else:
        inv = 1.0 / key
        lut = bytes(min(255, max(0, int(round(255.0 * ((i / 255.0) ** inv)))))
                    for i in range(256))
    with _lock:
        if len(_lut_cache) >= _MAX_LUT_CACHE:
            _lut_cache.clear()
        _lut_cache[key] = lut
    return lut

def mean_luma_sampled(buf, length: int, max_samples: int = 2048,
                      start: int = 0, step_hint: int = 1) -> float:
    try:
        n = int(length)
        if n <= 0:
            return 0.0
        per = max(1, int(step_hint))
        avail = max(1, n // per)
        reps = max(1, avail // max(1, int(max_samples)))
        sstep = per * reps
        end = min(len(buf), start + n)
        total = 0
        count = 0
        i = start
        while i < end:
            total += buf[i]
            count += 1
            i += sstep
        return (total / count) if count else 0.0
    except Exception:
        return 0.0

class LumaAdaptiveGamma:

    __slots__ = ("ema_luma", "gamma", "frames", "lock")

    def __init__(self) -> None:
        self.ema_luma: float | None = None
        self.gamma: float = 1.0
        self.frames: int = 0
        self.lock = threading.Lock()

    def observe(self, mean_luma: float) -> float:
        cfg = get_lowlight_config()
        if not cfg["enabled"]:
            return 1.0
        enter = float(cfg["enter_luma"])
        exit_luma = enter * _HYSTERESIS_RATIO
        gmax = float(cfg["gamma_max"])
        with self.lock:
            self.frames += 1
            if self.ema_luma is None:
                self.ema_luma = float(mean_luma)
            else:
                self.ema_luma += _LUMA_EMA_ALPHA * (float(mean_luma) - self.ema_luma)
            ema = self.ema_luma
            if ema < enter:
                lo = enter * 0.45
                frac = (exit_luma - ema) / max(1e-6, (exit_luma - lo))
                frac = min(1.0, max(0.0, frac))
                target = 1.0 + (gmax - 1.0) * frac
                self.gamma += _GAMMA_RAMP_UP * (target - self.gamma)
            else:
                self.gamma += _GAMMA_RAMP_DOWN * (1.0 - self.gamma)
            if self.gamma < 1.0 + _GAMMA_EPS:
                self.gamma = 1.0
            return self.gamma

    def lut(self, mean_luma: float) -> bytes | None:
        g = self.observe(mean_luma)
        if g <= 1.0 + _GAMMA_EPS:
            return None
        return build_gamma_lut(g)

def get_enhancer(key: str) -> LumaAdaptiveGamma:
    with _lock:
        e = _enhancers.get(key)
        if e is None:
            if len(_enhancers) >= 32:
                _enhancers.clear()
            e = _enhancers[key] = LumaAdaptiveGamma()
        return e

def reset_enhancer(key: str | None = None) -> None:
    with _lock:
        if key is None:
            _enhancers.clear()
        else:
            _enhancers.pop(key, None)

def apply_enhance_to_rgb_frame(rgb_frame: Any, stream_key: str) -> bool:
    if rgb_frame is None:
        return False
    if not get_lowlight_config()["enabled"]:
        return False
    try:
        w = int(rgb_frame.width)
        h = int(rgb_frame.height)
        if w <= 0 or h <= 0:
            return False
        plane = rgb_frame.planes[0]
        stride = int(plane.line_size)
        mv = memoryview(plane)
        nbytes = min(len(mv), (h - 1) * stride + w * 3) if stride >= w * 3 else len(mv)
        if nbytes <= 0:
            return False
        enh = get_enhancer(f"rgb:{stream_key}")
        mean = mean_luma_sampled(mv, nbytes, max_samples=2048, start=0, step_hint=3)
        lut = enh.lut(mean)
        if lut is None:
            return False
        translated = bytes(mv[:nbytes]).translate(lut)
        plane.update(translated)
        return True
    except Exception:
        return False

def enhance_payload(payload: bytes, fourcc_name: str, w: int, h: int,
                    stream_key: str = "webcam-v4l2") -> bytes:
    if not payload or w <= 0 or h <= 0:
        return payload
    if not get_lowlight_config()["enabled"]:
        return payload
    name = str(fourcc_name or "").upper()
    try:
        if name in ("YU12", "I420", "YUV420"):
            y_size = w * h
            if len(payload) < y_size + (y_size // 2):
                return payload
            enh = get_enhancer(f"payload:{stream_key}")
            mean = mean_luma_sampled(payload, y_size, max_samples=2048, start=0, step_hint=1)
            lut = enh.lut(mean)
            if lut is None:
                return payload
            return payload[:y_size].translate(lut) + payload[y_size:]
        if name in ("YUYV", "YUY2"):
            need = w * h * 2
            if len(payload) < need:
                return payload
            enh = get_enhancer(f"payload:{stream_key}")
            mean = mean_luma_sampled(payload, need, max_samples=2048, start=0, step_hint=2)
            lut = enh.lut(mean)
            if lut is None:
                return payload
            ba = bytearray(payload[:need])
            ba[0::2] = bytes(ba[0::2]).translate(lut)
            return bytes(ba) + payload[need:]
        if name in ("RGB3", "BGR3", "24BG", "24RGB"):
            need = w * h * 3
            if len(payload) < need:
                return payload
            enh = get_enhancer(f"payload:{stream_key}")
            mean = mean_luma_sampled(payload, need, max_samples=2048, start=0, step_hint=3)
            lut = enh.lut(mean)
            if lut is None:
                return payload
            return payload[:need].translate(lut) + payload[need:]
    except Exception:
        return payload
    return payload
