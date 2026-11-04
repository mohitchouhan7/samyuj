from __future__ import annotations
import json

import asyncio
import contextlib
import hashlib
import os
import ssl
import struct
import tempfile
from pathlib import Path

from samyuj.core.errors import PhoneFsError, PhoneFsLost
from samyuj.protocol.nfs.xdr import (
    XdrReader as _Xdr,
    ftype_kind as _kind,
    nfs_error_message as _nfs_err,
    norm_phone_path,
    parse_fattr3 as _parse_fattr3,
    post_op_attr as _post_op_attr,
    xopaque as _xopaque,
    xu32 as _xu32,
    xu64 as _xu64,
)

class _Transport(PhoneFsError):
    pass

def _peer_fp_hex(writer) -> str:
    so = writer.get_extra_info("ssl_object")
    der = so.getpeercert(binary_form=True) if so is not None else None
    return hashlib.sha256(der).hexdigest().upper() if der else ""

def build_phonefs_client_ctx(cert_pem: bytes, key_pem: bytes) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    with contextlib.suppress(ssl.SSLError):
        ctx.set_ciphers("DEFAULT:@SECLEVEL=0")
    with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as f:
        with contextlib.suppress(OSError):
            os.fchmod(f.fileno(), 0o600)
        f.write(cert_pem.rstrip() + b"\n" + key_pem)
        chain = f.name
    try:
        os.chmod(chain, 0o600)
        ctx.load_cert_chain(chain)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(chain)
    return ctx

