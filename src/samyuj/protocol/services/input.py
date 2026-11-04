from __future__ import annotations

from typing import Any

from samyuj.protocol.constants import (
    BTN_HWHEEL,
    BTN_WHEEL,
    DRAG_CMD_DATA,
    DRAG_CMD_DROP,
    DRAG_CMD_ENTER,
    EVT_CANCEL,
    IN_CMD_IME_STATE,
    IN_CMD_KEY,
    IN_CMD_MOUSE_BTN,
    IN_CMD_MOUSE_MOVE,
    IN_CMD_REQUEST_CAP,
    IN_CMD_SET_MODE,
    IN_CMD_TEXT,
    IN_CMD_TOUCH,
    KEY_FLAG_NATIVE,
)
from samyuj.protocol.envelope import pack_envelope

def _input_env_msg(ctx: Any, cmd: int, inner: dict[str, Any]) -> str:
    data = {"cmd": int(cmd), "data": inner, "instanceId": 0, "messageId": 0}
    return pack_envelope(ctx, None, None, 16, 16, data)

def build_input_request_cap(ctx: Any, display_id: int, caps: int = 7, ver: int = 1) -> str:
    return _input_env_msg(ctx, IN_CMD_REQUEST_CAP, {"ver": int(ver), "displayId": int(display_id), "caps": int(caps)})

def build_input_key(
    ctx: Any,
    display_id: int,
    key: int,
    down: bool,
    native: bool = True,
    meta_state: int = 0,
    flags: int | None = None,
) -> str:
    eff_flags = int(flags) if flags is not None else (KEY_FLAG_NATIVE if native else 0)
    inner = {
        "displayId": int(display_id),
        "key": int(key),
        "down": bool(down),
        "metaState": int(meta_state),
        "flags": eff_flags,
        "action": 1 if down else 0,
    }
    return _input_env_msg(ctx, IN_CMD_KEY, inner)

def build_input_mouse_move(ctx: Any, display_id: int, x: int, y: int) -> str:
    return _input_env_msg(
        ctx, IN_CMD_MOUSE_MOVE, {"displayId": int(display_id), "x": int(x), "y": int(y)}
    )

def build_input_mouse_button(
    ctx: Any,
    display_id: int,
    button_code: int,
    x: int,
    y: int,
    down: bool,
) -> str:
    return _input_env_msg(
        ctx,
        IN_CMD_MOUSE_BTN,
        {
            "displayId": int(display_id),
            "key": int(button_code),
            "x": int(x),
            "y": int(y),
            "down": bool(down),
            "mAction": 11 if down else 12,
            "buttonCode": int(button_code),
        },
    )

def build_input_mouse_wheel(
    ctx: Any,
    display_id: int,
    x: int,
    y: int,
    dy: float = 0.0,
    dx: float = 0.0,
) -> list[str]:
    frames: list[str] = []
    xi, yi = int(x), int(y)
    if abs(float(dy)) >= 1e-3:
        notches_v = max(1, min(6, int(round(abs(float(dy))))))
        wheel_up = float(dy) < 0.0
        for _ in range(notches_v):
            frames.append(build_input_mouse_button(ctx, int(display_id), BTN_WHEEL, xi, yi, wheel_up))
    if abs(float(dx)) >= 1e-3:
        notches_h = max(1, min(6, int(round(abs(float(dx))))))
        wheel_left = float(dx) < 0.0
        for _ in range(notches_h):
            frames.append(build_input_mouse_button(ctx, int(display_id), BTN_HWHEEL, xi, yi, wheel_left))
    return frames

def scroll_notch_distance(
    dy_notches: float,
    step: float = 0.12,
    cap: float = 0.50,
    min_notch: float = 0.35,
) -> float:
    d = float(dy_notches or 0.0)
    if abs(d) < 1e-6:
        return 0.0
    step_clamped = min(0.5, max(0.005, float(step)))
    notches = min(6.0, max(float(min_notch), abs(d)))
    eff_cap = 0.45 if step_clamped <= 0.095 else float(cap)
    mag = min(eff_cap, step_clamped * notches)
    return mag if d > 0.0 else -mag

def build_input_touch(
    ctx: Any,
    display_id: int,
    event: int,
    x: int,
    y: int,
    pointer_id: int = 0,
    pressure: float = 1.0,
    major: float = 6.0,
) -> str:
    return _input_env_msg(
        ctx,
        IN_CMD_TOUCH,
        {
            "displayId": int(display_id),
            "event": int(event),
            "pointerId": int(pointer_id),
            "x": int(x),
            "y": int(y),
            "pressure": float(pressure),
            "majorAxisSize": float(major),
        },
    )

def build_input_touch_cancel(
    ctx: Any,
    display_id: int,
    x: int = 0,
    y: int = 0,
    pointer_id: int = 0,
) -> str:
    return build_input_touch(ctx, int(display_id), EVT_CANCEL, int(x), int(y), int(pointer_id), pressure=0.0)

def build_input_ime_state(ctx: Any, display_id: int, show: bool) -> str:
    return _input_env_msg(ctx, IN_CMD_IME_STATE, {"displayId": int(display_id), "show": bool(show)})

def build_input_text(ctx: Any, display_id: int, text: str) -> str:
    return _input_env_msg(ctx, IN_CMD_TEXT, {"displayId": int(display_id), "text": str(text)})

def build_input_set_mode(ctx: Any, input_mode: int, touch_mode: int = 0) -> str:
    return _input_env_msg(
        ctx, IN_CMD_SET_MODE, {"inputMode": int(input_mode), "touchMode": int(touch_mode)}
    )

def _drag_env_msg(ctx: Any, cmd: int, fields: dict[str, Any]) -> str:
    data: dict[str, Any] = {"ver": "1.0", "cmd": int(cmd)}
    data.update(fields)
    return pack_envelope(ctx, None, None, 24, 24, data)

def build_drag_enter(
    ctx: Any,
    display_id: int,
    enter: bool = True,
    is_text: bool = False,
    thumb_b64: str = "",
) -> str:
    return _drag_env_msg(
        ctx,
        DRAG_CMD_ENTER,
        {
            "enter": 1 if enter else 0,
            "displayId": int(display_id),
            "text": 1 if is_text else 0,
            "thumb": thumb_b64,
        },
    )

def build_drag_data(
    ctx: Any,
    token: int,
    display_id: int,
    x: int,
    y: int,
    fex_id: int = 0,
    files: list[dict[str, Any]] | None = None,
    text: str = "",
    thumb_b64: str = "",
) -> str:
    fields: dict[str, Any] = {
        "token": int(token),
        "displayId": int(display_id),
        "x": int(x),
        "y": int(y),
        "text": text,
        "thumb": thumb_b64,
        "fexId": int(fex_id),
    }
    if files is not None:
        fields["file"] = files
    return _drag_env_msg(ctx, DRAG_CMD_DATA, fields)

def build_drop_data(
    ctx: Any,
    token: int,
    display_id: int,
    x: int,
    y: int,
    path: str = "",
) -> str:
    return _drag_env_msg(
        ctx,
        DRAG_CMD_DROP,
        {"token": int(token), "displayId": int(display_id), "path": path, "x": int(x), "y": int(y)},
    )
