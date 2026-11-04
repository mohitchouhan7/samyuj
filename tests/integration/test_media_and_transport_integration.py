from __future__ import annotations

import asyncio
import tempfile
import unittest
from typing import Any

import av

from samyuj.core.settings import UserSettings
from samyuj.core.tasks import _bind_pc_to_media_loop, get_mobile_desktop_service_thread
from samyuj.files.nfs.exports import MultiShareExport, ShareEntry
from samyuj.files.phone_browser import (
    PhoneFileEntry,
    PhoneFileWidgetConfig,
    PhoneFileWidgetController,
    PhoneFileWidgetResult,
)
from samyuj.files.transfer import TransferJob
from samyuj.media.capture import ScreenShareTrack
from samyuj.media.frame_hub import (
    RENDER_MAX_UPSCALE,
    RENDER_PIXEL_BUDGET,
    MediaStreamRegistry,
    StreamRecord,
    _plane_copy,
    apply_uv_swap_state,
    encode_jpeg,
    get_prepared_frame_bgrx,
    is_uv_swapped,
    swap_uv,
    target_render_size,
)
from samyuj.media.runtime import MediaRuntime
from samyuj.media.v4l2.setup import V4L2DeviceCapability, V4L2SetupDiagnosis
from samyuj.media.webrtc_adapter import (
    AdaptiveRemoteBitrateEstimator,
    FastH264Encoder,
    FastVp8Encoder,
    GatedVp8Decoder,
    ImmediateMarkerJitterBuffer,
    LowLatencyH264Decoder,
    SamyujPeerConnection,
    SamyujRtpReceiver,
    StormSafeNackGenerator,
)
from samyuj.session.lifecycle import SamyujRuntime
from samyuj.transport.network import UsbConfigState

