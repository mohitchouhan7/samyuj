from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from typing import Any

from samyuj.core.identity import make_client_ssl_context
from samyuj.session.lifecycle import SamyujRuntime
from samyuj.transport.server import ws_upgrade_client
from samyuj.transport.websocket import WsFrameParser, ws_frame

class FakePhonePeer:

    def __init__(self, host: str, port: int, device_id: str = "cGhvbmUtbm9uY2UtaWQ=") -> None:
        self.host = host
        self.port = port
        self.device_id = device_id
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.parser: WsFrameParser | None = None
        self.received: list[dict[str, Any]] = []
        self._rx_task: asyncio.Task[None] | None = None

    async def connect(self) -> None:
        ctx = make_client_ssl_context()
        self.reader, self.writer = await asyncio.open_connection(self.host, self.port, ssl=ctx)
        self.parser = await ws_upgrade_client(self.reader, self.writer, self.host, self.port)
        self._rx_task = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        assert self.reader and self.parser
        try:
            while True:
                chunk = await self.reader.read(65536)
                if not chunk:
                    break
                for op, _fin, payload in self.parser.feed(chunk):
                    if op == 0x1:
                        self.received.append(json.loads(payload.decode("utf-8")))
        except Exception:
            pass

    async def send_envelope(self, service_id: int, data: Any, msg_id: int = 1) -> None:
        assert self.writer is not None
        env = {
            "msgId": msg_id,
            "deviceId": self.device_id,
            "serviceId": service_id,
            "srcServiceId": service_id,
            "data": data,
        }
        raw = json.dumps(env, separators=(",", ":")).encode("utf-8")
        self.writer.write(ws_frame(0x1, raw, mask=True))
        await self.writer.drain()

    async def close(self) -> None:
        if self._rx_task:
            self._rx_task.cancel()
        if self.writer:
            self.writer.close()

class FakePhoneIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_full_handshake_and_retained_services_over_tls_websocket(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sc-e2e-") as td:
            rt = SamyujRuntime(
                host="127.0.0.1",
                port=0,
                out_dir=td,
                state_dir=td,
                media=False,
            )
            port = await rt.start()
            phone = FakePhonePeer("127.0.0.1", port)
            try:
                await phone.connect()
                await phone.send_envelope(
                    0,
                    {
                        "cmd": 0,
                        "ver": 3,
                        "paired": 1,
                        "token": rt.token_b64,
                        "device": {
                            "deviceId": "cGhvbmUtbm9uY2UtaWQ=",
                            "realDeviceId": "cGhvbmUtcmVhbC1pZDAx",
                            "deviceName": "Test Phone A",
                        },
                    },
                    msg_id=1,
                )
                for _ in range(40):
                    if any(f.get("serviceId") == 0 for f in phone.received):
                        break
                    await asyncio.sleep(0.05)

                hello_replies = [f for f in phone.received if f.get("serviceId") == 0 and f.get("data", {}).get("cmd") == 0]
                self.assertTrue(len(hello_replies) >= 1)
                self.assertEqual(hello_replies[0]["data"]["token"], "OK")
                sv = hello_replies[0]["data"]["serviceVersions"]
                self.assertNotIn("21", sv)
                self.assertEqual(rt._best_device_name(), "Test Phone A")

                await phone.send_envelope(
                    0,
                    {
                        "cmd": 1,
                        "ver": 1,
                        "key": "cGhvbmUtZWNkaC1rZXk=",
                        "device": {
                            "deviceId": "cGhvbmUtbm9uY2UtaWQ=",
                            "realDeviceId": "cGhvbmUtcmVhbC1pZDAx",
                            "deviceName": "Test Phone A",
                        },
                    },
                    msg_id=2,
                )
                for _ in range(40):
                    if any(f.get("serviceId") == 0 and f.get("data", {}).get("cmd") == 1 for f in phone.received):
                        break
                    await asyncio.sleep(0.05)
                key_replies = [f for f in phone.received if f.get("serviceId") == 0 and f.get("data", {}).get("cmd") == 1]
                self.assertTrue(len(key_replies) >= 1)
                self.assertIn("key", key_replies[0]["data"]["device"])
                paired = rt.load_paired_phones()
                self.assertIn("cGhvbmUtcmVhbC1pZDAx", paired)
                self.assertEqual(paired["cGhvbmUtcmVhbC1pZDAx"]["name"], "Test Phone A")

                await phone.send_envelope(9, {"msgId": 3, "data": {"clipId": 1, "text": "e2e-clip"}})
                await phone.send_envelope(
                    5,
                    {
                        "cmd": 0,
                        "key": "e2e-n1",
                        "event": 0,
                        "data": {"appName": "Calendar", "title": "Meeting", "text": "Now"},
                    },
                )
                await asyncio.sleep(0.1)
                self.assertEqual(rt.clip_last_text, "e2e-clip")
                self.assertTrue(any(n.get("key") == "e2e-n1" for n in rt.notifications_list))
            finally:
                await phone.close()
                await rt.stop()
