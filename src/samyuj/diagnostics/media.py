from __future__ import annotations

import asyncio
import contextlib
import datetime
import json
import os
import statistics
import sys
import threading
import time
import traceback
from collections import deque
from pathlib import Path

_IN_FIELDS = (
    "packetsReceived", "packetsLost", "jitter", "framesReceived",
    "framesDecoded", "framesDropped", "keyFramesDecoded", "nackCount",
    "pliCount", "firCount", "concealmentEvents", "freezeCount",
    "framesAssembledFromMultiplePackets", "totalDecodeTime",
    "totalAssemblyTime", "bytesReceived", "frameWidth", "frameHeight",
    "framesPerSecond", "decoderImplementation",
)
_OUT_FIELDS = (
    "framesEncoded", "framesSent", "qpSum", "retransmittedPacketsSent",
    "targetBitrate", "bytesSent", "frameWidth", "frameHeight",
    "framesPerSecond", "encoderImplementation", "qualityLimitationReason",
    "qualityLimitationDurations",
)
_PAIR_FIELDS = ("currentRoundTripTime", "availableOutgoingBitrate",
                "availableIncomingBitrate", "state", "nominated",
                "bytesReceived", "bytesSent", "packetsReceived", "packetsLost")

def _num(v, default=None):
    try:
        if v is None or isinstance(v, bool):
            return default
        return float(v)
    except (TypeError, ValueError):
        return default

def _get(obj, key, default=None):
    with contextlib.suppress(Exception):
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)
    return default

class StreamSample(dict):
    pass

