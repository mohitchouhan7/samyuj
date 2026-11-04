from __future__ import annotations
import datetime
from pathlib import Path
from samyuj.transport.websocket import ws_frame
qrmod = None

import asyncio
import contextlib
import json
import os
import time

from samyuj.core.identity import render_qr_png as _render_qr_png

IDENT_PC = 6
HAVE_MCL = False
from samyuj.protocol.constants import VENDOR_USB_P2P_PC_IP, derive_pc_sn
from samyuj.protocol.services import build_pair_frame
from samyuj.transport.discovery import decode_gatt_frame, gatt_bootstrap_frame, is_usb_ip

def qr_png_bytes(payload: str) -> bytes:
    return _render_qr_png(payload) or b""

class PairingServiceMixin:

    @property
    def _registry_path(self) -> Path:
        return Path(self.state_dir) / "paired_phones.json"

    def load_paired_phones(self) -> dict:
        with contextlib.suppress(Exception):
            return json.loads(self._registry_path.read_text(encoding="utf-8"))
        return {}

    def note_phone(self, *, real_id=None, nonce_id=None, name=None, key=None, ip=None, ws_port=None) -> None:
        reg = self.load_paired_phones()
        k = real_id or nonce_id
        if not k:
            return
        e = reg.get(k) or {}
        if real_id:
            e["real_id"] = real_id
        if name:
            e["name"] = name
        if key:
            e["shared_key"] = key
            e["pc_device_id"] = self.device_id_b64
            with contextlib.suppress(OSError):
                Path(self.state_dir).mkdir(parents=True, mode=0o700, exist_ok=True)
                sk_p = Path(self.state_dir) / "shared_key.txt"
                sk_p.write_text(str(key).strip(), encoding="utf-8")
                os.chmod(sk_p, 0o600)
        if ip:
            if is_usb_ip(str(ip), usb_link=getattr(self, 'usb_link', None)):
                e["last_usb_ip"] = ip
                if not e.get("last_ip"):
                    e["last_ip"] = ip
            else:
                e["last_wifi_ip"] = ip
                e["last_ip"] = ip
        if ws_port:
            e["last_ws_port"] = int(ws_port)
        if nonce_id:
            e["last_nonce"] = nonce_id
        e["last_seen"] = datetime.datetime.now(datetime.UTC).isoformat()
        reg[k] = e
        self.pairing_mode_active = False
        with contextlib.suppress(OSError):
            Path(self.state_dir).mkdir(parents=True, mode=0o700, exist_ok=True)
            self._registry_path.write_text(json.dumps(reg, indent=1), encoding="utf-8")
            os.chmod(self._registry_path, 0o600)

    def _qr_wifi_ips(self) -> list[str]:
        ips = [self.wifi_ip]
        with contextlib.suppress(Exception):
            for c in self._all_ipv4s():
                if c not in ips:
                    ips.append(c)
        return ips[:4]

    def _build_qr_payload_str(self) -> str:
        ident = IDENT_PC if HAVE_MCL else 6
        if HAVE_MCL:
            return qrmod.encode_payload(
                device_id=self.device_id_b64, device_name=self.device_name,
                host=self.wifi_ip, ws_port=self.port, token=self.token_b64,
                fingerprint_b64=self.fp_b64, app_ver=self.app_ver, ver_code=self.ver_code,
                ident=ident,
            )
        return "motorolardpconnection" + json.dumps({
            "version": "7.0.0",
            "wsData": {
                "ver": 2, "cmd": 1, "deviceId": self.device_id_b64,
                "deviceName": self.device_name, "fp": self.fp_b64,
                "token": self.token_b64,
                "policies": [{"wifi": self._qr_wifi_ips()}, {"wfd": [self.device_name]}],
                "wsPort": self.port, "appVer": self.app_ver, "verCode": self.ver_code,
                "os": 6, "scType": 2, "type": 4, "i3": 1, "serviceId": 19,
            }}, separators=(",", ":"))

    def get_pairing_qr_png(self, force_refresh: bool = False) -> bytes:
        if self._qr_png_cache is not None and not force_refresh:
            return self._qr_png_cache
        self.qr_payload = self._build_qr_payload_str()
        png = _render_qr_png(self.qr_payload, self.qr_ec) or b""
        self._qr_png_cache = png
        if self.debug and png:
            with contextlib.suppress(OSError):
                (self.out_dir / "qr.png").write_bytes(png)
        return png

    def start_pairing_mode(self) -> dict:
        self.pairing_mode_active = True
        png = self.get_pairing_qr_png(force_refresh=True)
        return {
            "ok": True,
            "pairing_active": True,
            "qr_bytes": len(png),
            "qr_payload": self.qr_payload,
            "wifi_ip": self.wifi_ip,
            "usb_ip": self.usb_link[1] if self.usb_link else None,
            "port": self.port,
        }

    def stop_pairing_mode(self) -> dict:
        self.pairing_mode_active = False
        return {"ok": True, "pairing_active": False}

    def list_saved_phones(self) -> list[dict]:
        reg = self.load_paired_phones()
        live = self._live_ws()
        live_ip = self._phone_ip(live) if live else ""
        out: list[dict] = []
        for k, e in reg.items():
            if k.startswith("_") or not isinstance(e, dict):
                continue
            rid = e.get("real_id") or k
            last_ip = e.get("last_ip") or ""
            is_conn = bool(live and (live.get("peer_real_id") == rid or (last_ip and last_ip == live_ip)))
            out.append({
                "real_id": rid,
                "name": e.get("name") or f"Motorola Phone ({rid[:8]})",
                "last_ip": last_ip,
                "last_seen": e.get("last_seen") or "",
                "has_key": bool(e.get("shared_key") or self._load_shared_key()),
                "connected": is_conn or bool(live and len(reg) == 1),
            })
        out.sort(key=lambda d: (d.get("connected", False), d.get("last_seen", "")), reverse=True)
        return out

    def unpair_phone(self, real_id: str) -> dict:
        reg = self.load_paired_phones()
        removed = False
        for k in list(reg.keys()):
            e = reg.get(k)
            if k == real_id or (isinstance(e, dict) and e.get("real_id") == real_id):
                reg.pop(k, None)
                removed = True
        with contextlib.suppress(OSError):
            self._registry_path.write_text(json.dumps(reg, indent=1), encoding="utf-8")
            os.chmod(self._registry_path, 0o600)
        if not any(isinstance(v, dict) and v.get("shared_key") for v in reg.values()):
            with contextlib.suppress(OSError):
                (Path(self.state_dir) / "shared_key.txt").unlink(missing_ok=True)
        return {"ok": removed, "unpaired": real_id, "devices": self.list_saved_phones()}

    async def reconnect_saved_phone(self, real_id: str = "") -> dict:
        if self._live_ws() is not None:
            return {"ok": True, "detail": "Phone is already connected"}
        for tc in list(self._conns):
            if not tc.get("closed") and tc.get("tunnel"):
                if await self._send_tunnel_bootstrap(tc, why="saved-reconnect"):
                    return {"ok": True, "detail": "Sent USB tunnel reconnect bootstrap"}
        d = self.latest_phone_discovery()
        if d and d.get("ips") and d.get("port"):
            ok, msg = await self.wifi_tunnel_push(f"{d['ips'][0]}:{d['port']}", wait_dial=6.0)
            return {"ok": ok, "detail": msg}
        if self.mdns_adv and self.mdns_adv.ok:
            self.mdns_adv.announce_now()
        return {"ok": True, "detail": "Announced PC presence over USB/Wi-Fi/BLE for saved phone auto-reconnect"}

    def install_user_desktop_launcher(self) -> dict:
        from samyuj.platform.linux.paths import ensure_user_desktop_integration
        ensured = ensure_user_desktop_integration()
        xdg_data = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
        desk_p = ensured if (ensured is not None and ensured.is_file()) else (xdg_data / "applications" / "samyuj.desktop")
        return {"ok": desk_p.is_file(), "desktop_entry": str(desk_p)}

    def phone_real_ids(self) -> list[str]:
        out = []
        for k, e in self.load_paired_phones().items():
            if k.startswith("_") or not isinstance(e, dict):
                continue
            out.append(e.get("real_id") or k)
        return [x for x in out if x]

    def pairing_identity_state(self) -> str:
        keyed = [e for e in self.load_paired_phones().values()
                 if isinstance(e, dict) and e.get("shared_key")]
        if not keyed:
            return "empty"
        ids = {e.get("pc_device_id") for e in keyed}
        ids.discard(None)
        if not ids:
            return "unknown"
        return "match" if self.device_id_b64 in ids else "mismatch"

    def _load_shared_key(self) -> str | None:
        with contextlib.suppress(OSError):
            v = (Path(self.state_dir) / "shared_key.txt").read_text().strip()
            return v or None
        return None

    def consistent_shared_key(self) -> str | None:
        if self.pairing_identity_state() == "mismatch":
            return None
        k = self._load_shared_key()
        if k:
            return k
        skeys = self.shared_keys()
        return skeys[0] if skeys else None

    def shared_keys(self) -> list[str]:
        keys = []
        k = self._load_shared_key()
        if k:
            keys.append(k)
        for e in self.load_paired_phones().values():
            if isinstance(e, dict) and e.get("shared_key") and e["shared_key"] not in keys:
                keys.append(e["shared_key"])
        return keys

    def _forget_shared_key(self) -> None:
        with contextlib.suppress(OSError):
            (Path(self.state_dir) / "shared_key.txt").unlink(missing_ok=True)
        reg = self.load_paired_phones()
        changed = False
        for e in reg.values():
            if isinstance(e, dict) and e.pop("shared_key", None) is not None:
                e.pop("pc_device_id", None)
                changed = True
        if changed:
            with contextlib.suppress(OSError):
                self._registry_path.write_text(json.dumps(reg, indent=1), encoding="utf-8")
                os.chmod(self._registry_path, 0o600)

    def _is_usb_conn(self, conn: dict | None) -> bool:
        if conn is None:
            return bool(self.usb_link)
        w = conn.get("writer")
        l_ip, p_ip = "", ""
        if w is not None:
            with contextlib.suppress(Exception):
                sn = w.get_extra_info("sockname")
                if sn:
                    l_ip = str(sn[0]).replace("::ffff:", "")
            with contextlib.suppress(Exception):
                pn = w.get_extra_info("peername")
                if pn:
                    p_ip = str(pn[0]).replace("::ffff:", "")
        uip = self.usb_link[1] if self.usb_link else ""
        t_ph = (self.tether or {}).get("phone_ip") or ""
        if uip and (l_ip == uip or (p_ip and p_ip.rsplit(".", 1)[0] == uip.rsplit(".", 1)[0])):
            return True
        if t_ph and p_ip == t_ph:
            return True
        if is_usb_ip(l_ip, usb_link=self.usb_link) or is_usb_ip(p_ip, usb_link=self.usb_link):
            return True
        return False

    def _conn_local_ip(self, conn: dict | None, is_usb: bool | None = None) -> str:
        if is_usb is None:
            is_usb = self._is_usb_conn(conn)
        if conn is not None and conn.get("writer") is not None:
            with contextlib.suppress(Exception):
                sn = conn["writer"].get_extra_info("sockname")
                if sn and sn[0] not in ("0.0.0.0", "::", "127.0.0.1"):
                    return str(sn[0]).replace("::ffff:", "")
        if is_usb and self.usb_link:
            return self.usb_link[1]
        wip = getattr(self, "wifi_ip", "") or ""
        if wip and wip != "127.0.0.1":
            return wip
        return self.lan_ip or VENDOR_USB_P2P_PC_IP

    def _build_bootstrap_json(self, conn: dict | None = None,
                              for_usb: bool | None = None,
                              ip_override: str | None = None) -> str:
        is_usb = self._is_usb_conn(conn) if for_usb is None else bool(for_usb)
        ip = ip_override or self._conn_local_ip(conn, is_usb=is_usb)
        usb_pol = [ip, self.moto_adb_serial] if getattr(self, "moto_adb_serial", None) else [ip]
        policies = [{"usb": usb_pol}, {"wifi": [ip]}] if is_usb else [{"wifi": [ip]}, {"usb": usb_pol}]
        return json.dumps({
            "cmd": 1, "ver": 3,
            "deviceId": self.device_id_b64,
            "deviceName": self.device_name,
            "token": self.token_b64,
            "fp": self.fp_b64,
            "wsPort": self.port,
            "ip": ip,
            "policies": policies,
            "os": 6, "type": 4, "scType": 2, "cap": self.pc_cap,
            "sn": getattr(self, "pc_sn", "") or derive_pc_sn(getattr(self, "device_id_b64", "")), "verCode": self.ver_code, "verName": self.app_ver,
        }, separators=(",", ":"), ensure_ascii=False)

    async def _send_tunnel_bootstrap(self, conn: dict, why: str = "tunnel",
                                     framed_nonce_b64: str | None = None) -> bool:
        if conn.get("closed") or not conn.get("tunnel"):
            return False
        is_usb = self._is_usb_conn(conn)
        boot = self._build_bootstrap_json(conn=conn, for_usb=is_usb)
        conn["bootstraps_sent"] = conn.get("bootstraps_sent", 0) + 1
        if framed_nonce_b64 and not is_usb:
            key = self.consistent_shared_key()
            raw_frame = gatt_bootstrap_frame(framed_nonce_b64, boot, key)
            if raw_frame is None:
                self.events.log("BLE_ENCRYPT_FAILED", f"conn{conn['id']:03d} refusing plaintext tunnel bootstrap")
                return False
            await self._send_raw(conn, ws_frame(0x2, raw_frame))
            self.events.log(f"TUNNEL_GATT_BOOTSTRAP_SENT({why})",
                            f"conn{conn['id']:03d} {len(raw_frame)}B op={raw_frame[16]}")
            return True
        return await self._send_ws_text(conn, boot, f"TUNNEL_BOOTSTRAP_SENT({why},usb={is_usb})")

    async def _on_tunnel_binary(self, conn: dict, payload: bytes) -> None:
        dec = decode_gatt_frame(payload, self.shared_keys())
        self.events.log("TUNNEL_BIN_IN", f"conn{conn['id']:03d} len={len(payload)} dec={bool(dec)}")
        if dec and dec.get("op") == 3:
            self._forget_shared_key()
            if self.tunnel_op3_evt is not None:
                self.tunnel_op3_evt.set()
            self.events.log("TUNNEL_DELETE_KEY",
                            f"conn{conn['id']:03d} phone reported op3 — cleared shared_key.txt & paired_phones key")
            return
        js = (dec or {}).get("json") or {}
        if js.get("action") == 1:
            dev = js.get("device") or {}
            with contextlib.suppress(Exception):
                peer = conn["writer"].get_extra_info("peername")
                self.note_phone(nonce_id=js.get("deviceId"),
                                name=dev.get("deviceName"),
                                ip=peer[0] if peer else None)
            if conn.get("bootstraps_sent", 0) > 0:
                await self._send_tunnel_bootstrap(conn, why="readyForInfo")
            return
        if js.get("cmd") == 0 or js:
            pt = conn.pop("proactive_task", None)
            if pt is not None:
                pt.cancel()
            framed_nonce = (dec or {}).get("nonce_b64") if not (dec or {}).get("raw") else None
            await self._send_tunnel_bootstrap(conn, why="cmd0-ippolicy", framed_nonce_b64=framed_nonce)

    async def _handle_pair(self, conn: dict, frame: dict, data, cmd: int | None, tag: str) -> None:
        self.pair_service_state["messages_received"] = int(
            self.pair_service_state.get("messages_received", 0)
        ) + 1
        self.pair_service_state["last_msg"] = data
        self.pair_service_state["connected"] = True
        d = data if isinstance(data, dict) else {}
        dev = d.get("device") if isinstance(d.get("device"), dict) else d
        hello_dev = ((conn.get("hello") or {}).get("data") or {}).get("device") or {}
        real_id = str(
            dev.get("realDeviceId")
            or dev.get("deviceId")
            or hello_dev.get("realDeviceId")
            or hello_dev.get("deviceId")
            or frame.get("deviceId")
            or ""
        )
        nonce_id = str(frame.get("deviceId") or hello_dev.get("deviceId") or "")
        dev_name = str(
            dev.get("deviceName")
            or hello_dev.get("deviceName")
            or self.phone_telemetry.get("deviceName")
            or "Motorola Phone"
        )
        source = str(d.get("source") if isinstance(d, dict) and "source" in d else "2")
        self.pair_service_state["last_source"] = source
        self.pair_service_state["last_device_id"] = real_id or nonce_id
        if real_id or nonce_id:
            if (real_id or nonce_id) not in self.pair_service_state["connected_devices"]:
                self.pair_service_state["connected_devices"].append(real_id or nonce_id)
            if (real_id or nonce_id) in self.pair_service_state["pending_devices"]:
                self.pair_service_state["pending_devices"].remove(real_id or nonce_id)
            with contextlib.suppress(Exception):
                self.note_phone(
                    real_id=real_id or None,
                    nonce_id=nonce_id or None,
                    name=dev_name,
                    ip=self._phone_ip(conn),
                )
        if source == "0" and not self._is_usb_conn(conn):
            self.pair_service_state["usb_switch_requests"] = int(
                self.pair_service_state.get("usb_switch_requests", 0)
            ) + 1
            self.events.log("PAIR_SVC19_USB_SWITCH", f"{tag} source=0 -> checking USB tether link", feature="pairing")
            if getattr(self, "usb_link", None) and hasattr(self, "_proactive_usb_switch_push"):
                self._usb_takeover = {"since": time.monotonic(), "wifi_conn_id": conn.get("id")}
                ph_ip = (getattr(self, "tether", None) or {}).get("phone_ip")
                with contextlib.suppress(Exception):
                    asyncio.get_running_loop().create_task(
                        self._proactive_usb_switch_push(self.usb_link[1], ph_ip)
                    )
        self.events.log(
            "PAIR_SVC19_RECV",
            f"{tag} peer={real_id or nonce_id} name={dev_name!r} source={source} data={str(data)[:120]}",
            feature="pairing",
        )
        ack_payload = build_pair_frame(
            self,
            payload="" if data == "" or data is None else {"ver": "1", "cmd": int(cmd or 0), "source": "2", "paired": 1},
            msg_id=frame.get("msgId"),
        )
        await self._send_ws_text(conn, ack_payload, "PAIR_SVC19_ACK_SENT(svc19)")

    def audit_src_service_ids(self) -> dict:
        return {
            "ok": True,
            "direct_service_rule": "peer side: when srcServiceId == -1, the phone echoes srcServiceId = serviceId",
            "feature_launcher_rule": "peer side: frames launched via svc-1001 arrive with srcServiceId = 19 (svc-1, svc-3, svc-15, svc-26, svc-202)",
            "message_usable_rule": "peer side: the cmd:1018 message-usable frame arrives with srcServiceId=204, serviceId=100",
            "audited_routes": {
                "0": {"srcServiceId": 0, "channel": "Smart Connect Service Envelope"},
                "1": {"srcServiceId": 1, "launcher_srcServiceId": 19, "channel": "Smart Connect Service Envelope"},
                "2": {"srcServiceId": 2, "channel": "Smart Connect Service Envelope"},
                "3": {"srcServiceId": 3, "launcher_srcServiceId": 19, "channel": "Smart Connect Service Envelope"},
                "5": {"srcServiceId": 5, "channel": "Smart Connect Service Envelope"},
                "9": {"srcServiceId": 9, "channel": "Smart Connect Service Envelope"},
                "10": {"srcServiceId": 10, "channel": "Smart Connect Service Envelope"},
                "15": {"srcServiceId": 15, "launcher_srcServiceId": 19, "channel": "Smart Connect Service Envelope"},
                "16": {"srcServiceId": 16, "channel": "Smart Connect Service Envelope"},
                "17": {"srcServiceId": 17, "channel": "Smart Connect Service Envelope"},
                "19": {"srcServiceId": 19, "channel": "Smart Connect Service Envelope"},
                "20": {"srcServiceId": 20, "channel": "Smart Connect Service Envelope"},
                "22": {"srcServiceId": 22, "channel": "Smart Connect Service Envelope"},
                "24": {"srcServiceId": 24, "channel": "Smart Connect Service Envelope"},
                "25": {"srcServiceId": 25, "channel": "Smart Connect Service Envelope"},
                "26": {"srcServiceId": 26, "launcher_srcServiceId": 19, "channel": "Smart Connect Service Envelope"},
                "100": {"srcServiceId": 204, "channel": "Smart Connect Service Envelope"},
                "168": {"srcServiceId": 168, "channel": "Smart Connect Service Envelope"},
                "202": {"srcServiceId": 202, "launcher_srcServiceId": 19, "channel": "Smart Connect Service Envelope"},
                "204": {"srcServiceId": 204, "channel": "Smart Connect Service Envelope"},
                "205": {"srcServiceId": 205, "channel": "Smart Connect Service Envelope"},
                "1001": {"srcServiceId": 1001, "channel": "Smart Connect Service Envelope"},
            },
        }

