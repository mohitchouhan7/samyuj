from __future__ import annotations
import datetime
import re
from samyuj.core.tasks import _make_threadsafe_task
from samyuj.media.frame_hub import MediaStreamRegistry, get_prepared_frame_bgrx
from samyuj.media.remb import (
    _REMB_FLOOR_BPS,
    _REMB_LOSS_HIGH_PCT,
    _REMB_LOSS_LOW_PCT,
    _REMB_RECOVER_SAMPLES,
    adaptive_remb_floor_bps as _adaptive_remb_floor_bps,
)
from samyuj.protocol.sdp import prefer_candidate_ip as _prefer_candidate_ip
from samyuj.protocol.services import build_ss_stop

import asyncio
import contextlib
import json
import time

try:
    from aiortc import RTCConfiguration, RTCSessionDescription
    from aiortc.sdp import candidate_from_sdp
    HAVE_MEDIA = True
except Exception:
    HAVE_MEDIA = False

from samyuj.media.frame_hub import (
    is_uv_swapped,
    mark_uv_swapped as _mark_uv_swapped,
    swap_uv,
)
from samyuj.media.peer import boost_pc_sockets_delayed as _boost_pc_sockets_delayed
from samyuj.media.webrtc_adapter import SamyujPeerConnection
from samyuj.media.present import (
    is_decoder_gate_active,
    request_h264_keyframe,
    set_h264_keyframe_hook,
    set_presenter_bridge,
)
from samyuj.media.remb import note_link_loss_for_remb
from samyuj.protocol.sdp import (
    order_sdp_candidate_lines as _order_sdp_candidate_lines,
    prefer_codec_in_answer,
    sanitize_answer_sdp,
)
from samyuj.protocol.services import (
    build_appstream_webrtc_signal,
)

from samyuj.core.tasks import _bind_pc_to_media_loop

