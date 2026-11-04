from __future__ import annotations

from typing import Any

from samyuj.protocol.constants import (
    CLIP_CLOSE,
    CLIP_CONFIRM,
    CLIP_GLOBAL_DISABLE,
    CLIP_GLOBAL_ENABLE,
    CLIP_NEGO,
    CLIP_PULL,
    CLIP_PULL_CANCEL,
    CLIP_PULL_RESP,
    CLIP_SUPPORT_FILES,
    CLIP_SYNCED,
    CLIP_UPDATE,
)
from samyuj.protocol.envelope import pack_envelope

def build_clipboard_msg(ctx: Any, clip_msg_id: int, inner: dict[str, Any]) -> str:
    data = {"msgId": int(clip_msg_id), "data": inner}
    return pack_envelope(ctx, None, None, 9, 9, data)

def build_clipboard_nego(ctx: Any, version: int = 1) -> str:
    return build_clipboard_msg(ctx, CLIP_NEGO, {"version": int(version)})

def build_clipboard_confirm(ctx: Any, version: int = 1) -> str:
    return build_clipboard_msg(ctx, CLIP_CONFIRM, {"version": int(version)})

def build_clipboard_update(ctx: Any, clip_id: int, text: str) -> str:
    return build_clipboard_msg(
        ctx,
        CLIP_UPDATE,
        {"clipId": int(clip_id), "text": str(text), "filelist": [], "storage": "pc"},
    )

def build_clipboard_pull(ctx: Any, request_id: str, clip_id: int = 1) -> str:
    return build_clipboard_msg(
        ctx, CLIP_PULL, {"requestId": str(request_id), "clipId": int(clip_id)}
    )

def build_clipboard_pull_response(
    ctx: Any,
    request_id: Any,
    clip_id: int,
    text: str,
    error: int = 0,
) -> str:
    return build_clipboard_msg(
        ctx,
        CLIP_PULL_RESP,
        {
            "requestId": request_id,
            "clipId": int(clip_id),
            "error": int(error),
            "text": str(text),
            "filelist": [],
            "storage": "pc",
        },
    )

def build_clipboard_pull_cancel(ctx: Any, request_id: str = "", clip_id: int = 1) -> str:
    return build_clipboard_msg(
        ctx, CLIP_PULL_CANCEL, {"requestId": str(request_id), "clipId": int(clip_id)}
    )

def build_clipboard_close(ctx: Any) -> str:
    return build_clipboard_msg(ctx, CLIP_CLOSE, {})

def build_clipboard_support_files(ctx: Any, enabled: bool = True) -> str:
    return build_clipboard_msg(ctx, CLIP_SUPPORT_FILES, {"enabled": bool(enabled)})

def build_clipboard_synced(ctx: Any, clip_id: int = 1, enabled: bool = True) -> str:
    return build_clipboard_msg(
        ctx, CLIP_SYNCED, {"clipId": int(clip_id), "enabled": bool(enabled)}
    )

def build_clipboard_global_enable(ctx: Any, enabled: bool = True) -> str:
    msg_id = CLIP_GLOBAL_ENABLE if enabled else CLIP_GLOBAL_DISABLE
    return build_clipboard_msg(ctx, msg_id, {"enabled": bool(enabled)})
