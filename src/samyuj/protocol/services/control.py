from __future__ import annotations

import base64
import contextlib
import hashlib
from typing import Any

from samyuj.protocol.constants import DEFAULT_PC_CAPS
from samyuj.protocol.envelope import pack_envelope

def build_pc_telemetry(
    ctx: Any,
    battery: int = 100,
    charging: bool = False,
    dnd: int = 0,
    wallpaper_b64: str = "",
    ai_capabilities: list[Any] | None = None,
    access_all_files: int = 1,
    msg_id: int | None = None,
) -> str:
    live_caps = (
        ctx.live_pc_caps()
        if hasattr(ctx, "live_pc_caps")
        else dict(DEFAULT_PC_CAPS)
    )
    dev_name = getattr(ctx, "device_name", "Samyuj")
    data = {
        "cmd": 1,
        "ver": "1",
        "AiCapabilities": list(ai_capabilities if ai_capabilities is not None else []),
        "accessAllFiles": int(access_all_files),
        "battery": max(0, min(100, int(battery))),
        "cap": {str(k): int(v) for k, v in (live_caps or {}).items()},
        "chargePlug": 1 if charging else 0,
        "deviceName": dev_name,
        "dndState": int(dnd),
        "isCharging": bool(charging),
        "model": getattr(ctx, "pc_sku", "Linux-PC"),
        "realDeviceName": dev_name,
        "wallpaper": wallpaper_b64 or "",
    }
    return pack_envelope(ctx, msg_id, None, 25, 25, data)

def build_device_status(ctx: Any, msg_id: int | None = None) -> str:
    snap = ctx.get_pc_telemetry_snapshot() if hasattr(ctx, "get_pc_telemetry_snapshot") else {}
    bat = snap.get("battery", getattr(ctx, "pc_battery", 100))
    chg = snap.get("isCharging", True)
    dnd_val = snap.get("dndState", 1 if getattr(ctx, "dnd", False) else 0)
    return build_pc_telemetry(
        ctx,
        battery=bat,
        charging=chg,
        dnd=dnd_val,
        wallpaper_b64=getattr(ctx, "wallpaper_b64", "") or "",
        msg_id=msg_id,
    )

def build_wallpaper_push(
    ctx: Any,
    png_b64: str,
    md5: str = "",
    msg_id: int | None = None,
) -> str:
    if not md5 and png_b64:
        with contextlib.suppress(Exception):
            md5 = hashlib.md5(base64.b64decode(png_b64)).hexdigest()
    data = {"cmd": 2, "ver": "1", "wallpaper": png_b64 or "", "md5": md5 or ""}
    return pack_envelope(ctx, msg_id, None, 25, 25, data)

def build_wallpaper_request(ctx: Any, msg_id: int | None = None) -> str:
    return pack_envelope(ctx, msg_id, None, 25, 25, {"cmd": 3, "ver": "1"})

def build_wallpaper_settings(ctx: Any, msg_id: int | None = None) -> str:
    return pack_envelope(ctx, msg_id, None, 25, 25, {"cmd": 4, "ver": "1"})

def build_dnd_sync(ctx: Any, on: bool, msg_id: int | None = None) -> str:
    return pack_envelope(ctx, msg_id, None, 25, 25, {"cmd": 6, "ver": "1", "dndState": 1 if on else 0})

def build_ai_capabilities(ctx: Any, caps: list[dict[str, Any]], msg_id: int | None = None) -> str:
    return pack_envelope(ctx, msg_id, None, 25, 25, {"cmd": 20, "ver": "1", "AiCapabilities": list(caps)})

def build_dialog_dismiss(ctx: Any, msg_id: int | None = None) -> str:
    return pack_envelope(ctx, msg_id, None, 25, 25, {"cmd": 21, "ver": "1"})

def build_control_lock_state(
    ctx: Any,
    state: int | bool | str,
    ver: str = "1",
    msg_id: int | None = None,
    src_service_id: int = 22,
) -> str:
    st_int = 1 if state in (1, True, "1", "locked", "true", "on") else 0
    data = {"ver": str(ver), "cmd": 2, "state": st_int}
    return pack_envelope(ctx, msg_id, None, 22, src_service_id, data)

def build_control_invite(
    ctx: Any,
    ver: str = "1",
    msg_id: int | None = None,
    src_service_id: int = 22,
) -> str:
    data = {"ver": str(ver), "cmd": 1}
    return pack_envelope(ctx, msg_id, None, 22, src_service_id, data)
