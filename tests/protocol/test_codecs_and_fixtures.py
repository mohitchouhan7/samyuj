from __future__ import annotations

import json
import unittest
from pathlib import Path

from samyuj.features import FEATURES, build_default_pc_caps, build_service_versions
from samyuj.protocol.constants import DEFAULT_PC_CAPS, PC_SERVICE_VERSIONS
from samyuj.protocol.envelope import EnvelopeContext, unpack_envelope
from samyuj.protocol.nfs.xdr import (
    PROG_MOUNT,
    XdrReader,
    XdrWriter,
    cnfs_add_one_to_file_name,
    cnfs_increment_file_name,
    dissect_rpc,
    norm_phone_path,
)
from samyuj.protocol.sdp import (
    expand_ssrc_identity,
    fix_answer_sdp,
    order_sdp_candidate_lines,
    prefer_codec_in_answer,
    sanitize_answer_sdp,
)
from samyuj.protocol.services import (
    build_applist_request,
    build_appstream_peer_start,
    build_appstream_resize,
    build_appstream_start,
    build_appstream_stop,
    build_appstream_stream_success,
    build_checkin,
    build_clipboard_confirm,
    build_clipboard_global_enable,
    build_clipboard_nego,
    build_clipboard_pull,
    build_clipboard_update,
    build_control_invite,
    build_control_lock_state,
    build_desktop_reply,
    build_desktop_request,
    build_desktop_set_resolution,
    build_desktop_stop,
    build_device_status,
    build_display_query,
    build_display_set_resolution,
    build_drag_data,
    build_drag_enter,
    build_drop_data,
    build_exchange_key,
    build_features_request,
    build_features_streaming_list_reply,
    build_filestation_announce,
    build_gallery_frame,
    build_input_key,
    build_input_mouse_button,
    build_input_mouse_move,
    build_input_mouse_wheel,
    build_input_request_cap,
    build_input_touch,
    build_input_touch_cancel,
    build_msg_mark_read,
    build_msg_notify_ready,
    build_msg_request_contacts,
    build_msg_request_messages,
    build_msg_request_sim_cards,
    build_msg_send_request,
    build_ntf_block_pkg,
    build_ntf_open_settings,
    build_ntf_pc_msg,
    build_pair_frame,
    build_pc_telemetry,
    build_phonefs_handshake,
    build_reply_json,
    build_runningapps_request,
    build_server_first_hello,
    build_ss_answer,
    build_ss_candidate,
    build_ss_connection_ready,
    build_ss_resolution,
    build_ss_stop,
    build_ss_switch,
    build_uc_disconnect,
    build_uc_request,
    build_uc_response,
    build_wallpaper_push,
    build_wallpaper_request,
    build_webcam_camera_query,
    build_webcam_keepalive,
    build_webcam_start,
    build_webcam_stop,
    build_webcam_switch_camera,
    build_webcam_toggle_flash,
    build_webcam_trigger,
    build_webcam_webrtc_signal,
    parse_appstream_audio_state,
    parse_contact_list_payload,
    parse_message_list_payload,
    scroll_notch_distance,
)

try:
    from fixtures import build_synthetic_fixtures
except ImportError:
    from tests.fixtures import build_synthetic_fixtures

_SYNTHETIC_FIXTURES = build_synthetic_fixtures()

def _load_fixture_text(name: str) -> str:
    val = _SYNTHETIC_FIXTURES[name]
    return val.decode("utf-8") if isinstance(val, bytes) else str(val)

def _load_fixture_bytes(name: str) -> bytes:
    val = _SYNTHETIC_FIXTURES[name]
    return val if isinstance(val, bytes) else str(val).encode("utf-8")

class ProtocolCatalogAndCodecsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.ctx = EnvelopeContext(
            device_id_b64="dGVzdC1wYy1kZXZpY2UtaWQ=",
            pc_real_device_id="cGMtcmVhbC1pZC0wMDAx",
            device_name="Linux-Test-PC",
            lan_ip="192.0.2.10",
            port=9831,
            fp_b64="ZmFrZS1mcC1iNjQ=",
            fingerprint_hex="a" * 64,
            token_b64="dGVzdC1zZXNzaW9uLXRva2Vu",
        )

    def test_feature_catalog_excludes_audio_svc21(self) -> None:
        keys = {f.key for f in FEATURES}
        self.assertEqual(
            keys,
            {
                "pairing", "desktop", "appstream", "webcam", "screenshare",
                "files", "clipboard", "notifications", "gallery", "messages",
                "cross_control", "control",
            },
        )
        sv = build_service_versions()
        caps = build_default_pc_caps()
        self.assertNotIn("21", sv)
        self.assertNotIn("21", caps)
        self.assertNotIn("21", PC_SERVICE_VERSIONS)
        self.assertNotIn("21", DEFAULT_PC_CAPS)
        for sid in ("0", "1", "2", "3", "5", "9", "10", "15", "16", "17", "19", "22", "25", "26", "168", "202", "204", "205", "1001"):
            self.assertIn(sid, sv)

    def test_pairing_and_golden_hello_fixture(self) -> None:
        hello = json.loads(_load_fixture_text("hello_fixture_a.json"))
        reply_raw = build_reply_json(self.ctx, "ok", hello)
        reply = unpack_envelope(reply_raw)
        self.assertEqual(reply["serviceId"], 0)
        self.assertEqual(reply["data"]["token"], "OK")
        self.assertNotIn("21", reply["data"]["serviceVersions"])

        srv_hello = unpack_envelope(build_server_first_hello(self.ctx, nonce_phone_id="nonce123"))
        self.assertEqual(srv_hello["data"]["noncePhoneId"], "nonce123")

        chk = unpack_envelope(build_checkin(self.ctx))
        self.assertEqual(chk["data"]["cmd"], 2)

        exk = unpack_envelope(build_exchange_key(self.ctx, key_b64="a2V5MTIz"))
        self.assertEqual(exk["data"]["cmd"], 1)
        self.assertEqual(exk["data"]["device"]["key"], "a2V5MTIz")

        pair_f = unpack_envelope(build_pair_frame(self.ctx, ""))
        self.assertEqual(pair_f["serviceId"], 19)
        self.assertEqual(pair_f["data"], "")

    def test_desktop_and_display_codecs(self) -> None:
        invite = {"msgId": 42, "data": {"cmd": 0, "view": 2, "drawerMode": True}}
        rep = unpack_envelope(build_desktop_reply(self.ctx, invite, accept=True))
        self.assertEqual(rep["serviceId"], 1)
        self.assertEqual(rep["data"]["reply"], 0)
        self.assertEqual(rep["data"]["resolution"], {"width": 1920, "height": 1080})

        req_portrait = unpack_envelope(build_desktop_request(self.ctx, view=2, width=1080, height=2400))
        self.assertEqual(req_portrait["data"]["resolution"], {"width": 1080, "height": 2400})
        self.assertEqual(req_portrait["data"]["rotation"], 1)

        res33 = unpack_envelope(build_desktop_set_resolution(self.ctx, 1080, 2400))
        self.assertEqual(res33["data"]["cmd"], 33)
        self.assertEqual(res33["data"]["rotation"], 1)

        stop_f = unpack_envelope(build_desktop_stop(self.ctx))
        self.assertEqual(stop_f["data"]["cmd"], 5)

        dq = unpack_envelope(build_display_query(self.ctx))
        self.assertEqual(dq["serviceId"], 10)
        dsr = unpack_envelope(build_display_set_resolution(self.ctx, 1080, 2400, fps=60.0))
        self.assertEqual(dsr["data"]["resolution"]["width"], 1080)
        self.assertEqual(dsr["data"]["resolution"]["height"], 2400)

    def test_appstream_and_applist_codecs_with_cmd23_audio_state(self) -> None:
        offer_raw = json.loads(_load_fixture_text("appstream_offer_p6.json"))
        self.assertIn("sdp", offer_raw)
        self.assertEqual(offer_raw.get("type"), "offer")

        ps = unpack_envelope(build_appstream_peer_start(self.ctx, {"msgId": 7, "data": {"data": {"pkg": "mirror", "sessionId": 99}}}))
        self.assertEqual(ps["serviceId"], 15)
        self.assertEqual(ps["data"]["data"]["displayWidth"], 0)

        ss = unpack_envelope(build_appstream_stream_success(self.ctx, 99, is_webrtc=True, webrtc_data='{"type":"answer"}'))
        self.assertEqual(ss["data"]["cmd"], 4)
        self.assertEqual(ss["data"]["data"]["isWebRTC"], 1)

        st = unpack_envelope(build_appstream_start(self.ctx, "com.android.settings"))
        self.assertEqual(st["data"]["cmd"], 1)
        self.assertEqual(st["data"]["data"]["pkg"], "com.android.settings")

        rsz = unpack_envelope(build_appstream_resize(self.ctx, 99, 2, 1280, 800))
        self.assertEqual(rsz["data"]["cmd"], 17)

        stp = unpack_envelope(build_appstream_stop(self.ctx, 99))
        self.assertEqual(stp["data"]["cmd"], 7)

        audio_st = parse_appstream_audio_state({"state": 0})
        self.assertTrue(audio_st["paused"])
        audio_st_play = parse_appstream_audio_state({"state": 1})
        self.assertFalse(audio_st_play["paused"])

        al = unpack_envelope(build_applist_request(self.ctx))
        self.assertEqual(al["serviceId"], 17)
        self.assertEqual(al["data"]["cmd"], 2)
        ra = unpack_envelope(build_runningapps_request(self.ctx))
        self.assertEqual(ra["data"]["cmd"], 5)

    def test_input_and_drag_drop_codecs(self) -> None:
        cap = unpack_envelope(build_input_request_cap(self.ctx, 2))
        self.assertEqual(cap["serviceId"], 16)
        self.assertEqual(cap["data"]["cmd"], 1)

        key = unpack_envelope(build_input_key(self.ctx, 2, 4, True))
        self.assertEqual(key["data"]["cmd"], 3)
        self.assertTrue(key["data"]["data"]["down"])

        mm = unpack_envelope(build_input_mouse_move(self.ctx, 2, 100, 200))
        self.assertEqual(mm["data"]["cmd"], 4)

        mb = unpack_envelope(build_input_mouse_button(self.ctx, 2, 1, 100, 200, True))
        self.assertEqual(mb["data"]["cmd"], 5)

        wheels = build_input_mouse_wheel(self.ctx, 2, 100, 200, dy=-1.0)
        self.assertEqual(len(wheels), 1)
        self.assertGreater(scroll_notch_distance(1.0), 0.0)

        tc = unpack_envelope(build_input_touch(self.ctx, 2, 1, 50, 60))
        self.assertEqual(tc["data"]["cmd"], 9)
        tcan = unpack_envelope(build_input_touch_cancel(self.ctx, 2))
        self.assertEqual(tcan["data"]["data"]["event"], 3)

        de = unpack_envelope(build_drag_enter(self.ctx, 2, enter=True))
        self.assertEqual(de["serviceId"], 24)
        dd = unpack_envelope(build_drag_data(self.ctx, 1, 2, 10, 20))
        self.assertEqual(dd["data"]["cmd"], 1)
        dr = unpack_envelope(build_drop_data(self.ctx, 1, 2, 10, 20))
        self.assertEqual(dr["data"]["cmd"], 2)

    def test_webcam_and_screenshare_codecs(self) -> None:
        trig_raw = build_webcam_trigger(self.ctx)
        self.assertNotIn("isReply", trig_raw)
        trig = unpack_envelope(trig_raw)
        self.assertEqual(trig["serviceId"], 3)

        self.assertEqual(unpack_envelope(build_webcam_start(self.ctx, 54010))["data"]["cmd"], 1)
        self.assertEqual(unpack_envelope(build_webcam_stop(self.ctx))["data"]["cmd"], 2)
        self.assertEqual(unpack_envelope(build_webcam_keepalive(self.ctx))["data"]["cmd"], 13)
        self.assertEqual(unpack_envelope(build_webcam_camera_query(self.ctx))["data"]["cmd"], 3)
        self.assertEqual(unpack_envelope(build_webcam_switch_camera(self.ctx, 1))["data"]["cmd"], 4)
        self.assertEqual(unpack_envelope(build_webcam_toggle_flash(self.ctx))["data"]["cmd"], 7)
        self.assertEqual(unpack_envelope(build_webcam_webrtc_signal(self.ctx, 5, "answer", "{}"))["data"]["cmd"], 17)

        rdy = unpack_envelope(build_ss_connection_ready(self.ctx, 10, share_type="extend"))
        self.assertEqual(rdy["serviceId"], 202)
        self.assertEqual(rdy["data"]["cmd"], 0)

        ans = unpack_envelope(build_ss_answer(self.ctx, 10, "v=0..."))
        self.assertEqual(ans["data"]["cmd"], 1)

        cand = unpack_envelope(build_ss_candidate(self.ctx, 10, "candidate:1 1 UDP 2122252543 192.0.2.10 50000 typ host"))
        cand_inner = json.loads(cand["data"]["data"])
        cand_obj = json.loads(cand_inner["candidate"])
        self.assertEqual(cand_obj["id"], "0")
        self.assertTrue(cand_obj["candidate"].startswith("candidate:"))

        self.assertEqual(unpack_envelope(build_ss_stop(self.ctx, 10))["data"]["cmd"], 26)
        self.assertEqual(unpack_envelope(build_ss_switch(self.ctx, 10, "mirror"))["data"]["cmd"], 14)
        self.assertEqual(unpack_envelope(build_ss_resolution(self.ctx, 10, 2400, 1080))["data"]["cmd"], 35)

    def test_files_nfs_xdr_and_golden_fixtures(self) -> None:
        fs_fixture = json.loads(_load_fixture_text("filestation_1001_handshake.json"))
        self.assertEqual(fs_fixture["serviceId"], 168)

        ann_raw, params = build_filestation_announce(self.ctx, fs_fixture)
        ann = unpack_envelope(ann_raw)
        self.assertEqual(ann["serviceId"], 168)
        self.assertEqual(params["type"], 1)

        pfs_raw, pfs_params = build_phonefs_handshake(self.ctx)
        self.assertEqual(unpack_envelope(pfs_raw)["serviceId"], 168)
        self.assertEqual(pfs_params["type"], 0)

        mnt_bin = _load_fixture_bytes("nfs_mount_call.bin")
        calls = dissect_rpc(mnt_bin)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["prog"], PROG_MOUNT)

        w = XdrWriter().u32(123).u64(456789).string("hello").opaque(b"xyz")
        r = XdrReader(w.bytes())
        self.assertEqual(r.u32(), 123)
        self.assertEqual(r.u64(), 456789)
        self.assertEqual(r.opaque(), b"hello")
        self.assertEqual(r.opaque(), b"xyz")

        self.assertEqual(cnfs_add_one_to_file_name("archive.tar.gz"), "archive(1).tar.gz")
        self.assertEqual(cnfs_increment_file_name("archive(1).tar.gz"), "archive(2).tar.gz")
        self.assertEqual(norm_phone_path("/storage/emulated/0/DCIM/Camera"), "/DCIM/Camera")

    def test_clipboard_notifications_gallery_messages_crosscontrol_control(self) -> None:
        self.assertEqual(unpack_envelope(build_clipboard_nego(self.ctx))["serviceId"], 9)
        self.assertEqual(unpack_envelope(build_clipboard_confirm(self.ctx))["data"]["msgId"], 2)
        self.assertEqual(unpack_envelope(build_clipboard_update(self.ctx, 1, "hi"))["data"]["msgId"], 3)
        self.assertEqual(unpack_envelope(build_clipboard_pull(self.ctx, "req1"))["data"]["msgId"], 4)
        self.assertEqual(unpack_envelope(build_clipboard_global_enable(self.ctx, True))["data"]["msgId"], 100)

        self.assertEqual(unpack_envelope(build_ntf_pc_msg(self.ctx, "k1", 1))["serviceId"], 5)
        self.assertEqual(unpack_envelope(build_ntf_block_pkg(self.ctx, "com.foo"))["data"]["cmd"], 1)
        self.assertEqual(unpack_envelope(build_ntf_open_settings(self.ctx))["data"]["cmd"], 2)

        self.assertEqual(unpack_envelope(build_gallery_frame(self.ctx))["serviceId"], 205)
        self.assertEqual(unpack_envelope(build_features_request(self.ctx, 8))["serviceId"], 1001)
        self.assertEqual(unpack_envelope(build_features_streaming_list_reply(self.ctx, 19, [{"pkg": "com.a"}]))["srcServiceId"], 1001)

        self.assertEqual(unpack_envelope(build_msg_notify_ready(self.ctx))["serviceId"], 204)
        self.assertEqual(unpack_envelope(build_msg_request_contacts(self.ctx))["data"]["cmd"], 1)
        self.assertEqual(unpack_envelope(build_msg_request_messages(self.ctx))["data"]["cmd"], 10)
        self.assertEqual(unpack_envelope(build_msg_request_sim_cards(self.ctx))["data"]["cmd"], 12)
        sms = unpack_envelope(build_msg_send_request(self.ctx, "+15550199", "Hello!"))
        self.assertEqual(sms["data"]["cmd"], 4)
        self.assertIsInstance(sms["data"]["data"]["msgId"], int)
        self.assertEqual(sms["data"]["data"]["medias"], [])
        self.assertEqual(unpack_envelope(build_msg_mark_read(self.ctx, 101))["data"]["cmd"], 8)

        msgs, notice = parse_message_list_payload("4.0", {"messageBeanList": [{"msgId": 1, "body": "test"}]})
        self.assertEqual(len(msgs), 1)
        self.assertEqual(notice, "matched")
        contacts, c_notice = parse_contact_list_payload("4.0", {"contactBeanList": [{"id": "1", "name": "Alice"}]})
        self.assertEqual(len(contacts), 1)
        self.assertEqual(c_notice, "matched")

        self.assertEqual(unpack_envelope(build_uc_request(self.ctx, 10))["serviceId"], 26)
        self.assertEqual(unpack_envelope(build_uc_response(self.ctx, 10))["data"]["cmd"], 3)
        self.assertEqual(unpack_envelope(build_uc_disconnect(self.ctx, 10))["data"]["cmd"], 5)

        self.assertEqual(unpack_envelope(build_pc_telemetry(self.ctx))["serviceId"], 25)
        self.assertEqual(unpack_envelope(build_device_status(self.ctx))["serviceId"], 25)
        self.assertEqual(unpack_envelope(build_wallpaper_push(self.ctx, ""))["data"]["cmd"], 2)
        self.assertEqual(unpack_envelope(build_wallpaper_request(self.ctx))["data"]["cmd"], 3)
        self.assertEqual(unpack_envelope(build_control_lock_state(self.ctx, True))["serviceId"], 22)
        self.assertEqual(unpack_envelope(build_control_invite(self.ctx))["data"]["cmd"], 1)

    def test_sdp_helpers_and_golden_fixtures(self) -> None:
        offer_sdp = _load_fixture_text("offer_fixture_a.sdp")
        raw_ans = _load_fixture_text("screenshare_answer_raw_crash.sdp")

        fixed_sdp, changes = fix_answer_sdp(raw_ans, offer_sdp, prefer_ip="192.0.2.10")
        self.assertTrue(len(changes) > 0)
        self.assertIn("a=ssrc:", fixed_sdp)
        self.assertIn("msid:", fixed_sdp)

        san_sdp, removed = sanitize_answer_sdp("v=0\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\na=recvonly\r\na=ssrc:123 cname:abc\r\n")
        self.assertEqual(removed, 1)
        self.assertNotIn("a=ssrc:", san_sdp)

        reordered, changed = prefer_codec_in_answer(
            "v=0\r\nm=video 9 UDP/TLS/RTP/SAVPF 96 102\r\na=rtpmap:96 VP8/90000\r\na=rtpmap:102 H264/90000\r\n",
            "H264",
        )
        self.assertTrue(changed)
        self.assertIn("m=video 9 UDP/TLS/RTP/SAVPF 102 96", reordered)

        cands = order_sdp_candidate_lines(
            "a=candidate:1 1 UDP 100 192.168.122.1 5000 typ host\r\n"
            "a=candidate:2 1 UDP 100 192.168.201.162 5001 typ host\r\n",
            prefer_usb=True,
        )
        self.assertEqual(len(cands), 2)
        self.assertIn("192.168.201.162", cands[0])
        expanded, count = expand_ssrc_identity(
            "v=0\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\na=sendonly\r\na=msid:stream1 track1\r\na=ssrc:999 cname:foo\r\n"
        )
        self.assertEqual(count, 1)
        self.assertIn("a=ssrc:999 msid:stream1 track1", expanded)
