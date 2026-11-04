from __future__ import annotations

import asyncio
import contextlib
import os
import re
import struct
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

PROG_PORTMAP = 100000
PROG_MOUNT = 100005
PROG_NFS = 100003

MAX_WRITEBACK_FILE_BYTES = 64 * 1024 * 1024

def _xdr_opaque(b: bytes) -> bytes:
    pad = (-len(b)) & 3
    return struct.pack(">I", len(b)) + b + (b"\x00" * pad)

def _skip_rpc_auth(rec: bytes, off: int = 24) -> int:
    for _ in range(2):
        if off + 8 > len(rec):
            return len(rec)
        _flavor, alen = struct.unpack(">II", rec[off:off + 8])
        off += 8 + ((alen + 3) & ~3)
    return min(off, len(rec))

def _patch_fattr3_bytes(raw84: bytes) -> bytes:
    if len(raw84) != 84:
        return raw84
    try:
        ftype = struct.unpack_from(">I", raw84, 0)[0]
        mode = 0o755 if ftype == 2 else 0o644
        uid = os.getuid() if hasattr(os, "getuid") else 1000
        gid = os.getgid() if hasattr(os, "getgid") else 1000
        ba = bytearray(raw84)
        struct.pack_into(">I", ba, 4, mode)
        struct.pack_into(">I", ba, 12, uid)
        struct.pack_into(">I", ba, 16, gid)
        return bytes(ba)
    except Exception:
        return raw84

def sanitize_device_folder_name(name: str) -> str:
    raw = (name or "").strip()
    if not raw or raw.lower() in ("unknown", "none", "phone"):
        return "Connected_Phone"
    clean = re.sub(r"[^\w\-. ]+", "_", raw).strip().replace(" ", "_")
    return clean or "Connected_Phone"

