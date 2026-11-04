from __future__ import annotations

from typing import Any

from samyuj.protocol.envelope import pack_envelope

def build_webcam_trigger(
    ctx: Any,
    heartbeat: bool = True,
    supported_media_format: int = 1,
    msg_id: int | None = None,
) -> str:
    data = {
        "cmd": 0,
        "ver": "1",
        "data": {
            "heartbeat": 1 if heartbeat else 0,
            "supportedMediaFormat": int(supported_media_format),
            "port": int(getattr(ctx, "webcam_port", 0) or getattr(ctx, "port", 0)),
        },
    }
    raw = pack_envelope(ctx, msg_id, None, 3, 3, data)
    if "isReply" in raw:
        raise AssertionError("webcam trigger must not contain 'isReply'")
    return raw

def build_webcam_start(ctx: Any, port: int | None = None, msg_id: int | None = None) -> str:
    eff_port = int(port if (port is not None and int(port) > 0) else (getattr(ctx, "webcam_port", 0) or getattr(ctx, "port", 0)))
    return pack_envelope(ctx, msg_id, None, 3, 3, {"cmd": 1, "ver": "1", "data": {"port": eff_port}})

def build_webcam_stop(ctx: Any, msg_id: int | None = None) -> str:
    return pack_envelope(ctx, msg_id, None, 3, 3, {"cmd": 2, "ver": "1"})

def build_webcam_keepalive(ctx: Any, msg_id: int | None = None) -> str:
    return pack_envelope(ctx, msg_id, None, 3, 3, {"cmd": 13, "ver": "1"})

def build_webcam_camera_query(ctx: Any, msg_id: int | None = None) -> str:
    return pack_envelope(ctx, msg_id, None, 3, 3, {"cmd": 3, "ver": "1"})

def build_webcam_switch_camera(
    ctx: Any,
    camera_id: int | None = None,
    msg_id: int | None = None,
) -> str:
    payload: dict[str, Any] = {"cmd": 4, "ver": "1"}
    if camera_id is not None:
        payload["data"] = {"cameraId": int(camera_id)}
    return pack_envelope(ctx, msg_id, None, 3, 3, payload)

def build_webcam_toggle_flash(ctx: Any, msg_id: int | None = None) -> str:
    return pack_envelope(ctx, msg_id, None, 3, 3, {"cmd": 7, "ver": "1"})

def build_webcam_set_angle(ctx: Any, state: int = 0, msg_id: int | None = None) -> str:
    return pack_envelope(
        ctx, msg_id, None, 3, 3, {"cmd": 6, "ver": "1", "data": {"state": int(state)}}
    )

def build_webcam_set_tracking(ctx: Any, state: int = 1, msg_id: int | None = None) -> str:
    return pack_envelope(
        ctx, msg_id, None, 3, 3, {"cmd": 8, "ver": "1", "data": {"state": int(state)}}
    )

def build_webcam_set_zoom(ctx: Any, level: int, msg_id: int | None = None) -> str:
    return pack_envelope(
        ctx, msg_id, None, 3, 3, {"cmd": 9, "ver": "1", "data": {"state": max(0, int(level))}}
    )

def build_webcam_set_mute(ctx: Any, mute: bool = True, msg_id: int | None = None) -> str:
    return pack_envelope(
        ctx, msg_id, None, 3, 3, {"cmd": 10, "ver": "1", "data": {"state": 1 if mute else 0}}
    )

def build_webcam_pause(ctx: Any, msg_id: int | None = None) -> str:
    return pack_envelope(ctx, msg_id, None, 3, 3, {"cmd": 11, "ver": "1"})

def build_webcam_resume(ctx: Any, msg_id: int | None = None) -> str:
    return pack_envelope(ctx, msg_id, None, 3, 3, {"cmd": 12, "ver": "1"})

def build_webcam_webrtc_signal(
    ctx: Any,
    session_id: Any,
    kind: str,
    payload: str,
    msg_id: int | None = None,
) -> str:
    try:
        sid_int = int(0 if session_id is None else session_id)
    except (TypeError, ValueError):
        sid_int = 0
    data = {
        "cmd": 17 if kind == "answer" else 18,
        "ver": "1",
        "data": {
            "sessionId": sid_int,
            "type": "answer" if kind == "answer" else "candidate",
            "webRTCData": payload,
        },
    }
    return pack_envelope(ctx, msg_id, None, 3, 3, data)
