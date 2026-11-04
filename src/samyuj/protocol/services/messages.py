from __future__ import annotations

import time
from typing import Any

from samyuj.protocol.constants import SVC_PC_UI
from samyuj.protocol.envelope import pack_envelope

IOS_MESSAGES_ERROR = "iPhone Messages requires Bluetooth OBEX (MAP/PBAP/MNS) — not supported on Linux"
SERVICE_ID_PC_UI = SVC_PC_UI

def build_message_frame(
    ctx: Any,
    cmd: int,
    data_payload: Any = None,
    pid: int = 110,
    ver: str = "4.0",
    msg_id: int | None = None,
    src_service_id: int = 204,
) -> str:
    outer: dict[str, Any] = {"ver": str(ver), "cmd": int(cmd), "pId": int(pid)}
    if data_payload is not None:
        outer["data"] = data_payload
    return pack_envelope(ctx, msg_id, None, 204, int(src_service_id), outer)

def build_msg_notify_ready(ctx: Any, msg_id: int | None = None) -> str:
    return build_message_frame(ctx, cmd=18, data_payload=None, pid=110, ver="4.0", msg_id=msg_id)

def build_msg_request_contacts(ctx: Any, msg_id: int | None = None) -> str:
    return build_message_frame(ctx, cmd=1, data_payload=None, pid=110, ver="4.0", msg_id=msg_id)

def build_msg_request_messages(
    ctx: Any,
    msg_type: int = 1,
    count: int = 100,
    msg_id: int | None = None,
) -> str:
    return build_message_frame(
        ctx,
        cmd=10,
        data_payload={"type": int(msg_type), "count": int(count)},
        pid=110,
        ver="4.0",
        msg_id=msg_id,
    )

def build_msg_request_sim_cards(ctx: Any, msg_id: int | None = None) -> str:
    return build_message_frame(ctx, cmd=12, data_payload=None, pid=110, ver="4.0", msg_id=msg_id)

def build_msg_send_request(
    ctx: Any,
    to_addresses: list[str] | str,
    body: str,
    sub_id: int = -1,
    request_id: str | int | None = None,
    msg_id_str: str | int = 0,
    message_type: int = 1,
    medias: list[dict[str, Any]] | None = None,
    msg_id: int | None = None,
) -> str:
    if isinstance(to_addresses, str):
        to_list = [a.strip() for a in to_addresses.split(",") if a.strip()]
    else:
        to_list = [str(a).strip() for a in (to_addresses or []) if str(a).strip()]
    if request_id is None or request_id == "":
        req_id_val: int | str = int(time.time() * 1000) % 2147483647
    elif isinstance(request_id, int):
        req_id_val = int(request_id)
    elif str(request_id).lstrip("-").isdigit():
        req_id_val = int(request_id)
    else:
        req_id_val = str(request_id)
    msg_id_s = str(msg_id_str if msg_id_str is not None else "").strip()
    msg_id_val: int | str = int(msg_id_s) if msg_id_s.lstrip("-").isdigit() else 0
    bean: dict[str, Any] = {
        "requestId": req_id_val,
        "msgId": msg_id_val,
        "subId": int(sub_id),
        "messageType": int(message_type),
        "toAddress": to_list,
        "fromAddress": "",
        "body": str(body or ""),
        "medias": list(medias) if medias else [],
        "read": 1,
        "date": int(time.time() * 1000),
    }
    return build_message_frame(ctx, cmd=4, data_payload=bean, pid=110, ver="4.0", msg_id=msg_id)

def build_msg_mark_read(
    ctx: Any,
    target_msg_id: str | int,
    status: int = 1,
    msg_type: int = 1,
    msg_id: int | None = None,
) -> str:
    mid_s = str(target_msg_id or "").strip()
    mid_val: int | str = int(mid_s) if mid_s.lstrip("-").isdigit() else mid_s
    return build_message_frame(
        ctx,
        cmd=8,
        data_payload={"msgId": mid_val, "status": int(status), "type": int(msg_type)},
        pid=110,
        ver="4.0",
        msg_id=msg_id,
    )

def build_msg_permission_request(
    ctx: Any,
    prompt_ui: bool = True,
    msg_id: int | None = None,
) -> str:
    return build_message_frame(
        ctx, cmd=16 if prompt_ui else 20, data_payload=None, pid=110, ver="4.0", msg_id=msg_id
    )

