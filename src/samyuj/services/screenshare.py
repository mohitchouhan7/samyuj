from __future__ import annotations
from samyuj.protocol.services.screenshare import _ss_env
from samyuj.services.media_pump import _bind_pc_to_media_loop
from samyuj.transport.connection import _boost_pc_sockets_delayed
def _run_cmd(args: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
    import subprocess
    try:
        cp = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
        return cp.returncode, cp.stdout or '', cp.stderr or ''
    except Exception as exc:
        return 1, '', str(exc)
from samyuj.core.tasks import _make_threadsafe_task

import asyncio
import contextlib
import json
import time

try:
    from aiortc import RTCConfiguration, RTCSessionDescription
    HAVE_MEDIA = True
except Exception:
    HAVE_MEDIA = False

from samyuj.media.capture import HAVE_TRACK, _ScreenShareTrack
from samyuj.media.webrtc_adapter import SamyujPeerConnection
from samyuj.protocol.constants import (
    SS_CMD_PHONE_CANDIDATE,
    SS_CMD_PHONE_ERROR,
    SS_CMD_PHONE_ICE_FAILURE,
    SS_CMD_PHONE_OFFER,
    SS_CMD_PHONE_REJECT,
    SS_CMD_PHONE_RESOLUTION,
    SS_CMD_PHONE_START_REQUEST,
    SS_CMD_PHONE_STARTED,
    SS_CMD_PHONE_STOP,
)
from samyuj.protocol.sdp import (
    fix_answer_sdp,
    order_sdp_candidate_lines as _order_sdp_candidate_lines,
    sanitize_answer_sdp,
)
from samyuj.protocol.services import (
    build_ss_answer,
    build_ss_candidate,
    build_ss_connection_ready,
    build_ss_resolution,
    build_ss_switch,
    build_ss_version,
)

import shutil
from samyuj.media.capture import _screen_grab_rgb

class ScreenShareServiceMixin:

    def _ss_virtual_size(self) -> tuple:
        bw, bh = int(self.ss_virtual_res[0]), int(self.ss_virtual_res[1])
        orient = str(self.ss_virtual_orient or "auto").lower()
        if orient == "landscape":
            w, h = max(bw, bh), min(bw, bh)
        elif orient == "portrait":
            w, h = min(bw, bh), max(bw, bh)
        else:
            w, h = bw, bh
        w, h = max(160, w & ~1), max(160, h & ~1)
        budget = int(getattr(self, "ss_max_virtual_pixels", 0) or 0)
        if budget and w * h > budget:
            import math as _math
            k = _math.sqrt(budget / float(w * h))
            w, h = max(160, int(w * k) & ~1), max(160, int(h * k) & ~1)
        return (w, h)

    def set_ss_virtual(self, orient: str | None = None,
                       res: tuple | None = None, pos: str | None = None) -> tuple:
        if pos:
            pv = str(pos).strip().lower()
            if pv in ("right", "left", "above", "below", "none"):
                self.ss_virtual_pos = pv
                with contextlib.suppress(Exception):
                    self.virtual_display.set_position(pv)
        if orient:
            o = str(orient).strip().lower()
            if o in ("auto", "landscape", "portrait", "l", "p"):
                self.ss_virtual_orient = {"l": "landscape", "p": "portrait"}.get(o, o)
        if res:
            rw, rh = int(res[0]), int(res[1])
            if rw >= 160 and rh >= 160:
                self.ss_virtual_res = (rw, rh)
        with contextlib.suppress(Exception):
            self.save_user_settings(
                ss_virtual_orient=self.ss_virtual_orient,
                ss_virtual_pos=self.ss_virtual_pos,
                ss_virtual_res=f"{self.ss_virtual_res[0]}x{self.ss_virtual_res[1]}")
        if self._ss_wants_virtual() and self.ss_stats.get("state") not in (None, "idle"):
            self.virtual_display.release("orientation/size change")
            self._ss_vd = None
        return self._ss_virtual_size()

    def get_phone_extend_res(self, orient: str | None = None) -> tuple[int, int]:
        pw, ph = 0, 0
        with contextlib.suppress(Exception):
            ps = ((self.phone_resolutions.get("screenshare") or {}).get("phoneScreen") or {})
            if int(ps.get("width") or 0) >= 320 and int(ps.get("height") or 0) >= 320:
                pw, ph = int(ps["width"]), int(ps["height"])
        if not (pw and ph):
            with contextlib.suppress(Exception):
                pn = ((self.phone_resolutions.get("appstream") or {}).get("phoneNative") or {})
                if int(pn.get("width") or 0) >= 320 and int(pn.get("height") or 0) >= 320:
                    pw, ph = int(pn["width"]), int(pn["height"])
        if not (pw and ph):
            with contextlib.suppress(Exception):
                if int(self.uc_state.get("phone_w") or 0) >= 320 and int(self.uc_state.get("phone_h") or 0) >= 320:
                    pw, ph = int(self.uc_state["phone_w"]), int(self.uc_state["phone_h"])
        if not (pw and ph):
            pw, ph = int(self.ss_virtual_res[0]), int(self.ss_virtual_res[1])
        if not (pw and ph):
            pw, ph = 2400, 1080

        eff_orient = str(orient or self.ss_virtual_orient or "auto").strip().lower()
        if eff_orient in ("portrait", "p"):
            w, h = min(pw, ph), max(pw, ph)
        elif eff_orient in ("landscape", "l"):
            w, h = max(pw, ph), min(pw, ph)
        else:
            cur_w, cur_h = int(self.ss_virtual_res[0]), int(self.ss_virtual_res[1])
            if cur_h > cur_w:
                w, h = min(pw, ph), max(pw, ph)
            else:
                w, h = max(pw, ph), min(pw, ph)
        return (max(160, w & ~1), max(160, h & ~1))

    @staticmethod
    def describe_aspect_ratio(w: int, h: int) -> str:
        if w <= 0 or h <= 0:
            return "20:9"
        ratio = float(max(w, h)) / float(min(w, h))
        is_p = h > w
        if abs(ratio - (20.0 / 9.0)) < 0.06:
            return "9:20" if is_p else "20:9"
        if abs(ratio - (22.0 / 9.0)) < 0.06:
            return "9:22" if is_p else "22:9"
        if abs(ratio - (16.0 / 9.0)) < 0.05:
            return "9:16" if is_p else "16:9"
        if abs(ratio - (16.0 / 10.0)) < 0.05:
            return "10:16" if is_p else "16:10"
        import math as _m
        g = _m.gcd(int(w), int(h)) or 1
        return f"{int(w)//g}:{int(h)//g}"

    async def rotate_screenshare_extend(self, conn: dict | None = None,
                                        target: str = "toggle") -> dict:
        cw, ch = self._ss_virtual_size()
        t = str(target or "toggle").strip().lower()
        if t in ("landscape", "l"):
            want_orient = "landscape"
        elif t in ("portrait", "p"):
            want_orient = "portrait"
        else:
            want_orient = "landscape" if ch > cw else "portrait"

        nw, nh = (min(cw, ch), max(cw, ch)) if want_orient == "portrait" else (max(cw, ch), min(cw, ch))
        nw, nh = self.set_ss_virtual(orient=want_orient, res=(nw, nh))
        self.ss_res = (nw, nh)
        self.phone_resolutions["screenshare"]["active"] = {
            "width": nw, "height": nh, "auto": 0,
        }

        c = conn or self._live_ws()
        session_live = bool(
            (c and c.get("ss_started"))
            or self._ss_track is not None
            or self.ss_stats.get("state") not in (None, "idle")
        )
        vd_info = self._ss_vd or {}
        if self._ss_wants_virtual() and session_live:
            vd_info = await self._ensure_ss_virtual_display()
            if self._ss_track is not None:
                with contextlib.suppress(Exception):
                    if hasattr(self._ss_track, "set_resolution"):
                        self._ss_track.set_resolution(nw, nh)
                    _reg, _disp, _out = self._ss_capture_source()
                    self._ss_track.set_source(region=_reg, display=_disp, output=_out)
                    self.ss_stats["backend"] = self._ss_track.backend
                    self.ss_stats["capture"] = (
                        "virtual-display" if (_reg or _disp or _out) else "primary-monitor"
                    )
        if c is not None and c.get("ss_started"):
            await self._send_ws_text(
                c, build_ss_resolution(
                    self, self.ss_session_id, nw, nh, auto=0,
                    res_list=self.phone_resolutions["screenshare"]["pcSupportedList"]),
                f"SCREENSHARE_EXTEND_ROTATE_RES_SENT(cmd35,{nw}x{nh})")
            await self._send_ws_text(
                c, build_ss_switch(
                    self, self.ss_session_id, self.ss_share_type,
                    self.ss_audio, self.ss_scale),
                f"SCREENSHARE_EXTEND_ROTATE_SWITCH_SENT(cmd14,{want_orient})")
        ar = self.describe_aspect_ratio(nw, nh)
        self.events.log(
            "SCREENSHARE_EXTEND_ROTATED",
            f"from={cw}x{ch} to={nw}x{nh} orient={want_orient} ratio={ar} "
            f"live={session_live}",
            feature="screen_share",
        )
        return {
            "ok": True,
            "orient": want_orient,
            "width": nw,
            "height": nh,
            "size": (nw, nh),
            "aspect": ar,
            "live": session_live,
            "virtualDisplay": vd_info,
        }

    def extend_install_hint(self) -> str:
        probe = {}
        with contextlib.suppress(Exception):
            probe = self.virtual_display.probe()
        sess = str(probe.get("session") or "unknown")
        out = []
        if sess == "wayland":
            out.append("you are on Wayland, where only wlroots compositors "
                       "(sway/hyprland) can create a headless output")
            if not probe.get("swaymsg"):
                out.append("GNOME/KDE on Wayland cannot create a second display "
                           "for an app; log into an 'Ubuntu on Xorg' session at "
                           "the login screen, then xrandr works")
            if not probe.get("Xvfb"):
                out.append("or install a nested desktop: sudo apt install xvfb openbox")
        else:
            if not probe.get("xrandr"):
                out.append("sudo apt install x11-xserver-utils   # provides xrandr")
            if not probe.get("Xvfb"):
                out.append("sudo apt install xvfb openbox   # nested-desktop fallback")
        if not out:
            out.append("a backend appears to be installed - check the Share Screen "
                       "window status line for why it was rejected")
        return "; ".join(out)

    def _ss_wants_virtual(self) -> bool:
        return (str(self.ss_share_type).lower() == "extend"
                and str(self.ss_virtual_backend).lower() != "off")

    _ss_vd: dict | None = None

    async def _ensure_ss_virtual_display(self) -> dict:
        st = self.ss_stats
        if not self._ss_wants_virtual():
            self._release_ss_virtual_display("share mode is mirror")
            st["virtualDisplay"] = {"ok": False, "backend": "none",
                                    "detail": "mirror mode - primary monitor"}
            self._ss_vd = st["virtualDisplay"]
            return st["virtualDisplay"]
        w, h = self._ss_virtual_size()
        vd = await asyncio.to_thread(self.virtual_display.ensure, w, h,
                                     str(self.ss_virtual_backend))
        info = vd.as_dict()
        st["virtualDisplay"] = info
        self._ss_vd = info
        if info.get("ok") and self._ss_track is not None:
            with contextlib.suppress(Exception):
                self._ss_track.set_source(region=info.get("region"),
                                          display=info.get("display"),
                                          output=info.get("output"))
                st["backend"] = self._ss_track.backend
                st["capture"] = "virtual-display"
        if vd.ok:
            ok_cap, why_cap = await asyncio.to_thread(self._verify_virtual_capture, info)
            info["capture_verified"] = bool(ok_cap)
            info["capture_check"] = why_cap
            self._ss_vd = info
            st["virtualDisplay"] = info
            if not ok_cap:
                st["extend_fallback"] = "mirror"
                self.events.log(
                    "SCREENSHARE_EXTEND_UNUSABLE",
                    f"backend={vd.backend} created {vd.width}x{vd.height} but "
                    f"{why_cap}. Streaming the primary monitor instead, so extend "
                    f"will look like mirror. Fixes: use a compositor that honours "
                    f"an output-less xrandr monitor (Xorg + i3/xfce/openbox/KDE "
                    f"X11), or sway on Wayland, or --ss-virtual-backend xvfb for a "
                    f"nested X desktop you can launch apps into.",
                    feature="screen_share")
                return info
            st["extend_fallback"] = ""
            self.events.log(
                "SCREENSHARE_EXTEND_ACTIVE",
                f"backend={vd.backend} name={vd.name} {vd.width}x{vd.height} "
                f"region={vd.region} display={vd.display} output={vd.output} - "
                f"the phone is now a SECOND PC MONITOR; drag a window onto it",
                feature="screen_share")
        else:
            st["extend_fallback"] = "mirror"
            st["extend_fix_hint"] = self.extend_install_hint()
            self.events.log(
                "SCREENSHARE_EXTEND_UNAVAILABLE",
                f"{vd.detail} hints={vd.hints} - streaming the PRIMARY monitor "
                f"instead, so extend will look identical to mirror. Fix it with "
                f"one of: {vd.hints}", feature="screen_share")
        return info

    def _verify_virtual_capture(self, vd: dict) -> tuple:
        if not (vd or {}).get("ok"):
            return (False, "no virtual display was created")
        reg, disp, out = (vd.get("region"), vd.get("display"), vd.get("output"))
        w = max(64, int(vd.get("width") or 320) // 4)
        h = max(64, int(vd.get("height") or 240) // 4)
        try:
            import time as _t
            a = _screen_grab_rgb(w, h, region=reg, display=disp, output=out)
            _t.sleep(0.3)
            b = _screen_grab_rgb(w, h, region=reg, display=disp, output=out)
        except Exception as exc:
            return (False, f"could not capture the region: {exc!r}")
        if a is None or b is None:
            return (False, "the capture backend returned no pixels for the region")
        if len(set(a[::997])) <= 2 and a == b:
            return (False, "the region captures as a single uniform colour and does "
                           "not change - the compositor is not drawing into the "
                           "output-less monitor, so extend cannot show a real "
                           "second desktop here")
        if a == b:
            return (True, "the region captures correctly (static content - nothing "
                          "was moving during the check)")
        return (True, "the region captures correctly and its content is changing")

    def _release_ss_virtual_display(self, why: str = "") -> None:
        with contextlib.suppress(Exception):
            self.virtual_display.release(why)
        with contextlib.suppress(Exception):
            self.ss_stats["virtualDisplay"] = {"ok": False, "backend": "none",
                                               "detail": f"released ({why})"}
        self._ss_vd = None

    def move_window_to_virtual_display(self, title: str = "") -> dict:
        vd = (self._ss_vd or self.virtual_display.status() or {})
        if not vd.get("ok"):
            reason = str(vd.get("detail") or "no virtual display is active")
            hints = list(vd.get("hints") or [])
            return {"ok": False, "detail": reason, "hints": hints,
                    "fix": self.extend_install_hint()}
        reg = vd.get("region") or {}
        w, h = int(vd.get("width") or 0), int(vd.get("height") or 0)
        x, y = int(reg.get("left") or 0), int(reg.get("top") or 0)
        if vd.get("backend") == "sway":
            sel = f'[title="{title}"]' if title else "focused"
            rc, out, err = _run_cmd(["swaymsg", "move", "container", "to", "output",
                                     str(vd.get("name"))])
            return {"ok": rc == 0, "backend": "sway",
                    "detail": (out or err or f"moved {sel} to output {vd.get('name')}")}
        if not shutil.which("wmctrl") and not shutil.which("xdotool"):
            return {"ok": False, "detail":
                    "install wmctrl (sudo apt install wmctrl) or xdotool to let the "
                    "app place a window on the phone display; or drag it past the "
                    f"{vd.get('position', 'right')} edge yourself"}
        if shutil.which("wmctrl"):
            target = ":ACTIVE:" if not title else title
            rc, out, err = _run_cmd(["wmctrl", "-r", target, "-e", f"0,{x},{y},{w},{h}"])
            if rc == 0:
                return {"ok": True, "backend": "wmctrl",
                        "detail": f"moved {target} to +{x}+{y} {w}x{h}"}
        wid = ""
        if shutil.which("xdotool"):
            rc, out, _e = _run_cmd(["xdotool", "search", "--name", title] if title
                                   else ["xdotool", "getactivewindow"])
            wid = (out or "").split("\n")[0].strip()
            if wid:
                _run_cmd(["xdotool", "windowmove", wid, str(x), str(y)])
                _run_cmd(["xdotool", "windowsize", wid, str(w), str(h)])
                return {"ok": True, "backend": "xdotool",
                        "detail": f"moved window {wid} to +{x}+{y} {w}x{h}"}
        return {"ok": False, "detail": "no window matched"}

    def _ss_capture_source(self) -> tuple:
        vd = self._ss_vd or self.virtual_display.status()
        if not (vd or {}).get("ok"):
            return (None, None, None)
        return (vd.get("region"), vd.get("display"), vd.get("output"))

    async def _handle_screenshare(self, conn: dict, frame: dict, data, cmd,
                                  tag: str) -> None:
        raw = (data or {}) if isinstance(data, dict) else {}
        inner = raw.get("data")
        if isinstance(inner, str):
            try:
                inner = json.loads(inner)
            except json.JSONDecodeError:
                inner = {"_raw": inner}
        if not isinstance(inner, dict):
            inner = {}
        sid = raw.get("sessionId", self.ss_session_id) or 0
        try:
            sid = int(sid)
        except (TypeError, ValueError):
            sid = self.ss_session_id

        if cmd == SS_CMD_PHONE_START_REQUEST:
            self.events.log("SCREENSHARE_START_REQUEST_RECV",
                            f"{tag} shareType={inner.get('shareType')} "
                            f"setAudio={inner.get('setAudio')} "
                            f"setScale={inner.get('setScale')} sessionId={sid}",
                            feature="screen_share")
            with contextlib.suppress(TypeError, ValueError):
                self.ss_scale = max(100, int(inner.get("setScale") or self.ss_scale))
            if inner.get("shareType") in ("extend", "mirror"):
                self.ss_share_type = "mirror"
                await self._ensure_ss_virtual_display()
            if inner.get("setAudio") is not None:
                self.ss_audio = bool(inner["setAudio"])
            ok50 = await self.start_screenshare(conn, session_id=sid or None,
                                                origin="phone-cmd50")
            self._notify_ui_feature_started("screenshare", conn=conn,
                                            force_present=True, sid=sid,
                                            origin="phone-cmd50", ok=bool(ok50))
            await self.push_service_status(conn, "screenshare-phone-cmd50")
            return

        if cmd == SS_CMD_PHONE_OFFER:
            offer = str(inner.get("sdpOffer") or "")
            res = inner.get("resolution")
            if isinstance(res, str):
                with contextlib.suppress(json.JSONDecodeError):
                    res = json.loads(res)
            self.ss_stats["offers"] += 1
            if isinstance(res, dict):
                if res.get("width") and res.get("height"):
                    self.phone_resolutions["screenshare"]["phoneScreen"] = {
                        "width": int(res["width"]),
                        "height": int(res["height"]),
                    }
                self.phone_resolutions["screenshare"]["phoneRequested"] = {
                    "width": res.get("widthReq") or res.get("width") or self.ss_res[0],
                    "height": res.get("heightReq") or res.get("height") or self.ss_res[1],
                    "fps": res.get("fps") or self.ss_fps,
                    "maxHeight": res.get("maxHeight"),
                }
            self.events.log("SCREENSHARE_OFFER_RECV",
                            f"{tag} sdpLen={len(offer)} errorCode={inner.get('errorCode')!r} "
                            f"androidVer={inner.get('key_android_ss_version')!r} "
                            f"resolution={res}", feature="screen_share")
            with contextlib.suppress(OSError):
                (self.out_dir / f"screenshare_offer_{sid}.json").write_text(
                    offer, encoding="utf-8")
            if inner.get("errorCode"):
                self.events.log("SCREENSHARE_OFFER_ERROR",
                                f"{tag} errorCode={inner.get('errorCode')} — the phone "
                                f"could not create a PeerConnection", feature="screen_share")
                await self._stop_screenshare(conn, "offer carried an errorCode",
                                             send=False)
                return
            if not offer:
                return
            self._notify_ui_feature_started("screenshare", conn=conn,
                                            force_present=True, sid=sid,
                                            origin="phone-cmd5-offer")
            answer = await self._ss_negotiate(conn, sid, offer, res or {})
            if not answer:
                await self._send_ws_text(
                    conn, _ss_env(self, SS_CMD_PHONE_ERROR,
                                  {"sdpOffer": "", "resolution": "",
                                   "errorCode": "ConnectionFailed"}, sid),
                    "SCREENSHARE_ERROR_SENT(cmd13,no-answer)")
                await self._stop_screenshare(conn, "no aiortc answer", send=False)
                return
            await self._send_ws_text(conn, build_ss_answer(self, sid, answer),
                                     f"SCREENSHARE_ANSWER_SENT(cmd1,len={len(answer)})")
            self.ss_stats["answers"] += 1
            self.ss_stats["state"] = "answered"
            await self._ss_send_local_candidates(conn, sid)
            return

        if cmd == SS_CMD_PHONE_CANDIDATE:
            cand = str(inner.get("candidate") or "")
            self.events.log("SCREENSHARE_CANDIDATE_RECV",
                            f"{tag} len={len(cand)} type={inner.get('messageType')} "
                            f"{cand[:90]}", feature="screen_share")
            if self._ss_pc is not None and cand:
                sig_payload = (cand if cand.lstrip().startswith("{")
                               else json.dumps({"candidate": cand, "id": "0", "label": 0}))
                await self._media_remote_signal(self._ss_pc, "candidate",
                                                sig_payload, tag)
                self.ss_stats["cand_in"] += 1
            return

        if cmd == SS_CMD_PHONE_STOP:
            self.events.log("SCREENSHARE_STOP_RECV",
                            f"{tag} stopFrom={inner.get('stopFrom')} — the phone "
                            f"ended the session", feature="screen_share")
            await self._stop_screenshare(conn, f"phone cmd:52 stopFrom="
                                               f"{inner.get('stopFrom')}", send=False)
            self._session_reset(conn, tag="screenshare stopped by phone")
            return

        if cmd == SS_CMD_PHONE_ICE_FAILURE:
            self.events.log("SCREENSHARE_ICE_FAILURE",
                            f"{tag} payload={inner} — pb/c.I(false) fired "
                            f"L(12,'iceFailure'); our candidates never reached it "
                            f"(check --lan-ip / the firewall / same subnet)",
                            feature="screen_share")
            self.ss_stats["state"] = "ice-failure"
            await self._stop_screenshare(conn, "ice failure", send=False)
            return

        if cmd == SS_CMD_PHONE_ERROR:
            self.events.log("SCREENSHARE_PHONE_ERROR",
                            f"{tag} errorCode={inner.get('errorCode')!r}",
                            feature="screen_share")
            await self._stop_screenshare(conn, "phone cmd:13", send=False)
            return

        if cmd == SS_CMD_PHONE_STARTED:
            self.events.log("SCREENSHARE_STARTED_MSG",
                            f"{tag} data={inner.get('data')!r} — 'pc ss is already "
                            f"started' means the phone still remembers a previous "
                            f"share session: send cmd:26 first (!ss stop)",
                            feature="screen_share")
            return

        if cmd == SS_CMD_PHONE_REJECT:
            self.events.log("SCREENSHARE_REJECT",
                            f"{tag} data={inner.get('data')!r} (folded / lock "
                            f"screen rejection)", feature="screen_share")
            self.ss_stats["state"] = "rejected"
            await self._stop_screenshare(conn, "phone rejected", send=False)
            return

        if cmd == SS_CMD_PHONE_RESOLUTION:
            sw = inner.get("setWidth")
            sh = inner.get("setHeight")
            sa = inner.get("setAuto", 0)
            self.events.log("SCREENSHARE_RESOLUTION_RECV",
                            f"{tag} {sw}x{sh} auto={sa}", feature="screen_share")
            if isinstance(sw, int) and isinstance(sh, int) and sw >= 64 and sh >= 64:
                self.ss_res = (sw, sh)
                self.phone_resolutions["screenshare"]["active"] = {
                    "width": sw, "height": sh, "auto": int(sa or 0),
                }
                if self._ss_track is not None and hasattr(self._ss_track, "set_resolution"):
                    self._ss_track.set_resolution(sw, sh)
                    self.events.log("SCREENSHARE_TRACK_RESIZED",
                                    f"resized live ScreenShareTrack to {sw}x{sh} (phone cmd:70)",
                                    feature="screen_share")
            return

        self.events.log("SCREENSHARE_MSG",
                        f"{tag} cmd={cmd} keys={sorted(inner.keys())} — logged, no reply",
                        feature="screen_share")

    async def start_screenshare(self, conn: dict | None = None, session_id: int | None = None,
                                origin: str = "operator") -> bool:
        c = conn or self._live_ws()
        if c is None:
            self.events.log("SCREENSHARE_START_FAIL", "no live phone connection",
                            feature="screen_share")
            return False
        if self.screenshare == "off":
            self.events.log("SCREENSHARE_START_FAIL", "--screenshare off",
                            feature="screen_share")
            return False
        if session_id is None:
            session_id = int(time.time() * 1000) % 2**31 or 1
        self.ss_session_id = int(session_id)
        if c.get("ss_started"):
            await self._stop_screenshare(c, f"restart ({origin})")
            await asyncio.sleep(0.2)
        await self._exclusive_stop(c, keep="screenshare",
                                   tag=f"before screen share ({origin})")
        self._session_reset(c, tag=f"screenshare {origin}")
        c["session"]["feature"] = "screenshare"
        c["ss_started"] = True
        self.ss_stats["state"] = "connecting"
        if getattr(self, "ss_share_type", "") not in ("extend", "mirror"):
            self.ss_share_type = "mirror"
        await self._ensure_ss_virtual_display()
        if self._ss_wants_virtual():
            _vw, _vh = self._ss_virtual_size()
            if (self._ss_vd or {}).get("ok"):
                _vw = int(self._ss_vd.get("width") or _vw)
                _vh = int(self._ss_vd.get("height") or _vh)
            self.ss_res = (_vw, _vh)
            self.phone_resolutions["screenshare"]["active"] = {
                "width": _vw, "height": _vh, "auto": 0,
            }
        self.events.log("SCREENSHARE_START",
                        f"conn{c['id']:03d} sessionId={self.ss_session_id} "
                        f"shareType={self.ss_share_type} audio={self.ss_audio} "
                        f"scale={self.ss_scale} origin={origin} "
                        f"res={self.ss_res[0]}x{self.ss_res[1]} fps={self.ss_fps} "
                        f"source={self.ss_source} aiortc={HAVE_TRACK}",
                        feature="screen_share")
        ok = await self._send_ws_text(
            c, build_ss_connection_ready(self, self.ss_session_id,
                                         self.ss_share_type, self.ss_audio,
                                         self.ss_scale),
            "SCREENSHARE_CONNECTION_READY_SENT(cmd0)")
        await self._send_ws_text(c, build_ss_version(self, self.ss_session_id),
                                 "SCREENSHARE_VERSION_SENT(cmd37)")
        await self._send_ws_text(
            c, build_ss_resolution(
                self, self.ss_session_id,
                self.ss_res[0], self.ss_res[1], auto=0,
                res_list=self.phone_resolutions["screenshare"]["pcSupportedList"]),
            "SCREENSHARE_RESLIST_SENT(cmd35)")
        return ok

    async def _handle_display_service(self, conn: dict, frame: dict, data,
                                      tag: str) -> None:
        msg_id = frame.get("msgId")
        d = data if isinstance(data, dict) else {}
        if isinstance(d.get("displayInfo"), dict):
            d = d["displayInfo"]
        res_list = d.get("resolutions") if isinstance(d.get("resolutions"), list) else []
        cur_res = d.get("resolution") if isinstance(d.get("resolution"), dict) else {}
        ds = self.phone_resolutions["display_service"]
        ds["queried"] = True
        if d.get("type") is not None:
            ds["type"] = d.get("type")
        if d.get("mode") is not None:
            ds["mode"] = d.get("mode")
        if d.get("supportedMode") is not None:
            ds["supportedMode"] = d.get("supportedMode")
        if d.get("supportOrientation") is not None:
            ds["supportOrientation"] = bool(d.get("supportOrientation"))
        if cur_res.get("width") and cur_res.get("height"):
            ds["current"] = cur_res
            self.desktop_res = (int(cur_res["width"]), int(cur_res["height"]))
        if res_list:
            ds["supported"] = res_list
            self.phone_resolutions["desktop"]["supported"] = res_list
        self.events.log("DISPLAY_INFO_RECV",
                        f"{tag} msgId={msg_id} mode={ds.get('mode')} "
                        f"supportedMode={ds.get('supportedMode')} "
                        f"current={ds.get('current')} "
                        f"supported={len(ds.get('supported') or [])} modes: "
                        f"{ds.get('supported')}", feature="desktop")

    async def stop_screenshare(self, conn: dict | None = None,
                               reason: str = "operator") -> bool:
        c = conn or self._live_ws()
        if c is None:
            return False
        sent = await self._stop_screenshare(c, reason)
        self._session_reset(c, tag="screenshare stopped")
        return sent

    async def _ss_negotiate(self, conn: dict, sid: int, offer_sdp: str,
                            res: dict):
        if getattr(self, "use_desktop_thread", True) and HAVE_MEDIA:
            with contextlib.suppress(RuntimeError):
                cur_l = asyncio.get_running_loop()
                dt_l = getattr(getattr(self, "desktop_service_thread", None), "loop", None)
                if cur_l is not dt_l:
                    self._control_loop = cur_l
                    if isinstance(conn, dict) and conn.get("_control_loop") is None:
                        conn["_control_loop"] = cur_l
            dt = self._ensure_desktop_service_thread()
            if dt is not None and not dt.is_current_loop():
                dt.stats["media_sessions_negotiated"] = int(dt.stats.get("media_sessions_negotiated", 0)) + 1
                return await dt.run_coroutine(self._ss_negotiate(conn, sid, offer_sdp, res))
        if not HAVE_TRACK:
            self.events.log("SCREENSHARE_NEGOTIATE_FAIL",
                            "aiortc/av not installed (pip install aiortc) — cannot "
                            "send the PC screen; the phone will sit on its "
                            "connection timer", feature="screen_share")
            return None
        try:
            off = json.loads(offer_sdp)
        except json.JSONDecodeError:
            off = {"type": "offer", "sdp": offer_sdp}
        try:
            w = int(res.get("widthReq") or res.get("width") or self.ss_res[0])
            h = int(res.get("heightReq") or res.get("height") or self.ss_res[1])
        except (TypeError, ValueError):
            w, h = self.ss_res
        if self._ss_wants_virtual() and self._ss_vd is not None:
            _vw, _vh = self._ss_virtual_size()
            if (self._ss_vd or {}).get("ok"):
                _vw = int(self._ss_vd.get("width") or _vw)
                _vh = int(self._ss_vd.get("height") or _vh)
            w, h = _vw, _vh
            self.ss_res = (w, h)
            self.phone_resolutions["screenshare"]["active"] = {
                "width": w, "height": h, "auto": 0,
            }
        with contextlib.suppress(TypeError, ValueError):
            mh = int(res.get("maxHeight") or 0)
            if mh:
                if self._ss_wants_virtual() and h > w:
                    if w > mh:
                        h = max(64, int(h * mh / w) & ~1)
                        w = mh & ~1
                elif h > mh:
                    w = max(64, int(w * mh / h))
                    h = mh
        w = max(64, min(4096, w))
        h = max(64, min(4096, h))
        eff_fps = float(self.ss_fps)
        with contextlib.suppress(TypeError, ValueError):
            req_fps = float((res or {}).get("fps") or 0)
            if req_fps >= 15.0:
                eff_fps = max(eff_fps, min(60.0, req_fps))
        self.ss_fps = eff_fps
        pc = SamyujPeerConnection(RTCConfiguration(iceServers=[]))
        if getattr(self, "desktop_service_thread", None) is not None and self.desktop_service_thread.is_current_loop():
            _bind_pc_to_media_loop(pc, self.desktop_service_thread)
        self._ss_pc = pc
        _reg, _disp, _out = self._ss_capture_source()
        self._ss_track = _ScreenShareTrack(width=w, height=h, fps=eff_fps,
                                          source=self.ss_source,
                                          region=_reg, display=_disp, output=_out)
        self.ss_stats["backend"] = (
            "probing" if (self._ss_track.backend == "pattern"
                          and self.ss_source in ("auto", "screen"))
            else self._ss_track.backend)
        self.ss_stats["capture"] = ("virtual-display" if (_reg or _disp or _out)
                                    else "primary-monitor")
        self.events.log("SCREENSHARE_SOURCE",
                        f"backend={self._ss_track.backend} {w}x{h}@{self.ss_fps}fps "
                        f"capture={self.ss_stats['capture']} shareType={self.ss_share_type} "
                        f"region={_reg} display={_disp} output={_out} "
                        f"(requested source={self.ss_source})", feature="screen_share")
        try:
            pc.addTrack(self._ss_track)

            @pc.on("connectionstatechange")
            async def _on_state():
                st = pc.connectionState
                self.ss_stats["state"] = str(st)
                self.events.log("SCREENSHARE_MEDIA_STATE", f"sid={sid} state={st}",
                                feature="screen_share")
                if st in ("failed", "closed", "disconnected"):
                    await self._stop_screenshare(conn, f"peerconnection {st}",
                                                 send=False)

            await pc.setRemoteDescription(
                RTCSessionDescription(sdp=off["sdp"], type=off.get("type", "offer")))
            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)
            desc = pc.localDescription
            ans = desc.sdp
            if self.ss_sdp_fix:
                ans, fx = fix_answer_sdp(
                    ans, off.get("sdp", ""), prefer_ip=self._ss_prefer_ip(conn),
                    codec=self.ss_codec, reject_datachannel=self.ss_no_datachannel,
                    reject_audio=self.ss_no_audio)
                for c in dict.fromkeys(fx):
                    self.events.log("SCREENSHARE_SDP_FIXED", c, feature="screen_share")
                if not fx:
                    self.events.log("SCREENSHARE_SDP_FIX_NOOP",
                                    "answer already canonical", feature="screen_share")
            ans_clean, removed = sanitize_answer_sdp(ans)
            if removed:
                self.events.log("SCREENSHARE_ANSWER_SANITIZED",
                                f"removed={removed} non-sending ssrc/msid lines",
                                feature="screen_share")
            with contextlib.suppress(OSError):
                (self.out_dir / f"screenshare_answer_{sid}.sdp").write_text(
                    ans_clean, encoding="utf-8")
            self.events.log("SCREENSHARE_ANSWER_CREATED",
                            f"sid={sid} sdpLen={len(ans_clean)}", feature="screen_share")
            if isinstance(conn, dict):
                conn.setdefault("tasks", []).append(_make_threadsafe_task(
                    _boost_pc_sockets_delayed(self, pc, f"screenshare sid={sid}")))
            return json.dumps({"type": desc.type, "sdp": ans_clean})
        except Exception as exc:
            self.events.log("SCREENSHARE_NEGOTIATE_FAIL", f"sid={sid} {exc!r}",
                            feature="screen_share")
            with contextlib.suppress(Exception):
                await pc.close()
            self._ss_pc = None
            return None

    def _ss_prefer_ip(self, conn: dict) -> str:
        if not self.ss_prefer_lan_ip:
            return ""
        is_usb = self._is_usb_conn(conn)
        if is_usb:
            if self.usb_link and len(self.usb_link) >= 2 and self.usb_link[1]:
                return str(self.usb_link[1])
            clip = self._conn_local_ip(conn, is_usb=True)
            if clip and "." in clip and not clip.startswith("127."):
                return clip
        peer = self._phone_ip(conn)
        if peer and "." in peer:
            p24 = peer.rsplit(".", 1)[0]
            pool = list(self._all_ipv4s())
            if self.usb_link and len(self.usb_link) >= 2 and self.usb_link[1] not in pool:
                pool.insert(0, str(self.usb_link[1]))
            for cand in pool:
                if cand.rsplit(".", 1)[0] == p24:
                    return cand
        lip = str(getattr(self, "lan_ip", "") or "")
        return lip if "." in lip and not lip.startswith("127.") else ""

    async def _ss_send_local_candidates(self, conn: dict, sid: int) -> None:
        pc = self._ss_pc
        if pc is None or pc.localDescription is None:
            return
        ordered = _order_sdp_candidate_lines(
            pc.localDescription.sdp,
            prefer_ip=self._ss_prefer_ip(conn),
            prefer_usb=self._is_usb_conn(conn),
        )
        seen = set(ordered)
        for cand in ordered:
            await self._send_ws_text(
                conn, build_ss_candidate(self, sid, cand),
                "SCREENSHARE_CANDIDATE_SENT(cmd2)")
            self.ss_stats["cand_out"] += 1
        self.events.log("SCREENSHARE_CANDIDATES_TRICKLED",
                        f"sid={sid} {len(seen)} unique candidate(s) "
                        f"(de-duplicated across m-sections)", feature="screen_share")

from .base import SamyujService

class ScreenShareService(SamyujService):
    service_id = 202
    id = "screenshare"
    name = "Screen Share Service (PC -> Phone)"
    description = "svc-202 R4SS WebRTC sender with JSON-wrapped IceCandidate fix"

    def actions(self) -> list[dict]:
        return [
            {"name": "start", "label": "Start Extend (cmd:0)", "description": "Start PC-to-phone screen share", "default_params": {"shareType": "extend"}},
            {"name": "start_mirror", "label": "Start Mirror (cmd:0)", "description": "Start mirror screen share", "default_params": {"shareType": "mirror"}},
            {"name": "switch_mode", "label": "Switch Mode Live (cmd:14)", "description": "Switch running screen share between extend and mirror", "default_params": {"shareType": "mirror", "scale": 200}},
            {"name": "set_audio", "label": "Toggle Audio (cmd:18)", "description": "Enable/disable screen share audio routing", "default_params": {"on": True}},
            {"name": "stop", "label": "Stop Screen Share (cmd:26)", "description": "Send svc-202 cmd:26 to clear the phone's remembered screen-share state"},
        ]

    def get_state(self) -> dict:
        return {**super().get_state(), **dict(getattr(self.h, "ss_stats", {}) or {})}

    async def invoke(self, action: str, params: dict | None = None,
                     conn: dict | None = None) -> dict:
        params = params or {}
        c = conn or self.h._live_ws()
        if action in ("start", "start_mirror"):
            if action == "start_mirror":
                self.h.ss_share_type = "mirror"
            elif params.get("shareType") in ("extend", "mirror"):
                self.h.ss_share_type = str(params["shareType"])
            if params.get("scale"):
                self.h.ss_scale = max(100, int(params["scale"]))
            if params.get("source") in ("auto", "screen", "pattern"):
                self.h.ss_source = str(params["source"])
            return await self.h.request("screenshare", c)
        if action == "stop":
            ok = await self.h.stop_screenshare(c, "service-api")
            return {"ok": bool(ok), "detail": "svc-202 cmd:26 sent"}
        if action == "switch_mode":
            mode = str(params.get("shareType") or "mirror")
            scale = max(100, int(params.get("scale") or self.h.ss_scale))
            self.h.ss_share_type = mode
            self.h.ss_scale = scale
            return await self.h.request(f"ss-mode:{mode}", c)
        if action == "set_audio":
            on = bool(params.get("on", True))
            self.h.ss_audio = on
            return await self.h.request(f"ss-audio:{'on' if on else 'off'}", c)
        if action == "set_resolution":
            res = str(params.get("res") or "1280x720")
            return await self.h.request(f"ss-res:{res}", c)
        return await super().invoke(action, params, conn)
