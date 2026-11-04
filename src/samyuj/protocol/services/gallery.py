from __future__ import annotations

from typing import Any

from samyuj.protocol.envelope import pack_envelope

def build_features_request(
    ctx: Any,
    cmd: int,
    data_payload: Any = None,
    device_id: str | None = None,
    src_service_id: int = 19,
    msg_id: int | None = None,
) -> str:
    target_dev = (
        device_id
        if device_id is not None
        else (getattr(ctx, "pc_real_device_id", None) or getattr(ctx, "device_id_b64", ""))
    )
    body: dict[str, Any] = {"ver": "1", "cmd": int(cmd), "deviceId": str(target_dev)}
    if data_payload is not None:
        body["data"] = data_payload
    return pack_envelope(ctx, msg_id, None, 1001, int(src_service_id), body)

def build_features_streaming_list_reply(
    ctx: Any,
    dest_service_id: int,
    apps: list[dict[str, Any]] | None = None,
    device_id: str | None = None,
    msg_id: int | None = None,
) -> str:
    target_dev = (
        device_id
        or getattr(ctx, "pc_real_device_id", None)
        or getattr(ctx, "device_id_b64", "")
    )
    app_items = []
    source_apps = apps if apps is not None else (getattr(ctx, "app_running", []) or [])
    for a in source_apps:
        if isinstance(a, dict) and a.get("pkg"):
            app_items.append({"pkg": str(a["pkg"]), "userId": int(a.get("userId", 0) or 0)})
    body = {"ver": "1", "cmd": 8, "apps": app_items}
    return pack_envelope(ctx, msg_id, str(target_dev), int(dest_service_id), 1001, body)

def build_gallery_frame(
    ctx: Any,
    cmd: int = 1,
    category: int = 7,
    path: str = "/DCIM/Camera",
    msg_id: int | None = None,
    src_service_id: int = 205,
) -> str:
    body = {
        "ver": "1",
        "cmd": int(cmd),
        "category": int(category),
        "path": str(path),
    }
    return pack_envelope(ctx, msg_id, None, 205, int(src_service_id), body)
