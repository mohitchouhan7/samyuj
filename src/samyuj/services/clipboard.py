from __future__ import annotations
import asyncio

import contextlib

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
from samyuj.protocol.services import (
    build_clipboard_confirm,
    build_clipboard_global_enable,
    build_clipboard_pull_response,
)

class ClipboardServiceMixin:

    async def _handle_clipboard(self, conn: dict, frame: dict, data, tag: str) -> None:
        d = data if isinstance(data, dict) else {}
        if "msgId" not in d and isinstance(d.get("data"), dict) and "msgId" in d["data"]:
            d = d["data"]
        clip_msg_id = d.get("msgId")
        inner = d.get("data") if isinstance(d.get("data"), dict) else {}
        text = str(inner.get("text") or "")
        rec = {"tag": tag, "clipMsgId": clip_msg_id, "payload": inner, "text": text}
        if clip_msg_id == CLIP_NEGO:
            self.clipboard_version = min(int(inner.get("version", 1)), 3)
            self.events.log("CLIP_NEGO", f"{tag} version={self.clipboard_version}", feature="clipboard")
            await self._send_ws_text(conn, build_clipboard_confirm(self, self.clipboard_version), "CLIP_CONFIRM_SENT")
            await self._send_ws_text(
                conn,
                build_clipboard_global_enable(self, bool(self.clip_sync.enabled)),
                "CLIP_GLOBAL_ENABLE_SENT")
        elif clip_msg_id == CLIP_CONFIRM:
            self.clip_seen.append(rec)
            if "version" in inner:
                with contextlib.suppress(Exception):
                    self.clipboard_version = min(int(inner.get("version", self.clipboard_version)), 3)
            self.events.log("CLIP_CONFIRM_RECV", f"{tag} version={self.clipboard_version}", feature="clipboard")
        elif clip_msg_id == CLIP_UPDATE:
            self.clip_seen.append(rec)
            if len(self.clip_seen) > 240:
                del self.clip_seen[:120]
            self.clip_last_text = text
            self.events.log("CLIP_RECV", f"{tag} clipId={inner.get('clipId')} len={len(text)} text={text[:80]!r}", feature="clipboard")
            await self._send_ws_text(conn, build_clipboard_confirm(self, self.clipboard_version), "CLIP_CONFIRM_SENT")
            if text:
                await self.clip_sync.on_phone_clipboard(text, kind="update")
        elif clip_msg_id == CLIP_PULL_RESP:
            self.clip_seen.append(rec)
            req_id = inner.get("requestId", "")
            err = inner.get("error", 0)
            self.events.log("CLIP_PULL_RESP_RECV",
                            f"{tag} requestId={req_id} error={err} len={len(text)}",
                            feature="clipboard")
            if text and not err:
                self.clip_last_text = text
                await self.clip_sync.on_phone_clipboard(text, kind="pull-resp")
        elif clip_msg_id == CLIP_PULL:
            self.clip_seen.append(rec)
            req_id = inner.get("requestId", "")
            raw_cid = inner.get("clipId")
            cid = int(raw_cid) if raw_cid is not None else int(self._clip_seq or 1)
            self.events.log("CLIP_PULL_REQ", f"{tag} requestId={req_id} clipId={cid}", feature="clipboard")
            live_txt = None
            if self.clip_sync._gtk_getter is not None:
                with contextlib.suppress(Exception):
                    live_txt = self.clip_sync._gtk_getter()
            if not (isinstance(live_txt, str) and live_txt):
                live_txt = await asyncio.to_thread(self.clip_sync.read_pc_clipboard_sync)
            if isinstance(live_txt, str) and live_txt:
                self.pc_clipboard_text = live_txt
                answer_txt = live_txt
            else:
                answer_txt = str(self.pc_clipboard_text or "")
            await self._send_ws_text(
                conn, build_clipboard_pull_response(self, req_id, cid, answer_txt),
                "CLIP_PULL_RESP_SENT")
        elif clip_msg_id == CLIP_PULL_CANCEL:
            self.clip_seen.append(rec)
            self.events.log("CLIP_PULL_CANCEL_RECV", f"{tag} requestId={inner.get('requestId')}", feature="clipboard")
        elif clip_msg_id == CLIP_CLOSE:
            self.clip_seen.append(rec)
            self.events.log("CLIP_CLOSE_RECV", f"{tag} phone closed clipboard transfer", feature="clipboard")
        elif clip_msg_id in (CLIP_SUPPORT_FILES, CLIP_SYNCED):
            self.clip_seen.append(rec)
            self.events.log("CLIP_SYNC_STATE_RECV", f"{tag} msgId={clip_msg_id} {inner}", feature="clipboard")
        elif clip_msg_id in (CLIP_GLOBAL_ENABLE, CLIP_GLOBAL_DISABLE):
            self.clip_seen.append(rec)
            en_glob = (clip_msg_id == CLIP_GLOBAL_ENABLE)
            self.clip_sync.set_enabled(en_glob)
            self.events.log("CLIP_GLOBAL_TOGGLE_RECV", f"{tag} enabled={en_glob}", feature="clipboard")
        else:
            self.clip_seen.append(rec)
            self.events.log("CLIP_MSG", f"{tag} clipMsgId={clip_msg_id} keys={sorted(inner.keys())}", feature="clipboard")