class MediaDiagnostics:

    def __init__(self, harness=None, interval: float = 1.0,
                 history_seconds: int = 900, log=None,
                 report_dir: Path | str | None = None) -> None:
        self.h = harness
        self.interval = max(0.25, float(interval))
        self.log = log or (lambda evt, msg: None)
        self.report_dir = Path(report_dir) if report_dir else None
        self.enabled = True
        self._samples: deque = deque(maxlen=max(30, int(history_seconds / self.interval)))
        self._stalls: deque = deque(maxlen=200)
        self._last_seq: dict = {}
        self._last_gaps: dict = {}
        self._prev_in: dict = {}
        self._prev_out: dict = {}
        self._prev_local: dict = {}
        self._prev_drops: dict = {}
        self._prev_kernel_udp: dict | None = None
        self._task = None
        self._loop_lag_ms: float = 0.0
        self._worst_loop_lag_ms: float = 0.0
        self._lag_probe_step: float = 0.04
        self.input_events: dict = {"move": 0, "press": 0, "release": 0,
                                   "dropped_busy": 0, "suppressed": 0}
        self._input_snap: dict = dict(self.input_events)
        self._dragging = False
        self._last_drag_mono = 0.0
        self.started_mono = time.monotonic()
        self.poll_errors = 0
        self.polls = 0
        self.stall_threshold_s = 0.4
        self._loop_heartbeat_mono = time.monotonic()
        self._stall_dumps: deque = deque(maxlen=40)
        self._stall_active: dict | None = None
        self._watchdog = None
        self._main_tid = threading.get_ident()

    def note_input(self, kind: str = "move") -> None:
        with contextlib.suppress(Exception):
            self.input_events[kind] = self.input_events.get(kind, 0) + 1
            if kind == "move":
                self._dragging = True
                self._last_drag_mono = time.monotonic()

    def note_stall(self, stream_id, gap_s: float, expected_s: float) -> None:
        with contextlib.suppress(Exception):
            self._stalls.append({"t": round(time.monotonic() - self.started_mono, 3),
                                 "stream": str(stream_id),
                                 "gap_s": round(float(gap_s), 4),
                                 "expected_s": round(float(expected_s), 4),
                                 "loop_lag_ms": round(self._loop_lag_ms, 1),
                                 "dragging": self._dragging})

    def note_frame(self, stream_id, seq) -> None:
        with contextlib.suppress(Exception):
            now = time.monotonic()
            prev = self._last_seq.get(stream_id)
            if prev is not None:
                gap = now - prev[1]
                gaps = self._last_gaps.setdefault(stream_id, deque(maxlen=60))
                gaps.append(gap)
                if len(gaps) >= 8:
                    med = statistics.median(gaps)
                    if med > 0 and gap > max(0.25, med * 3.5):
                        self.note_stall(stream_id, gap, med)
            self._last_seq[stream_id] = (seq, now)

    def start(self) -> None:
        if self._task is None or self._task.done():
            with contextlib.suppress(Exception):
                self._task = asyncio.get_running_loop().create_task(self.run())

    def stop(self) -> None:
        t, self._task = self._task, None
        if t is not None and not t.done():
            t.cancel()

    async def run(self) -> None:
        lag_task = None
        with contextlib.suppress(Exception):
            lag_task = asyncio.get_running_loop().create_task(self._lag_probe())
            self._start_watchdog()
        try:
            while True:
                t0 = time.monotonic()
                if self.enabled:
                    with contextlib.suppress(Exception):
                        await self.poll_once()
                dt = time.monotonic() - t0
                await asyncio.sleep(max(0.05, self.interval - dt))
        except asyncio.CancelledError:
            pass
        finally:
            if lag_task is not None and not lag_task.done():
                lag_task.cancel()

    def _start_watchdog(self) -> None:
        if self._watchdog is not None and self._watchdog.is_alive():
            return
        self._main_tid = threading.get_ident()
        th = threading.Thread(target=self._watchdog_loop, name="sc-loop-watchdog",
                              daemon=True)
        self._watchdog = th
        with contextlib.suppress(Exception):
            th.start()

    def _watchdog_loop(self) -> None:
        while True:
            time.sleep(0.1)
            now = time.monotonic()
            stale_for = now - self._loop_heartbeat_mono
            thr = max(self.stall_threshold_s,
                      float(getattr(self, "_lag_probe_step", 0.04)) * 2.0 + 0.2)
            if stale_for >= thr:
                if self._stall_active is None:
                    first = self._capture_stacks()
                    self._stall_active = {"started": now, "frames": first,
                                          "seq": [first], "last_seq": now}
                elif (now - float(self._stall_active.get("last_seq", 0.0)) >= 0.5
                        and len(self._stall_active.get("seq") or ()) < 24):
                    self._stall_active["seq"].append(self._capture_stacks())
                    self._stall_active["last_seq"] = now
            elif self._stall_active is not None:
                dur = now - self._stall_active["started"]
                seq = self._stall_active.get("seq") or [self._stall_active["frames"]]
                main_samples = [(s[0] if s else "?") for s in seq]
                track = []
                for m in main_samples:
                    if not track or track[-1] != m:
                        track.append(m)
                rec = {"t": round(self._stall_active["started"] - self.started_mono, 3),
                       "duration_s": round(dur, 3),
                       "stacks": self._stall_active["frames"],
                       "main_samples": main_samples}
                self._stall_dumps.append(rec)
                self._stall_active = None
                with contextlib.suppress(Exception):
                    self.log("LOOP_STALL",
                             f"the asyncio event loop was BLOCKED for {dur:.2f}s - "
                             f"nothing could service RTP/RTCP, so packets were lost "
                             f"and the decoder lost its reference frames. Main-thread "
                             f"track ({len(main_samples)} samples @0.5s): "
                             + " || ".join(track)[:2000])

    def _capture_stacks(self) -> list:
        out = []
        with contextlib.suppress(Exception):
            frames = sys._current_frames()
            order = sorted(frames.items(),
                           key=lambda kv: 0 if kv[0] == self._main_tid else 1)
            for tid, fr in order:
                lines = []
                for f, lineno in traceback.walk_stack(fr):
                    fn = f.f_code.co_filename
                    if "services/media_diagnostics.py" in fn:
                        continue
                    lines.append(f"{os.path.basename(fn)}:{lineno} "
                                 f"in {f.f_code.co_name}()")
                    if len(lines) >= 12:
                        break
                if lines:
                    tag = "MAIN" if tid == self._main_tid else f"tid{tid}"
                    out.append(f"[{tag}] blocked in " + lines[0]
                               + "  |  callers: " + " <- ".join(lines[1:5]))
        return out[:6]

    async def _lag_probe(self) -> None:
        try:
            while True:
                idle = True
                with contextlib.suppress(Exception):
                    idle = not bool(getattr(self.h, "_pcs", None))
                step = 0.25 if idle else 0.04
                self._lag_probe_step = step
                t0 = time.monotonic()
                await asyncio.sleep(step)
                lag = (time.monotonic() - t0 - step) * 1000.0
                self._loop_lag_ms = max(0.0, lag)
                self._loop_heartbeat_mono = time.monotonic()
                if self._loop_lag_ms > self._worst_loop_lag_ms:
                    self._worst_loop_lag_ms = self._loop_lag_ms
        except asyncio.CancelledError:
            pass

    def _session_context(self) -> dict:
        ctx: dict = {}
        h = self.h
        if h is None:
            return ctx
        with contextlib.suppress(Exception):
            st = getattr(h, "ss_stats", {}) or {}
            vd = st.get("virtualDisplay") or {}
            ctx["ss_share_type"] = str(getattr(h, "ss_share_type", ""))
            ctx["ss_capture"] = str(st.get("capture") or "")
            ctx["ss_backend"] = str(st.get("backend") or "")
            ctx["ss_state"] = str(st.get("state") or "")
            ctx["ss_extend_fallback"] = str(st.get("extend_fallback") or "")
            if vd:
                ctx["virtual_display"] = {
                    "ok": bool(vd.get("ok")),
                    "backend": str(vd.get("backend") or ""),
                    "position": str(vd.get("position") or ""),
                    "attached": bool(vd.get("attached")),
                    "capture_verified": vd.get("capture_verified"),
                    "size": [vd.get("width"), vd.get("height")],
                    "detail": str(vd.get("detail") or "")[:200],
                }
            c = h._live_ws() if hasattr(h, "_live_ws") else None
            if c:
                ctx["desktop_view"] = c.get("desktop_view")
                ctx["feature"] = (c.get("session") or {}).get("feature")
            ctx["ss_virtual_orient"] = str(getattr(h, "ss_virtual_orient", ""))
            ctx["ss_virtual_pos"] = str(getattr(h, "ss_virtual_pos", ""))
        return ctx

    async def poll_once(self) -> StreamSample | None:
        h = self.h
        if h is None:
            return None
        self.polls += 1
        now = time.monotonic()
        self._loop_heartbeat_mono = now
        inp = dict(self.input_events)
        d_in = {k: inp.get(k, 0) - self._input_snap.get(k, 0)
                for k in ("move", "press", "release", "dropped_busy", "suppressed")}
        self._input_snap = inp
        dragging = bool(self._dragging and (now - self._last_drag_mono) < 0.4)

        dt_thread = getattr(h, "desktop_service_thread", None) if getattr(h, "use_desktop_thread", False) else None
        media_lag_ms = (
            round(float(getattr(dt_thread, "loop_lag_ms", 0.0) or 0.0), 1)
            if (dt_thread is not None and dt_thread.is_alive())
            else round(self._loop_lag_ms, 1)
        )
        worst_media_lag_ms = (
            round(float(getattr(dt_thread, "worst_loop_lag_ms", 0.0) or 0.0), 1)
            if (dt_thread is not None and dt_thread.is_alive())
            else round(self._worst_loop_lag_ms, 1)
        )
        sample: StreamSample = StreamSample(
            t=round(now - self.started_mono, 3),
            utc=datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
            loop_lag_ms=round(self._loop_lag_ms, 1),
            worst_loop_lag_ms=round(self._worst_loop_lag_ms, 1),
            media_loop_lag_ms=media_lag_ms,
            worst_media_loop_lag_ms=worst_media_lag_ms,
            desktop_thread_offload=bool(dt_thread is not None and dt_thread.is_alive()),
            dragging=dragging,
            input_rate_per_s={k: round(v / self.interval, 1) for k, v in d_in.items()},
            streams={}, transport={}, outbound={}, local={}, causes=[],
            context=self._session_context(),
        )

        pcs = dict(getattr(h, "_pcs", {}) or {})
        for label, pc in pcs.items():
            try:
                rep = await pc.getStats()
            except Exception:
                self.poll_errors += 1
                continue
            items = list(rep.values()) if hasattr(rep, "values") else list(rep or [])
            for st in items:
                kind = _get(st, "type")
                if kind == "inbound-rtp":
                    self._absorb_inbound(sample, str(label), st)
                elif kind == "outbound-rtp":
                    self._absorb_outbound(sample, str(label), st)
                elif kind in ("candidate-pair", "transport"):
                    self._absorb_transport(sample, str(label), st)

        reg = getattr(h, "stream_registry", None)
        if reg is not None:
            with contextlib.suppress(Exception):
                for sid_k, rec in dict(getattr(reg, "streams", {}) or {}).items():
                    seq = int(getattr(rec, "seq", 0) or 0)
                    prev = self._prev_local.get(sid_k)
                    fps = None if prev is None else round(
                        (seq - prev) / self.interval, 1)
                    self._prev_local[sid_k] = seq
                    if seq:
                        _drawn = int(getattr(rec, "drawn_seq", -1))
                        if _drawn >= 0:
                            _drop = max(0, seq - _drawn)
                        else:
                            _drop = -1
                        _pdrop = self._prev_drops.get(sid_k, 0)
                        self._prev_drops[sid_k] = _drop if _drop >= 0 else _pdrop
                        sample["local"][str(sid_k)] = {
                            "frames": seq, "fps": fps,
                            "ui_dropped": (_drop if _drop >= 0 else None),
                            "ui_dropped_delta": (max(0, _drop - _pdrop)
                                                 if _drop >= 0 else 0),
                            "owner": str(getattr(rec, "owner_service", "")),
                            "status": str(getattr(rec, "status", "")),
                            "w": int(getattr(rec, "width", 0) or 0),
                            "h": int(getattr(rec, "height", 0) or 0),
                        }
                        self.note_frame(sid_k, seq)

        with contextlib.suppress(Exception):
            from samyuj.platform.linux.setup import check_udp_buffer_headroom, read_kernel_udp_drop_counters
            cur_k = read_kernel_udp_drop_counters()
            uh = check_udp_buffer_headroom()
            prev_k = self._prev_kernel_udp
            self._prev_kernel_udp = cur_k
            d_rcvbuf = 0
            d_sock = 0
            d_in_err = 0
            if prev_k is not None:
                d_rcvbuf = max(0, int(cur_k.get("rcvbuf_errors", 0)) - int(prev_k.get("rcvbuf_errors", 0)))
                d_sock = max(0, int(cur_k.get("socket_drops", 0)) - int(prev_k.get("socket_drops", 0)))
                d_in_err = max(0, int(cur_k.get("in_errors", 0)) - int(prev_k.get("in_errors", 0)))
            sample["kernel_udp"] = {
                "rcvbuf_errors_delta": d_rcvbuf,
                "socket_drops_delta": d_sock,
                "in_errors_delta": d_in_err,
                "rmem_max": uh.get("rmem_max"),
                "clamped": bool(uh.get("clamped")),
            }

        with contextlib.suppress(Exception):
            _worst = 0.0
            for _lab, _entries in (sample.get("streams") or {}).items():
                for _e in _entries or []:
                    _worst = max(_worst, float(_e.get("loss_pct") or 0.0))
            _note = getattr(self.h, "note_link_loss", None)
            if callable(_note):
                _note(_worst)

        sample["causes"] = self.classify(sample)
        self._samples.append(sample)
        if sample["causes"]:
            self.log("MEDIA_DIAG",
                     f"t={sample['t']}s lag={sample['loop_lag_ms']}ms "
                     f"drag={sample['dragging']} causes={sample['causes']}")
        return sample

    def _absorb_inbound(self, sample, label, st) -> None:
        if _get(st, "kind") not in (None, "video") and _get(st, "mediaType") != "video":
            pass
        key = f"{label}:{_get(st, 'id', '')}"
        cur = {f: _num(_get(st, f)) for f in _IN_FIELDS}
        prev = self._prev_in.get(key)
        self._prev_in[key] = cur
        d = {}
        if prev:
            for f in ("packetsReceived", "packetsLost", "framesReceived",
                      "framesDecoded", "framesDropped", "keyFramesDecoded",
                      "nackCount", "pliCount", "firCount", "concealmentEvents",
                      "freezeCount", "framesAssembledFromMultiplePackets",
                      "bytesReceived"):
                a, b = cur.get(f), prev.get(f)
                if a is not None and b is not None and a >= b:
                    d[f] = a - b
        recv = d.get("packetsReceived") or 0
        lost = d.get("packetsLost") or 0
        entry = {
            "kind": str(_get(st, "kind") or _get(st, "mediaType") or "?"),
            "codec": str(_get(st, "decoderImplementation") or ""),
            "delta": {k: int(v) for k, v in d.items()},
            "loss_pct": round(100.0 * lost / (recv + lost), 2) if (recv + lost) else 0.0,
            "jitter_ms": round((cur.get("jitter") or 0.0) * 1000.0, 1),
            "fps": (round(d.get("framesDecoded", 0) / self.interval, 1)
                    if (prev and d.get("framesDecoded")) else None),
            "decode_ms_per_frame": (round(1000.0 * (d.get("totalDecodeTime") or 0.0)
                                          / d["framesDecoded"], 2)
                                    if d.get("framesDecoded") else None),
            "w": int(cur.get("frameWidth") or 0), "h": int(cur.get("frameHeight") or 0),
            "kbps": round(8.0 * d.get("bytesReceived", 0) / self.interval / 1000.0, 1),
        }
        sample["streams"].setdefault(label, []).append(entry)

    def _absorb_outbound(self, sample, label, st) -> None:
        key = f"out:{label}:{_get(st, 'id', '')}"
        cur = {f: _num(_get(st, f)) for f in _OUT_FIELDS}
        prev = self._prev_out.get(key)
        self._prev_out[key] = cur
        d = {}
        if prev:
            for f in ("framesEncoded", "framesSent", "qpSum",
                      "retransmittedPacketsSent", "bytesSent"):
                a, b = cur.get(f), prev.get(f)
                if a is not None and b is not None and a >= b:
                    d[f] = a - b
        qp_avg = None
        if d.get("framesEncoded"):
            qp_avg = round(d.get("qpSum", 0) / d["framesEncoded"], 1)
        sample["outbound"].setdefault(label, []).append({
            "kind": str(_get(st, "kind") or "?"),
            "encoder": str(_get(st, "encoderImplementation") or ""),
            "delta": {k: int(v) for k, v in d.items()},
            "fps": round(d.get("framesEncoded", 0) / self.interval, 1) if prev else None,
            "avg_qp": qp_avg,
            "target_bitrate_kbps": round((cur.get("targetBitrate") or 0) / 1000.0, 1),
            "quality_limit": str(_get(st, "qualityLimitationReason") or ""),
            "kbps": round(8.0 * d.get("bytesSent", 0) / self.interval / 1000.0, 1),
        })

    def _absorb_transport(self, sample, label, st) -> None:
        if _get(st, "type") != "candidate-pair" and not _get(st, "currentRoundTripTime"):
            if _get(st, "type") != "transport":
                return
        e = {f: _get(st, f) for f in _PAIR_FIELDS if _get(st, f) is not None}
        if not e:
            return
        with contextlib.suppress(TypeError, ValueError):
            if e.get("currentRoundTripTime") is not None:
                e["rtt_ms"] = round(float(e["currentRoundTripTime"]) * 1000.0, 1)
                e.pop("currentRoundTripTime", None)
            if e.get("availableOutgoingBitrate") is not None:
                e["avail_out_kbps"] = round(float(e["availableOutgoingBitrate"]) / 1000.0, 1)
                e.pop("availableOutgoingBitrate", None)
        sample["transport"].setdefault(label, []).append(e)

    @staticmethod
    def _worst_inbound(sample) -> dict:
        worst = {}
        for _label, entries in (sample.get("streams") or {}).items():
            for e in entries or []:
                for k in ("loss_pct",):
                    if e.get(k, 0) > worst.get(k, 0):
                        worst = e
                if not worst and e.get("delta"):
                    worst = e
        return worst or {}

    def classify(self, sample: dict) -> list:
        out: list = []
        inb = self._worst_inbound(sample)
        d = inb.get("delta") or {}
        loss = float(inb.get("loss_pct") or 0.0)
        recv = int(d.get("packetsReceived") or 0)
        lost = int(d.get("packetsLost") or 0)
        dropped = int(d.get("framesDropped") or 0)
        keys = int(d.get("keyFramesDecoded") or 0)
        nacks = int(d.get("nackCount") or 0)
        plis = int(d.get("pliCount") or 0)
        lag = float(sample.get("loop_lag_ms") or 0.0)
        rtt = 0.0
        for _l, pairs in (sample.get("transport") or {}).items():
            for p in pairs or []:
                rtt = max(rtt, float(p.get("rtt_ms") or 0.0))

        k_udp = sample.get("kernel_udp") or {}
        k_drops = max(int(k_udp.get("rcvbuf_errors_delta") or 0),
                      int(k_udp.get("socket_drops_delta") or 0))
        if k_drops > 0:
            rmem = k_udp.get("rmem_max") or 212992
            out.append(
                f"KERNEL UDP BUFFER DROPS x{k_drops} (rmem_max={rmem}B) - packets "
                f"arrived at this PC but overflowed the Linux UDP socket buffer before "
                f"Python read them; run '!rq fix-udp-buf' or 'sudo sysctl -w "
                f"net.core.rmem_max=8388608 net.core.wmem_max=8388608'"
            )
        if recv or lost:
            if loss >= 2.0:
                where = ("includes local kernel buffer overflow"
                         if k_drops > 0 else "0 kernel buffer drops -> on-air Wi-Fi/link loss")
                out.append(f"NETWORK LOSS {loss:.1f}% ({lost}/{recv + lost} pkts"
                           f"{f', rtt {rtt:.0f} ms' if rtt else ''}, {where}) - references are "
                           f"not arriving; expect macroblocking until the next IDR")
            if nacks >= 5:
                out.append(f"NACK x{nacks} - the jitter buffer is asking for "
                           f"retransmits (loss or reordering on the link)")
        if dropped >= 3 and loss < 2.0:
            out.append(f"FRAMES DROPPED x{dropped} with only {loss:.1f}% loss - "
                       f"decode/display cannot keep up, not a link problem")
        dms = inb.get("decode_ms_per_frame")
        if dms and dms > 25.0:
            out.append(f"DECODE {dms:.1f} ms/frame (>25 ms) - CPU-bound decoding")
        if keys >= 2 and loss < 1.0:
            out.append(f"KEYFRAMES x{keys} with ~no loss - the phone's ENCODER is "
                       f"restarting (bitrate/REMB swings), each IDR is a visible pop")
        if lag > 60.0:
            out.append(f"EVENT-LOOP LAG {lag:.0f} ms - our own asyncio loop was "
                       f"starved, so RTP/RTCP was serviced late (this alone can "
                       f"look exactly like packet loss)")
        if plis >= 3:
            out.append(f"PLI x{plis} - we are begging for keyframes; if loss is "
                       f"low the frames are being discarded locally, not lost")
        ui_drop = 0
        for _sid, loc in (sample.get("local") or {}).items():
            if loc.get("ui_dropped") is not None:
                ui_drop += int(loc.get("ui_dropped_delta") or 0)
        if ui_drop >= 3 and loss < 2.0:
            out.append(f"UI DROPPED {ui_drop} decoded frame(s) this second with only "
                       f"{loss:.1f}% loss - the GTK draw path cannot keep up; the "
                       f"picture is fine on the wire, so this is a RENDERER problem, "
                       f"not a network one")
        if sample.get("dragging") and out:
            out.append("...and this sample was DURING A DRAG - compare against "
                       "samples with dragging=false to confirm the input link")
        return out

    def samples(self, n: int | None = None) -> list:
        items = list(self._samples)
        return items[-int(n):] if n else items

    def summary(self, window: int = 120) -> dict:
        rows_all = self.samples(window)
        rows = [r for r in rows_all
                if (r.get("streams") or r.get("local"))]
        idle_no_stream = len(rows_all) - len(rows)
        stall_block = {
            "loop_stalls": len(self._stall_dumps),
            "loop_stall_worst_s": (round(max(d["duration_s"] for d in self._stall_dumps), 3)
                                   if self._stall_dumps else 0.0),
            "loop_stall_dumps": list(self._stall_dumps)[-6:],
        }
        if not rows:
            return {"samples": 0, "window_s": 0,
                    "note": "no WebRTC samples yet - is a stream running?",
                    "stalls": len(self._stalls),
                    "recent_stalls": list(self._stalls)[-12:],
                    "cause_histogram": {}, **stall_block}
        drag = [r for r in rows if r.get("dragging")]
        idle = [r for r in rows if not r.get("dragging")]

        def _agg(rs):
            if not rs:
                return None
            losses, lags, drops, keys, plis, nacks, fps, kdrops = [], [], [], [], [], [], [], []
            for r in rs:
                lags.append(float(r.get("loop_lag_ms") or 0.0))
                inb = self._worst_inbound(r)
                dd = inb.get("delta") or {}
                ku = r.get("kernel_udp") or {}
                losses.append(float(inb.get("loss_pct") or 0.0))
                drops.append(int(dd.get("framesDropped") or 0))
                keys.append(int(dd.get("keyFramesDecoded") or 0))
                plis.append(int(dd.get("pliCount") or 0))
                nacks.append(int(dd.get("nackCount") or 0))
                kdrops.append(max(int(ku.get("rcvbuf_errors_delta") or 0),
                                  int(ku.get("socket_drops_delta") or 0)))
                if inb.get("fps") is not None:
                    fps.append(float(inb["fps"]))
            return {
                "samples": len(rs),
                "loss_pct_mean": round(statistics.fmean(losses), 2),
                "loss_pct_max": round(max(losses), 2),
                "loop_lag_ms_mean": round(statistics.fmean(lags), 1),
                "loop_lag_ms_max": round(max(lags), 1),
                "frames_dropped_total": sum(drops),
                "keyframes_total": sum(keys),
                "pli_total": sum(plis),
                "nack_total": sum(nacks),
                "kernel_udp_drops_total": sum(kdrops),
                "fps_mean": round(statistics.fmean(fps), 1) if fps else None,
            }

        causes: dict = {}
        for r in rows:
            for c in (r.get("causes") or self.classify(r)):
                head = c.split(" - ")[0][:60]
                causes[head] = causes.get(head, 0) + 1
        return {
            "window_s": round(len(rows) * self.interval),
            "samples_total": len(rows_all),
            "samples_with_no_stream": idle_no_stream,
            "during_drag": _agg(drag),
            "idle": _agg(idle),
            "stalls": len(self._stalls),
            "recent_stalls": list(self._stalls)[-12:],
            **stall_block,
            "cause_histogram": dict(sorted(causes.items(), key=lambda kv: -kv[1])),
            "polls": self.polls,
            "poll_errors": self.poll_errors,
        }

    def verdict(self) -> str:
        s = self.summary()
        lines = []
        ctxs = [r.get("context") or {} for r in self.samples(120)]
        ss = [c for c in ctxs if c.get("ss_share_type") == "extend"]
        if ss:
            last = ss[-1]
            vd = last.get("virtual_display") or {}
            if not vd.get("ok"):
                lines.append(
                    "EXTEND IS NOT ACTIVE: share mode is 'extend' but no virtual "
                    f"display was created ({last.get('ss_extend_fallback') or 'no backend'})"
                    f" - capture={last.get('ss_capture') or '?'}, so the phone is "
                    f"being shown the PRIMARY monitor, i.e. it looks like mirroring.")
            elif vd.get("capture_verified") is False:
                lines.append(
                    "EXTEND CREATED BUT UNUSABLE: the virtual display exists but "
                    f"captures as a blank slab - {vd.get('detail')} - so the app "
                    "fell back to the primary monitor. That is why it still looks "
                    "like mirroring.")
            elif not vd.get("attached"):
                lines.append(
                    f"EXTEND ACTIVE but NOT pointer-traversable ({vd.get('backend')} "
                    f"'{vd.get('position')}'): the framebuffer grew, yet the "
                    "compositor did not keep the output-less monitor, so the mouse "
                    "cannot cross onto it.")
            else:
                lines.append(
                    f"EXTEND ACTIVE and verified: {vd.get('backend')} "
                    f"{vd.get('size')} at the {vd.get('position')} edge, "
                    f"capture={last.get('ss_capture')}.")
        if s.get("loop_stalls"):
            dumps = s.get("loop_stall_dumps") or []
            lines.append(
                f"*** {s['loop_stalls']} EVENT-LOOP FREEZE(S), worst "
                f"{s.get('loop_stall_worst_s', 0.0):.2f} s. While frozen nothing "
                f"services RTP/RTCP: packets are dropped by the kernel socket "
                f"buffer and the decoder loses its reference frames, which renders "
                f"exactly as 'scratched DVD' macroblocking until the next IDR. "
                f"The watchdog captured what was blocking:")
            for d in dumps[-3:]:
                top = (d.get("stacks") or ["?"])[0]
                lines.append(f"    {d['duration_s']:.2f}s at t={d['t']}s -> {top}")
        if not self._samples:
            if lines:
                lines.append("(no WebRTC samples in this window - start Mobile "
                             "Desktop / Share Screen to collect them)")
                return "\n".join(lines)
            return ("no samples yet - start Mobile Desktop / Share Screen and let "
                    "it run for ~30 s, then re-run !rq media-diag")
        drag, idle = s.get("during_drag"), s.get("idle")
        if drag and idle and drag.get("samples", 0) >= 3 and idle.get("samples", 0) >= 3:
            if drag["loss_pct_max"] > idle["loss_pct_max"] + 1.0 or \
               drag["loop_lag_ms_max"] > idle["loop_lag_ms_max"] + 40:
                lines.append(
                    f"CORRELATES WITH INPUT: while dragging, loss peaks at "
                    f"{drag['loss_pct_max']}% and loop lag at {drag['loop_lag_ms_max']:.0f} ms, "
                    f"versus {idle['loss_pct_max']}% / {idle['loop_lag_ms_max']:.0f} ms idle.")
            else:
                lines.append(
                    f"NOT input-correlated: dragging ({drag['loss_pct_max']}% loss, "
                    f"{drag['loop_lag_ms_max']:.0f} ms lag) is no worse than idle "
                    f"({idle['loss_pct_max']}% / {idle['loop_lag_ms_max']:.0f} ms) - "
                    f"look at the link or the encoder instead.")
        elif drag or idle:
            have = "dragging" if drag else "idle"
            lines.append(
                f"only {have} seconds had a live stream in this window "
                f"({(drag or idle).get('samples', 0)} samples), so the drag-vs-idle "
                f"comparison is not meaningful yet - reproduce the corruption while "
                f"moving things AND while leaving it alone for ~30 s each.")
        top = list((s.get("cause_histogram") or {}).items())[:3]
        if top:
            lines.append("Most frequent findings: "
                         + "; ".join(f"{k} (x{v})" for k, v in top) + ".")
        if s.get("stalls"):
            lines.append(f"{s['stalls']} frame-arrival stall(s) detected in our own "
                         f"pipeline; last: {s['recent_stalls'][-1] if s['recent_stalls'] else '-'}")
        if not (s.get("cause_histogram") or {}) and not s.get("loop_stalls"):
            agg = drag or idle or {}
            lines.append(
                f"NO ANOMALIES in the last {s.get('window_s', 0)} s "
                f"({agg.get('samples', 0)} samples): packet loss peaked at "
                f"{agg.get('loss_pct_max', 0)}%, event-loop lag at "
                f"{agg.get('loop_lag_ms_max', 0)} ms, {agg.get('frames_dropped_total', 0)} "
                f"frames dropped, {agg.get('keyframes_total', 0)} keyframes, "
                f"{agg.get('pli_total', 0)} PLIs. If the picture still looked "
                f"corrupt in that window, the loss is happening where we cannot "
                f"see it (the phone's encoder, or the compositor) - capture a run "
                f"WHILE the corruption is on screen.")
        return " ".join(lines)

    def write_report(self, path: Path | str | None = None) -> Path:
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        base = Path(path) if path else None
        if base is None:
            d = self.report_dir or Path.cwd()
            with contextlib.suppress(OSError):
                d.mkdir(parents=True, exist_ok=True)
            base = d / f"media_diagnostics_{stamp}"
        base = Path(base)
        rows = self.samples()
        payload = {
            "generated": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
            "interval_s": self.interval,
            "samples": len(rows),
            "summary": self.summary(window=len(rows) or 120),
            "verdict": self.verdict(),
            "input_events": dict(self.input_events),
            "data": rows,
        }
        jpath = base.with_suffix(".json")
        jpath.write_text(json.dumps(payload, separators=(",", ":"), default=str),
                         encoding="utf-8")
        cpath = base.with_suffix(".csv")
        with contextlib.suppress(Exception):
            import csv as _csv
            cols = ["t", "dragging", "loop_lag_ms", "worst_loop_lag_ms",
                    "pkts_rx", "pkts_lost", "loss_pct", "jitter_ms", "fps_codec",
                    "fps_local", "ui_dropped", "keyframes", "pli", "nack",
                    "rtt_ms", "ss_share_type", "ss_capture", "vd_backend",
                    "vd_ok", "vd_attached", "vd_verified", "vd_position",
                    "causes"]
            with cpath.open("w", newline="", encoding="utf-8") as fh:
                wr = _csv.DictWriter(fh, fieldnames=cols)
                wr.writeheader()
                for r in rows:
                    inb = self._worst_inbound(r)
                    dd = inb.get("delta") or {}
                    loc = next(iter((r.get("local") or {}).values()), {}) or {}
                    ctx = r.get("context") or {}
                    vd = ctx.get("virtual_display") or {}
                    rtt = 0.0
                    for _l, pairs in (r.get("transport") or {}).items():
                        for pp in pairs or []:
                            rtt = max(rtt, float(pp.get("rtt_ms") or 0.0))
                    wr.writerow({
                        "t": r.get("t"), "dragging": int(bool(r.get("dragging"))),
                        "loop_lag_ms": r.get("loop_lag_ms"),
                        "worst_loop_lag_ms": r.get("worst_loop_lag_ms"),
                        "pkts_rx": int(dd.get("packetsReceived") or 0),
                        "pkts_lost": int(dd.get("packetsLost") or 0),
                        "loss_pct": inb.get("loss_pct", 0.0),
                        "jitter_ms": inb.get("jitter_ms"),
                        "fps_codec": inb.get("fps"),
                        "fps_local": loc.get("fps"),
                        "ui_dropped": loc.get("ui_dropped_delta"),
                        "keyframes": int(dd.get("keyFramesDecoded") or 0),
                        "pli": int(dd.get("pliCount") or 0),
                        "nack": int(dd.get("nackCount") or 0),
                        "rtt_ms": rtt,
                        "ss_share_type": ctx.get("ss_share_type", ""),
                        "ss_capture": ctx.get("ss_capture", ""),
                        "vd_backend": vd.get("backend", ""),
                        "vd_ok": int(bool(vd.get("ok"))),
                        "vd_attached": int(bool(vd.get("attached"))),
                        "vd_verified": ("" if vd.get("capture_verified") is None
                                        else int(bool(vd.get("capture_verified")))),
                        "vd_position": vd.get("position", ""),
                        "causes": " | ".join(r.get("causes") or []),
                    })
        mpath = base.with_suffix(".md")
        md = [f"# Media diagnostics - {payload['generated']}", "",
              f"{len(rows)} samples at {self.interval}s intervals "
              f"({payload['summary'].get('window_s', 0)} s window).", "",
              "## Verdict", "", payload["verdict"], "",
              "## Event-loop freezes (the blocking stacks)", "",
              *(["```"] + [f"{d['duration_s']:.2f}s at t={d['t']}s"
                           for dd in (payload["summary"].get("loop_stall_dumps") or [])
                           for d in [dd]] + ["```", ""]
                if payload["summary"].get("loop_stalls") else
                ["None detected in this window.", ""]),
              "```text",
              *[line for d in (payload["summary"].get("loop_stall_dumps") or [])
                for line in d.get("stacks") or []],
              "```", "",
              "## Summary", "", "```json",
              json.dumps(payload["summary"], indent=1, default=str), "```", "",
              "## How to read this", "",
              "* `loss_pct` rising -> packets are not arriving (network).",
              "* `loop_lag_ms` spiking -> OUR asyncio loop was too busy to service "
              "RTP/RTCP; looks identical to loss but is fixed by moving work off "
              "the media loop.",
              "* `framesDropped` with low loss -> decode/display cannot keep up.",
              "* `keyFramesDecoded` frequent with no loss -> the phone's encoder is "
              "restarting (bitrate swings); each IDR is a visible pop.",
              "* compare `during_drag` vs `idle` - that is the input question.",
              ""]
        md += ["## Files", "",
               f"* `{jpath.name}` - compact JSON, everything (samples, summary, "
               f"loop-stall stacks)",
               f"* `{mpath.name}` - this summary",
               f"* `{cpath.name}` - flat CSV, one row per second: "
               f"loss, lag, fps, drops, share mode and virtual-display state. "
               f"Open this one first.", ""]
        mpath.write_text("\n".join(md), encoding="utf-8")
        self.log("MEDIA_DIAG_REPORT",
                 f"wrote {jpath} + {mpath} + {cpath} ({len(rows)} samples)")
        return jpath

