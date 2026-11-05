from __future__ import annotations

import asyncio
import errno
import os
import threading
import time
import unittest

from samyuj.files.nfs import fuse_bridge
from samyuj.files.nfs.fuse_bridge import PhoneFuseBridge, _PhoneFuseOps, _map_errno

class FakePhoneClient:

    def __init__(self, fail_times: int = 0) -> None:
        self.calls: list[tuple] = []
        self.fail_times = fail_times
        self.files = {
            "/DCIM": {"type": "dir", "size": 0, "mtime": 100.0},
            "/DCIM/a.jpg": {"type": "file", "size": 3, "mtime": 101.0},
        }
        self.writes: list[tuple[str, int, bytes]] = []
        self.truncates: list[tuple[str, int]] = []
        self.created: list[str] = []
        self.removed: list[str] = []
        self.rmdirs: list[str] = []
        self.renames: list[tuple[str, str]] = []
        self.mkdirs: list[str] = []

    def _maybe_fail(self, what: str) -> None:
        if self.fail_times > 0:
            self.fail_times -= 1
            raise TimeoutError(f"{what}: simulated transient timeout")

    async def stat(self, path: str) -> dict:
        self.calls.append(("stat", path))
        self._maybe_fail("stat")
        if path not in self.files:
            raise RuntimeError("NFS3ERR_NOENT: no such file or directory")
        return dict(self.files[path])

    async def listdir(self, path: str) -> list[dict]:
        self.calls.append(("listdir", path))
        self._maybe_fail("listdir")
        prefix = path.rstrip("/") + "/"
        out = []
        for p, meta in self.files.items():
            if p.startswith(prefix) and "/" not in p[len(prefix):]:
                out.append({"name": p[len(prefix):], **meta})
        return out

    async def read_range_iter(self, path: str, offset: int = 0, length: int | None = None):
        self.calls.append(("read", path, offset, length))
        self._maybe_fail("read")
        payload = b"abc"[offset % 3:] if path == "/DCIM/a.jpg" else b""
        yield payload[:length]

    async def fsstat(self) -> dict:
        return {"total_bytes": 10 << 30, "free_bytes": 4 << 30, "avail_bytes": 4 << 30}

    async def write_file(self, path: str, data: bytes) -> dict:
        self.calls.append(("write_file", path, len(data)))
        self.created.append(path)
        self.files[path] = {"type": "file", "size": len(data), "mtime": time.time()}
        return {"ok": True}

    async def write_range(self, path: str, offset: int, data: bytes) -> dict:
        self.calls.append(("write_range", path, offset, len(data)))
        self.writes.append((path, offset, data))
        return {"ok": True}

    async def truncate(self, path: str, size: int = 0) -> dict:
        self.calls.append(("truncate", path, size))
        self.truncates.append((path, size))
        return {"ok": True}

    async def mkdir(self, path: str) -> dict:
        self.mkdirs.append(path)
        return {"ok": True}

    async def remove(self, path: str) -> dict:
        self.removed.append(path)
        self.files.pop(path, None)
        return {"ok": True}

    async def rmdir(self, path: str) -> dict:
        self.rmdirs.append(path)
        return {"ok": True}

    async def rename(self, src: str, dst: str) -> dict:
        self.renames.append((src, dst))
        return {"ok": True}

class FuseOpsTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.loop = asyncio.new_event_loop()
        cls.thread = threading.Thread(target=cls.loop.run_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.loop.call_soon_threadsafe(cls.loop.stop)
        cls.thread.join(timeout=3)
        cls.loop.close()

    def _ops(self, client=None, readonly: bool = False) -> tuple[_PhoneFuseOps, FakePhoneClient]:
        cl = client if client is not None else FakePhoneClient()
        holder = {"cl": cl}
        ops = _PhoneFuseOps(lambda: holder["cl"], self.loop, timeout=5.0, readonly=readonly)
        return ops, cl

    def test_root_getattr_returns_immediately_without_rpc_deadlock(self) -> None:
        unstarted_loop = asyncio.new_event_loop()
        try:
            cl = FakePhoneClient()
            ops = _PhoneFuseOps(lambda: cl, unstarted_loop, timeout=0.2)
            st = ops.getattr("/")
            self.assertTrue(st["st_mode"] & 0o170000 == 0o040000)
            self.assertEqual(cl.calls, [])
        finally:
            unstarted_loop.close()

    def test_getattr_caches_and_negative_caches(self) -> None:
        ops, cl = self._ops()
        st = ops.getattr("/DCIM")
        self.assertTrue(st["st_mode"] & 0o170000 == 0o040000)
        st2 = ops.getattr("/DCIM")
        self.assertEqual([c for c in cl.calls if c[0] == "stat"], [("stat", "/DCIM")])
        self.assertEqual(st, st2)
        with self.assertRaises(fuse_bridge.FuseOSError) as ctx:
            ops.getattr("/nope")
        self.assertEqual(ctx.exception.errno, errno.ENOENT)
        n_stat = len([c for c in cl.calls if c[0] == "stat"])
        with self.assertRaises(fuse_bridge.FuseOSError):
            ops.getattr("/nope")
        self.assertEqual(len([c for c in cl.calls if c[0] == "stat"]), n_stat)

    def test_readdir_primes_attr_cache(self) -> None:
        ops, cl = self._ops()
        names = list(ops.readdir("/DCIM", 0))
        self.assertIn(".", names)
        self.assertIn("..", names)
        self.assertIn("a.jpg", names)
        cached = ops._cached_stat("/DCIM/a.jpg")
        self.assertIsNotNone(cached)
        self.assertEqual(cached["st_size"], 3)

    def test_read_aggregates_chunks(self) -> None:
        ops, cl = self._ops()
        data = ops.read("/DCIM/a.jpg", 3, 0, 0)
        self.assertEqual(data, b"abc")

    def test_transient_errors_are_retried(self) -> None:
        cl = FakePhoneClient(fail_times=1)
        ops, _ = self._ops(client=cl)
        st = ops.getattr("/DCIM")
        self.assertEqual(st["st_size"], 0)
        self.assertEqual(len([c for c in cl.calls if c[0] == "stat"]), 2)

    def test_persistent_transient_errors_surface_as_eagain(self) -> None:
        cl = FakePhoneClient(fail_times=5)
        ops, _ = self._ops(client=cl)
        with self.assertRaises(fuse_bridge.FuseOSError) as ctx:
            ops.getattr("/DCIM")
        self.assertEqual(ctx.exception.errno, errno.EAGAIN)
        self.assertEqual(len([c for c in cl.calls if c[0] == "stat"]), 2)

    def test_missing_client_maps_to_enotconn(self) -> None:
        ops = _PhoneFuseOps(lambda: None, self.loop, timeout=2.0)
        with self.assertRaises(fuse_bridge.FuseOSError) as ctx:
            ops.getattr("/DCIM")
        self.assertEqual(ctx.exception.errno, errno.ENOTCONN)

    def test_write_buffers_then_flushes_on_release(self) -> None:
        ops, cl = self._ops()
        ops.create("/DCIM/new.txt", 0o644, None)
        self.assertEqual(cl.created, ["/DCIM/new.txt"])
        n = ops.write("/DCIM/new.txt", b"hello ", 0, 0)
        n += ops.write("/DCIM/new.txt", b"world", 6, 0)
        self.assertEqual(n, 11)
        self.assertEqual(cl.writes, [])
        ops.release("/DCIM/new.txt", 0)
        self.assertEqual(cl.writes, [("/DCIM/new.txt", 0, b"hello world")])

    def test_nonsequential_write_flushes_window(self) -> None:
        ops, cl = self._ops()
        ops.create("/f.bin", 0o644, None)
        ops.write("/f.bin", b"AAAA", 0, 0)
        ops.write("/f.bin", b"Z", 100, 0)
        self.assertEqual(cl.writes[0], ("/f.bin", 0, b"AAAA"))
        ops.release("/f.bin", 0)
        self.assertEqual(cl.writes[1], ("/f.bin", 100, b"Z"))

    def test_open_trunc_sets_size_zero(self) -> None:
        ops, cl = self._ops()
        ops.open("/DCIM/a.jpg", os.O_RDWR | os.O_TRUNC)
        self.assertIn(("/DCIM/a.jpg", 0), cl.truncates)

    def test_mkdir_unlink_rmdir_rename_delegate(self) -> None:
        ops, cl = self._ops()
        ops.mkdir("/DCIM/New", 0o755)
        ops.unlink("/DCIM/a.jpg")
        ops.rmdir("/DCIM/New")
        ops.rename("/DCIM/x", "/DCIM/y")
        self.assertEqual(cl.mkdirs, ["/DCIM/New"])
        self.assertEqual(cl.removed, ["/DCIM/a.jpg"])
        self.assertEqual(cl.rmdirs, ["/DCIM/New"])
        self.assertEqual(cl.renames, [("/DCIM/x", "/DCIM/y")])

    def test_readonly_mode_rejects_writes(self) -> None:
        ops, _ = self._ops(readonly=True)
        for fn, args in (("write", ("/x", b"d", 0, 0)), ("create", ("/x", 0, None)),
                         ("mkdir", ("/x", 0)), ("unlink", ("/x",)),
                         ("rename", ("/x", "/y")), ("truncate", ("/x", 0, None))):
            with self.assertRaises(fuse_bridge.FuseOSError) as ctx:
                getattr(ops, fn)(*args)
            self.assertEqual(ctx.exception.errno, errno.EROFS, fn)
        with self.assertRaises(fuse_bridge.FuseOSError):
            ops.access("/DCIM", os.W_OK)

    def test_statfs_reports_phone_capacity(self) -> None:
        ops, _ = self._ops()
        fs = ops.statfs("/")
        self.assertEqual(fs["f_blocks"], (10 << 30) // 1048576)
        self.assertEqual(fs["f_bfree"], (4 << 30) // 1048576)

    def test_map_errno_coverage(self) -> None:
        self.assertEqual(_map_errno(RuntimeError("no such file or directory (status 2)")), errno.ENOENT)
        self.assertEqual(_map_errno(RuntimeError("not a directory (status 20)")), errno.ENOTDIR)
        self.assertEqual(_map_errno(RuntimeError("is a directory (status 21)")), errno.EISDIR)
        self.assertEqual(_map_errno(RuntimeError("NFS3ERR_STALE (status 70)")), errno.ESTALE)
        self.assertEqual(_map_errno(RuntimeError("server busy, try again (status 10008)")), errno.EBUSY)
        self.assertEqual(_map_errno(RuntimeError("NFS3ERR_ROFS")), errno.EROFS)
        self.assertEqual(_map_errno(TimeoutError("x")), errno.EAGAIN)

    def test_xattr_and_indexer_sentinels_avoid_rpc(self) -> None:
        ops, cl = self._ops()
        self.assertEqual(ops.listxattr("/DCIM"), [])
        with self.assertRaises(fuse_bridge.FuseOSError) as ctx:
            ops.getxattr("/DCIM", "user.baloo.rating")
        self.assertEqual(ctx.exception.errno, getattr(errno, "ENODATA", errno.ENOENT))
        for sentinel in ("/.nomedia", "/.trackerignore", "/DCIM/.nomedia"):
            st = ops.getattr(sentinel)
            self.assertEqual(st["st_size"], 0)
            self.assertEqual(ops.read(sentinel, 64, 0, 0), b"")
        self.assertEqual(cl.calls, [])

class PhoneFuseBridgeNoFuseTest(unittest.TestCase):
    def test_bridge_records_error_without_fusepy(self) -> None:
        loop = asyncio.new_event_loop()
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()
        try:
            b = PhoneFuseBridge(FakePhoneClient(), loop)
            if fuse_bridge.FUSE is None:
                b._run("/nonexistent-mountpoint")
                self.assertTrue(b.last_error)
            self.assertFalse(b.running)
        finally:
            loop.call_soon_threadsafe(loop.stop)
            t.join(timeout=3)
            loop.close()

class MountPathsAndBookmarksTest(unittest.TestCase):

    def _with_env(self, td: str, xdg: str | None):
        old = {k: os.environ.get(k) for k in ("HOME", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME")}
        os.environ["HOME"] = td
        os.environ.pop("XDG_CONFIG_HOME", None)
        os.environ.pop("XDG_CACHE_HOME", None)
        if xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = xdg
        return old

    @staticmethod
    def _restore(old: dict) -> None:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_default_base_is_hidden_xdg_path(self) -> None:
        import tempfile as _tf
        from pathlib import Path
        from samyuj.files.nfs import PhoneLinuxMountManager, default_mount_base

        with _tf.TemporaryDirectory(prefix="sc-home-") as td:
            old = self._with_env(td, None)
            try:
                base = default_mount_base()
                self.assertEqual(base, Path(td) / ".local" / "share" / "samyuj" / "mounts")
                rel_parts = base.relative_to(td).parts
                self.assertTrue(rel_parts[0].startswith("."),
                                "mount base must live under a hidden dir so indexers skip it")
                mgr = PhoneLinuxMountManager(None)
                self.assertEqual(mgr.base_dir, base)
            finally:
                self._restore(old)

        with _tf.TemporaryDirectory(prefix="sc-home-") as td:
            xdg = str(Path(td) / "data")
            old = self._with_env(td, xdg)
            try:
                self.assertEqual(default_mount_base(), Path(xdg) / "samyuj" / "mounts")
            finally:
                self._restore(old)

    def test_bookmarks_registered_in_gtk_and_kde_places_without_duplicates(self) -> None:
        import tempfile as _tf
        from pathlib import Path
        from samyuj.files.nfs import PhoneLinuxMountManager

        with _tf.TemporaryDirectory(prefix="sc-home-") as td:
            xdg = str(Path(td) / "data")
            old = self._with_env(td, xdg)
            try:
                mgr = PhoneLinuxMountManager(None)
                mp = Path(td) / "mnt" / "Moto_Test"
                mp.mkdir(parents=True)
                self.assertTrue(mgr.register_file_manager_bookmarks(mp, "Moto Test"))
                self.assertTrue(mgr.bookmark_registered)

                with open(Path(td) / ".config" / "gtk-3.0" / "bookmarks", encoding="utf-8") as fh:
                    gtk_bm = fh.read()
                self.assertIn("Moto Test (Phone NFS)", gtk_bm)
                self.assertIn("file://" + str(mp.resolve()), gtk_bm)

                xbel_p = Path(xdg) / "user-places.xbel"
                with open(xbel_p, encoding="utf-8") as fh:
                    xbel = fh.read()
                self.assertIn("<xbel", xbel)
                self.assertIn(f'href="file://{mp.resolve()}"', xbel)
                self.assertIn("<title>Moto Test (Phone NFS)</title>", xbel)

                mgr.register_file_manager_bookmarks(mp, "Moto Test")
                with open(xbel_p, encoding="utf-8") as fh:
                    self.assertEqual(fh.read().count("<bookmark "), 1)
                with open(Path(td) / ".config" / "gtk-3.0" / "bookmarks", encoding="utf-8") as fh:
                    lines = [l for l in fh.read().splitlines() if l.strip()]
                self.assertEqual(len(lines), 1)
                baloo_p = Path(td) / ".config" / "baloofilerc"
                self.assertFalse(baloo_p.exists())
                with open(baloo_p, "w", encoding="utf-8") as fh:
                    fh.write("[General]\n")
                mgr.register_file_manager_bookmarks(mp, "Moto Test")
                with open(baloo_p, encoding="utf-8") as fh:
                    baloo_cfg = fh.read()
                self.assertIn("exclude folders", baloo_cfg)
                self.assertTrue((mp / ".nomedia").exists())
                self.assertTrue((mp / ".trackerignore").exists())
            finally:
                self._restore(old)

if __name__ == "__main__":
    unittest.main()
