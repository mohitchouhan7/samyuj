from __future__ import annotations
from dataclasses import dataclass

import collections
import contextlib
import dataclasses
import errno
import hashlib
import hmac
import os
import stat
import struct
import threading
from pathlib import Path

NFS3_OK = 0
NFS3ERR_PERM = 1
NFS3ERR_NOENT = 2
NFS3ERR_IO = 5
NFS3ERR_NXIO = 6
NFS3ERR_ACCES = 13
NFS3ERR_EXIST = 17
NFS3ERR_XDEV = 18
NFS3ERR_NODEV = 19
NFS3ERR_NOTDIR = 20
NFS3ERR_ISDIR = 21
NFS3ERR_INVAL = 22
NFS3ERR_FBIG = 27
NFS3ERR_NOSPC = 28
NFS3ERR_ROFS = 30
NFS3ERR_MLINK = 31
NFS3ERR_NAMETOOLONG = 63
NFS3ERR_NOTEMPTY = 66
NFS3ERR_DQUOT = 69
NFS3ERR_STALE = 70
NFS3ERR_BADHANDLE = 10001
NFS3ERR_NOT_SYNC = 10002
NFS3ERR_BAD_COOKIE = 10003
NFS3ERR_NOTSUPP = 10004
NFS3ERR_TOOSMALL = 10005
NFS3ERR_SERVERFAULT = 10006

NF3REG = 1
NF3DIR = 2
NF3BLK = 3
NF3CHR = 4
NF3LNK = 5
NF3SOCK = 6
NF3FIFO = 7

ACCESS3_READ = 0x0001
ACCESS3_LOOKUP = 0x0002
ACCESS3_MODIFY = 0x0004
ACCESS3_EXTEND = 0x0008
ACCESS3_DELETE = 0x0010
ACCESS3_EXECUTE = 0x0020

_DEFAULT_FSID = 0x4D434C00

_SENSITIVE_SYSTEM_PREFIXES = (
    "/etc",
    "/proc",
    "/sys",
    "/dev",
    "/boot",
    "/root",
    "/run",
    "/var/run",
    "/usr",
    "/bin",
    "/sbin",
    "/lib",
    "/lib64",
)

_SENSITIVE_HOME_REL_DIRS = (
    ".ssh",
    ".gnupg",
    ".aws",
    ".kube",
    ".config/gcloud",
    ".local/share/keyrings",
    ".password-store",
)

def validate_share_host_path(host_path: str | Path) -> Path:
    hp = Path(host_path).expanduser().resolve()
    hp_str = str(hp)
    if hp_str == "/":
        raise ValueError("Refusing to export filesystem root '/' over NFS")
    for sys_prefix in _SENSITIVE_SYSTEM_PREFIXES:
        if hp_str == sys_prefix or hp_str.startswith(sys_prefix + "/"):
            raise ValueError(f"Refusing to export sensitive system path: {hp_str}")
    with contextlib.suppress(Exception):
        home = Path.home().resolve()
        if hp == home:
            raise ValueError("Refusing to export entire home directory; select a specific subfolder")
        for rel_sec in _SENSITIVE_HOME_REL_DIRS:
            sec_p = (home / rel_sec).resolve()
            if hp == sec_p or hp.is_relative_to(sec_p):
                raise ValueError(f"Refusing to export sensitive credential directory: {hp_str}")
    return hp

def _xtime(ts: float) -> bytes:
    sec = max(0, int(ts))
    nsec = max(0, min(999_999_999, int((ts - sec) * 1_000_000_000)))
    return struct.pack(">II", sec, nsec)