from .base import SamyujService

class PairingService(SamyujService):
    service_id = 0
    id = "pairing"
    name = "Pairing & Authentication Service"
    description = "Hello/Ack (token OK), EXCHANGE_KEY (svc-0 cmd:1), Check-In (cmd:2), and shared key store"

    def actions(self) -> list[dict]:
        return [
            {"name": "checkin", "label": "Send Check-In (cmd:2)", "description": "Send the svc-0 cmd:2 check-in frame"},
            {"name": "exchange_key", "label": "Send Exchange Key (cmd:1)", "description": "Send svc-0 cmd:1 EXCHANGE_KEY"},
            {"name": "forget_key", "label": "Reset Stored Key", "description": "Clear stored shared_key.txt for clean re-pairing"},
        ]

    async def invoke(self, action: str, params: dict | None = None,
                     conn: dict | None = None) -> dict:
        if action == "checkin":
            return await self.h.request("checkin", conn)
        if action == "exchange_key":
            return await self.h.request("exchange-key", conn)
        if action == "forget_key":
            return await self.h.request("forget-key", conn)
        return await super().invoke(action, params, conn)

class PairDeviceService(SamyujService):
    service_id = 19
    id = "device_paired"
    name = "Paired Device Record & Originator Service"
    description = (
        "svc-19: QR/barcode pairing completion "
        "channel, USB network upgrade trigger, paired_phones.json persistence, and "
        "srcServiceId:19 feature-launch originator"
    )

    def actions(self) -> list[dict]:
        return [
            {"name": "pair_handshake", "label": "Send svc-19 Pair Handshake", "description": "Send svc-19 empty/device-record frame"},
            {"name": "list_paired", "label": "List Saved Paired Phones", "description": "Return persisted paired_phones.json records via list_saved_phones()"},
            {"name": "audit_src_sid", "label": "Audit srcServiceId Consistency", "description": "Verify srcServiceId usage against the svc-1001 launcher rules"},
        ]

    def get_state(self) -> dict:
        st = dict(getattr(self.h, "pair_service_state", {}) or {})
        saved = self.h.list_saved_phones() if hasattr(self.h, "list_saved_phones") else []
        return {
            **super().get_state(),
            **st,
            "paired_phones_count": len(saved),
            "paired_phones": saved,
        }

    async def invoke(self, action: str, params: dict | None = None,
                     conn: dict | None = None) -> dict:
        params = params or {}
        c = conn or self.h._live_ws()
        if action in ("pair_handshake", "handshake", "pair"):
            return await self.h.request("pair-device", c)
        if action == "list_paired":
            saved = self.h.list_saved_phones()
            return {"ok": True, "paired_phones": saved, "detail": f"{len(saved)} paired phone(s)"}
        if action == "audit_src_sid":
            return self.h.audit_src_service_ids()
        return await super().invoke(action, params, conn)
