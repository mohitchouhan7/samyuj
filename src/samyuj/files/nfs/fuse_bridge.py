from __future__ import annotations

import asyncio
import contextlib
import errno
import logging
import os
import posixpath
import shutil
import stat as stat_mod
import subprocess
import threading
import time

for _ln in ("fuse", "fuse.log-mixin"):
    logging.getLogger(_ln).setLevel(logging.WARNING)

_FUSE_IMPORT_ERROR: str = ""
try:
    from fuse import FUSE, FuseOSError, Operations, fuse_get_context
except Exception as _exc:
    FUSE = None

    class FuseOSError(OSError):

        def __init__(self, err_no: int) -> None:
            super().__init__(err_no, os.strerror(err_no))

    Operations = object
    fuse_get_context = None
    _FUSE_IMPORT_ERROR = repr(_exc)

_ATTR_TTL_S = 3.0
_NEG_TTL_S = 2.0
_WRITE_FLUSH_BYTES = 8 * 1024 * 1024
_ATTEMPTS = 2
_TRANSIENT_MARKERS = (
    "NXIO", "LOST", "STALE", "BUSY", "10008", "NOTCONN", "NOT CONNECTED",
    "TIMED OUT", "TIMEOUT", "TEMPORARILY", "WOULDBLOCK", "EOF",
    "XID MISMATCH", "CLOSED",
)
_INDEXER_SENTINELS = frozenset({
    ".nomedia",
    ".trackerignore",
    ".noindex",
    ".metadata_never_index",
})
_FAST_ENOENT_NAMES = frozenset({
    ".Trash",
    ".Trash-0",
    f".Trash-{os.getuid() if hasattr(os, 'getuid') else 1000}",
    ".xdg-volume-info",
    "autorun.inf",
    ".DS_Store",
    "Thumbs.db",
})
_INDEXER_COMM_PREFIXES = (
    "baloo_file",
    "baloorunner",
    "tracker-miner",
    "tracker-extract",
    "localsearch",
    "updatedb",
    "plocate",
    "recollindex",
    "akonadi_index",
)

def fuse_available() -> bool:
    return FUSE is not None

def fuse_missing_hint() -> str:
    if FUSE is not None:
        return ""
    return (f"FUSE bridge unavailable ({_FUSE_IMPORT_ERROR or 'fusepy not importable'}). "
            "Install once:  pip install fusepy   plus libfuse2 "
            "(Debian/Ubuntu: sudo apt install fuse libfuse2 · Fedora: sudo dnf install "
            "fuse-libs python3-pip && pip install fusepy). Then re-select the FUSE method.")

def _map_errno(exc: BaseException | None) -> int:
    if exc is None:
        return errno.EIO
    if isinstance(exc, FuseOSError):
        return int(exc.errno or errno.EIO)
    if isinstance(exc, TimeoutError):
        return errno.EAGAIN
    if isinstance(exc, ConnectionError):
        return errno.ENOTCONN
    if isinstance(exc, asyncio.CancelledError):
        return errno.ENOTCONN
    msg = f"{type(exc).__name__}: {exc}".upper()
    if "NOENT" in msg or "NO SUCH FILE" in msg or "STATUS 2)" in msg:
        return errno.ENOENT
    if "ROFS" in msg or "READ-ONLY" in msg or "STATUS 30)" in msg:
        return errno.EROFS
    if "ACCES" in msg or "PERMISSION DENIED" in msg or "STATUS 13)" in msg:
        return errno.EACCES
    if "NOT PERMITTED" in msg or "PERM" in msg or "STATUS 1)" in msg:
        return errno.EACCES
    if "ISDIR" in msg or "IS A DIRECTORY" in msg or "STATUS 21)" in msg:
        return errno.EISDIR
    if "NOTDIR" in msg or "NOT A DIRECTORY" in msg or "STATUS 20)" in msg:
        return errno.ENOTDIR
    if "NOTEMPTY" in msg or "NOT EMPTY" in msg or "STATUS 66)" in msg:
        return errno.ENOTEMPTY
    if "EXIST" in msg or "STATUS 17)" in msg:
        return errno.EEXIST
    if "NOSPC" in msg or "NO SPACE" in msg or "STATUS 28)" in msg:
        return errno.ENOSPC
    if "FBIG" in msg or "STATUS 27)" in msg:
        return errno.EFBIG
    if "XDEV" in msg or "STATUS 18)" in msg:
        return errno.EXDEV
    if "NAMETOOLONG" in msg or "STATUS 63)" in msg:
        return errno.ENAMETOOLONG
    if "NOTSUPP" in msg or "STATUS 10004)" in msg:
        return getattr(errno, "ENOTSUP", errno.EOPNOTSUPP)
    if "INVAL" in msg or "INVALID ARGUMENT" in msg or "STATUS 22)" in msg:
        return errno.EINVAL
    if "STALE" in msg or "BAD FILE HANDLE" in msg or "STATUS 70)" in msg or "STATUS 10001)" in msg:
        return errno.ESTALE
    if "BUSY" in msg or "10008" in msg:
        return errno.EBUSY
    if ("NXIO" in msg or "LOST" in msg or "NOTCONN" in msg
            or "NOT CONNECTED" in msg or "EVENT LOOP IS CLOSED" in msg
            or "CANCELLED" in msg):
        return errno.ENOTCONN
    return errno.EIO

