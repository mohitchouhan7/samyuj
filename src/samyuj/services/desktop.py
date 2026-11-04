from __future__ import annotations
from samyuj.protocol.constants import APPSTREAM_SOURCE_DESKTOP
from samyuj.session.models import new_session_state
from samyuj.core.tasks import _make_threadsafe_task

import asyncio
import contextlib
import time

from samyuj.protocol.constants import DESKTOP_VIEW_DESKTOP
from samyuj.protocol.services import (
    build_appstream_resize,
    build_appstream_start,
    build_desktop_reply,
    build_desktop_request,
    build_desktop_set_resolution,
    build_display_set_resolution,
)

_RDP_ERR = {
    145: "no VirtualDeviceManager permission",
    275: "RDP already in use",
    293: "RDP session busy",
    296: "hardware video encoder shortage",
    305: "desktop capability disabled",
    306: "blocked by device administrator",
    1537: "secondary / work user not allowed",
}

class MobileDesktopServiceMixin:

    def _desktop_supported_modes(self) -> list:
        out: list = []
        seen: set = set()
        for key in ("desktop", "display_service"):
            for m in (self.phone_resolutions.get(key) or {}).get("supported") or []:
                if not isinstance(m, dict):
                    continue
                try:
                    w, h = int(m.get("width") or 0), int(m.get("height") or 0)
                except (TypeError, ValueError):
                    continue
                if w < 64 or h < 64:
                    continue
                w, h = w & ~1, h & ~1
                if (w, h) in seen:
                    continue
                seen.add((w, h))
                with contextlib.suppress(TypeError, ValueError):
                    out.append((w, h, float(m.get("fps") or 60.0)))
        return out

    def _pick_rotate_target(self, cur_w: int, cur_h: int, portrait: bool,
                            explicit: tuple | None = None) -> tuple:
        if explicit:
            w, h = int(explicit[0]) & ~1, int(explicit[1]) & ~1
            return (w, h, 60.0, "explicit")
        modes = self._desktop_supported_modes()
        long_side, short_side = max(cur_w, cur_h), min(cur_w, cur_h)
        want = (short_side, long_side) if portrait else (long_side, short_side)
        if modes:
            exact = next((m for m in modes if (m[0], m[1]) == want), None)
            if exact:
                return (exact[0], exact[1], exact[2], "advertised-exact")
            same_orient = [m for m in modes
                           if (m[1] > m[0]) == bool(portrait) and m[0] != m[1]]
            if same_orient:
                want_ar = want[0] / float(want[1])
                best = max(same_orient,
                           key=lambda m: (-round(abs((m[0] / float(m[1])) - want_ar), 2),
                                          m[0] * m[1]))
                return (best[0], best[1], best[2], "advertised-nearest")
        return (want[0] & ~1, want[1] & ~1, 60.0,
                "swapped" if modes else "swapped-no-advertised-list")

    async def _wait_resize_verdict(self, display_id, timeout: float = 2.5) -> dict | None:
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._resize_waiters[display_id] = fut
        self._resize_waiters[None] = fut
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            return None
        finally:
            for k in (display_id, None):
                if self._resize_waiters.get(k) is fut:
                    self._resize_waiters.pop(k, None)

    async def _wait_geometry_verified(self, w: int, h: int, t0: float,
                                      timeout: float = 4.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            cur = (self.phone_resolutions.get("desktop") or {}).get("current") or {}
            mono = (self.phone_resolutions.get("desktop") or {}).get("current_mono") or 0.0
            if mono >= t0 and int(cur.get("width") or 0) == int(w) \
                    and int(cur.get("height") or 0) == int(h):
                return True
            await asyncio.sleep(0.15)
        return False

    async def rotate_desktop(self, conn: dict | None = None, target: str = "toggle",
                             verify_timeout: float = 4.0) -> dict:
        c = conn or self._live_ws()
        rec: dict = {"ok": False, "detail": "", "target": None, "method": None,
                     "verified": None, "strategy": None}
        if c is None:
            rec["detail"] = "no live phone connection"
            return rec

        want = str(target or "toggle").strip().lower()
        explicit = None
        cur_w, cur_h = int(self.desktop_res[0]), int(self.desktop_res[1])
        if "x" in want:
            with contextlib.suppress(ValueError):
                ew, eh = (max(160, int(x)) for x in want.split("x", 1))
                explicit = (ew, eh)
            portrait = bool(explicit) and explicit[1] > explicit[0]
        elif want in ("portrait", "vertical", "1"):
            portrait = True
        elif want in ("landscape", "horizontal", "0"):
            portrait = False
        else:
            portrait = cur_w >= cur_h

        w, h, fps, strategy = self._pick_rotate_target(cur_w, cur_h, portrait, explicit)
        rec["target"] = f"{w}x{h}"
        rec["strategy"] = strategy
        orient = "portrait" if h > w else "landscape"

        self.desktop_res = (w, h)
        self.desktop_rotation = 1 if h > w else 0
        self.phone_resolutions["desktop"]["current"] = {"width": w, "height": h}
        self.phone_resolutions["display_service"]["current"] = {
            "width": w, "height": h, "fps": fps}
        t0 = time.monotonic()
        ok1 = await self._send_ws_text(
            c, build_desktop_set_resolution(self, w, h),
            f"RQ_DESKTOP_ROTATE_SENT(svc1 cmd33,{w}x{h})")
        ok2 = await self._send_ws_text(
            c, build_display_set_resolution(self, w, h, fps=fps),
            f"RQ_DISPLAY_ROTATE_SENT(svc10 msgId3,{w}x{h}@{fps})")
        self.events.log("DESKTOP_ROTATE_PERSISTED",
                        f"{w}x{h} {orient} strategy={strategy} "
                        f"(desktop_resolution_remote is read by the phone "
                        f"at session START only)", feature="desktop")

        view = c.get("desktop_view") or {"mirror": 1, "desktop": 2,
                                         "mobile": 3}.get(c.get("as_pkg"), 2)
        live = bool(c.get("desktop_started") or c.get("as_session_id") is not None
                    or c.get("as_setup_seen"))
        if not live:
            rec.update(ok=bool(ok1 or ok2), method="persist-only", verified=None,
                       detail=f"no running Mobile Desktop session - {w}x{h} {orient} "
                              f"persisted and will be used by the next start")
            return rec

        sid_r = c.get("as_session_id")
        disp_r = c.get("as_display_id")
        verdict = None

        if view == 1:
            self.events.log("DESKTOP_ROTATE_LIVE_SKIPPED",
                            "view=1 (mirror): the phone always refuses cmd:17 for "
                            "mirror displays, so restarting the session "
                            "instead", feature="desktop")
        elif sid_r is None and disp_r is None:
            self.events.log("DESKTOP_ROTATE_LIVE_SKIPPED",
                            "no svc-15 sessionId/displayId known yet - restarting "
                            "the session instead", feature="desktop")
        else:
            ok3 = await self._send_ws_text(
                c, build_appstream_resize(
                    self, sid_r if sid_r is not None else 0,
                    disp_r if disp_r is not None else 0,
                    w, h, density=self.as_density, scale=self.as_scale,
                    custom_display=True, pkg=(c.get("as_pkg") or "desktop")),
                f"RQ_DESKTOP_ROTATE_LIVE_SENT(svc15 cmd17,{w}x{h})")
            verdict = await self._wait_resize_verdict(disp_r, timeout=2.5)
            if verdict is None:
                if await self._wait_geometry_verified(w, h, t0, 1.5):
                    rec["method"] = "live-cmd17"
                    rec.update(ok=True, verified=True,
                               detail=f"rotated to {w}x{h} {orient} live via svc-15 "
                                      f"cmd:17 (strategy={strategy}); no cmd:26 verdict "
                                      f"but the phone reported the new geometry back")
                    self.events.log("DESKTOP_ROTATE_OK", rec["detail"], feature="desktop")
                    return rec
                self.events.log("DESKTOP_ROTATE_LIVE_NOVERDICT",
                                f"sent={ok3} but no svc-15 cmd:26 within 2.5s and the "
                                f"phone never reported the new geometry back - it "
                                f"ignored cmd:17 (unsupported on this phone "
                                f"build). Falling back to a session restart.",
                                feature="desktop")
            elif verdict.get("success"):
                rec["method"] = "live-cmd17"
                verified = await self._wait_geometry_verified(w, h, t0, verify_timeout)
                rec.update(ok=True, verified=verified,
                           detail=f"rotated to {w}x{h} {orient} live via svc-15 "
                                  f"cmd:17 (strategy={strategy}); phone confirmed "
                                  f"success" + ("" if verified else
                                                " but has not reported the new "
                                                "geometry back yet"))
                self.events.log("DESKTOP_ROTATE_OK", rec["detail"], feature="desktop")
                return rec
            else:
                self.events.log("DESKTOP_ROTATE_LIVE_REFUSED",
                                f"phone answered cmd:26 success=false "
                                f"error={verdict.get('error')} - restarting the "
                                f"session instead", feature="desktop")

        if not self._desktop_stream_has_video():
            rec["method"] = "refused-no-video"
            rec.update(ok=False, verified=False,
                       detail=(f"the phone refused the live resize to {w}x{h} "
                               f"AND no desktop video has arrived on this "
                               f"session - it is still waiting for you to tap "
                               f"ALLOW on the phone (or its Mobile Desktop "
                               f"tile). Resolve that first, then rotate."))
            self.events.log("DESKTOP_ROTATE_SKIPPED_NO_VIDEO", rec["detail"],
                            feature="desktop")
            return rec
        rec["method"] = "restart"
        await self._stop_appstream(c, "rotate-restart", send=True, only_kind="desktop")
        await self._stop_desktop(c, "rotate-restart", send=True)
        await asyncio.sleep(0.35)
        name = {1: "mirror", 2: "desktop", 3: "mobile"}.get(int(view), "desktop")
        rq = await self.request(f"mobiledesktop:{name}", c)
        verified = await self._wait_geometry_verified(w, h, t0, verify_timeout)
        rec.update(ok=bool(rq.get("ok")), verified=verified,
                   detail=f"rotated to {w}x{h} {orient} by RESTARTING the {name} "
                          f"session (strategy={strategy}); the phone re-reads "
                          f"desktop_resolution_remote at start"
                          + ("; phone confirmed the new geometry" if verified else
                             "; geometry not confirmed by the phone yet - watch "
                             "for svc-1 cmd:34/56"))
        self.events.log("DESKTOP_ROTATE_OK" if rec["ok"] else "DESKTOP_ROTATE_FAIL",
                        rec["detail"], feature="desktop")
        return rec

    def fit_desktop_resolution(self, width: int, height: int) -> tuple:
        w, h = int(width) & ~1, int(height) & ~1
        if w < 64 or h < 64:
            return (max(64, w), max(64, h))
        portrait = h > w
        modes: list = []
        with contextlib.suppress(Exception):
            for key in ("desktop", "display_service"):
                for m in (self.phone_resolutions.get(key) or {}).get("supported") or []:
                    if not isinstance(m, dict):
                        continue
                    mw, mh = int(m.get("width") or 0), int(m.get("height") or 0)
                    if mw >= 64 and mh >= 64:
                        modes.append((mw & ~1, mh & ~1))
        if not modes:
            return (w, h)
        if (w, h) in modes:
            return (w, h)
        same = [m for m in modes if (m[1] > m[0]) == portrait and m[0] != m[1]]
        if not same:
            self.events.log(
                "DESKTOP_RES_ORIENTATION_UNADVERTISED",
                f"{w}x{h} ({'portrait' if portrait else 'landscape'}) is not in the "
                f"phone's advertised mode list {sorted(set(modes))} - asking anyway; "
                f"if the phone refuses it (svc-15 cmd:26 success=false, or "
                f"'no enough codec to resize display') that orientation is not "
                f"supported by its hardware encoder", feature="desktop")
            return (w, h)
        want_ar = w / float(h)
        best = max(same, key=lambda m: (-round(abs((m[0] / float(m[1])) - want_ar), 2),
                                        -abs(m[0] * m[1] - w * h)))
        if best != (w, h):
            self.events.log(
                "DESKTOP_RES_FITTED",
                f"requested {w}x{h} is not in the phone's advertised mode list "
                f"{sorted(set(modes))} - using {best[0]}x{best[1]} instead "
                f"(the phone rejects an unsupported pair, which kills the "
                f"stream silently)", feature="desktop")
        return best

    def _mark_desktop_progress(self, conn: dict, why: str) -> None:
        conn["desktop_progress"] = True
        conn["desktop_progress_why"] = why
        conn["desktop_progress_mono"] = time.monotonic()

    async def _proactive_peer_start(self, conn: dict, gen: int = 0) -> None:
        delay = float(getattr(self, "desktop_fallback_delay", 5.0))
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        if conn.get("closed") or (self._stop_evt and self._stop_evt.is_set()):
            return
        cur_gen = int((conn.get("session") or {}).get("gen", 0))
        if gen and cur_gen != gen:
            return
        if not conn.get("desktop_pending") and not conn.get("desktop_started"):
            return
        if time.monotonic() < conn.get("_user_stopped_until", 0.0):
            return
        if conn.get("desktop_progress"):
            self.events.log(
                "DESKTOP_FALLBACK_SKIPPED",
                f"conn{conn['id']:03d} gen={cur_gen} the phone is already driving "
                f"this session ({conn.get('desktop_progress_why')}) - not sending "
                f"a proactive svc-15 cmd:1", feature="desktop")
            return
        if self.appstream == "off":
            return
        policy = str(getattr(self, "desktop_fallback", "prompt")).lower()
        if policy in ("prompt", "off"):
            self.as_status.update(
                phase="waiting-accept", pkg="desktop", sid=None,
                detail=("the phone received the Mobile Desktop request and is "
                        "waiting for you to TAP ALLOW on the phone (or tap its "
                        "Mobile Desktop tile) - Motorola's per-session "
                        "confirmation"),
                updated=time.time())
            self.events.log(
                "DESKTOP_ACCEPT_WAITING",
                f"conn{conn['id']:03d} gen={cur_gen} no answer to svc-1 cmd:1 "
                f"within {delay:.0f}s (policy={policy}) - the phone shows its "
                f"Allow dialog and sends nothing until the user "
                f"taps; NOT sending the silent svc-15 fallback (it creates a "
                f"video-less zombie session on this phone build)",
                feature="desktop")
            with contextlib.suppress(Exception):
                cb = getattr(self, "on_desktop_waiting_cb", None)
                if callable(cb):
                    cb("Waiting for you to tap ALLOW on the phone (or tap its "
                       "Mobile Desktop tile).")
            if policy == "prompt":
                view_now = int(conn.get("desktop_view") or DESKTOP_VIEW_DESKTOP)
                for attempt in range(1, int(getattr(self, "desktop_fallback_retries", 2)) + 1):
                    await asyncio.sleep(delay)
                    if conn.get("closed") or (self._stop_evt and self._stop_evt.is_set()):
                        return
                    if gen and int((conn.get("session") or {}).get("gen", 0)) != gen:
                        return
                    if not conn.get("desktop_pending") and not conn.get("desktop_started"):
                        return
                    if time.monotonic() < conn.get("_user_stopped_until", 0.0):
                        return
                    if conn.get("desktop_progress"):
                        self.events.log("DESKTOP_ACCEPT_RESOLVED",
                                        f"the phone started driving the session "
                                        f"({conn.get('desktop_progress_why')}) - "
                                        f"standing down", feature="desktop")
                        return
                    await self._send_ws_text(
                        conn, build_desktop_request(self, view_now),
                        f"DESKTOP_ACCEPT_RESENT(svc1 cmd1,attempt {attempt})")
            return
        self._as_bump_instance("desktop silent fallback")
        view_fb = int(conn.get("desktop_view") or DESKTOP_VIEW_DESKTOP)
        pkg_fb = {1: "mirror", 2: "desktop", 3: "mobile"}.get(view_fb, "desktop")
        conn["as_pkg"] = pkg_fb
        ok = await self._send_ws_text(
            conn,
            build_appstream_start(self, pkg_fb, None,
                                  source=APPSTREAM_SOURCE_DESKTOP),
            f"DESKTOP_SILENT_FALLBACK_SENT(svc15 cmd1,pkg={pkg_fb},source=1)")
        if ok:
            conn["as_setup_seen"] = True
            conn.setdefault("session", new_session_state())["feature"] = "desktop"
            self.as_status.update(
                phase="starting", pkg=pkg_fb, sid=None,
                detail=("no answer to the svc-1 invite - started Mobile Desktop "
                        "directly (silent pseudo-package); tap Allow on the phone "
                        "if it asks"),
                updated=time.time())
            self._notify_ui_feature_started(
                "phone_on_pc" if view_fb in (1, 3) else "desktop",
                conn=conn, view=view_fb, pkg=pkg_fb, force_present=True)
        self.events.log(
            "DESKTOP_SILENT_FALLBACK",
            f"conn{conn['id']:03d} gen={cur_gen} sent={ok} - the phone never "
            f"answered svc-1 cmd:1 within {delay:.0f}s (it shows an Allow "
            f"dialog first; if it was missed the session "
            f"never starts). Falling back to the silent svc-15 pseudo-package "
            f"start that the phone accepts without a dialog.", feature="desktop")

    async def _handle_desktop_service(self, conn: dict, frame: dict, data: dict, tag: str) -> None:
        dcmd = data.get("cmd")
        if dcmd in (0, 30, 33, 34, 56):
            self._mark_desktop_progress(conn, f"svc1 cmd:{dcmd}")
        if dcmd == 0:
            view_in = int(data.get("view") or 2)
            mode = {1: "mirror", 2: "desktop", 3: "mobile"}.get(
                view_in, f"view{view_in}")
            self.events.log("DESKTOP_INVITE_RECV",
                            f"{tag} view={view_in} mode={mode} "
                            f"res={data.get('resolution')} -> policy={self.desktop}")
            accept = (self.desktop == "accept")
            if accept:
                if self.exclusive == "auto" and conn.get("ss_started"):
                    await self._stop_screenshare(conn, "desktop-invite")
                await self._stop_appstream(conn, "desktop-invite-local", send=False, only_kind="desktop")
                self._session_reset(conn, tag=tag)
                self._as_bump_instance("phone-desktop-invite")
                conn.pop("_user_stopped_until", None)
                conn["desktop_view"] = view_in
                conn["as_pkg"] = {1: "mirror", 2: "desktop", 3: "mobile"}.get(view_in, "desktop")
            await self._send_ws_text(conn, build_desktop_reply(self, frame, accept),
                                     f"DESKTOP_REPLY_SENT({'accept' if accept else 'decline'})")
            if accept:
                conn["desktop_started"] = True
                conn["desktop_pending"] = False
                conn["desktop_view"] = view_in
                conn["desktop_progress"] = False
                conn["session"]["feature"] = "desktop"
                self._notify_ui_feature_started(
                    "phone_on_pc" if view_in in (1, 3) else "desktop",
                    conn=conn, view=view_in, force_present=True
                )
                if self.appstream != "off":
                    conn["tasks"].append(_make_threadsafe_task(
                        self._proactive_peer_start(conn, conn["session"]["gen"])))
            conn["session"]["feature"] = "desktop" if accept else conn["session"]["feature"]
            return
        if dcmd == 11:
            code = data.get("errorCode")
            self.events.log("DESKTOP_ERROR_RECV",
                            f"{tag} errorCode={code} "
                            f"({_RDP_ERR.get(code, 'unknown')})")
            await self._stop_appstream(conn, reason=f"phone error {code}",
                                       send=False, only_kind="desktop")
            await self._stop_desktop(conn, reason=f"phone error {code}",
                                     send=False)
            return
        if dcmd == 31:
            v = data.get("view")
            if isinstance(v, int):
                self.phone_resolutions["desktop"]["supportedViews"] = v
            self.events.log("DESKTOP_VIEWS_RECV", f"{tag} cmd=31 supportedViews={v}")
            return
        if dcmd == 34:
            di = data.get("displayInfo") if isinstance(data.get("displayInfo"), dict) else {}
            res_list = di.get("resolutions") if isinstance(di.get("resolutions"), list) else []
            cur_res = di.get("resolution") if isinstance(di.get("resolution"), dict) else {}
            if res_list:
                self.phone_resolutions["desktop"]["supported"] = res_list
                self.phone_resolutions["display_service"]["supported"] = res_list
            if cur_res.get("width") and cur_res.get("height"):
                self.phone_resolutions["desktop"]["current"] = cur_res
                self.phone_resolutions["desktop"]["current_mono"] = time.monotonic()
                self.phone_resolutions["display_service"]["current"] = cur_res
            self.events.log("DESKTOP_DISPLAYINFO_RECV",
                            f"{tag} cmd=34 current={cur_res} supported={res_list}")
            return
        if dcmd == 56:
            rx, ry = data.get("resolutionX"), data.get("resolutionY")
            if rx and ry:
                self.phone_resolutions["desktop"]["current"] = {"width": int(rx), "height": int(ry)}
                self.phone_resolutions["desktop"]["current_mono"] = time.monotonic()
            self.events.log("DESKTOP_RES56_RECV",
                            f"{tag} cmd=56 {rx}x{ry} quality={data.get('quality')}")
            return
        if dcmd == 30:
            v30 = int(data.get("view") or conn.get("desktop_view") or 2)
            conn["desktop_view"] = v30
            conn["desktop_started"] = True
            conn["as_pkg"] = {1: "mirror", 2: "desktop", 3: "mobile"}.get(v30, "desktop")
            conn["session"]["feature"] = "desktop"
            s = conn.setdefault("session", new_session_state())
            s["gen"] = int(s.get("gen", 0)) + 1
            self.events.log("DESKTOP_VIEW_SWITCH_RECV",
                            f"{tag} cmd=30 view={v30} pkg={conn['as_pkg']} gen={s['gen']}")
            self._notify_ui_feature_started(
                "phone_on_pc" if v30 in (1, 3) else "desktop",
                conn=conn, view=v30, force_present=True
            )
            return
        if dcmd == 3:
            dr = data.get("displayRotation") if isinstance(data.get("displayRotation"), dict) else {}
            rot3 = dr.get("rotation", data.get("rotation", 0))
            self.desktop_rotation = int(rot3 or 0)
            self.phone_resolutions["desktop"]["rotation"] = self.desktop_rotation
            self.events.log("DESKTOP_ROTATION_RECV", f"{tag} cmd=3 rotation={self.desktop_rotation}")
            return
        if dcmd == 5:
            self.events.log("DESKTOP_STOP_RECV", f"{tag} phone sent svc-1 cmd:5 stop")
            await self._stop_appstream(conn, reason="phone cmd:5", send=False, only_kind="desktop")
            await self._stop_desktop(conn, reason="phone cmd:5", send=False)
            return
        if dcmd in (33, 35, 36):
            if dcmd == 36 and "autoStart" in data:
                self.desktop_autostart = bool(data.get("autoStart"))
                with contextlib.suppress(Exception):
                    self.save_user_settings(phone_at_a_glance=self.desktop_autostart)
            self.events.log("DESKTOP_MSG_RECV",
                            f"{tag} cmd={dcmd} view={data.get('view')} "
                            f"res={data.get('resolution')} keys={sorted(data.keys())}")
            return

    async def stop_desktop(self, conn: dict | None, reason: str = "ui") -> bool:
        c = conn or self._live_ws()
        if c is None:
            return False
        return await self._stop_desktop(c, reason, send=True)

from .base import SamyujService

class MobileDesktopService(SamyujService):
    service_id = 1
    id = "mobiledesktop"
    name = "Mobile Desktop Service"
    description = "svc-1 virtual desktop, mirror & mobile views"

    def actions(self) -> list[dict]:
        return [
            {"name": "start_desktop", "label": "Start Desktop (view:2)", "description": "Request Mobile Desktop mode"},
            {"name": "start_mirror", "label": "Start Mirror (view:1)", "description": "Request Phone Mirror mode"},
            {"name": "start_mobile", "label": "Start Mobile (view:3)", "description": "Request Virtual Mobile mode"},
            {"name": "lock_phone", "label": "Lock Phone Display (cmd:20)", "description": "Put phone physical screen to sleep"},
            {"name": "stop", "label": "Stop Desktop (cmd:5)", "description": "Send svc-1 cmd:5 teardown"},
        ]

    async def invoke(self, action: str, params: dict | None = None,
                     conn: dict | None = None) -> dict:
        c = conn or self.h._live_ws()
        if action == "start_desktop":
            return await self.h.request("mobiledesktop:desktop", c)
        if action == "start_mirror":
            return await self.h.request("mobiledesktop:mirror", c)
        if action == "start_mobile":
            return await self.h.request("mobiledesktop:mobile", c)
        if action == "lock_phone":
            return await self.h.request("lock", c)
        if action == "stop":
            if c:
                await self.h._stop_desktop(c, "service-api")
            return {"ok": True, "detail": "svc-1 cmd:5 sent"}
        return await super().invoke(action, params, conn)

class ResolutionManagerService(SamyujService):
    service_id = 10
    id = "resolution"
    name = "Phone Resolution Discovery & Switcher Service"
    description = (
        "Unified phone resolution discovery & live switching across R4DisplayService "
        "(svc-10 msgId:1/2/3/4), Mobile Desktop (svc-1 cmd:33/34/56), AppStream "
        "(svc-15 cmd:1/3/17/22/26 + supportCustomDisplay), Screen Share "
        "(svc-202 cmd:5/35/70 + setList), and Webcam (svc-3 cmd:3/5 + PC rescaling)"
    )

    def actions(self) -> list[dict]:
        return [
            {"name": "query_all", "label": "Query Phone Supported Resolutions (svc-10 + svc-3)", "description": "Send svc-10 msgId:1 DISPLAY_MSG_ID_QUERY and svc-3 cmd:3 camera query"},
            {"name": "set_display_res", "label": "Set Display Resolution (svc-10 msgId:3)", "description": "Set phone remote display resolution (writes desktop_resolution_remote & resizes the phone-side virtual display)", "default_params": {"width": 1920, "height": 1080, "fps": 60}},
            {"name": "set_desktop_res", "label": "Resize Running Desktop (svc-1 cmd:33)", "description": "Live-resize running Mobile Desktop virtual display", "default_params": {"width": 1920, "height": 1080}},
            {"name": "rotate_desktop", "label": "Rotate Desktop (portrait <-> landscape)", "description": "Swap the Mobile Desktop virtual display geometry via svc-1 cmd:33 + svc-10 msgId:3", "default_params": {"to": "toggle"}},
            {"name": "resize_appstream", "label": "Live-Resize AppStream (svc-15 cmd:17)", "description": "Send the svc-15 cmd:17 resize frame with supportCustomDisplay=True to resize the active AppStream window", "default_params": {"width": 1280, "height": 800, "density": 240, "custom": True}},
            {"name": "set_screenshare_res", "label": "Set Screen Share Resolution & List (svc-202 cmd:35)", "description": "Send CMD_SET_RESOLUTION_FROM_PC with setList + resize PC capture track", "default_params": {"width": 1920, "height": 1080, "auto": 0}},
            {"name": "set_webcam_res", "label": "Set Webcam Output Resolution", "description": "Rescale received phone webcam stream on PC (or 0x0 for native)", "default_params": {"res": "1280x720"}},
        ]

    def get_state(self) -> dict:
        return {
            **super().get_state(),
            "resolutions": self.h.get_resolution_summary() if hasattr(self.h, "get_resolution_summary") else {},
        }

    async def invoke(self, action: str, params: dict | None = None,
                     conn: dict | None = None) -> dict:
        params = params or {}
        c = conn or self.h._live_ws()
        if action == "query_all":
            return await self.h.request("res-query", c)
        if action == "set_display_res":
            w = int(params.get("width") or 1920)
            h_ = int(params.get("height") or 1080)
            fps = int(float(params.get("fps") or 60))
            return await self.h.request(f"display-res:{w}x{h_}@{fps}", c)
        if action == "set_desktop_res":
            w = int(params.get("width") or 1920)
            h_ = int(params.get("height") or 1080)
            return await self.h.request(f"desktop-res:{w}x{h_}", c)
        if action == "rotate_desktop":
            to = str(params.get("to") or "toggle")
            return await self.h.request("desktop-rotate", c, arg=("" if to == "toggle" else to))
        if action == "resize_appstream":
            w = int(params.get("width") or 1280)
            h_ = int(params.get("height") or 720)
            dpi = int(params.get("density") or self.h.as_density)
            return await self.h.request(f"appstream-resize:{w}x{h_}@{dpi}", c)
        if action == "set_screenshare_res":
            w = int(params.get("width") or 1920)
            h_ = int(params.get("height") or 1080)
            auto = int(params.get("auto", 0))
            return await self.h.request(f"ss-res:{w}x{h_}:{'auto' if auto else 'manual'}", c)
        if action == "set_webcam_res":
            res = str(params.get("res") or "1280x720").strip()
            return await self.h.request(f"webcam-res:{res}", c)
        return await super().invoke(action, params, conn)
