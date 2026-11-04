from __future__ import annotations

import asyncio
import contextlib

from samyuj.protocol.services import (
    build_appstream_hb_pause,
    build_appstream_stop,
    build_desktop_stop,
    build_ss_stop,
)
from .models import new_session_state
from .registry import ServiceSessionRegistry

class SessionTeardownMixin:

    @staticmethod
    def _ensure_session_registry(conn: dict) -> ServiceSessionRegistry:
        reg = conn.get("sessions")
        if not isinstance(reg, ServiceSessionRegistry):
            reg = ServiceSessionRegistry()
            conn["sessions"] = reg
        return reg

    def _sync_legacy_session_feature(self, conn: dict) -> None:
        s = conn.setdefault("session", new_session_state())
        reg = self._ensure_session_registry(conn)
        if conn.get("desktop_started"):
            reg.get("desktop").status = "live"
        if conn.get("as_session_id") is not None or conn.get("as_setup_seen"):
            reg.get("appstream").status = "live"
            reg.get("appstream").session_id = conn.get("as_session_id")
            reg.get("appstream").display_id = conn.get("as_display_id")
        if conn.get("ss_started"):
            reg.get("screenshare").status = "live"
        if conn.get("wc_started"):
            reg.get("webcam").status = "live"
        prim = reg.primary_feature()
        if prim is not None:
            s["feature"] = prim
        elif conn.get("desktop_started"):
            s["feature"] = "desktop"
        elif conn.get("as_session_id") is not None or conn.get("as_setup_seen"):
            s["feature"] = "appstream"
        elif conn.get("ss_started"):
            s["feature"] = "screenshare"
        elif conn.get("wc_started"):
            s["feature"] = "webcam"
        else:
            s["feature"] = None

    def reset_webcam_session(self, conn: dict, tag: str = "") -> int:
        reg = self._ensure_session_registry(conn)
        wc_st = reg.get("webcam")
        gen = wc_st.reset()
        conn["wc_trigger_answered"] = False
        conn["wc_start_sent"] = False
        if getattr(self, "stream_registry", None) is not None:
            for k in list(self.stream_registry.streams):
                if self.stream_registry.streams[k].owner_service == "webcam":
                    self.stream_registry.mark_stopped(k)
        self._sync_legacy_session_feature(conn)
        self.events.log("WEBCAM_SESSION_RESET",
                        f"conn{conn.get('id', 0):03d} wc_gen={gen} {tag}".strip(),
                        feature="webcam")
        return gen

    def reset_desktop_session(self, conn: dict, tag: str = "") -> int:
        reg = self._ensure_session_registry(conn)
        gen = reg.get("desktop").reset()
        conn.pop("desktop_started", None)
        self._sync_legacy_session_feature(conn)
        return gen

    def reset_appstream_session(self, conn: dict, tag: str = "") -> int:
        reg = self._ensure_session_registry(conn)
        gen = reg.get("appstream").reset()
        hb = conn.pop("as_hb_task", None)
        if hb is not None:
            hb.cancel()
        for k in ("as_setup_seen", "as_peer_start_sent", "as_answered_session",
                  "as_session_id", "as_webrtc_session", "as_display_id",
                  "as_geom", "as_hb_task", "input_cap_sent", "as_pkg", "as_cmp"):
            conn.pop(k, None)
        self._sync_legacy_session_feature(conn)
        return gen

    def reset_screenshare_session(self, conn: dict, tag: str = "") -> int:
        reg = self._ensure_session_registry(conn)
        gen = reg.get("screenshare").reset()
        conn.pop("ss_started", None)
        self._sync_legacy_session_feature(conn)
        return gen

    def _session_reset(self, conn: dict, tag: str = "") -> int:
        if "webcam" in (tag or "").lower():
            return self.reset_webcam_session(conn, tag=tag)
        s = conn.setdefault("session", new_session_state())
        s["gen"] = int(s.get("gen", 0)) + 1
        reg = self._ensure_session_registry(conn)
        reg.get("desktop").reset()
        reg.get("appstream").reset()
        reg.get("screenshare").reset()
        for k in ("as_setup_seen", "as_peer_start_sent", "as_answered_session",
                  "as_session_id", "as_webrtc_session", "as_display_id",
                  "as_geom", "as_hb_task", "input_cap_sent", "input_cap_displays",
                  "as_pkg", "as_cmp", "desktop_started", "desktop_pending",
                  "desktop_as_sid", "desktop_display_id", "ss_started",
                  "desktop_progress", "desktop_progress_why", "desktop_progress_mono",
                  "media_last_frame", "media_connected_at", "as_media_connected"):
            conn.pop(k, None)
        if isinstance(getattr(self, "stream_target_sizes", None), dict):
            self.stream_target_sizes.pop("desktop", None)
            self.stream_target_sizes.pop("phone_on_pc", None)
        self._sync_legacy_session_feature(conn)
        self.events.log("SESSION_RESET",
                        f"conn{conn['id']:03d} gen={s['gen']} {tag}".strip())
        return s["gen"]

    async def _close_pc_safe(self, pc) -> None:
        if pc is None:
            return
        with contextlib.suppress(Exception):
            mloop = getattr(pc, "_sc_media_loop", None)
            try:
                cur_loop = asyncio.get_running_loop()
            except RuntimeError:
                cur_loop = None
            if mloop is not None and mloop.is_running() and cur_loop is not mloop:
                fut = asyncio.run_coroutine_threadsafe(pc.close(), mloop)
                await asyncio.wait_for(asyncio.wrap_future(fut), timeout=2.0)
                return
            await pc.close()

    async def _release_local_sessions(self, conn: dict) -> None:
        hb = conn.pop("as_hb_task", None)
        if hb is not None:
            hb.cancel()
        if conn.get("_session_migrated"):
            return
        sid = conn.pop("as_session_id", None)
        sids_to_close = {sid, conn.get("as_webrtc_session"), conn.get("desktop_as_sid"), conn.get("app_as_sid")}
        for k_s in list((conn.get("as_sessions") or {}).keys()):
            sids_to_close.add(k_s)
        for key in sids_to_close:
            pc = self._pcs.pop(key, None) if key is not None else None
            if pc is not None:
                await self._close_pc_safe(pc)
        if self._ss_pc is not None and conn.get("ss_started"):
            with contextlib.suppress(Exception):
                await self._ss_pc.close()
            self._ss_pc = None
        if self._ss_track is not None and conn.get("ss_started"):
            with contextlib.suppress(Exception):
                self._ss_track.stop()
            self._ss_track = None
            self.ss_stats["state"] = "idle"
            self.ss_stats["backend"] = ""

    async def _stop_desktop(self, conn: dict, reason: str = "", send: bool = True) -> bool:
        if self._presenter_bridge is not None and (
                self._presenter_sid is None
                or self._presenter_sid == conn.get("desktop_as_sid")):
            self._stop_presenter(f"desktop session ended ({reason or 'stop'})")
        was = bool(conn.get("desktop_started") or conn.get("desktop_pending"))
        conn["desktop_started"] = False
        conn["desktop_pending"] = False
        reg = self._ensure_session_registry(conn)
        reg.get("desktop").status = "idle"
        self._sync_legacy_session_feature(conn)
        if getattr(self, "power_mgr", None) is not None:
            with contextlib.suppress(Exception):
                self.power_mgr.note_service_active("desktop", False)
        if not (was or send):
            return False
        if send and not conn.get("closed") and conn.get("ws"):
            await self._send_ws_text(conn, build_desktop_stop(self),
                                     f"DESKTOP_STOP_SENT(cmd5,{reason or 'explicit'})")
        else:
            self.events.log("DESKTOP_STOP_LOCAL",
                            f"conn{conn['id']:03d} reason={reason or 'explicit'} "
                            f"(no frame sent: was={was} send={send})")
        return True

    async def _stop_appstream(self, conn: dict, reason: str = "", send: bool = True,
                              sid=None, only_kind: str | None = None) -> bool:
        if self._presenter_bridge is not None and (
                sid is None or only_kind is not None
                or self._presenter_sid == sid):
            self._stop_presenter(f"appstream session ended ({reason or 'stop'})")
        as_sessions = conn.get("as_sessions")
        if isinstance(as_sessions, dict) and (sid is not None or only_kind in ("desktop", "app")):
            target_sids = []
            if sid is not None and sid in as_sessions:
                target_sids.append(sid)
            elif only_kind == "desktop":
                target_sids = [k for k, v in list(as_sessions.items()) if isinstance(v, dict) and v.get("is_desktop")]
                if not target_sids and conn.get("desktop_as_sid") is not None:
                    target_sids = [conn.get("desktop_as_sid")]
            elif only_kind == "app":
                target_sids = [k for k, v in list(as_sessions.items()) if isinstance(v, dict) and not v.get("is_desktop")]
                if not target_sids and conn.get("app_as_sid") is not None:
                    target_sids = [conn.get("app_as_sid")]

            if not target_sids and only_kind == "desktop":
                _cur_sid = conn.get("as_session_id")
                if _cur_sid is not None and conn.get("as_pkg") in (None, "desktop", "mobile", "mirror"):
                    target_sids = [_cur_sid]
            for t_sid in target_sids:
                as_sessions.pop(t_sid, None)
                pc_t = self._pcs.pop(t_sid, None)
                if pc_t is not None:
                    await self._close_pc_safe(pc_t)
                if getattr(self, "stream_registry", None) is not None:
                    for k in (f"appstream:{t_sid}", f"desktop:{t_sid}"):
                        self.stream_registry.mark_stopped(k)
                if isinstance(getattr(self, "stream_target_sizes", None), dict):
                    self.stream_target_sizes.pop(f"sid:{t_sid}", None)
                if send and not conn.get("closed") and conn.get("ws") and t_sid is not None:
                    await self._send_ws_text(
                        conn, build_appstream_stop(self, t_sid),
                        f"APPSTREAM_STOP_SENT(cmd7,sid={t_sid},{reason or 'scoped'})"
                    )

            if only_kind == "desktop":
                conn.pop("desktop_as_sid", None)
                conn.pop("desktop_display_id", None)
                if getattr(self, "stream_registry", None) is not None:
                    for _sk, _sr in list(self.stream_registry.streams.items()):
                        if _sr.owner_service == "desktop" or str(_sk).startswith("desktop:"):
                            self.stream_registry.mark_stopped(_sk)
                if isinstance(getattr(self, "stream_target_sizes", None), dict):
                    self.stream_target_sizes.pop("desktop", None)
                    self.stream_target_sizes.pop("phone_on_pc", None)
            elif only_kind == "app":
                conn.pop("app_as_sid", None)
                conn.pop("app_pkg", None)
                conn.pop("app_display_id", None)

            remaining = [v for v in as_sessions.values() if isinstance(v, dict)]
            if remaining:
                rem = remaining[-1]
                conn["as_session_id"] = rem.get("sid")
                conn["as_pkg"] = rem.get("pkg")
                if rem.get("display_id") is not None:
                    conn["as_display_id"] = rem.get("display_id")
                if rem.get("geom"):
                    conn["as_geom"] = rem.get("geom")
                self._sync_legacy_session_feature(conn)
                return bool(target_sids)
            if target_sids:
                hb_d = conn.pop("as_hb_task", None)
                if hb_d is not None:
                    hb_d.cancel()
                if send and not conn.get("closed") and conn.get("ws"):
                    await self._send_ws_text(conn, build_appstream_hb_pause(self),
                                             "APPSTREAM_HB_PAUSE_SENT(cmd25)")
                for k in ("as_setup_seen", "as_peer_start_sent", "as_answered_session",
                          "as_session_id", "as_webrtc_session", "input_cap_sent",
                          "input_cap_displays", "media_last_frame",
                          "as_pkg", "as_cmp",
                          "app_as_sid", "app_pkg", "app_display_id",
                          "desktop_as_sid", "desktop_display_id"):
                    conn.pop(k, None)
                as_sessions.clear()
                reg_d = self._ensure_session_registry(conn)
                reg_d.get("appstream").status = "idle"
                self._sync_legacy_session_feature(conn)
                self._as_bump_instance(reason or "stop")
                return True

        sid_last = sid if sid is not None else (conn.get("as_session_id") or conn.get("desktop_as_sid"))
        hb = conn.pop("as_hb_task", None)
        if hb is not None:
            hb.cancel()
        was = sid_last is not None or bool(conn.get("as_setup_seen"))
        pc = self._pcs.pop(sid_last, None) if sid_last is not None else None
        if pc is not None:
            await self._close_pc_safe(pc)
        if getattr(self, "stream_registry", None) is not None:
            if sid_last is not None:
                for k in (f"appstream:{sid_last}", f"desktop:{sid_last}"):
                    self.stream_registry.mark_stopped(k)
            if only_kind in (None, "desktop"):
                for _sk, _sr in list(self.stream_registry.streams.items()):
                    if _sr.owner_service == "desktop" or str(_sk).startswith("desktop:"):
                        self.stream_registry.mark_stopped(_sk)
        for k in ("as_setup_seen", "as_peer_start_sent", "as_answered_session",
                  "as_session_id", "as_webrtc_session", "input_cap_sent",
                  "input_cap_displays", "media_last_frame",
                  "as_pkg", "as_cmp",
                  "app_as_sid", "app_pkg", "app_display_id",
                  "desktop_as_sid", "desktop_display_id"):
            conn.pop(k, None)
        if isinstance(as_sessions, dict):
            as_sessions.clear()
        reg = self._ensure_session_registry(conn)
        reg.get("appstream").status = "idle"
        self._sync_legacy_session_feature(conn)
        self._as_bump_instance(reason or "stop")
        if not was:
            return False
        if send and not conn.get("closed") and conn.get("ws"):
            if sid_last is not None:
                await self._send_ws_text(conn, build_appstream_hb_pause(self),
                                         "APPSTREAM_HB_PAUSE_SENT(cmd25)")
                await self._send_ws_text(conn, build_appstream_stop(self, sid_last),
                                         f"APPSTREAM_STOP_SENT(cmd7,sid={sid_last},"
                                         f"{reason or 'explicit'})")
            else:
                self.events.log("APPSTREAM_STOP_NO_SID",
                                f"conn{conn['id']:03d} no sessionId recorded "
                                f"({reason or 'explicit'}) — nothing to release")
        return True

    async def _stop_screenshare(self, conn: dict, reason: str = "",
                                send: bool = True) -> bool:
        was = bool(conn.get("ss_started"))
        conn["ss_started"] = False
        s = conn.setdefault("session", new_session_state())
        if s.get("feature") == "screenshare":
            s["feature"] = None
        if self._ss_pc is not None:
            with contextlib.suppress(Exception):
                await self._ss_pc.close()
            self._ss_pc = None
        if self._ss_track is not None:
            with contextlib.suppress(Exception):
                self._ss_track.stop()
            self._ss_track = None
        self.ss_stats["state"] = "idle"
        self.ss_stats["backend"] = ""
        self.ss_stats["capture"] = ""
        self._release_ss_virtual_display(f"screenshare stopped ({reason or 'explicit'})")
        if getattr(self, "power_mgr", None) is not None:
            with contextlib.suppress(Exception):
                self.power_mgr.note_service_active("screenshare", False)
        if not was:
            return False
        if send and not conn.get("closed") and conn.get("ws"):
            await self._send_ws_text(conn, build_ss_stop(self, self.ss_session_id),
                                     f"SCREENSHARE_STOP_SENT(cmd26,{reason or 'explicit'})")
        return True

    async def _stop_all(self, conn: dict, reason: str = "") -> None:
        if getattr(self, "uc_state", {}).get("connected"):
            with contextlib.suppress(Exception):
                await self.stop_cross_control(conn)
        await self._stop_screenshare(conn, reason or "stop-all")
        await self._stop_webcam(conn, reason or "stop-all")
        await self._stop_appstream(conn, reason or "stop-all")
        await self._stop_desktop(conn, reason or "stop-all")

    async def _exclusive_stop(self, conn: dict, keep: str, tag: str = "") -> None:
        if self.exclusive != "auto":
            return
        s = conn.setdefault("session", new_session_state())
        cur = s.get("feature")
        if cur in (None, keep):
            if cur == keep:
                return
        stopped = []
        if keep != "screenshare":
            if await self._stop_screenshare(conn, f"exclusive:{keep}"):
                stopped.append("screenshare")
        if keep != "appstream":
            if await self._stop_appstream(conn, f"exclusive:{keep}"):
                stopped.append("appstream")
        if keep != "desktop":
            if await self._stop_desktop(conn, f"exclusive:{keep}"):
                stopped.append("desktop")
        if stopped:
            self.events.log("SESSION_EXCLUSIVE_STOP",
                            f"conn{conn['id']:03d} stopping {','.join(stopped)} "
                            f"before {keep} {tag}".strip())
            await asyncio.sleep(0.45 if (keep == "webcam" and conn.get("writer") is not None) else 0.15)

    def _as_bump_instance(self, reason: str = "") -> int:
        self._as_instance_seq += 1
        self.as_instance_id = (self._as_instance_base + self._as_instance_seq) % 2**53
        self.events.log("APPSTREAM_INSTANCE_BUMP",
                        f"instanceId={self.as_instance_id} (#{self._as_instance_seq}) "
                        f"reason={reason or 'new session'}")
        return self.as_instance_id