def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    msg = f"{type(exc).__name__}: {exc}".upper()
    if "NOENT" in msg or "NO SUCH FILE" in msg or "STATUS 2)" in msg:
        return False
    return any(m in msg for m in _TRANSIENT_MARKERS)

class _PhoneFuseOps(Operations):

    def __init__(self, client_getter, loop: asyncio.AbstractEventLoop,
                 timeout: float = 20.0, readonly: bool = False) -> None:
        self.client_getter = client_getter
        self.loop = loop
        self.timeout = timeout
        self.readonly = bool(readonly)
        self._cache_lock = threading.RLock()
        self._attr_cache: dict[str, tuple[float, dict]] = {}
        self._neg_cache: dict[str, float] = {}
        self._dir_children: dict[str, tuple[float, set[str]]] = {}
        self._wbuf: dict[str, dict] = {}
        self._pid_comm_cache: dict[int, tuple[float, bool]] = {}

    def _is_indexer_caller(self) -> bool:
        if fuse_get_context is None:
            return False
        try:
            _uid, _gid, pid = fuse_get_context()
            pid = int(pid or 0)
            if pid <= 0:
                return False
            now = time.monotonic()
            cached = self._pid_comm_cache.get(pid)
            if cached is not None and (now - cached[0]) < 30.0:
                return cached[1]
            with open(f"/proc/{pid}/comm", "r", encoding="utf-8", errors="replace") as fh:
                comm = fh.read().strip().lower()
            is_idx = any(comm.startswith(pfx) for pfx in _INDEXER_COMM_PREFIXES)
            if len(self._pid_comm_cache) > 512:
                self._pid_comm_cache.clear()
            self._pid_comm_cache[pid] = (now, is_idx)
            return is_idx
        except Exception:
            return False

    def _client(self):
        cl = self.client_getter() if callable(self.client_getter) else self.client_getter
        if cl is None:
            raise FuseOSError(errno.ENOTCONN)
        return cl

    def _attempt(self, coro_factory, what: str = ""):
        if self.loop is None or self.loop.is_closed():
            raise FuseOSError(errno.ENOTCONN)
        last: BaseException | None = None
        for i in range(_ATTEMPTS):
            fut = None
            try:
                fut = asyncio.run_coroutine_threadsafe(coro_factory(), self.loop)
                return fut.result(self.timeout)
            except (FuseOSError, KeyboardInterrupt, SystemExit):
                raise
            except BaseException as exc:
                if fut is not None and not fut.done():
                    with contextlib.suppress(Exception):
                        fut.cancel()
                last = exc
                if not _is_transient(exc) or i == _ATTEMPTS - 1:
                    break
                time.sleep(0.2 * (i + 1))
        raise FuseOSError(_map_errno(last))

    def _invalidate(self, *paths: str) -> None:
        with self._cache_lock:
            for p in paths:
                if not p:
                    continue
                self._attr_cache.pop(p, None)
                self._neg_cache.pop(p, None)
                self._dir_children.pop(p, None)
                parent = posixpath.dirname(p.rstrip("/")) or "/"
                self._dir_children.pop(parent, None)

    def _cached_stat(self, path: str) -> dict | None:
        with self._cache_lock:
            hit = self._attr_cache.get(path)
            if hit is not None and (time.monotonic() - hit[0]) < _ATTR_TTL_S:
                return hit[1]
            return None

    def _mkst(self, kind: str, size: int, mtime) -> dict:
        is_dir = (kind == "dir")
        mode = (stat_mod.S_IFDIR | 0o755) if is_dir else (stat_mod.S_IFREG | 0o644)
        t = float(mtime or 0)
        return {
            "st_mode": mode,
            "st_nlink": 2 if is_dir else 1,
            "st_size": int(size or 0),
            "st_atime": t, "st_mtime": t, "st_ctime": t,
            "st_uid": os.getuid(), "st_gid": os.getgid(),
            "st_blocks": max(1, int(size or 0) // 512 + 1),
            "st_blksize": 131072,
        }

    def _buf_flush(self, path: str) -> None:
        with self._cache_lock:
            ent = self._wbuf.get(path)
            if not ent or not ent["data"]:
                return
            data = bytes(ent["data"])
            start = int(ent["start"])
            ent["data"] = bytearray()
            ent["start"] = start + len(data)
        self._attempt(lambda: self._client().write_range(path, start, data),
                      f"write {path}")
        self._invalidate(path)

    def getattr(self, path, fh=None):
        path = path or "/"
        if path == "/":
            return self._mkst("dir", 4096, time.time())
        base_name = posixpath.basename(path.rstrip("/"))
        if base_name in _INDEXER_SENTINELS:
            st_sen = self._mkst("file", 0, 0.0)
            st_sen["st_mode"] = stat_mod.S_IFREG | 0o444
            return st_sen
        if self._is_indexer_caller():
            raise FuseOSError(errno.ENOENT)
        cached = self._cached_stat(path)
        if cached is not None:
            return cached
        now = time.monotonic()
        with self._cache_lock:
            neg = self._neg_cache.get(path)
            if neg is not None and (now - neg) < _NEG_TTL_S:
                raise FuseOSError(errno.ENOENT)
            if base_name in _FAST_ENOENT_NAMES and path not in self._wbuf:
                self._neg_cache[path] = now
                raise FuseOSError(errno.ENOENT)
            parent = posixpath.dirname(path.rstrip("/")) or "/"
            d_hit = self._dir_children.get(parent)
            if (d_hit is not None
                    and (now - d_hit[0]) < _ATTR_TTL_S
                    and base_name not in d_hit[1]
                    and path not in self._wbuf):
                self._neg_cache[path] = now
                raise FuseOSError(errno.ENOENT)
        try:
            st = self._attempt(lambda: self._client().stat(path), f"stat {path}")
        except FuseOSError as exc:
            if exc.errno == errno.ENOENT:
                with self._cache_lock:
                    self._neg_cache[path] = time.monotonic()
            raise
        out = self._mkst(st.get("type", "file"), st.get("size", 0), st.get("mtime", 0))
        with self._cache_lock:
            self._attr_cache[path] = (time.monotonic(), out)
            self._neg_cache.pop(path, None)
        return out

    def readdir(self, path, fh):
        yield "."
        yield ".."
        if self._is_indexer_caller():
            return
        norm_dir = (path or "/").rstrip("/") or "/"
        entries = self._attempt(lambda: self._client().listdir(norm_dir),
                                f"listdir {norm_dir}")
        now = time.monotonic()
        names_set: set[str] = set()
        out_names: list[str] = []
        with self._cache_lock:
            for e in entries or []:
                name = e.get("name") or ""
                if not name or name in (".", ".."):
                    continue
                names_set.add(name)
                child = (norm_dir + "/" + name) if norm_dir != "/" else "/" + name
                self._attr_cache[child] = (now, self._mkst(
                    e.get("type", "file"), e.get("size", 0), e.get("mtime", 0)))
                self._neg_cache.pop(child, None)
                out_names.append(name)
            self._dir_children[norm_dir] = (now, names_set)
        for name in out_names:
            yield name

    def getxattr(self, path, name, position=0):
        raise FuseOSError(getattr(errno, "ENODATA", errno.ENOENT))

    def listxattr(self, path):
        return []

    def setxattr(self, path, name, value, options, position=0):
        return 0

    def removexattr(self, path, name):
        return 0

    def open(self, path, flags):
        base_name = posixpath.basename((path or "").rstrip("/"))
        if base_name in _INDEXER_SENTINELS:
            return 0
        if self._is_indexer_caller():
            raise FuseOSError(errno.EACCES)
        wmode = bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_APPEND))
        if wmode and self.readonly:
            raise FuseOSError(errno.EROFS)
        self.getattr(path)
        if wmode:
            if flags & os.O_TRUNC:
                self.truncate(path, 0, None)
            else:
                with self._cache_lock:
                    self._wbuf.setdefault(path, {"start": 0, "data": bytearray()})
        return 0

    def create(self, path, mode, fi=None):
        if self.readonly:
            raise FuseOSError(errno.EROFS)
        self._attempt(lambda: self._client().write_file(path, b""), f"create {path}")
        now = time.monotonic()
        with self._cache_lock:
            self._wbuf[path] = {"start": 0, "data": bytearray()}
            self._attr_cache[path] = (now, self._mkst("file", 0, time.time()))
            self._neg_cache.pop(path, None)
            parent = posixpath.dirname(path.rstrip("/")) or "/"
            if parent in self._dir_children:
                self._dir_children[parent][1].add(posixpath.basename(path.rstrip("/")))
        return 0

    def read(self, path, size, offset, fh):
        base_name = posixpath.basename((path or "").rstrip("/"))
        if base_name in _INDEXER_SENTINELS:
            return b""
        if self._is_indexer_caller():
            raise FuseOSError(errno.EACCES)
        size = int(size)
        offset = int(offset)

        async def _gather(cl):
            out = bytearray()
            async for chunk in cl.read_range_iter(path, offset=offset, length=size):
                if not chunk:
                    continue
                out += chunk
                if len(out) >= size:
                    break
            return bytes(out[:size])

        return self._attempt(lambda: _gather(self._client()), f"read {path}")

    def write(self, path, data, offset, fh):
        if self.readonly:
            raise FuseOSError(errno.EROFS)
        offset = int(offset)
        need_pre_flush = False
        need_post_flush = False
        with self._cache_lock:
            ent = self._wbuf.get(path)
            if ent is None:
                ent = {"start": offset, "data": bytearray()}
                self._wbuf[path] = ent
            if offset == ent["start"] + len(ent["data"]):
                ent["data"] += data
            elif offset == ent["start"] and not ent["data"]:
                ent["data"] += data
            else:
                need_pre_flush = True
            if not need_pre_flush and len(ent["data"]) >= _WRITE_FLUSH_BYTES:
                need_post_flush = True
        if need_pre_flush:
            self._buf_flush(path)
            with self._cache_lock:
                ent = {"start": offset, "data": bytearray(data)}
                self._wbuf[path] = ent
                if len(ent["data"]) >= _WRITE_FLUSH_BYTES:
                    need_post_flush = True
        if need_post_flush:
            self._buf_flush(path)
        return len(data)

    def truncate(self, path, length, fh=None):
        if self.readonly:
            raise FuseOSError(errno.EROFS)
        length = int(length or 0)

        async def _trunc(cl):
            try:
                return await cl.truncate(path, length)
            except Exception:
                if length == 0:
                    return await cl.write_file(path, b"")
                raise

        self._attempt(lambda: _trunc(self._client()), f"truncate {path}")
        with self._cache_lock:
            self._wbuf[path] = {"start": length, "data": bytearray()}
        self._invalidate(path)
        return 0

    def flush(self, path, fh):
        if not self.readonly:
            self._buf_flush(path)
        return 0

    def fsync(self, path, datasync, fh):
        if not self.readonly:
            self._buf_flush(path)
        return 0

    def release(self, path, fh):
        if not self.readonly:
            self._buf_flush(path)
            with self._cache_lock:
                self._wbuf.pop(path, None)
        return 0

    def mkdir(self, path, mode):
        if self.readonly:
            raise FuseOSError(errno.EROFS)
        self._attempt(lambda: self._client().mkdir(path), f"mkdir {path}")
        self._invalidate(path)
        return 0

    def unlink(self, path):
        if self.readonly:
            raise FuseOSError(errno.EROFS)
        self._attempt(lambda: self._client().remove(path), f"unlink {path}")
        with self._cache_lock:
            self._wbuf.pop(path, None)
            self._attr_cache.pop(path, None)
            self._neg_cache[path] = time.monotonic()
            parent = posixpath.dirname(path.rstrip("/")) or "/"
            self._dir_children.pop(parent, None)
        return 0

    def rmdir(self, path):
        if self.readonly:
            raise FuseOSError(errno.EROFS)
        self._attempt(lambda: self._client().rmdir(path), f"rmdir {path}")
        with self._cache_lock:
            self._attr_cache.pop(path, None)
            self._neg_cache[path] = time.monotonic()
            parent = posixpath.dirname(path.rstrip("/")) or "/"
            self._dir_children.pop(parent, None)
        return 0

    def rename(self, old, new):
        if self.readonly:
            raise FuseOSError(errno.EROFS)
        self._attempt(lambda: self._client().rename(old, new), f"rename {old}->{new}")
        with self._cache_lock:
            self._wbuf.pop(old, None)
        self._invalidate(old, new)
        return 0

    def access(self, path, mode):
        self.getattr(path)
        if self.readonly and (mode & os.W_OK):
            raise FuseOSError(errno.EROFS)
        return 0

    def chmod(self, path, mode):
        return 0

    def chown(self, path, uid, gid):
        return 0

    def utimens(self, path, times=None):
        return 0

    def statfs(self, path):
        bsize = 1048576
        try:
            fs = self._attempt(lambda: self._client().fsstat(), "fsstat")
        except FuseOSError:
            return {"f_bsize": bsize, "f_frsize": bsize, "f_blocks": 0,
                    "f_bfree": 0, "f_bavail": 0, "f_files": 0, "f_ffree": 0,
                    "f_namemax": 255}
        total = int(fs.get("total_bytes") or 0)
        free = int(fs.get("free_bytes") or fs.get("avail_bytes") or 0)
        return {
            "f_bsize": bsize, "f_frsize": bsize,
            "f_blocks": total // bsize, "f_bfree": free // bsize,
            "f_bavail": free // bsize,
            "f_files": 1_000_000, "f_ffree": 1_000_000, "f_namemax": 255,
        }

