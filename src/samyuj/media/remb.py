from __future__ import annotations

import time
from typing import Any

_REMB_FLOOR_BPS = 6_000_000
_REMB_LOSS_FLOOR_BPS = 1_500_000
_REMB_CEIL_BPS = 12_000_000
_REMB_LOSS_HIGH_PCT = 5.0
_REMB_LOSS_LOW_PCT = 1.0
_REMB_RECOVER_SAMPLES = 3
_RBE_MIN_FEEDBACK_INTERVAL_S = 0.05
_REMB_ADAPT: dict[str, Any] = {"degraded_since": None, "healthy_streak": 0, "last_loss_pct": 0.0}

def set_remb_floor(kbps: int) -> int:
    global _REMB_FLOOR_BPS
    try:
        _REMB_FLOOR_BPS = max(0, int(kbps)) * 1000
    except (TypeError, ValueError):
        pass
    return _REMB_FLOOR_BPS

def get_remb_floor_bps() -> int:
    return _REMB_FLOOR_BPS

def clamp_remb_result(res: Any, floor_bps: int, ceil_bps: int = 0) -> Any:
    if res is None:
        return res
    try:
        br, ssrcs = res
        if br is None:
            return res
        out_br = int(br)
        if floor_bps > 0 and out_br < floor_bps:
            out_br = int(floor_bps)
        if ceil_bps > 0 and out_br > ceil_bps:
            out_br = int(ceil_bps)
        if out_br != br:
            return out_br, ssrcs
    except (TypeError, ValueError):
        return res
    return res

def set_remb_loss_floor(kbps: int) -> int:
    global _REMB_LOSS_FLOOR_BPS
    try:
        _REMB_LOSS_FLOOR_BPS = max(0, int(kbps)) * 1000
    except (TypeError, ValueError):
        pass
    return _REMB_LOSS_FLOOR_BPS

def set_remb_ceiling(kbps: int) -> int:
    global _REMB_CEIL_BPS
    try:
        _REMB_CEIL_BPS = max(0, int(kbps)) * 1000
    except (TypeError, ValueError):
        pass
    return _REMB_CEIL_BPS

def note_link_loss_for_remb(loss_pct: float) -> str | None:
    st = _REMB_ADAPT
    try:
        loss = max(0.0, float(loss_pct))
    except (TypeError, ValueError):
        return None
    st["last_loss_pct"] = loss
    if st["degraded_since"] is None:
        if loss >= _REMB_LOSS_HIGH_PCT:
            st["degraded_since"] = time.monotonic()
            st["healthy_streak"] = 0
            return "degraded"
        return None
    if loss <= _REMB_LOSS_LOW_PCT:
        st["healthy_streak"] += 1
        if st["healthy_streak"] >= _REMB_RECOVER_SAMPLES:
            st["degraded_since"] = None
            st["healthy_streak"] = 0
            return "recovered"
    else:
        st["healthy_streak"] = 0
    return None

def remb_floor_degraded() -> bool:
    return _REMB_ADAPT["degraded_since"] is not None

def adaptive_remb_floor_bps() -> int:
    if _REMB_FLOOR_BPS <= 0:
        return _REMB_FLOOR_BPS
    if _REMB_ADAPT["degraded_since"] is not None:
        return min(_REMB_FLOOR_BPS, _REMB_LOSS_FLOOR_BPS)
    return _REMB_FLOOR_BPS

def adaptive_remb_ceiling_bps() -> int:
    if _REMB_CEIL_BPS <= 0:
        return 0
    loss = float(_REMB_ADAPT.get("last_loss_pct") or 0.0)
    loss_floor = min(_REMB_FLOOR_BPS, _REMB_LOSS_FLOOR_BPS) if _REMB_FLOOR_BPS > 0 else 0
    if _REMB_ADAPT["degraded_since"] is not None:
        streak = int(_REMB_ADAPT.get("healthy_streak") or 0)
        if loss >= 20.0:
            ceil = max(2_500_000, _REMB_LOSS_FLOOR_BPS or 1_500_000)
        elif streak == 0:
            ceil = 4_000_000
        elif streak == 1:
            ceil = 6_000_000
        else:
            ceil = 8_000_000
        ceil = min(_REMB_CEIL_BPS, ceil)
        return max(loss_floor, ceil) if loss_floor > 0 else ceil
    if loss >= 2.0:
        ceil = min(_REMB_CEIL_BPS, 6_000_000)
        return max(loss_floor, ceil) if loss_floor > 0 else ceil
    floor_now = adaptive_remb_floor_bps()
    return max(floor_now, _REMB_CEIL_BPS) if floor_now > 0 else _REMB_CEIL_BPS