class MediaPumpServiceMixin:

    def _install_h264_keyframe_hook(self) -> None:
        if getattr(self, "_h264_hook_installed", False):
            return
        self._h264_hook_installed = True
        dt = self._ensure_desktop_service_thread()
        loop = dt.loop if (dt is not None and dt.loop is not None and dt.is_alive()) else None
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                self._h264_hook_installed = False
                return

        def _hook():
            with contextlib.suppress(Exception):
                loop.call_soon_threadsafe(
                    lambda: asyncio.ensure_future(
                        self.request_stream_pli(force=True)))

        set_h264_keyframe_hook(_hook)

    def _desktop_stream_has_video(self) -> bool:
        reg = getattr(self, "stream_registry", None)
        if reg is None:
            return False
        with contextlib.suppress(Exception):
            for rec in list(getattr(reg, "streams", {}).values()):
                if (getattr(rec, "owner_service", "") == "desktop"
                        and getattr(rec, "direction", "") == "inbound"
                        and int(getattr(rec, "frames", 0) or 0) > 0):
                    return True
        return False

    async def _connect_heal(self, c: dict, delay: float = 0.6) -> None:
        await asyncio.sleep(max(0.0, float(delay)))
        if c.get("closed"):
            return
        if (c.get("desktop_started") or c.get("desktop_pending")
                or c.get("ss_started") or c.get("as_session_id") is not None
                or (c.get("as_sessions") or {})):
            self.events.log("CONNECT_HEAL_SKIPPED",
                            "phone-initiated session already in flight",
                            feature="gui")
            return
        sent = []
        with contextlib.suppress(Exception):
            if await self._stop_desktop(c, "heal-on-connect", send=True):
                sent.append("svc1-cmd5")
        with contextlib.suppress(Exception):
            if c.get("ws") and not c.get("closed"):
                await self._send_ws_text(c, build_ss_stop(self, self.ss_session_id),
                                         "CONNECT_HEAL_SS(cmd26)")
                sent.append("svc202-cmd26")
        self.events.log("CONNECT_HEAL_SENT",
                        (",".join(sent) if sent else "nothing-to-send")
                        + " - phone session flags cleared (idempotent)",
                        feature="gui")

    def _on_presenter_stats(self, stats: dict) -> None:
        self._presenter_stats = dict(stats or {})

    def _stop_presenter(self, why: str) -> None:
        b = getattr(self, "_presenter_bridge", None)
        if b is None:
            return
        self._presenter_bridge = None
        self._presenter_sid = None
        set_presenter_bridge(None)
        with contextlib.suppress(Exception):
            b.stop(reason=why)
        self.events.log("PRESENTER_STOPPED", why, feature="media")

    def _update_presenter_for_stream(self, conn: dict, sid) -> None:
        if str(getattr(self, "present_engine", "gtk")).lower() != "gst":
            return
        try:
            owner, _svc, _stream = self._resolve_stream_owner(conn, sid)
        except Exception:
            return
        if owner == "webcam":
            self._stop_presenter("webcam session negotiated (the sidecar is "
                                 "desktop/appstream-only in v1)")
            return
        if len(getattr(self, "_pcs", {}) or {}) > 1:
            self._stop_presenter(f"{len(self._pcs)} concurrent media sessions "
                                 "(v1 forwards exactly one elementary stream)")
            return
        if self._presenter_bridge is not None:
            return
        try:
            from samyuj.media.gst_present.bridge import PresenterBridge
            b = PresenterBridge(
                title=f"Samyuj — {owner} (GStreamer sidecar)",
                sink=self.present_sink,
                on_death=lambda: set_presenter_bridge(None),
                on_stats=self._on_presenter_stats,
                on_drop=lambda: request_h264_keyframe(on_arm=True))
            if b.start():
                self._presenter_bridge = b
                self._presenter_sid = sid
                set_presenter_bridge(b)
                request_h264_keyframe(on_arm=True)
                self.events.log(
                    "PRESENTER_STARTED",
                    f"sink={b.sink_chosen} decoder={b.decoder_chosen} pid={b.pid} "
                    f"- decode+present moved OUT of this process; the GTK stream "
                    f"window shows its placeholder while the sidecar window "
                    f"renders the video", feature="media")
            else:
                self.present_engine = "gtk"
                self.events.log(
                    "PRESENTER_START_FAIL",
                    f"{b.last_error or 'unknown'} - staying on in-process decode "
                    f"+ GTK paint. Install: sudo apt install python3-gi "
                    f"gstreamer1.0-plugins-base gstreamer1.0-plugins-good "
                    f"gstreamer1.0-plugins-bad gstreamer1.0-libav "
                    f"gstreamer1.0-vaapi; probe: python3 "
                    f"media/gst_present/presenter.py --selfcheck", feature="media")
        except Exception as exc:
            self.present_engine = "gtk"
            self.events.log("PRESENTER_IMPORT_FAIL", repr(exc)[:200], feature="media")

    async def _media_negotiate(self, conn: dict, sid, offer_json: str):
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
                return await dt.run_coroutine(self._media_negotiate(conn, sid, offer_json))
        off_sdp = ""
        off_type = "offer"
        raw = str(offer_json or "").strip()
        try:
            off = json.loads(raw)
            if isinstance(off, str):
                off = json.loads(off)
            if isinstance(off, dict) and isinstance(off.get("sdp"), str):
                off_sdp = off["sdp"]
                off_type = str(off.get("type") or "offer")
            else:
                raise ValueError(f"unexpected offer shape: {type(off).__name__}")
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            if raw.startswith("v="):
                off_sdp, off_type = raw, "offer"
                self.events.log("MEDIA_OFFER_RAW_SDP",
                                f"sid={sid} offer was bare SDP ({len(raw)} chars) — accepted")
            else:
                self.events.log("MEDIA_OFFER_PARSE_FAIL",
                                f"sid={sid} {exc!r} head={raw[:120]!r}")
                return None
        try:
            pc = SamyujPeerConnection(RTCConfiguration(iceServers=[]))
            if getattr(self, "desktop_service_thread", None) is not None and self.desktop_service_thread.is_current_loop():
                _bind_pc_to_media_loop(pc, self.desktop_service_thread)
        except Exception as exc:
            self.events.log("MEDIA_PC_CREATE_FAIL", f"sid={sid} {exc!r}")
            return None
        is_audio_only = ("m=audio" in off_sdp and "m=video" not in off_sdp)
        is_wc_sid = str(sid).startswith("wc") or str(sid) == "webcam"
        is_as_video = (not is_audio_only) and (not is_wc_sid)
        if is_as_video:
            conn["as_media_connected"] = False
            self.as_status.update(phase="negotiating", sid=sid,
                                  detail="WebRTC offer received — creating answer",
                                  updated=time.time())
        if is_audio_only:
            if not hasattr(self, "_audio_pcs") or not isinstance(self._audio_pcs, dict):
                self._audio_pcs = {}
            self._audio_pcs[sid] = pc
            if not self._pcs:
                self._pcs[sid] = pc
        else:
            self._pcs[sid] = pc
        self.media_stats["sessions"].append(sid)
        if not is_audio_only:
            self._install_h264_keyframe_hook()
            await asyncio.to_thread(self._update_presenter_for_stream, conn, sid)
        try:
            if not is_audio_only:
                pc.addTransceiver("video", direction="recvonly")

            @pc.on("track")
            def _on_track(track):
                self.events.log("MEDIA_TRACK", f"sid={sid} kind={track.kind}")
                if track.kind == "video":
                    conn["tasks"].append(
                        _make_threadsafe_task(self._media_pump(conn, track, sid)))
                elif track.kind == "audio" and getattr(self, "audio", "on") != "off":
                    conn["tasks"].append(
                        _make_threadsafe_task(self._audio_pump(conn, track, sid)))

            @pc.on("connectionstatechange")
            async def _on_state():
                st = pc.connectionState
                self.media_stats["states"].append(st)
                if len(self.media_stats["states"]) > 200:
                    del self.media_stats["states"][:100]
                self.events.log("MEDIA_STATE", f"sid={sid} state={st}")
                if st == "connected":
                    conn["media_connected_at"] = time.time()
                    if not is_audio_only:
                        async def _initial_pli_nudge():
                            for _d in (0.06, 0.30):
                                await asyncio.sleep(_d)
                                with contextlib.suppress(Exception):
                                    await self.request_stream_pli(conn, sid=sid, force=True)
                        conn.setdefault("tasks", []).append(_make_threadsafe_task(_initial_pli_nudge()))
                    if is_as_video:
                        conn["as_media_connected"] = True
                        self.as_status.update(phase="live", sid=sid,
                                              detail="media connected",
                                              updated=time.time())
                        if self.auto_swipe and not conn.get("auto_swipe_done") and hasattr(self, "_auto_swipe_task"):
                            conn["auto_swipe_done"] = True
                            conn["tasks"].append(
                                _make_threadsafe_task(getattr(self, "_auto_swipe_task")(conn)))
                elif st in ("failed", "closed", "disconnected"):
                    if is_as_video:
                        self.as_status.update(phase="media-" + st, sid=sid,
                                              detail=f"peer connection {st}",
                                              updated=time.time())

            await pc.setRemoteDescription(
                RTCSessionDescription(sdp=off_sdp, type=off_type))
            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)
            desc = pc.localDescription
            ans_sdp = desc.sdp
            if not self.sdp_raw:
                ans_sdp, n_removed = sanitize_answer_sdp(ans_sdp)
                self.events.log("MEDIA_ANSWER_SANITIZED",
                                f"sid={sid} removed={n_removed} ssrc/msid lines "
                                f"(sdpLen {len(desc.sdp)} -> {len(ans_sdp)})")
                pref_ip = self._ss_prefer_ip(conn)
                if pref_ip:
                    ans_sdp, n_pref = _prefer_candidate_ip(ans_sdp, pref_ip)
                    if n_pref:
                        self.events.log(
                            "MEDIA_ANSWER_IP_PREFERRED",
                            f"sid={sid} preferred candidate IP {pref_ip} moved first in {n_pref} section(s)",
                            feature="appstream",
                        )
            else:
                self.events.log("MEDIA_ANSWER_RAW",
                                f"sid={sid} --sdp-raw: sending aiortc SDP verbatim")
            with contextlib.suppress(Exception):
                m_prof = re.search(r"profile-level-id=([0-9a-fA-F]{6})", off_sdp)
                if m_prof:
                    ans_sdp = re.sub(
                        r"profile-level-id=[0-9a-fA-F]{6}",
                        f"profile-level-id={m_prof.group(1)}",
                        ans_sdp,
                    )
            if is_audio_only:
                with contextlib.suppress(Exception):
                    m_opus = re.search(r"a=rtpmap:(\d+) opus/48000/2", ans_sdp, re.IGNORECASE)
                    if m_opus:
                        opus_pt = m_opus.group(1)
                        if f"a=fmtp:{opus_pt} " not in ans_sdp:
                            m_off_fmtp = re.search(rf"a=fmtp:{opus_pt}\s+([^\r\n]+)", off_sdp)
                            fmtp_val = m_off_fmtp.group(1).strip() if m_off_fmtp else "minptime=10;useinbandfec=1; stereo=1; ptime=10"
                            ans_sdp = ans_sdp.replace(
                                m_opus.group(0),
                                f"{m_opus.group(0)}\r\na=fmtp:{opus_pt} {fmtp_val}",
                                1,
                            )
            pref = "auto" if is_wc_sid else str(getattr(self, "media_codec_pref", "auto")).lower()
            if pref in ("h264", "vp8", "vp9", "h265"):
                ans_sdp, reordered = prefer_codec_in_answer(ans_sdp, pref.upper().replace("VP9", "VP9"))
                if reordered:
                    self.events.log("MEDIA_ANSWER_CODEC_PREFERRED",
                                    f"sid={sid} {pref.upper()} moved first in m=video "
                                    f"(phone will use its hardware encoder)",
                                    feature="appstream")
            ans_json = json.dumps({"type": desc.type, "sdp": ans_sdp})
            sel = [l for l in ans_sdp.splitlines() if l.startswith("m=")]
            self.events.log("MEDIA_ANSWER_CREATED",
                            f"sid={sid} sdpLen={len(ans_sdp)} m={sel[:1]} codecPref={pref}")
            conn.setdefault("tasks", []).append(_make_threadsafe_task(
                _boost_pc_sockets_delayed(self, pc, f"media sid={sid}")))
            return ans_json
        except Exception as exc:
            self.events.log("MEDIA_NEGOTIATE_FAIL", f"sid={sid} {exc!r}")
            with contextlib.suppress(Exception):
                await pc.close()
            self._pcs.pop(sid, None)
            return None

    async def _media_remote_signal(self, pc, ctype: str, cdata: str, tag: str):
        if getattr(self, "use_desktop_thread", True) and getattr(pc, "_sc_media_loop", None) is not None:
            dt = self._ensure_desktop_service_thread()
            if dt is not None and not dt.is_current_loop():
                dt.stats["candidates_signaled"] = int(dt.stats.get("candidates_signaled", 0)) + 1
                return await dt.run_coroutine(self._media_remote_signal(pc, ctype, cdata, tag))
        try:
            d = json.loads(cdata)
        except json.JSONDecodeError:
            self.events.log("MEDIA_SIGNAL_PARSE_FAIL", f"{tag} {cdata[:80]!r}")
            return
        if "candidate" not in d:
            self.events.log("MEDIA_SIGNAL_IGNORED", f"{tag} keys={list(d.keys())}")
            return
        try:
            cand_sdp = str(d["candidate"])
            if cand_sdp.startswith("candidate:"):
                cand_sdp = cand_sdp[len("candidate:"):]
            cand = candidate_from_sdp(cand_sdp)
            cand.sdpMid = str(d.get("id") or "0")
            cand.sdpMLineIndex = int(d.get("label") or 0)
            await pc.addIceCandidate(cand)
            self.media_stats["candidates_in"] += 1
            self.events.log("MEDIA_CANDIDATE_ADDED",
                            f"{tag} {cand.ip}:{cand.port}/{cand.protocol}/{cand.type}")
        except Exception as exc:
            self.events.log("MEDIA_CANDIDATE_FAIL", f"{tag} {exc!r}")

    async def _media_send_local_candidates(self, conn, pc, sid):
        desc = pc.localDescription
        if not desc:
            return
        ordered = _order_sdp_candidate_lines(
            desc.sdp,
            prefer_ip=self._ss_prefer_ip(conn),
            prefer_usb=self._is_usb_conn(conn),
        )
        for raw in ordered:
            cand = {"type": "candidate", "label": 0, "id": "0", "candidate": raw}
            await self._send_ws_text(
                conn,
                build_appstream_webrtc_signal(self, sid,
                                              json.dumps(cand, separators=(",", ":"))),
                "MEDIA_CANDIDATE_SENT(cmd20)")
            self.media_stats["candidates_out"] += 1

    def _resolve_stream_owner(self, conn: dict, sid) -> tuple[str, int, str]:
        sid_str = str(sid)
        if sid_str.startswith("wc") or sid_str == "webcam":
            return "webcam", 3, "webcam"
        sess_info = (conn.get("as_sessions") or {}).get(sid)
        if isinstance(sess_info, dict) and sess_info.get("pkg"):
            if sess_info.get("pkg") in ("desktop", "mobile", "mirror"):
                return "desktop", 1, f"desktop:{sid_str}"
            return "appstream", 15, f"appstream:{sid_str}"
        if sid is not None and conn.get("desktop_as_sid") is not None and sid == conn.get("desktop_as_sid"):
            return "desktop", 1, f"desktop:{sid_str}"
        if conn.get("desktop_started") and conn.get("as_pkg") in (None, "desktop", "mobile", "mirror"):
            return "desktop", 1, f"desktop:{sid_str}"
        return "appstream", 15, f"appstream:{sid_str}"

    async def _audio_pump(self, conn: dict, track, sid: int | str) -> None:
        frames = 0
        getattr(self, "events", None) and self.events.log("AUDIO_PUMP_START", f"sid={sid}", feature="appstream")
        try:
            while not conn.get("closed"):
                try:
                    await asyncio.wait_for(track.recv(), timeout=5.0)
                    frames += 1
                    self.media_stats["audio_frames"] = int(self.media_stats.get("audio_frames", 0)) + 1
                except (asyncio.TimeoutError, TimeoutError):
                    continue
                except Exception:
                    break
        except asyncio.CancelledError:
            pass
        finally:
            getattr(self, "events", None) and self.events.log("AUDIO_PUMP_END", f"sid={sid} frames={frames}", feature="appstream")

    async def _media_pump(self, conn: dict, track, sid) -> None:
        n = 0
        last_snap = 0.0
        t0 = time.time()
        owner_service, service_id, stream_id = self._resolve_stream_owner(conn, sid)
        reg = getattr(self, "stream_registry", None)
        if reg is None:
            reg = self.stream_registry = MediaStreamRegistry()
        rec = None
        try:
            rec = reg.register(
                stream_id=stream_id,
                owner_service=owner_service,
                service_id=service_id,
                session_id=sid,
                track_id=str(getattr(track, "id", "") or sid),
                direction="inbound",
            )
            is_wc = (rec.owner_service == "webcam")
            while not conn["closed"]:
                frame = await track.recv()
                _tq = getattr(track, "_queue", None)
                if _tq is not None and hasattr(_tq, "empty") and hasattr(_tq, "get_nowait"):
                    _drained = 0
                    while not _tq.empty() and _drained < 8:
                        try:
                            _newer = _tq.get_nowait()
                        except Exception:
                            break
                        if _newer is not None and hasattr(_newer, "width"):
                            frame = _newer
                            n += 1
                            _drained += 1
                want_uv_swap = bool(getattr(self, "uv_swap", True) and (is_wc or getattr(self, "uv_swap_all", False)))
                uv_swapped = False
                if want_uv_swap:
                    if not is_uv_swapped(frame):
                        frame = swap_uv(frame)
                    uv_swapped = True
                if is_wc and getattr(self, "webcam_output_res", None):
                    tw, th = self.webcam_output_res
                    if (getattr(frame, "width", tw), getattr(frame, "height", th)) != (tw, th):
                        with contextlib.suppress(Exception):
                            if hasattr(frame, "reformat"):
                                frame = frame.reformat(width=tw, height=th)
                                if uv_swapped:
                                    _mark_uv_swapped(frame)
                n += 1
                dt_pump = getattr(self, "desktop_service_thread", None) or (self._ensure_desktop_service_thread() if hasattr(self, "_ensure_desktop_service_thread") else None)
                if dt_pump is not None:
                    dt_pump.stats["frames_pumped"] = int(dt_pump.stats.get("frames_pumped", 0)) + 1
                _pm = getattr(self, "power_mgr", None)
                _do_bgrx = True if _pm is None else _pm.should_convert_frame(owner_service, sid=sid, is_first=(n <= 2))
                if dt_pump is not None and hasattr(frame, "reformat") and _do_bgrx:
                    _fw = int(getattr(frame, "width", 0) or 0)
                    _fh = int(getattr(frame, "height", 0) or 0)
                    _tsize = self.get_stream_target_size(owner_service, sid=sid, fw=_fw, fh=_fh)
                    if _tsize is not None:
                        await dt_pump.prepare_frame_bgrx_async(
                            frame, _tsize[0], _tsize[1], stream_key=stream_id
                        )
                        if _tq is not None and hasattr(_tq, "empty") and not _tq.empty():
                            _post_newer = None
                            _post_drained = 0
                            while not _tq.empty() and _post_drained < 4:
                                try:
                                    _cand = _tq.get_nowait()
                                except Exception:
                                    break
                                if _cand is not None and hasattr(_cand, "width"):
                                    _post_newer = _cand
                                    n += 1
                                    _post_drained += 1
                            if _post_newer is not None:
                                frame = _post_newer
                                if want_uv_swap and not is_uv_swapped(frame):
                                    frame = swap_uv(frame)
                                    uv_swapped = True
                                if is_wc and getattr(self, "webcam_output_res", None):
                                    tw, th = self.webcam_output_res
                                    if (getattr(frame, "width", tw), getattr(frame, "height", th)) != (tw, th):
                                        with contextlib.suppress(Exception):
                                            if hasattr(frame, "reformat"):
                                                frame = frame.reformat(width=tw, height=th)
                                                if uv_swapped:
                                                    _mark_uv_swapped(frame)
                                _fw = int(getattr(frame, "width", 0) or 0)
                                _fh = int(getattr(frame, "height", 0) or 0)
                                _tsize2 = self.get_stream_target_size(owner_service, sid=sid, fw=_fw, fh=_fh)
                                if _tsize2 is not None and hasattr(frame, "reformat"):
                                    await dt_pump.prepare_frame_bgrx_async(
                                        frame, _tsize2[0], _tsize2[1], stream_key=stream_id
                                    )
                if rec.status == "stopped" and sid not in self._pcs and len(self._pcs) > 0:
                    break
                rec.push_frame(frame, uv_swapped=uv_swapped)
                if is_wc and getattr(self, "webcam_controller", None) is not None:
                    if n == 1 and self.webcam_controller.v4l2_auto:
                        with contextlib.suppress(Exception):
                            await asyncio.to_thread(self.webcam_controller.arm_for_stream, False)
                    self.webcam_controller.on_webcam_frame(frame, uv_swapped=uv_swapped)
                conn.setdefault("media_last_frame_by_service", {})[owner_service] = (frame, sid, n)
                if is_wc:
                    conn["webcam_last_frame"] = (frame, sid, n)
                else:
                    conn["media_last_frame"] = (frame, sid, n)
                self.media_stats["frames"] = self.media_stats.get("frames", 0) + 1
                if callable(getattr(self, "on_frame_ready_cb", None)):
                    with contextlib.suppress(Exception):
                        self.on_frame_ready_cb(owner_service, sid, frame, n)
                if n == 1:
                    self.events.log("MEDIA_FIRST_FRAME",
                                    f"stream={stream_id} owner={owner_service} sid={sid} {frame.width}x{frame.height}",
                                    service_id=service_id, session_id=sid)
                    if getattr(self, "debug", False) or self.snap_interval > 0.0:
                        self._enqueue_snapshot(conn, frame, sid, n, tag="first")
                    last_snap = time.time()
                now = time.time()
                if self.snap_interval > 0.0 and (now - last_snap >= self.snap_interval):
                    last_snap = now
                    self._enqueue_snapshot(conn, frame, sid, n)
                if n % 300 == 0:
                    fps = n / max(1e-6, now - t0)
                    self.events.log("MEDIA_STATS",
                                    f"stream={stream_id} sid={sid} frames={n} fps={fps:.1f} "
                                    f"{frame.width}x{frame.height}",
                                    service_id=service_id, session_id=sid)
        except (asyncio.CancelledError, ConnectionError):
            pass
        except Exception as exc:
            self.events.log("MEDIA_PUMP_STOP", f"stream={stream_id} sid={sid} {exc!r}")
        finally:
            if rec is not None:
                rec.status = "stopped"
            dt = time.time() - t0
            self.events.log("MEDIA_PUMP_END",
                            f"stream={stream_id} sid={sid} frames={n} in {dt:.1f}s "
                            f"({n / max(1e-6, dt):.1f} fps)")

    def _enqueue_snapshot(self, conn: dict, frame, sid, n: int, tag: str = "") -> None:
        key = f"{sid}:{tag or 'periodic'}"
        pending = getattr(self, "_snap_pending", None)
        if pending is None:
            pending = self._snap_pending = set()
        if key in pending and not tag:
            return
        ex = getattr(self, "_snap_executor", None)
        if ex is None:
            pending.add(key)
            try:
                self._save_snapshot(conn, frame, sid, n, tag=tag)
            finally:
                pending.discard(key)
            return
        try:
            prep_snap = get_prepared_frame_bgrx(frame)
            if isinstance(prep_snap, dict) and prep_snap.get("rgb_frame") is not None:
                rgb_fr = prep_snap["rgb_frame"]
                plane = rgb_fr.planes[0]
                detached = ("rgb", memoryview(plane), int(rgb_fr.width), int(rgb_fr.height), int(plane.line_size))
            else:
                fmt_name = str(getattr(getattr(frame, "format", None), "name", "yuv420p") or "yuv420p")
                w_s, h_s = int(frame.width), int(frame.height)
                planes_mv = [(memoryview(p), int(p.line_size)) for p in frame.planes]
                detached = ("planes", fmt_name, w_s, h_s, planes_mv)
        except Exception:
            return
        pending.add(key)

        def _worker():
            try:
                if detached[0] == "rgb":
                    _, raw_rgb, w_w, h_w, stride_w = detached
                    self._save_snapshot(conn, (bytes(raw_rgb), w_w, h_w, stride_w), sid, n, tag=tag)
                else:
                    _, fmt_w, w_w, h_w, p_list = detached
                    import av as _av_s
                    vf_clone = _av_s.VideoFrame(w_w, h_w, fmt_w)
                    for idx_p, (p_bytes, _ls) in enumerate(p_list):
                        if idx_p < len(vf_clone.planes):
                            vf_clone.planes[idx_p].update(p_bytes)
                    rgb_w = vf_clone.reformat(format="rgb24")
                    pl_w = rgb_w.planes[0]
                    self._save_snapshot(conn, (bytes(pl_w), int(rgb_w.width), int(rgb_w.height), int(pl_w.line_size)), sid, n, tag=tag)
            finally:
                pending.discard(key)

        try:
            ex.submit(_worker)
        except Exception:
            pending.discard(key)

    def _save_snapshot(self, conn: dict, frame, sid, n: int, tag: str = "") -> None:
        snap_dir = self.out_dir / "snaps"
        with contextlib.suppress(OSError):
            snap_dir.mkdir(exist_ok=True)
        stamp = datetime.datetime.now().strftime("%H%M%S")
        base = snap_dir / f"snap_{sid}_{n:06d}_{stamp}{'_' + tag if tag else ''}"
        kind = None
        if isinstance(frame, tuple) and len(frame) == 4:
            raw_rgb, fw, fh, stride = frame
            with contextlib.suppress(Exception):
                from PIL import Image
                img = Image.frombytes("RGB", (fw, fh), raw_rgb, "raw", "RGB", stride, 1)
                img.save(str(base.with_suffix(".jpg")), format="JPEG", quality=85)
                kind = "jpg"
            if kind is None:
                with contextlib.suppress(Exception):
                    import gzip
                    row_bytes = fw * 3
                    packed = b"".join(raw_rgb[r * stride:r * stride + row_bytes] for r in range(fh))
                    with gzip.open(str(base.with_suffix(".ppm.gz")), "wb") as gz:
                        gz.write(f"P6\n{fw} {fh}\n255\n".encode())
                        gz.write(packed)
                    kind = "ppm.gz"
            if kind:
                self.media_stats["snapshots"] += 1
                self.events.log("MEDIA_SNAPSHOT",
                                f"sid={sid} n={n} {fw}x{fh} -> {base.name}.{kind.split('.')[0]} ({kind})")
            else:
                self.events.log("MEDIA_SNAPSHOT_FAIL", f"sid={sid} n={n}")
            return
        fw = getattr(frame, "width", 0)
        fh = getattr(frame, "height", 0)
        with contextlib.suppress(Exception):
            import av
            vf = frame.reformat(format="yuvj420p")
            codec = av.CodecContext.create("mjpeg", "w")
            codec.width, codec.height = vf.width, vf.height
            codec.pix_fmt = "yuvj420p"
            codec.open()
            packets = codec.encode(vf) + codec.encode(None)
            data = b"".join(bytes(p) for p in packets)
            if data[:2] == b"\xff\xd8":
                base.with_suffix(".jpg").write_bytes(data)
                kind = "jpg"
        if kind is None:
            with contextlib.suppress(Exception):
                img = frame.to_image()
                img.save(str(base.with_suffix(".png")))
                kind = "png"
        if kind is None:
            with contextlib.suppress(Exception):
                import gzip
                rgb = frame.reformat(format="rgb24")
                raw = bytes(rgb.planes[0])
                with gzip.open(str(base.with_suffix(".ppm.gz")), "wb") as gz:
                    gz.write(f"P6\n{rgb.width} {rgb.height}\n255\n".encode())
                    gz.write(raw)
                kind = "ppm.gz"
        if kind:
            self.media_stats["snapshots"] += 1
            self.events.log("MEDIA_SNAPSHOT",
                            f"sid={sid} n={n} {fw}x{fh} -> {base.name}.{kind.split('.')[0]} ({kind})")
        else:
            self.events.log("MEDIA_SNAPSHOT_FAIL", f"sid={sid} n={n}")

    def note_link_loss(self, loss_pct: float) -> None:
        with contextlib.suppress(Exception):
            change = note_link_loss_for_remb(loss_pct)
            if change == "degraded":
                self.events.log(
                    "REMB_FLOOR_ADAPTED",
                    f"loss={loss_pct:.1f}% >= {_REMB_LOSS_HIGH_PCT:.0f}% - REMB floor "
                    f"dropped {_REMB_FLOOR_BPS // 1000} -> "
                    f"{_adaptive_remb_floor_bps() // 1000} kbps so the phone's "
                    f"encoder can back off (saturated link)", feature="media")
            elif change == "recovered":
                self.events.log(
                    "REMB_FLOOR_RESTORED",
                    f"loss <= {_REMB_LOSS_LOW_PCT:.0f}% for {_REMB_RECOVER_SAMPLES} "
                    f"s - REMB floor back to {_REMB_FLOOR_BPS // 1000} kbps",
                    feature="media")

    def _link_is_congested(self) -> float:
        worst = 0.0
        with contextlib.suppress(Exception):
            md = getattr(self, "media_diag", None)
            if md is None:
                return 0.0
            for r in md.samples(4):
                for _lab, entries in (r.get("streams") or {}).items():
                    for e in entries or []:
                        worst = max(worst, float(e.get("loss_pct") or 0.0))
        return worst

    async def request_stream_pli(self, conn: dict | None = None, sid=None,
                                 force: bool = False) -> bool:
        now_m = time.monotonic()
        effective_force = bool(force or is_decoder_gate_active())
        if effective_force:
            if now_m - getattr(self, "_last_forced_pli_mono", 0.0) < 0.35:
                return False
        else:
            if now_m - getattr(self, "_last_manual_pli_mono", 0.0) < 0.4:
                return False
        loss = self._link_is_congested()
        gate = float(getattr(self, "pli_loss_gate_pct", 3.0))
        if not effective_force and gate > 0 and loss >= gate:
            self.input_stats["pli_suppressed_congestion"] = \
                self.input_stats.get("pli_suppressed_congestion", 0) + 1
            with contextlib.suppress(Exception):
                if now_m - getattr(self, "_last_pli_suppress_log", 0.0) > 5.0:
                    self._last_pli_suppress_log = now_m
                    self.events.log(
                        "PLI_SUPPRESSED_CONGESTION",
                        f"loss={loss:.1f}% >= {gate:.0f}% gate - NOT requesting a "
                        f"speculative IDR keyframe during drag; recovery PLIs remain "
                        f"unmuted if the decoder loses sync.", feature="media")
            return False
        if effective_force:
            self._last_forced_pli_mono = now_m
        else:
            self._last_manual_pli_mono = now_m
        c = conn or self._live_ws()
        target_sid = sid if sid is not None else ((c or {}).get("desktop_as_sid") or (c or {}).get("as_session_id"))
        pc = self._pcs.get(target_sid) if target_sid is not None else None
        if pc is None and self._pcs:
            pc = next(iter(self._pcs.values()), None)
        if pc is None:
            return False
        async def _do_pli() -> bool:
            _sent = False
            with contextlib.suppress(Exception):
                for tr in pc.getTransceivers():
                    if getattr(tr, "kind", "") == "video" and getattr(tr, "receiver", None) is not None:
                        rx = tr.receiver
                        ssrcs = list(getattr(rx, "_RTCRtpReceiver__active_ssrc", {}) or {})
                        for ssrc in ssrcs[:1]:
                            await rx._send_rtcp_pli(ssrc)
                            _sent = True
            return _sent

        mloop = getattr(pc, "_sc_media_loop", None)
        try:
            cur_l = asyncio.get_running_loop()
        except RuntimeError:
            cur_l = None
        if mloop is not None and mloop is not cur_l and mloop.is_running():
            dt = getattr(self, "desktop_service_thread", None)
            if dt is not None:
                dt.stats["pli_requests_dispatched"] = int(dt.stats.get("pli_requests_dispatched", 0)) + 1
            fut = asyncio.run_coroutine_threadsafe(_do_pli(), mloop)
            return await asyncio.wrap_future(fut)
        return await _do_pli()
