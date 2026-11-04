from __future__ import annotations

from typing import Any

from samyuj.protocol.constants import (
    DESKTOP_VIEW_DESKTOP,
    DISPLAY_MODE_DESKTOP,
    DISPLAY_MSG_ID_QUERY,
    DISPLAY_MSG_ID_SET_DISPLAY_MODE,
    DISPLAY_MSG_ID_SET_RESOLUTION,
    DISPLAY_TYPE_REMOTE,
    RDP_CLIENT_CMD_DISPLAY_ROTATION,
)
from samyuj.protocol.envelope import pack_envelope

def build_desktop_reply(ctx: Any, req_frame: dict[str, Any], accept: bool = True) -> str:
    d = req_frame.get("data") or {}
    view_val = int(d.get("view", 2) or 2)
    res = getattr(ctx, "desktop_res", (1920, 1080))
    rw, rh = int(res[0]) & ~1, int(res[1]) & ~1
    w, hh = max(64, rw), max(64, rh)
    rot = 1 if hh > w else 0
    data: dict[str, Any] = {
        "cmd": 1,
        "reply": 0 if accept else 1,
        "view": view_val,
        "ver": "1.0.0",
        "port": 0,
        "errorCode": 0,
        "mediaFormat": 0,
        "autoStart": False,
        "drawerMode": bool(d.get("drawerMode")),
        "dynamicPhone": False,
        "lockscreenState": 0,
        "rotation": rot,
        "showNaviBar": False,
        "deviceWidth": 0,
        "deviceHeight": 0,
    }
    if view_val == 2:
        data["resolution"] = {"width": w, "height": hh}
    return pack_envelope(ctx, req_frame.get("msgId"), None, 1, 1, data)

def build_desktop_request(
    ctx: Any,
    view: int = DESKTOP_VIEW_DESKTOP,
    width: int | None = None,
    height: int | None = None,
    drawer_mode: bool = False,
    msg_id: int | None = None,
) -> str:
    res = getattr(ctx, "desktop_res", (1920, 1080))
    rw = int(width or res[0]) & ~1
    rh = int(height or res[1]) & ~1
    w, hh = max(64, rw), max(64, rh)
    view_val = int(view)
    rot = 1 if hh > w else 0
    data: dict[str, Any] = {
        "cmd": 1,
        "reply": 0,
        "view": view_val,
        "ver": "1.0.0",
        "port": 0,
        "errorCode": 0,
        "mediaFormat": 0,
        "autoStart": False,
        "drawerMode": bool(drawer_mode),
        "dynamicPhone": False,
        "lockscreenState": 0,
        "rotation": rot,
        "showNaviBar": False,
        "deviceWidth": 0,
        "deviceHeight": 0,
    }
    if view_val == DESKTOP_VIEW_DESKTOP:
        data["resolution"] = {"width": w, "height": hh}
    return pack_envelope(ctx, msg_id, None, 1, 1, data)

def build_desktop_set_resolution(
    ctx: Any,
    width: int,
    height: int,
    view: int = 2,
    msg_id: int | None = None,
) -> str:
    rot = 1 if int(height) > int(width) else 0
    data = {
        "cmd": 33,
        "ver": "1.0.0",
        "view": int(view),
        "reply": 0,
        "port": 0,
        "errorCode": 0,
        "mediaFormat": 0,
        "autoStart": False,
        "drawerMode": False,
        "dynamicPhone": False,
        "lockscreenState": 0,
        "rotation": rot,
        "showNaviBar": False,
        "deviceWidth": 0,
        "deviceHeight": 0,
        "resolution": {"width": int(width), "height": int(height)},
    }
    return pack_envelope(ctx, msg_id, None, 1, 1, data)

def build_desktop_stop(ctx: Any, msg_id: int | None = None) -> str:
    data = {
        "cmd": 5,
        "reply": 0,
        "ver": "1.0.0",
        "port": 0,
        "errorCode": 0,
        "mediaFormat": 0,
        "view": 2,
        "drawerMode": False,
        "autoStart": False,
        "dynamicPhone": False,
        "lockscreenState": 0,
        "rotation": 0,
        "showNaviBar": False,
        "deviceWidth": 0,
        "deviceHeight": 0,
    }
    return pack_envelope(ctx, msg_id, None, 1, 1, data)

