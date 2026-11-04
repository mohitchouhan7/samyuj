from __future__ import annotations

from typing import Any

from samyuj.protocol.constants import APPSTREAM_SOURCE_PC_APP
from samyuj.protocol.envelope import pack_envelope

def build_appstream_peer_start(ctx: Any, req_frame: dict[str, Any]) -> str:
    sd = (req_frame.get("data") or {}).get("data") or {}
    req_outer = req_frame.get("data") or {}
    pkg_name = sd.get("pkg", "desktop")
    res = getattr(ctx, "desktop_res", (1920, 1080))
    rw, rh = int(res[0]), int(res[1])
    w, hh = max(rw, rh), min(rw, rh)
    if pkg_name in ("mirror", "mobile") and (w < 1920 or hh < 1080):
        w, hh = max(1920, w), max(1080, hh)
    pw, ph = getattr(ctx, "as_phy", (597, 336))
    data = {
        "cmd": 1,
        "ver": "1",
        "data": {
            "pkg": pkg_name,
            "userId": sd.get("userId", 0),
            "source": sd.get("source", 1),
            "sessionId": sd.get("sessionId", req_outer.get("sessionId")),
            "deviceId": sd.get("deviceId") or req_frame.get("deviceId"),
            "realDeviceId": (
                sd.get("realDeviceId") or sd.get("deviceId") or req_frame.get("deviceId")
            ),
            "instanceId": int(getattr(ctx, "as_instance_id", 1)),
            "port": 0,
            "width": w,
            "height": hh,
            "displayWidth": 0,
            "displayHeight": 0,
            "displayScale": float(getattr(ctx, "as_scale", 1.0)),
            "displayDensity": int(getattr(ctx, "as_density", 160)),
            "phyWidth": int(pw),
            "phyHeight": int(ph),
            "align": 32,
            "supportedMediaFormat": 1,
            "supportWebRTC": 1,
            "supportCustomDisplay": False,
            "vrSupportUnfolded": False,
        },
    }
    return pack_envelope(ctx, req_frame.get("msgId"), None, 15, 15, data)

def build_appstream_stream_success(
    ctx: Any,
    session_id: Any,
    is_webrtc: bool = False,
    webrtc_data: str | None = None,
    resize: int = 0,
) -> str:
    inner: dict[str, Any] = {
        "sessionId": session_id,
        "error": 0,
        "resize": int(resize),
        "isWebRTC": 1 if (is_webrtc and webrtc_data) else 0,
        "userId": 0,
    }
    if is_webrtc and webrtc_data:
        inner["webRTCData"] = webrtc_data
    data = {"cmd": 4, "ver": "1", "data": inner}
    return pack_envelope(ctx, None, None, 15, 15, data)

def build_appstream_heartbeat(ctx: Any) -> str:
    return pack_envelope(ctx, None, None, 15, 15, {"cmd": 9, "ver": "1"})

def build_appstream_webrtc_signal(
    ctx: Any,
    session_id: int,
    cand_json: str,
    pkg: str = "desktop",
    user_id: int = 0,
) -> str:
    inner = {
        "sessionId": session_id,
        "pkg": pkg,
        "userId": user_id,
        "cmp": None,
        "shortcutId": None,
        "type": "candidate",
        "data": cand_json,
    }
    return pack_envelope(ctx, None, None, 15, 15, {"cmd": 20, "ver": "1", "data": inner})

def build_appstream_stop(
    ctx: Any,
    session_id: Any,
    error: int = 0,
    msg_id: int | None = None,
) -> str:
    data = {
        "cmd": 7,
        "ver": "1",
        "data": {"sessionId": session_id, "error": int(error), "userId": 0},
    }
    return pack_envelope(ctx, msg_id, None, 15, 15, data)

def build_appstream_hb_pause(ctx: Any, msg_id: int | None = None) -> str:
    return pack_envelope(ctx, msg_id, None, 15, 15, {"cmd": 25, "ver": "1"})

def build_appstream_start(
    ctx: Any,
    pkg: str,
    cmp: str | None = None,
    user_id: int = 0,
    source: int = APPSTREAM_SOURCE_PC_APP,
    shortcut_id: str | None = None,
    width: int | None = None,
    height: int | None = None,
    density: int | None = None,
    scale: float | None = None,
    custom_display: bool | None = None,
    instance_id: int | None = None,
    msg_id: int | None = None,
) -> str:
    res = getattr(ctx, "desktop_res", (1920, 1080))
    rw = int(width or res[0])
    rh = int(height or res[1])
    pw, ph = getattr(ctx, "as_phy", (597, 336))
    iid = int(instance_id if instance_id is not None else getattr(ctx, "as_instance_id", 1))
    use_custom = bool(
        custom_display
        if custom_display is not None
        else getattr(ctx, "as_custom_display", False)
    )
    if use_custom:
        w, hh = max(64, int(rw) & ~1), max(64, int(rh) & ~1)
    else:
        w, hh = max(rw, rh), min(rw, rh)
    inner: dict[str, Any] = {
        "pkg": pkg,
        "userId": int(user_id),
        "source": int(source),
        "instanceId": iid,
        "port": 0,
        "width": w,
        "height": hh,
        "displayWidth": w if use_custom else 0,
        "displayHeight": hh if use_custom else 0,
        "displayScale": float(scale if scale is not None else getattr(ctx, "as_scale", 1.0)),
        "displayDensity": int(density if density is not None else getattr(ctx, "as_density", 160)),
        "phyWidth": int(pw),
        "phyHeight": int(ph),
        "align": 32,
        "supportedMediaFormat": 1,
        "supportWebRTC": 1,
        "supportCustomDisplay": use_custom,
        "vrSupportUnfolded": False,
    }
    if cmp:
        inner["cmp"] = cmp
    if shortcut_id:
        inner["shortcutId"] = shortcut_id
    return pack_envelope(ctx, msg_id, None, 15, 15, {"cmd": 1, "ver": "1", "data": inner})

