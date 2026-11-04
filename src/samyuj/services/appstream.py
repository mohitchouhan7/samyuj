from __future__ import annotations
import ssl
from samyuj.protocol.constants import APPSTREAM_SOURCE_PC_APP
from samyuj.protocol.services import build_appstream_start
HAVE_MEDIA = True
from samyuj.core.tasks import _make_threadsafe_task

import asyncio
import contextlib
import time

from samyuj.protocol.services import (
    build_appstream_heartbeat,
    build_appstream_peer_start,
    build_appstream_stream_success,
    build_input_request_cap,
)

class AppStreamServiceMixin:

    async def _as_on_setup_cmd2(self, conn: dict, frame: dict, inner: dict, tag: str) -> None:
        prev_sid = conn.get("as_session_id")
        prev_setup = bool(conn.get("as_setup_seen"))
        new_pkg = inner.get("pkg")
        new_is_desktop = new_pkg in ("desktop", "mobile", "mirror")
        coexisting_desktop = bool(conn.get("desktop_started") and not new_is_desktop)
        saved_desktop_view = conn.get("desktop_view")
        saved_desktop_sid = conn.get("desktop_as_sid")
        saved_desktop_disp = conn.get("desktop_display_id")
        saved_sessions = dict(conn.get("as_sessions") or {})
        if self.exclusive == "auto" and conn.get("ss_started"):
            await self._stop_screenshare(conn, "appstream-invite")
        if coexisting_desktop:
            await self._stop_appstream(conn, "re-invite-app-only", send=False, only_kind="app")
            saved_sessions = dict(conn.get("as_sessions") or {})
        elif prev_setup or prev_sid is not None:
            await self._stop_appstream(conn, "re-invite-local", send=False)
        gen = self._session_reset(conn, tag="appstream cmd:2")
        if coexisting_desktop:
            conn["desktop_started"] = True
            if saved_desktop_view is not None:
                conn["desktop_view"] = saved_desktop_view
            if saved_desktop_sid is not None:
                conn["desktop_as_sid"] = saved_desktop_sid
            if saved_desktop_disp is not None:
                conn["desktop_display_id"] = saved_desktop_disp
            if saved_sessions:
                conn["as_sessions"] = saved_sessions
        elif new_is_desktop:
            conn["as_sessions"] = {
                k: v for k, v in saved_sessions.items()
                if isinstance(v, dict) and not v.get("is_desktop")
            }
            if getattr(self, "stream_registry", None) is not None:
                for _sk, _sr in list(self.stream_registry.streams.items()):
                    if _sr.owner_service == "desktop" or str(_sk).startswith("desktop:"):
                        self.stream_registry.mark_stopped(_sk)
        conn["session"]["feature"] = "appstream"
        conn["as_setup_seen"] = True
        conn["as_peer_start_sent"] = True
        conn["as_pkg"] = new_pkg
        if inner.get("sessionId") is not None:
            conn.setdefault("as_sessions", {})[inner.get("sessionId")] = {
                "sid": inner.get("sessionId"),
                "pkg": new_pkg,
                "display_id": inner.get("displayId"),
                "is_desktop": new_is_desktop,
            }
        conn.pop("_user_stopped_until", None)
        if new_is_desktop:
            conn["desktop_started"] = True
            conn["desktop_pending"] = False
            conn["desktop_view"] = {"mirror": 1, "desktop": 2, "mobile": 3}.get(new_pkg, 2)
            if inner.get("sessionId") is not None:
                conn["desktop_as_sid"] = inner.get("sessionId")
            conn["session"]["feature"] = "desktop"
            self._notify_ui_feature_started(
                "phone_on_pc" if conn["desktop_view"] in (1, 3) else "desktop",
                conn=conn, view=conn["desktop_view"],
                sid=inner.get("sessionId"), pkg=new_pkg,
                display_id=inner.get("displayId"), force_present=True,
            )
        else:
            self._notify_ui_feature_started(
                "appstream", conn=conn, pkg=new_pkg,
                sid=inner.get("sessionId"), force_present=True,
            )
        self.events.log(
            "APPSTREAM_SETUP_RECV",
            f"{tag} pkg={conn['as_pkg']} source={inner.get('source')} "
            f"userId={inner.get('userId')} deviceId={inner.get('deviceId')} "
            f"sessionId={inner.get('sessionId')} gen={gen} instanceId={self.as_instance_id}",
        )
        await self._send_ws_text(conn, build_appstream_peer_start(self, frame), "APPSTREAM_PEER_START_SENT(cmd1)")

    async def _as_on_setparams_cmd3(self, conn: dict, _frame: dict, inner: dict, tag: str) -> None:
        sid = inner.get("sessionId")
        hb = inner.get("hb") or 0
        offer = inner.get("webRTCData")
        disp = inner.get("displayId")
        if isinstance(disp, int) and disp >= 0:
            conn["as_display_id"] = disp
        if inner.get("width") and inner.get("height"):
            conn["as_geom"] = (int(inner["width"]), int(inner["height"]))
            self.phone_resolutions["appstream"]["stream"].update({
                "width": int(inner["width"]),
                "height": int(inner["height"]),
                "density": inner.get("density") or self.as_density,
                "displayId": disp,
            })
        if inner.get("screenWidth") and inner.get("screenHeight"):
            self.phone_resolutions["appstream"]["phoneNative"].update({
                "width": int(inner["screenWidth"]),
                "height": int(inner["screenHeight"]),
                "density": inner.get("screenDensity"),
            })
        self.events.log(
            "APPSTREAM_SETPARAMS_RECV",
            f"{tag} sessionId={sid} ip={inner.get('ip')} hb={hb} "
            f"isWebRTC={inner.get('isWebRTC')} mediaFormat={inner.get('mediaFormat')} "
            f"key={bool(inner.get('key'))} offerLen={len(str(offer or ''))} "
            f"wxh={inner.get('width')}x{inner.get('height')} displayId={disp} "
            f"density={inner.get('density')} error={inner.get('error')}",
        )
        conn["as_session_id"] = sid
        if inner.get("pkg"):
            conn["as_pkg"] = inner.get("pkg")
        _eff_pkg = inner.get("pkg") or ((conn.get("as_sessions") or {}).get(sid) or {}).get("pkg") or conn.get("as_pkg")
        _is_desk = _eff_pkg in ("desktop", "mobile", "mirror")
        if sid is not None:
            conn.setdefault("as_sessions", {})[sid] = {
                "sid": sid,
                "pkg": _eff_pkg,
                "display_id": disp if isinstance(disp, int) and disp >= 0 else conn.get("as_display_id"),
                "geom": conn.get("as_geom"),
                "is_desktop": _is_desk,
            }
        if time.monotonic() < conn.get("_user_stopped_until", 0.0):
            self.events.log("APPSTREAM_SETPARAMS_IGNORED_DURING_STOP", f"{tag} sid={sid}")
            return
        if _is_desk:
            conn["desktop_started"] = True
            conn["desktop_pending"] = False
            conn["desktop_as_sid"] = sid
            if isinstance(disp, int) and disp >= 0:
                conn["desktop_display_id"] = disp
            elif _eff_pkg == "mirror" and conn.get("as_display_id") is None:
                conn["as_display_id"] = 0
                conn["desktop_display_id"] = 0
            conn["desktop_view"] = {"mirror": 1, "desktop": 2, "mobile": 3}.get(
                _eff_pkg, conn.get("desktop_view") or 2
            )
            conn["session"]["feature"] = "desktop"
            self._notify_ui_feature_started(
                "phone_on_pc" if conn["desktop_view"] in (1, 3) else "desktop",
                conn=conn, view=conn["desktop_view"], sid=sid, pkg=_eff_pkg, display_id=disp,
            )
        elif _eff_pkg:
            conn["app_as_sid"] = sid
            conn["app_pkg"] = _eff_pkg
            if isinstance(disp, int) and disp >= 0:
                conn["app_display_id"] = disp
            self._notify_ui_feature_started("appstream", conn=conn, pkg=_eff_pkg, sid=sid)
        answer = None
        is_webrtc = False
        if offer:
            with contextlib.suppress(OSError):
                (self.out_dir / f"appstream_offer_{sid}.json").write_text(str(offer), encoding="utf-8")
            if self.as_answer:
                answer = self.as_answer
                is_webrtc = True
            elif self.media and HAVE_MEDIA:
                answer = await self._media_negotiate(conn, sid, str(offer))
                is_webrtc = bool(answer)
            if is_webrtc:
                conn["as_webrtc_session"] = sid
            if (self.input_cap and isinstance(disp, int) and disp >= 0
                    and conn.get("input_cap_sent")
                    and disp not in (conn.get("input_cap_displays") or set())):
                conn.setdefault("input_cap_displays", set()).add(disp)
                await self._send_ws_text(conn, build_input_request_cap(self, disp), "INPUT_REQUEST_CAP_SENT(svc16 cmd1)")
        elif conn.get("as_answered_session") == sid:
            await self._send_ws_text(conn, build_appstream_stream_success(self, sid, resize=1), "APPSTREAM_PARAMS_ACK_SENT(cmd4,resize1)")
            return
        conn["as_answered_session"] = sid
        await self._send_ws_text(
            conn,
            build_appstream_stream_success(self, sid, is_webrtc, answer),
            f"APPSTREAM_STREAM_SUCCESS_SENT(cmd4,isWebRTC={1 if is_webrtc else 0})",
        )
        if is_webrtc:
            pc_ans = self._pcs.get(sid)
            if pc_ans is not None:
                conn["tasks"].append(_make_threadsafe_task(self._media_send_local_candidates(conn, pc_ans, sid)))
            self.as_status.update(phase="waiting-media", sid=sid, detail="answer sent — waiting for video", updated=time.time())
        else:
            self.as_status.update(
                phase="no-webrtc", sid=sid,
                detail="media negotiation FAILED — phone streams with no video; check aiortc install (pip install aiortc) and MEDIA_* events",
                updated=time.time(),
            )
        try:
            interval = min(25.0, max(2.0, float(hb) * 0.5 if hb else self.as_hb_interval))
        except (TypeError, ValueError):
            interval = self.as_hb_interval
        if not conn.get("as_hb_task"):
            gen = (conn.get("session") or {}).get("gen", 0)
            conn["as_hb_task"] = _make_threadsafe_task(self._as_heartbeat(conn, interval, gen))
            conn["tasks"].append(conn["as_hb_task"])

    async def _as_on_state_cmd8(self, conn: dict, _frame: dict, inner: dict, tag: str) -> None:
        state = inner.get("state")
        disp = inner.get("displayId")
        if isinstance(disp, int) and disp >= 0:
            conn["as_display_id"] = disp
        reason = inner.get("reason")
        self.events.log(
            "APPSTREAM_STATE_RECV",
            f"{tag} state={state} reason={reason} pkg={inner.get('pkg')} "
            f"sessionId={inner.get('sessionId')} displayId={disp} supportTouch={inner.get('supportTouch')}",
        )
        if state == 1:
            if time.monotonic() < conn.get("_user_stopped_until", 0.0):
                return
            if inner.get("pkg"):
                conn["as_pkg"] = inner.get("pkg")
            if inner.get("sessionId") is not None and conn.get("as_session_id") is None:
                conn["as_session_id"] = inner.get("sessionId")
            if conn.get("as_pkg") in ("desktop", "mobile", "mirror"):
                conn["desktop_started"] = True
                conn["desktop_pending"] = False
                conn["desktop_view"] = {"mirror": 1, "desktop": 2, "mobile": 3}.get(
                    conn.get("as_pkg"), conn.get("desktop_view") or 2
                )
                conn["session"]["feature"] = "desktop"
                self._notify_ui_feature_started(
                    "phone_on_pc" if conn["desktop_view"] in (1, 3) else "desktop",
                    conn=conn, view=conn["desktop_view"],
                    sid=conn.get("as_session_id"), pkg=conn.get("as_pkg"), display_id=disp,
                )
            else:
                self._notify_ui_feature_started("appstream", conn=conn)
            target_disp = int(disp if (isinstance(disp, int) and disp >= 0) else (conn.get("as_display_id") or 0))
            cap_disps = conn.setdefault("input_cap_displays", set())
            if self.input_cap and (not conn.get("input_cap_sent") or target_disp not in cap_disps):
                conn["input_cap_sent"] = True
                cap_disps.add(target_disp)
                await self._send_ws_text(conn, build_input_request_cap(self, target_disp), "INPUT_REQUEST_CAP_SENT(svc16 cmd1)")
            return
        if state == 0:
            pkg_retry = conn.get("as_pkg")
            if (pkg_retry and pkg_retry not in ("desktop", "mobile", "mirror")
                    and conn.get("as_last_source") == 1
                    and not conn.get("as_dialog_retry_done")
                    and not conn.get("as_media_connected")
                    and not conn.get("closed")):
                conn["as_dialog_retry_done"] = True
                self.events.log(
                    "APPSTREAM_SOURCE1_DENIED_RETRY",
                    f"pkg={pkg_retry} reason={reason} — retrying with source=0 (the phone will show its accept dialog; tap Allow)",
                    feature="appstream",
                )
                self.as_status.update(
                    phase="needs-accept", sid=None, pkg=pkg_retry,
                    detail="Phone denied the silent start — ACCEPT THE DIALOG ON THE PHONE",
                    updated=time.time(),
                )
                await self._stop_appstream(conn, f"state=0 retry {pkg_retry}", send=False)
                await self._send_ws_text(
                    conn,
                    build_appstream_start(self, pkg_retry, conn.get("as_cmp"), source=APPSTREAM_SOURCE_PC_APP),
                    f"RQ_APPSTREAM_SENT(svc15 cmd1,pkg={pkg_retry},source=0-retry)",
                )
                conn["as_last_source"] = 0
                return
            ev_sid = inner.get("sessionId")
            cur_sid = conn.get("as_session_id")
            if ev_sid is not None and cur_sid is not None and ev_sid != cur_sid:
                old_pc = self._pcs.pop(ev_sid, None)
                if old_pc is not None:
                    with contextlib.suppress(Exception):
                        await old_pc.close()
                if getattr(self, "stream_registry", None) is not None:
                    for k in (f"appstream:{ev_sid}", f"desktop:{ev_sid}"):
                        self.stream_registry.mark_stopped(k)
                (conn.get("as_sessions") or {}).pop(ev_sid, None)
                self.events.log("APPSTREAM_OLD_SESSION_CLOSED", f"conn{conn['id']:03d} closed old sid={ev_sid} while sid={cur_sid} remains active")
                return
            await self._stop_appstream(conn, f"phone state=0 reason={reason}", send=False)
            await self._stop_desktop(conn, f"appstream stopped (reason={reason})", send=False)
            self._session_reset(conn, tag="appstream state=0")

    async def _as_on_stop_cmd7(self, conn: dict, _frame: dict, inner: dict, tag: str) -> None:
        stop_sid = inner.get("sessionId")
        self.events.log("APPSTREAM_STOP_RECV", f"{tag} sessionId={stop_sid} error={inner.get('error')}")
        await self._stop_appstream(conn, f"phone cmd:7 sid={stop_sid}", send=False, sid=stop_sid)

    async def _as_on_rotation_cmd16(self, conn: dict, _frame: dict, inner: dict, tag: str) -> None:
        rot = int(inner.get("rotation") or 0)
        self.phone_resolutions["appstream"]["rotation"] = rot
        cur_g = conn.get("as_geom")
        if isinstance(cur_g, (tuple, list)) and len(cur_g) == 2 and cur_g[0] and cur_g[1]:
            gw, gh = int(cur_g[0]), int(cur_g[1])
            if (rot in (1, 3) and gh > gw) or (rot in (0, 2) and gw > gh):
                conn["as_geom"] = (gh, gw)
            sid16 = inner.get("sessionId")
            if sid16 is not None and isinstance((conn.get("as_sessions") or {}).get(sid16), dict):
                conn["as_sessions"][sid16]["geom"] = conn["as_geom"]
        self.events.log(
            "APPSTREAM_ROTATION_RECV",
            f"{tag} sessionId={inner.get('sessionId')} displayId={inner.get('displayId')} rotation={rot}",
        )

    async def _as_on_webrtc_cmd20(self, _conn: dict, _frame: dict, inner: dict, tag: str) -> None:
        ctype = inner.get("type")
        cdata = str(inner.get("data") or "")
        self.events.log("APPSTREAM_WEBRTC_RECV", f"{tag} sessionId={inner.get('sessionId')} type={ctype} dataLen={len(cdata)}")
        pc = self._pcs.get(inner.get("sessionId"))
        if pc is not None and cdata:
            await self._media_remote_signal(pc, ctype, cdata, tag)

    async def _as_on_resize_cmd26(self, conn: dict, _frame: dict, inner: dict, tag: str) -> None:
        ok26 = bool(inner.get("success"))
        did26 = inner.get("displayId")
        self.phone_resolutions["appstream"]["lastResizeOk"] = ok26
        if ok26:
            conn["as_geom"] = self.desktop_res
        self.events.log(
            "APPSTREAM_RESIZE_RESULT",
            f"{tag} success={ok26} error={inner.get('error')} displayId={did26} — "
            + ("phone APPLIED the new geometry" if ok26 else
               "phone REFUSED the resize (mirror displays are always refused; so is an unsupported geometry, and some phone builds ignore cmd:17 entirely)"),
            feature="appstream",
        )
        with contextlib.suppress(Exception):
            for _k in (did26, None):
                fut = self._resize_waiters.pop(_k, None)
                if fut is not None and not fut.done():
                    fut.set_result({"success": ok26, "displayId": did26, "error": inner.get("error")})

    async def _as_on_focus_cmd12_14(self, conn: dict, _frame: dict, inner: dict, cmd: int, tag: str) -> None:
        pkg_f = inner.get("pkg") or conn.get("as_pkg")
        focus_val = inner.get("focus")
        stopped_cooldown = bool(time.monotonic() < conn.get("_user_stopped_until", 0.0))
        if not stopped_cooldown and focus_val is not False:
            if (pkg_f in ("desktop", "mobile", "mirror") and conn.get("as_session_id")) or conn.get("desktop_started"):
                v_f = {"mirror": 1, "desktop": 2, "mobile": 3}.get(pkg_f, conn.get("desktop_view") or 2)
                conn["desktop_view"] = v_f
                self._notify_ui_feature_started("phone_on_pc" if v_f in (1, 3) else "desktop", conn=conn, view=v_f, force_present=False)
            elif conn.get("app_as_sid") or (conn.get("as_session_id") and pkg_f not in (None, "desktop", "mobile", "mirror")):
                self._notify_ui_feature_started("appstream", conn=conn, force_present=False)
        self.events.log("APPSTREAM_FOCUS_RECV", f"{tag} cmd={cmd} pkg={pkg_f} focus={focus_val}")

    async def _handle_appstream(self, conn: dict, frame: dict, data, cmd,
                                tag: str) -> None:
        inner = (data or {}).get("data") if isinstance(data, dict) else None
        inner = inner if isinstance(inner, dict) else {}
        if cmd in (2, 3, 4):
            self._mark_desktop_progress(conn, f"svc15 cmd:{cmd}")
        if cmd == 2:
            await self._as_on_setup_cmd2(conn, frame, inner, tag)
            return
        if cmd == 3:
            await self._as_on_setparams_cmd3(conn, frame, inner, tag)
            return
        if cmd == 7:
            await self._as_on_stop_cmd7(conn, frame, inner, tag)
            return
        if cmd == 8:
            await self._as_on_state_cmd8(conn, frame, inner, tag)
            return
        if cmd == 10:
            self.events.log("APPSTREAM_HB_ACK", tag)
            return
        if cmd in (12, 13, 14):
            await self._as_on_focus_cmd12_14(conn, frame, inner, cmd, tag)
            return
        if cmd == 16:
            await self._as_on_rotation_cmd16(conn, frame, inner, tag)
            return
        if cmd == 20:
            await self._as_on_webrtc_cmd20(conn, frame, inner, tag)
            return
        if cmd == 22:
            w22, h22 = inner.get("width"), inner.get("height")
            if w22 and h22:
                self.phone_resolutions["appstream"]["phoneNative"] = {
                    "width": int(w22), "height": int(h22),
                    "density": inner.get("density"), "naviMode": inner.get("naviMode"),
                }
            self.events.log("APPSTREAM_DEVICEINFO_RECV", f"{tag} phoneNative={w22}x{h22}@{inner.get('density')}dpi naviMode={inner.get('naviMode')}")
            return
        if cmd == 23:
            self.phone_resolutions["appstream"]["audioState"] = dict(inner)
            st_val = inner.get("state", inner.get("audioState", inner.get("enable")))
            self.phone_resolutions["appstream"]["audioPaused"] = bool(st_val in (0, False, "0", "false", "paused", "stopped"))
            self.events.log("APPSTREAM_AUDIO_STATE_RECV", f"{tag} {inner}")
            return
        if cmd == 26:
            await self._as_on_resize_cmd26(conn, frame, inner, tag)
            return
        self.events.log("APPSTREAM_MSG", f"{tag} — logged, no reply")

    async def _as_heartbeat(self, conn: dict, interval: float, gen: int = 0) -> None:
        n = 0
        try:
            while not conn["closed"]:
                await asyncio.sleep(interval)
                if conn["closed"]:
                    break
                if gen and (conn.get("session") or {}).get("gen", 0) != gen:
                    self.events.log("APPSTREAM_HB_EXIT_STALE_GEN",
                                    f"conn{conn['id']:03d} gen {gen} -> "
                                    f"{(conn.get('session') or {}).get('gen')}")
                    break
                if conn.get("as_hb_task") is not asyncio.current_task():
                    break
                n += 1
                await self._send_ws_text(conn, build_appstream_heartbeat(self),
                                         f"APPSTREAM_HB_SENT(cmd9,#{n})")
        except (ConnectionError, ssl.SSLError, OSError, asyncio.CancelledError):
            pass
        finally:
            if conn.get("as_hb_task") is asyncio.current_task():
                conn.pop("as_hb_task", None)

    async def stop_appstream(self, conn: dict | None, reason: str = "ui",
                             sid=None, only_kind: str | None = None) -> bool:
        c = conn or self._live_ws()
        if c is None:
            return False
        return await self._stop_appstream(c, reason, send=True, sid=sid, only_kind=only_kind)

