from __future__ import annotations
import re

import asyncio
import base64
import contextlib
import os
import time
from pathlib import Path

from samyuj.protocol.services import (
    IOS_MESSAGES_ERROR,
    build_msg_check_availability,
    build_msg_get_mms_parts,
    build_msg_notify_ready,
    build_msg_permission_request,
    build_msg_request_contacts,
    build_msg_request_messages,
    build_msg_request_sim_cards,
    build_msg_send_request,
    is_low_version_phone,
    parse_contact_list_payload,
    parse_message_list_payload,
)

class MessagesServiceMixin:

    def _emit_vue_message_event(self, event_name: str, payload: dict) -> None:
        ev_rec = {"cmd_vue": event_name, "payload": payload, "ts": time.time()}
        self.messages_state["vue_events"].append(ev_rec)
        if len(self.messages_state["vue_events"]) > 100:
            del self.messages_state["vue_events"][:50]
        self.events.log("MESSAGES_VUE_EVENT", f"{event_name} keys={sorted(payload.keys())}", feature="messages")

    def _rebuild_message_threads(self) -> None:
        threads: dict[str, list[dict]] = {}
        unread_total = 0
        for m in self.messages_state.get("messages") or []:
            peer = m.get("fromAddress") or (m.get("toAddress") or ["Unknown"])[0] or "Unknown"
            threads.setdefault(peer, []).append(m)
            if int(m.get("read", 1) or 0) == 0:
                unread_total += 1
        for peer_k in threads:
            threads[peer_k].sort(key=lambda x: int(x.get("date", 0) or 0))
        self.messages_state["threads"] = threads
        self.messages_state["conversations"] = threads
        self.messages_state["sim_cards"] = self.messages_state.get("simCards", [])
        self.messages_state["unread"] = unread_total
        if callable(getattr(self, "on_messages_updated_cb", None)):
            with contextlib.suppress(Exception):
                self.on_messages_updated_cb()

    async def _delayed_messages_sync_burst(self, conn: dict, delay: float = 0.35) -> None:
        with contextlib.suppress(Exception):
            await asyncio.sleep(max(0.05, float(delay)))
            if conn.get("closed"):
                return
            await self._send_ws_text(conn, build_msg_request_contacts(self), "MESSAGES_DELAYED_CONTACTS_SENT(svc204 pId110 cmd1)")
            await self._send_ws_text(conn, build_msg_request_messages(self, msg_type=1, count=100), "MESSAGES_DELAYED_SMS_SENT(svc204 pId110 cmd10)")
            await self._send_ws_text(conn, build_msg_request_messages(self, msg_type=3, count=50), "MESSAGES_DELAYED_MMS_SENT(svc204 pId110 cmd10)")
            await self._send_ws_text(conn, build_msg_request_sim_cards(self), "MESSAGES_DELAYED_SIMS_SENT(svc204 pId110 cmd12)")

    def _cache_mms_parts_from_messages(self, device_id: str, msg_list: list[dict]) -> list[str]:
        dev_slug = re.sub(r"[^A-Za-z0-9_.-]", "_", str(device_id or "default")) or "default"
        dev_dir = self.messages_cache_root / dev_slug
        with contextlib.suppress(OSError):
            dev_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(dev_dir, 0o700)
        saved_paths: list[str] = []
        for m in msg_list:
            raw_mid = str(m.get("msgId") or "mms")
            mid = re.sub(r"[^A-Za-z0-9_.-]", "_", raw_mid).strip("._")[:64] or "mms"
            for idx, part in enumerate(m.get("medias") or []):
                if not isinstance(part, dict):
                    continue
                mdata = part.get("mediaData")
                pname = str(part.get("name") or f"part_{mid}_{idx}.bin")
                safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", pname)
                if mdata:
                    raw_b = b""
                    if isinstance(mdata, str):
                        with contextlib.suppress(Exception):
                            raw_b = base64.b64decode(mdata)
                        if not raw_b:
                            raw_b = mdata.encode("utf-8")
                    elif isinstance(mdata, (bytes, bytearray)):
                        raw_b = bytes(mdata)
                    if raw_b:
                        out_f = dev_dir / f"{mid}_{safe_name}"
                        with contextlib.suppress(OSError):
                            out_f.write_bytes(raw_b)
                            os.chmod(out_f, 0o600)
                        part["cached_path"] = str(out_f)
                        self.messages_state["cached_mms_files"][f"{mid}:{part.get('partId', idx)}"] = str(out_f)
                        if part.get("path"):
                            self.messages_state["cached_mms_files"][str(part["path"])] = str(out_f)
                        saved_paths.append(str(out_f))
        return saved_paths

    async def update_android_device_message_setting(
        self, conn: dict | None, device_id: str,
        enabled: bool, granted: bool, triggered: bool,
        unread_cnt: int | None = None,
        device_type: int = 1, ver_code: int = 90030130,
    ) -> dict:
        if is_low_version_phone(device_type, ver_code):
            granted = True
        was_granted = bool(self.messages_state.get("granted", False))
        was_enabled = bool(self.messages_state.get("enabled", False))
        self.messages_state.update({
            "enabled": bool(enabled),
            "granted": bool(granted),
            "usable": bool(granted and enabled),
            "triggered": bool(triggered),
            "deviceType": int(device_type),
            "verCode": int(ver_code),
        })
        if unread_cnt is not None:
            self.messages_state["unread"] = int(unread_cnt)
            self.messages_state["taskbar_badge"] = {"deviceId": str(device_id), "count": int(unread_cnt)}
        self._emit_vue_message_event(
            "NOTIFY_ANDROID_DEVICE_MESSAGE_SETTING",
            {"deviceId": device_id, "enabled": bool(enabled), "granted": bool(granted), "triggered": bool(triggered)},
        )
        self._emit_vue_message_event(
            "UPDATE_DEVICE_PERMISSION",
            {"deviceId": device_id, "granted": bool(granted)},
        )
        c = conn or self._live_ws()
        if granted and (not was_granted or (enabled and not was_enabled) or not self.messages_state.get("triggered")) and c is not None:
            self.messages_state["triggered"] = True
            await self._send_ws_text(c, build_msg_notify_ready(self), "MESSAGES_NOTIFY_READY_SENT(svc204 pId110 cmd18)")
            await self._send_ws_text(c, build_msg_request_contacts(self), "MESSAGES_REQ_CONTACTS_SENT(svc204 pId110 cmd1)")
            await self._send_ws_text(c, build_msg_request_messages(self, msg_type=1, count=100), "MESSAGES_REQ_SMS_SENT(svc204 pId110 cmd10)")
            await self._send_ws_text(c, build_msg_request_messages(self, msg_type=3, count=50), "MESSAGES_REQ_MMS_SENT(svc204 pId110 cmd10)")
            await self._send_ws_text(c, build_msg_request_sim_cards(self), "MESSAGES_REQ_SIMS_SENT(svc204 pId110 cmd12)")
            if not enabled and c.get("writer") is not None:
                c.setdefault("tasks", []).append(asyncio.create_task(self._delayed_messages_sync_burst(c, delay=0.35)))
        if callable(getattr(self, "on_messages_updated_cb", None)):
            with contextlib.suppress(Exception):
                self.on_messages_updated_cb()
        return {
            "ok": True,
            "granted": bool(granted),
            "enabled": bool(enabled),
            "triggered": bool(self.messages_state["triggered"]),
        }

    async def send_android_sms(self, conn: dict | None, to_addr: str | list[str],
                               body: str, sub_id: int = -1,
                               request_id: str | None = None) -> dict:
        if int(self.messages_state.get("deviceOs", 1)) == 2:
            return {"ok": False, "error": IOS_MESSAGES_ERROR}
        c = conn or self._live_ws()
        if sub_id < 0 and self.messages_state.get("selected_sub_id", -1) >= 0:
            sub_id = int(self.messages_state["selected_sub_id"])
        elif sub_id < 0 and self.messages_state.get("simCards"):
            sub_id = int(self.messages_state["simCards"][0].get("subId", -1))
        if request_id is None or request_id == "":
            req_id_raw: int | str = int(time.time() * 1000) % 2147483647
        else:
            req_id_raw = request_id
        req_id = str(req_id_raw)
        frame_str = build_msg_send_request(self, to_addr, body, sub_id=sub_id, request_id=req_id_raw)
        to_list_norm = [to_addr] if isinstance(to_addr, str) else list(to_addr)
        now_ms = int(time.time() * 1000)
        self.messages_state["pending_sends"][req_id] = {
            "requestId": req_id,
            "toAddress": to_list_norm,
            "body": body,
            "subId": sub_id,
            "status": "sending",
            "ts": time.time(),
            "date": now_ms,
        }
        local_msg = {
            "msgId": f"out_{req_id}",
            "requestId": req_id,
            "subId": int(sub_id),
            "messageType": 1,
            "fromAddress": "",
            "toAddress": to_list_norm,
            "body": str(body or ""),
            "date": now_ms,
            "read": 1,
            "medias": [],
            "has_mms_placeholder": False,
            "outgoing": True,
        }
        self.messages_state["messages"].append(local_msg)
        self._rebuild_message_threads()
        if c is None:
            return {"ok": False, "requestId": req_id, "error": "no live phone connection"}
        ok = await self._send_ws_text(c, frame_str, f"MESSAGES_SEND_SMS_SENT(svc204 pId110 cmd4,reqId={req_id},subId={sub_id})")
        return {"ok": bool(ok), "requestId": req_id, "subId": sub_id, "detail": f"svc-204 pId:110 cmd:4 sent (subId={sub_id})"}

    async def download_mms_media(self, conn: dict | None, media_path_or_id: str,
                                 device_id: str = "") -> dict:
        key = str(media_path_or_id or "").strip()
        if key in self.messages_state["cached_mms_files"]:
            cpath = self.messages_state["cached_mms_files"][key]
            if Path(cpath).is_file():
                return {"ok": True, "cached": True, "path": cpath}
        c = conn or self._live_ws()
        if c is None:
            return {"ok": False, "cached": False, "error": "no live phone connection"}
        mms_id = key.split(":")[0] if ":" in key else key
        ok = await self._send_ws_text(
            c, build_msg_get_mms_parts(self, [mms_id]),
            f"MESSAGES_GET_MMS_PARTS_SENT(svc204 pId110 cmd22,id={mms_id})",
        )
        return {"ok": bool(ok), "cached": False, "requested_id": mms_id}

    async def _handle_messages(self, conn: dict, frame: dict, data, cmd: int | None, tag: str) -> dict:
        d = data if isinstance(data, dict) else {}
        if "cmd" not in d and isinstance(d.get("data"), dict) and ("cmd" in d["data"] or "pId" in d["data"]):
            d = d["data"]
        cmd_val = int(d.get("cmd", cmd) if d.get("cmd", cmd) is not None else -1)
        pid_val = int(d.get("pId", 110 if frame.get("serviceId") == 204 else 100) or 110)
        ver_str = str(d.get("ver") or "4.0")
        inner = d.get("data")
        peer_id = str(
            (inner.get("deviceId") if isinstance(inner, dict) else None)
            or d.get("deviceId")
            or ((conn.get("hello") or {}).get("data") or {}).get("device", {}).get("realDeviceId")
            or frame.get("deviceId")
            or "android_peer"
        )
        self.messages_state["last_pid"] = pid_val
        self.messages_state["last_cmd"] = cmd_val

        dev_os = int(
            (inner.get("deviceOs") if isinstance(inner, dict) and "deviceOs" in inner else None)
            or d.get("deviceOs")
            or self.messages_state.get("deviceOs", 1)
        )
        if dev_os == 2 or pid_val in (109, 111):
            self.events.log("MESSAGES_IOS_REJECTED", f"{tag} pId={pid_val} cmd={cmd_val} deviceOs={dev_os} -> rejected", feature="messages")
            return {"ok": False, "error": IOS_MESSAGES_ERROR}
        self.messages_state["deviceOs"] = 1

        if cmd_val == 1018:
            usable = d if "granted" in d else (inner if isinstance(inner, dict) else {})
            return await self.update_android_device_message_setting(
                conn, peer_id,
                enabled=bool(usable.get("enabled", True)),
                granted=bool(usable.get("granted", False)),
                triggered=bool(usable.get("triggered", False)),
                unread_cnt=usable.get("unreadCnt"),
            )

        router_table = {
            (101, 0): self._msg_route_pid101_open,
            (102, 0): self._msg_route_pid102_req_contacts,
            (102, 1): self._msg_route_pid102_req_messages,
            (102, 14): self._msg_route_pid102_download_mms,
            (102, 18): self._msg_route_pid102_notify_ready,
            (102, 19): self._msg_route_pid102_get_availability,
            (102, 20): self._msg_route_pid102_stop_availability,
            (102, 100): self._msg_route_pid102_retry_permission,
            (110, 2): self._msg_route_pid110_contacts_resp,
            (110, 3): self._msg_route_pid110_contacts_resp,
            (110, 5): self._msg_route_pid110_send_ack,
            (110, 6): self._msg_route_pid110_send_result,
            (110, 7): self._msg_route_pid110_messages_sync,
            (110, 11): self._msg_route_pid110_messages_resp,
            (110, 13): self._msg_route_pid110_sim_cards_resp,
            (110, 14): self._msg_route_pid110_delete_messages,
            (110, 15): self._msg_route_pid110_sim_cards_resp,
            (110, 17): self._msg_route_pid110_permission_resp,
            (110, 21): self._msg_route_pid110_permission_resp,
            (110, 23): self._msg_route_pid110_mms_parts_resp,
            (110, 25): self._msg_route_pid110_contacts_resp,
            (110, 27): self._msg_route_pid110_availability_resp,
        }
        handler_fn = router_table.get((pid_val, cmd_val))
        if handler_fn is not None:
            return await handler_fn(conn, frame, d, inner, ver_str, peer_id, tag)

        self.events.log("MESSAGES_UNHANDLED_ROUTE", f"{tag} pId={pid_val} cmd={cmd_val}", feature="messages")
        return {"ok": False, "error": f"unhandled messages route (pId={pid_val}, cmd={cmd_val})"}

    async def _msg_route_pid101_open(self, conn, frame, d, inner, ver_str, peer_id, tag) -> dict:
        self.messages_state["enabled"] = True
        await self._send_ws_text(conn, build_msg_permission_request(self, prompt_ui=False), "MESSAGES_QUERY_PERM_SENT(svc204 pId110 cmd20)")
        return {"ok": True, "pId": 101, "cmd": 0, "deviceId": peer_id}

    async def _msg_route_pid102_req_contacts(self, conn, frame, d, inner, ver_str, peer_id, tag) -> dict:
        ok = await self._send_ws_text(conn, build_msg_request_contacts(self), "MESSAGES_REQ_CONTACTS_SENT(svc204 pId110 cmd1)")
        return {"ok": bool(ok), "pId": 102, "cmd": 0}

    async def _msg_route_pid102_req_messages(self, conn, frame, d, inner, ver_str, peer_id, tag) -> dict:
        ok1 = await self._send_ws_text(conn, build_msg_request_messages(self, msg_type=1, count=100), "MESSAGES_REQ_SMS_SENT(svc204 pId110 cmd10)")
        ok2 = await self._send_ws_text(conn, build_msg_request_messages(self, msg_type=3, count=50), "MESSAGES_REQ_MMS_SENT(svc204 pId110 cmd10)")
        return {"ok": bool(ok1 and ok2), "pId": 102, "cmd": 1}

    async def _msg_route_pid102_download_mms(self, conn, frame, d, inner, ver_str, peer_id, tag) -> dict:
        p_info = inner if isinstance(inner, dict) else d
        mpath = str(p_info.get("mediaPath") or p_info.get("msgId") or "")
        return await self.download_mms_media(conn, mpath, device_id=peer_id)

    async def _msg_route_pid102_notify_ready(self, conn, frame, d, inner, ver_str, peer_id, tag) -> dict:
        self.messages_state["triggered"] = True
        ok = await self._send_ws_text(conn, build_msg_notify_ready(self), "MESSAGES_NOTIFY_READY_SENT(svc204 pId110 cmd18)")
        return {"ok": bool(ok), "pId": 102, "cmd": 18}

    async def _msg_route_pid102_get_availability(self, conn, frame, d, inner, ver_str, peer_id, tag) -> dict:
        ok = await self._send_ws_text(conn, build_msg_check_availability(self), "MESSAGES_CHECK_AVAIL_SENT(svc204 pId110 cmd26)")
        return {"ok": bool(ok), "pId": 102, "cmd": 19}

    async def _msg_route_pid102_stop_availability(self, conn, frame, d, inner, ver_str, peer_id, tag) -> dict:
        return {"ok": True, "pId": 102, "cmd": 20, "stopped": True}

    async def _msg_route_pid102_retry_permission(self, conn, frame, d, inner, ver_str, peer_id, tag) -> dict:
        ok = await self._send_ws_text(conn, build_msg_permission_request(self, prompt_ui=True), "MESSAGES_RETRY_PERM_SENT(svc204 pId110 cmd16)")
        return {"ok": bool(ok), "pId": 102, "cmd": 100}

    async def _msg_route_pid110_contacts_resp(self, conn, frame, d, inner, ver_str, peer_id, tag) -> dict:
        contacts, notice = parse_contact_list_payload(ver_str, inner)
        if notice != "matched":
            self.messages_state["schema_notices"].append(notice)
            self.events.log("MESSAGES_SCHEMA_MISMATCH", notice, feature="messages")
        by_id = {c["id"]: c for c in self.messages_state["contacts"] if c.get("id")}
        for c in contacts:
            by_id[c["id"] or c["number"]] = c
        self.messages_state["contacts"] = list(by_id.values())
        self._emit_vue_message_event("UPDATE_CONTACTS", {"deviceId": peer_id, "contactBeanList": self.messages_state["contacts"]})
        self.events.log("MESSAGES_CONTACTS_RECV", f"{tag} count={len(contacts)} total={len(self.messages_state['contacts'])} notice={notice}", feature="messages")
        if callable(getattr(self, "on_messages_updated_cb", None)):
            with contextlib.suppress(Exception):
                self.on_messages_updated_cb()
        return {"ok": True, "contacts": contacts, "schema_notice": notice}

    async def _msg_route_pid110_send_ack(self, conn, frame, d, inner, ver_str, peer_id, tag) -> dict:
        req_id = str((inner or {}).get("requestId") if isinstance(inner, dict) else "")
        if req_id in self.messages_state["pending_sends"]:
            self.messages_state["pending_sends"][req_id]["status"] = "acked"
        self.events.log("MESSAGES_SEND_ACK_RECV", f"{tag} requestId={req_id}", feature="messages")
        return {"ok": True, "requestId": req_id}

    async def _msg_route_pid110_send_result(self, conn, frame, d, inner, ver_str, peer_id, tag) -> dict:
        res_b = inner if isinstance(inner, dict) else {}
        req_id = str(res_b.get("requestId") or "")
        msg_id_s = str(res_b.get("msgId") or "")
        st = int(res_b.get("status", 0) or 0)
        code = int(res_b.get("code", 0) or 0)
        rep = {"requestId": req_id, "msgId": msg_id_s, "status": st, "code": code, "type": res_b.get("type", 1)}
        self.messages_state["delivery_reports"].append(rep)
        if req_id in self.messages_state["pending_sends"]:
            self.messages_state["pending_sends"][req_id]["status"] = "sent" if st == 1 else "failed"
            self.messages_state["pending_sends"][req_id]["msgId"] = msg_id_s
        vue_name = "NOTIFY_SEND_MESSAGE_SUCCESS" if st == 1 else "NOTIFY_SEND_MESSAGE_ERROR"
        self._emit_vue_message_event(vue_name, rep)
        self.events.log("MESSAGES_SEND_RESULT_RECV", f"{tag} {vue_name} reqId={req_id} msgId={msg_id_s} status={st} code={code}", feature="messages")
        return {"ok": st == 1, "vue_event": vue_name, "report": rep}

    async def _msg_route_pid110_messages_resp(self, conn, frame, d, inner, ver_str, peer_id, tag) -> dict:
        msgs, notice = parse_message_list_payload(ver_str, inner)
        if notice != "matched":
            self.messages_state["schema_notices"].append(notice)
            self.events.log("MESSAGES_SCHEMA_MISMATCH", notice, feature="messages")
        by_id = {m["msgId"]: m for m in self.messages_state["messages"] if m.get("msgId")}
        for m in msgs:
            by_id[m["msgId"] or f"m_{len(by_id)}"] = m
        self.messages_state["messages"] = sorted(by_id.values(), key=lambda x: int(x.get("date", 0) or 0))
        self._cache_mms_parts_from_messages(peer_id, msgs)
        self._rebuild_message_threads()
        self._emit_vue_message_event("UPDATE_LOCAL_MESSAGES", {"deviceId": peer_id, "messageBeanList": self.messages_state["messages"]})
        self.events.log("MESSAGES_LIST_RECV", f"{tag} count={len(msgs)} total={len(self.messages_state['messages'])} unread={self.messages_state['unread']} notice={notice}", feature="messages")
        return {"ok": True, "messages": msgs, "schema_notice": notice}

    async def _msg_route_pid110_messages_sync(self, conn, frame, d, inner, ver_str, peer_id, tag) -> dict:
        msgs, notice = parse_message_list_payload(ver_str, inner)
        action = int(inner.get("action", 1) if isinstance(inner, dict) else 1)
        by_id = {m["msgId"]: m for m in self.messages_state["messages"] if m.get("msgId")}
        for m in msgs:
            by_id[m["msgId"] or f"m_{len(by_id)}"] = m
            if action == 1 and int(m.get("read", 1) or 0) == 0 and m.get("fromAddress"):
                self._emit_desktop_notification("Messages", m["fromAddress"], m.get("body") or "[MMS Media]", include_reply_action=True)
        self.messages_state["messages"] = sorted(by_id.values(), key=lambda x: int(x.get("date", 0) or 0))
        self._cache_mms_parts_from_messages(peer_id, msgs)
        self._rebuild_message_threads()
        self.messages_state["taskbar_badge"] = {"deviceId": peer_id, "count": self.messages_state["unread"]}
        self._emit_vue_message_event("UPDATE_RECEIVE_MESSAGE" if action == 1 else "UPDATE_SEND_MESSAGE", {"deviceId": peer_id, "messageBeanList": msgs})
        self.events.log("MESSAGES_SYNC_RECV", f"{tag} action={action} count={len(msgs)} unread={self.messages_state['unread']}", feature="messages")
        return {"ok": True, "action": action, "messages": msgs}

    async def _msg_route_pid110_sim_cards_resp(self, conn, frame, d, inner, ver_str, peer_id, tag) -> dict:
        raw_sims = inner if isinstance(inner, list) else ((inner or {}).get("simCards") if isinstance(inner, dict) else [])
        sims: list[dict] = []
        for s in raw_sims or []:
            if isinstance(s, dict):
                sims.append({
                    "subId": int(s.get("subId", -1)),
                    "slotId": int(s.get("slotId", 0)),
                    "phoneNumber": str(s.get("phoneNumber") or ""),
                    "countryCode": str(s.get("countryCode") or ""),
                })
        self.messages_state["simCards"] = sims
        self.messages_state["sim_cards"] = sims
        if sims and self.messages_state.get("selected_sub_id", -1) < 0:
            self.messages_state["selected_sub_id"] = sims[0]["subId"]
        self._emit_vue_message_event("UPDATE_ANDROID_SIM_CARDS", {"deviceId": peer_id, "simCards": sims})
        self.events.log("MESSAGES_SIM_CARDS_RECV", f"{tag} count={len(sims)} selectedSubId={self.messages_state['selected_sub_id']}", feature="messages")
        if callable(getattr(self, "on_messages_updated_cb", None)):
            with contextlib.suppress(Exception):
                self.on_messages_updated_cb()
        return {"ok": True, "simCards": sims}

    async def _msg_route_pid110_delete_messages(self, conn, frame, d, inner, ver_str, peer_id, tag) -> dict:
        del_ids = set(str(x) for x in ((inner or {}).get("deleteList") if isinstance(inner, dict) else (inner if isinstance(inner, list) else [])) or [])
        self.messages_state["messages"] = [m for m in self.messages_state["messages"] if str(m.get("msgId")) not in del_ids]
        self._rebuild_message_threads()
        self._emit_vue_message_event("UPDATE_DELETE_MESSAGE", {"deviceId": peer_id, "deleteList": sorted(del_ids)})
        self.events.log("MESSAGES_DELETE_RECV", f"{tag} deleted={len(del_ids)}", feature="messages")
        return {"ok": True, "deleted": sorted(del_ids)}

    async def _msg_route_pid110_permission_resp(self, conn, frame, d, inner, ver_str, peer_id, tag) -> dict:
        u = inner if isinstance(inner, dict) else d
        return await self.update_android_device_message_setting(
            conn, peer_id,
            enabled=bool(u.get("enabled", True)),
            granted=bool(u.get("granted", False)),
            triggered=bool(u.get("triggered", False)),
            unread_cnt=u.get("unreadCnt"),
        )

    async def _msg_route_pid110_mms_parts_resp(self, conn, frame, d, inner, ver_str, peer_id, tag) -> dict:
        msgs, _ = parse_message_list_payload("3.0" if isinstance(inner, list) else ver_str, inner)
        saved = self._cache_mms_parts_from_messages(peer_id, msgs)
        by_id = {m["msgId"]: m for m in self.messages_state["messages"] if m.get("msgId")}
        for m in msgs:
            if m.get("msgId") in by_id:
                by_id[m["msgId"]]["medias"] = m.get("medias", [])
                by_id[m["msgId"]]["has_mms_placeholder"] = False
            else:
                by_id[m.get("msgId") or f"mms_{len(by_id)}"] = m
        self.messages_state["messages"] = sorted(by_id.values(), key=lambda x: int(x.get("date", 0) or 0))
        self._rebuild_message_threads()
        self.events.log("MESSAGES_MMS_PARTS_CACHED", f"{tag} msgs={len(msgs)} cachedFiles={len(saved)}", feature="messages")
        return {"ok": True, "cached_files": saved, "messages": msgs}

    async def _msg_route_pid110_availability_resp(self, conn, frame, d, inner, ver_str, peer_id, tag) -> dict:
        av_d = inner if isinstance(inner, dict) else d
        is_av = bool(av_d.get("isAvailable", True))
        err_c = int(av_d.get("errorCode", 0) or 0)
        msg_s = str(av_d.get("msg") or "")
        self.messages_state.update({
            "sms_send_available": is_av,
            "availability_error": err_c,
            "availability_msg": msg_s,
        })
        self._emit_vue_message_event("UPDATE_SMS_SEND_AVAILABILITY", {"deviceId": peer_id, "isAvailable": is_av, "errorCode": err_c, "msg": msg_s})
        self.events.log("MESSAGES_AVAILABILITY_RECV", f"{tag} isAvailable={is_av} errorCode={err_c}", feature="messages")
        return {"ok": True, "isAvailable": is_av, "errorCode": err_c, "msg": msg_s}