def build_desktop_switch_view(
    ctx: Any,
    view: int,
    width: int | None = None,
    height: int | None = None,
    msg_id: int | None = None,
) -> str:
    view_val = int(view)
    data: dict[str, Any] = {
        "cmd": 30,
        "ver": "1.0.0",
        "view": view_val,
        "reply": 0,
        "port": 0,
        "errorCode": 0,
        "mediaFormat": 0,
        "autoStart": False,
        "drawerMode": False,
        "dynamicPhone": False,
        "lockscreenState": 0,
        "rotation": 0,
        "showNaviBar": False,
        "deviceWidth": 0,
        "deviceHeight": 0,
    }
    if view_val == DESKTOP_VIEW_DESKTOP and width and height:
        w_l, h_l = max(64, int(width) & ~1), max(64, int(height) & ~1)
        data["resolution"] = {"width": w_l, "height": h_l}
        data["rotation"] = 1 if h_l > w_l else 0
    return pack_envelope(ctx, msg_id, None, 1, 1, data)

def build_desktop_lock(ctx: Any, lock: bool = True, msg_id: int | None = None) -> str:
    data = {
        "cmd": 20,
        "ver": "1.0.0",
        "lockscreenState": 1 if lock else 0,
        "reply": 0,
        "view": 2,
        "port": 0,
        "errorCode": 0,
        "mediaFormat": 0,
        "autoStart": False,
        "drawerMode": False,
        "dynamicPhone": False,
        "rotation": 0,
        "showNaviBar": False,
        "deviceWidth": 0,
        "deviceHeight": 0,
    }
    return pack_envelope(ctx, msg_id, None, 1, 1, data)

def build_desktop_autostart(ctx: Any, on: bool, msg_id: int | None = None) -> str:
    data = {
        "cmd": 36,
        "ver": "1.0.0",
        "autoStart": bool(on),
        "reply": 0,
        "view": 2,
        "port": 0,
        "errorCode": 0,
        "mediaFormat": 0,
        "drawerMode": False,
        "dynamicPhone": False,
        "lockscreenState": 0,
        "rotation": 0,
        "showNaviBar": False,
        "deviceWidth": 0,
        "deviceHeight": 0,
    }
    return pack_envelope(ctx, msg_id, None, 1, 1, data)

def build_rdp_client_rotation(ctx: Any, rotation: int, msg_id: int | None = None) -> str:
    data = {
        "cmd": RDP_CLIENT_CMD_DISPLAY_ROTATION,
        "ver": 1,
        "rotation": int(rotation),
        "displayRotation": {"rotation": int(rotation)},
    }
    return pack_envelope(ctx, msg_id, None, 1, 1, data)

def build_display_query(
    ctx: Any,
    display_type: int = DISPLAY_TYPE_REMOTE,
    service_id: int = 10,
) -> str:
    data = {"type": int(display_type), "serviceId": int(service_id)}
    return pack_envelope(ctx, DISPLAY_MSG_ID_QUERY, None, 10, 10, data)

def build_display_set_resolution(
    ctx: Any,
    width: int,
    height: int,
    fps: float = 60.0,
    display_type: int = DISPLAY_TYPE_REMOTE,
) -> str:
    w_l, h_l = max(64, int(width) & ~1), max(64, int(height) & ~1)
    data = {
        "type": int(display_type),
        "resolution": {"width": w_l, "height": h_l, "fps": float(fps)},
    }
    return pack_envelope(ctx, DISPLAY_MSG_ID_SET_RESOLUTION, None, 10, 10, data)

def build_display_set_mode(
    ctx: Any,
    mode: int = DISPLAY_MODE_DESKTOP,
    display_type: int = DISPLAY_TYPE_REMOTE,
) -> str:
    data = {"type": int(display_type), "mode": int(mode)}
    return pack_envelope(ctx, DISPLAY_MSG_ID_SET_DISPLAY_MODE, None, 10, 10, data)
