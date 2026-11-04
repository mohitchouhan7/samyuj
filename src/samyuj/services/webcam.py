from __future__ import annotations
from samyuj.protocol.envelope import _env
from samyuj.protocol.sdp import order_sdp_candidate_lines as _order_sdp_candidate_lines
HAVE_MEDIA = True
import ssl

import asyncio
import contextlib
import json
import threading
import time

from samyuj.media.frame_hub import apply_uv_swap_state
from samyuj.media.v4l2 import (
    V4L2LoopbackWriter,
    check_v4l2loopback_module,
    diagnose_v4l2_setup,
    discover_v4l2_devices,
    install_v4l2loopback_package,
    is_v4l2_udev_hotplug_ready,
    is_v4l2loopback_exclusive_caps_ok,
    load_v4l2loopback_module,
)
from samyuj.protocol.constants import (
    WEBCAM_ERR_ADMIN_DISABLED,
    WEBCAM_ERR_CODEC_LACK,
    WEBCAM_ERR_IN_USE,
    WEBCAM_ERR_MULTI_USER,
    WEBCAM_ERR_PC_DRIVER_MISSING,
)
from samyuj.protocol.services import (
    build_webcam_keepalive,
    build_webcam_start,
    build_webcam_stop,
    build_webcam_trigger,
    build_webcam_webrtc_signal,
)

_WC_ERR = {
    WEBCAM_ERR_IN_USE: "camera in use on phone",
    WEBCAM_ERR_ADMIN_DISABLED: "camera disabled by administrator",
    WEBCAM_ERR_MULTI_USER: "camera unavailable in secondary user profile",
    WEBCAM_ERR_CODEC_LACK: "hardware video encoder busy on phone",
    WEBCAM_ERR_PC_DRIVER_MISSING: "PC webcam driver missing",
}

