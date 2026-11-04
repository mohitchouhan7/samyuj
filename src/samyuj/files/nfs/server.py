from __future__ import annotations
import datetime
from samyuj.protocol.nfs.xdr import NFS3ERR_ACCES
import concurrent.futures

import asyncio
import contextlib
import ssl
import struct
import time
from pathlib import Path

from samyuj.protocol.nfs.xdr import (
    MNT3_OK,
    MNT3ERR_ACCES,
    PROG_MOUNT,
    PROG_NFS,
    PROG_NFS_LOCAL,
    PROG_PORTMAP,
    RPC_GARBAGE_ARGS,
    RPC_PROC_UNAVAIL,
    RPC_PROG_MISMATCH,
    RPC_PROG_UNAVAIL,
    RPC_SUCCESS,
    RPC_SYSTEM_ERR,
    XdrReader as _Xdr,
    XdrWriter as _XdrWriter,
    xopaque as _xop,
)
from .cnfs import CnfsProceduresMixin
from .exports import Export, ShareEntry
from .procedures import Nfs3ProceduresMixin, _MAX_READ, _MAX_WRITE

class NfsRpcServer(Nfs3ProceduresMixin, CnfsProceduresMixin):
    MAX_RPC_RECORD_BYTES = 16 * 1024 * 1024

    def __init__(self, export: Export, announced_path: str = "/C/nfs_demo",
                 on_event=None, allow_ips=None, read_max: int = _MAX_READ,
                 write_max: int = _MAX_WRITE,
                 require_tls_external: bool = True,
                 nfs_version: int = 8,
                 nfs_capability: int = 25) -> None:
        self.export = export
        self.announced_path = announced_path or "/C/nfs_demo"
        self.on_event = on_event
        self.allow_ips = allow_ips
        self.read_max = int(read_max)
        self.write_max = int(write_max)
        self.require_tls_external = bool(require_tls_external)
        self.nfs_version = int(nfs_version)
        self.nfs_capability = int(nfs_capability)
        self.portmap: dict[int, int] = {}
        self.stats: dict[str, int] = {"calls": 0, "mounts": 0, "reads": 0,
                                      "writes": 0, "lookups": 0, "readdirs": 0,
                                      "errors": 0, "unhandled": 0, "cnfs_calls": 0}
        self.clients: dict[str, dict] = {}
        self._srv: list = []
        self._conns: set = set()
        self._unhandled_path: Path | None = None
        self._lock = None
        self._rpc_lock = __import__("threading").RLock()
        self._io_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=8, thread_name_prefix="sc-nfs-io"
        )

    async def start_listener(self, host: str, port: int, ssl_ctx=None) -> int:
        srv = await asyncio.start_server(
            lambda r, w, sc=ssl_ctx: self._client(r, w, sc), host, port, ssl=None)
        self._srv.append(srv)
        p = srv.sockets[0].getsockname()[1]
        return p

    async def stop(self) -> None:
        for s in self._srv:
            s.close()
        for t in list(self._conns):
            t.cancel()
        self._srv.clear()
        self._conns.clear()
        with contextlib.suppress(Exception):
            self._io_pool.shutdown(wait=False)

    def set_export(self, root, readonly: bool | None = None) -> Export:
        old_shares = dict(getattr(self.export, "shares", {}) or {})
        old_rev = int(getattr(self.export, "revision", 1))
        new_exp = Export(root, self.export.readonly if readonly is None else bool(readonly))
        for k, entry in old_shares.items():
            new_exp.shares[k] = entry
        new_exp.revision = old_rev + 1
        self.export = new_exp
        self.stats["mounts"] = 0
        return self.export

    def add_share(self, alias: str, host_path: str | Path, access_mode: str = "rw",
                  enabled: bool = True) -> ShareEntry:
        return self.export.add_share(alias, host_path, access_mode=access_mode, enabled=enabled)

    def remove_share(self, alias: str) -> bool:
        return self.export.remove_share(alias)

    def set_share_access_mode(self, alias: str, access_mode: str) -> bool:
        return self.export.set_share_access_mode(alias, access_mode)

    def list_shares(self) -> list[dict]:
        return self.export.list_shares()

    def _ev(self, kind: str, detail: str, rel: str | None = None) -> None:
        if self.on_event is None:
            return
        try:
            self.on_event(kind, detail, rel)
        except Exception:
            pass

    def _unhandled(self, what: str, raw: bytes) -> None:
        self.stats["unhandled"] += 1
        if self._unhandled_path is None:
            return
        try:
            with open(self._unhandled_path, "a", encoding="utf-8") as f:
                f.write(f"{datetime.datetime.now().isoformat()} {what} "
                        f"rawargs={raw.hex()[:512]}\n")
        except OSError:
            pass

    @staticmethod
    async def _read_record(r: asyncio.StreamReader) -> bytes | None:
        out = bytearray()
        while True:
            hdr = await r.readexactly(4)
            n = int.from_bytes(hdr, "big")
            last = bool(n & 0x80000000)
            n &= 0x7FFFFFFF
            if n == 0 or n > 8 * 1024 * 1024:
                raise ValueError(f"implausible RPC fragment length {n}")
            out += await r.readexactly(n)
            if last:
                return bytes(out)

    @staticmethod
    def _record(body: bytes) -> bytes:
        return struct.pack(">I", 0x80000000 | len(body)) + body

    async def _client(self, reader: asyncio.StreamReader,
                      writer: asyncio.StreamWriter,
                      ssl_ctx: ssl.SSLContext | None = None) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._conns.add(task)
        peer = writer.get_extra_info("peername")
        ip = str(peer[0]).replace("::ffff:", "") if peer else ""
        try:
            first_chunk = await asyncio.wait_for(reader.read(4096), timeout=30.0)
        except Exception:
            first_chunk = b""
        if not first_chunk:
            if task is not None:
                self._conns.discard(task)
            with contextlib.suppress(Exception):
                writer.close()
            return

        is_loopback = ip in ("127.0.0.1", "::1", "")
        if not is_loopback and self.allow_ips is not None and ip not in self.allow_ips:
            self._ev("NFS_REJECT_PEER", f"peer={peer} ip={ip} not in allowed_ips={sorted(self.allow_ips)}")
            if task is not None:
                self._conns.discard(task)
            with contextlib.suppress(Exception):
                writer.close()
            return
        is_tls_hello = (first_chunk[0] == 0x16 and ssl_ctx is not None)
        if not is_tls_hello and ssl_ctx is not None and self.require_tls_external and not is_loopback:
            self._ev("NFS_REJECT_PLAIN_TCP", f"peer={peer} plain-TCP rejected on external interface; TLS 1.2 required")
            if task is not None:
                self._conns.discard(task)
            with contextlib.suppress(Exception):
                writer.close()
            return
        in_bio = ssl.MemoryBIO() if is_tls_hello else None
        out_bio = ssl.MemoryBIO() if is_tls_hello else None
        ssl_obj = ssl_ctx.wrap_bio(in_bio, out_bio, server_side=True) if is_tls_hello else None
        plain_buf = bytearray()

        if is_tls_hello:
            in_bio.write(first_chunk)
            try:
                while True:
                    try:
                        ssl_obj.do_handshake()
                        pending = out_bio.read()
                        if pending:
                            writer.write(pending)
                            await writer.drain()
                        break
                    except ssl.SSLWantReadError:
                        pending = out_bio.read()
                        if pending:
                            writer.write(pending)
                            await writer.drain()
                        more = await asyncio.wait_for(reader.read(16384), timeout=15.0)
                        if not more:
                            raise ConnectionError("EOF during TLS handshake")
                        in_bio.write(more)
                with contextlib.suppress(ssl.SSLWantReadError):
                    while True:
                        dec = ssl_obj.read(65536)
                        if not dec:
                            break
                        plain_buf.extend(dec)
            except Exception as tls_exc:
                self._ev("NFS_TLS_ERR", f"peer={peer} {tls_exc!r}")
                if task is not None:
                    self._conns.discard(task)
                with contextlib.suppress(Exception):
                    writer.close()
                return
        else:
            plain_buf.extend(first_chunk)

        tls_ver = (ssl_obj.version() or "TLS") if ssl_obj is not None else "plain"
        self._ev("NFS_CONN", f"peer={peer} tls={tls_ver} export={self.export.root}")
        self.clients[ip] = {"peer": str(peer), "tls": tls_ver, "since": time.time(),
                            "calls": 0}

        async def read_exact_plain(n: int) -> bytes:
            while len(plain_buf) < n:
                if ssl_obj is not None:
                    try:
                        dec = ssl_obj.read(65536)
                        if dec:
                            plain_buf.extend(dec)
                            continue
                    except ssl.SSLWantReadError:
                        pass
                    pending = out_bio.read()
                    if pending:
                        writer.write(pending)
                        await writer.drain()
                raw_more = await reader.read(65536)
                if not raw_more:
                    raise asyncio.IncompleteReadError(bytes(plain_buf), n)
                if ssl_obj is not None:
                    in_bio.write(raw_more)
                else:
                    plain_buf.extend(raw_more)
            res = bytes(plain_buf[:n])
            del plain_buf[:n]
            return res

        async def read_rpc_rec() -> bytes | None:
            out = bytearray()
            while True:
                hdr = await read_exact_plain(4)
                n = int.from_bytes(hdr, "big")
                last = bool(n & 0x80000000)
                n &= 0x7FFFFFFF
                if n == 0 or n > self.MAX_RPC_RECORD_BYTES or (len(out) + n) > self.MAX_RPC_RECORD_BYTES:
                    raise ValueError(f"implausible RPC fragment length {n}")
                out += await read_exact_plain(n)
                if last:
                    return bytes(out)

        async def write_rpc_rec(raw_reply: bytes) -> None:
            framed = self._record(raw_reply)
            if ssl_obj is not None:
                off = 0
                while off < len(framed):
                    off += ssl_obj.write(framed[off:])
                    pending = out_bio.read()
                    if pending:
                        writer.write(pending)
                await writer.drain()
            else:
                writer.write(framed)
                await writer.drain()

        loop = asyncio.get_running_loop()
        try:
            while True:
                try:
                    rec = await read_rpc_rec()
                except (asyncio.IncompleteReadError, ConnectionError):
                    break
                except (ValueError, OSError, ssl.SSLError) as exc:
                    self._ev("RPC_GARBAGE", f"peer={peer} {exc!r}")
                    break
                if rec is None:
                    break
                try:
                    reply = await loop.run_in_executor(self._io_pool, self._dispatch, rec, ip, peer)
                except Exception as exc:
                    self.stats["errors"] += 1
                    self._ev("RPC_INTERNAL_ERROR", f"peer={peer} {exc!r}")
                    reply = self._rpc_error(rec, RPC_SYSTEM_ERR)
                if reply is None:
                    break
                try:
                    await write_rpc_rec(reply)
                except (ConnectionError, OSError, ssl.SSLError):
                    break
        finally:
            self._ev("NFS_DISCONN", f"peer={peer} calls={self.clients.get(ip, {}).get('calls', 0)}")
            if task is not None:
                self._conns.discard(task)
            with contextlib.suppress(Exception):
                writer.close()

    @staticmethod
    def _parse_call(rec: bytes) -> dict | None:
        if len(rec) < 28:
            return None
        xid, mtype, rpcvers, prog, vers, proc = struct.unpack(">IIIIII", rec[:24])
        if mtype != 0 or rpcvers != 2:
            return None
        off = 24
        if len(rec) < off + 8:
            return None
        c_flavor, c_len = struct.unpack(">II", rec[off:off + 8])
        if len(rec) < off + 8 + c_len:
            return None
        creds = rec[off + 8:off + 8 + c_len]
        off += 8 + c_len
        if len(rec) < off + 8:
            return None
        v_flavor, v_len = struct.unpack(">II", rec[off:off + 8])
        if len(rec) < off + 8 + v_len:
            return None
        verf = rec[off + 8:off + 8 + v_len]
        off += 8 + v_len
        return {"xid": xid, "prog": prog, "vers": vers, "proc": proc,
                "auth": (c_flavor, creds), "verf": (v_flavor, verf),
                "args": rec[off:], "off": off}

    def _reply(self, xid: int, verf: bytes, accept: int, body: bytes) -> bytes:
        return (struct.pack(">III", xid, 1, 0)
                + struct.pack(">I", 0)
                + _xop(verf or b"")
                + struct.pack(">I", accept) + body)

    def _ok(self, call: dict, body: bytes) -> bytes:
        return self._reply(call["xid"], call.get("verf", (0, b""))[1], RPC_SUCCESS, body)

    def _proc_unavail(self, call: dict) -> bytes:
        return self._reply(call["xid"], call.get("verf", (0, b""))[1],
                           RPC_PROC_UNAVAIL, b"")

    def _rpc_error(self, rec: bytes, accept: int) -> bytes | None:
        try:
            xid = int.from_bytes(rec[:4], "big")
        except Exception:
            return None
        return (struct.pack(">III", xid, 1, 0) + struct.pack(">I", 0)
                + struct.pack(">I", 0) + struct.pack(">I", accept))

    def _dispatch(self, rec: bytes, ip: str, peer) -> bytes | None:
        call = self._parse_call(rec)
        if call is None:
            self._ev("RPC_GARBAGE", f"peer={peer} undecodable CALL "
                                    f"({len(rec)} B) head={rec[:32].hex()}")
            return self._rpc_error(rec, RPC_GARBAGE_ARGS)
        self.stats["calls"] += 1
        if ip in self.clients:
            self.clients[ip]["calls"] += 1
        if self.allow_ips is not None and ip and ip not in ("127.0.0.1", "::1") and ip not in self.allow_ips:
            self._ev("NFS_REJECT_PEER",
                     f"{ip} is not the paired phone (allow={sorted(self.allow_ips)}); "
                     f"use --fs-any-peer to lift this")
            if call["prog"] == PROG_MOUNT:
                return self._ok(call, struct.pack(">I", MNT3ERR_ACCES))
            return self._ok(call, struct.pack(">I", NFS3ERR_ACCES))
        prog, vers, proc = call["prog"], call["vers"], call["proc"]
        if prog == PROG_PORTMAP:
            return self._do_portmap(call, ip, peer)
        if prog == PROG_MOUNT:
            if vers not in (1, 3):
                return self._reply(call["xid"], call.get("verf", (0, b""))[1], RPC_PROG_MISMATCH,
                                   struct.pack(">II", 3, 3))
            return self._do_mount(call, ip, peer)
        if prog in (PROG_NFS, PROG_NFS_LOCAL):
            if vers != 3:
                return self._reply(call["xid"], call.get("verf", (0, b""))[1], RPC_PROG_MISMATCH,
                                   struct.pack(">II", 3, 3))
            return self._do_nfs(call, ip, peer)
        self._ev("RPC_UNKNOWN_PROGRAM",
                 f"peer={peer} prog={prog} vers={vers} proc={proc}")
        return self._reply(call["xid"], call.get("verf", (0, b""))[1], RPC_PROG_UNAVAIL, b"")

    def _do_portmap(self, call: dict, ip: str, peer) -> bytes:
        proc = call["proc"]
        if proc == 0:
            return self._ok(call, b"")
        x = _Xdr(call["args"])
        if proc == 3:
            with contextlib.suppress(Exception):
                prog = x.u32()
                vers = x.u32()
                prot = x.u32()
                self._ev("NFS_PORTMAP_GETPORT",
                         f"peer={peer} prog={prog} vers={vers} prot={prot} "
                         f"-> {self.portmap.get(prog, self.portmap.get(PROG_MOUNT, 0))}")
                return self._ok(call, struct.pack(
                    ">I", self.portmap.get(prog, self.portmap.get(PROG_MOUNT, 0))))
            return self._ok(call, struct.pack(">I", 0))
        if proc == 1:
            with contextlib.suppress(Exception):
                prog, vers, prot, port = x.u32(), x.u32(), x.u32(), x.u32()
                self.portmap[prog] = port
                self._ev("NFS_PORTMAP_SET", f"peer={peer} prog={prog}:{vers}/{prot} -> {port}")
            return self._ok(call, struct.pack(">I", 1))
        if proc == 2:
            with contextlib.suppress(Exception):
                prog = x.u32()
                self.portmap.pop(prog, None)
            return self._ok(call, struct.pack(">I", 1))
        if proc == 4:
            w = _XdrWriter()
            for prog, port in sorted(self.portmap.items()):
                w.u32(1).u32(prog).u32(3).u32(6).u32(port)
            w.u32(0)
            return self._ok(call, w.bytes())
        self._unhandled(f"portmap proc={proc}", call["args"])
        return self._proc_unavail(call)

    def _do_mount(self, call: dict, ip: str, peer) -> bytes:
        proc = call["proc"]
        if proc == 0:
            return self._ok(call, b"")
        x = _Xdr(call["args"])
        if proc == 1:
            try:
                dirpath = x.opaque().decode("utf-8", "replace")
            except Exception:
                dirpath = ""
            self.export.mounts.append((ip, dirpath, time.time()))
            self.stats["mounts"] += 1
            self._ev("NFS_MOUNT",
                     f"peer={peer} dirpath={dirpath!r} export={self.export.root} "
                     f"(mount #{self.stats['mounts']})", "")
            sub_rel = ""
            clean_dp = "/" + (dirpath or "").strip("/")
            ann = "/" + (self.announced_path or "").strip("/")
            if clean_dp != "/" and clean_dp != ann:
                cand_sub = clean_dp[len(ann):].strip("/") if (ann != "/" and clean_dp.startswith(ann + "/")) else clean_dp.strip("/")
                if cand_sub and self.export.path(cand_sub) is not None and self.export.path(cand_sub).is_dir():
                    sub_rel = cand_sub
            root_fh = self.export.fh(sub_rel)
            w = _XdrWriter()
            w.u32(MNT3_OK).u32(len(root_fh)).fixed(root_fh)
            w.u32(1).u32(1)
            return self._ok(call, w.bytes())
        if proc == 2:
            w = _XdrWriter()
            for (mip, mdir, _t) in self.export.mounts[-16:]:
                w.u32(1).string(mip).string(mdir or self.announced_path)
            w.u32(0)
            return self._ok(call, w.bytes())
        if proc == 3:
            with contextlib.suppress(Exception):
                dirpath = x.opaque().decode("utf-8", "replace")
                self.export.mounts = [m for m in self.export.mounts
                                      if not (m[0] == ip and m[1] == dirpath)]
                self._ev("NFS_UNMOUNT", f"peer={peer} dirpath={dirpath!r}", "")
            return self._ok(call, b"")
        if proc == 4:
            self.export.mounts = [m for m in self.export.mounts if m[0] != ip]
            self._ev("NFS_UNMOUNT", f"peer={peer} UMNTALL", "")
            return self._ok(call, b"")
        if proc == 5:
            w = _XdrWriter()
            w.u32(1).string(self.announced_path)
            w.u32(1).string("*").u32(0)
            for alias in sorted(self.export.shares.keys()):
                w.u32(1).string(f"{self.announced_path.rstrip('/')}/{alias}")
                w.u32(1).string("*").u32(0)
            w.u32(0)
            return self._ok(call, w.bytes())
        self._unhandled(f"mount proc={proc}", call["args"])
        return self._proc_unavail(call)

