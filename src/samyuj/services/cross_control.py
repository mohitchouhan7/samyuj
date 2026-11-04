from __future__ import annotations
from samyuj.transport.websocket import ws_frame

import contextlib
import re
import subprocess
import time

from samyuj.protocol.constants import (
    UC_CMD_DISCONNECT,
    UC_CMD_DISCONNECT_RESP,
    UC_CMD_PHONE_REQUEST,
    UC_CMD_PHONE_RESPONSE,
)
from samyuj.protocol.services import (
    build_input_key,
    build_input_mouse_button,
    build_input_mouse_move,
    build_uc_disconnect,
    build_uc_disconnect_resp,
    build_uc_request,
    build_uc_response,
    build_uc_show_pointer,
)

class CrossControlServiceMixin:

    def _pc_screen_size(self) -> tuple[int, int]:
        with contextlib.suppress(Exception):
            out = subprocess.run(["xrandr", "--current"], capture_output=True,
                                 text=True, timeout=3).stdout
            for line in out.splitlines():
                if " connected" in line and "*" in line:
                    m = re.search(r"(\d+)x(\d+)", line)
                    if m:
                        return int(m.group(1)), int(m.group(2))
            m2 = re.search(r"(\d+)x(\d+)", out)
            if m2:
                return int(m2.group(1)), int(m2.group(2))
        return (int(self.ss_res[0]), int(self.ss_res[1])) if getattr(self, "ss_res", None) else (1920, 1080)

    async def _uc_set_phase(self, phase: str, detail: str = "", **kw) -> None:
        self.uc_state.update(phase=phase, detail=detail, **kw)
        self.uc_state["updated"] = time.time()

    async def _handle_universal_control(self, conn: dict, frame: dict, data,
                                        cmd, tag: str) -> None:
        d = data if isinstance(data, dict) else {}
        inner = d.get("data") if isinstance(d.get("data"), dict) else d
        cid = inner.get("cid")
        self.events.log("UC_RECV", f"{tag} cmd={cmd} cid={cid} code={inner.get('code')} "
                                   f"w={inner.get('width')} h={inner.get('height')} "
                                   f"supportTouch={inner.get('supportTouch')}",
                        feature="input", service_id=26)
        if cmd == UC_CMD_PHONE_REQUEST:
            self.uc_state.update(cid=int(cid or 0), trigger_used=None)
            pw, ph = self._pc_screen_size()
            await self._send_ws_text(conn, build_uc_response(self, int(cid or 0), True, pw, ph),
                                     "UC_RESPONSE_SENT(cmd3,code1,auto-accept)")
            await self._uc_set_phase("phone-requested", "accepted phone's cross-control request")
            return
        if cmd == UC_CMD_PHONE_RESPONSE:
            code = int(inner.get("code") or 0)
            if code == 1:
                self.uc_state.update(
                    connected=True, cid=int(cid if cid is not None else self.uc_state.get("cid", -1)),
                    phone_w=int(inner.get("width") or 0), phone_h=int(inner.get("height") or 0),
                    support_touch=bool(inner.get("supportTouch")))
                await self._uc_set_phase(
                    "connected",
                    f"phone accepted cross control ({self.uc_state['phone_w']}x"
                    f"{self.uc_state['phone_h']}, touch={self.uc_state['support_touch']})")
                conn["uc_connected"] = True
                self._notify_ui_feature_started("crosscontrol", conn=conn,
                                                force_present=True,
                                                origin="phone-cmd2")
                await self.push_service_status(conn, "crosscontrol-connected")
                self.events.log("UC_CONNECTED",
                                f"cid={self.uc_state['cid']} phone={self.uc_state['phone_w']}x"
                                f"{self.uc_state['phone_h']} supportTouch={self.uc_state['support_touch']}",
                                feature="input", service_id=26)
            else:
                conn["uc_connected"] = False
                await self._uc_set_phase("denied", f"phone denied cross control (code={code})")
                self.events.log("UC_DENIED", f"cid={cid} code={code}", feature="input", service_id=26)
                if (self.uc_state.get("trigger_used") == 1
                        and not self.uc_state.get("retried_dialog")
                        and not conn.get("closed")):
                    self.uc_state["retried_dialog"] = True
                    new_cid = self.uc_cid_counter = (self.uc_cid_counter + 1) % 2**31
                    pw, ph = self._pc_screen_size()
                    self.uc_state.update(cid=new_cid, trigger_used=0)
                    self.events.log("UC_RETRY_WITH_DIALOG",
                                    f"trigger=1 was denied — resending cmd:1 trigger=0 "
                                    f"(the phone will show its accept dialog)",
                                    feature="input", service_id=26)
                    await self._uc_set_phase("needs-accept",
                                             "ACCEPT THE CROSS-CONTROL DIALOG ON THE PHONE")
                    await self._send_ws_text(
                        conn, build_uc_request(self, new_cid, trigger=0, width=pw, height=ph),
                        "UC_REQUEST_SENT(cmd1,trigger0-retry)")
            return
        if cmd == UC_CMD_DISCONNECT:
            conn["uc_connected"] = False
            self.uc_state.update(connected=False, cid=-1)
            await self._uc_set_phase("idle", "phone ended cross control")
            await self._send_ws_text(conn, build_uc_disconnect_resp(self, int(cid or 0)),
                                     "UC_DISCONNECT_RESP_SENT(cmd6)")
            return
        if cmd == UC_CMD_DISCONNECT_RESP:
            conn["uc_connected"] = False
            self.uc_state.update(connected=False, cid=-1)
            await self._uc_set_phase("idle", "cross control disconnected")
            return
        return

    async def start_cross_control(self, conn: dict | None = None, trigger: int = 1) -> dict:
        c = conn or self._live_ws()
        if c is None:
            return {"ok": False, "error": "no live phone connection"}
        if self.uc_state.get("connected"):
            return {"ok": True, "detail": "cross control already connected"}
        self.uc_cid_counter = (self.uc_cid_counter + 1) % 2**31
        cid = self.uc_cid_counter
        pw, ph = self._pc_screen_size()
        self.uc_state.update(cid=cid, trigger_used=int(trigger),
                             retried_dialog=(trigger == 0))
        ok = await self._send_ws_text(
            c, build_uc_request(self, cid, trigger=int(trigger), width=pw, height=ph),
            f"UC_REQUEST_SENT(cmd1,cid={cid},trigger={trigger})")
        await self._uc_set_phase(
            "starting" if trigger else "needs-accept",
            "waiting for the phone to accept cross control" if trigger
            else "accept the dialog on the phone",
            connected=False)
        return {"ok": ok, "cid": cid, "trigger": trigger}

    async def stop_cross_control(self, conn: dict | None = None) -> dict:
        c = conn or self._live_ws()
        cid = int(self.uc_state.get("cid") or -1)
        self.uc_state.update(connected=False)
        if c is not None and cid != -1 and not c.get("closed"):
            ok = await self._send_ws_text(c, build_uc_disconnect(self, cid),
                                          f"UC_DISCONNECT_SENT(cmd5,cid={cid})")
        else:
            ok = False
        await self._uc_set_phase("idle", "cross control stopped")
        if c is not None:
            c["uc_connected"] = False
        return {"ok": ok or c is None, "detail": "svc-26 cmd:5 sent" if ok else "no active session"}

    async def send_uc_mouse_move(self, conn: dict, x: int, y: int, buttons: int = 0) -> bool:
        if conn is None or conn.get("closed"):
            return False
        self._uc_move_n = int(getattr(self, "_uc_move_n", 0)) + 1
        if self._uc_move_n % 120 == 1:
            self.events.log("UC_MOUSE_MOVE",
                            f"#{self._uc_move_n} -> ({x},{y}) displayId=0 "
                            f"(further moves quiet; buttons/keys still logged)",
                            feature="input", service_id=26)
        await self._send_raw(conn, ws_frame(
            0x1, build_input_mouse_move(self, 0, int(x), int(y)).encode("utf-8")))
        return True

    async def _send_raw_bool(self, conn: dict, frame_str: str) -> bool:
        await self._send_raw(conn, ws_frame(0x1, frame_str.encode("utf-8")))
        return True

    async def send_uc_mouse_button(self, conn: dict, key: int, down: bool,
                                   x: int, y: int) -> bool:
        if conn is None or conn.get("closed"):
            return False
        await self._send_raw_bool(conn, build_input_mouse_button(
            self, 0, int(key), int(x), int(y), bool(down)))
        self.events.log("UC_MOUSE_BUTTON",
                        f"key={key} down={bool(down)} xy=({x},{y}) displayId=0",
                        feature="input", service_id=26)
        return True

    async def send_uc_scroll(self, conn: dict, x: int, y: int,
                             v: int = 0, h: int = 0) -> bool:
        if conn is None or conn.get("closed"):
            return False
        ok = True
        if v:
            ok &= await self._send_raw_bool(conn, build_input_mouse_button(
                self, 0, 4, int(x), int(y), v > 0))
        if h:
            ok &= await self._send_raw_bool(conn, build_input_mouse_button(
                self, 0, 5, int(x), int(y), h > 0))
        return bool(ok)

    async def send_uc_key(self, conn: dict, key: int, down: bool,
                          meta_state: int = 0) -> bool:
        if conn is None or conn.get("closed"):
            return False
        self._uc_key_n = int(getattr(self, "_uc_key_n", 0)) + 1
        if self._uc_key_n % 40 == 1:
            self.events.log("UC_KEY",
                            f"#{self._uc_key_n} key={key} down={bool(down)} meta={int(meta_state)} "
                            f"(subsequent keys quiet)", feature="input", service_id=26)
        await self._send_raw_bool(conn, build_input_key(
            self, 0, int(key), bool(down), meta_state=int(meta_state)))
        return True

    async def set_cross_control_pointer(self, show: bool = True,
                                        conn: dict | None = None) -> bool:
        c = conn or self._live_ws()
        if c is None:
            return False
        self.uc_state["pointer_shown"] = bool(show)
        return await self._send_ws_text(c, build_uc_show_pointer(self, bool(show)),
                                        f"UC_SHOW_POINTER_SENT(cmd4,show={bool(show)})")

