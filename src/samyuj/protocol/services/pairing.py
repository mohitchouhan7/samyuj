from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

from samyuj.protocol.constants import PC_SERVICE_VERSIONS, derive_pc_sn
from samyuj.transport.discovery.usb import is_usb_ip
from samyuj.protocol.envelope import build_envelope, pack_envelope

def build_pc_device_record(ctx: Any, phone_hello_data: dict[str, Any] | None = None) -> dict[str, Any]:
    sv = getattr(ctx, "pc_service_versions", None) or PC_SERVICE_VERSIONS
    dev_id = getattr(ctx, "device_id_b64", "")
    return {
        "cap": getattr(ctx, "pc_cap", 5365),
        "cloudPush": 0,
        "deviceId": dev_id,
        "realDeviceId": getattr(ctx, "pc_real_device_id", None) or dev_id,
        "deviceName": getattr(ctx, "device_name", "Samyuj"),
        "i3": 1,
        "minVerCode": {"1": 7000000, "2": 700000000, "3": 7000000, "4": 7000000},
        "os": 6,
        "scType": 2,
        "serviceVersions": dict(sv),
        "sku": getattr(ctx, "pc_sku", "Linux-PC"),
        "sn": getattr(ctx, "pc_sn", None) or derive_pc_sn(getattr(ctx, "device_id_b64", "")),
        "type": 4,
        "verCode": getattr(ctx, "ver_code", 903013000),
        "verName": getattr(ctx, "app_ver", "09.0.3.013.000"),
    }

def build_reply_json(
    ctx: Any,
    variant: str,
    hello: dict[str, Any] | None,
    conn: Any | None = None,
) -> str:
    hdata = (hello or {}).get("data", {}) if hello else {}
    token = getattr(ctx, "token_b64", "")
    if variant == "ok":
        token = "OK"
    elif variant == "wrongtoken":
        raw = base64.b64decode(token)
        tampered = bytes([raw[0] ^ 0xFF]) + raw[1:]
        token = base64.b64encode(tampered).decode("ascii")

    if variant == "minimal":
        data: dict[str, Any] = {"cmd": 0, "ver": 3, "token": "OK", "paired": 0}
    else:
        cmd = 1 if variant == "cmd1" else 0
        sv = dict(getattr(ctx, "pc_service_versions", None) or PC_SERVICE_VERSIONS)
        rfav = dict(getattr(ctx, "pc_rfa_service_versions", None) or sv)
        is_usb = (
            ctx._is_usb_conn(conn)
            if (conn is not None and hasattr(ctx, "_is_usb_conn"))
            else bool(
                getattr(ctx, "usb_link", None)
                or is_usb_ip(str(getattr(ctx, "lan_ip", "")), usb_link=getattr(ctx, "usb_link", None))
            )
        )
        reply_ip = (
            ctx._conn_local_ip(conn, is_usb=is_usb)
            if hasattr(ctx, "_conn_local_ip")
            else getattr(ctx, "lan_ip", "127.0.0.1")
        )
        is_reconnect = bool(
            (hdata.get("paired") == 1)
            or (hdata.get("token") and hdata.get("token") != getattr(ctx, "token_b64", ""))
        )
        conn_type_val = 2 if is_usb else (1 if is_reconnect else 0)
        paired_val = 1 if (is_usb or is_reconnect) else 0
        fp_b64 = getattr(ctx, "fp_b64", "")
        port = getattr(ctx, "port", 9831)
        ver_code = getattr(ctx, "ver_code", 903013000)
        app_ver = getattr(ctx, "app_ver", "09.0.3.013.000")
        data = {
            "cmd": cmd,
            "ver": 3,
            "connType": conn_type_val,
            "paired": paired_val,
            "limitService": 0,
            "token": token,
            "ip": reply_ip,
            "ips": [{"connType": 2 if is_usb else 1, "fp": fp_b64, "ip": reply_ip, "port": port}],
            "appVer": app_ver,
            "verCode": ver_code,
            "apkVerCode": ver_code,
            "apkVerName": app_ver,
            "serviceVersions": sv,
            "r4ServiceVersions": sv,
            "rfaServiceVersions": rfav,
            "device": build_pc_device_record(ctx, hdata),
        }
        if hello and hello.get("deviceId"):
            data["noncePhoneId"] = hello["deviceId"]

    reply_file = getattr(ctx, "reply_file", None)
    if variant == "custom" and reply_file:
        tpl = Path(reply_file).read_text(encoding="utf-8")
        ident = getattr(ctx, "identity", None)
        fp_hex = getattr(ident, "fingerprint", getattr(ctx, "fingerprint_hex", ""))
        for k, v in {
            "{token}": str(token),
            "{deviceId}": str(getattr(ctx, "device_id_b64", "")),
            "{fp_b64}": str(getattr(ctx, "fp_b64", "")),
            "{fp_hex}": str(fp_hex),
            "{ip}": str(getattr(ctx, "lan_ip", "")),
            "{port}": str(getattr(ctx, "port", 9831)),
            "{deviceName}": str(getattr(ctx, "device_name", "")),
            "{sku}": str(getattr(ctx, "pc_sku", "")),
            "{msgId}": str(hello.get("msgId", 1) if hello else 1),
        }.items():
            tpl = tpl.replace(k, v)
        return tpl

    if getattr(ctx, "msgid_mode", "echo") == "echo" and hello:
        msg_id = hello.get("msgId", ctx.next_msg_id())
    else:
        msg_id = ctx.next_msg_id() if hasattr(ctx, "next_msg_id") else 1

    if getattr(ctx, "deviceid_mode", "self") == "peer" and hello:
        env_device = hello.get("deviceId", getattr(ctx, "device_id_b64", ""))
    else:
        env_device = getattr(ctx, "device_id_b64", "")

    svc_id = int(getattr(ctx, "service_id", 0) or 0)
    envelope = build_envelope(ctx, msg_id, env_device, svc_id, 0, data)
    return json.dumps(envelope, separators=(",", ":"), ensure_ascii=False)