from .base import SamyujService

class MessagesService(SamyujService):
    service_id = 204
    id = "messages"
    name = "Android SMS/MMS Messages & Contacts Service"
    description = (
        "svc-204: "
        "table-driven (pId, cmd) sub-router (pId:110 phone wire + pId:101/102 local UI), "
        "Android-only (rejects deviceOs==2 iOS), dual schema auto-detection, MMS on-demand cache, "
        "multi-SIM simCards, delivery reports, and gdbus inline-reply desktop notifications"
    )

    def actions(self) -> list[dict]:
        return [
            {"name": "sync_all", "label": "Sync Contacts, Messages & SIMs", "description": "Send notifyMessageReady (cmd:18), requestContactList (cmd:1), requestMessageList (cmd:10), requestSimCards (cmd:12)"},
            {"name": "request_contacts", "label": "Request Contacts (cmd:1)", "description": "Send svc-204 pId:110 cmd:1 CMD_GET_CONTACTS_REQUEST"},
            {"name": "request_messages", "label": "Request SMS/MMS (cmd:10)", "description": "Send svc-204 pId:110 cmd:10 CMD_GET_MESSAGE_REQUEST"},
            {"name": "request_sims", "label": "Query SIM Cards (cmd:12)", "description": "Send svc-204 pId:110 cmd:12 CMD_GET_PHONE_NUMBER_REQUEST"},
            {"name": "send_sms", "label": "Send SMS (cmd:4)", "description": "Send an SMS via svc-204 pId:110 cmd:4", "default_params": {"to": "+15550100", "body": "Hello from Samyuj", "subId": -1}},
            {"name": "request_permission", "label": "Request Phone SMS Permission (cmd:16)", "description": "Send svc-204 pId:110 cmd:16 CMD_MESSAGE_PERMISSION_REQUEST"},
        ]

    def get_state(self) -> dict:
        ms = getattr(self.h, "messages_state", {}) or {}
        return {
            **super().get_state(),
            "deviceOs": ms.get("deviceOs", 1),
            "granted": bool(ms.get("granted", False)),
            "enabled": bool(ms.get("enabled", False)),
            "triggered": bool(ms.get("triggered", False)),
            "unread": int(ms.get("unread", 0) or 0),
            "simCards": list(ms.get("simCards") or []),
            "selected_sub_id": ms.get("selected_sub_id", -1),
            "contacts_count": len(ms.get("contacts") or []),
            "messages_count": len(ms.get("messages") or []),
            "threads_count": len(ms.get("threads") or {}),
            "mms_cache_dir": ms.get("mms_cache_dir", ""),
            "sms_send_available": ms.get("sms_send_available", True),
            "last_cmd": ms.get("last_cmd"),
            "last_pid": ms.get("last_pid"),
        }

    async def invoke(self, action: str, params: dict | None = None,
                     conn: dict | None = None) -> dict:
        params = params or {}
        c = conn or self.h._live_ws()
        dev_os = int(params.get("deviceOs", self.h.messages_state.get("deviceOs", 1)) or 1)
        if dev_os == 2:
            return {
                "ok": False,
                "error": "iPhone Messages requires Bluetooth OBEX (MAP/PBAP/MNS) — not supported on Linux",
            }
        if action == "sync_all":
            return await self.h.request("messages-sync", c)
        if action == "request_contacts":
            return await self.h.request("messages-contacts", c)
        if action == "request_messages":
            return await self.h.request("messages-list", c)
        if action == "request_sims":
            return await self.h.request("messages-sims", c)
        if action == "send_sms":
            to_addr = str(params.get("to") or params.get("toAddress") or "")
            body = str(params.get("body") or "")
            sub_id = int(params.get("subId", self.h.messages_state.get("selected_sub_id", -1)))
            return await self.h.send_android_sms(c, to_addr, body, sub_id=sub_id)
        if action == "request_permission":
            return await self.h.request("messages-permission", c)
        if action == "download_mms":
            mpath = str(params.get("mediaPath") or params.get("msgId") or "")
            return await self.h.request(f"messages-mms:{mpath}", c)
        return await super().invoke(action, params, conn)