class WebcamServiceController:

    def __init__(
        self,
        harness,
        v4l2_enabled: bool = False,
        v4l2_target: str = "auto",
        v4l2_format: str = "YU12",
        v4l2_fps: float = 30.0,
        v4l2_auto: bool = True,
    ) -> None:
        self.h = harness
        self.v4l2_enabled = bool(v4l2_enabled)
        self.v4l2_auto = bool(v4l2_auto)
        self.v4l2_user_stopped = False
        self._autoload_attempted = False
        self._setup_lock = threading.RLock()
        self.last_setup_result: dict = {}
        self.v4l2_writer = V4L2LoopbackWriter(
            target_spec=v4l2_target,
            pix_fmt_name=v4l2_format,
            fps_cap=v4l2_fps,
        )
        if self.v4l2_enabled:
            self.v4l2_writer.start()

    def _log(self, event: str, detail: str) -> None:
        if getattr(self.h, "events", None):
            self.h.events.log(event, detail, feature="webcam", service_id=3)

    def on_webcam_frame(self, frame, uv_swapped: bool | None = None) -> bool:
        if frame is not None:
            want = bool(getattr(self.h, "uv_swap", True))
            if uv_swapped is None or bool(uv_swapped) != want:
                with contextlib.suppress(Exception):
                    frame, _ = apply_uv_swap_state(frame, want)
        if not self.v4l2_enabled or self.v4l2_writer.state != "running":
            if (frame is not None and self.v4l2_auto
                    and not self.v4l2_user_stopped):
                self._maybe_rearm_throttled()
            return False
        return self.v4l2_writer.submit_frame(frame)

    def _maybe_rearm_throttled(self) -> None:
        now = time.monotonic()
        if now - float(getattr(self, "_last_rearm_mono", 0.0)) < 10.0:
            return
        if getattr(self, "_rearm_inflight", False):
            return
        self._last_rearm_mono = now
        self._rearm_inflight = True

        def _rearm() -> None:
            try:
                self.arm_for_stream(interactive=False)
            except Exception:
                pass
            finally:
                self._rearm_inflight = False

        threading.Thread(target=_rearm, name="sc-v4l2-rearm", daemon=True).start()

    def ensure_startup_ready(self, interactive: bool = False) -> dict:
        if not interactive:
            acquired = self._setup_lock.acquire(blocking=False)
            if not acquired:
                return self.v4l2_writer.status_dict()
        else:
            self._setup_lock.acquire()
        try:
            if not self.v4l2_auto or self.v4l2_user_stopped:
                return self.v4l2_writer.status_dict()
            is_selftest = getattr(self.h, "device_name", "") == "SELFTEST-PC"
            is_sink = str(self.v4l2_writer.target_spec).startswith("sink:")
            if is_selftest or is_sink:
                return self.v4l2_writer.status_dict()
            excl_ok = is_v4l2loopback_exclusive_caps_ok()
            if self.v4l2_writer.state == "running":
                if excl_ok or not interactive or self._autoload_attempted:
                    return self.v4l2_writer.status_dict()
                self.v4l2_writer.stop()
            diag_pre = diagnose_v4l2_setup(self.v4l2_writer.target_spec)
            if diag_pre.resolved_device is not None and excl_ok:
                return self.start_v4l2()
            mod = check_v4l2loopback_module()
            if mod["installed"]:
                return self.arm_for_stream(interactive=interactive)
            if interactive and not self._autoload_attempted:
                self._autoload_attempted = True
                res = install_v4l2loopback_package(interactive=True)
                self.last_setup_result = res
                self._log("V4L2_PKG_INSTALL", f"result={res}")
                if res.get("ok") or check_v4l2loopback_module()["installed"]:
                    if not check_v4l2loopback_module()["loaded"] or not is_v4l2loopback_exclusive_caps_ok() or not is_v4l2_udev_hotplug_ready():
                        self._autoload_attempted = False
                    return self.arm_for_stream(interactive=interactive)
            return self.v4l2_writer.status_dict()
        finally:
            self._setup_lock.release()

    def arm_for_stream(self, interactive: bool = True) -> dict:
        self.v4l2_user_stopped = False
        self.v4l2_enabled = True
        if not interactive:
            acquired = self._setup_lock.acquire(blocking=False)
            if not acquired:
                return self.v4l2_writer.status_dict()
        else:
            self._setup_lock.acquire()
        try:
            is_selftest = getattr(self.h, "device_name", "") == "SELFTEST-PC"
            is_sink = str(self.v4l2_writer.target_spec).startswith("sink:")
            excl_ok = is_sink or is_v4l2loopback_exclusive_caps_ok()
            if self.v4l2_writer.state == "running":
                if excl_ok or not interactive or is_selftest or self._autoload_attempted:
                    if not is_sink:
                        with contextlib.suppress(Exception):
                            self.v4l2_writer._notify_v4l2_consumers_ready()
                    return self.v4l2_writer.status_dict()
                self.v4l2_writer.stop()
            diag_pre = diagnose_v4l2_setup(self.v4l2_writer.target_spec)
            if diag_pre.resolved_device is not None and excl_ok:
                st = self.start_v4l2()
                if st.get("state") == "running":
                    if not is_sink:
                        with contextlib.suppress(Exception):
                            self.v4l2_writer._notify_v4l2_consumers_ready()
                    self._log("V4L2_AUTO_ARMED",
                              f"virtual camera live on {st.get('resolved_device')} — "
                              f"select 'Samyuj Webcam' in Firefox/Zoom/OBS")
                    return st
            if not is_sink and (self.v4l2_writer.state == "running" or self.v4l2_writer._fd is not None):
                self.v4l2_writer.stop()
            do_interactive = bool(interactive and not is_selftest and not self._autoload_attempted)
            if do_interactive:
                self._autoload_attempted = True
            res = load_v4l2loopback_module(interactive=do_interactive)
            self.last_setup_result = res
            self._log("V4L2_AUTOLOAD", f"result={res}")
            st = self.start_v4l2()
            if st.get("state") == "running":
                if not is_sink:
                    with contextlib.suppress(Exception):
                        self.v4l2_writer._notify_v4l2_consumers_ready()
                self._log("V4L2_AUTO_ARMED",
                          f"virtual camera live on {st.get('resolved_device')} — "
                          f"select 'Samyuj Webcam' in Firefox/Zoom/OBS")
            else:
                diag = st.get("diagnosis") or {}
                self._log("V4L2_AUTO_ARM_PREVIEW_ONLY",
                          f"{diag.get('user_message', st.get('last_error', ''))} "
                          f"(hint: {diag.get('setup_command_hint', '')})")
            return st
        finally:
            self._setup_lock.release()

    def explicit_setup(self, interactive: bool = True) -> dict:
        with self._setup_lock:
            is_sink = str(self.v4l2_writer.target_spec).startswith("sink:")
            if not is_sink and (self.v4l2_writer.state == "running" or self.v4l2_writer._fd is not None):
                self.v4l2_writer.stop()
            res = load_v4l2loopback_module(interactive=interactive)
            self.last_setup_result = res
            self._log("V4L2_EXPLICIT_SETUP", f"result={res}")
            self.v4l2_user_stopped = False
            st = self.start_v4l2()
            if self.v4l2_writer.state == "running":
                with contextlib.suppress(Exception):
                    self.v4l2_writer._notify_v4l2_consumers_ready()
            return {"setup": res, **st}

    def start_v4l2(
        self,
        target_spec: str | None = None,
        pix_fmt_name: str | None = None,
    ) -> dict:
        self.v4l2_enabled = True
        self.v4l2_user_stopped = False
        st = self.v4l2_writer.start(target_spec=target_spec, pix_fmt_name=pix_fmt_name)
        if getattr(self.h, "events", None):
            self.h.events.log(
                "V4L2_WRITER_START",
                f"state={st['state']} target={st['target_spec']} resolved={st['resolved_device']} fmt={st['pix_fmt']}",
                feature="webcam",
                service_id=3,
            )
        return st

    def standby_v4l2(self) -> dict:
        if self.v4l2_writer.state == "running":
            st = self.v4l2_writer.clear_to_standby()
            self._log("V4L2_WRITER_STANDBY",
                      f"reset {st.get('resolved_device')} to standby keepalive (camera remains visible to apps)")
            return st
        return self.v4l2_writer.status_dict()

    def stop_v4l2(self) -> dict:
        self.v4l2_enabled = False
        self.v4l2_user_stopped = True
        st = self.v4l2_writer.stop()
        if getattr(self.h, "events", None):
            self.h.events.log(
                "V4L2_WRITER_STOP",
                "stopped V4L2 output (WebRTC webcam preview remains untouched)",
                feature="webcam",
                service_id=3,
            )
        return st

    def status(self) -> dict:
        st = self.v4l2_writer.status_dict()
        st["v4l2_enabled"] = self.v4l2_enabled
        st["v4l2_auto"] = self.v4l2_auto
        st["uv_swap"] = bool(getattr(self.h, "uv_swap", True))
        st["devices"] = [d.to_dict() for d in discover_v4l2_devices()]
        return st

