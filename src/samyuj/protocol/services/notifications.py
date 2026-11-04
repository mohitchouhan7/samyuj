from __future__ import annotations

from typing import Any

from samyuj.protocol.envelope import pack_envelope

def build_ntf_pc_msg(
    ctx: Any,
    key: str,
    event: int,
    data: dict[str, Any] | None = None,
) -> str:
    body: dict[str, Any] = {"cmd": 0, "version": "1.0", "key": str(key), "event": int(event)}
    if data is not None:
        body["data"] = data
    return pack_envelope(ctx, None, None, 5, 5, body)

def build_ntf_block_pkg(ctx: Any, pkg: str) -> str:
    body = {"cmd": 1, "version": "1.0", "pkg": str(pkg)}
    return pack_envelope(ctx, None, None, 5, 5, body)

def build_ntf_open_settings(ctx: Any) -> str:
    body = {"cmd": 2, "version": "1.0"}
    return pack_envelope(ctx, None, None, 5, 5, body)
