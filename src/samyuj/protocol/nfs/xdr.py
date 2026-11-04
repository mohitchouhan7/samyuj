from __future__ import annotations

import re
import struct
from pathlib import Path
from typing import Any

from samyuj.core.errors import PhoneFsError

PROG_PORTMAP = 100000
PROG_MOUNT = 100005
PROG_NFS = 100003
PROG_NFS_LOCAL = 524282

_RPC_PROGNAME = {
    PROG_PORTMAP: "portmap",
    PROG_MOUNT: "mount",
    PROG_NFS: "nfs",
    PROG_NFS_LOCAL: "nfs-local",
}

_PORTMAP_PROC = {0: "NULL", 1: "SET", 2: "UNSET", 3: "GETPORT", 4: "DUMP", 5: "CALLIT"}
_MOUNT_PROC = {0: "NULL", 1: "MNT", 2: "DUMP", 3: "UMNT", 4: "UMNTALL", 5: "EXPORT"}
_NFS3_PROC = {
    0: "NULL", 1: "GETATTR", 2: "SETATTR", 3: "LOOKUP", 4: "ACCESS", 5: "READLINK",
    6: "READ", 7: "WRITE", 8: "CREATE", 9: "MKDIR", 10: "SYMLINK", 11: "MKNOD",
    12: "REMOVE", 13: "RMDIR", 14: "RENAME", 15: "LINK", 16: "READDIR",
    17: "READDIRPLUS", 18: "FSSTAT", 19: "FSINFO", 20: "PATHCONF", 21: "COMMIT",
    22: "READTHUMBNAIL", 23: "READCATEGORYDIR", 24: "GETATTRPATH", 25: "READPATH",
    26: "CREATEPATH", 27: "REMOVEBATCH", 28: "TRANSFERIMAGE", 29: "FILECHANGENOTIFY",
}

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
NFS3ERR_NOT_SYNC = 10002
NFS3ERR_REMOTE = 71
NFS3ERR_BADHANDLE = 10001
NFS3ERR_NOTSUPP = 10004
NFS3ERR_SERVERFAULT = 10006

MNT3_OK = 0
MNT3ERR_PERM = 1
MNT3ERR_NOENT = 2
MNT3ERR_ACCES = 13
MNT3ERR_NOTDIR = 20
MNT3ERR_NAMETOOLONG = 63

RPC_SUCCESS = 0
RPC_PROG_UNAVAIL = 1
RPC_PROG_MISMATCH = 2
RPC_PROC_UNAVAIL = 3
RPC_GARBAGE_ARGS = 4
RPC_SYSTEM_ERR = 5

NF3REG, NF3DIR, NF3BLK, NF3CHR, NF3LNK, NF3SOCK, NF3FIFO = 1, 2, 3, 4, 5, 6, 7

ACCESS3_READ = 0x01
ACCESS3_LOOKUP = 0x02
ACCESS3_MODIFY = 0x04
ACCESS3_EXTEND = 0x08
ACCESS3_DELETE = 0x10
ACCESS3_EXECUTE = 0x20

_NFS_ERR = {
    1: "operation not permitted",
    2: "no such file or directory",
    5: "I/O error",
    13: "permission denied (does Smart Connect have 'All files access'?)",
    20: "not a directory",
    21: "is a directory",
    22: "invalid argument",
    70: "stale file handle",
    10001: "bad file handle",
    10006: "server fault",
    10008: "server busy, try again",
}

def nfs_error_message(code: int) -> str:
    return f"{_NFS_ERR.get(code, 'NFS error')} (status {code})"

def xu32(n: int) -> bytes:
    return struct.pack(">I", int(n) & 0xFFFFFFFF)

def xu64(n: int) -> bytes:
    return struct.pack(">Q", int(n) & 0xFFFFFFFFFFFFFFFF)

def xopaque(b: bytes) -> bytes:
    raw = bytes(b)
    return xu32(len(raw)) + raw + b"\x00" * (-len(raw) % 4)

def xstr(s: str) -> bytes:
    b = (s or "").encode("utf-8", "surrogateescape")
    return struct.pack(">I", len(b)) + b + b"\x00" * ((4 - len(b) % 4) % 4)

def xb(v: bool) -> bytes:
    return struct.pack(">I", 1 if v else 0)

def xtime(ts: float) -> bytes:
    sec = int(ts)
    nsec = int(round((ts - sec) * 1e9))
    if nsec >= 1_000_000_000:
        sec += 1
        nsec -= 1_000_000_000
    return struct.pack(">II", sec & 0xFFFFFFFF, nsec & 0xFFFFFFFF)

class XdrReader:

    __slots__ = ("b", "o")

    def __init__(self, b: bytes, o: int = 0) -> None:
        self.b = b
        self.o = o

    def _need(self, n: int) -> None:
        if n < 0 or self.o + n > len(self.b):
            raise PhoneFsError("truncated reply from the phone")

    def u32(self) -> int:
        self._need(4)
        v = struct.unpack_from(">I", self.b, self.o)[0]
        self.o += 4
        return v

    def u64(self) -> int:
        self._need(8)
        v = struct.unpack_from(">Q", self.b, self.o)[0]
        self.o += 8
        return v

    def opaque(self) -> bytes:
        n = self.u32()
        self._need(n)
        v = self.b[self.o : self.o + n]
        self.o = min(self.o + ((n + 3) & ~3), len(self.b))
        return v

    def skip(self, n: int) -> None:
        self._need(n)
        self.o += n