def build_server_first_hello(
    ctx: Any,
    nonce_phone_id: str | None = None,
    sender: int | None = None,
) -> str:
    lan_ip = getattr(ctx, "lan_ip", "127.0.0.1")
    fp_b64 = getattr(ctx, "fp_b64", "")
    port = getattr(ctx, "port", 9831)
    sv = dict(getattr(ctx, "pc_service_versions", None) or PC_SERVICE_VERSIONS)
    data: dict[str, Any] = {
        "cmd": 0,
        "ver": 3,
        "connType": 0,
        "paired": 0,
        "limitService": 0,
        "token": getattr(ctx, "token_b64", ""),
        "ip": lan_ip,
        "ips": [{"connType": 1, "fp": fp_b64, "ip": lan_ip, "port": port}],
        "appVer": getattr(ctx, "app_ver", "09.0.3.013.000"),
        "verCode": getattr(ctx, "ver_code", 903013000),
        "serviceVersions": sv,
        "device": build_pc_device_record(ctx, None),
    }
    if nonce_phone_id:
        data["noncePhoneId"] = nonce_phone_id
    svc_id = int(getattr(ctx, "service_id", 0) or 0)
    eff_sender = 1 if sender == 1 else getattr(ctx, "sender_id", None)
    return pack_envelope(ctx, None, None, svc_id, 0, data, sender=eff_sender)

def build_checkin(ctx: Any) -> str:
    ident = getattr(ctx, "identity", None)
    fp_hex = getattr(ident, "fingerprint", getattr(ctx, "fingerprint_hex", ""))
    fp = fp_hex if getattr(ctx, "checkin_fp_format", "hex") == "hex" else getattr(ctx, "fp_b64", "")
    svc_id = int(getattr(ctx, "service_id", 0) or 0)
    data = {
        "cmd": 2,
        "ver": "1",
        "lang": getattr(ctx, "lang", "en-US"),
        "fingerprint": fp,
        "port": getattr(ctx, "port", 9831),
        "token": getattr(ctx, "token_b64", ""),
        "serviceId": svc_id,
        "autoDownload": False,
    }
    return pack_envelope(ctx, None, None, svc_id, 0, data)

def build_exchange_key(ctx: Any, key_b64: str | None = None) -> str:
    key = key_b64 or base64.b64encode(os.urandom(32)).decode("ascii")
    dev_id = getattr(ctx, "device_id_b64", "")
    data = {
        "cmd": 1,
        "ver": 3,
        "device": {
            "deviceId": dev_id,
            "realDeviceId": getattr(ctx, "pc_real_device_id", None) or dev_id,
            "deviceName": getattr(ctx, "device_name", "Samyuj"),
            "key": key,
            "os": 6,
            "scType": 2,
            "type": 4,
            "cap": getattr(ctx, "pc_cap", 5365),
            "sn": getattr(ctx, "pc_sn", None) or derive_pc_sn(getattr(ctx, "device_id_b64", "")),
            "verCode": getattr(ctx, "ver_code", 903013000),
            "verName": getattr(ctx, "app_ver", "09.0.3.013.000"),
        },
    }
    return pack_envelope(ctx, None, None, 0, 0, data)

def build_pair_frame(
    ctx: Any,
    payload: str | dict[str, Any] = "",
    msg_id: int | None = None,
    src_service_id: int = 19,
) -> str:
    return pack_envelope(ctx, msg_id, None, 19, src_service_id, payload)
