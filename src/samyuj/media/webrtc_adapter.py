from __future__ import annotations

import collections
import contextlib
import fractions
import os
import time
from typing import Any

from samyuj.media.peer import boost_pc_socket_buffers
from samyuj.media.present import (
    _H264_GATE_STATS,
    _IDR_GATE_CAP_S,
    _NACK_STORM_STATS,
    get_presenter_bridge,
    h264_has_idr_or_sps,
    request_h264_keyframe,
    set_decoder_gate_active,
    vp8_is_keyframe,
)
from samyuj.media.remb import (
    _RBE_MIN_FEEDBACK_INTERVAL_S,
    adaptive_remb_ceiling_bps,
    adaptive_remb_floor_bps,
    clamp_remb_result,
)

_NACK_MAX_MISSING_FOR_RETRANSMIT = 64
_NACK_PER_PKT_RETRY_S = 0.025
_NACK_MAX_RETRIES = 4
_JB_MAX_HOLE_WAIT_S = 0.065
_JB_MAX_HOLE_SPAN = 192

try:
    import aiortc.codecs as _aiortc_codecs
    import aiortc.codecs.h264 as _aiortc_h264
    import aiortc.codecs.vpx as _aiortc_vpx
    import aiortc.jitterbuffer as _jb_mod
    import aiortc.rtcrtpreceiver as _rtprecv
    import aiortc.rtcrtpsender as _rtpsend
    from aiortc import RTCConfiguration, RTCPeerConnection, RTCRtpReceiver, RTCRtpSender, RTCRtpTransceiver
    from aiortc.codecs.h264 import H264Decoder, H264Encoder
    from aiortc.codecs.vpx import Vp8Decoder, Vp8Encoder
    from aiortc.jitterbuffer import JitterBuffer
    from aiortc.rate import RemoteBitrateEstimator
    from aiortc.rtcrtpreceiver import NackGenerator
    try:
        from aiortc.utils import uint16_add as _u16_add
    except ImportError:
        from aiortc.rtp import uint16_add as _u16_add
    _JitterFrameCls = getattr(_jb_mod, "JitterFrame", None) or getattr(_jb_mod, "EncodedFrame", None)
    HAVE_WEBRTC = True
except Exception:
    HAVE_WEBRTC = False
    RTCPeerConnection = object
    RTCRtpReceiver = object
    JitterBuffer = object
    NackGenerator = object
    RemoteBitrateEstimator = object
    H264Decoder = object
    H264Encoder = object
    Vp8Decoder = object
    Vp8Encoder = object

def _pkt_payload_bytes(pkt: Any) -> bytes:
    d = getattr(pkt, "_data", b"")
    if isinstance(d, (bytes, bytearray)):
        return bytes(d)
    return bytes(getattr(d, "data", b"") or b"")

