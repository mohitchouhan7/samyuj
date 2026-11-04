from __future__ import annotations

import base64
import json
import mimetypes
import os
import time
from typing import Any

from samyuj.protocol.envelope import pack_envelope

def gen_task_id() -> str:
    rnd = base64.urlsafe_b64encode(os.urandom(9)).decode("ascii").rstrip("=")[:12]
    return f"{rnd}{int(time.time())}"

def guess_mime(name: str) -> str:
    return (
        mimetypes.guess_type(name)[0]
        or ("image/png" if name.lower().endswith(".png") else "application/octet-stream")
    )

def build_filestation_announce(
    ctx: Any,
    frame: dict[str, Any] | None = None,
    conn: dict[str, Any] | None = None,
    chosen_ip: str | None = None,
) -> tuple[str, dict[str, Any]]:
    fs = getattr(ctx, "fs_ports", None) or {"portmap": 0, "mountd": 0, "nfsd": 0}
    ident = getattr(ctx, "identity", None)
    fp_hex_up = str(getattr(ident, "fingerprint", getattr(ctx, "fingerprint_hex", ""))).upper()
    ip_val = chosen_ip or (ctx._conn_local_ip(conn) if (conn is not None and hasattr(ctx, "_conn_local_ip")) else getattr(ctx, "lan_ip", "127.0.0.1"))
    eff_dt2 = (
        (1 if getattr(ctx, "fs_dt2type", 2) == 1 else 2)
        if getattr(ctx, "filestation", "server") == "server"
        else int(getattr(ctx, "fs_dt2type", 2))
    )
    params = {
        "type": 1,
        "path": getattr(ctx, "fs_path", "/C/nfs_demo"),
        "ip": ip_val,
        "port": fs["portmap"],
        "nfsport": fs["mountd"],
        "nfsDataPort": fs["nfsd"],
        "fp": fp_hex_up,
        "fp2": fp_hex_up,
        "dt2type": eff_dt2,
        "capability": 25,
        "deviceId": getattr(ctx, "device_id_b64", ""),
        "timestamp": int(time.time() * 1000),
        "screen_state": 1,
        "nfs_version": 8,
        "nfs_capability": 25,
        "nfs_dependency": 0,
    }
    msg_id = (frame or {}).get("msgId") if frame else None
    reply = pack_envelope(
        ctx,
        msg_id,
        None,
        168,
        168,
        {"data": json.dumps(params, separators=(",", ":")), "cmd": 1001, "ver": "1.0"},
    )
    return reply, params

def build_phonefs_handshake(ctx: Any) -> tuple[str, dict[str, Any]]:
    ident = getattr(ctx, "identity", None)
    fp_up = str(getattr(ident, "fingerprint", getattr(ctx, "fingerprint_hex", ""))).upper()
    params = {
        "type": 0,
        "path": "",
        "port": 0,
        "fp": fp_up,
        "fp2": fp_up,
        "deviceId": getattr(ctx, "device_id_b64", ""),
        "capability": 1,
        "timestamp": int(time.time() * 1000),
        "nfs_version": 8,
        "nfs_dependency": 0,
        "dt2type": 2,
    }
    msg = pack_envelope(
        ctx,
        None,
        None,
        168,
        168,
        {"data": json.dumps(params, separators=(",", ":")), "cmd": 1001, "ver": "1.0"},
    )
    return msg, params

def build_fileex_announce_task(ctx: Any, task: Any) -> str:
    ident = getattr(ctx, "identity", None)
    fp_hex = str(getattr(ident, "fingerprint", getattr(ctx, "fingerprint_hex", "")))
    data = {
        "ver": "1",
        "cmd": 1,
        "port": int(getattr(task, "server_port", getattr(ctx, "xfer_port", 0))),
        "serviceId": 100,
        "fingerprint": fp_hex,
        "token": getattr(task, "token", ""),
        "autoDownload": True,
        "errorcode": 0,
        "paired": 1,
        "requestId": getattr(task, "task_id", getattr(task, "id", "")),
    }
    return pack_envelope(ctx, None, None, 2, 2, data)

def build_fileex_text_share(ctx: Any, text: str, request_id: str | None = None) -> str:
    rid = request_id or gen_task_id()
    data = {"ver": "1.0.0", "cmd": 4, "text": str(text), "requestId": rid}
    return pack_envelope(ctx, None, None, 2, 2, data)