from .base import SamyujService

class AppStreamService(SamyujService):
    service_id = 15
    id = "appstream"
    name = "AppStream Service"
    description = "svc-15 Android app streaming & virtual display lifecycle"

    def actions(self) -> list[dict]:
        return [
            {"name": "launch_desktop", "label": "Stream Desktop (pseudo-pkg)", "description": "Start svc-15 cmd:1 pkg=desktop"},
            {"name": "launch_mirror", "label": "Stream Mirror (pseudo-pkg)", "description": "Start svc-15 cmd:1 pkg=mirror"},
            {"name": "pause", "label": "Pause Stream (cmd:5)", "description": "Send svc-15 cmd:5 pause"},
            {"name": "resume", "label": "Resume Stream (cmd:6)", "description": "Send svc-15 cmd:6 resume"},
            {"name": "stop", "label": "Stop AppStream (cmd:7)", "description": "Release active AppStream session"},
        ]

    async def invoke(self, action: str, params: dict | None = None,
                     conn: dict | None = None) -> dict:
        params = params or {}
        c = conn or self.h._live_ws()
        if action == "launch_desktop":
            return await self.h.request("appstream:desktop", c)
        if action == "launch_mirror":
            return await self.h.request("appstream:mirror", c)
        if action == "launch_app":
            pkg = str(params.get("pkg") or "desktop")
            return await self.h.request(f"appstream:{pkg}", c)
        if action == "pause":
            return await self.h.request("appstream-pause", c)
        if action == "resume":
            return await self.h.request("appstream-resume", c)
        if action == "stop":
            if c:
                await self.h._stop_appstream(c, "service-api")
            return {"ok": True, "detail": "svc-15 cmd:7 sent"}
        return await super().invoke(action, params, conn)