if HAVE_WEBRTC:

    class AdaptiveRemoteBitrateEstimator(RemoteBitrateEstimator):

        def __init__(self) -> None:
            super().__init__()
            self._sc_last_remb_mono: float = 0.0
            self._sc_last_remb_bps: int = 0

        def add(self, *args: Any, **kwargs: Any) -> Any:
            raw = super().add(*args, **kwargs)
            ceil_now = adaptive_remb_ceiling_bps()
            floor_now = adaptive_remb_floor_bps()
            if ceil_now > 0 and floor_now > ceil_now:
                floor_now = ceil_now
            clamped = clamp_remb_result(raw, floor_now, ceil_now)
            if clamped is None:
                return None
            now_m = time.monotonic()
            br_now = clamped[0] if isinstance(clamped, tuple) and clamped else 0
            if (
                self._sc_last_remb_mono > 0.0
                and (now_m - self._sc_last_remb_mono) < _RBE_MIN_FEEDBACK_INTERVAL_S
                and (not self._sc_last_remb_bps or br_now > int(self._sc_last_remb_bps * 0.85))
            ):
                return None
            self._sc_last_remb_mono = now_m
            self._sc_last_remb_bps = br_now
            return clamped

    class StormSafeNackGenerator(NackGenerator):

        def __init__(self) -> None:
            super().__init__()
            self._sc_nack_sent: dict[int, tuple[float, int]] = {}

        def add(self, packet: Any) -> bool:
            ret = super().add(packet)
            missing = getattr(self, "missing", None)
            if missing is None:
                return ret
            m_len = len(missing)
            if m_len == 0:
                if self._sc_nack_sent:
                    self._sc_nack_sent.clear()
                return False
            if m_len > _NACK_MAX_MISSING_FOR_RETRANSMIT:
                _NACK_STORM_STATS["storms_broken"] += 1
                _NACK_STORM_STATS["packets_cleared"] += m_len
                missing.clear()
                self._sc_nack_sent.clear()
                request_h264_keyframe(on_arm=True)
                return False
            now_m = time.monotonic()
            for s_old in [s for s in self._sc_nack_sent if s not in missing]:
                self._sc_nack_sent.pop(s_old, None)
            due_any = False
            expired = []
            for seq in list(missing):
                prev = self._sc_nack_sent.get(seq)
                if prev is None:
                    self._sc_nack_sent[seq] = (now_m, 1)
                    due_any = True
                else:
                    last_t, tries = prev
                    if tries >= _NACK_MAX_RETRIES:
                        expired.append(seq)
                    elif (now_m - last_t) >= _NACK_PER_PKT_RETRY_S:
                        self._sc_nack_sent[seq] = (now_m, tries + 1)
                        due_any = True
            for seq in expired:
                missing.discard(seq)
                self._sc_nack_sent.pop(seq, None)
            if not due_any:
                _NACK_STORM_STATS["throttled"] += 1
                return False
            return True

    class ImmediateMarkerJitterBuffer(JitterBuffer):

        def __init__(self, capacity: int = 512, prefetch: int = 0, is_video: bool = True) -> None:
            eff_cap = max(512, int(capacity)) if is_video else int(capacity)
            super().__init__(capacity=eff_cap, prefetch=prefetch, is_video=is_video)
            self._pli_drop_pending: bool = False
            self._hole_since_mono: float = 0.0
            self._hole_seq: int | None = None
            self._last_emitted_end_seq: int | None = None
            self._ready_frames: collections.deque = collections.deque()

        def smart_remove(self, count: int) -> bool:
            last_dropped_ts = None
            removed_any = False
            for _ in range(self._capacity):
                if count <= 0:
                    break
                pkt = self._packets[self._origin % self._capacity]
                if pkt is not None:
                    if removed_any and last_dropped_ts is not None and pkt.timestamp != last_dropped_ts:
                        break
                    last_dropped_ts = pkt.timestamp
                    removed_any = True
                else:
                    removed_any = True
                self._packets[self._origin % self._capacity] = None
                self._origin = _u16_add(self._origin, 1)
                count -= 1
            if removed_any and last_dropped_ts is not None:
                for _ in range(self._capacity):
                    pkt = self._packets[self._origin % self._capacity]
                    if pkt is None or pkt.timestamp != last_dropped_ts:
                        break
                    self._packets[self._origin % self._capacity] = None
                    self._origin = _u16_add(self._origin, 1)
            if removed_any and getattr(self, "_is_video", False):
                self._pli_drop_pending = True
            return self._origin is not None and any(p is not None for p in self._packets)

        def _tag_frame(self, frame: Any, frame_pkts: list[Any]) -> Any:
            if frame is None or not frame_pkts:
                return frame
            first_p = frame_pkts[0]
            last_p = frame_pkts[-1]
            desc = getattr(first_p, "_description", None) or getattr(first_p, "_data", None)
            has_start = True
            if desc is not None and not isinstance(desc, (bytes, bytearray)):
                if hasattr(desc, "first_fragment"):
                    has_start = bool(desc.first_fragment)
                elif hasattr(desc, "partition_start"):
                    has_start = bool(desc.partition_start)
            elif hasattr(first_p, "first_fragment"):
                has_start = bool(first_p.first_fragment)
            m_val = getattr(last_p, "marker", None)
            has_marker = True if m_val is None else (m_val in (1, True))
            prev_end = self._last_emitted_end_seq
            seq_contiguous = True if prev_end is None else (first_p.sequence_number == _u16_add(prev_end, 1))
            self._last_emitted_end_seq = last_p.sequence_number
            if not has_start or not has_marker:
                with contextlib.suppress(Exception):
                    frame._frame_incomplete = True
                    frame._pli_dropped = True
                self._pli_drop_pending = True
            elif not seq_contiguous or self._pli_drop_pending:
                with contextlib.suppress(Exception):
                    frame._pli_dropped = True
                self._pli_drop_pending = False
            return frame

        def _remove_frame(self, *args: Any, **kwargs: Any) -> Any:
            if not getattr(self, "_is_video", False) or self._origin is None or int(getattr(self, "_prefetch", 0) or 0) > 0:
                return super()._remove_frame(*args, **kwargs)
            frame_pkts = []
            timestamp = None
            for count in range(self._capacity):
                pos = (self._origin + count) % self._capacity
                pkt = self._packets[pos]
                if pkt is None:
                    break
                if timestamp is None:
                    timestamp = pkt.timestamp
                elif pkt.timestamp != timestamp:
                    frame = _JitterFrameCls(
                        data=b"".join(_pkt_payload_bytes(x) for x in frame_pkts),
                        timestamp=timestamp,
                    )
                    self.remove(count)
                    return self._tag_frame(frame, frame_pkts)
                frame_pkts.append(pkt)
                if getattr(pkt, "marker", None) == 1:
                    next_pkt = (
                        self._packets[(self._origin + count + 1) % self._capacity]
                        if (count + 1) < self._capacity
                        else None
                    )
                    if next_pkt is None or next_pkt.timestamp != timestamp:
                        frame = _JitterFrameCls(
                            data=b"".join(_pkt_payload_bytes(x) for x in frame_pkts),
                            timestamp=timestamp,
                        )
                        self.remove(count + 1)
                        return self._tag_frame(frame, frame_pkts)
            return None

        def _find_true_hole_seq(self, max_seq: int) -> tuple[int | None, int]:
            if self._origin is None:
                return None, 0
            span = _u16_add(max_seq, -self._origin)
            if span <= 0 or span >= self._capacity:
                return None, 0
            for idx in range(span):
                seq = _u16_add(self._origin, idx)
                if self._packets[seq % self._capacity] is None:
                    after = _u16_add(max_seq, -seq)
                    return seq, after
            return None, 0

        def add(self, packet: Any) -> tuple[bool, Any]:
            pli_flag, encoded_frame = super().add(packet)
            is_vid = bool(getattr(self, "_is_video", False))
            if is_vid and encoded_frame is None and self._origin is not None:
                hole_seq, after_hole = self._find_true_hole_seq(packet.sequence_number)
                if hole_seq is not None and after_hole > 0:
                    now_m = time.monotonic()
                    if self._hole_seq != hole_seq:
                        self._hole_seq = hole_seq
                        self._hole_since_mono = now_m
                    hole_age = now_m - self._hole_since_mono
                    if after_hole >= _JB_MAX_HOLE_SPAN or hole_age >= _JB_MAX_HOLE_WAIT_S:
                        steps = _u16_add(hole_seq, -self._origin) + 1
                        self.smart_remove(max(1, steps))
                        while (
                            self._origin is not None
                            and self._packets[self._origin % self._capacity] is None
                            and 0 < _u16_add(packet.sequence_number, -self._origin) < self._capacity
                        ):
                            self._origin = _u16_add(self._origin, 1)
                        self._hole_seq = None
                        self._hole_since_mono = 0.0
                        self._pli_drop_pending = True
                        pli_flag = True
                        encoded_frame = self._remove_frame(packet.sequence_number)
                else:
                    self._hole_seq = None
                    self._hole_since_mono = 0.0
            elif is_vid:
                self._hole_seq = None
                self._hole_since_mono = 0.0
            if pli_flag and is_vid:
                self._pli_drop_pending = True
            if encoded_frame is not None and self._pli_drop_pending:
                with contextlib.suppress(Exception):
                    encoded_frame._pli_dropped = True
                if not getattr(encoded_frame, "_frame_incomplete", False):
                    self._pli_drop_pending = False
            if is_vid and encoded_frame is not None:
                while True:
                    nxt = self._remove_frame(packet.sequence_number)
                    if nxt is None:
                        break
                    self._ready_frames.append(nxt)
            return pli_flag, encoded_frame

    class LowLatencyH264Decoder(H264Decoder):

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self._awaiting_idr_until: float = 0.0
            self._idr_gate_since: float = 0.0
            with contextlib.suppress(Exception):
                self.codec.flags |= (1 << 19)
                if os.environ.get("SC_H264_FAST_FLAG2") == "1":
                    self.codec.flags2 |= 1
                self.codec.thread_count = 1

        def _arm_gate(self, now_m: float, window_s: float) -> None:
            was_gated = self._idr_gate_since > 0.0
            if not was_gated:
                self._idr_gate_since = now_m
            self._awaiting_idr_until = now_m + window_s
            set_decoder_gate_active(True)
            if not was_gated:
                request_h264_keyframe(on_arm=True)

        def _clear_gate(self) -> None:
            self._awaiting_idr_until = 0.0
            self._idr_gate_since = 0.0
            set_decoder_gate_active(False)

        def decode(self, encoded_frame: Any) -> list[Any]:
            data = getattr(encoded_frame, "data", b"") or b""
            now_m = time.monotonic()
            if not data.startswith(b"\x00\x00\x00\x01"):
                self._arm_gate(now_m, 0.55)
                return []
            if getattr(encoded_frame, "_frame_incomplete", False):
                _H264_GATE_STATS["incomplete_keyframes_dropped"] = (
                    _H264_GATE_STATS.get("incomplete_keyframes_dropped", 0) + 1
                )
                _H264_GATE_STATS["gated_frames"] += 1
                self._arm_gate(now_m, 0.55)
                return []
            if getattr(encoded_frame, "_pli_dropped", False):
                if not h264_has_idr_or_sps(data):
                    self._arm_gate(now_m, 0.55)
                    return []
                self._clear_gate()
            elif now_m < self._awaiting_idr_until:
                if not h264_has_idr_or_sps(data):
                    _H264_GATE_STATS["gated_frames"] += 1
                    return []
                self._clear_gate()
            elif self._idr_gate_since > 0.0:
                if now_m - self._idr_gate_since < _IDR_GATE_CAP_S:
                    request_h264_keyframe()
                    self._awaiting_idr_until = now_m + 0.5
                    _H264_GATE_STATS["gated_frames"] += 1
                    return []
                _H264_GATE_STATS["cap_reached"] += 1
                self._clear_gate()
            bridge = get_presenter_bridge()
            if bridge is not None and getattr(bridge, "alive", False):
                if bridge.write(data):
                    _H264_GATE_STATS["forwarded_frames"] += 1
                    _H264_GATE_STATS["forwarded_bytes"] += len(data)
                    return []
            frames = super().decode(encoded_frame)
            if not frames and len(data) > 64:
                self._arm_gate(now_m, 0.45)
            return frames

    class GatedVp8Decoder(Vp8Decoder):

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self._awaiting_idr_until: float = 0.0
            self._idr_gate_since: float = 0.0

        def _arm_gate(self, now_m: float, window_s: float) -> None:
            was_gated = self._idr_gate_since > 0.0
            if not was_gated:
                self._idr_gate_since = now_m
            self._awaiting_idr_until = now_m + window_s
            set_decoder_gate_active(True)
            if not was_gated:
                request_h264_keyframe(on_arm=True)

        def _clear_gate(self) -> None:
            self._awaiting_idr_until = 0.0
            self._idr_gate_since = 0.0
            set_decoder_gate_active(False)

        def decode(self, encoded_frame: Any) -> list[Any]:
            data = getattr(encoded_frame, "data", b"") or b""
            now_m = time.monotonic()
            if not data or getattr(encoded_frame, "_frame_incomplete", False):
                _H264_GATE_STATS["vp8_gated_frames"] = _H264_GATE_STATS.get("vp8_gated_frames", 0) + 1
                self._arm_gate(now_m, 0.55)
                return []
            if getattr(encoded_frame, "_pli_dropped", False):
                if not vp8_is_keyframe(data):
                    _H264_GATE_STATS["vp8_gated_frames"] = _H264_GATE_STATS.get("vp8_gated_frames", 0) + 1
                    self._arm_gate(now_m, 0.55)
                    return []
                self._clear_gate()
            elif now_m < self._awaiting_idr_until:
                if not vp8_is_keyframe(data):
                    _H264_GATE_STATS["vp8_gated_frames"] = _H264_GATE_STATS.get("vp8_gated_frames", 0) + 1
                    return []
                self._clear_gate()
            elif self._idr_gate_since > 0.0:
                if now_m - self._idr_gate_since < _IDR_GATE_CAP_S:
                    request_h264_keyframe()
                    self._awaiting_idr_until = now_m + 0.5
                    _H264_GATE_STATS["vp8_gated_frames"] = _H264_GATE_STATS.get("vp8_gated_frames", 0) + 1
                    return []
                _H264_GATE_STATS["cap_reached"] += 1
                self._clear_gate()
            return super().decode(encoded_frame)

    class FastH264Encoder(H264Encoder):

        def _encode_frame(self, frame: Any, force_keyframe: bool) -> Any:
            if getattr(self, "codec", None) is None or (frame.width, frame.height) != (self.codec.width, self.codec.height):
                import av as _av
                codec = _av.CodecContext.create("libx264", "w")
                codec.width = int(frame.width)
                codec.height = int(frame.height)
                codec.bit_rate = max(int(getattr(self, "target_bitrate", 4_000_000) or 4_000_000), 2_000_000)
                codec.pix_fmt = "yuv420p"
                codec.framerate = fractions.Fraction(_aiortc_h264.MAX_FRAME_RATE, 1)
                codec.time_base = fractions.Fraction(1, _aiortc_h264.MAX_FRAME_RATE)
                codec.options = {
                    "profile": "baseline",
                    "level": "31",
                    "preset": "ultrafast",
                    "tune": "zerolatency",
                    "threads": "4",
                    "sliced-threads": "1",
                }
                codec.open()
                self.codec = codec
                self.buffer_data = b""
                self.buffer_pts = None
            return super()._encode_frame(frame, force_keyframe)

    class FastVp8Encoder(Vp8Encoder):

        def encode(self, frame: Any, force_keyframe: bool = False) -> Any:
            was_none = getattr(self, "codec", None) is None
            res = super().encode(frame, force_keyframe=force_keyframe)
            if was_none and getattr(self, "codec", None) is not None:
                with contextlib.suppress(Exception):
                    _aiortc_vpx.lib.vpx_codec_control_(
                        self.codec,
                        _aiortc_vpx.lib.VP8E_SET_CPUUSED,
                        _aiortc_vpx.ffi.cast("int", -12),
                    )
                    _aiortc_vpx.lib.vpx_codec_control_(
                        self.codec,
                        _aiortc_vpx.lib.VP8E_SET_STATIC_THRESHOLD,
                        _aiortc_vpx.ffi.cast("int", 100),
                    )
            return res

    class SamyujRtpReceiver(RTCRtpReceiver):

        def __init__(self, kind: str, transport: Any) -> None:
            super().__init__(kind, transport)
            if kind == "video":
                self._RTCRtpReceiver__jitter_buffer = ImmediateMarkerJitterBuffer(capacity=512, is_video=True)
                self._RTCRtpReceiver__nack_generator = StormSafeNackGenerator()
                self._RTCRtpReceiver__remote_bitrate_estimator = AdaptiveRemoteBitrateEstimator()

        async def _handle_rtp_packet(self, packet: Any, arrival_time_ms: int) -> None:
            await super()._handle_rtp_packet(packet, arrival_time_ms)
            with contextlib.suppress(Exception):
                jb = getattr(self, "_RTCRtpReceiver__jitter_buffer", None)
                rq = getattr(jb, "_ready_frames", None) if jb is not None else None
                if rq:
                    codecs = getattr(self, "_RTCRtpReceiver__codecs", {}) or {}
                    codec = codecs.get(packet.payload_type)
                    dec_q = getattr(self, "_RTCRtpReceiver__decoder_queue", None)
                    dec_t = getattr(self, "_RTCRtpReceiver__decoder_thread", None)
                    ts_map = getattr(self, "_RTCRtpReceiver__timestamp_mapper", None)
                    while rq:
                        extra_fr = rq.popleft()
                        if codec is not None and dec_q is not None and dec_t is not None:
                            if ts_map is not None:
                                extra_fr.timestamp = ts_map.map(extra_fr.timestamp)
                            dec_q.put((codec, extra_fr))
                dec_q2 = getattr(self, "_RTCRtpReceiver__decoder_queue", None)
                if dec_q2 is not None and hasattr(dec_q2, "qsize") and dec_q2.qsize() > 16:
                    while dec_q2.qsize() > 8:
                        try:
                            dec_q2.get_nowait()
                        except Exception:
                            break
                    request_h264_keyframe(on_arm=True)

    def _samyuj_get_decoder(codec: Any) -> Any:
        mime = codec.mimeType.lower()
        if mime == "video/h264":
            return LowLatencyH264Decoder()
        if mime == "video/vp8":
            return GatedVp8Decoder()
        return _aiortc_codecs.get_decoder(codec)

    def _samyuj_get_encoder(codec: Any) -> Any:
        mime = codec.mimeType.lower()
        if mime == "video/h264":
            return FastH264Encoder()
        if mime == "video/vp8":
            return FastVp8Encoder()
        return _aiortc_codecs.get_encoder(codec)

    class SamyujPeerConnection(RTCPeerConnection):

        def __init__(self, configuration: RTCConfiguration | None = None) -> None:
            _rtprecv.RTP_HISTORY_SIZE = 512
            _rtprecv.get_decoder = _samyuj_get_decoder
            _rtpsend.get_encoder = _samyuj_get_encoder
            super().__init__(configuration=configuration)

        def _RTCPeerConnection__createTransceiver(
            self, direction: str, kind: str, sender_track: Any = None
        ) -> RTCRtpTransceiver:
            dtlsTransport = self._RTCPeerConnection__createDtlsTransport()
            transceiver = RTCRtpTransceiver(
                direction=direction,
                kind=kind,
                sender=RTCRtpSender(sender_track or kind, dtlsTransport),
                receiver=SamyujRtpReceiver(kind, dtlsTransport),
            )
            transceiver.receiver._set_rtcp_ssrc(transceiver.sender._ssrc)
            transceiver.sender._stream_id = self._RTCPeerConnection__stream_id
            transceiver._bundled = False
            transceiver._transport = dtlsTransport
            self._RTCPeerConnection__transceivers.append(transceiver)
            return transceiver

        async def setLocalDescription(self, sessionDescription: Any = None) -> None:
            await super().setLocalDescription(sessionDescription)
            with contextlib.suppress(Exception):
                boost_pc_socket_buffers(self, size=4 * 1024 * 1024)
                self._wrap_stun_protocols_for_safe_close()

        def _wrap_stun_protocols_for_safe_close(self) -> None:
            with contextlib.suppress(Exception):
                for t in self.getTransceivers():
                    dtls = getattr(t, "_transport", None)
                    ice = getattr(dtls, "transport", None)
                    conn = getattr(ice, "_connection", None)
                    for attr_t in ("_check_task", "_consent_task", "_query_task"):
                        t_ice = getattr(conn, attr_t, None)
                        if t_ice is not None and hasattr(t_ice, "cancel"):
                            with contextlib.suppress(Exception):
                                t_ice.cancel()
                    for proto in list(getattr(conn, "_protocols", []) or []):
                        if getattr(proto, "_sc_safe_wrapped", False):
                            continue
                        proto._sc_safe_wrapped = True
                        orig_send_stun = proto.send_stun
                        orig_close = proto.close

                        def _safe_send_stun(message: Any, addr: Any, _p: Any = proto, _orig: Any = orig_send_stun) -> None:
                            tr = getattr(_p, "transport", None)
                            if tr is None or getattr(tr, "_closing", False) or getattr(tr, "_sock", None) is None:
                                return
                            with contextlib.suppress(Exception):
                                _orig(message, addr)

                        async def _safe_close(_p: Any = proto, _orig_c: Any = orig_close) -> None:
                            for tx in list(getattr(_p, "transactions", {}).values()):
                                h = getattr(tx, "_Transaction__timeout_handle", None)
                                if h is not None:
                                    with contextlib.suppress(Exception):
                                        h.cancel()
                            with contextlib.suppress(Exception):
                                await _orig_c()

                        proto.send_stun = _safe_send_stun
                        proto.close = _safe_close

        async def close(self) -> None:
            self._wrap_stun_protocols_for_safe_close()
            with contextlib.suppress(Exception):
                for t in self.getTransceivers():
                    dtls = getattr(t, "_transport", None)
                    ice = getattr(dtls, "transport", None)
                    conn = getattr(ice, "_connection", None)
                    for attr_t in ("_check_task", "_consent_task", "_query_task"):
                        t_ice = getattr(conn, attr_t, None)
                        if t_ice is not None and hasattr(t_ice, "cancel"):
                            with contextlib.suppress(Exception):
                                t_ice.cancel()
                    for proto in list(getattr(conn, "_protocols", []) or []):
                        for tx in list(getattr(proto, "transactions", {}).values()):
                            h = getattr(tx, "_Transaction__timeout_handle", None)
                            if h is not None:
                                with contextlib.suppress(Exception):
                                    h.cancel()
            await super().close()

else:
    SamyujPeerConnection = RTCPeerConnection
