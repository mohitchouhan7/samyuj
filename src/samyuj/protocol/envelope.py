from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .constants import DEFAULT_LAN_IP, DEFAULT_PC_CAPS, DEFAULT_SS_RES_LIST, PC_SS_VERSION

@dataclass(frozen=True)
class EnvelopeContext:
    device_id_b64: str
    token_b64: str
    pc_real_device_id: str | None = None
    device_name: str = ""
    lan_ip: str = DEFAULT_LAN_IP
    port: int = 0
    pc_caps: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_PC_CAPS))
    pc_sn: str = ""
    fingerprint_hex: str = ""
    fp_b64: str = ""
    sender_id: int | None = None

    ss_ShareType: str = "extend"
    ss_width: int = 1920
    ss_height: int = 1080
    ss_fps: int = 30
    ss_dpi: int = 160
    ss_bitrate: int = 5 * 1024 * 1024
    ss_codec: str = "h264"
    ss_version: int = PC_SS_VERSION
    ss_res_list: list[dict[str, int]] = field(
        default_factory=lambda: [dict(x) for x in DEFAULT_SS_RES_LIST]
    )

    webcam_port: int = 0

    filestation: str = "server"
    fs_dt2type: int = 2
    fs_path: str = "/C/nfs_demo"
    fs_ports: dict[str, int] = field(
        default_factory=lambda: {"portmap": 0, "mountd": 0, "nfsd": 0}
    )

    xfer_port: int = 0
    _msg_id: int = 0

    def next_msg_id(self) -> int:
        cur = int(getattr(self, "_msg_id", 0))
        nxt = (cur + 1) % 2147483647
        object.__setattr__(self, "_msg_id", nxt)
        return nxt

def _resolve_msg_id(ctx: Any, msg_id: int | None) -> int:
    if msg_id is not None:
        return int(msg_id)
    next_fn = getattr(ctx, "next_msg_id", None)
    if callable(next_fn):
        return int(next_fn())
    cur = int(getattr(ctx, "_msg_id", 0)) + 1
    try:
        setattr(ctx, "_msg_id", cur)
    except Exception:
        object.__setattr__(ctx, "_msg_id", cur)
    return cur

def build_envelope(
    ctx: Any,
    msg_id: int | None,
    device_id: str | None,
    service_id: int,
    msg_type: int,
    data: Any,
    sender: int | None = None,
    sender_id: int | None = None,
) -> dict[str, Any]:
    mid = _resolve_msg_id(ctx, msg_id)
    dev_id = (
        device_id
        if device_id is not None
        else getattr(ctx, "device_id_b64", "")
    )
    if not dev_id or not str(dev_id).strip():
        raise ValueError("device_id_b64 is required to build a protocol envelope")
    env: dict[str, Any] = {
        "msgId": mid,
        "deviceId": str(dev_id),
        "serviceId": int(service_id),
        "srcServiceId": int(msg_type),
        "data": data,
    }
    sid = (
        sender
        if sender is not None
        else (sender_id if sender_id is not None else getattr(ctx, "sender_id", None))
    )
    if sid is not None:
        env["senderId"] = sid
    return env

def pack_envelope(
    ctx: Any,
    msg_id: int | None,
    device_id: str | None,
    service_id: int,
    msg_type: int,
    data: Any,
    sender: int | None = None,
    sender_id: int | None = None,
) -> str:
    env = build_envelope(
        ctx,
        msg_id,
        device_id,
        service_id,
        msg_type,
        data,
        sender=sender,
        sender_id=sender_id,
    )
    return json.dumps(env, separators=(",", ":"), ensure_ascii=False)

def unpack_envelope(raw: str | bytes) -> dict[str, Any]:
    text = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else raw
    outer = json.loads(text)
    if not isinstance(outer, dict):
        return {"raw": outer}
    d = outer.get("data")
    if isinstance(d, str) and d and d[0] in "{[":
        try:
            outer = dict(outer)
            outer["data"] = json.loads(d)
        except json.JSONDecodeError:
            pass
    return outer

_env = build_envelope
_pack = pack_envelope
_unpack = unpack_envelope