class WebcamServiceMixin:

    async def _handle_webcam(self, conn: dict, frame: dict, data, cmd,
                             tag: str) -> None:
        if isinstance(data, str) and data.lstrip().startswith("{"):
            with contextlib.suppress(Exception):
                _p = json.loads(data)
                if isinstance(_p, dict):
                    data = _p
        d = data if isinstance(data, dict) else {}
        if cmd is None:
            cmd = d.get("cmd")
        raw_inner = d.get("data")
        if isinstance(raw_inner, str) and raw_inner.lstrip().startswith(("{", "[")):
            with contextlib.suppress(Exception):
                raw_inner = json.loads(raw_inner)
        inner = raw_inner if isinstance(raw_inner, dict) else {}
        if cmd == 0:
            is_reply = inner.get("isReply")
            self.events.log("WEBCAM_HANDSHAKE_RECV",
                            f"{tag} isReply={is_reply} heartbeat={inner.get('heartbeat')} "
                            f"supportedWebRTC={inner.get('supportedWebRTC')} "
                            f"port={inner.get('port')}", feature="webcam")
            if is_reply == 0 and not conn.get("wc_trigger_answered"):
                conn["wc_trigger_answered"] = True
                await self._send_ws_text(conn, self._wc_accept_json(),
                                         "WEBCAM_ACCEPT_SENT(cmd0,isReply:1)")
                conn["session"]["feature"] = "webcam"
                conn["wc_started"] = True
                self.webcam_stats["state"] = "phone-triggered"
                await self._wc_keepalive_start(conn)
                if self.webcam_controller.v4l2_auto:
                    with contextlib.suppress(Exception):
                        await asyncio.to_thread(self.webcam_controller.arm_for_stream, False)
                self._notify_ui_feature_started("webcam", conn=conn,
                                                force_present=True,
                                                origin="phone-cmd0")
                await self.push_service_status(conn, "webcam-phone-trigger")
            elif is_reply == 1:
                conn["wc_started"] = True
                conn["session"]["feature"] = "webcam"
                reg = self._ensure_session_registry(conn)
                reg.get("webcam").bump("starting")
                self._sync_legacy_session_feature(conn)
                self.webcam_sid = inner.get("sessionId") or self.webcam_sid
                if not conn.get("wc_start_sent"):
                    conn["wc_start_sent"] = True
                    await self._send_ws_text(
                        conn, build_webcam_start(self, self.webcam_port),
                        f"WEBCAM_START_SENT(cmd1,port={self.webcam_port})")
                    self.webcam_stats["state"] = "starting"
                    await self._wc_keepalive_start(conn)
                await self.push_service_status(conn, "webcam-accepted")
            return
        if cmd == 1 or (cmd == 12 and inner.get("webRTCData")):
            offer = inner.get("webRTCData")
            self.webcam_stats["offers"] += 1
            self.events.log("WEBCAM_OFFER_RECV",
                            f"{tag} cameraId={inner.get('cameraId')} "
                            f"isWebRTC={inner.get('isWebRTC')} "
                            f"mediaFormat={inner.get('mediaFormat')} "
                            f"{inner.get('w')}x{inner.get('h')} "
                            f"sessionId={inner.get('sessionId')} "
                            f"offerLen={len(str(offer or ''))}", feature="webcam")
            conn.pop("_wc_code9_retried", None)
            conn["wc_started"] = True
            conn["session"]["feature"] = "webcam"
            reg = self._ensure_session_registry(conn)
            reg.get("webcam").status = "live"
            self._sync_legacy_session_feature(conn)
            if inner.get("w") and inner.get("h"):
                self.phone_resolutions["webcam"]["nativeStream"] = {
                    "width": int(inner["w"]),
                    "height": int(inner["h"]),
                    "fps": 30,
                }
            if inner.get("cameraId") is not None:
                self.phone_resolutions["webcam"]["activeCameraId"] = int(inner["cameraId"])
            if inner.get("sessionId") is not None:
                self.webcam_sid = inner.get("sessionId")
            elif self.webcam_sid is None:
                self.webcam_sid = 0
            self._notify_ui_feature_started("webcam", conn=conn,
                                            force_present=True,
                                            origin="webcam-offer")
            if not offer:
                return
            with contextlib.suppress(OSError):
                (self.out_dir / f"webcam_offer_{self.webcam_sid}.json").write_text(
                    str(offer), encoding="utf-8")
            if not (self.media and HAVE_MEDIA):
                self.events.log("WEBCAM_OFFER_UNANSWERED",
                                f"{tag} --media/aiortc not available: offer saved to "
                                f"webcam_offer_{self.webcam_sid}.json but not answered",
                                feature="webcam")
                return
            await self._wc_keepalive_start(conn)
            if self.webcam_controller.v4l2_auto and self.webcam_controller.v4l2_writer.state != "running":
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(self.webcam_controller.arm_for_stream, False)
            answer = await self._media_negotiate(conn, f"wc{self.webcam_sid}",
                                                 str(offer))
            if not answer:
                return
            await self._send_ws_text(
                conn, build_webcam_webrtc_signal(self, self.webcam_sid, "answer",
                                                 answer),
                "WEBCAM_ANSWER_SENT(cmd17)")
            self.webcam_stats["answers"] += 1
            self.webcam_stats["state"] = "answered"
            pc = self._pcs.get(f"wc{self.webcam_sid}")
            if pc is not None:
                await self._wc_send_local_candidates(conn, pc)
            await self.push_service_status(conn, "webcam-answered")
            return
        if cmd == 2:
            self.events.log("WEBCAM_STOP_RECV",
                            f"{tag} the phone ended the webcam session",
                            feature="webcam")
            await self._stop_webcam(conn, "phone cmd:2", send=False)
            return
        if cmd == 3:
            cams = raw_inner if isinstance(raw_inner, list) else (inner or d)
            self.webcam_cameras = cams
            if isinstance(cams, list):
                self.phone_resolutions["webcam"]["cameras"] = cams
            self.events.log("WEBCAM_CAMERAS_RECV",
                            f"{tag} {str(self.webcam_cameras)[:240]}", feature="webcam")
            return
        if cmd == 5:
            wc = self.phone_resolutions["webcam"]
            if inner.get("cameraId") is not None:
                wc["activeCameraId"] = inner.get("cameraId")
            for k in ("flash", "zoom", "angle", "mute", "tracking"):
                if isinstance(inner.get(k), dict):
                    wc[k] = inner[k]
            self.events.log("WEBCAM_CAPS_RECV",
                            f"{tag} cameraId={wc.get('activeCameraId')} "
                            f"flash={wc.get('flash')} zoom={wc.get('zoom')} "
                            f"tracking={wc.get('tracking')}", feature="webcam")
            return
        if cmd == 13:
            self.events.log("WEBCAM_KEEPALIVE_RECV", tag, feature="webcam")
            return
        if cmd == 18:
            ctype = inner.get("type")
            cdata = str(inner.get("webRTCData") or inner.get("data") or "")
            self.events.log("WEBCAM_CANDIDATE_RECV",
                            f"{tag} type={ctype} len={len(cdata)}", feature="webcam")
            pc = self._pcs.get(f"wc{self.webcam_sid}")
            if pc is None:
                pc = next((v for k, v in self._pcs.items() if str(k).startswith("wc")), None)
            if pc is not None and cdata:
                await self._media_remote_signal(pc, ctype or "candidate", cdata, tag)
                self.webcam_stats["cand_in"] += 1
            return
        if cmd == 65535:
            code = inner.get("code", inner.get("cmd"))
            self.webcam_stats["error"] = code
            self.webcam_stats["state"] = "error"
            self.events.log("WEBCAM_ERROR_RECV",
                            f"{tag} code={code} "
                            f"({_WC_ERR.get(code, 'unknown')})", feature="webcam")
            if code == 9 and not conn.get("_wc_code9_retried"):
                conn["_wc_code9_retried"] = True
                self.events.log("WEBCAM_CODEC_LACK_RETRY",
                                f"{tag} code=9 (codec lack) — stopping desktop/appstream and retrying cmd:0",
                                feature="webcam")
                await self._stop_appstream(conn, "webcam-code9-recovery")
                await self._stop_desktop(conn, "webcam-code9-recovery")
                if conn.get("writer") is not None:
                    await asyncio.sleep(0.55)
                conn["wc_trigger_answered"] = False
                conn["wc_start_sent"] = False
                conn["wc_started"] = True
                self.webcam_stats.update({"state": "triggered", "error": None})
                await self._send_ws_text(conn, build_webcam_trigger(self),
                                         "WEBCAM_TRIGGER_SENT(cmd0,code9-retry)")
                return
            await self._stop_webcam(conn, f"phone error {code}", send=False)
            return
        self.events.log("WEBCAM_MSG", f"{tag} cmd={cmd} keys={sorted(d.keys())} "
                                      f"— logged, no reply", feature="webcam")

    def _wc_accept_json(self) -> str:
        data = {"cmd": 0, "ver": "1",
                "data": {"port": int(self.webcam_port or self.port), "isReply": 1,
                         "supportedWebRTC": 1, "heartbeat": 1,
                         "supportedMediaFormat": 1}}
        return json.dumps(_env(self, self.next_msg_id(), self.device_id_b64,
                               3, 3, data),
                          separators=(",", ":"), ensure_ascii=False)

    async def _wc_keepalive_start(self, conn: dict) -> None:
        if self._wc_hb_task is not None and not self._wc_hb_task.done():
            return
        self._wc_hb_task = asyncio.create_task(self._wc_keepalive(conn))
        conn["tasks"].append(self._wc_hb_task)

    async def _wc_keepalive(self, conn: dict, interval: float = 10.0) -> None:
        n = 0
        try:
            while not conn.get("closed") and conn.get("wc_started"):
                await asyncio.sleep(interval)
                if conn.get("closed") or not conn.get("wc_started"):
                    break
                n += 1
                await self._send_ws_text(conn, build_webcam_keepalive(self),
                                         f"WEBCAM_KEEPALIVE_SENT(cmd13,#{n})")
        except (ConnectionError, ssl.SSLError, OSError, asyncio.CancelledError):
            pass

    async def _wc_send_local_candidates(self, conn: dict, pc) -> None:
        if pc.localDescription is None:
            return
        ordered = _order_sdp_candidate_lines(
            pc.localDescription.sdp,
            prefer_ip=self._ss_prefer_ip(conn),
            prefer_usb=self._is_usb_conn(conn),
        )
        for raw in ordered:
            await self._send_ws_text(
                conn, build_webcam_webrtc_signal(
                    self, self.webcam_sid, "candidate",
                    json.dumps({"type": "candidate", "label": 0, "id": "0",
                                "candidate": raw}, separators=(",", ":"))),
                "WEBCAM_CANDIDATE_SENT(cmd18)")
            self.webcam_stats["cand_out"] += 1

    async def start_webcam(self, conn: dict | None = None,
                           origin: str = "operator") -> bool:
        c = conn or self._live_ws()
        if c is None:
            self.events.log("WEBCAM_START_FAIL", "no live phone connection",
                            feature="webcam")
            return False
        if self.webcam == "off":
            self.events.log("WEBCAM_START_FAIL", "--webcam off", feature="webcam")
            return False
        c.pop("_wc_code9_retried", None)
        await self._exclusive_stop(c, keep="webcam", tag=f"before webcam ({origin})")
        self.reset_webcam_session(c, tag=f"webcam {origin}")
        c["wc_trigger_answered"] = False
        c["wc_start_sent"] = False
        c["wc_started"] = True
        reg = self._ensure_session_registry(c)
        reg.get("webcam").bump("starting")
        self._sync_legacy_session_feature(c)
        self.webcam_stats.update({"state": "triggered", "error": None})
        if self.webcam_controller.v4l2_auto:
            try:
                await asyncio.to_thread(self.webcam_controller.arm_for_stream, False)
            except Exception as exc:
                self.events.log("V4L2_ARM_FAIL", repr(exc), feature="webcam")
        self.events.log("WEBCAM_START",
                        f"conn{c['id']:03d} cmd:0 trigger (no isReply) port="
                        f"{self.webcam_port} origin={origin} media={self.media} "
                        f"aiortc={HAVE_MEDIA} — accept the dialog on the phone",
                        feature="webcam")
        return await self._send_ws_text(c, build_webcam_trigger(self),
                                        "WEBCAM_TRIGGER_SENT(cmd0)")

    async def _stop_webcam(self, conn: dict, reason: str = "",
                           send: bool = True) -> bool:
        was = bool(conn.get("wc_started"))
        conn["wc_started"] = False
        conn.pop("wc_start_sent", None)
        conn.pop("wc_trigger_answered", None)
        reg = self._ensure_session_registry(conn)
        reg.get("webcam").status = "idle"
        self._sync_legacy_session_feature(conn)
        if self._wc_hb_task is not None:
            self._wc_hb_task.cancel()
            self._wc_hb_task = None
        key = f"wc{self.webcam_sid}"
        pc = self._pcs.pop(key, None)
        if pc is not None:
            await self._close_pc_safe(pc)
        if getattr(self, "stream_registry", None) is not None:
            self.stream_registry.mark_stopped(key)
            self.stream_registry.mark_stopped("webcam")
        if getattr(self, "webcam_controller", None) is not None and self.webcam_controller.v4l2_auto and not self.webcam_controller.v4l2_user_stopped:
            with contextlib.suppress(Exception):
                self.webcam_controller.standby_v4l2()
        self.webcam_stats["state"] = "idle"
        if not was:
            return False
        if send and not conn.get("closed") and conn.get("ws"):
            await self._send_ws_text(conn, build_webcam_stop(self),
                                     f"WEBCAM_STOP_SENT(cmd2,{reason or 'explicit'})")
        return True

    async def stop_webcam(self, conn: dict | None = None,
                          reason: str = "operator") -> bool:
        c = conn or self._live_ws()
        if c is None:
            return False
        sent = await self._stop_webcam(c, reason)
        self.reset_webcam_session(c, tag="webcam stopped")
        return sent