class PhoneFuseBridge:

    def __init__(self, client_or_getter, loop: asyncio.AbstractEventLoop,
                 readonly: bool = False, timeout: float = 20.0) -> None:
        self.client_getter = (client_or_getter if callable(client_or_getter)
                              else (lambda: client_or_getter))
        self.loop = loop
        self.readonly = bool(readonly)
        self.timeout = timeout
        self.mountpoint: str | None = None
        self.last_error: str = ""
        self._thread: threading.Thread | None = None
        self._fuse = None

    def start_async(self, mountpoint: str):
        self.mountpoint = mountpoint
        self._thread = threading.Thread(
            target=self._run, args=(mountpoint,),
            name="phone-fuse-bridge", daemon=True)
        self._thread.start()
        return self._thread

    def _run(self, mountpoint: str) -> None:
        if FUSE is None:
            self.last_error = _FUSE_IMPORT_ERROR or "fusepy not importable"
            return
        for _ln in ("fuse", "fuse.log-mixin"):
            logging.getLogger(_ln).setLevel(logging.WARNING)
        cl_now = self.client_getter() if callable(self.client_getter) else self.client_getter
        export_p = str(getattr(cl_now, "mount_path", "/sdcard") or "/sdcard")
        if not export_p.startswith("/"):
            export_p = "/" + export_p
        nfs_fsname = f"127.0.0.1:{export_p}"
        try:
            ops = _PhoneFuseOps(self.client_getter, self.loop,
                                timeout=self.timeout, readonly=self.readonly)
            self._fuse = FUSE(ops, mountpoint, foreground=True,
                              ro=self.readonly, allow_other=False,
                              auto_unmount=True, fsname=nfs_fsname, subtype="nfs")
        except Exception as exc:
            try:
                ops = _PhoneFuseOps(self.client_getter, self.loop,
                                    timeout=self.timeout, readonly=self.readonly)
                self._fuse = FUSE(ops, mountpoint, foreground=True,
                                  ro=self.readonly, allow_other=False,
                                  fsname=nfs_fsname)
            except Exception as exc2:
                self.last_error = f"{exc!r}; fallback: {exc2!r}"

    @property
    def running(self) -> bool:
        return bool(self._thread is not None and self._thread.is_alive())

    def stop_sync(self) -> None:
        mp = self.mountpoint
        if not mp:
            return
        for cmd in (["fusermount", "-u", mp], ["fusermount3", "-u", mp],
                    ["fusermount", "-u", "-z", mp], ["fusermount3", "-u", "-z", mp],
                    ["umount", "-l", mp]):
            if not shutil.which(cmd[0]):
                continue
            try:
                subprocess.run(cmd, timeout=3, check=False,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2.0)