class LocalPhoneNfsLoopback:

    def __init__(self, get_pfs_client) -> None:
        self._get_client = get_pfs_client
        self._srv: asyncio.AbstractServer | None = None
        self.port: int = 0
        self.rpc_forwarded: int = 0
        self.last_local_reply_error: str = ""
        self._dir_cache: dict[bytes, tuple[float, bytes, list[tuple[int, bytes, bytes, bytes | None]]]] = {}

    async def start(self, host: str = "127.0.0.1", port: int = 0) -> int:
        if self._srv is not None and self.port > 0:
            return self.port
        self._srv = await asyncio.start_server(self._handle_client, host, port)
        self.port = self._srv.sockets[0].getsockname()[1]
        return self.port

    async def stop(self) -> None:
        if self._srv is not None:
            self._srv.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._srv.wait_closed(), timeout=2.0)
            self._srv = None
            self.port = 0
        self._dir_cache.clear()

    @staticmethod
    async def _read_record(r: asyncio.StreamReader) -> bytes | None:
        out = bytearray()
        while True:
            hdr = await r.readexactly(4)
            n = int.from_bytes(hdr, "big")
            last = bool(n & 0x80000000)
            n &= 0x7FFFFFFF
            if n == 0 or n > 4 * 1024 * 1024:
                return None
            out += await r.readexactly(n)
            if last:
                return bytes(out)

    @staticmethod
    def _pack_record(body: bytes) -> bytes:
        return struct.pack(">I", 0x80000000 | len(body)) + body

    @staticmethod
    def _rewrite_mnt_path(rec: bytes, cl) -> bytes:
        try:
            prog = struct.unpack(">I", rec[12:16])[0]
            proc = struct.unpack(">I", rec[20:24])[0]
            if prog != PROG_MOUNT or proc != 1 or len(rec) < 44:
                return rec
            good = str(getattr(cl, "mount_path", "") or "/")
            off = _skip_rpc_auth(rec, 24)
            if off > len(rec):
                return rec
            body = rec[:off]
            return body + _xdr_opaque(good.encode("utf-8"))
        except Exception:
            return rec

    @staticmethod
    def _normalize_rpc_auth_for_phone(rec: bytes) -> bytes:
        if len(rec) < 40:
            return rec
        off = _skip_rpc_auth(rec, 24)
        return rec[:24] + (b"\x00" * 16) + rec[off:]

    def _local_portmap_reply(self, rec: bytes) -> bytes | None:
        if len(rec) < 24:
            return None
        xid, mtype, _rpcvers, prog, _vers, proc = struct.unpack(">IIIIII", rec[:24])
        if mtype != 0 or prog != PROG_PORTMAP:
            return None
        reply_hdr = struct.pack(">IIIIII", xid, 1, 0, 0, 0, 0)
        if proc == 0:
            return reply_hdr
        if proc in (1, 2):
            return reply_hdr + struct.pack(">I", 1)
        if proc == 3:
            return reply_hdr + struct.pack(">I", self.port)
        if proc == 4:
            body = b""
            for p, v in ((PROG_PORTMAP, 2), (PROG_MOUNT, 3), (PROG_NFS, 3)):
                body += struct.pack(">IIIII", 1, p, v, 6, self.port)
                body += struct.pack(">IIIII", 1, p, v, 17, self.port)
            body += struct.pack(">I", 0)
            return reply_hdr + body
        return None

    def _local_mount_reply(self, rec: bytes, cl) -> bytes | None:
        if len(rec) < 24:
            return None
        xid, mtype, _rpcvers, prog, _vers, proc = struct.unpack(">IIIIII", rec[:24])
        if mtype != 0 or prog != PROG_MOUNT:
            return None
        root_fh = getattr(cl, "root_fh", b"") or b""
        if not root_fh:
            return None
        reply_hdr = struct.pack(">IIIIII", xid, 1, 0, 0, 0, 0)
        if proc in (0, 3, 4):
            return reply_hdr
        if proc == 1:
            return reply_hdr + struct.pack(">I", 0) + _xdr_opaque(root_fh) + struct.pack(">III", 2, 0, 1)
        if proc == 2:
            return reply_hdr + struct.pack(">I", 0)
        if proc == 5:
            mp = str(getattr(cl, "mount_path", "/sdcard") or "/sdcard").encode("utf-8")
            return reply_hdr + struct.pack(">I", 1) + _xdr_opaque(mp) + struct.pack(">II", 0, 0)
        return None

    async def _fetch_dir_entries(self, cl, fh: bytes) -> tuple[int, bytes, list[tuple[int, bytes, bytes, bytes | None]]]:
        now = time.monotonic()
        cached = self._dir_cache.get(fh)
        if cached is not None and (now - cached[0]) < 2.5:
            return 0, cached[1], cached[2]

        entries: list[tuple[int, bytes, bytes, bytes | None]] = []
        dir_attr_bytes = struct.pack(">I", 0)
        cookie = 0
        verf = 0
        seen_names: set[bytes] = set()

        for _page in range(50):
            plus_args = (
                _xdr_opaque(fh)
                + struct.pack(">QQII", cookie, verf, 65536, 1048576)
            )
            async with cl._lock:
                x = await cl._nfs(17, plus_args)
            st = x.u32()
            if st != 0:
                return st, dir_attr_bytes, []
            raw_buf = getattr(x, "b", None)
            if raw_buf is None:
                raw_buf = getattr(x, "buf", b"")
            pos_attr = "o" if hasattr(x, "o") else "pos"
            has_dir_attr = x.u32()
            if has_dir_attr:
                cur_o = getattr(x, pos_attr)
                raw_attr = _patch_fattr3_bytes(raw_buf[cur_o:cur_o + 84])
                setattr(x, pos_attr, cur_o + 84)
                if len(raw_attr) == 84:
                    dir_attr_bytes = struct.pack(">I", 1) + raw_attr
            verf = x.u64()
            got_in_page = 0
            last_phone_cookie = cookie
            while x.u32():
                fid = x.u64()
                name_b = x.opaque()
                last_phone_cookie = x.u64()
                entry_attr = b""
                if x.u32():
                    cur_o = getattr(x, pos_attr)
                    entry_attr = _patch_fattr3_bytes(raw_buf[cur_o:cur_o + 84])
                    setattr(x, pos_attr, cur_o + 84)
                entry_fh: bytes | None = None
                if x.u32():
                    entry_fh = x.opaque()
                got_in_page += 1
                if name_b in (b"", b".", b"..") or name_b in seen_names:
                    continue
                seen_names.add(name_b)
                entries.append((fid or len(entries) + 2, name_b, entry_attr, entry_fh))
            eof = x.u32()
            if eof or got_in_page == 0 or last_phone_cookie <= cookie:
                break
            cookie = last_phone_cookie

        if len(self._dir_cache) > 128:
            self._dir_cache.clear()
        self._dir_cache[fh] = (now, dir_attr_bytes, entries)
        return 0, dir_attr_bytes, entries

    async def _local_or_fixed_nfs_reply(self, rec: bytes, cl) -> bytes | None:
        if len(rec) < 24:
            return None
        xid, mtype, _rpcvers, prog, _vers, proc = struct.unpack(">IIIIII", rec[:24])
        if mtype != 0 or prog != PROG_NFS:
            return None
        reply_hdr = struct.pack(">IIIIII", xid, 1, 0, 0, 0, 0)
        if proc == 0:
            return reply_hdr
        if proc == 20:
            return reply_hdr + struct.pack(
                ">IIIIIIII",
                0,
                0,
                1023,
                255,
                1,
                1,
                0,
                1,
            )
        if proc == 19 and hasattr(cl, "_nfs"):
            return reply_hdr + struct.pack(
                ">IIIIIIIIIQIII",
                0,
                0,
                65536,
                65536,
                4096,
                65536,
                65536,
                4096,
                65536,
                0x7FFFFFFFFFFFFFFF,
                1,
                0,
                0x001B,
            )
        if proc == 5:
            return reply_hdr + struct.pack(">II", 10004, 0)
        if proc in (10, 11):
            return reply_hdr + struct.pack(">III", 10004, 0, 0)
        if proc in (16, 17) and hasattr(cl, "_nfs"):
            try:
                off = _skip_rpc_auth(rec, 24)
                if off + 4 > len(rec):
                    return None
                fh_len = struct.unpack(">I", rec[off:off + 4])[0]
                off += 4
                fh = rec[off:off + fh_len]
                off += (fh_len + 3) & ~3
                if proc == 16:
                    if off + 20 > len(rec):
                        return None
                    cookie, _cookieverf, count = struct.unpack(">QQI", rec[off:off + 20])
                    max_bytes = max(2048, min(int(count), 262144))
                else:
                    if off + 24 > len(rec):
                        return None
                    cookie, _cookieverf, dircount, maxcount = struct.unpack(">QQII", rec[off:off + 24])
                    max_bytes = max(2048, min(int(maxcount or dircount or 8192), 262144))

                st, dir_attr_bytes, entries = await self._fetch_dir_entries(cl, fh)
                if st != 0:
                    return reply_hdr + struct.pack(">II", st, 0)

                start_idx = max(0, int(cookie))
                entries_buf = bytearray()
                budget = max(512, max_bytes - 256)
                next_idx = start_idx

                for idx in range(start_idx, len(entries)):
                    fid, name_b, entry_attr, entry_fh = entries[idx]
                    seq_cookie = idx + 1
                    if proc == 16:
                        item_bytes = (
                            struct.pack(">IQ", 1, fid)
                            + _xdr_opaque(name_b)
                            + struct.pack(">Q", seq_cookie)
                        )
                    else:
                        attr_part = (struct.pack(">I", 1) + entry_attr) if len(entry_attr) == 84 else struct.pack(">I", 0)
                        fh_part = (struct.pack(">I", 1) + _xdr_opaque(entry_fh)) if entry_fh else struct.pack(">I", 0)
                        item_bytes = (
                            struct.pack(">IQ", 1, fid)
                            + _xdr_opaque(name_b)
                            + struct.pack(">Q", seq_cookie)
                            + attr_part
                            + fh_part
                        )
                    if entries_buf and (len(entries_buf) + len(item_bytes) > budget):
                        break
                    entries_buf += item_bytes
                    next_idx = idx + 1

                eof = 1 if next_idx >= len(entries) else 0
                entries_buf += struct.pack(">II", 0, eof)
                self.rpc_forwarded += 1
                return (
                    reply_hdr
                    + struct.pack(">I", 0)
                    + dir_attr_bytes
                    + struct.pack(">Q", 0)
                    + bytes(entries_buf)
                )
            except Exception:
                return None
        return None

    def _repair_phone_nfs_reply(self, proc: int, raw_reply: bytes) -> bytes:
        try:
            if len(raw_reply) < 28:
                return raw_reply
            vlen = struct.unpack(">I", raw_reply[16:20])[0]
            pay = 24 + ((vlen + 3) & ~3)
            if pay + 4 > len(raw_reply):
                return raw_reply
            accept_stat = struct.unpack(">I", raw_reply[pay - 4:pay])[0]
            if accept_stat != 0:
                return raw_reply
            status = struct.unpack(">I", raw_reply[pay:pay + 4])[0]
            rest = raw_reply[pay + 4:]

            if proc in (7, 8, 9, 12, 13, 14):
                self._dir_cache.clear()

            if proc == 1:
                if status == 0 and len(rest) >= 84:
                    return raw_reply[:pay + 4] + _patch_fattr3_bytes(rest[:84]) + rest[84:]
                return raw_reply

            if proc == 3:
                if status != 0:
                    if len(rest) == 0:
                        return raw_reply + struct.pack(">I", 0)
                    if len(rest) == 4 and struct.unpack(">I", rest)[0] == 1:
                        return raw_reply[:pay + 4] + struct.pack(">I", 0)
                    if len(rest) >= 88 and struct.unpack(">I", rest[:4])[0] == 1:
                        return raw_reply[:pay + 8] + _patch_fattr3_bytes(rest[4:88]) + rest[88:]
                    return raw_reply
                if len(rest) >= 4:
                    fh_len = struct.unpack(">I", rest[:4])[0]
                    fh_end = 4 + ((fh_len + 3) & ~3)
                    out = bytearray(raw_reply[:pay + 4] + rest[:fh_end])
                    cur = fh_end
                    for _ in range(2):
                        if cur + 4 > len(rest):
                            out += struct.pack(">I", 0)
                            break
                        follows = struct.unpack(">I", rest[cur:cur + 4])[0]
                        cur += 4
                        if follows and cur + 84 <= len(rest):
                            out += struct.pack(">I", 1) + _patch_fattr3_bytes(rest[cur:cur + 84])
                            cur += 84
                        else:
                            out += struct.pack(">I", 0)
                    return bytes(out)

            if proc == 4:
                if status != 0 and len(rest) == 0:
                    return raw_reply + struct.pack(">I", 0)
                if status == 0 and len(rest) >= 92 and struct.unpack(">I", rest[:4])[0] == 1:
                    return raw_reply[:pay + 8] + _patch_fattr3_bytes(rest[4:88]) + rest[88:]

            if proc == 6 and status == 0 and len(rest) >= 88:
                if struct.unpack(">I", rest[:4])[0] == 1:
                    return raw_reply[:pay + 8] + _patch_fattr3_bytes(rest[4:88]) + rest[88:]

            if proc in (8, 9) and status == 0 and len(rest) >= 4:
                has_fh = struct.unpack(">I", rest[:4])[0]
                cur = 4
                if has_fh and len(rest) >= 8:
                    fh_len = struct.unpack(">I", rest[4:8])[0]
                    cur = 8 + ((fh_len + 3) & ~3)
                if cur + 88 <= len(rest) and struct.unpack(">I", rest[cur:cur + 4])[0] == 1:
                    patched = _patch_fattr3_bytes(rest[cur + 4:cur + 88])
                    return raw_reply[:pay + 4] + rest[:cur + 4] + patched + rest[cur + 88:]

            if proc in (2, 7, 8, 9, 12, 13, 21):
                if status != 0 and len(rest) == 0:
                    return raw_reply + struct.pack(">II", 0, 0)

            if proc == 14:
                if status != 0 and len(rest) == 0:
                    return raw_reply + struct.pack(">IIII", 0, 0, 0, 0)

            return raw_reply
        except Exception:
            return raw_reply

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                try:
                    rec = await self._read_record(reader)
                except (asyncio.IncompleteReadError, ConnectionError, OSError):
                    break
                if not rec or len(rec) < 24:
                    break
                pm_reply = self._local_portmap_reply(rec)
                if pm_reply is not None:
                    writer.write(self._pack_record(pm_reply))
                    await writer.drain()
                    continue
                cl = self._get_client()
                if cl is None:
                    xid = struct.unpack(">I", rec[:4])[0]
                    err_reply = struct.pack(">IIIIII", xid, 1, 0, 0, 0, 5)
                    writer.write(self._pack_record(err_reply))
                    await writer.drain()
                    continue
                prog = struct.unpack(">I", rec[12:16])[0]
                proc = struct.unpack(">I", rec[20:24])[0]
                if prog == PROG_MOUNT:
                    mnt_reply = self._local_mount_reply(rec, cl)
                    if mnt_reply is not None:
                        self.rpc_forwarded += 1
                        writer.write(self._pack_record(mnt_reply))
                        await writer.drain()
                        continue
                    rec = self._rewrite_mnt_path(rec, cl)
                elif prog == PROG_NFS:
                    try:
                        nfs_local = await self._local_or_fixed_nfs_reply(rec, cl)
                    except Exception as exc:
                        nfs_local = None
                        self.last_local_reply_error = f"proc={proc}: {exc!r}"[:200]
                    if nfs_local is not None:
                        self.rpc_forwarded += 1
                        writer.write(self._pack_record(nfs_local))
                        await writer.drain()
                        continue
                try:
                    call_to_send = self._normalize_rpc_auth_for_phone(rec) if hasattr(cl, "_nfs") else rec
                    raw_reply = await cl.forward_raw_rpc(prog, call_to_send)
                    if prog == PROG_NFS and hasattr(cl, "_nfs"):
                        raw_reply = self._repair_phone_nfs_reply(proc, raw_reply)
                    self.rpc_forwarded += 1
                    writer.write(self._pack_record(raw_reply))
                    await writer.drain()
                except Exception:
                    xid = struct.unpack(">I", rec[:4])[0]
                    err_reply = struct.pack(">IIIIII", xid, 1, 0, 0, 0, 5)
                    writer.write(self._pack_record(err_reply))
                    await writer.drain()
        finally:
            with contextlib.suppress(Exception):
                writer.close()

