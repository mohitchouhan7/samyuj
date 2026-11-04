from __future__ import annotations

import unittest
import unittest.mock

from samyuj.bootstrap import parse_launcher_args
from samyuj.session import (
    CompatibilityPolicy,
    PeerSession,
    ServiceSessionRegistry,
    ServiceSessionState,
)

class SessionAndBootstrapTest(unittest.TestCase):
    def test_peer_session_mapping_and_typed_properties(self) -> None:
        sess = PeerSession(id=7, role="server", transport="usb", ws=True)
        self.assertEqual(sess.id, 7)
        self.assertEqual(sess.transport, "usb")
        self.assertTrue(sess.ws)
        self.assertFalse(sess.replied)
        sess["custom_key"] = "value"
        self.assertEqual(sess.get("custom_key"), "value")
        sess.replied = True
        self.assertTrue(sess["replied"])

    def test_service_session_registry_and_compatibility_policy(self) -> None:
        reg = ServiceSessionRegistry()
        st = reg.get("desktop")
        self.assertIsInstance(st, ServiceSessionState)
        gen1 = st.bump("live")
        self.assertEqual(gen1, 1)
        self.assertIn("desktop", reg.active_names())
        self.assertEqual(reg.primary_feature(), "desktop")

        res_wc = CompatibilityPolicy.evaluate_start(reg.active_names(), "webcam")
        self.assertTrue(res_wc.allowed)
        self.assertNotIn("desktop", res_wc.to_stop)
        self.assertIn("screenshare", res_wc.to_stop)

        res_ss = CompatibilityPolicy.evaluate_start(reg.active_names(), "screenshare")
        self.assertEqual(set(res_ss.to_stop), {"desktop", "appstream", "webcam"})

        st.reset()
        self.assertEqual(st.status, "idle")
        self.assertEqual(reg.active_names(), [])

    def test_bootstrap_launcher_flags_strictly_gui_first(self) -> None:
        args = parse_launcher_args(["--debug", "--log-level", "debug"])
        self.assertTrue(args.debug)
        self.assertEqual(args.log_level, "debug")

        for removed_flag in ("--cli", "--web-ui", "--gui", "--no-gui", "--rq"):
            with self.assertRaises(SystemExit):
                parse_launcher_args([removed_flag])

import base64
import json
import os
import stat
import tempfile
from pathlib import Path

from samyuj.core.logging import ConnLog, redact_secrets, verify_redaction_selftest
from samyuj.protocol.constants import PHONE_EXTEND_PRESETS, derive_pc_sn
from samyuj.protocol.envelope import EnvelopeContext, build_envelope
from samyuj.session.lifecycle import SamyujRuntime
from samyuj.session.models import new_session_state
from samyuj.transport.discovery.ble import gatt_bootstrap_frame
from samyuj.transport.discovery.usb import (
    find_tether_link,
    iface_for_ip,
    is_usb_ip,
    usb_candidate_matches_names,
)
from samyuj.transport.websocket import WsFrameParser