from .base import SamyujService

class WebcamService(SamyujService):
    service_id = 3
    id = "webcam"
    name = "Webcam Streaming Service (Phone -> PC)"
    description = "svc-3 WebRTC camera receiver with UV chroma swap ON by default, camera toggle (cmd:4), flash (cmd:7), zoom (cmd:9) & output rescaling"

    def actions(self) -> list[dict]:
        return [
            {"name": "start", "label": "Start Webcam (cmd:0)", "description": "Trigger phone webcam session"},
            {"name": "stop", "label": "Stop Webcam (cmd:2)", "description": "Stop phone webcam session"},
            {"name": "toggle_uv_swap", "label": "Toggle UV Swap", "description": "Toggle U/V chroma plane swap (default ON)"},
            {"name": "query_cameras", "label": "Query Cameras (cmd:3)", "description": "Request phone camera list (svc-3 cmd:3)"},
            {"name": "toggle_camera", "label": "Toggle Front/Back (cmd:4)", "description": "Flip phone camera via peer service handler cmd:4"},
            {"name": "toggle_flash", "label": "Toggle Flash/Torch (cmd:7)", "description": "Toggle phone camera flash via peer service handler cmd:7"},
            {"name": "set_zoom", "label": "Set Zoom Level (cmd:9)", "description": "Set phone camera zoom level (0..max)", "default_params": {"level": 1}},
            {"name": "set_output_res", "label": "Set PC Output Resolution", "description": "Rescale received webcam stream on PC (e.g. 1920x1080, 1280x720, 640x480)", "default_params": {"res": "1280x720"}},
            {"name": "pause", "label": "Pause Webcam (cmd:11)", "description": "Send svc-3 cmd:11 pause"},
            {"name": "resume", "label": "Resume Webcam (cmd:12)", "description": "Send svc-3 cmd:12 resume"},
        ]

    def get_state(self) -> dict:
        return {
            **super().get_state(),
            "uv_swap": bool(getattr(self.h, "uv_swap", True)),
            "output_res": list(self.h.webcam_output_res) if getattr(self.h, "webcam_output_res", None) else None,
            "capabilities": dict((getattr(self.h, "phone_resolutions", {}) or {}).get("webcam", {})),
            **dict(getattr(self.h, "webcam_stats", {}) or {}),
        }

    async def invoke(self, action: str, params: dict | None = None,
                     conn: dict | None = None) -> dict:
        params = params or {}
        if action == "start":
            return await self.h.request("webcam", conn)
        if action == "stop":
            return await self.h.request("webcam-stop", conn)
        if action == "toggle_uv_swap":
            if "on" in params:
                self.h.set_uv_swap(bool(params["on"]))
            else:
                self.h.set_uv_swap(None)
            return {"ok": True, "uv_swap": self.h.uv_swap, "detail": f"uv_swap={self.h.uv_swap} (persisted)"}
        if action == "query_cameras":
            return await self.h.request("webcam-cameras", conn)
        if action == "toggle_camera":
            return await self.h.request("webcam-flip", conn)
        if action == "switch_back":
            return await self.h.request("webcam-switch:0", conn)
        if action == "switch_front":
            return await self.h.request("webcam-switch:1", conn)
        if action == "toggle_flash":
            return await self.h.request("webcam-flash", conn)
        if action == "set_zoom":
            lvl = int(params.get("level", 0))
            return await self.h.request(f"webcam-zoom:{lvl}", conn)
        if action == "set_output_res":
            res = str(params.get("res") or "1280x720").strip()
            return await self.h.request(f"webcam-res:{res}", conn)
        if action == "pause":
            return await self.h.request("webcam-pause", conn)
        if action == "resume":
            return await self.h.request("webcam-resume", conn)
        return await super().invoke(action, params, conn)

WebcamFeatureService = WebcamService