def build_msg_get_mms_parts(
    ctx: Any,
    id_list: list[str],
    msg_id: int | None = None,
) -> str:
    return build_message_frame(
        ctx,
        cmd=22,
        data_payload={"idList": [str(x) for x in (id_list or [])]},
        pid=110,
        ver="4.0",
        msg_id=msg_id,
    )

def build_msg_check_availability(ctx: Any, msg_id: int | None = None) -> str:
    return build_message_frame(ctx, cmd=26, data_payload=None, pid=110, ver="4.0", msg_id=msg_id)

def build_taskbar_unread_badge(
    ctx: Any,
    device_id: str,
    count: int,
    msg_id: int | None = None,
) -> str:
    body = {"cmd": 153, "deviceId": str(device_id), "count": int(count)}
    return pack_envelope(ctx, msg_id, None, SERVICE_ID_PC_UI, 204, body)

def is_low_version_phone(device_type: int, ver_code: int) -> bool:
    return int(device_type or 0) == 1 and 0 < int(ver_code or 0) < 9_000_000

def parse_message_list_payload(ver: str | float | int, data_obj: Any) -> tuple[list[dict[str, Any]], str]:
    try:
        v_num = float(str(ver or "4.0"))
    except ValueError:
        v_num = 4.0
    notice = "matched"
    if isinstance(data_obj, list):
        if v_num > 3.0:
            notice = "CMD_GET_MESSAGE_RESPONSE: ver indicates new proto but data is Array; using legacy parsing"
        raw_list = data_obj
    elif isinstance(data_obj, dict):
        if v_num <= 3.0:
            notice = "CMD_GET_MESSAGE_RESPONSE: ver indicates legacy proto but data is Object; using new parsing"
        raw_list = data_obj.get("messageBeanList") or data_obj.get("messages") or []
    else:
        raw_list = []

    normalized: list[dict[str, Any]] = []
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        to_addr = item.get("toAddress")
        if isinstance(to_addr, str):
            to_addr = [to_addr] if to_addr else []
        elif not isinstance(to_addr, list):
            to_addr = []
        medias = item.get("medias") if isinstance(item.get("medias"), list) else []
        mtype = int(item.get("messageType", 1) or 1)
        normalized.append(
            {
                "msgId": str(item.get("msgId") or ""),
                "requestId": str(item.get("requestId") or ""),
                "subId": int(item.get("subId", -1) if item.get("subId") is not None else -1),
                "fromAddress": str(item.get("fromAddress") or ""),
                "toAddress": [str(x) for x in to_addr],
                "body": str(item.get("body") or ""),
                "date": int(item.get("date", 0) or 0),
                "read": int(item.get("read", 1) if item.get("read") is not None else 1),
                "messageType": mtype,
                "medias": medias,
                "has_mms_placeholder": bool(
                    mtype == 2 and any(not p.get("mediaData") for p in medias if isinstance(p, dict))
                ),
            }
        )
    return normalized, notice

def parse_contact_list_payload(ver: str | float | int, data_obj: Any) -> tuple[list[dict[str, Any]], str]:
    try:
        v_num = float(str(ver or "4.0"))
    except ValueError:
        v_num = 4.0
    notice = "matched"
    if isinstance(data_obj, list):
        if v_num > 2.0:
            notice = "CMD_GET_CONTACTS_RESPONSE: ver indicates new proto but data is Array; using legacy parsing"
        raw_list = data_obj
    elif isinstance(data_obj, dict):
        if v_num <= 2.0:
            notice = "CMD_GET_CONTACTS_RESPONSE: ver indicates legacy proto but data is Object; using new parsing"
        raw_list = data_obj.get("contactBeanList") or data_obj.get("contacts") or []
    else:
        raw_list = []

    contacts: list[dict[str, Any]] = []
    for item in raw_list:
        if not isinstance(item, dict):
            notice = "CMD_GET_CONTACTS_RESPONSE: legacy proto expects Object items"
            continue
        cid = item.get("id")
        if v_num > 2.0 and cid is not None and not isinstance(cid, str):
            notice = "CMD_GET_CONTACTS_RESPONSE: expected String id in data array for new proto"
        contacts.append(
            {
                "id": str(cid if cid is not None else ""),
                "name": str(item.get("name") or ""),
                "number": str(item.get("number") or ""),
                "photo": str(item.get("photo") or ""),
            }
        )
    return contacts, notice
