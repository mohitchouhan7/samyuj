from __future__ import annotations
import ssl
from samyuj.protocol.constants import IN_CMD_SET_MODE
from samyuj.core.events import _console_show, feature_of_tag as _feature_of_tag
from samyuj.transport.websocket import ws_frame

import asyncio
import contextlib
import time

from samyuj.media.present import is_decoder_gate_active
from samyuj.protocol.constants import (
    IN_CMD_CURSOR_ICON,
    IN_CMD_IME_STATE,
    IN_CMD_REQUEST_CAP_RESP,
    IN_CMD_SYNC_MODE,
    INPUT_MODE_MOUSE,
    TOUCH_MODE_DIRECT,
)
from samyuj.protocol.services import (
    build_input_key,
    build_input_mouse_button,
    build_input_mouse_move,
    build_input_mouse_wheel,
    build_input_set_mode,
    build_input_text,
    build_input_touch,
    build_input_touch_cancel,
    scroll_notch_distance,
)

class InputServiceMixin:

    async def send_touch(self, conn: dict, event: int, x: float, y: float,
                         pointer_id: int = 0, quiet: bool = False,
                         display_id: int | None = None) -> bool:
        disp = display_id if (isinstance(display_id, int) and display_id >= 0) else conn.get("as_display_id")
        if disp is None:
            self.events.log("INPUT_SKIP", "no displayId seen yet (need cmd:3/8/16)")
            return False
        with contextlib.suppress(Exception):
            if getattr(self, "media_diag", None) is not None:
                self.media_diag.note_input({2: "move", 1: "press", 0: "release"}.get(int(event), "move"))
        if quiet:
            if conn.get("closed"):
                return False
            xi, yi = int(x), int(y)
            frame_b = ws_frame(0x1, build_input_touch(self, disp, event, xi, yi,
                                                      pointer_id).encode("utf-8"))
            if event == 2:
                if getattr(self, "_in_synthetic_drag", False):
                    await self._send_raw(conn, frame_b)
                    self._last_touch_xy = (disp, xi, yi)
                    self.input_stats["moves_sent"] += 1
                    return True
                if self._last_touch_xy == (disp, xi, yi):
                    self.input_stats["moves_suppressed_dup"] += 1
                    return True
                ok_mv = await self._send_input_nowait(conn, frame_b)
                if ok_mv:
                    self._last_touch_xy = (disp, xi, yi)
                    self.input_stats["moves_sent"] += 1
                    self._input_stats_maybe_log(conn)
                    now_pli = time.monotonic()
                    if now_pli - self._last_drag_pli_mono > 2.5:
                        if is_decoder_gate_active():
                            self._last_drag_pli_mono = now_pli
                            with contextlib.suppress(Exception):
                                asyncio.get_running_loop().create_task(
                                    self.request_stream_pli(conn))
                                self.input_stats["pli_during_drag"] += 1
                return ok_mv
            self._last_touch_xy = None
            await self._send_raw(conn, frame_b)
            self.input_stats["press" if event == 1 else "release"] += 1
            self.events.log("GUI_TOUCH", f"evt={event} xy={xi},{yi} disp={disp}")
            return True
        ok = await self._send_ws_text(
            conn,
            build_input_touch(self, disp, event, int(x), int(y), pointer_id),
            f"INPUT_TOUCH_SENT(evt={event},xy={int(x)},{int(y)},disp={disp})")
        return ok

    async def send_touch_frac(self, conn: dict, event: int, xf: float, yf: float,
                              quiet: bool = True, display_id: int | None = None,
                              geom: tuple[int, int] | None = None) -> bool:
        if isinstance(geom, (tuple, list)) and len(geom) == 2 and geom[0] and geom[1]:
            w, h = int(geom[0]), int(geom[1])
        else:
            w, h = conn.get("as_geom") or self.desktop_res
        x = min(max(float(xf), 0.0), 1.0) * max(w - 1, 0)
        y = min(max(float(yf), 0.0), 1.0) * max(h - 1, 0)
        return await self.send_touch(conn, event, x, y, quiet=quiet, display_id=display_id)

    async def send_drag(self, conn: dict, x0f: float, y0f: float, x1f: float, y1f: float,
                        duration_ms: int = 200, steps: int = 4,
                        display_id: int | None = None,
                        geom: tuple[int, int] | None = None,
                        hold_ms: int = 0) -> bool:
        if getattr(self, "_drag_inflight", False):
            self.input_stats["drags_skipped_overlap"] = \
                self.input_stats.get("drags_skipped_overlap", 0) + 1
            return False
        self._drag_inflight = True

        async def _drag_body() -> bool:
            self._in_synthetic_drag = True
            try:
                if not await self.send_touch_frac(conn, 1, x0f, y0f,
                                                  display_id=display_id, geom=geom):
                    return False
                n_steps = max(1, int(steps))
                step = max(0.004, duration_ms / 1000.0 / n_steps)
                for i in range(1, n_steps + 1):
                    if int(hold_ms) > 0 and n_steps >= 3:
                        t = min(1.0, i / float(n_steps - 1))
                    else:
                        t = i / float(n_steps)
                    await self.send_touch_frac(
                        conn, 2, x0f + (x1f - x0f) * t, y0f + (y1f - y0f) * t,
                        display_id=display_id, geom=geom)
                    await asyncio.sleep(step)
                if int(hold_ms) > 0:
                    await asyncio.sleep(min(0.5, float(hold_ms) / 1000.0))
                return await self.send_touch_frac(conn, 0, x1f, y1f,
                                                  display_id=display_id, geom=geom)
            finally:
                self._in_synthetic_drag = False

        try:
            if (
                getattr(self, "use_desktop_thread", True)
            ):
                return await self._run_on_desktop_thread(
                    _drag_body(), stat_key="input_events_dispatched"
                )
            return await _drag_body()
        finally:
            self._drag_inflight = False

    def _resolve_disp_and_pixels(self, conn: dict, fx: float, fy: float,
                                 display_id: int | None = None,
                                 geom: tuple[int, int] | None = None) -> tuple[int | None, int, int]:
        disp = display_id if (isinstance(display_id, int) and display_id >= 0) else conn.get("as_display_id")
        if isinstance(geom, (tuple, list)) and len(geom) == 2 and geom[0] and geom[1]:
            w, h = int(geom[0]), int(geom[1])
        else:
            w, h = conn.get("as_geom") or self.desktop_res
        xi = int(min(max(float(fx), 0.0), 1.0) * max(w - 1, 0))
        yi = int(min(max(float(fy), 0.0), 1.0) * max(h - 1, 0))
        return disp, xi, yi

    async def send_stream_wheel(self, conn: dict, fx: float, fy: float,
                                dy: float, dx: float = 0.0,
                                display_id: int | None = None,
                                geom: tuple[int, int] | None = None) -> bool:
        if conn is None or conn.get("closed"):
            return False
        disp, xi, yi = self._resolve_disp_and_pixels(conn, fx, fy, display_id=display_id, geom=geom)
        if disp is None:
            return False
        frames = build_input_mouse_wheel(self, disp, xi, yi, dy=dy, dx=dx)
        if not frames:
            return True
        for f_str in frames:
            await self._send_raw(conn, ws_frame(0x1, f_str.encode("utf-8")))
            self.input_stats["wheel_sent"] = int(self.input_stats.get("wheel_sent", 0)) + 1
        return True

    async def send_stream_scroll(self, conn: dict, fx: float, fy: float,
                                 dy: float, dx: float = 0.0,
                                 display_id: int | None = None,
                                 geom: tuple[int, int] | None = None,
                                 duration_ms: int = 85, steps: int = 4,
                                 hold_ms: int = 45,
                                 mode: str | None = None) -> bool:
        if conn is None or conn.get("closed"):
            return False
        eff_mode = str(mode or getattr(self, "stream_scroll_mode", "touch") or "touch").strip().lower()
        if eff_mode == "wheel":
            return await self.send_stream_wheel(conn, fx, fy, dy, dx,
                                                display_id=display_id, geom=geom)
        if conn.get("_scroll_busy"):
            return True
        conn["_scroll_busy"] = True
        try:
            if eff_mode == "hybrid":
                await self.send_stream_wheel(conn, fx, fy, dy, dx,
                                             display_id=display_id, geom=geom)
            step_frac = float(getattr(self, "stream_scroll_step", 0.12) or 0.12)
            step_y = scroll_notch_distance(dy, step_frac)
            step_x = scroll_notch_distance(dx, step_frac, min_notch=0.35)
            x0 = min(max(float(fx), 0.08), 0.92)
            y0 = min(max(float(fy), 0.08), 0.92)
            x1 = min(max(x0 - step_x, 0.02), 0.98)
            y1 = min(max(y0 - step_y, 0.02), 0.98)
            return await self.send_drag(conn, x0, y0, x1, y1,
                                        duration_ms=max(10, int(duration_ms)),
                                        steps=max(1, int(steps)),
                                        hold_ms=max(0, int(hold_ms)),
                                        display_id=display_id, geom=geom)
        finally:
            conn["_scroll_busy"] = False

    async def send_stream_mouse_move(self, conn: dict, fx: float, fy: float,
                                     display_id: int | None = None,
                                     geom: tuple[int, int] | None = None) -> bool:
        if conn is None or conn.get("closed"):
            return False
        disp, xi, yi = self._resolve_disp_and_pixels(conn, fx, fy, display_id=display_id, geom=geom)
        if disp is None:
            return False
        if self._last_mouse_xy == (disp, xi, yi):
            return True
        frame_b = ws_frame(0x1, build_input_mouse_move(self, disp, xi, yi).encode("utf-8"))
        ok = await self._send_input_nowait(conn, frame_b)
        if ok:
            self._last_mouse_xy = (disp, xi, yi)
            self.input_stats["mouse_moves_sent"] = int(self.input_stats.get("mouse_moves_sent", 0)) + 1
        return ok

    async def send_stream_mouse_button(self, conn: dict, button_code: int, down: bool,
                                       fx: float, fy: float,
                                       display_id: int | None = None,
                                       geom: tuple[int, int] | None = None) -> bool:
        if conn is None or conn.get("closed"):
            return False
        disp, xi, yi = self._resolve_disp_and_pixels(conn, fx, fy, display_id=display_id, geom=geom)
        if disp is None:
            return False
        frame_b = ws_frame(0x1, build_input_mouse_button(self, disp, int(button_code), xi, yi, bool(down)).encode("utf-8"))
        await self._send_raw(conn, frame_b)
        self.input_stats["mouse_buttons_sent"] = int(self.input_stats.get("mouse_buttons_sent", 0)) + 1
        return True

    async def send_touch_cancel(self, conn: dict, display_id: int | None = None,
                                pointer_id: int = 0) -> bool:
        self._drag_inflight = False
        self._last_touch_xy = None
        if conn is None or conn.get("closed"):
            return False
        disp = display_id if (isinstance(display_id, int) and display_id >= 0) else conn.get("as_display_id")
        if disp is None:
            return False
        frame_b = ws_frame(0x1, build_input_touch_cancel(self, disp, 0, 0, pointer_id).encode("utf-8"))
        await self._send_raw(conn, frame_b)
        self.input_stats["touch_cancels_sent"] = int(self.input_stats.get("touch_cancels_sent", 0)) + 1
        return True

    async def send_key_event(self, conn: dict, key: int, down: bool,
                             display_id: int | None = None,
                             meta_state: int = 0, native: bool = True,
                             flags: int | None = None, quiet: bool = True) -> bool:
        if conn is None or conn.get("closed"):
            return False
        disp = display_id if (isinstance(display_id, int) and display_id >= 0) else (conn.get("as_display_id") or 0)
        msg = build_input_key(self, disp, int(key), bool(down), native=native,
                              meta_state=int(meta_state), flags=flags)
        if quiet:
            await self._send_raw(conn, ws_frame(0x1, msg.encode("utf-8")))
            self.input_stats["keys_sent"] = int(self.input_stats.get("keys_sent", 0)) + 1
            return True
        ok = await self._send_ws_text(conn, msg, f"GUI_KEY_SENT(svc16 cmd3,key={key},down={down},disp={disp})")
        if ok:
            self.input_stats["keys_sent"] = int(self.input_stats.get("keys_sent", 0)) + 1
        return ok

    async def send_key_tap(self, conn: dict, key: int,
                           display_id: int | None = None,
                           meta_state: int = 0, native: bool = True,
                           flags: int | None = None, quiet: bool = True) -> bool:
        ok1 = await self.send_key_event(conn, key, True, display_id=display_id,
                                        meta_state=meta_state, native=native,
                                        flags=flags, quiet=quiet)
        ok2 = await self.send_key_event(conn, key, False, display_id=display_id,
                                        meta_state=meta_state, native=native,
                                        flags=flags, quiet=quiet)
        return bool(ok1 and ok2)

    async def send_text_commit(self, conn: dict, text: str,
                               display_id: int | None = None,
                               quiet: bool = True) -> bool:
        if conn is None or conn.get("closed") or not text:
            return False
        disp = display_id if (isinstance(display_id, int) and display_id >= 0) else (conn.get("as_display_id") or 0)
        msg = build_input_text(self, disp, str(text))
        if quiet:
            await self._send_raw(conn, ws_frame(0x1, msg.encode("utf-8")))
            self.input_stats["text_committed"] = int(self.input_stats.get("text_committed", 0)) + 1
            return True
        ok = await self._send_ws_text(conn, msg, f"INPUT_TEXT_SENT(svc16 cmd7,disp={disp},len={len(text)})")
        if ok:
            self.input_stats["text_committed"] = int(self.input_stats.get("text_committed", 0)) + 1
        return ok

    async def send_input_mode(self, conn: dict, input_mode: int = INPUT_MODE_MOUSE,
                              touch_mode: int = TOUCH_MODE_DIRECT) -> bool:
        if conn is None or conn.get("closed"):
            return False
        self.input_state["input_mode"] = int(input_mode)
        self.input_state["touch_mode"] = int(touch_mode)
        return await self._send_ws_text(
            conn, build_input_set_mode(self, int(input_mode), int(touch_mode)),
            f"INPUT_SET_MODE_SENT(svc16 cmd12,input={input_mode},touch={touch_mode})")

    async def send_swipe(self, conn: dict, direction: str = "right",
                         duration_ms: int = 120, steps: int = 6,
                         post_snap_delay: float = 0.0) -> None:
        w, h = conn.get("as_geom") or self.desktop_res
        fr = {"right": ((0.25, 0.5), (0.75, 0.5)),
              "left": ((0.75, 0.5), (0.25, 0.5)),
              "up": ((0.5, 0.75), (0.5, 0.25)),
              "down": ((0.5, 0.25), (0.5, 0.75))}.get(direction.lower())
        if fr is None:
            self.events.log("INPUT_SWIPE_BAD_DIR", str(direction))
            return
        (x0f, y0f), (x1f, y1f) = fr
        steps = max(1, int(steps))
        self.events.log("INPUT_SWIPE_START",
                        f"dir={direction} dur={duration_ms}ms geom={w}x{h} "
                        f"displayId={conn.get('as_display_id')}")
        await self.send_touch(conn, 1, x0f * w, y0f * h)
        step = max(0.005, duration_ms / 1000.0 / steps)
        for i in range(1, steps + 1):
            t = i / steps
            await self.send_touch(conn, 2,
                                  (x0f + (x1f - x0f) * t) * w,
                                  (y0f + (y1f - y0f) * t) * h)
            await asyncio.sleep(step)
        await self.send_touch(conn, 0, x1f * w, y1f * h)
        self.events.log("INPUT_SWIPE_DONE", f"dir={direction}")
        eff_delay = float(post_snap_delay) if post_snap_delay > 0 else (1.2 if getattr(self, "auto_swipe", None) else 0.0)
        if eff_delay > 0:
            await asyncio.sleep(eff_delay)
            if self.media and self._pcs:
                await self.force_snapshot(conn, tag="post_swipe")

    async def send_tap(self, conn: dict, xf: float, yf: float,
                       hold_ms: int = 15, display_id: int | None = None,
                       geom: tuple[int, int] | None = None) -> None:
        if isinstance(geom, (tuple, list)) and len(geom) == 2 and geom[0] and geom[1]:
            w, h = int(geom[0]), int(geom[1])
        else:
            w, h = conn.get("as_geom") or self.desktop_res
        await self.send_touch(conn, 1, xf * w, yf * h, display_id=display_id)
        await asyncio.sleep(max(0.005, float(hold_ms) / 1000.0))
        await self.send_touch(conn, 0, xf * w, yf * h, display_id=display_id)
        self.events.log("INPUT_TAP_DONE", f"frac={xf},{yf}")

    async def force_snapshot(self, conn: dict, tag: str = "manual") -> None:
        st = conn.get("media_last_frame")
        if st is not None:
            self._save_snapshot(conn, st[0], st[1], st[2], tag=tag)
        else:
            self.events.log("MEDIA_SNAPSHOT_SKIP", "no frame received yet")

    async def _keepalive(self, conn: dict) -> None:
        n = 0
        try:
            while not conn["closed"]:
                await asyncio.sleep(2.0)
                if conn["closed"]:
                    break
                n += 1
                await self._send_raw(conn, ws_frame(0x9, b"sc-keepalive"))
                conn["log"].line(f"WS PING >>> #{n}")
                self.events.log("WS_PING_SENT", f"conn{conn['id']:03d} n={n}")
        except (ConnectionError, ssl.SSLError, OSError, asyncio.CancelledError):
            pass

    async def _send_ws_text(self, conn: dict, text: str, tag: str) -> bool:
        if conn["closed"]:
            return False
        payload = text.encode("utf-8")
        await self._send_raw(conn, ws_frame(0x1, payload))
        conn["log"].line(f"WS TEXT <<< [{tag}] {text}")
        self.events.log(tag, f"conn{conn['id']:03d}: {text}",
                        feature=_feature_of_tag(tag))
        if _console_show(_feature_of_tag(tag), f"WS-OUT/{tag}"):
            print(f"[conn{conn['id']:03d} WS-OUT/{tag}] {text[:600]}")
        return True

    async def _handle_input_inbound(self, conn: dict, frame: dict, data, cmd, tag: str) -> None:
        d = data if isinstance(data, dict) else {}
        inner = d.get("data") if isinstance(d.get("data"), dict) else d
        self.input_seen.append({"tag": tag, "cmd": cmd, "payload": inner})
        if len(self.input_seen) > 400:
            del self.input_seen[:200]
        if cmd == IN_CMD_REQUEST_CAP_RESP:
            disp_c = inner.get("displayId", 0)
            caps_c = inner.get("caps", 7)
            self.input_state["caps_by_display"][disp_c] = caps_c
            conn.setdefault("input_caps_acked", {})[disp_c] = caps_c
            self.events.log("INPUT_CAP_RESP_RECV",
                            f"{tag} displayId={disp_c} caps={caps_c} ver={inner.get('ver')}",
                            feature="input")
        elif cmd == IN_CMD_CURSOR_ICON:
            self.input_state["cursor_icon"] = {
                "displayId": inner.get("displayId"),
                "width": inner.get("width"),
                "height": inner.get("height"),
                "xHotspot": inner.get("xHotspot"),
                "yHotspot": inner.get("yHotspot"),
                "hasIcon": bool(inner.get("icon")),
            }
            self.events.log("INPUT_CURSOR_ICON",
                            f"{tag} displayId={inner.get('displayId')} hotspot=({inner.get('xHotspot')},{inner.get('yHotspot')})",
                            feature="input")
        elif cmd == IN_CMD_IME_STATE:
            show_ime = bool(inner.get("show"))
            self.input_state["ime_visible"] = show_ime
            self.events.log("INPUT_IME_STATE_RECV",
                            f"{tag} displayId={inner.get('displayId')} show={show_ime}",
                            feature="input")
        elif cmd == IN_CMD_SET_MODE:
            self.input_state["input_mode"] = int(inner.get("inputMode") or 0)
            self.input_state["touch_mode"] = int(inner.get("touchMode") or 0)
            self.events.log("INPUT_SET_MODE_RECV",
                            f"{tag} inputMode={self.input_state['input_mode']} touchMode={self.input_state['touch_mode']}",
                            feature="input")
        elif cmd == IN_CMD_SYNC_MODE:
            if inner.get("inputMode") is not None:
                self.input_state["input_mode"] = int(inner.get("inputMode") or 0)
            if inner.get("touchModeSupported") is not None:
                self.input_state["touch_supported"] = bool(inner.get("touchModeSupported"))
            self.events.log("INPUT_SYNC_MODE",
                            f"{tag} inputMode={inner.get('inputMode')} touchSupported={inner.get('touchModeSupported')}",
                            feature="input")
        else:
            self.events.log("INPUT_MSG_RECV", f"{tag} cmd={cmd} keys={sorted(inner.keys())}", feature="input")

    async def _handle_dragdrop(self, conn: dict, frame: dict, data, cmd, tag: str) -> None:
        d = data if isinstance(data, dict) else {}
        if isinstance(d.get("data"), dict) and "cmd" not in d:
            d = d["data"]
        self.drag_seen.append({"tag": tag, "cmd": cmd, "payload": d})
        if len(self.drag_seen) > 400:
            del self.drag_seen[:200]
        self.events.log("DRAGDROP_RECV", f"{tag} cmd={cmd} keys={sorted(d.keys())} files={len(d.get('file') or [])}", feature="file_transfer")