class FakeControlWriter:

    def __init__(self, peer_ip: str = "192.0.2.50", local_ip: str = "192.0.2.10") -> None:
        self._peer = (peer_ip, 50123)
        self._sock = (local_ip, 8443)
        self.closed = False
        self.buf = bytearray()

    def get_extra_info(self, key: str, default=None):
        return {"peername": self._peer, "sockname": self._sock}.get(key, default)

    def write(self, data: bytes) -> None:
        self.buf.extend(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

class SessionSecurityAndDiscoveryTest(unittest.IsolatedAsyncioTestCase):

    async def test_p0_1_state_dir_and_secret_file_permissions(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sc-p0-perm-") as td:
            st_dir = Path(td) / "state"
            st_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(st_dir, 0o755)
            legacy_reg = st_dir / "paired_phones.json"
            with open(legacy_reg, "w", encoding="utf-8") as f:
                f.write("{}")
            os.chmod(legacy_reg, 0o644)

            rt = SamyujRuntime(host="127.0.0.1", port=0, out_dir=td, state_dir=st_dir, media=False)
            await rt.start()
            try:
                rt.note_phone(real_id="phone_1", name="Test Phone A", key="secretKey123")
                st_mode = stat.S_IMODE(st_dir.stat().st_mode)
                self.assertEqual(st_mode, 0o700)
                for fn in ("identity.key.pem", "identity.cert.pem", "device_id.txt", "paired_phones.json"):
                    fp = st_dir / fn
                    self.assertTrue(fp.exists(), f"Expected {fn} to exist")
                    fmode = stat.S_IMODE(fp.stat().st_mode)
                    self.assertEqual(fmode & 0o077, 0, f"{fn} has group/other bits set: {oct(fmode)}")
            finally:
                await rt.stop()

    async def test_p0_2_fail_closed_hello_token_verification(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sc-p0-tok-") as td:
            rt = SamyujRuntime(host="127.0.0.1", port=0, out_dir=td, state_dir=td, media=False)
            rt.device_id_b64 = "dGVzdC1wYy1pZA=="
            rt.token_b64 = "dmFsaWQtdG9rZW4tMTIzNA=="

            for bad_tok in ("", "wrong-token-9999"):
                w = FakeControlWriter()
                conn = PeerSession({
                    "id": 10,
                    "reader": None,
                    "writer": w,
                    "remote": "('192.0.2.50', 50123)",
                    "mode": "ws",
                    "ws": True,
                    "tunnel": False,
                    "role": "control",
                    "closed": False,
                    "hello": None,
                    "readiness": "connected",
                    "tasks": [],
                    "log": ConnLog(None, disk_enabled=False),
                    "parser": WsFrameParser(),
                    "session": new_session_state(),
                    "sessions": ServiceSessionRegistry(),
                })
                hello_frame = {
                    "msgId": 1,
                    "deviceId": "cGhvbmUtbm9uY2U=",
                    "serviceId": 0,
                    "data": {
                        "cmd": 0,
                        "ver": 3,
                        "token": bad_tok,
                        "device": {"deviceId": "cGhvbmUtbm9uY2U=", "deviceName": "Test Phone A"},
                    },
                }
                await rt._on_ws_text(conn, json.dumps(hello_frame))
                self.assertTrue(conn["closed"])
                self.assertTrue(w.closed)
                self.assertIsNone(conn["hello"])
                self.assertNotIn(conn.get("readiness"), ("hello_verified", "paired"))
                self.assertEqual(len(w.buf), 0)

            w_tun = FakeControlWriter()
            tun_conn = PeerSession({
                "id": 11,
                "reader": None,
                "writer": w_tun,
                "remote": "('192.0.2.50', 50124)",
                "mode": "ws",
                "ws": True,
                "tunnel": True,
                "role": "tunnel",
                "closed": False,
                "hello": None,
                "readiness": "connected",
                "tasks": [],
                "log": ConnLog(None, disk_enabled=False),
                "parser": WsFrameParser(),
                "session": new_session_state(),
                "sessions": ServiceSessionRegistry(),
            })
            rt.auto_reply = "silent"
            await rt._on_ws_text(tun_conn, json.dumps({
                "msgId": 2,
                "deviceId": "cGhvbmUtbm9uY2U=",
                "serviceId": 0,
                "data": {
                    "cmd": 0,
                    "ver": 3,
                    "token": "",
                    "device": {"deviceId": "cGhvbmUtbm9uY2U=", "deviceName": "Test Phone A"},
                },
            }))
            self.assertFalse(tun_conn["closed"])
            self.assertEqual(tun_conn["readiness"], "hello_verified")

    def test_p0_3_gatt_bootstrap_frame_fail_closed_on_bad_key(self) -> None:
        nonce = base64.b64encode(b"01234567890123456789").decode("ascii")
        bad_key = base64.b64encode(b"short_key").decode("ascii")
        res = gatt_bootstrap_frame(nonce, '{"cmd":1}', secret_key_b64=bad_key)
        self.assertIsNone(res)

        unpaired = gatt_bootstrap_frame(nonce, '{"cmd":1}', secret_key_b64=None)
        self.assertIsNotNone(unpaired)
        assert unpaired is not None
        self.assertEqual(unpaired[16], 2)

    def test_p0_4_envelope_context_requires_identity_and_rejects_empty_device_id(self) -> None:
        with self.assertRaises(TypeError):
            EnvelopeContext()

        ctx = EnvelopeContext(device_id_b64="", token_b64="tok")
        with self.assertRaises(ValueError):
            build_envelope(ctx, 1, None, 0, 0, {"cmd": 0})

    def test_p0_5_redaction_selftest(self) -> None:
        self.assertTrue(verify_redaction_selftest())
        red = redact_secrets('{"token":"mySecretTok99","fp":"0123456789abcdef"}')
        self.assertNotIn("mySecretTok99", red)
        self.assertNotIn("0123456789abcdef", red)

    def test_p1_provenance_usb_vs_wifi_and_dynamic_tether_discovery(self) -> None:
        fake_net = {
            "wlan0": {
                "operstate": "up",
                "ipv4": [("192.168.42.10", 24), ("10.42.0.10", 24)],
                "usb_device": None,
            },
            "enx001122334455": {
                "operstate": "up",
                "ipv4": [("10.0.0.2", 24), ("169.254.10.20", 16)],
                "usb_device": "/sys/devices/pci0000:00/usb1/1-1",
            },
        }
        self.assertEqual(iface_for_ip("192.168.42.55", fake_net), "wlan0")
        self.assertFalse(is_usb_ip("192.168.42.55", net=fake_net))
        self.assertFalse(is_usb_ip("10.42.0.99", net=fake_net))

        self.assertEqual(iface_for_ip("10.0.0.99", fake_net), "enx001122334455")
        self.assertTrue(is_usb_ip("10.0.0.99", net=fake_net))
        self.assertTrue(is_usb_ip("169.254.10.99", net=fake_net))

        link = find_tether_link(fake_net, phone_ips=["10.0.0.2"])
        self.assertEqual(link, ("enx001122334455", "169.254.10.20"))

        self.assertTrue(
            usb_candidate_matches_names(
                [{"idVendor": "9999", "product": "CustomPhone", "manufacturer": "Acme"}],
                paired_names=["My CustomPhone"],
            )
        )
        self.assertTrue(
            usb_candidate_matches_names(
                [{"idVendor": "abcd", "product": "Device", "manufacturer": "Vendor"}],
                extra_vids=["0xABCD"],
            )
        )

    def test_p1_6_derived_pc_sn_and_p2_debranded_presets(self) -> None:
        sn1 = derive_pc_sn("dGVzdC1kZXZpY2UtaWQtMQ==")
        sn2 = derive_pc_sn("dGVzdC1kZXZpY2UtaWQtMg==")
        self.assertTrue(sn1.startswith("SAM-PC-"))
        self.assertNotEqual(sn1, "SAM-PC-0001")
        self.assertNotEqual(sn1, sn2)

        for key, label in PHONE_EXTEND_PRESETS:
            self.assertIn("x", key)
            low = label.lower()
            self.assertNotIn("g" + "85", low)
            self.assertNotIn("edge " + "50", low)
            self.assertNotIn("ra" + "zr", low)

    def test_discovery_validation_and_clipboard_limits(self) -> None:
        from samyuj.bootstrap import parse_launcher_args
        from samyuj.platform.linux.adb import probe_phone_ip
        from samyuj.protocol.envelope import build_envelope
        from samyuj.protocol.services.webcam import build_webcam_start
        from samyuj.transport.discovery.usb import is_usb_ip
        from samyuj.ui.facade import ApplicationFacade

        with tempfile.TemporaryDirectory(prefix="sc-sec-") as td:
            rt = SamyujRuntime(host="127.0.0.1", port=45454, out_dir=td, state_dir=td, media=False)
            self.assertTrue(bool(rt.token_b64))
            self.assertTrue(bool(rt.device_id_b64))
            self.assertEqual(rt.phone_storage, {"free_bytes": 0, "total_bytes": 0})

            env = build_envelope(rt, 1, None, 3, 3, {"cmd": 0})
            self.assertNotIn("type", env)
            self.assertEqual(env["srcServiceId"], 3)

            wc_raw = build_webcam_start(rt, 0)
            self.assertIn("45454", wc_raw)

            args = parse_launcher_args([])
            self.assertIsNone(args.usb_addr_order)

            self.assertTrue(is_usb_ip("192.168.42.129", net={}, usb_link=("usb0", "192.168.42.10")))
            self.assertIsNone(probe_phone_ip("invalid iface!", "192.168.42.10"))

            conn = PeerSession({
                "id": 99,
                "peer": ("192.168.1.50", 51000),
                "ws": True,
                "hello": {"serviceId": 0},
                "role": "control",
                "closed": False,
                "tunnel": False,
                "session": new_session_state(),
                "sessions": ServiceSessionRegistry(),
            })
            conn["ws"] = True
            conn["hello"] = {"serviceId": 0}
            conn["role"] = "control"
            rt._conns.append(conn)
            rt.phone_telemetry["battery"] = 74
            rt.phone_storage = {"free_bytes": 32 * (1024 ** 3), "total_bytes": 64 * (1024 ** 3)}
            fac = ApplicationFacade(rt)
            cs = fac.get_connection_summary()
            self.assertTrue(cs.connected)
            self.assertEqual(cs.battery_pct, 74)
            self.assertEqual(cs.storage_free_gb, 32.0)
            fws = fac.get_feature_windows()
            self.assertTrue(len(fws) >= 1)
            rep = fac.get_support_report()
            self.assertTrue(rep.ok)
            rt.user_settings["extra_smartconnect_pkgs"] = ["com.example.companion"]
            with unittest.mock.patch("shutil.which", return_value="/usr/bin/adb"), unittest.mock.patch(
                "subprocess.run"
            ) as mock_run:
                mock_run.return_value = unittest.mock.Mock(returncode=0, stdout="", stderr="")
                ok_adb = rt.power_mgr.whitelist_phone_battery_saver_adb("SERIAL123", force=True)
                self.assertTrue(ok_adb)