class PhoneNfsClient:

    RPC_TIMEOUT = 30.0
    READ_CHUNK = 262144

    def __init__(self, host: str, portmap_port: int, nfs_port: int, server_fp: str,
                 ssl_ctx: ssl.SSLContext | None, mount_path: str = "/sdcard",
                 timeout: float = 15.0, dt2type: int = 2, nfs_data_port: int = 0) -> None:
        self.host, self.portmap_port, self.nfs_port = host, int(portmap_port), int(nfs_port)
        self.nfs_data_port = int(nfs_data_port or nfs_port)
        self.dt2type = int(dt2type or 2)
        self.server_fp = (server_fp or "").upper()
        self._ctx, self.mount_path, self.timeout = ssl_ctx, mount_path, timeout
        self.mount_port = 0
        self.root_fh = b"\x00" * 64
        self._fh: dict[str, bytes] = {}
        self._rw = None
        self._xid = int.from_bytes(os.urandom(2), "big")
        self._lock = asyncio.Lock()

    async def _open(self, port: int):
        use_ssl = self._ctx if self.dt2type != 1 else None
        try:
            r, w = await asyncio.wait_for(
                asyncio.open_connection(
                    self.host, int(port),
                    ssl=use_ssl,
                    server_hostname=self.host if use_ssl else None,
                ),
                self.timeout,
            )
        except ssl.SSLError as ssl_exc:
            if not self.server_fp and ("WRONG_VERSION_NUMBER" in str(ssl_exc) or "UNKNOWN_PROTOCOL" in str(ssl_exc)):
                try:
                    r, w = await asyncio.wait_for(
                        asyncio.open_connection(self.host, int(port), ssl=None),
                        self.timeout,
                    )
                    return r, w
                except Exception:
                    pass
            raise _Transport(f"cannot open TLS connection to {self.host}:{port}: "
                             f"{ssl_exc.__class__.__name__}: {ssl_exc}") from ssl_exc
        except (ConnectionError, OSError, asyncio.TimeoutError) as exc:
            raise _Transport(f"cannot open connection to {self.host}:{port}: "
                             f"{exc.__class__.__name__}: {exc}") from exc
        if use_ssl is not None:
            got = _peer_fp_hex(w)
            if self.server_fp and got != self.server_fp:
                w.close()
                raise PhoneFsError(f"phone TLS certificate fingerprint mismatch "
                                   f"(got {got[:16]}…, announced {self.server_fp[:16]}…) — refusing")
        return r, w

    @staticmethod
    def _close(rw) -> None:
        if rw is not None:
            with contextlib.suppress(Exception):
                rw[1].close()

    async def _read_record(self, r) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            n = struct.unpack(">I", await r.readexactly(4))[0]
            last, n = bool(n & 0x80000000), n & 0x7FFFFFFF
            total += n
            if total > (64 << 20):
                raise _Transport("reply record too large")
            chunks.append(await r.readexactly(n))
            if last:
                return b"".join(chunks)

    async def _rpc(self, rw, prog: int, vers: int, proc: int, args: bytes = b"") -> _Xdr:
        r, w = rw
        self._xid = (self._xid + 1) & 0x7FFFFFFF or 1
        xid = self._xid
        call = struct.pack(">IIIIII", xid, 0, 2, prog, vers, proc) + b"\x00" * 16 + args
        try:
            w.write(struct.pack(">I", 0x80000000 | len(call)) + call)
            await w.drain()
            payload = await asyncio.wait_for(self._read_record(r), self.RPC_TIMEOUT)
        except (asyncio.IncompleteReadError, ssl.SSLError, ConnectionError, OSError,
                asyncio.TimeoutError) as exc:
            self._close(rw)
            if self._rw is rw:
                self._rw = None
            raise _Transport(f"phone closed/lost the connection ({exc.__class__.__name__}) — "
                             "a rejected client certificate fingerprint looks like this") from exc
        except BaseException:
            self._close(rw)
            if self._rw is rw:
                self._rw = None
            raise
        x = _Xdr(payload)
        if x.u32() != xid:
            raise _Transport("RPC xid mismatch")
        if x.u32() != 1 or x.u32() != 0:
            raise PhoneFsError("RPC call denied by the phone")
        x.u32()
        x.skip((x.u32() + 3) & ~3)
        acc = x.u32()
        if acc != 0:
            raise PhoneFsError(f"RPC not accepted by the phone (accept_stat={acc})")
        return x

    async def connect(self) -> dict:
        async with self._lock:
            return await self._connect_locked()

    async def _connect_locked(self) -> dict:
        self._close(self._rw)
        self._rw = None
        self._fh = {"/": self.root_fh}
        self.mount_port = 0
        pm_nfs_port = 0
        with contextlib.suppress(Exception):
            pm = await self._open(self.portmap_port)
            try:
                x = await self._rpc(pm, 100000, 2, 3,
                                    _xu32(100005) + _xu32(3) + _xu32(6) + _xu32(0))
                self.mount_port = x.u32()
                with contextlib.suppress(Exception):
                    x_nfs = await self._rpc(pm, 100000, 2, 3,
                                            _xu32(100003) + _xu32(3) + _xu32(6) + _xu32(0))
                    pm_nfs_port = x_nfs.u32()
            finally:
                self._close(pm)
        if not self.mount_port:
            self.mount_port = self.nfs_port or self.portmap_port
        if pm_nfs_port > 0:
            self.nfs_port = pm_nfs_port
        if self.mount_port == self.portmap_port:
            await asyncio.sleep(0.015)
        mt = await self._open(self.mount_port)
        try:
            cand_paths = []
            for cp in (self.mount_path, "/sdcard", "/storage/emulated/0", "/"):
                if cp and cp not in cand_paths:
                    cand_paths.append(cp)
            last_status = 0
            mounted_ok = False
            for cp in cand_paths:
                x = await self._rpc(mt, 100005, 3, 1, _xopaque(cp.encode()))
                last_status = x.u32()
                if last_status == 0:
                    self.root_fh = x.opaque()
                    self.mount_path = cp
                    mounted_ok = True
                    break
            if not mounted_ok:
                raise PhoneFsError(f"mount refused: {_nfs_err(last_status)}")
        finally:
            self._close(mt)
        self._fh = {"/": self.root_fh}
        if self.nfs_port == self.mount_port:
            await asyncio.sleep(0.015)
        cand_nfs_ports = []
        for np in (self.nfs_port, getattr(self, "nfs_data_port", 0), self.portmap_port):
            if isinstance(np, int) and np > 0 and np not in cand_nfs_ports:
                cand_nfs_ports.append(np)
        last_exc = None
        for np in cand_nfs_ports:
            try:
                self._rw = await self._open(np)
                x = await self._rpc(self._rw, 100003, 3, 1, _xopaque(self.root_fh))
                status = x.u32()
                if status:
                    raise PhoneFsError(f"cannot read the export root: {_nfs_err(status)}")
                self.nfs_port = np
                return {"mount_port": self.mount_port, "nfs_port": self.nfs_port, "root": _parse_fattr3(x)}
            except Exception as exc:
                last_exc = exc
                self._close(self._rw)
                self._rw = None
        raise last_exc or PhoneFsError("cannot connect to phone NFSv3 port")

    async def close(self) -> None:
        async with self._lock:
            self._close(self._rw)
            self._rw = None

    async def _nfs(self, proc: int, args: bytes) -> _Xdr:
        for attempt in (0, 1):
            try:
                if self._rw is None:
                    self._rw = await self._open(self.nfs_port)
                    self._fh = {"/": self.root_fh}
                return await self._rpc(self._rw, 100003, 3, proc, args)
            except _Transport as exc:
                self._close(self._rw)
                self._rw = None
                self._fh = {"/": self.root_fh}
                if attempt:
                    raise PhoneFsLost(f"connection to the phone's storage lost: {exc}") from exc
        raise AssertionError("unreachable")

    @staticmethod
    def _fh_from_fileid(fileid: int) -> bytes:
        return struct.pack("<I", int(fileid) & 0xFFFFFFFF) + b"\x00" * 60

    def _phone_raw_path(self, norm: str) -> str:
        mp = "/" + (self.mount_path or "/sdcard").strip("/")
        if mp == "/":
            return norm
        return mp if norm == "/" else f"{mp}{norm}"

    async def _getattrpath_attr_locked(self, path: str) -> tuple[int, dict | None]:
        norm = norm_phone_path(path)
        for cand_p in (self._phone_raw_path(norm), ("/sdcard" if norm == "/" else f"/sdcard{norm}")):
            with contextlib.suppress(Exception):
                x = await self._nfs(24, _xopaque(cand_p.encode("utf-8")))
                status = x.u32()
                if status == 0 and (len(x.b) - x.o) >= 84:
                    attr = _parse_fattr3(x)
                    fid = int(attr.get("fileid", 0) or 0)
                    if norm == "/":
                        self._fh["/"] = self.root_fh
                    elif fid > 0:
                        self._fh.setdefault(norm, self._fh_from_fileid(fid))
                    return 0, attr
                if status == 2:
                    return 2, None
        return 5, None

    async def _getattrpath_fh_locked(self, path: str) -> bytes | None:
        norm = norm_phone_path(path)
        st, attr = await self._getattrpath_attr_locked(norm)
        if st == 0 and attr is not None:
            fid = int(attr.get("fileid", 0) or 0)
            if norm == "/":
                return self.root_fh
            if fid > 0:
                fh = self._fh_from_fileid(fid)
                self._fh[norm] = fh
                return fh
        return None

    async def _resolve_locked(self, path: str, force_fresh: bool = False) -> bytes:
        path = norm_phone_path(path)
        if path == "/":
            if force_fresh:
                await self._getattrpath_fh_locked("/")
            self._fh["/"] = self.root_fh
            return self.root_fh
        if force_fresh:
            segs_clean = [s for s in path.strip("/").split("/") if s]
            cur_evict = ""
            for s in segs_clean:
                cur_evict += "/" + s
                self._fh.pop(cur_evict, None)
            await self._getattrpath_fh_locked("/")
        elif path in self._fh:
            return self._fh[path]

        fh, cur = self.root_fh, ""
        for seg in path.strip("/").split("/"):
            prev_cur = cur or "/"
            cur += "/" + seg
            if not force_fresh and cur in self._fh:
                fh = self._fh[cur]
                continue
            x = await self._nfs(3, _xopaque(fh) + _xopaque(seg.encode("utf-8")))
            status = x.u32()
            if status != 0:
                if status == 2 and (prev_cur == "/" or force_fresh):
                    raise PhoneFsError(f"{cur}: {_nfs_err(2)}")
                fresh_parent_fh = None
                if prev_cur == "/":
                    fresh_parent_fh = self.root_fh
                else:
                    with contextlib.suppress(Exception):
                        fresh_parent_fh = await self._resolve_locked(prev_cur, force_fresh=True)
                if fresh_parent_fh is not None:
                    fh = fresh_parent_fh
                    x2 = await self._nfs(3, _xopaque(fh) + _xopaque(seg.encode("utf-8")))
                    status2 = x2.u32()
                    if status2 == 0:
                        fh = x2.opaque()
                        self._fh[cur] = fh
                        continue
                    if status2 == 2:
                        raise PhoneFsError(f"{cur}: {_nfs_err(2)}")
                direct_fh = await self._getattrpath_fh_locked(cur)
                if direct_fh is not None:
                    fh = direct_fh
                    continue
                raise PhoneFsError(f"{cur}: {_nfs_err(status)}")
            fh = x.opaque()
            self._fh[cur] = fh
        return fh

    async def stat(self, path: str) -> dict:
        norm = norm_phone_path(path)
        async with self._lock:
            last_status = 2
            for force in (False, True):
                try:
                    fh = await self._resolve_locked(norm, force_fresh=force)
                except PhoneFsError as exc:
                    if "status 2)" in str(exc) or force:
                        raise
                    continue
                x = await self._nfs(1, _xopaque(fh))
                status = x.u32()
                if status == 0:
                    a = _parse_fattr3(x)
                    return {"type": _kind(a["ftype"]), "size": a["size"], "mtime": a["mtime"]}
                last_status = status
                if force:
                    break
            st24, attr24 = await self._getattrpath_attr_locked(norm)
            if st24 == 0 and attr24 is not None:
                return {"type": _kind(attr24["ftype"]), "size": attr24["size"], "mtime": attr24["mtime"]}
            if st24 == 2:
                raise PhoneFsError(f"{norm}: {_nfs_err(2)}")
            raise PhoneFsError(f"{norm}: {_nfs_err(last_status)}")

    async def _readdirplus_locked(self, path: str, fh: bytes) -> tuple[int, list[dict]]:
        entries: list[dict] = []
        cookie = verf = 0
        for _page in range(100000):
            x = await self._nfs(17, _xopaque(fh) + _xu64(cookie) + _xu64(verf)
                                + _xu32(4096) + _xu32(1 << 20))
            status = x.u32()
            if status != 0:
                return status, entries
            _post_op_attr(x)
            verf = x.u64()
            got, last_cookie = 0, cookie
            while x.u32():
                fid = x.u64()
                name = x.opaque().decode("utf-8", "replace")
                last_cookie = x.u64()
                attr = _post_op_attr(x)
                efh = x.opaque() if x.u32() else None
                if name in (".", ".."):
                    got += 1
                    continue
                child_path = (path if path != "/" else "") + "/" + name
                if efh is not None and any(efh):
                    self._fh[child_path] = efh
                entries.append({
                    "name": name,
                    "path": child_path,
                    "remote_path": child_path,
                    "type": _kind(attr["ftype"]) if attr else "other",
                    "size": attr["size"] if attr else 0,
                    "mtime": attr["mtime"] if attr else 0})
                got += 1
            eof = x.u32()
            if eof or got == 0 or last_cookie == cookie:
                break
            cookie = last_cookie
        entries.sort(key=lambda e: (e["type"] != "dir", e["name"].casefold()))
        return 0, entries

    async def _readdir_proc16_locked(self, path: str, fh: bytes) -> list[dict]:
        entries: list[dict] = []
        cookie = verf = 0
        for _page in range(100000):
            x = await self._nfs(16, _xopaque(fh) + _xu64(cookie) + _xu64(verf) + _xu32(65536))
            status = x.u32()
            if status != 0:
                raise PhoneFsError(f"{path}: {_nfs_err(status)}")
            _post_op_attr(x)
            verf = x.u64()
            got, last_cookie = 0, cookie
            while x.u32():
                fid = x.u64()
                name = x.opaque().decode("utf-8", "replace")
                last_cookie = x.u64()
                if name in (".", ".."):
                    got += 1
                    continue
                child_path = (path if path != "/" else "") + "/" + name
                child_fh = self._fh_from_fileid(fid)
                self._fh[child_path] = child_fh
                ftype_str = "other"
                fsize = 0
                fmtime = 0.0
                with contextlib.suppress(Exception):
                    xg = await self._nfs(1, _xopaque(child_fh))
                    if xg.u32() == 0:
                        a = _parse_fattr3(xg)
                        ftype_str = _kind(a["ftype"])
                        fsize = a["size"]
                        fmtime = a["mtime"]
                entries.append({
                    "name": name,
                    "path": child_path,
                    "remote_path": child_path,
                    "type": ftype_str,
                    "size": fsize,
                    "mtime": fmtime,
                })
                got += 1
            eof = x.u32()
            if eof or got == 0 or last_cookie == cookie:
                break
            cookie = last_cookie
        entries.sort(key=lambda e: (e["type"] != "dir", e["name"].casefold()))
        return entries

    async def listdir(self, path: str) -> list[dict]:
        path = norm_phone_path(path)
        async with self._lock:
            last_err: Exception | None = None
            for force in (False, True):
                try:
                    fh = await self._resolve_locked(path, force_fresh=force)
                except PhoneFsError as exc:
                    last_err = exc
                    if not force:
                        continue
                    raise
                status, entries = await self._readdirplus_locked(path, fh)
                if status == 0:
                    return entries
                last_err = PhoneFsError(f"{path}: {_nfs_err(status)}")
                if force:
                    fh24 = await self._getattrpath_fh_locked(path)
                    if fh24 is not None:
                        st2, entries2 = await self._readdirplus_locked(path, fh24)
                        if st2 == 0:
                            return entries2
                        with contextlib.suppress(Exception):
                            return await self._readdir_proc16_locked(path, fh24)
                    with contextlib.suppress(Exception):
                        return await self._readdir_proc16_locked(path, fh)
            raise last_err or PhoneFsError(f"{path}: {_nfs_err(2)}")

    async def read_file(self, path: str) -> bytes:
        chunks = []
        async for ch in self.read_iter(path):
            chunks.append(ch)
        return b"".join(chunks)

    async def read_range_iter(self, path: str, offset: int = 0, length: int | None = None, chunk_size: int = 131072):
        norm = norm_phone_path(path)
        fh: bytes | None = None
        async with self._lock:
            with contextlib.suppress(PhoneFsError):
                fh = await self._resolve_locked(norm)
        cur_off = max(0, int(offset))
        remaining = int(length) if length is not None else None
        step = max(4096, min(int(chunk_size or self.READ_CHUNK), self.READ_CHUNK))
        while remaining is None or remaining > 0:
            req_n = step if remaining is None else min(step, remaining)
            async with self._lock:
                status = 5
                x = None
                if fh is not None:
                    x = await self._nfs(6, _xopaque(fh) + _xu64(cur_off) + _xu32(req_n))
                    status = x.u32()
                if status != 0:
                    with contextlib.suppress(PhoneFsError):
                        fh = await self._resolve_locked(norm, force_fresh=True)
                        x = await self._nfs(6, _xopaque(fh) + _xu64(cur_off) + _xu32(req_n))
                        status = x.u32()
                if status != 0:
                    raw_p = self._phone_raw_path(norm)
                    x25 = await self._nfs(25, _xu64(cur_off) + _xu32(req_n) + _xopaque(raw_p.encode("utf-8")))
                    st25 = x25.u32()
                    if st25 == 0:
                        x = x25
                        status = 0
                    elif status == 5 and st25 != 0:
                        status = st25
                if status or x is None:
                    raise PhoneFsError(f"{norm}: {_nfs_err(status)}")
                _post_op_attr(x)
                count = x.u32()
                eof = bool(x.u32())
                data = x.opaque()[:count]
            if data:
                if remaining is not None:
                    data = data[:remaining]
                    remaining -= len(data)
                cur_off += len(data)
                yield data
            if eof or (not data and not count):
                return
            await asyncio.sleep(0)

    async def read_iter(self, path: str, size: int | None = None):
        norm = norm_phone_path(path)
        async with self._lock:
            fh = await self._resolve_locked(norm)
        offset = 0
        while True:
            async with self._lock:
                x = await self._nfs(6, _xopaque(fh) + _xu64(offset) + _xu32(self.READ_CHUNK))
                status = x.u32()
                if status == 2 and offset == 0:
                    fh = await self._resolve_locked(norm, force_fresh=True)
                    x = await self._nfs(6, _xopaque(fh) + _xu64(offset) + _xu32(self.READ_CHUNK))
                    status = x.u32()
                if status:
                    raise PhoneFsError(f"{norm}: {_nfs_err(status)}")
                _post_op_attr(x)
                count = x.u32()
                eof = bool(x.u32())
                data = x.opaque()[:count]
            if data:
                offset += len(data)
                yield data
            if eof or (not data and not count):
                return
            if size is not None and offset >= size and eof:
                return

    async def fsstat(self) -> dict:
        async with self._lock:
            x = await self._nfs(18, _xopaque(self.root_fh))
            status = x.u32()
            if status:
                raise PhoneFsError(f"FSSTAT failed: {_nfs_err(status)}")
            _post_op_attr(x)
            tbytes = x.u64()
            fbytes = x.u64()
            abytes = x.u64()
            return {"total_bytes": tbytes, "free_bytes": fbytes, "avail_bytes": abytes}

    async def write_file(self, path: str, data: bytes) -> dict:
        norm = norm_phone_path(path)
        parent = "/" + norm.strip("/").rpartition("/")[0] if "/" in norm.strip("/") else "/"
        name = norm.strip("/").rpartition("/")[2]
        if not name:
            raise PhoneFsError("invalid remote filename")
        sattr_none = _xu32(0) * 6
        async with self._lock:
            dfh = await self._resolve_locked(parent)
            x = await self._nfs(8, _xopaque(dfh) + _xopaque(name.encode("utf-8")) + _xu32(0) + sattr_none)
            st = x.u32()
            if st == 2:
                dfh = await self._resolve_locked(parent, force_fresh=True)
                x = await self._nfs(8, _xopaque(dfh) + _xopaque(name.encode("utf-8")) + _xu32(0) + sattr_none)
                st = x.u32()
            if st != 0:
                raise PhoneFsError(f"CREATE {norm} failed: {_nfs_err(st)}")
            has_fh = x.u32()
            fh = x.opaque() if has_fh else await self._resolve_locked(norm)
            self._fh[norm] = fh
        off = 0
        chunk_sz = 65536
        total = len(data)
        while off < total or total == 0:
            part = data[off:off + chunk_sz]
            async with self._lock:
                x = await self._nfs(
                    7,
                    _xopaque(fh) + _xu64(off) + _xu32(len(part)) + _xu32(2) + _xopaque(part),
                )
                st = x.u32()
                if st != 0:
                    raise PhoneFsError(f"WRITE {norm} at {off} failed: {_nfs_err(st)}")
            off += len(part)
            if total == 0:
                break
        return {"ok": True, "path": norm, "bytes": total}

    async def write_range(self, path: str, offset: int, data: bytes) -> dict:
        norm = norm_phone_path(path)
        total = len(data)
        if total == 0:
            return {"ok": True, "path": norm, "bytes": 0}
        chunk_sz = 65536
        base = max(0, int(offset))
        written = 0
        async with self._lock:
            fh = await self._resolve_locked(norm)
        while written < total:
            part = data[written:written + chunk_sz]
            async with self._lock:
                x = await self._nfs(
                    7,
                    _xopaque(fh) + _xu64(base + written) + _xu32(len(part)) + _xu32(2) + _xopaque(part),
                )
                st = x.u32()
                if st == 70:
                    fh = await self._resolve_locked(norm, force_fresh=True)
                    x = await self._nfs(
                        7,
                        _xopaque(fh) + _xu64(base + written) + _xu32(len(part)) + _xu32(2) + _xopaque(part),
                    )
                    st = x.u32()
            if st != 0:
                raise PhoneFsError(f"WRITE {norm} at {base + written} failed: {_nfs_err(st)}")
            written += len(part)
        return {"ok": True, "path": norm, "bytes": written}

    async def truncate(self, path: str, size: int = 0) -> dict:
        norm = norm_phone_path(path)
        sattr = (
            _xu32(0) + _xu32(0) + _xu32(0)
            + _xu32(1) + _xu64(max(0, int(size)))
            + _xu32(0) + _xu32(0)
        )
        async with self._lock:
            fh = await self._resolve_locked(norm)
            x = await self._nfs(2, _xopaque(fh) + sattr)
            st = x.u32()
            if st == 70:
                fh = await self._resolve_locked(norm, force_fresh=True)
                x = await self._nfs(2, _xopaque(fh) + sattr)
                st = x.u32()
        if st != 0:
            raise PhoneFsError(f"SETATTR(size={size}) {norm} failed: {_nfs_err(st)}")
        return {"ok": True, "path": norm, "size": max(0, int(size))}

    async def mkdir(self, path: str) -> dict:
        norm = norm_phone_path(path)
        parent = "/" + norm.strip("/").rpartition("/")[0] if "/" in norm.strip("/") else "/"
        name = norm.strip("/").rpartition("/")[2]
        sattr_none = _xu32(0) * 6
        async with self._lock:
            dfh = await self._resolve_locked(parent)
            x = await self._nfs(9, _xopaque(dfh) + _xopaque(name.encode("utf-8")) + sattr_none)
            st = x.u32()
            if st == 2:
                dfh = await self._resolve_locked(parent, force_fresh=True)
                x = await self._nfs(9, _xopaque(dfh) + _xopaque(name.encode("utf-8")) + sattr_none)
                st = x.u32()
            if st != 0:
                raise PhoneFsError(f"MKDIR {norm} failed: {_nfs_err(st)}")
            if x.u32():
                self._fh[norm] = x.opaque()
        return {"ok": True, "path": norm}

    async def remove(self, path: str) -> dict:
        norm = norm_phone_path(path)
        parent = "/" + norm.strip("/").rpartition("/")[0] if "/" in norm.strip("/") else "/"
        name = norm.strip("/").rpartition("/")[2]
        async with self._lock:
            dfh = await self._resolve_locked(parent)
            x = await self._nfs(12, _xopaque(dfh) + _xopaque(name.encode("utf-8")))
            st = x.u32()
            if st != 0:
                x2 = await self._nfs(13, _xopaque(dfh) + _xopaque(name.encode("utf-8")))
                st2 = x2.u32()
                if st2 != 0:
                    raise PhoneFsError(f"REMOVE {norm} failed: {_nfs_err(st)}")
            self._fh.pop(norm, None)
        return {"ok": True, "path": norm}

    async def rmdir(self, path: str) -> dict:
        norm = norm_phone_path(path)
        parent = "/" + norm.strip("/").rpartition("/")[0] if "/" in norm.strip("/") else "/"
        name = norm.strip("/").rpartition("/")[2]
        async with self._lock:
            dfh = await self._resolve_locked(parent)
            x = await self._nfs(13, _xopaque(dfh) + _xopaque(name.encode("utf-8")))
            st = x.u32()
            if st != 0:
                raise PhoneFsError(f"RMDIR {norm} failed: {_nfs_err(st)}")
            self._fh.pop(norm, None)
        return {"ok": True, "path": norm}

    async def rename(self, src_path: str, dst_path: str) -> dict:
        norm_src = norm_phone_path(src_path)
        norm_dst = norm_phone_path(dst_path)
        src_parent = "/" + norm_src.strip("/").rpartition("/")[0] if "/" in norm_src.strip("/") else "/"
        src_name = norm_src.strip("/").rpartition("/")[2]
        dst_parent = "/" + norm_dst.strip("/").rpartition("/")[0] if "/" in norm_dst.strip("/") else "/"
        dst_name = norm_dst.strip("/").rpartition("/")[2]
        if not src_name or not dst_name:
            raise PhoneFsError("invalid source or destination filename")
        async with self._lock:
            src_dfh = await self._resolve_locked(src_parent)
            dst_dfh = await self._resolve_locked(dst_parent)
            args = (
                _xopaque(src_dfh)
                + _xopaque(src_name.encode("utf-8"))
                + _xopaque(dst_dfh)
                + _xopaque(dst_name.encode("utf-8"))
            )
            x = await self._nfs(14, args)
            st = x.u32()
            if st != 0:
                raise PhoneFsError(f"RENAME {norm_src} -> {norm_dst} failed: {_nfs_err(st)}")
            old_fh = self._fh.pop(norm_src, None)
            if old_fh is not None:
                self._fh[norm_dst] = old_fh
        return {"ok": True, "src": norm_src, "dst": norm_dst}

    async def read_category_dir(self, category: int = 7, offset: int = 0, limit: int = 500) -> list[dict]:
        items: list[dict] = []
        async with self._lock:
            args = _xopaque(self.root_fh) + _xu32(int(offset)) + _xu32(int(limit)) + _xu32(int(category))
            x = await self._nfs(23, args)
            st = x.u32()
            if st != 0:
                return items
            while x.o + 4 <= len(x.b):
                follows = x.u32()
                if not follows:
                    break
                s1 = x.opaque().decode("utf-8", "replace")
                if s1.startswith("{"):
                    try:
                        meta = json.loads(s1)
                        rpath = norm_phone_path(str(meta.get("path") or ""))
                        ftype_n = int(meta.get("type", 2) or 2)
                        items.append({
                            "name": str(meta.get("name") or Path(rpath).name),
                            "path": rpath,
                            "remote_path": rpath,
                            "type": "dir" if ftype_n == 1 else "file",
                            "is_dir": (ftype_n == 1),
                            "size": int(meta.get("fileSize", 0) or 0),
                            "mtime": float(meta.get("modifyDate", 0) or 0),
                            "file_kind": "video" if ftype_n == 4 else ("image" if ftype_n == 2 else "file"),
                        })
                    except Exception:
                        break
                else:
                    rpath_raw = x.opaque().decode("utf-8", "replace")
                    name_raw = x.opaque().decode("utf-8", "replace")
                    ftype_n = x.u32()
                    fsize = x.u64()
                    mtime = x.u64()
                    _ctime = x.u64()
                    if x.o + 12 <= len(x.b):
                        w0, w1, w2 = struct.unpack_from(">III", x.b, x.o)
                        if w0 in (0, 1) and w1 in (0, 1) and w2 in (0, 1):
                            if w2 == 0 or (x.o + 16 <= len(x.b) and 0 < struct.unpack_from(">I", x.b, x.o + 12)[0] <= 256):
                                x.skip(8)
                    try:
                        rpath = norm_phone_path(rpath_raw)
                    except Exception:
                        rpath = "/" + rpath_raw.lstrip("/")
                    items.append({
                        "name": name_raw or Path(rpath).name,
                        "path": rpath,
                        "remote_path": rpath,
                        "type": "dir" if ftype_n == 1 else "file",
                        "is_dir": (ftype_n == 1),
                        "size": int(fsize),
                        "mtime": float(mtime),
                        "file_kind": "video" if ftype_n == 4 else ("image" if ftype_n == 2 else "file"),
                    })
        return items

    async def forward_raw_rpc(self, prog: int, raw_call: bytes) -> bytes:
        async with self._lock:
            if prog == 100005:
                mt = await self._open(self.mount_port or self.nfs_port)
                try:
                    r, w = mt
                    w.write(struct.pack(">I", 0x80000000 | len(raw_call)) + raw_call)
                    await w.drain()
                    return await asyncio.wait_for(self._read_record(r), self.RPC_TIMEOUT)
                finally:
                    self._close(mt)
            for attempt in (0, 1):
                try:
                    if self._rw is None:
                        self._rw = await self._open(self.nfs_port)
                    r, w = self._rw
                    w.write(struct.pack(">I", 0x80000000 | len(raw_call)) + raw_call)
                    await w.drain()
                    return await asyncio.wait_for(self._read_record(r), self.RPC_TIMEOUT)
                except (ConnectionError, OSError, asyncio.IncompleteReadError, asyncio.TimeoutError, _Transport):
                    self._close(self._rw)
                    self._rw = None
                    if attempt:
                        raise
