from __future__ import annotations
import os
import shutil
import subprocess
import datetime

import contextlib
import time

from samyuj.protocol.services import (
    build_ntf_pc_msg,
)

class NotificationServiceMixin:

    def _emit_desktop_notification(self, app_name: str, title: str, body: str,
                                   include_reply_action: bool = True) -> dict:
        head = f"{app_name}: {title}" if (app_name and title and app_name.lower() not in title.lower()) else (title or app_name or "Samyuj")
        head_s = str(head)[:120]
        body_s = str(body or "")[:240]
        actions_arg = '["reply", "Reply"]' if include_reply_action else "[]"
        gdbus_cmd = [
            "gdbus", "call", "--session",
            "--dest", "org.freedesktop.Notifications",
            "--object-path", "/org/freedesktop/Notifications",
            "--method", "org.freedesktop.Notifications.Notify",
            "Samyuj", "0", "phone",
            head_s, body_s, actions_arg, "{}", "5000",
        ]
        notify_send_cmd = ["notify-send", "-a", "Samyuj", "-i", "phone", "--", head_s, body_s]
        has_display = bool(os.environ.get("DBUS_SESSION_BUS_ADDRESS") or os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
        actions_list = ["reply", "Reply"] if include_reply_action else []
        if shutil.which("gdbus") and has_display and os.environ.get("SC_DISABLE_GDBUS_NOTIFY") != "1":
            with contextlib.suppress(Exception):
                subprocess.Popen(
                    gdbus_cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return {"method": "gdbus", "cmd": gdbus_cmd, "gdbus_cmd": gdbus_cmd, "notify_send_cmd": notify_send_cmd, "actions": actions_list}
        if shutil.which("notify-send") and has_display:
            with contextlib.suppress(Exception):
                subprocess.Popen(
                    notify_send_cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return {"method": "notify-send", "cmd": notify_send_cmd, "gdbus_cmd": gdbus_cmd, "notify_send_cmd": notify_send_cmd, "actions": actions_list}
        return {"method": "logged_only", "gdbus_cmd": gdbus_cmd, "notify_send_cmd": notify_send_cmd, "actions": actions_list}

    async def clear_all_notifications(self) -> int:
        c = self._live_ws()
        items = list(self.notifications_list)
        self.notifications_list.clear()
        n = 0
        if c:
            for item in items:
                k = item.get("key")
                if k:
                    with contextlib.suppress(Exception):
                        await self.send_ntf_dismiss(c, str(k))
                        n += 1
        if callable(self.on_notification_cb):
            with contextlib.suppress(Exception):
                self.on_notification_cb(None)
        return len(items)

    async def _handle_notifications(self, conn: dict, frame: dict, data, cmd, tag: str) -> None:
        d = data if isinstance(data, dict) else {}
        sid = frame.get("serviceId")
        if "cmd" not in d and isinstance(d.get("data"), dict) and ("cmd" in d["data"] or "key" in d["data"]):
            d = d["data"]
            cmd = d.get("cmd", cmd)
        if sid in (100, 35) and cmd == 1018:
            self.ntf_status = dict(d)
            self.events.log("NTF_STATUS", f"{tag} enabled={d.get('enabled')} granted={d.get('granted')} unreadCnt={d.get('unreadCnt')}", feature="notifications")
            return
        if sid == 5:
            key = str(d.get("key") or f"ntf_{int(time.time()*1000)}")
            event = d.get("event")
            if event == 2:
                self.notifications_list = [x for x in self.notifications_list if x.get("key") != key]
                self.events.log("NTF_REMOVED", f"{tag} key={key}", feature="notifications")
                if callable(self.on_notification_cb):
                    with contextlib.suppress(Exception):
                        self.on_notification_cb(None)
                return
            nd = d.get("data") if isinstance(d.get("data"), dict) else {}
            title = str(nd.get("title") or nd.get("tickerText") or d.get("title") or "").strip()
            text = str(nd.get("text") or nd.get("subText") or d.get("text") or "").strip()
            pkg = str(d.get("pkg") or nd.get("pkg") or "").strip()
            if not pkg and "|" in key:
                parts = key.split("|")
                if len(parts) >= 2:
                    pkg = parts[1]
            app_name = str(nd.get("appName") or d.get("appName") or "").strip()
            if not app_name and pkg:
                tail = pkg.split(".")[-1].replace("_", " ").title()
                app_name = {"Messaging": "Messages", "Gm": "Gmail", "Whatsapp": "WhatsApp", "Photos": "Photos"}.get(tail, tail)
            actions = (d.get("actions") if isinstance(d.get("actions"), list) else nd.get("actions")) or []
            item = {
                "key": key,
                "event": event,
                "type": d.get("type"),
                "pkg": pkg,
                "app_name": app_name or "Phone",
                "title": title or app_name or "Notification",
                "text": text or "New activity from your phone",
                "actions": actions if isinstance(actions, list) else [],
                "ts": time.time(),
                "utc": datetime.datetime.now(datetime.UTC).isoformat(),
            }
            self.notifications_list = [x for x in self.notifications_list if x.get("key") != key]
            self.notifications_list.append(item)
            if len(self.notifications_list) > 200:
                del self.notifications_list[:100]
            self.events.log("NTF_EVENT", f"{tag} key={key} event={event} app={item['app_name']!r} title={title[:50]!r} actions={len(item['actions'])}", feature="notifications")
            self._emit_desktop_notification(item["app_name"], item["title"], item["text"])
            if callable(self.on_notification_cb):
                with contextlib.suppress(Exception):
                    self.on_notification_cb(item)

    async def send_ntf_dismiss(self, conn: dict, key: str) -> bool:
        match = next((x for x in reversed(self.notifications_list) if x.get("key") == key), None)
        actions = (match or {}).get("actions") or []
        clear_words = ("dismiss", "mark as read", "mark read", "read", "clear", "close")
        avoid_words = ("mute", "block", "reply", "delete", "spam")
        aid = None
        for a in actions:
            nm = str(a.get("action_name", "")).lower()
            if not a.get("remote_input") and not any(w in nm for w in avoid_words) and any(w in nm for w in clear_words):
                aid = a.get("action_id")
                break
        if aid is not None:
            return await self._send_ws_text(conn, build_ntf_pc_msg(self, key, 1, {"action_id": int(aid)}), f"NTF_ACTION_DISMISS_SENT(key={key},aid={aid})")
        return await self._send_ws_text(conn, build_ntf_pc_msg(self, key, 2), f"NTF_CLICK_DISMISS_SENT(key={key})")

from .base import SamyujService

class NotificationService(SamyujService):
    service_id = 5
    id = "notifications"
    name = "Phone Notifications Service"
    description = "svc-5 / svc-100 / svc-35 live phone notifications, inline reply (event:0), action click (event:1), notification click/dismiss (event:2) & package block (cmd:1)"

    def actions(self) -> list[dict]:
        return [
            {"name": "open_settings", "label": "Open Phone Notification Settings (cmd:2)", "description": "Open notification sync settings on the phone"},
            {"name": "dismiss_latest", "label": "Click/Dismiss Latest Notification", "description": "Trigger safe dismiss action or click (event:2) on latest phone notification"},
            {"name": "reply_latest", "label": "Inline Reply to Latest Notification", "description": "Send the event:0 PC-reply frame to the latest notification", "default_params": {"text": "Got it, thanks!"}},
        ]

    def get_state(self) -> dict:
        return {
            **super().get_state(),
            "status": dict(getattr(self.h, "ntf_status", {}) or {}),
            "count": len(getattr(self.h, "notifications_list", []) or []),
            "items": list(getattr(self.h, "notifications_list", []) or [])[-15:],
        }

    async def invoke(self, action: str, params: dict | None = None,
                     conn: dict | None = None) -> dict:
        params = params or {}
        c = conn or self.h._live_ws()
        items = list(getattr(self.h, "notifications_list", []) or [])
        key = str(params.get("key") or (items[-1].get("key") if items else "") or "")
        if action == "open_settings":
            return await self.h.request("ntf-settings", c)
        if action in ("dismiss", "dismiss_latest", "click"):
            if not key:
                return {"ok": False, "error": "no notification key available"}
            return await self.h.request(f"ntf-dismiss:{key}", c)
        if action in ("reply", "reply_latest"):
            if not key:
                return {"ok": False, "error": "no notification key available"}
            txt = str(params.get("text") or "Got it!")
            return await self.h.request(f"ntf-reply:{key}:{txt}", c)
        if action == "action_click":
            aid = int(params.get("action_id", 0))
            return await self.h.request(f"ntf-action:{key}:{aid}", c)
        if action == "block_pkg":
            pkg = str(params.get("pkg") or "")
            return await self.h.request(f"ntf-block:{pkg}", c)
        return await super().invoke(action, params, conn)
