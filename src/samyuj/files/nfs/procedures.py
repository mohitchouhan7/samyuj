from __future__ import annotations
from samyuj.core.errors import PhoneFsError
from samyuj.protocol.nfs.xdr import NFS3ERR_NOT_SYNC, NFS3ERR_STALE, NFS3ERR_XDEV, RPC_GARBAGE_ARGS
import posixpath

import contextlib
import os
import struct
import time
from pathlib import Path

from samyuj.protocol.nfs.xdr import (
    ACCESS3_DELETE,
    ACCESS3_EXECUTE,
    ACCESS3_EXTEND,
    ACCESS3_LOOKUP,
    ACCESS3_MODIFY,
    ACCESS3_READ,
    NFS3_OK,
    NFS3ERR_ACCES,
    NFS3ERR_EXIST,
    NFS3ERR_INVAL,
    NFS3ERR_IO,
    NFS3ERR_ISDIR,
    NFS3ERR_NOENT,
    NFS3ERR_NOTDIR,
    NFS3ERR_NOTEMPTY,
    NFS3ERR_NOTSUPP,
    NFS3ERR_ROFS,
    XdrReader as _Xdr,
    XdrWriter as _XdrWriter,
    xtime as _xtime,
)

_WRITE_VERF = b"MCLNFSv3"
_MAX_READ = 1024 * 1024
_MAX_WRITE = 1024 * 1024
_FS_ID = 0x4D434C00