from .base import SamyujService

class CrossControlService(SamyujService):
    service_id = 26
    id = "crosscontrol"
    name = "Cross Control Service (Universal Control)"
    description = ("svc-26: one mouse+keyboard across PC and phone. "
                   "cmd:1 request (trigger:1 auto-accept), cmd:2 response (phone panel WxH), "
                   "cmd:3 answer phone request, cmd:4 phone pointer, cmd:5/6 teardown; "
                   "input flows over svc-16 with displayId=0 into the phone's virtual mouse/keyboard")

    def actions(self) -> list[dict]:
        return [
            {"name": "start", "label": "Start Cross Control (cmd:1)", "description": "Ask the phone for Universal Control (auto-accept; falls back to the phone dialog once if denied)"},
            {"name": "start_dialog", "label": "Start With Phone Dialog", "description": "svc-26 cmd:1 trigger:0 — the phone shows its accept dialog"},
            {"name": "stop", "label": "Stop Cross Control (cmd:5)", "description": "Send svc-26 cmd:5 disconnect"},
            {"name": "pointer_on", "label": "Show Phone Pointer (cmd:4)", "description": "Make the injected pointer visible on the phone screen"},
            {"name": "pointer_off", "label": "Hide Phone Pointer (cmd:4)", "description": "Hide the injected pointer on the phone screen"},
        ]

    def get_state(self) -> dict:
        return {**super().get_state(), **dict(getattr(self.h, "uc_state", {}) or {})}

    async def invoke(self, action: str, params: dict | None = None,
                     conn: dict | None = None) -> dict:
        c = conn or self.h._live_ws()
        if action == "start":
            r = await self.h.start_cross_control(c, trigger=1)
            return {"ok": bool(r.get("ok")), "detail": f"cid={r.get('cid')} trigger=1 (auto-accept)"}
        if action == "start_dialog":
            r = await self.h.start_cross_control(c, trigger=0)
            return {"ok": bool(r.get("ok")), "detail": f"cid={r.get('cid')} trigger=0 (phone dialog)"}
        if action == "stop":
            return await self.h.stop_cross_control(c)
        if action in ("pointer_on", "pointer_off"):
            return await self.h.request(f"uc-pointer:{'on' if action == 'pointer_on' else 'off'}", c)
        return await super().invoke(action, params, conn)
