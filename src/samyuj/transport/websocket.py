from __future__ import annotations

import base64
import contextlib
import ctypes
import hashlib
import os
from pathlib import Path

from samyuj.protocol.constants import WS_GUID

_LIBSC_FAST = None
for _cand in (
    Path(__file__).resolve().parents[3] / "libsc_fast.so",
    Path(__file__).resolve().parents[2] / "libsc_fast.so",
    Path("/usr/lib/samyuj/libsc_fast.so"),
):
    if _cand.is_file():
        with contextlib.suppress(Exception):
            _lib = ctypes.CDLL(str(_cand))
            _lib.sc_ws_xor_mask.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p]
            _lib.sc_ws_xor_mask.restype = None
            _LIBSC_FAST = _lib
            break

def fast_ws_xor_mask(payload: bytes, mask: bytes) -> bytes:
    ln = len(payload)
    if ln == 0 or not mask:
        return bytes(payload)
    if _LIBSC_FAST is not None:
        with contextlib.suppress(Exception):
            buf = ctypes.create_string_buffer(bytes(payload), ln)
            _LIBSC_FAST.sc_ws_xor_mask(buf, ln, bytes(mask[:4]))
            return buf.raw
    rep = (mask * ((ln >> 2) + 1))[:ln]
    return (int.from_bytes(payload, "big") ^ int.from_bytes(rep, "big")).to_bytes(ln, "big")

def ws_frame(opcode: int, payload: bytes, mask: bool = False) -> bytes:
    out = bytearray([0x80 | (opcode & 0x0F)])
    ln = len(payload)
    mb = 0x80 if mask else 0x00
    if ln < 126:
        out.append(mb | ln)
    elif ln < 65536:
        out.append(mb | 126)
        out += ln.to_bytes(2, "big")
    else:
        out.append(mb | 127)
        out += ln.to_bytes(8, "big")
    if mask:
        m = os.urandom(4)
        out += m
        out += fast_ws_xor_mask(payload, m)
    else:
        out += payload
    return bytes(out)

def ws_accept_value(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + WS_GUID).encode("ascii")).digest()).decode("ascii")

class WsFrameParser:

    MAX_FRAME_BYTES = 32 * 1024 * 1024

    def __init__(self) -> None:
        self.buf = bytearray()

    def feed(self, data: bytes) -> list[tuple[int, bool, bytes]]:
        if len(self.buf) + len(data) > self.MAX_FRAME_BYTES * 2:
            self.buf.clear()
            raise ValueError("WebSocket buffer exceeded maximum size")
        self.buf.extend(data)
        out: list[tuple[int, bool, bytes]] = []
        while True:
            frame = self._try_frame()
            if frame is None:
                return out
            out.append(frame)

    def _try_frame(self) -> tuple[int, bool, bytes] | None:
        b = self.buf
        if len(b) < 2:
            return None
        fin = bool(b[0] & 0x80)
        opcode = b[0] & 0x0F
        masked = bool(b[1] & 0x80)
        ln = b[1] & 0x7F
        pos = 2
        if ln == 126:
            if len(b) < pos + 2:
                return None
            ln = int.from_bytes(b[pos : pos + 2], "big")
            pos += 2
        elif ln == 127:
            if len(b) < pos + 8:
                return None
            ln = int.from_bytes(b[pos : pos + 8], "big")
            pos += 8
            if ln > self.MAX_FRAME_BYTES:
                self.buf.clear()
                raise ValueError(f"WebSocket frame length {ln} exceeds limit")
        mask = b""
        if masked:
            if len(b) < pos + 4:
                return None
            mask = bytes(b[pos : pos + 4])
            pos += 4
        if len(b) < pos + ln:
            return None
        raw_payload = bytes(b[pos : pos + ln])
        del b[: pos + ln]
        if masked and raw_payload:
            raw_payload = fast_ws_xor_mask(raw_payload, mask)
        return opcode, fin, raw_payload