from .base import SamyujService

class InputDragDropService(SamyujService):
    service_id = 16
    id = "input_dragdrop"
    name = "Input Injection & Drag-and-Drop Service"
    description = "svc-16 keyboard (cmd:3), mouse move/button (cmd:4/5), text commit (cmd:7), touch (cmd:9), mode (cmd:12) & svc-24 R4DragData/R4DropData/DragEnter"

    def actions(self) -> list[dict]:
        return [
            {"name": "key_back", "label": "Send Back Key (4)", "description": "Inject Android KEYCODE_BACK (svc-16 cmd:3)"},
            {"name": "key_home", "label": "Send Home Key (3)", "description": "Inject Android KEYCODE_HOME (svc-16 cmd:3)"},
            {"name": "key_recents", "label": "Send Recents Key (187)", "description": "Inject Android KEYCODE_APP_SWITCH (svc-16 cmd:3)"},
            {"name": "drag_enter", "label": "Send DragEnter (svc-24 cmd:3)", "description": "Notify phone that PC cursor entered with drag payload"},
            {"name": "drag_text", "label": "Drag Text to Phone (svc-24 cmd:1)", "description": "Send R4DragData with text payload", "default_params": {"text": "Dragged from Linux PC"}},
            {"name": "drop", "label": "Send Drop Event (svc-24 cmd:2)", "description": "Send R4DropData at (500, 500)", "default_params": {"x": 500, "y": 500}},
            {"name": "stop_all", "label": "Stop All Display Sessions", "description": "Tear down Desktop, AppStream, ScreenShare, and Webcam"},
        ]

    def get_state(self) -> dict:
        return {
            **super().get_state(),
            "input_seen": len(getattr(self.h, "input_seen", []) or []),
            "drag_seen": len(getattr(self.h, "drag_seen", []) or []),
        }

    async def invoke(self, action: str, params: dict | None = None,
                     conn: dict | None = None) -> dict:
        params = params or {}
        c = conn or self.h._live_ws()
        if action == "key_back":
            return await self.h.request("keyevent:4", c)
        if action == "key_home":
            return await self.h.request("keyevent:3", c)
        if action == "key_recents":
            return await self.h.request("keyevent:187", c)
        if action == "send_text":
            return await self.h.request(f"text:{params.get('text', '')}", c)
        if action == "drag_enter":
            return await self.h.request("drag-enter:1", c)
        if action == "drag_text":
            txt = str(params.get("text") or "Dragged from Linux PC")
            return await self.h.request(f"drag-text:{txt}", c)
        if action == "drop":
            x = int(params.get("x", 500))
            y = int(params.get("y", 500))
            return await self.h.request(f"drop:{x}x{y}", c)
        if action == "stop_all":
            return await self.h.request("stop", c)
        return await super().invoke(action, params, conn)
