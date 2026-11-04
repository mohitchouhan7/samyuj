from __future__ import annotations

import asyncio
import base64
import os
import unittest

from samyuj.transport.discovery import (
    ble_advertisement_hex,
    ble_service_data,
    decode_gatt_frame,
    dns_encode_name,
    dns_parse_name,
    dns_parse_records,
    dns_rr,
    dns_txt_rdata,
    fragment_long_gatt,
    gatt_bootstrap_frame,
    make_nonce_id,
    parse_r4_service_data,
    reassemble_long_gatt,
    verify_nonce_id,
)
from samyuj.transport.network import is_usb_peer_address
from samyuj.transport.server import (
    TlsControlServer,
    build_http_101_upgrade_response,
    ws_upgrade_client,
)
from samyuj.transport.websocket import WsFrameParser, ws_accept_value, ws_frame

class TransportAndDiscoveryTest(unittest.IsolatedAsyncioTestCase):
    def test_websocket_frame_roundtrip_masked_and_unmasked(self) -> None:
        parser = WsFrameParser()
        for size in (0, 15, 125, 126, 1024, 70000):
            payload = os.urandom(size)
            raw_unmasked = ws_frame(0x2, payload, mask=False)
            raw_masked = ws_frame(0x2, payload, mask=True)
            frames = parser.feed(raw_unmasked + raw_masked)
            self.assertEqual(len(frames), 2)
            self.assertEqual(frames[0], (0x2, True, payload))
            self.assertEqual(frames[1], (0x2, True, payload))

    def test_ble_nonce_service_data_fragmentation_and_gatt_encryption(self) -> None:
        real_id = base64.b64encode(os.urandom(16)).decode("ascii")
        nonce = make_nonce_id(real_id)
        self.assertTrue(verify_nonce_id(nonce, real_id))

        sd = ble_service_data(nonce, dev_type=4, os_id=4, cap=5365)
        parsed = parse_r4_service_data(sd)
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed["nonce_b64"], nonce)
        self.assertEqual(parsed["cap"], 5365)

        adv_hex, srsp_hex = ble_advertisement_hex(nonce, "Linux-PC")
        self.assertTrue(len(adv_hex) > 10)
        self.assertTrue(len(srsp_hex) > 10)

        msg = b'{"cmd":1,"deviceName":"Linux-PC"}' * 4
        frags = fragment_long_gatt(msg, mtu=27, msg_id=3)
        self.assertEqual(reassemble_long_gatt(frags), msg)

        key_b64 = base64.b64encode(os.urandom(32)).decode("ascii")
        enc_frame = gatt_bootstrap_frame(nonce, '{"cmd":1,"port":9831}', secret_key_b64=key_b64)
        dec = decode_gatt_frame(enc_frame, keys=[key_b64])
        self.assertIsNotNone(dec)
        assert dec is not None
        self.assertEqual(dec["op"], 1)
        self.assertEqual(dec["json"]["port"], 9831)

    def test_mdns_dns_encoding_and_record_parsing(self) -> None:
        enc = dns_encode_name("samyuj-pc._readyformdns._tcp.local.")
        name, off = dns_parse_name(enc, 0)
        self.assertEqual(name, "samyuj-pc._readyformdns._tcp.local")
        self.assertEqual(off, len(enc))

        txt = dns_txt_rdata({"deviceName": "LinuxPC", "type": "4"})
        rr = dns_rr("inst._readyformdns._tcp.local.", 16, 120, txt)
        pkt = (0).to_bytes(2, "big") + (0x8400).to_bytes(2, "big") + (0).to_bytes(2, "big") + (1).to_bytes(2, "big") + (0).to_bytes(4, "big") + rr
        recs = dns_parse_records(pkt)
        self.assertEqual(len(recs), 1)
        self.assertIn("deviceName=LinuxPC", recs[0]["txt"])
        self.assertTrue(is_usb_peer_address("192.168.201.161"))
        self.assertFalse(is_usb_peer_address("192.0.2.50"))

    async def test_tcp_websocket_upgrade_handshake(self) -> None:
        received: list[bytes] = []

        async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            req = await reader.readuntil(b"\r\n\r\n")
            key = ""
            for ln in req.decode("latin1").splitlines():
                if ln.lower().startswith("sec-websocket-key:"):
                    key = ln.split(":", 1)[1].strip()
            writer.write(build_http_101_upgrade_response(key))
            await writer.drain()
            data = await reader.read(1024)
            for _op, _fin, payload in WsFrameParser().feed(data):
                received.append(payload)
            writer.close()

        srv = TlsControlServer("127.0.0.1", 0, None, _handle)
        port = await srv.start()
        try:
            r, w = await asyncio.open_connection("127.0.0.1", port)
            await ws_upgrade_client(r, w, "127.0.0.1", port)
            w.write(ws_frame(0x1, b"hello-ws", mask=True))
            await w.drain()
            await asyncio.sleep(0.05)
            w.close()
            self.assertEqual(received, [b"hello-ws"])
        finally:
            await srv.stop()