from .base import SamyujService

class ClipboardService(SamyujService):
    service_id = 9
    id = "clipboard"
    name = "Clipboard Sync Service"
    description = "svc-9 bidirectional text & file clipboard sync (CLIP_NEGO=1, CLIP_CONFIRM=2, CLIP_UPDATE=3, CLIP_PULL=4, CLIP_PULL_RESP=5)"

    def actions(self) -> list[dict]:
        return [
            {"name": "push_text", "label": "Push Text to Phone (CLIP_UPDATE)", "description": "Send msgId:3 UPDATE_NOTIFY to phone clipboard", "default_params": {"text": "Hello from Samyuj PC!"}},
            {"name": "pull", "label": "Pull Phone Clipboard (CLIP_PULL)", "description": "Send msgId:4 PULL request to phone"},
            {"name": "nego", "label": "Negotiate Clipboard (CLIP_NEGO)", "description": "Send msgId:1 NEGO version=3"},
            {"name": "enable_files", "label": "Enable Clipboard File Sync (msgId:10)", "description": "Send msgId:10 SYNC_SUPPORT_FILES enabled=true"},
            {"name": "auto_toggle", "label": "Toggle AUTOMATIC Clipboard Sync", "description": "Auto-sync Linux<->phone clipboard with echo suppression (no infinite loop)"},
        ]

    def get_state(self) -> dict:
        return {
            **super().get_state(),
            "version": int(getattr(self.h, "clipboard_version", 1)),
            "last_text": str(getattr(self.h, "clip_last_text", "")),
            "seen_count": len(getattr(self.h, "clip_seen", []) or []),
        }

    async def invoke(self, action: str, params: dict | None = None,
                     conn: dict | None = None) -> dict:
        params = params or {}
        c = conn or self.h._live_ws()
        if action == "push_text":
            txt = str(params.get("text") or "Hello from Samyuj PC!")
            return await self.h.request(f"clipboard:{txt}", c)
        if action == "pull":
            return await self.h.request("clipboard-pull", c)
        if action == "nego":
            return await self.h.request("clipboard-nego", c)
        if action == "enable_files":
            return await self.h.request("clipboard-files:on", c)
        if action == "auto_toggle":
            st = self.h.clip_sync.set_enabled(not self.h.clip_sync.enabled)
            return {"ok": True, "detail": f"automatic clipboard sync {'ON' if st['enabled'] else 'OFF'}", **st}
        return await super().invoke(action, params, conn)