def build_appstream_pause(ctx: Any, session_id: Any, msg_id: int | None = None) -> str:
    return pack_envelope(
        ctx, msg_id, None, 15, 15, {"cmd": 5, "ver": "1", "data": {"sessionId": session_id}}
    )

def build_appstream_resume(ctx: Any, session_id: Any, msg_id: int | None = None) -> str:
    return pack_envelope(
        ctx, msg_id, None, 15, 15, {"cmd": 6, "ver": "1", "data": {"sessionId": session_id}}
    )

def build_appstream_resize(
    ctx: Any,
    session_id: Any,
    display_id: int,
    width: int,
    height: int,
    density: int | None = None,
    scale: float | None = None,
    custom_display: bool = True,
    pkg: str | None = None,
    msg_id: int | None = None,
) -> str:
    w, hh = int(width), int(height)
    pw, ph = getattr(ctx, "as_phy", (597, 336))
    dpi = int(density if density is not None else getattr(ctx, "as_density", 160))
    sc = float(scale if scale is not None else getattr(ctx, "as_scale", 1.0))
    inner: dict[str, Any] = {
        "sessionId": session_id,
        "displayId": int(display_id),
        "userId": 0,
        "width": w,
        "height": hh,
        "displayWidth": w,
        "displayHeight": hh,
        "displayDensity": dpi,
        "displayScale": sc,
        "phyWidth": int(pw),
        "phyHeight": int(ph),
        "supportWebRTC": 1,
        "supportCustomDisplay": bool(custom_display),
    }
    if pkg:
        inner["pkg"] = str(pkg)
    return pack_envelope(ctx, msg_id, None, 15, 15, {"cmd": 17, "ver": "1", "data": inner})

def build_appstream_move_monitor(
    ctx: Any,
    session_id: Any,
    display_id: int,
    msg_id: int | None = None,
) -> str:
    return pack_envelope(
        ctx,
        msg_id,
        None,
        15,
        15,
        {"cmd": 15, "ver": "1", "data": {"sessionId": session_id, "displayId": int(display_id)}},
    )

def build_appstream_applist_sync(
    ctx: Any,
    apps: list[dict[str, Any]],
    msg_id: int | None = None,
) -> str:
    return pack_envelope(
        ctx, msg_id, None, 15, 15, {"cmd": 24, "ver": "1", "data": {"appList": apps}}
    )

def build_appstream_app_merge(
    ctx: Any,
    session_id: Any,
    merge: bool = True,
    msg_id: int | None = None,
) -> str:
    return pack_envelope(
        ctx,
        msg_id,
        None,
        15,
        15,
        {"cmd": 12, "ver": "1", "data": {"sessionId": session_id, "merge": bool(merge)}},
    )

def build_appstream_focus_changed(
    ctx: Any,
    session_id: Any,
    display_id: int = 0,
    focused: bool = True,
    pkg: str | None = None,
    msg_id: int | None = None,
) -> str:
    inner: dict[str, Any] = {
        "sessionId": session_id,
        "displayId": int(display_id),
        "hasFocus": bool(focused),
        "focused": bool(focused),
    }
    if pkg:
        inner["pkg"] = str(pkg)
    return pack_envelope(ctx, msg_id, None, 15, 15, {"cmd": 13, "ver": "1", "data": inner})

def build_appstream_move_to_top(
    ctx: Any,
    session_id: Any,
    display_id: int = 0,
    pkg: str | None = None,
    msg_id: int | None = None,
) -> str:
    inner: dict[str, Any] = {"sessionId": session_id, "displayId": int(display_id)}
    if pkg:
        inner["pkg"] = str(pkg)
    return pack_envelope(ctx, msg_id, None, 15, 15, {"cmd": 14, "ver": "1", "data": inner})

def build_appstream_rotation(
    ctx: Any,
    session_id: Any,
    display_id: int,
    rotation: int,
    msg_id: int | None = None,
) -> str:
    return pack_envelope(
        ctx,
        msg_id,
        None,
        15,
        15,
        {
            "cmd": 16,
            "ver": "1",
            "data": {
                "sessionId": session_id,
                "displayId": int(display_id),
                "rotation": int(rotation),
            },
        },
    )

def parse_appstream_audio_state(inner: dict[str, Any]) -> dict[str, Any]:
    st_val = inner.get("state", inner.get("audioState", inner.get("enable")))
    paused = st_val in (0, False, "0", "false", "paused", "stopped")
    return {"raw": dict(inner), "paused": paused}

def build_applist_request(ctx: Any, msg_id: int | None = None) -> str:
    return pack_envelope(ctx, msg_id, None, 17, 17, {"cmd": 2, "ver": "1"})

def build_runningapps_request(ctx: Any, msg_id: int | None = None) -> str:
    return pack_envelope(ctx, msg_id, None, 17, 17, {"cmd": 5, "ver": "1"})

def build_app_blacklist(
    ctx: Any,
    pkgs: list[str] | None = None,
    cmps: list[dict[str, Any]] | None = None,
    msg_id: int | None = None,
) -> str:
    data = {
        "cmd": 4,
        "ver": "1",
        "blacklist": list(pkgs or []),
        "blackCmpList": list(cmps or []),
    }
    return pack_envelope(ctx, msg_id, None, 17, 17, data)