def errno_to_nfs3(err: int) -> int:
    return {
        errno.EPERM: NFS3ERR_PERM,
        errno.ENOENT: NFS3ERR_NOENT,
        errno.EIO: NFS3ERR_IO,
        errno.EACCES: NFS3ERR_ACCES,
        errno.EEXIST: NFS3ERR_EXIST,
        errno.EXDEV: NFS3ERR_XDEV,
        errno.ENOTDIR: NFS3ERR_NOTDIR,
        errno.EISDIR: NFS3ERR_ISDIR,
        errno.EINVAL: NFS3ERR_INVAL,
        errno.EFBIG: NFS3ERR_FBIG,
        errno.ENOSPC: NFS3ERR_NOSPC,
        errno.EROFS: NFS3ERR_ROFS,
        errno.ENAMETOOLONG: NFS3ERR_NAMETOOLONG,
        errno.ENOTEMPTY: NFS3ERR_NOTEMPTY,
        errno.ESTALE: NFS3ERR_STALE,
    }.get(err, NFS3ERR_IO)

@dataclass
class ShareEntry:
    share_id: str
    alias: str
    host_path: Path
    access_mode: str = "rw"
    enabled: bool = True
    confirmed_rw: bool = True
    description: str = ""
    generation: int = 1

    @property
    def readonly(self) -> bool:
        return self.access_mode != "rw" or not self.confirmed_rw

    def is_available(self) -> bool:
        return self.enabled and self.host_path.is_dir()

    def to_dict(self) -> dict:
        return {
            "share_id": self.share_id,
            "alias": self.alias,
            "remote_path": f"/{self.alias}",
            "host_path": str(self.host_path),
            "access_mode": "ro" if self.readonly else "rw",
            "enabled": self.enabled,
            "confirmed_rw": self.confirmed_rw,
            "available": self.is_available(),
            "description": self.description,
            "generation": self.generation,
        }

