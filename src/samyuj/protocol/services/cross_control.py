from __future__ import annotations

from typing import Any

from samyuj.protocol.constants import (
    UC_CMD_DISCONNECT,
    UC_CMD_DISCONNECT_RESP,
    UC_CMD_PC_REQUEST,
    UC_CMD_PC_RESPONSE,
    UC_CMD_SHOW_POINTER,
    UC_CMD_SHOW_SCREEN,
)
from samyuj.protocol.envelope import pack_envelope

def build_uc_msg(ctx: Any, cmd: int, inner: dict[str, Any] | None = None) -> str:
    data: dict[str, Any] = {"cmd": int(cmd), "ver": "1"}
    data.update(inner or {})
    return pack_envelope(ctx, None, None, 26, 26, data)

def build_uc_request(
    ctx: Any,
    cid: int,
    trigger: int = 1,
    width: int = 1920,
    height: int = 1080,
    support_touch: bool = False,
) -> str:
    return build_uc_msg(
        ctx,
        UC_CMD_PC_REQUEST,
        {
            "cid": int(cid),
            "trigger": int(trigger),
            "controller": True,
            "width": int(width),
            "height": int(height),
            "supportTouch": bool(support_touch),
            "code": 1,
        },
    )

def build_uc_response(
    ctx: Any,
    cid: int,
    allow: bool = True,
    width: int = 1920,
    height: int = 1080,
) -> str:
    return build_uc_msg(
        ctx,
        UC_CMD_PC_RESPONSE,
        {
            "cid": int(cid),
            "code": 1 if allow else 2,
            "width": int(width),
            "height": int(height),
        },
    )

def build_uc_disconnect(ctx: Any, cid: int) -> str:
    return build_uc_msg(ctx, UC_CMD_DISCONNECT, {"cid": int(cid)})

def build_uc_disconnect_resp(ctx: Any, cid: int) -> str:
    return build_uc_msg(ctx, UC_CMD_DISCONNECT_RESP, {"cid": int(cid)})

def build_uc_show_pointer(ctx: Any, show: bool) -> str:
    return build_uc_msg(ctx, UC_CMD_SHOW_POINTER, {"show": bool(show)})

def build_uc_show_screen(ctx: Any, on: bool) -> str:
    return build_uc_msg(ctx, UC_CMD_SHOW_SCREEN, {"showScreen": bool(on)})