class XdrWriter:

    __slots__ = ("b",)

    def __init__(self) -> None:
        self.b = bytearray()

    def u32(self, v: int) -> "XdrWriter":
        self.b += struct.pack(">I", int(v) & 0xFFFFFFFF)
        return self

    def u64(self, v: int) -> "XdrWriter":
        self.b += struct.pack(">Q", int(v) & 0xFFFFFFFFFFFFFFFF)
        return self

    def boolean(self, v: bool) -> "XdrWriter":
        return self.u32(1 if v else 0)

    def string(self, s: str) -> "XdrWriter":
        self.b += xstr(s)
        return self

    def opaque(self, o: bytes) -> "XdrWriter":
        self.b += xopaque(o)
        return self

    def fixed(self, o: bytes) -> "XdrWriter":
        self.b += bytes(o)
        return self

    def bytes(self) -> bytes:
        return bytes(self.b)

def parse_fattr3(x: XdrReader) -> dict[str, Any]:
    ftype = x.u32()
    mode = x.u32()
    x.u32()
    x.u32()
    x.u32()
    size = x.u64()
    x.u64()
    x.u32()
    x.u32()
    x.u64()
    fileid = x.u64()
    x.u32()
    x.u32()
    mtime = x.u32()
    x.u32()
    x.u32()
    x.u32()
    return {"ftype": ftype, "mode": mode, "size": size, "mtime": mtime, "fileid": fileid}

def post_op_attr(x: XdrReader) -> dict[str, Any] | None:
    return parse_fattr3(x) if x.u32() else None

def ftype_kind(ftype: int) -> str:
    return "file" if ftype == 1 else ("dir" if ftype == 2 else "other")

def norm_phone_path(p: str) -> str:
    if "\x00" in str(p):
        raise PhoneFsError("invalid path")
    segs: list[str] = []
    for s in str(p).replace("\\", "/").split("/"):
        if s in ("", "."):
            continue
        if s == "..":
            raise PhoneFsError("'..' is not allowed in phone paths")
        segs.append(s)
    if len(segs) >= 3 and segs[0] == "storage" and segs[1] == "emulated" and segs[2] == "0":
        segs = segs[3:]
    elif len(segs) >= 1 and segs[0] == "sdcard":
        segs = segs[1:]
    return "/" + "/".join(segs)

def dissect_rpc(data: bytes) -> list[dict[str, int]]:
    calls: list[dict[str, int]] = []
    i = 0
    n = len(data)
    while n - i >= 4:
        hdr = int.from_bytes(data[i : i + 4], "big")
        frag_len = hdr & 0x7FFFFFFF
        last = bool(hdr & 0x80000000)
        if frag_len == 0 or frag_len > 4 * 1024 * 1024 or i + 4 + frag_len > n:
            i += 1
            continue
        body = data[i + 4 : i + 4 + frag_len]
        end = i + 4 + frag_len
        if len(body) >= 24:
            xid = int.from_bytes(body[0:4], "big")
            mtype = int.from_bytes(body[4:8], "big")
            rpcvers = int.from_bytes(body[8:12], "big")
            prog = int.from_bytes(body[12:16], "big")
            ver = int.from_bytes(body[16:20], "big")
            proc = int.from_bytes(body[20:24], "big")
            if mtype == 0 and rpcvers in (0, 2) and prog in _RPC_PROGNAME:
                calls.append(
                    {
                        "xid": xid,
                        "rpcvers": rpcvers,
                        "prog": prog,
                        "ver": ver,
                        "proc": proc,
                        "len": frag_len,
                    }
                )
                i = end
                continue
            if last:
                i = end
                continue
        i += 1
    return calls

def rpc_proc_name(prog: int, proc: int) -> str:
    if prog == PROG_PORTMAP:
        return _PORTMAP_PROC.get(proc, f"pmnt{proc}")
    if prog == PROG_MOUNT:
        return _MOUNT_PROC.get(proc, f"mnt{proc}")
    if prog in (PROG_NFS, PROG_NFS_LOCAL):
        return _NFS3_PROC.get(proc, f"nfs{proc}")
    return f"proc{proc}"

def strings_in(data: bytes, min_len: int = 3) -> list[str]:
    out: list[str] = []
    cur = bytearray()
    for b in data:
        if 32 <= b < 127:
            cur.append(b)
        else:
            if len(cur) >= min_len:
                out.append(cur.decode("ascii"))
            cur = bytearray()
    if len(cur) >= min_len:
        out.append(cur.decode("ascii"))
    return out

_CNFS_INCR_RE = re.compile(r"(.*?)(\(\d+\))(\..*)?$")

def cnfs_add_one_to_file_name(name: str) -> str:
    dot = name.find(".")
    if dot == -1:
        return name + "(1)"
    return name[:dot] + "(1)" + name[dot:]

def cnfs_increment_file_name(name: str) -> str:
    m = _CNFS_INCR_RE.match(name)
    if not m:
        return cnfs_add_one_to_file_name(name)
    head = m.group(1) or ""
    if head.endswith("."):
        head = head[:-1]
    num_txt = re.sub(r"[()]", "", m.group(2) or "")
    ext = m.group(3) or ""
    try:
        nxt = int(num_txt) + 1
    except ValueError:
        nxt = 1
    return f"{head}({nxt}){ext}"

def cnfs_create_file_name(target: Path) -> str:
    name = target.name
    if not target.exists():
        return name
    parent = target.parent
    try:
        existing = {e.name for e in parent.iterdir()}
    except OSError:
        existing = {name}
    if name in existing:
        name = cnfs_add_one_to_file_name(name)
    for _ in range(10000):
        if name not in existing:
            return name
        name = cnfs_increment_file_name(name)
    return name