class MultiShareExport:

    DEFAULT_MAX_HANDLES = 65536

    def __init__(
        self,
        root: Path,
        readonly: bool = False,
        secret: bytes | None = None,
        allow_symlinks_within_share: bool = False,
        max_handles: int = DEFAULT_MAX_HANDLES,
    ) -> None:
        self._lock = threading.RLock()
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.readonly = bool(readonly)
        self.secret = secret or os.urandom(32)
        self.allow_symlinks_within_share = bool(allow_symlinks_within_share)
        self.max_handles = max(64, int(max_handles))
        self.generation = 1

        self.shares: dict[str, ShareEntry] = {}
        self._fh_to_loc: collections.OrderedDict[bytes, tuple[str, int, str]] = collections.OrderedDict()
        self._loc_to_fh: collections.OrderedDict[tuple[str, int, str], bytes] = collections.OrderedDict()
        self._loc_to_hid: dict[tuple[str, int, str], int] = {}
        self._next_hid: int = 1

        self.mounts: list[tuple[str, str, float]] = []
        self.root_fh = self.fh_for_loc("__ROOT__", "")
        self._fh_to_loc[b"\x00" * 64] = ("__ROOT__", 1, "")

    def set_export(self, root: Path, readonly: bool | None = None) -> None:
        with self._lock:
            self.root = Path(root).expanduser().resolve()
            self.root.mkdir(parents=True, exist_ok=True)
            if readonly is not None:
                self.readonly = bool(readonly)
            self.generation += 1

    @staticmethod
    def _clean_alias(alias: str) -> str:
        clean = (alias or "").strip().strip("/").replace("\\", "_")
        if not clean or clean in (".", "..") or "/" in clean:
            raise ValueError(f"Invalid share alias: {alias!r}")
        return clean

    def add_share(
        self,
        alias: str,
        host_path: str | Path,
        access_mode: str = "rw",
        description: str = "",
        confirmed_rw: bool = True,
        create_if_missing: bool = False,
        share_id: str | None = None,
        enabled: bool = True,
    ) -> ShareEntry:
        clean_alias = self._clean_alias(alias)
        hp = Path(host_path).expanduser()
        if create_if_missing and not hp.exists():
            hp.mkdir(parents=True, exist_ok=True)
        if not hp.exists() or not hp.is_dir():
            raise ValueError(f"Host directory does not exist: {hp}")
        real_hp = validate_share_host_path(hp)
        mode = "rw" if str(access_mode).lower() in ("rw", "read-write", "write") else "ro"
        with self._lock:
            self.generation += 1
            prev = self.shares.get(clean_alias)
            sid = share_id or (prev.share_id if prev else f"share_{clean_alias.lower()}")
            gen = (prev.generation + 1) if prev else 1
            entry = ShareEntry(
                share_id=sid,
                alias=clean_alias,
                host_path=real_hp,
                access_mode=mode,
                enabled=bool(enabled),
                confirmed_rw=bool(confirmed_rw) if mode == "rw" else False,
                description=description,
                generation=gen,
            )
            self.shares[clean_alias] = entry
            return entry

    def remove_share(self, alias_or_id: str) -> bool:
        with self._lock:
            target_alias = None
            if alias_or_id in self.shares:
                target_alias = alias_or_id
            else:
                for al, sh in self.shares.items():
                    if sh.share_id == alias_or_id:
                        target_alias = al
                        break
            if target_alias is None:
                return False
            removed = self.shares.pop(target_alias)
            self.generation += 1
            stale_fhs = [
                fh for fh, (sid, _g, _r) in self._fh_to_loc.items()
                if sid == removed.share_id
            ]
            for fh in stale_fhs:
                loc = self._fh_to_loc.pop(fh, None)
                if loc:
                    self._loc_to_fh.pop(loc, None)
                    self._loc_to_hid.pop(loc, None)
            return True

    def set_share_access_mode(
        self, alias_or_id: str, access_mode: str, confirmed_rw: bool = True
    ) -> ShareEntry | None:
        mode = "rw" if str(access_mode).lower() in ("rw", "read-write", "write") else "ro"
        with self._lock:
            for sh in self.shares.values():
                if sh.alias == alias_or_id or sh.share_id == alias_or_id:
                    sh.access_mode = mode
                    sh.confirmed_rw = bool(confirmed_rw) if mode == "rw" else False
                    self.generation += 1
                    return sh
        return None

    def list_shares(self) -> list[dict]:
        with self._lock:
            return [sh.to_dict() for sh in self.shares.values()]

    def _evict_handles_if_needed(self) -> None:
        while len(self._loc_to_fh) > self.max_handles:
            evicted = False
            for loc_key, fh_val in list(self._loc_to_fh.items()):
                sid, _gen, rel_p = loc_key
                if sid == "__ROOT__" and rel_p == "":
                    self._loc_to_fh.move_to_end(loc_key)
                    continue
                if rel_p == "":
                    self._loc_to_fh.move_to_end(loc_key)
                    continue
                self._loc_to_fh.pop(loc_key, None)
                self._fh_to_loc.pop(fh_val, None)
                self._loc_to_hid.pop(loc_key, None)
                evicted = True
                break
            if not evicted:
                break

    def fh_for_loc(self, share_id: str, rel_path: str) -> bytes:
        norm_rel = (rel_path or "").strip("/")
        with self._lock:
            gen = 1
            if share_id != "__ROOT__":
                sh = next((s for s in self.shares.values() if s.share_id == share_id), None)
                if sh is not None:
                    gen = sh.generation
            key = (share_id, gen, norm_rel)
            got = self._loc_to_fh.get(key)
            if got is not None:
                self._loc_to_fh.move_to_end(key)
                if got in self._fh_to_loc:
                    self._fh_to_loc.move_to_end(got)
                return got
            if share_id == "__ROOT__" and norm_rel == "":
                hid = 0
            else:
                hid = self._next_hid
                self._next_hid = (self._next_hid + 1) & 0x7FFFFFFF or 1
            self._loc_to_hid[key] = hid
            msg = f"{share_id}:{gen}:{norm_rel}".encode("utf-8", "surrogateescape")
            mac1 = hmac.new(self.secret, b"FH1:" + msg, hashlib.sha256).digest()
            mac2 = hmac.new(self.secret, b"FH2:" + msg, hashlib.sha256).digest()
            fh = struct.pack("<I", hid) + (mac1 + mac2)[:60]
            self._loc_to_fh[key] = fh
            self._fh_to_loc[fh] = key
            self._evict_handles_if_needed()
            return fh

    def hid_for_rel(self, rel: str = "") -> int:
        fh = self.fh(rel)
        return int.from_bytes(fh[:4], "little")

    def fh(self, rel: str = "") -> bytes:
        norm = (rel or "").strip("/")
        if not norm:
            return self.root_fh
        first, _, rest = norm.partition("/")
        with self._lock:
            sh = self.shares.get(first)
            if sh is not None:
                return self.fh_for_loc(sh.share_id, rest)
            return self.fh_for_loc("__ROOT__", norm)

    def rel_of(self, fh: bytes) -> str | None:
        resolved = self.resolve_fh(fh)
        if resolved is None:
            return None
        share, rel_in_share, _path, virt_rel = resolved
        return virt_rel

    def resolve_fh(self, fh: bytes) -> tuple[ShareEntry | None, str, Path, str] | None:
        with self._lock:
            loc = self._fh_to_loc.get(fh)
            if loc is None:
                return None
            if fh != b"\x00" * 64:
                self._fh_to_loc.move_to_end(fh)
                if loc in self._loc_to_fh:
                    self._loc_to_fh.move_to_end(loc)
            share_id, gen, rel_in_share = loc
            if share_id == "__ROOT__":
                if not rel_in_share:
                    if not self.root.is_dir():
                        return None
                    return None, "", self.root, ""
                p = self._safe_resolve_under(self.root, rel_in_share)
                if p is None or not p.exists():
                    return None
                return None, rel_in_share, p, rel_in_share

            sh = next((s for s in self.shares.values() if s.share_id == share_id), None)
            if sh is None or not sh.enabled or sh.generation != gen or not sh.host_path.is_dir():
                return None
            if not rel_in_share:
                return sh, "", sh.host_path, sh.alias
            p = self._safe_resolve_under(sh.host_path, rel_in_share)
            if p is None or not p.exists():
                return None
            virt_rel = f"{sh.alias}/{rel_in_share}"
            return sh, rel_in_share, p, virt_rel

    def _safe_resolve_under(self, base_root: Path, rel_subpath: str) -> Path | None:
        try:
            canon_root = base_root.resolve()
        except OSError:
            return None
        norm = (rel_subpath or "").replace("\\", "/").strip("/")
        if not norm:
            return canon_root
        parts = [pt for pt in norm.split("/") if pt and pt != "."]
        if any(pt == ".." or "\x00" in pt for pt in parts):
            return None

        cur = canon_root
        for part in parts:
            cand = cur / part
            with contextlib.suppress(OSError):
                if cand.is_symlink():
                    if not self.allow_symlinks_within_share:
                        return None
                    target = cand.resolve()
                    if not target.is_relative_to(canon_root):
                        return None
            cur = cand

        try:
            parent_real = cur.parent.resolve()
            if not parent_real.is_relative_to(canon_root):
                return None
            if cur.exists():
                cur_real = cur.resolve()
                if not cur_real.is_relative_to(canon_root):
                    return None
        except OSError:
            return None
        return cur

    def path(self, virt_rel: str) -> Path | None:
        norm = (virt_rel or "").replace("\\", "/").strip("/")
        if not norm:
            return self.root
        parts = [pt for pt in norm.split("/") if pt and pt != "."]
        if any(pt == ".." or "\x00" in pt for pt in parts):
            return None
        first = parts[0]
        rest = "/".join(parts[1:])
        with self._lock:
            sh = self.shares.get(first)
            if sh is not None:
                if not sh.is_available():
                    return None
                return self._safe_resolve_under(sh.host_path, rest)
            return self._safe_resolve_under(self.root, norm)

    def is_readonly_for(self, share: ShareEntry | None, virt_rel: str = "") -> bool:
        if share is not None:
            return share.readonly
        norm = (virt_rel or "").strip("/")
        first = norm.split("/", 1)[0] if norm else ""
        with self._lock:
            if first in self.shares:
                return self.shares[first].readonly
            return self.readonly

    @staticmethod
    def pack_fattr3_stat(
        st: os.stat_result,
        fsid: int = _DEFAULT_FSID,
        fileid: int | None = None,
    ) -> bytes:
        mode = st.st_mode
        if stat.S_ISDIR(mode):
            ftype = NF3DIR
        elif stat.S_ISLNK(mode):
            ftype = NF3LNK
        else:
            ftype = NF3REG
        perm = mode & 0o7777
        size = int(st.st_size)
        used = ((size + 4095) // 4096) * 4096
        fid = int(fileid if fileid is not None else (st.st_ino & 0x7FFFFFFF)) & 0xFFFFFFFFFFFFFFFF
        return (
            struct.pack(
                ">IIIIIQQIIQQ",
                ftype,
                perm,
                max(1, int(st.st_nlink)),
                int(st.st_uid) & 0xFFFFFFFF,
                int(st.st_gid) & 0xFFFFFFFF,
                size,
                used,
                0,
                0,
                int(fsid) & 0xFFFFFFFFFFFFFFFF,
                fid,
            )
            + _xtime(st.st_atime)
            + _xtime(st.st_mtime)
            + _xtime(st.st_ctime)
        )

    @property
    def revision(self) -> int:
        return self.generation

    @revision.setter
    def revision(self, val: int) -> None:
        self.generation = int(val)

    def rel(self, fh: bytes) -> str | None:
        return self.rel_of(fh)

    def is_readonly(self, virt_rel: str = "") -> bool:
        return self.is_readonly_for(None, virt_rel)

    def resolve_rel(self, virt_rel: str) -> tuple[str, Path] | None:
        norm = (virt_rel or "").replace("\\", "/").strip("/")
        if not norm:
            return ("__ROOT__", self.root)
        parts = [pt for pt in norm.split("/") if pt and pt != "."]
        if any(pt == ".." or "\x00" in pt for pt in parts):
            return None
        first = parts[0]
        rest = "/".join(parts[1:])
        with self._lock:
            sh = self.shares.get(first)
            if sh is not None:
                if not sh.is_available():
                    return None
                rp = self._safe_resolve_under(sh.host_path, rest)
                return (sh.share_id, rp) if rp is not None else None
            rp = self._safe_resolve_under(self.root, norm)
            return ("__ROOT__", rp) if rp is not None else None

    def fattr3(self, p: Path, st=None, rel: str = "", fsid_override: int | None = None) -> bytes:
        st_obj = st or p.lstat()
        fid = self.hid_for_rel(rel) if rel is not None else (st_obj.st_ino & 0x7FFFFFFF)
        return self.pack_fattr3_stat(st_obj, fsid_override or _DEFAULT_FSID, fileid=fid)

    def post_op_attr(self, p: Path | None, rel: str = "") -> bytes:
        if p is None:
            return struct.pack(">I", 0)
        try:
            st = p.lstat()
            fid = self.hid_for_rel(rel) if rel is not None else (st.st_ino & 0x7FFFFFFF)
            return struct.pack(">I", 1) + self.pack_fattr3_stat(st, fileid=fid)
        except OSError:
            return struct.pack(">I", 0)

    @staticmethod
    def pre_op_attr_from_stat(st: os.stat_result | None) -> bytes:
        if st is None:
            return struct.pack(">I", 0)
        return (
            struct.pack(">IQ", 1, int(st.st_size))
            + _xtime(st.st_mtime)
            + _xtime(st.st_ctime)
        )

    def wcc(self, p: Path | None, pre_stat: os.stat_result | None = None) -> bytes:
        return self.pre_op_attr_from_stat(pre_stat) + self.post_op_attr(p)

    @staticmethod
    def no_wcc() -> bytes:
        return struct.pack(">II", 0, 0)

class Export(MultiShareExport):
    pass
