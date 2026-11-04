from __future__ import annotations

import json
import struct

def _build_synthetic_nfs_mount_call() -> bytes:
    path_bytes = b"/C/nfs_demo"
    pad = (4 - (len(path_bytes) % 4)) % 4
    body = struct.pack(
        "!IIIIIIIIII",
        0x12345678,
        0,
        2,
        100005,
        3,
        1,
        0,
        0,
        0,
        0,
    ) + struct.pack("!I", len(path_bytes)) + path_bytes + (b"\x00" * pad)
    rec_hdr = struct.pack("!I", 0x80000000 | len(body))
    return rec_hdr + body

def build_synthetic_fixtures() -> dict[str, str | bytes]:
    hello_obj = {
        "msgId": 1,
        "deviceId": "cGhvbmUtbm9uY2UtaWQ=",
        "serviceId": 0,
        "srcServiceId": 0,
        "data": {
            "cmd": 0,
            "ver": 3,
            "paired": 1,
            "token": "dGVzdC1zZXNzaW9uLXRva2Vu",
            "ips": [
                {
                    "connType": 1,
                    "fp": "ZmFrZS1waG9uZS1mcA==",
                    "ip": "192.0.2.50",
                    "port": 9831,
                }
            ],
            "device": {
                "deviceId": "cGhvbmUtbm9uY2UtaWQ=",
                "realDeviceId": "cGhvbmUtcmVhbC1pZDAx",
                "deviceName": "Test Phone A",
                "os": 1,
                "scType": 1,
                "type": 1,
            },
        },
    }
    fs_1001_obj = {
        "deviceId": "bW9jay1ub25jZS1pZC0wMQ==",
        "msgId": 99,
        "senderId": 1,
        "serviceId": 168,
        "srcServiceId": 168,
        "data": {
            "data": json.dumps({
                "type": 0,
                "path": "",
                "port": 0,
                "fp": "8F" * 32,
                "fp2": "c7" * 32,
                "deviceId": "bW9jay1yZWFsLWlkLTAwMQ==",
                "capability": 1,
                "timestamp": 1791602382663,
                "nfs_version": 8,
                "nfs_dependency": 24,
                "dt2type": 3,
            }),
            "cmd": 1001,
            "ver": "1.0",
        },
    }
    offer_sdp = (
        "v=0\r\n"
        "o=- 1111111111 2 IN IP4 127.0.0.1\r\n"
        "s=-\r\n"
        "t=0 0\r\n"
        "a=group:BUNDLE 0 1 2\r\n"
        "a=msid-semantic: WMS ARDAMS\r\n"
        "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"
        "c=IN IP4 0.0.0.0\r\n"
        "a=mid:0\r\n"
        "a=sendonly\r\n"
        "a=rtpmap:111 opus/48000/2\r\n"
        "m=video 9 UDP/TLS/RTP/SAVPF 102 96\r\n"
        "c=IN IP4 0.0.0.0\r\n"
        "a=mid:1\r\n"
        "a=recvonly\r\n"
        "a=rtpmap:102 H264/90000\r\n"
        "a=rtpmap:96 VP8/90000\r\n"
        "m=application 9 UDP/DTLS/SCTP webrtc-datachannel\r\n"
        "c=IN IP4 0.0.0.0\r\n"
        "a=mid:2\r\n"
        "a=sctp-port:5000\r\n"
    )
    raw_ans_sdp = (
        "v=0\r\n"
        "o=- 4001138371 4001138371 IN IP4 0.0.0.0\r\n"
        "s=-\r\n"
        "t=0 0\r\n"
        "a=group:BUNDLE 0 1 2\r\n"
        "a=msid-semantic:WMS *\r\n"
        "m=audio 33217 UDP/TLS/RTP/SAVPF 111\r\n"
        "c=IN IP4 192.0.2.1\r\n"
        "a=inactive\r\n"
        "a=mid:0\r\n"
        "a=rtcp-mux\r\n"
        "a=rtpmap:111 opus/48000/2\r\n"
        "m=video 33217 UDP/TLS/RTP/SAVPF 96 102\r\n"
        "c=IN IP4 192.0.2.1\r\n"
        "a=sendonly\r\n"
        "a=mid:1\r\n"
        "a=rtcp-mux\r\n"
        "a=msid:stream-v track-v\r\n"
        "a=ssrc:12345678 cname:synthetic-pc\r\n"
        "a=rtpmap:96 VP8/90000\r\n"
        "a=rtpmap:102 H264/90000\r\n"
        "a=candidate:1 1 UDP 2130706431 192.0.2.1 33217 typ host\r\n"
        "a=candidate:2 1 UDP 2130706431 192.0.2.10 59913 typ host\r\n"
        "m=application 33217 UDP/DTLS/SCTP webrtc-datachannel\r\n"
        "c=IN IP4 192.0.2.1\r\n"
        "a=mid:2\r\n"
        "a=sctp-port:5000\r\n"
    )
    return {
        "hello_fixture_a.json": json.dumps(hello_obj),
        "appstream_offer_p6.json": json.dumps({"type": "offer", "sdp": offer_sdp}),
        "filestation_1001_handshake.json": json.dumps(fs_1001_obj),
        "offer_fixture_a.sdp": offer_sdp,
        "screenshare_answer_raw_crash.sdp": raw_ans_sdp,
        "nfs_mount_call.bin": _build_synthetic_nfs_mount_call(),
    }