class FakeVideoTrack:

    kind = "video"
    id = "fake-video-track-0"

    def __init__(self, count: int = 3, width: int = 640, height: int = 360) -> None:
        self.remaining = count
        self.width = width
        self.height = height
        self._seq = 0

    async def recv(self) -> av.VideoFrame:
        if self.remaining <= 0:
            await asyncio.sleep(0.02)
            raise ConnectionError("track ended")
        self.remaining -= 1
        self._seq += 1
        frame = av.VideoFrame(self.width, self.height, "yuv420p")
        y_len = frame.planes[0].line_size * self.height
        u_len = frame.planes[1].line_size * (self.height // 2)
        v_len = frame.planes[2].line_size * (self.height // 2)
        frame.planes[0].update(bytes([128]) * y_len)
        frame.planes[1].update(bytes([64]) * u_len)
        frame.planes[2].update(bytes([192]) * v_len)
        frame.pts = self._seq * 3000
        return frame

class MediaAndTransportIntegrationTest(unittest.IsolatedAsyncioTestCase):

    def test_all_nine_dataclasses_instantiate_cleanly(self) -> None:
        rec = StreamRecord(
            stream_id="desktop:1",
            owner_service="desktop",
            service_id=1,
            session_id=1,
            track_id="t1",
            direction="inbound",
        )
        self.assertEqual(rec.stream_id, "desktop:1")
        self.assertEqual(rec.status, "negotiating")

        with tempfile.TemporaryDirectory() as td:
            se = ShareEntry(share_id="s1", alias="Docs", host_path=td, access_mode="rw")
            self.assertEqual(se.alias, "Docs")

        cfg = PhoneFileWidgetConfig(widget_id="w1", source_id="gallery")
        ent = PhoneFileEntry(name="IMG_1.jpg", remote_path="/DCIM/IMG_1.jpg", is_dir=False, size=1024, mtime=1.0, file_kind="image")
        res = PhoneFileWidgetResult(
            widget_id="w1",
            source_id="gallery",
            title="Gallery",
            remote_path="/DCIM",
            view_mode="grid",
            sort_by="date",
            sort_desc=True,
            size_mode="expanded",
            status="ready",
            user_message="Ready",
            next_step_hint="",
            entries=[],
            transfer_summary={},
        )
        self.assertEqual(cfg.widget_id, "w1")
        self.assertEqual(ent.file_kind, "image")
        self.assertEqual(res.status, "ready")

        job = TransferJob(
            job_id="j1",
            direction="pc_to_phone",
            channel="fileex_svc2",
            filename="a.txt",
            source_path="/tmp/a.txt",
            dest_path="/sdcard/Download/a.txt",
        )
        self.assertEqual(job.job_id, "j1")

        cap = V4L2DeviceCapability(device_path="/dev/video10")
        diag = V4L2SetupDiagnosis(
            status="ready",
            module_installed=True,
            module_loaded=True,
            secure_boot_enabled=False,
            usable_devices=[],
            selected_target="auto",
            resolved_device="/dev/video10",
            user_message="Ready",
            setup_command_hint="",
        )
        self.assertIsInstance(diag, V4L2SetupDiagnosis)
        self.assertEqual(cap.device_path, "/dev/video10")

        usb_st = UsbConfigState(iface="usb0")
        self.assertEqual(usb_st.iface, "usb0")

    def test_render_pixel_budget_and_no_upscale(self) -> None:
        self.assertEqual(RENDER_MAX_UPSCALE, 1.0)
        self.assertEqual(RENDER_PIXEL_BUDGET, 4_200_000)

        tw, th = target_render_size(1280, 720, 2560, 1440)
        self.assertEqual((tw, th), (1280, 720))

        tw4k, th4k = target_render_size(3840, 2160, 3840, 2160)
        self.assertLessEqual(tw4k * th4k, RENDER_PIXEL_BUDGET + 8000)

    def test_swap_uv_copy_on_swap_never_mutates_dpb_in_place(self) -> None:
        frame = av.VideoFrame(64, 64, "yuv420p")
        y_len = frame.planes[0].line_size * 64
        u_len = frame.planes[1].line_size * 32
        v_len = frame.planes[2].line_size * 32
        orig_u = bytes([42]) * u_len
        orig_v = bytes([210]) * v_len
        frame.planes[0].update(bytes([100]) * y_len)
        frame.planes[1].update(orig_u)
        frame.planes[2].update(orig_v)

        swapped = swap_uv(frame)
        self.assertIsNot(swapped, frame)
        self.assertTrue(is_uv_swapped(swapped))
        self.assertFalse(is_uv_swapped(frame))

        self.assertEqual(bytes(frame.planes[1])[:u_len], orig_u)
        self.assertEqual(bytes(frame.planes[2])[:v_len], orig_v)

        self.assertEqual(bytes(swapped.planes[1])[:u_len], orig_v[:u_len])
        self.assertEqual(bytes(swapped.planes[2])[:v_len], orig_u[:v_len])

        f_on, state_on = apply_uv_swap_state(frame, True)
        self.assertTrue(state_on)
        self.assertTrue(is_uv_swapped(f_on))
        f_on2, state_on2 = apply_uv_swap_state(f_on, True)
        self.assertIs(f_on2, f_on)
        self.assertTrue(state_on2)

        f_off, state_off = apply_uv_swap_state(f_on, False)
        self.assertFalse(state_off)
        self.assertFalse(is_uv_swapped(f_off))

        jpg = encode_jpeg(swapped)
        self.assertIsNotNone(jpg)
        assert jpg is not None
        self.assertEqual(jpg[:2], b"\xff\xd8")

    async def test_subclassed_webrtc_adapter_and_safe_close(self) -> None:
        pc = SamyujPeerConnection()
        try:
            tr = pc.addTransceiver("video", direction="recvonly")
            self.assertIsInstance(tr.receiver, SamyujRtpReceiver)
            jb = getattr(tr.receiver, "_RTCRtpReceiver__jitter_buffer", None)
            nack = getattr(tr.receiver, "_RTCRtpReceiver__nack_generator", None)
            rbe = getattr(tr.receiver, "_RTCRtpReceiver__remote_bitrate_estimator", None)
            self.assertIsInstance(jb, ImmediateMarkerJitterBuffer)
            self.assertEqual(jb._capacity, 512)
            self.assertIsInstance(nack, StormSafeNackGenerator)
            self.assertIsInstance(rbe, AdaptiveRemoteBitrateEstimator)

            h264_dec = LowLatencyH264Decoder()
            vp8_dec = GatedVp8Decoder()
            h264_enc = FastH264Encoder()
            vp8_enc = FastVp8Encoder()
            self.assertIsNotNone(h264_dec)
            self.assertIsNotNone(vp8_dec)
            self.assertIsNotNone(h264_enc)
            self.assertIsNotNone(vp8_enc)
        finally:
            await pc.close()

    async def test_media_pump_end_to_end_on_desktop_service_thread(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sc-pump-") as td:
            rt = SamyujRuntime(host="127.0.0.1", port=0, out_dir=td, state_dir=td, media=True)
            self.assertIsInstance(rt.media_runtime, MediaRuntime)
            self.assertIsInstance(rt.settings_model, UserSettings)
            conn: dict[str, Any] = {
                "id": 1,
                "closed": False,
                "desktop_started": True,
                "as_pkg": "desktop",
                "tasks": [],
                "session": {"feature": "desktop"},
            }
            rt.set_stream_target_size("desktop", 640, 360, sid=101)
            track = FakeVideoTrack(count=3, width=640, height=360)
            await rt._media_pump(conn, track, sid=101)

            rec = rt.stream_registry.get("desktop:101")
            self.assertIsNotNone(rec)
            assert rec is not None
            self.assertEqual(rec.frames, 3)
            self.assertEqual(rec.seq, 3)
            self.assertEqual(rec.width, 640)
            self.assertEqual(rec.height, 360)
            self.assertIsNotNone(rec.last_frame)
            prep = get_prepared_frame_bgrx(rec.last_frame)
            self.assertIsNotNone(prep)
            assert prep is not None
            self.assertEqual(prep["tw"], 640)
            self.assertEqual(prep["th"], 360)

    async def test_screenshare_negotiate_and_extend_mode_preservation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sc-ss-") as td:
            rt = SamyujRuntime(host="127.0.0.1", port=0, out_dir=td, state_dir=td, media=True)
            rt.ss_share_type = "extend"
            ss_track = ScreenShareTrack(width=640, height=360, fps=15.0, source="pattern")
            frame = await ss_track.recv()
            self.assertEqual(frame.width, 640)
            self.assertEqual(frame.height, 360)

            cat = rt.service_bus.catalog()
            self.assertEqual(len(cat), 20)
            for item in cat:
                self.assertIn("id", item)
                self.assertIn("state", item)

    async def test_usb_wifi_auto_switching_and_regex_detection(self) -> None:
        from samyuj.transport.discovery.usb import USB_IFACE_RE, find_tether_link
        from samyuj.session.models import new_session_state

        self.assertTrue(bool(USB_IFACE_RE.match("usb0")))
        self.assertTrue(bool(USB_IFACE_RE.match("enx123456abcdef")))
        self.assertTrue(bool(USB_IFACE_RE.match("enp2s0f0u1")))
        self.assertFalse(bool(USB_IFACE_RE.match("wlan0")))

        fake_net = {
            "wlan0": {"operstate": "up", "ipv4": [("192.0.2.10", 24)], "usb_device": None},
            "enp2s0f0u1": {"operstate": "up", "ipv4": [("192.168.201.162", 24)], "usb_device": "1-1"},
        }
        self.assertEqual(find_tether_link(fake_net), ("enp2s0f0u1", "192.168.201.162"))

        with tempfile.TemporaryDirectory(prefix="sc-usb-") as td:
            rt = SamyujRuntime(host="127.0.0.1", port=0, out_dir=td, state_dir=td, media=True)
            rt.note_phone(real_id="moto_r1", nonce_id="n1", name="Moto", key="k1", ip="192.0.2.184", ws_port=6466)
            rt.note_phone(real_id="moto_r1", nonce_id="n1", name="Moto", key="k1", ip="192.168.201.161", ws_port=6466)
            reg = rt.load_paired_phones()["moto_r1"]
            self.assertEqual(reg["last_wifi_ip"], "192.0.2.184")
            self.assertEqual(reg["last_usb_ip"], "192.168.201.161")
            self.assertEqual(reg["last_ip"], "192.0.2.184")

            rt.usb_link = None
            rt.discovered_phones = [{"deviceId": "n1", "real_id": "moto_r1", "ips": ["192.168.201.161", "192.0.2.184"], "port": 6466}]
            disc = rt.latest_phone_discovery()
            self.assertEqual(disc["ips"], ["192.0.2.184"])

            class _FakeWriter:
                def __init__(self, peer_ip: str, local_ip: str):
                    self._p = (peer_ip, 51000)
                    self._s = (local_ip, 8443)
                    self.closed = False

                def get_extra_info(self, k, default=None):
                    return {"peername": self._p, "sockname": self._s}.get(k, default)

                def close(self):
                    self.closed = True

            wifi_conn = {
                "id": 1,
                "closed": False,
                "ws": True,
                "tunnel": False,
                "role": "control",
                "readiness": "paired",
                "writer": _FakeWriter("192.0.2.184", "192.0.2.10"),
                "remote": "('192.0.2.184', 51000)",
                "desktop_started": True,
                "as_session_id": 77,
                "as_display_id": 2,
                "as_geom": (1920, 1080),
                "session": new_session_state(),
                "log": type("L", (), {"line": lambda *a, **k: None, "close": lambda *a: None})(),
            }
            wifi_conn["session"]["feature"] = "desktop"
            usb_conn = {
                "id": 2,
                "closed": False,
                "ws": True,
                "tunnel": False,
                "role": "control",
                "readiness": "paired",
                "writer": _FakeWriter("192.168.201.161", "192.168.201.162"),
                "remote": "('192.168.201.161', 51001)",
                "desktop_started": False,
                "session": new_session_state(),
                "log": type("L", (), {"line": lambda *a, **k: None, "close": lambda *a: None})(),
            }
            rt.usb_link = ("usb0", "192.168.201.162")
            rt._conns = [wifi_conn, usb_conn]
            rt._usb_takeover = {"since": 0.0, "wifi_conn_id": 1}
            dummy_pc = SamyujPeerConnection()
            rt._pcs[77] = dummy_pc
            try:
                await rt.check_transport_switch()
                self.assertTrue(wifi_conn["closed"])
                self.assertTrue(wifi_conn.get("_session_migrated"))
                self.assertFalse(usb_conn["closed"])
                self.assertTrue(usb_conn["desktop_started"])
                self.assertEqual(usb_conn["as_session_id"], 77)
                await rt._release_local_sessions(wifi_conn)
                self.assertIn(77, rt._pcs)

                rt.note_phone(real_id="moto_r1", nonce_id="n1", name="Moto", key="k1", ip="192.0.2.184", ws_port=11775)
                rt.discovered_phones = [{"deviceId": "n1", "real_id": "moto_r1", "ips": ["192.0.2.184"], "port": 11775}]
                rt._invalidate_phone_ws_port("192.0.2.184", 11775)
                self.assertIsNone(rt.latest_phone_discovery())
                self.assertNotIn("last_ws_port", rt.load_paired_phones()["moto_r1"])

                from samyuj.transport.discovery.mdns import UsbMdnsAdvertiser
                adv = UsbMdnsAdvertiser(rt)
                adv.instance = "testpc"
                adv.hostname = "testpc-pc.local."
                adv.nonce_id = "n1"
                resp_pkt = adv._packet_for_mode("wifi", [("_readyformdns._tcp.local", 12)])
                self.assertEqual(int.from_bytes(resp_pkt[4:6], "big"), 0)
            finally:
                await dummy_pc.close()
                rt._pcs.clear()

    async def test_scroll_wheel_consistency_and_zero_fling_horizon(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sc-scroll-") as td:
            rt = SamyujRuntime(host="127.0.0.1", port=0, out_dir=td, state_dir=td, media=True)
            rt.use_desktop_thread = False
            events: list[tuple[int, float, float, float]] = []

            async def _record_touch(conn, event: int, xf: float, yf: float, **kw) -> bool:
                import time as _t
                events.append((int(event), round(float(xf), 4), round(float(yf), 4), _t.monotonic()))
                return True

            rt.send_touch_frac = _record_touch
            conn = {"id": 1, "closed": False, "as_display_id": 2, "as_geom": (1920, 1080), "tasks": []}
            ok = await rt.send_stream_scroll(conn, 0.5, 0.5, dy=1.0, duration_ms=60, steps=4, hold_ms=50)
            self.assertTrue(ok)
            self.assertEqual(len(events), 6)
            self.assertEqual([e[0] for e in events], [1, 2, 2, 2, 2, 0])
            self.assertAlmostEqual(events[3][2], events[4][2], places=4)
            self.assertAlmostEqual(events[4][2], events[5][2], places=4)
            self.assertAlmostEqual(events[0][2] - events[5][2], 0.12, places=4)
