from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from samyuj.core.logging import ConnLog
from samyuj.session.lifecycle import SamyujRuntime
from samyuj.session.models import PeerSession, new_session_state
from samyuj.session.registry import ServiceSessionRegistry
from samyuj.transport.websocket import WsFrameParser

class FakeStreamWriter:

    def __init__(self, peer_ip: str = "192.0.2.50") -> None:
        self.buffer = bytearray()
        self.parser = WsFrameParser()
        self.sent_frames: list[dict[str, Any]] = []
        self.peer_ip = peer_ip
        self.closed = False

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)
        for opcode, _fin, payload in self.parser.feed(data):
            if opcode == 0x1:
                self.sent_frames.append(json.loads(payload.decode("utf-8")))

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None

    def get_extra_info(self, name: str, default: Any = None) -> Any:
        if name == "peername":
            return (self.peer_ip, 45678)
        return default

class ServicesBehaviorTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="sc-svc-test-")
        self.runtime = SamyujRuntime(
            host="127.0.0.1",
            port=0,
            out_dir=self.tmp.name,
            state_dir=self.tmp.name,
            media=False,
        )
        self.runtime.device_id_b64 = "dGVzdC1wYy1kZXZpY2UtaWQ="
        self.runtime.fp_b64 = "ZmFrZS1mcC1iNjQ="
        self.runtime.token_b64 = "dGVzdC1zZXNzaW9uLXRva2Vu"
        self.writer = FakeStreamWriter()
        self.conn = PeerSession(
            {
                "id": 1,
                "reader": None,
                "writer": self.writer,
                "remote": "('192.0.2.50', 45678)",
                "mode": "ws",
                "ws": True,
                "replied": True,
                "closed": False,
                "log": ConnLog(None, disk_enabled=False),
                "parser": WsFrameParser(),
                "session": new_session_state(),
                "sessions": ServiceSessionRegistry(),
                "hello": {
                    "msgId": 1,
                    "deviceId": "cGhvbmUtbm9uY2UtaWQ=",
                    "serviceId": 0,
                    "data": {
                        "cmd": 0,
                        "device": {
                            "deviceId": "cGhvbmUtbm9uY2UtaWQ=",
                            "realDeviceId": "cGhvbmUtcmVhbC1pZDAx",
                            "deviceName": "Test Phone C",
                        },
                    },
                },
            }
        )
        self.runtime._conns.append(self.conn)

    async def asyncTearDown(self) -> None:
        self.tmp.cleanup()

    async def _feed_inbound(self, service_id: int, data: Any, src_service_id: int | None = None) -> None:
        env = {
            "msgId": 10,
            "deviceId": "cGhvbmUtbm9uY2UtaWQ=",
            "serviceId": service_id,
            "srcServiceId": src_service_id if src_service_id is not None else service_id,
            "data": data,
        }
        await self.runtime._on_ws_text(self.conn, json.dumps(env))

    async def test_service_bus_catalog_and_no_audio_service(self) -> None:
        catalog = self.runtime.service_bus.catalog()
        ids = {entry["id"] for entry in catalog}
        self.assertNotIn("audio", ids)
        self.assertIn("pairing", ids)
        self.assertIn("mobiledesktop", ids)
        self.assertIn("appstream", ids)
        self.assertIn("webcam", ids)
        self.assertIn("screenshare", ids)
        self.assertIn("clipboard", ids)
        self.assertIn("notifications", ids)
        self.assertIn("messages", ids)
        self.assertIn("gallery", ids)
        self.assertIn("control", ids)

    async def test_pairing_and_control_telemetry_services(self) -> None:
        await self._feed_inbound(19, {"source": "2", "connected": ["cGhvbmUtcmVhbC1pZDAx"]})
        self.assertTrue(self.runtime.pair_service_state["connected"])

        await self._feed_inbound(22, {"ver": "1", "cmd": 1})
        self.assertEqual(self.runtime.control_state["invite"]["count"], 1)

        await self._feed_inbound(25, {"cmd": 1, "battery": 84, "isCharging": True, "deviceName": "Test Phone C"})
        self.assertEqual(self.runtime.phone_telemetry.get("battery"), 84)

    async def test_clipboard_and_notifications_services(self) -> None:
        await self._feed_inbound(9, {"msgId": 3, "data": {"clipId": 7, "text": "phone-clip-text"}})
        self.assertEqual(self.runtime.clip_last_text, "phone-clip-text")

        await self._feed_inbound(
            5,
            {
                "cmd": 0,
                "key": "notif-1",
                "event": 0,
                "data": {"appName": "Messages", "title": "Alice", "text": "See you at 5"},
            },
        )
        self.assertTrue(any(n.get("key") == "notif-1" for n in self.runtime.notifications_list))

    async def test_messages_and_gallery_features_services(self) -> None:
        await self._feed_inbound(
            204,
            {
                "ver": "4.0",
                "pId": 110,
                "cmd": 2,
                "data": {"contactBeanList": [{"id": "c1", "name": "Bob", "number": "+15550101"}]},
            },
        )
        self.assertEqual(len(self.runtime.messages_state["contacts"]), 1)

        await self._feed_inbound(
            204,
            {
                "ver": "4.0",
                "pId": 110,
                "cmd": 11,
                "data": {
                    "messageBeanList": [
                        {
                            "msgId": 501,
                            "fromAddress": "+15550101",
                            "toAddress": ["+15550199"],
                            "body": "Hello from phone",
                            "date": 1700000000000,
                            "read": 0,
                            "messageType": 1,
                        }
                    ]
                },
            },
        )
        self.assertEqual(len(self.runtime.messages_state["messages"]), 1)

        res_sms = await self.runtime.service_bus.invoke(
            "messages", "send_sms", {"to": "+15550101", "body": "Reply from Linux"}, self.conn
        )
        self.assertTrue(res_sms.get("ok"))

        self.runtime.app_running = [{"pkg": "org.mozilla.firefox", "userId": 0}]
        await self._feed_inbound(1001, {"ver": "1", "cmd": 8, "deviceId": "cGhvbmUtcmVhbC1pZDAx"}, src_service_id=19)
        self.assertTrue(any(f.get("srcServiceId") == 1001 for f in self.writer.sent_frames))

    async def test_cross_control_webcam_screenshare_desktop_appstream(self) -> None:
        await self._feed_inbound(26, {"cmd": 2, "cid": 10, "code": 1, "width": 1080, "height": 2400})
        self.assertTrue(self.runtime.uc_state["connected"])

        await self._feed_inbound(3, {"cmd": 0, "data": {"isReply": 1, "supportedWebRTC": 1, "port": 6466}})
        self.assertTrue(any(f.get("serviceId") == 3 and f.get("data", {}).get("cmd") == 1 for f in self.writer.sent_frames))

        await self._feed_inbound(1, {"cmd": 0, "view": 2, "resolution": {"width": 1920, "height": 1080}})
        self.assertTrue(any(f.get("serviceId") == 1 and f.get("data", {}).get("cmd") == 1 for f in self.writer.sent_frames))

        await self._feed_inbound(15, {"cmd": 2, "data": {"pkg": "desktop", "sessionId": 301}})
        self.assertTrue(any(f.get("serviceId") == 15 and f.get("data", {}).get("cmd") == 1 for f in self.writer.sent_frames))

        await self._feed_inbound(15, {"cmd": 23, "data": {"state": 0}})
        self.assertTrue(self.runtime.phone_resolutions["appstream"]["audioPaused"])

        res_ss = await self.runtime.service_bus.invoke("screenshare", "start", {"shareType": "mirror"}, self.conn)
        self.assertTrue(res_ss.get("ok"))

    async def test_bind_pc_to_media_loop_preserves_pyee_event_loop(self) -> None:
        from samyuj.core.tasks import _bind_pc_to_media_loop, get_mobile_desktop_service_thread
        dt = get_mobile_desktop_service_thread(start=True)
        self.assertTrue(dt.is_alive())
        self.assertIsNotNone(dt.loop)
        self.assertTrue(hasattr(dt.loop, "create_task"))
        self.assertTrue(hasattr(dt, "prepare_frame_bgrx_async"))
        self.assertTrue(hasattr(dt, "prepare_frame_bgrx_sync"))
        self.assertTrue(hasattr(dt, "call_soon"))
        self.assertTrue(hasattr(dt, "snapshot"))

        class DummyPeerConnection:
            def __init__(self, loop):
                self._loop = loop
            async def close(self):
                return "closed"

        real_loop = asyncio.get_running_loop()
        pc = DummyPeerConnection(real_loop)
        _bind_pc_to_media_loop(pc, dt)
        self.assertIs(pc._loop, real_loop)
        self.assertTrue(hasattr(pc._loop, "create_task"))
        self.assertIs(pc._sc_media_loop, dt.loop)
        self.assertEqual(await pc.close(), "closed")