class Nfs3ProceduresMixin:

    def _fh_of(self, x: _Xdr) -> bytes:
        n = x.u32()
        if n <= 0 or n > 64:
            raise ValueError(f"bad nfs_fh3 length {n}")
        raw = x.b[x.o:x.o + n]
        x.o += n
        x.o += (4 - n % 4) % 4
        return bytes(raw)

    def _resolve(self, fh: bytes) -> tuple[str, Path] | None:
        rel = self.export.rel(fh)
        if rel is None:
            return None
        p = self.export.path(rel)
        if p is None or not p.exists():
            return None
        return rel, p

    @staticmethod
    def _parse_sattr3(x: _Xdr) -> dict:
        out: dict = {}
        if x.u32():
            out["mode"] = x.u32() & 0o1777
        if x.u32():
            out["uid"] = x.u32()
        if x.u32():
            out["gid"] = x.u32()
        if x.u32():
            out["size"] = x.u64()
        set_atime = x.u32()
        if set_atime == 1:
            out["atime"] = time.time()
        elif set_atime == 2:
            out["atime"] = x.u32() + x.u32() / 1e9
        set_mtime = x.u32()
        if set_mtime == 1:
            out["mtime"] = time.time()
        elif set_mtime == 2:
            out["mtime"] = x.u32() + x.u32() / 1e9
        return out

    @staticmethod
    def _apply_sattr3(p: Path, sattr: dict) -> None:
        if "size" in sattr and p.is_file():
            os.truncate(p, int(sattr["size"]))
        if "mode" in sattr:
            with contextlib.suppress(OSError):
                os.chmod(p, int(sattr["mode"]))
        if "atime" in sattr or "mtime" in sattr:
            with contextlib.suppress(OSError):
                st = p.lstat()
                at = float(sattr.get("atime", st.st_atime))
                mt = float(sattr.get("mtime", st.st_mtime))
                os.utime(p, (at, mt))

    _WCC_PROCS = frozenset({2, 7, 8, 9, 10, 11, 12, 13, 21})

    def _nfserr(self, call: dict, code: int, dir_path: Path | None = None,
                pre_stat=None) -> bytes:
        self.stats["errors"] += 1
        proc = call.get("proc", 0)
        if proc == 1:
            tail = b""
        elif proc in self._WCC_PROCS:
            tail = self.export.wcc(dir_path, pre_stat) if dir_path is not None else self.export.no_wcc()
        elif proc == 14:
            wcc = self.export.wcc(dir_path, pre_stat) if dir_path is not None else self.export.no_wcc()
            tail = wcc + self.export.no_wcc()
        elif proc == 15:
            tail = self.export.post_op_attr(dir_path) + self.export.no_wcc()
        else:
            tail = self.export.post_op_attr(dir_path)
        return self._ok(call, struct.pack(">I", code) + tail)

    def _nfs_proc_getattr(self, call: dict, x: _Xdr, _peer) -> bytes:
        got = self._resolve(self._fh_of(x))
        if not got:
            return self._nfserr(call, NFS3ERR_STALE)
        _rel, p = got
        return self._ok(call, struct.pack(">I", NFS3_OK) + self.export.fattr3(p, rel=_rel))

    def _nfs_proc_setattr(self, call: dict, x: _Xdr, peer) -> bytes:
        fh = self._fh_of(x)
        sattr = self._parse_sattr3(x)
        guard_check = x.u32() if (x.o + 4 <= len(x.b)) else 0
        guard_sec = x.u32() if (guard_check and x.o + 8 <= len(x.b)) else 0
        if guard_check and x.o + 4 <= len(x.b):
            _ = x.u32()
        got = self._resolve(fh)
        if not got:
            return self._nfserr(call, NFS3ERR_STALE)
        _rel, p = got
        if self.export.is_readonly(_rel):
            return self._nfserr(call, NFS3ERR_ROFS, p)
        pre_st = p.lstat()
        if guard_check and int(pre_st.st_ctime) != int(guard_sec):
            return self._nfserr(call, NFS3ERR_NOT_SYNC, p, pre_st)
        try:
            self._apply_sattr3(p, sattr)
        except OSError:
            return self._nfserr(call, NFS3ERR_IO, p, pre_st)
        self._ev("NFS_SETATTR", f"peer={peer} path={_rel or '/'} sattr={sattr}", _rel)
        return self._ok(call, struct.pack(">I", NFS3_OK) + self.export.wcc(p, pre_st))

    def _nfs_proc_lookup(self, call: dict, x: _Xdr, peer) -> bytes:
        dfh = self._fh_of(x)
        name = x.opaque().decode("utf-8", "surrogateescape")
        self.stats["lookups"] += 1
        dg = self._resolve(dfh)
        if not dg:
            return self._nfserr(call, NFS3ERR_STALE)
        drel, dp = dg
        if not dp.is_dir():
            return self._nfserr(call, NFS3ERR_NOTDIR, dp)
        if name in ("..", ".") or "/" in name or "\x00" in name:
            if name == ".":
                child_rel = drel
            elif name == "..":
                child_rel = "" if (not drel or "/" not in drel) else drel.rsplit("/", 1)[0]
            else:
                return self._nfserr(call, NFS3ERR_ACCES, dp)
        else:
            child_rel = posixpath.join(drel, name) if drel else name
        cp = self.export.path(child_rel)
        if cp is None:
            return self._nfserr(call, NFS3ERR_ACCES, dp)
        if not cp.exists():
            self._ev("NFS_LOOKUP", f"peer={peer} {drel or '/'}/{name} -> NOENT", child_rel)
            return self._nfserr(call, NFS3ERR_NOENT, dp)
        self._ev(
            "NFS_LOOKUP",
            f"peer={peer} {drel or '/'}/{name} -> OK {'dir' if cp.is_dir() else 'file'} "
            f"{cp.stat().st_size if cp.is_file() else ''}",
            child_rel,
        )
        w = _XdrWriter()
        w.u32(NFS3_OK).u32(64).fixed(self.export.fh(child_rel))
        w.fixed(self.export.post_op_attr(cp, rel=child_rel))
        w.fixed(self.export.post_op_attr(dp, rel=drel))
        return self._ok(call, w.bytes())

    def _nfs_proc_access(self, call: dict, x: _Xdr, _peer) -> bytes:
        fh = self._fh_of(x)
        want = x.u32()
        got = self._resolve(fh)
        if not got:
            return self._nfserr(call, NFS3ERR_STALE)
        _rel, p = got
        ro_eff = self.export.is_readonly(_rel)
        supported = ACCESS3_READ | ACCESS3_LOOKUP | ACCESS3_EXECUTE
        if not ro_eff:
            supported |= ACCESS3_MODIFY | ACCESS3_EXTEND | ACCESS3_DELETE
        granted = want & supported
        if p.is_dir():
            granted &= ~(ACCESS3_EXECUTE)
        w = _XdrWriter()
        w.u32(NFS3_OK).fixed(self.export.post_op_attr(p, rel=_rel)).u32(granted)
        return self._ok(call, w.bytes())

    def _nfs_proc_readlink(self, call: dict, x: _Xdr, _peer) -> bytes:
        fh = self._fh_of(x)
        got = self._resolve(fh)
        if not got:
            return self._nfserr(call, NFS3ERR_STALE)
        _rel, p = got
        try:
            target = os.readlink(p)
        except OSError:
            return self._nfserr(call, NFS3ERR_INVAL)
        w = _XdrWriter()
        w.u32(NFS3_OK).fixed(self.export.post_op_attr(p, rel=_rel)).string(target)
        return self._ok(call, w.bytes())

    def _nfs_proc_read(self, call: dict, x: _Xdr, peer) -> bytes:
        fh = self._fh_of(x)
        off = x.u64()
        cnt = x.u32()
        got = self._resolve(fh)
        if not got:
            return self._nfserr(call, NFS3ERR_STALE)
        rel, p = got
        if not p.is_file():
            return self._nfserr(call, NFS3ERR_ISDIR, p)
        cnt = max(0, min(cnt or self.read_max, self.read_max))
        try:
            with open(p, "rb") as f:
                f.seek(off)
                blob = f.read(cnt)
        except OSError as exc:
            self._ev("NFS_READ", f"peer={peer} {rel} off={off} ERR {exc!r}", rel)
            return self._nfserr(call, NFS3ERR_IO, p)
        eof = (off + len(blob)) >= p.stat().st_size
        self.stats["reads"] += 1
        if off == 0:
            self._ev("NFS_READ", f"peer={peer} {rel or '/'} off={off} len={len(blob)} eof={eof}", rel)
        w = _XdrWriter()
        w.u32(NFS3_OK).fixed(self.export.post_op_attr(p, rel=rel))
        w.u32(len(blob)).boolean(eof).opaque(blob)
        return self._ok(call, w.bytes())

    def _nfs_proc_write(self, call: dict, x: _Xdr, peer) -> bytes:
        fh = self._fh_of(x)
        off = x.u64()
        _cnt = x.u32()
        stable = x.u32()
        blob = x.opaque()
        got = self._resolve(fh)
        if not got:
            return self._nfserr(call, NFS3ERR_STALE)
        rel, p = got
        if self.export.is_readonly(rel):
            return self._nfserr(call, NFS3ERR_ROFS, p)
        if p.is_symlink() or (p.exists() and p.is_dir()):
            return self._nfserr(call, NFS3ERR_ISDIR if p.is_dir() else NFS3ERR_ACCES, p)
        if len(blob) > self.write_max * 4 or off > (1 << 40):
            return self._nfserr(call, NFS3ERR_INVAL, p)
        pre_st = p.lstat()
        try:
            with open(p, "r+b" if p.exists() else "wb") as f:
                f.seek(off)
                f.write(blob)
                f.flush()
                if stable == 2:
                    os.fsync(f.fileno())
        except OSError:
            return self._nfserr(call, NFS3ERR_IO, p, pre_st)
        self.stats["writes"] += 1
        self._ev("NFS_WRITE", f"peer={peer} {rel or '/'} off={off} len={len(blob)} stable={stable}", rel)
        w = _XdrWriter()
        w.u32(NFS3_OK).fixed(self.export.wcc(p, pre_st)).u32(len(blob)).u32(2).fixed(_WRITE_VERF)
        return self._ok(call, w.bytes())

    def _nfs_proc_create_mkdir_symlink(self, call: dict, x: _Xdr, peer) -> bytes:
        proc = call["proc"]
        dfh = self._fh_of(x)
        name = x.opaque().decode("utf-8", "surrogateescape")
        dg = self._resolve(dfh)
        if not dg:
            return self._nfserr(call, NFS3ERR_STALE)
        drel, dp = dg
        if name in (".", "..") or "/" in name or "\x00" in name:
            return self._nfserr(call, NFS3ERR_ACCES, dp)
        child_rel = posixpath.join(drel, name) if drel else name
        if self.export.is_readonly(child_rel) or self.export.is_readonly(drel):
            return self._nfserr(call, NFS3ERR_ROFS, dp)
        cp = self.export.path(child_rel)
        if cp is None:
            return self._nfserr(call, NFS3ERR_ACCES, dp)
        pre_dp = dp.lstat()
        try:
            if proc == 9:
                sattr = self._parse_sattr3(x) if (x.o + 4 <= len(x.b)) else {}
                cp.mkdir(parents=False, exist_ok=False)
                if sattr:
                    self._apply_sattr3(cp, sattr)
            elif proc == 8:
                create_mode = x.u32() if (x.o + 4 <= len(x.b)) else 0
                sattr = {}
                if create_mode in (0, 1) and (x.o + 4 <= len(x.b)):
                    sattr = self._parse_sattr3(x)
                elif create_mode == 2 and (x.o + 8 <= len(x.b)):
                    x.skip(8)
                if create_mode == 1:
                    cp.touch(exist_ok=False)
                else:
                    if cp.exists() and cp.is_dir():
                        return self._nfserr(call, NFS3ERR_EXIST, dp, pre_dp)
                    with open(cp, "wb"):
                        pass
                if sattr:
                    self._apply_sattr3(cp, sattr)
            else:
                sattr = self._parse_sattr3(x) if (x.o + 24 <= len(x.b)) else {}
                link_target = ""
                with contextlib.suppress(Exception):
                    link_target = x.opaque().decode("utf-8", "replace")
                if not getattr(self.export, "allow_symlinks_within_share", False) or not link_target or ".." in Path(link_target).parts or link_target.startswith("/"):
                    return self._nfserr(call, NFS3ERR_ACCES, dp, pre_dp)
                os.symlink(link_target, cp)
        except FileExistsError:
            return self._nfserr(call, NFS3ERR_EXIST, dp, pre_dp)
        except OSError as exc:
            self._ev("NFS_CREATE", f"peer={peer} {child_rel} ERR {exc!r}", child_rel)
            return self._nfserr(call, NFS3ERR_IO, dp, pre_dp)
        self._ev("NFS_MKDIR" if proc == 9 else "NFS_CREATE", f"peer={peer} {child_rel}", child_rel)
        w = _XdrWriter()
        w.u32(NFS3_OK).u32(1).u32(64).fixed(self.export.fh(child_rel))
        w.fixed(self.export.post_op_attr(cp, rel=child_rel)).fixed(self.export.wcc(dp, pre_dp))
        return self._ok(call, w.bytes())

    def _nfs_proc_remove_rmdir(self, call: dict, x: _Xdr, peer) -> bytes:
        proc = call["proc"]
        dfh = self._fh_of(x)
        name = x.opaque().decode("utf-8", "surrogateescape")
        dg = self._resolve(dfh)
        if not dg:
            return self._nfserr(call, NFS3ERR_STALE)
        drel, dp = dg
        if name in (".", "..") or "/" in name or "\x00" in name:
            return self._nfserr(call, NFS3ERR_ACCES, dp)
        if drel == "" and name in self.export.shares:
            return self._nfserr(call, NFS3ERR_ACCES, dp)
        child_rel = posixpath.join(drel, name) if drel else name
        if self.export.is_readonly(child_rel) or self.export.is_readonly(drel):
            return self._nfserr(call, NFS3ERR_ROFS, dp)
        cp = self.export.path(child_rel)
        if cp is None or not cp.exists():
            return self._nfserr(call, NFS3ERR_NOENT, dp)
        pre_dp = dp.lstat()
        try:
            if proc == 13:
                cp.rmdir()
            else:
                cp.unlink()
        except OSError:
            return self._nfserr(call, NFS3ERR_NOTEMPTY if proc == 13 else NFS3ERR_IO, dp, pre_dp)
        self._ev("NFS_RMDIR" if proc == 13 else "NFS_REMOVE", f"peer={peer} {child_rel}", child_rel)
        return self._ok(call, struct.pack(">I", NFS3_OK) + self.export.wcc(dp, pre_dp))

    def _nfs_proc_rename(self, call: dict, x: _Xdr, peer) -> bytes:
        ffh = self._fh_of(x)
        fname = x.opaque().decode("utf-8", "surrogateescape")
        tfh = self._fh_of(x)
        tname = x.opaque().decode("utf-8", "surrogateescape")
        fg, tg = self._resolve(ffh), self._resolve(tfh)
        if not fg or not tg:
            return self._nfserr(call, NFS3ERR_STALE)
        if any(nm in (".", "..") or "/" in nm or "\x00" in nm for nm in (fname, tname)):
            return self._nfserr(call, NFS3ERR_ACCES, fg[1])
        src_rel = posixpath.join(fg[0], fname) if fg[0] else fname
        dst_rel = posixpath.join(tg[0], tname) if tg[0] else tname
        if self.export.is_readonly(src_rel) or self.export.is_readonly(dst_rel):
            return self._nfserr(call, NFS3ERR_ROFS, fg[1])
        src_res = self.export.resolve_rel(src_rel)
        dst_res = self.export.resolve_rel(dst_rel)
        if src_res is None or dst_res is None:
            return self._nfserr(call, NFS3ERR_ACCES, fg[1])
        if src_res[0] != dst_res[0]:
            return self._nfserr(call, NFS3ERR_XDEV, fg[1])
        src, dst = src_res[1], dst_res[1]
        if not src.exists():
            return self._nfserr(call, NFS3ERR_NOENT, fg[1])
        pre_fg, pre_tg = fg[1].lstat(), tg[1].lstat()
        try:
            os.replace(src, dst)
        except OSError:
            return self._nfserr(call, NFS3ERR_IO, fg[1], pre_fg)
        self._ev("NFS_RENAME", f"peer={peer} {src_rel} -> {dst_rel}", dst_rel)
        w = _XdrWriter()
        w.u32(NFS3_OK).fixed(self.export.wcc(fg[1], pre_fg)).fixed(self.export.wcc(tg[1], pre_tg))
        return self._ok(call, w.bytes())

    def _nfs_proc_readdir_entry(self, call: dict, x: _Xdr, peer) -> bytes:
        proc = call["proc"]
        fh = self._fh_of(x)
        cookie = x.u64()
        with contextlib.suppress(Exception):
            x.skip(8)
        if proc == 16:
            cnt = x.u32()
            return self._readdir(call, fh, cookie, cnt or 8192, plus=False, peer=peer)
        _dircount = x.u32()
        maxcount = x.u32()
        return self._readdir(call, fh, cookie, maxcount or 32768, plus=True, peer=peer)

    def _nfs_proc_fsstat(self, call: dict, x: _Xdr, _peer) -> bytes:
        got = self._resolve(self._fh_of(x))
        if not got:
            return self._nfserr(call, NFS3ERR_STALE)
        _rel, p = got
        try:
            st = os.statvfs(p)
            t = st.f_blocks * st.f_frsize
            f = st.f_bfree * st.f_frsize
            a = st.f_bavail * st.f_frsize
        except OSError:
            t = f = a = 0
        nfiles = 4096
        w = _XdrWriter()
        w.u32(NFS3_OK).fixed(self.export.post_op_attr(p, rel=_rel))
        for v in (t, f, a, nfiles, nfiles // 2, nfiles // 2):
            w.u64(v)
        return self._ok(call, w.bytes())

    def _nfs_proc_fsinfo(self, call: dict, x: _Xdr, _peer) -> bytes:
        got = self._resolve(self._fh_of(x))
        if not got:
            return self._nfserr(call, NFS3ERR_STALE)
        _rel, p = got
        w = _XdrWriter()
        w.u32(NFS3_OK).fixed(self.export.post_op_attr(p, rel=_rel))
        w.u32(self.read_max).u32(min(self.read_max, 32768)).u32(4096)
        w.u32(self.write_max).u32(min(self.write_max, 32768)).u32(4096)
        w.u32(32768).u64(1 << 40)
        w.fixed(_xtime(0.000001)).u32(0x01 | 0x02 | 0x04 | 0x08)
        return self._ok(call, w.bytes())

    def _nfs_proc_pathconf(self, call: dict, x: _Xdr, _peer) -> bytes:
        got = self._resolve(self._fh_of(x))
        if not got:
            return self._nfserr(call, NFS3ERR_STALE)
        _rel, p = got
        w = _XdrWriter()
        w.u32(NFS3_OK).fixed(self.export.post_op_attr(p, rel=_rel))
        w.u32(0).u32(255).boolean(True).boolean(False).boolean(False).boolean(True)
        return self._ok(call, w.bytes())

    def _nfs_proc_commit(self, call: dict, x: _Xdr, _peer) -> bytes:
        got = self._resolve(self._fh_of(x))
        if not got:
            return self._nfserr(call, NFS3ERR_STALE)
        _rel, p = got
        if self.export.is_readonly(_rel):
            return self._nfserr(call, NFS3ERR_ROFS, p)
        w = _XdrWriter()
        w.u32(NFS3_OK).fixed(self.export.wcc(p)).fixed(_WRITE_VERF)
        return self._ok(call, w.bytes())

    _NFS3_PROC_TABLE = {
        1: _nfs_proc_getattr,
        2: _nfs_proc_setattr,
        3: _nfs_proc_lookup,
        4: _nfs_proc_access,
        5: _nfs_proc_readlink,
        6: _nfs_proc_read,
        7: _nfs_proc_write,
        8: _nfs_proc_create_mkdir_symlink,
        9: _nfs_proc_create_mkdir_symlink,
        10: _nfs_proc_create_mkdir_symlink,
        12: _nfs_proc_remove_rmdir,
        13: _nfs_proc_remove_rmdir,
        14: _nfs_proc_rename,
        16: _nfs_proc_readdir_entry,
        17: _nfs_proc_readdir_entry,
        18: _nfs_proc_fsstat,
        19: _nfs_proc_fsinfo,
        20: _nfs_proc_pathconf,
        21: _nfs_proc_commit,
    }

    def _do_nfs(self, call: dict, ip: str, peer) -> bytes:
        proc = call["proc"]
        if proc == 0:
            return self._ok(call, b"")
        x = _Xdr(call["args"])
        handler = self._NFS3_PROC_TABLE.get(proc)
        try:
            if handler is not None:
                return handler(self, call, x, peer)
            if proc in (11, 15):
                self._unhandled(f"nfs proc={proc} (MKNOD/LINK not implemented)", call["args"])
                return self._nfserr(call, NFS3ERR_NOTSUPP)
        except (IndexError, ValueError, struct.error, OSError, PhoneFsError) as exc:
            self._ev("RPC_GARBAGE_ARGS", f"peer={peer} proc={proc} {exc!r}")
            return self._reply(call["xid"], call.get("verf", (0, b""))[1], RPC_GARBAGE_ARGS, b"")

        if 22 <= proc <= 29 or proc in (40014, 40015, 40016):
            self.stats["cnfs_calls"] = self.stats.get("cnfs_calls", 0) + 1
            return self._do_cnfs(call, proc, peer)
        self._unhandled(f"nfs proc={proc} (unknown procedure) peer={peer}", call["args"])
        return self._proc_unavail(call)

    def _readdir(self, call: dict, fh: bytes, cookie: int, budget: int,
                 plus: bool, peer) -> bytes:
        got = self._resolve(fh)
        if not got:
            return self._nfserr(call, NFS3ERR_STALE)
        drel, dp = got
        if not dp.is_dir():
            return self._nfserr(call, NFS3ERR_NOTDIR, dp)
        try:
            phys_entries = sorted(dp.iterdir(), key=lambda q: q.name.lower())
        except OSError:
            return self._nfserr(call, NFS3ERR_IO, dp)
        items: list[tuple[str, Path, str]] = []
        seen_names: set[str] = set()
        if drel == "":
            for alias, s_entry in sorted(self.export.shares.items(), key=lambda kv: kv[0].lower()):
                if s_entry.enabled and s_entry.host_path.is_dir():
                    items.append((alias, s_entry.host_path, alias))
                    seen_names.add(alias)
        for p in phys_entries:
            if p.name in seen_names:
                continue
            rel = posixpath.join(drel, p.name) if drel else p.name
            items.append((p.name, p, rel))
        w = _XdrWriter()
        w.u32(NFS3_OK).fixed(self.export.post_op_attr(dp, rel=drel))
        w.fixed(b"\x00" * 8)
        used = 0
        n = 0
        eof = True
        for idx, (entry_name, p, rel) in enumerate(items, start=1):
            if idx <= cookie:
                continue
            try:
                st = p.lstat()
            except OSError:
                continue
            piece = _XdrWriter()
            piece.u32(1)
            fid = self.export.hid_for_rel(rel) if hasattr(self.export, "hid_for_rel") else (st.st_ino & 0x7FFFFFFF)
            piece.u64(fid).string(entry_name).u64(idx)
            if plus:
                piece.fixed(self.export.post_op_attr(p, rel=rel))
                piece.u32(1).u32(64).fixed(self.export.fh(rel))
            b = piece.bytes()
            if used + len(b) > budget and n:
                eof = False
                break
            w.fixed(b)
            used += len(b)
            n += 1
            self._ev("NFS_LOOKUP", f"peer={peer} readdir {drel or '/'}/{entry_name}", rel)
        w.u32(0)
        w.boolean(eof)
        self.stats["readdirs"] += 1
        self._ev("NFS_READDIR",
                 f"peer={peer} path={drel or '/'} entries={n} eof={eof} "
                 f"cookie={cookie} budget={budget}", drel)
        return self._ok(call, w.bytes())
