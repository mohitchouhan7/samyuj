from __future__ import annotations
from .loopback import MAX_WRITEBACK_FILE_BYTES

import asyncio
import contextlib
import os
import shutil
import subprocess
import time
import urllib.parse
from pathlib import Path

from .loopback import LocalPhoneNfsLoopback, sanitize_device_folder_name

def default_mount_base() -> Path:
    data_home = os.environ.get("XDG_DATA_HOME")
    if data_home:
        return Path(data_home).expanduser() / "samyuj" / "mounts"
    return Path.home() / ".local" / "share" / "samyuj" / "mounts"

class PhoneLinuxMountManager:

    KEY_MEDIA_DIRS = ("/DCIM", "/Pictures", "/Download", "/Documents", "/Music", "/Movies")

    def __init__(self, harness, base_dir: Path | None = None) -> None:
        self.h = harness
        self.base_dir = Path(base_dir).expanduser() if base_dir else default_mount_base()
        self.loopback = LocalPhoneNfsLoopback(
            lambda: (getattr(self.h, "_pfs_client", None) or getattr(self.h, "phonefs_client", None))
        )
        self.mount_dir: Path | None = None
        self.device_label: str = ""
        self.state: str = "unmounted"
        self.bookmark_registered: bool = False
        self.synced_entries: int = 0
        self.last_sync_ts: float = 0.0
        self.kernel_mounted_at: str | None = None
        self.fuse_mounted_at: str | None = None
        self.fuse_readonly: bool = False
        self.attach_method: str = "auto"
        self._fuse_bridge = None
        self._fuse_supervisor: asyncio.Task | None = None
        self._gio_mounted_uri: str | None = None
        self._watch_task: asyncio.Task | None = None
        self._deep_sync_task: asyncio.Task | None = None
        self._attach_lock = asyncio.Lock()
        self._known_mtimes: dict[str, tuple[float, int]] = {}

    def get_mount_root(self, device_name: str | None = None) -> Path:
        name = device_name or self.h._best_device_name() or "Connected_Phone"
        folder = sanitize_device_folder_name(name)
        self.device_label = name
        self.mount_dir = self.base_dir / folder
        return self.mount_dir

    @staticmethod
    def _safe_mount_uri(mount_path: Path) -> str:
        try:
            if mount_path.name:
                resolved_str = str(mount_path.parent.resolve() / mount_path.name)
            else:
                resolved_str = os.path.abspath(str(mount_path))
        except OSError:
            resolved_str = os.path.abspath(str(mount_path))
        return "file://" + urllib.parse.quote(resolved_str, safe="/")

    def exclude_from_desktop_indexers(self, *paths: Path) -> bool:
        ok = False
        targets = [self.base_dir, *[Path(p) for p in paths if p is not None]]
        for d in targets:
            if self._fuse_mountpoint_alive(d):
                continue
            with contextlib.suppress(OSError):
                d.mkdir(parents=True, exist_ok=True)
                for sentinel in (".nomedia", ".trackerignore", ".noindex", ".metadata_never_index"):
                    sp = d / sentinel
                    if not sp.exists():
                        sp.write_bytes(b"")
                ok = True

        with contextlib.suppress(OSError):
            cfg_candidates = [Path.home() / ".config"]
            env_cfg = os.environ.get("XDG_CONFIG_HOME")
            if env_cfg:
                cfg_candidates.insert(0, Path(env_cfg))
            baloo_rc = next((c / "baloofilerc" for c in cfg_candidates if (c / "baloofilerc").is_file()), None)
            if baloo_rc is None:
                return ok
            base_abs = os.path.abspath(os.path.expanduser(str(self.base_dir))).rstrip("/") + "/"
            home_abs = os.path.abspath(os.path.expanduser(str(Path.home()))).rstrip("/")
            rel_token = ("$HOME/" + base_abs[len(home_abs) + 1:]) if base_abs.startswith(home_abs + "/") else base_abs
            raw = baloo_rc.read_text(encoding="utf-8", errors="replace")
            lines = raw.splitlines()
            in_general = False
            found_general = False
            found_exclude = False
            new_lines: list[str] = []
            for ln in lines:
                stripped = ln.strip()
                if stripped.startswith("[") and stripped.endswith("]"):
                    if in_general and not found_exclude:
                        new_lines.append(f"exclude folders[$e]=$HOME/,{rel_token}" if "$HOME/" not in rel_token else f"exclude folders[$e]={rel_token}")
                        found_exclude = True
                    in_general = (stripped.lower() == "[general]")
                    if in_general:
                        found_general = True
                    new_lines.append(ln)
                    continue
                if in_general and (stripped.startswith("exclude folders[$e]=") or stripped.startswith("exclude folders=")):
                    found_exclude = True
                    key, _, val = ln.partition("=")
                    parts = [p.strip() for p in val.split(",") if p.strip()]
                    if rel_token not in parts and base_abs not in parts and base_abs.rstrip("/") not in parts:
                        parts.append(rel_token)
                    new_lines.append(f"{key}={','.join(parts)}")
                else:
                    new_lines.append(ln)
            if not found_general:
                new_lines.append("[General]")
                new_lines.append(f"exclude folders[$e]={rel_token}")
            elif in_general and not found_exclude:
                new_lines.append(f"exclude folders[$e]={rel_token}")
            baloo_rc.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
            ok = True
        return ok

    def _stale_device_uris(self, keep_uri: str) -> set[str]:
        stale: set[str] = set()
        if self.mount_dir is not None:
            for cand in (self.mount_dir, self.mount_dir / "live", self.mount_dir / "phone"):
                u = self._safe_mount_uri(cand)
                if u != keep_uri:
                    stale.add(u)
        return stale

    def register_gtk_bookmark(self, mount_path: Path, label: str) -> bool:
        try:
            bm_dir = Path.home() / ".config" / "gtk-3.0"
            bm_dir.mkdir(parents=True, exist_ok=True)
            bm_file = bm_dir / "bookmarks"
            uri = self._safe_mount_uri(mount_path)
            clean_label = label if "(Phone NFS)" in label else f"{label} (Phone NFS)"
            line_to_add = f"{uri} {clean_label}"
            stale_uris = self._stale_device_uris(uri)
            existing: list[str] = []
            if bm_file.is_file():
                existing = bm_file.read_text(encoding="utf-8", errors="replace").splitlines()
            prefix = uri + " "
            updated = False
            new_lines: list[str] = []
            for ln in existing:
                s = ln.strip()
                if not s:
                    continue
                if any(s == su or s.startswith(su + " ") for su in stale_uris):
                    continue
                if s == uri or s.startswith(prefix):
                    if not updated:
                        new_lines.append(line_to_add)
                        updated = True
                else:
                    new_lines.append(ln)
            if not updated:
                new_lines.append(line_to_add)
            bm_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
            self.bookmark_registered = True
            return True
        except OSError:
            return False

    def register_kde_place(self, mount_path: Path, label: str) -> bool:
        try:
            import re as _re
            data_home = Path(os.environ.get("XDG_DATA_HOME")
                             or (Path.home() / ".local" / "share"))
            data_home.mkdir(parents=True, exist_ok=True)
            xbel = data_home / "user-places.xbel"
            uri = self._safe_mount_uri(mount_path)
            title = label if "(Phone NFS)" in label else f"{label} (Phone NFS)"
            header = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                      '<xbel version="1.0" '
                      'xmlns:bookmark="http://www.freedesktop.org/standards/desktop-bookmarks" '
                      'xmlns:mime="http://www.freedesktop.org/standards/shared-mime-info">\n')
            entry = (
                f'<bookmark href="{uri}" '
                f'added="{time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}">\n'
                f"<title>{title}</title>\n"
                f"</bookmark>\n"
            )
            stale_uris = self._stale_device_uris(uri)
            if xbel.is_file():
                text = xbel.read_text(encoding="utf-8", errors="replace")
                for su in stale_uris:
                    pat = _re.compile(
                        r'<bookmark\s+href="' + _re.escape(su) + r'"[^>]*>.*?</bookmark>\s*',
                        _re.DOTALL,
                    )
                    text = pat.sub("", text)
                if f'href="{uri}"' in text:
                    xbel.write_text(text, encoding="utf-8")
                    return True
                if "</xbel>" in text:
                    text = text.replace("</xbel>", entry + "</xbel>")
                else:
                    text = text.rstrip() + "\n" + entry + "</xbel>\n"
            else:
                text = header + entry + "</xbel>\n"
            xbel.write_text(text, encoding="utf-8")
            return True
        except OSError:
            return False

    def register_file_manager_bookmarks(self, mount_path: Path, label: str) -> bool:
        self.exclude_from_desktop_indexers(mount_path.parent if mount_path.name in ("live", "phone") else mount_path)
        gtk_ok = self.register_gtk_bookmark(mount_path, label)
        kde_ok = self.register_kde_place(mount_path, label)
        self.bookmark_registered = bool(gtk_ok or kde_ok)
        return bool(gtk_ok or kde_ok)

    def _has_active_media_stream(self) -> bool:
        with contextlib.suppress(Exception):
            pcs = getattr(self.h, "_pcs", None)
            if pcs:
                return True
        return False

    async def _background_deep_sync(self, download_small_files: bool = True) -> None:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            for sub in self.KEY_MEDIA_DIRS:
                if self.state != "mounted":
                    return
                eager_dl = bool(
                    download_small_files and not self._has_active_media_stream()
                )
                await self.sync_directory(
                    sub,
                    max_depth=2,
                    download_small_files=eager_dl,
                    small_file_limit=8 * 1024 * 1024,
                )
                await asyncio.sleep(0.15)

    async def attach(self, device_name: str | None = None, sync_files: bool = True) -> dict:
        async with self._attach_lock:
            if (self.state == "mounted" and self.loopback.port
                    and self.mount_dir is not None):
                want = sanitize_device_folder_name(device_name) if device_name else None
                if want is None or want == self.mount_dir.name:
                    with contextlib.suppress(Exception):
                        self.h.events.log(
                            "PHONEFS_LINUX_ATTACH_DEDUP",
                            f"already mounted at {self.mount_dir} — skipping duplicate attach",
                            feature="file_transfer")
                    return {
                        "ok": True, "state": self.state, "deduped": True,
                        "mount_path": str(self.mount_dir),
                        "loopback_port": self.loopback.port,
                        "nfs_uri": f"nfs://127.0.0.1:{self.loopback.port}/sdcard",
                        "bookmark_registered": self.bookmark_registered,
                        "synced_entries": self.synced_entries,
                        "sync_result": {"ok": True, "deduped": True},
                        "gio_mount": dict(getattr(self, "gio_mount", {}) or {}),
                    }
            port = await self.loopback.start()
            mroot = self.get_mount_root(device_name)
            await asyncio.to_thread(mroot.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(self.exclude_from_desktop_indexers, self.base_dir, mroot)

            with contextlib.suppress(OSError):
                info_txt = (
                    f"Device: {self.device_label}\n"
                    f"Phone Path: {self.h.phonefs.get('path', '/sdcard')}\n"
                    f"Local NFSv3 Loopback: 127.0.0.1:{port}\n"
                    f"Kernel NFSv3 Mount Command:\n"
                    f"  sudo mount -t nfs -o vers=3,proto=tcp,port={port},mountport={port},nolock "
                    f"127.0.0.1:/sdcard {mroot}\n"
                    f"GVFS Mount Command:\n"
                    f"  gio mount \"nfs://127.0.0.1:{port}/sdcard?version=3&nfsport={port}&mountport={port}\"\n"
                )
                await asyncio.to_thread((mroot / ".samyuj_nfs_info.txt").write_text, info_txt, "utf-8")

            self.state = "mounted"
            method = str((getattr(self.h, "user_settings", {}) or {})
                         .get("nfs_attach_method") or "auto").lower()
            if method not in ("auto", "mirror", "gvfs", "fuse", "kernel"):
                method = "auto"
            self.attach_method = method
            if method == "mirror":
                self.gio_mount = {"ok": False, "skipped": True,
                                  "hint": "attach method = mirror (local folder only)"}
            elif method == "gvfs":
                self.gio_mount = await self.auto_gio_mount(port, allow_kernel_fallback=False)
                if not self.gio_mount.get("ok"):
                    self.gio_mount["hint"] = (
                        "GVFS mount failed — install gvfs-backends (Debian/Ubuntu) or "
                        "gvfs-nfs (Fedora), or pick another Phone-files method in Settings. "
                        + str(self.gio_mount.get("hint") or ""))
            elif method == "kernel":
                cl_k = self.loopback._get_client()
                export_k = str(getattr(cl_k, "mount_path", "") or "/") or "/"
                exp_k = export_k if export_k.startswith("/") else "/" + export_k
                kern = await self.kernel_mount_fallback(port, exp_k)
                self.gio_mount = {"ok": bool(kern.get("ok")), "method": "kernel",
                                  "path": kern.get("path"),
                                  "hint": kern.get("hint"), "error": kern.get("error")}
            elif method == "fuse":
                self.gio_mount = await self.fuse_mount_attach()
            else:
                fus = await self.fuse_mount_attach()
                if fus.get("ok"):
                    self.gio_mount = fus
                else:
                    gio = await self.auto_gio_mount(port)
                    if not gio.get("ok"):
                        gio["hint"] = (
                            "FUSE: " + str(fus.get("hint") or fus.get("error") or "")
                            + " | GVFS: " + str(gio.get("hint") or ""))
                    self.gio_mount = gio

            live_ok = bool((self.gio_mount or {}).get("ok"))
            live_path_str = str((self.gio_mount or {}).get("path") or "")
            bookmark_target = Path(live_path_str) if (live_ok and live_path_str) else mroot
            await asyncio.to_thread(self.register_file_manager_bookmarks, bookmark_target, self.device_label)

            if method == "mirror" or sync_files or not live_ok:
                sync_res = await self.sync_directory(
                    "/",
                    max_depth=1 if sync_files else 0,
                    download_small_files=sync_files,
                    small_file_limit=4 * 1024 * 1024,
                )
            else:
                sync_res = {"ok": True, "path": "/", "local_path": str(bookmark_target), "count": 0, "live": True}

            deep_wanted = bool((getattr(self.h, "user_settings", {}) or {})
                               .get("nfs_deep_media_sync", False))
            if (method == "mirror" and deep_wanted
                    and (self._deep_sync_task is None or self._deep_sync_task.done())):
                self._deep_sync_task = asyncio.create_task(self._background_deep_sync(sync_files))
            if (method == "mirror" or not live_ok) and (self._watch_task is None or self._watch_task.done()):
                self._watch_task = asyncio.create_task(self._local_writeback_watcher())
            self.h.events.log(
                "PHONEFS_LINUX_MOUNTED",
                f"Phone NFS attached at {mroot} (loopback=127.0.0.1:{port}, "
                f"bookmark={self.bookmark_registered}, entries={self.synced_entries})",
                feature="file_transfer",
            )
            return {
                "ok": True,
                "state": self.state,
                "mount_path": str(mroot),
                "loopback_port": port,
                "nfs_uri": f"nfs://127.0.0.1:{port}/sdcard",
                "bookmark_registered": self.bookmark_registered,
                "synced_entries": self.synced_entries,
                "sync_result": sync_res,
                "gio_mount": dict(getattr(self, "gio_mount", {}) or {}),
            }

    async def auto_gio_mount(self, port: int, allow_kernel_fallback: bool | None = None) -> dict:
        cl = self.loopback._get_client()
        export = str(getattr(cl, "mount_path", "") or "/") or "/"
        exp_path = export if export.startswith("/") else "/" + export
        uri = "nfs://127.0.0.1:%d%s" % (port, exp_path)
        uri_with_opts = f"{uri}?version=3&nfsport={port}&mountport={port}"
        if not shutil.which("gio"):
            return {
                "ok": False,
                "uri": uri,
                "error": "gio not found",
                "hint": (
                    "Install glib2 (gio) or mount manually: sudo mount -t nfs "
                    f"-o vers=3,proto=tcp,port={port},mountport={port},nolock "
                    f"127.0.0.1:{export} <mountpoint>"
                ),
            }
        try:
            err_txt = ""
            for cand_uri in (uri, uri_with_opts):
                proc_u = await asyncio.create_subprocess_exec(
                    "gio", "mount", "-u", cand_uri,
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                )
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(proc_u.communicate(), timeout=3.0)
                proc = await asyncio.create_subprocess_exec(
                    "gio", "mount", cand_uri,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                try:
                    _out, err = await asyncio.wait_for(proc.communicate(), timeout=12.0)
                except asyncio.TimeoutError:
                    with contextlib.suppress(Exception):
                        proc.kill()
                    err_txt = "timeout"
                    continue
                err_txt = (err or b"").decode("utf-8", "replace").strip()
                if proc.returncode == 0:
                    self._gio_mounted_uri = cand_uri
                    with contextlib.suppress(Exception):
                        self.h.events.log(
                            "PHONEFS_GIO_MOUNTED",
                            f"{cand_uri} mounted in the Linux file manager",
                            feature="file_transfer",
                        )
                    return {"ok": True, "uri": uri, "mounted_uri": cand_uri}
            if err_txt == "timeout":
                return {
                    "ok": False,
                    "uri": uri,
                    "error": "timeout",
                    "hint": "The NFS mount timed out — check the phone is still connected (Files page) and retry.",
                }
            hint = err_txt
            if "unsupported" in err_txt.lower() or "unhandled" in err_txt.lower():
                hint = (
                    "Your file manager lacks the gvfs NFS backend. Install it: "
                    "sudo apt install gvfs-backends (Debian/Ubuntu) or "
                    "sudo dnf install gvfs-nfs (Fedora) — then retry. Meanwhile "
                    "your phone files are mirrored under the Samyuj "
                    "bookmark folder."
                )
            with contextlib.suppress(Exception):
                self.h.events.log(
                    "PHONEFS_GIO_MOUNT_FAIL",
                    f"{uri} {err_txt[:200]}",
                    feature="file_transfer",
                )
            if allow_kernel_fallback is None:
                allow_kernel_fallback = bool(
                    getattr(self.h, "user_settings", {}).get("allow_kernel_nfs_fallback", False)
                )
            if allow_kernel_fallback:
                kern = await self.kernel_mount_fallback(port, export)
                if kern.get("ok"):
                    return {
                        "ok": True,
                        "uri": uri,
                        "method": "kernel",
                        "path": kern.get("path"),
                        "note": (
                            "Mounted with the kernel NFS client (one permission "
                            "prompt). Visible in every file manager."
                        ),
                    }
                hint = hint + " | kernel fallback: " + str(kern.get("hint") or kern.get("error") or "failed")
            return {"ok": False, "uri": uri, "error": err_txt[:200], "hint": hint}
        except Exception as exc:
            return {"ok": False, "uri": uri, "error": repr(exc)[:200]}

    async def kernel_mount_fallback(self, port: int, export: str) -> dict:
        try:
            base = self.mount_dir or self.get_mount_root()
            mdir = Path(base) / "phone"
            mdir.mkdir(parents=True, exist_ok=True)
            if not (shutil.which("mount") and shutil.which("pkexec")):
                return {
                    "ok": False,
                    "error": "mount/pkexec not found",
                    "hint": "install util-linux + polkit + nfs-common/nfs-utils",
                }
            exp = export if export.startswith("/") else "/" + export
            cmd = [
                "pkexec",
                "mount",
                "-t",
                "nfs",
                "-o",
                f"vers=3,proto=tcp,port={port},mountport={port},nolock,soft,timeo=50,retrans=2",
                f"127.0.0.1:{exp}",
                str(mdir),
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _o, err = await asyncio.wait_for(proc.communicate(), timeout=30.0)
            except asyncio.TimeoutError:
                with contextlib.suppress(Exception):
                    proc.kill()
                return {
                    "ok": False,
                    "error": "timeout",
                    "hint": "the permission prompt was not answered in time",
                }
            err_txt = (err or b"").decode("utf-8", "replace").strip()
            if proc.returncode == 0:
                self.kernel_mounted_at = str(mdir)
                self.register_file_manager_bookmarks(mdir, self.device_label)
                with contextlib.suppress(Exception):
                    self.h.events.log(
                        "PHONEFS_KERNEL_MOUNTED",
                        f"127.0.0.1:{exp} -> {mdir} (loopback port {port})",
                        feature="file_transfer",
                    )
                return {"ok": True, "path": str(mdir)}
            with contextlib.suppress(Exception):
                self.h.events.log(
                    "PHONEFS_KERNEL_MOUNT_FAIL",
                    f"rc={proc.returncode} {err_txt[:200]}",
                    feature="file_transfer",
                )
            return {
                "ok": False,
                "error": err_txt[:200],
                "hint": f"manual: sudo {' '.join(cmd[1:])}",
            }
        except Exception as exc:
            return {"ok": False, "error": repr(exc)[:200]}

    async def fuse_mount_attach(self) -> dict:
        try:
            from .fuse_bridge import PhoneFuseBridge, fuse_available, fuse_missing_hint
        except Exception as exc:
            return {"ok": False, "error": f"fuse bridge import failed: {exc!r}"[:300]}
        if not fuse_available():
            return {"ok": False, "error": "fusepy/libfuse2 not available",
                    "hint": fuse_missing_hint()}
        base = self.mount_dir or self.get_mount_root()
        target = Path(base) / "live"
        if self._fuse_mountpoint_alive(target):
            await self._fuse_unmount(target, lazy=True)
        try:
            await asyncio.to_thread(target.mkdir, parents=True, exist_ok=True)
        except OSError as exc:
            return {"ok": False, "error": f"cannot create {target}: {exc!r}"[:300]}
        getter = lambda: (getattr(self.h, "_pfs_client", None)
                          or getattr(self.h, "phonefs_client", None))
        if getter() is None:
            return {"ok": False, "error": "no live phone NFS client"}
        readonly = bool((getattr(self.h, "user_settings", {}) or {})
                        .get("phone_fuse_readonly", False))
        self.fuse_readonly = readonly
        try:
            loop = asyncio.get_running_loop()
            bridge = PhoneFuseBridge(getter, loop, readonly=readonly)
            self._fuse_bridge = bridge
            bridge.start_async(str(target))
            ok = False
            for _ in range(50):
                await asyncio.sleep(0.1)
                if bridge.last_error:
                    break
                if self._fuse_mountpoint_alive(target):
                    ok = True
                    break
        except Exception as exc:
            return {"ok": False, "error": repr(exc)[:300], "hint": fuse_missing_hint()}
        if ok:
            self.fuse_mounted_at = str(target)
            await asyncio.to_thread(self.register_file_manager_bookmarks, target, self.device_label)
            self._fuse_supervisor_start()
            with contextlib.suppress(Exception):
                self.h.events.log(
                    "PHONEFS_FUSE_MOUNTED",
                    f"sshfs-style FUSE live mount at {target} "
                    f"({'read-only' if readonly else 'read-write'}, no root, supervised)",
                    feature="file_transfer")
            if readonly:
                note = ("Live read-only mount (sshfs-style). Copy files INTO the "
                        "parent mirror folder to upload them to the phone.")
            else:
                note = ("Live read-write mount (sshfs/KDE-Connect-style): drag & drop "
                        "in either direction works; the supervisor re-mounts "
                        "automatically if the phone reconnects.")
            return {"ok": True, "method": "fuse", "path": str(target),
                    "rw": not readonly, "note": note}
        err = (getattr(self._fuse_bridge, "last_error", "") or "mount did not appear")
        self._fuse_bridge = None
        return {"ok": False, "error": err[:300], "hint": fuse_missing_hint()}

    @staticmethod
    def _fuse_mountpoint_alive(target: Path) -> bool:
        want = str(target).replace(" ", "\\040")
        try:
            with open("/proc/mounts", "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    parts = line.split()
                    if len(parts) >= 3 and parts[1] == want and "fuse" in parts[2]:
                        return True
            return False
        except OSError:
            pass
        try:
            return os.path.ismount(str(target))
        except OSError:
            return False

    async def _fuse_stop_bridge(self) -> None:
        bridge = self._fuse_bridge
        if bridge is None:
            return
        self._fuse_bridge = None
        with contextlib.suppress(Exception):
            await asyncio.get_running_loop().run_in_executor(None, bridge.stop_sync)

    def _fuse_supervisor_start(self) -> None:
        if self._fuse_supervisor is not None and not self._fuse_supervisor.done():
            return
        with contextlib.suppress(RuntimeError):
            self._fuse_supervisor = asyncio.get_running_loop().create_task(
                self._fuse_supervisor_loop())

    async def _fuse_supervisor_loop(self) -> None:
        misses = 0
        while True:
            await asyncio.sleep(3.0)
            mp = self.fuse_mounted_at
            if not mp:
                misses = 0
                continue
            bridge = self._fuse_bridge
            thread_ok = bool(bridge is not None and bridge.running)
            mount_ok = self._fuse_mountpoint_alive(Path(mp))
            if thread_ok and mount_ok:
                misses = 0
                continue
            misses += 1
            if misses < 2:
                continue
            misses = 0
            with contextlib.suppress(Exception):
                self.h.events.log(
                    "PHONEFS_FUSE_SUPERVISOR",
                    f"live mount unhealthy (thread={thread_ok} mount={mount_ok}) "
                    f"at {mp} — cleaning up and re-attaching", feature="file_transfer")
            if bridge is not None:
                await self._fuse_stop_bridge()
            await self._fuse_unmount(Path(mp), lazy=True)
            self.fuse_mounted_at = None
            cl_now = (getattr(self.h, "_pfs_client", None)
                      or getattr(self.h, "phonefs_client", None))
            if cl_now is None:
                with contextlib.suppress(Exception):
                    self.h.events.log(
                        "PHONEFS_FUSE_WAIT_PHONE",
                        "phone NFS client gone — live mount will be re-attached "
                        "on reconnect", feature="file_transfer")
                continue
            with contextlib.suppress(Exception):
                await self.fuse_mount_attach()

    async def _fuse_unmount(self, target: Path, lazy: bool = False) -> bool:
        variants = (["-u", "-z"] if lazy else ["-u"])
        attempts = (
            ("fusermount", variants), ("fusermount3", variants),
            ("fusermount", ["-u", "-z"]), ("fusermount3", ["-u", "-z"]),
            ("umount", ["-l"]),
        )
        for tool, args in attempts:
            if not shutil.which(tool):
                continue
            with contextlib.suppress(Exception):
                proc = await asyncio.create_subprocess_exec(
                    tool, *args, str(target),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL)
                await asyncio.wait_for(proc.communicate(), timeout=6.0)
                if not self._fuse_mountpoint_alive(target):
                    return True
        return not self._fuse_mountpoint_alive(target)

    async def sync_directory(
        self,
        remote_path: str = "/",
        max_depth: int = 1,
        download_small_files: bool = False,
        small_file_limit: int = 25 * 1024 * 1024,
    ) -> dict:
        if self.mount_dir is None:
            self.get_mount_root()
        assert self.mount_dir is not None
        self.mount_dir.mkdir(parents=True, exist_ok=True)
        cl = getattr(self.h, "_pfs_client", None) or getattr(self.h, "phonefs_client", None)
        if cl is None:
            return {"ok": False, "error": "Phone NFS client not connected"}

        norm_rem = "/" + (remote_path or "/").strip("/")
        local_base = self.mount_dir if norm_rem == "/" else (self.mount_dir / norm_rem.lstrip("/"))
        local_base.mkdir(parents=True, exist_ok=True)

        count = 0
        try:
            entries = await cl.listdir(norm_rem)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

        for ent in entries:
            name = str(ent.get("name") or "").strip()
            if not name or name in (".", "..", "phone", "live") or "/" in name:
                continue
            etype = ent.get("type", "file")
            esize = int(ent.get("size") or 0)
            emtime = float(ent.get("mtime") or time.time())
            child_rem = (norm_rem.rstrip("/") + "/" + name) if norm_rem != "/" else ("/" + name)
            local_target = local_base / name

            if etype == "dir":
                with contextlib.suppress(OSError):
                    local_target.mkdir(parents=True, exist_ok=True)
                count += 1
                if max_depth > 0:
                    sub_res = await self.sync_directory(
                        child_rem,
                        max_depth=max_depth - 1,
                        download_small_files=download_small_files,
                        small_file_limit=small_file_limit,
                    )
                    count += int(sub_res.get("count", 0))
            elif etype == "file":
                need_dl = False
                if download_small_files and esize <= small_file_limit:
                    if not local_target.exists():
                        need_dl = True
                    else:
                        with contextlib.suppress(OSError):
                            st = local_target.stat()
                            if st.st_size != esize:
                                need_dl = True
                if need_dl:
                    tmp_target = local_base / f".{name}.scpart"
                    try:
                        buf = bytearray()
                        async for chunk in cl.read_iter(child_rem, size=esize):
                            buf.extend(chunk)
                            await asyncio.sleep(0)

                        def _commit_file(tp: Path, lp: Path, data_b: bytes, mt: float):
                            with open(tp, "wb") as fh:
                                fh.write(data_b)
                            os.replace(tp, lp)
                            with contextlib.suppress(OSError):
                                os.utime(lp, (mt, mt))
                            return lp.stat()

                        st_now = await asyncio.to_thread(
                            _commit_file, tmp_target, local_target, bytes(buf), emtime
                        )
                        self._known_mtimes[str(local_target)] = (st_now.st_mtime, st_now.st_size)
                    except Exception:
                        with contextlib.suppress(OSError):
                            tmp_target.unlink(missing_ok=True)
                elif local_target.exists():
                    with contextlib.suppress(OSError):
                        st_now = await asyncio.to_thread(local_target.stat)
                        self._known_mtimes[str(local_target)] = (st_now.st_mtime, st_now.st_size)
                count += 1
                await asyncio.sleep(0)

        self.synced_entries = max(self.synced_entries, count)
        self.last_sync_ts = time.time()
        return {"ok": True, "path": norm_rem, "local_path": str(local_base), "count": count}

    def _scan_writeback_candidates_sync(self) -> list[tuple[Path, str, tuple[float, int]]]:
        out: list[tuple[Path, str, tuple[float, int]]] = []
        mdir = self.mount_dir
        if mdir is None or not mdir.is_dir():
            return out
        for root_str, dirs, files in os.walk(mdir, followlinks=False):
            dirs[:] = [
                d
                for d in dirs
                if not d.startswith(".")
                and d not in ("phone", "live")
                and not os.path.islink(os.path.join(root_str, d))
                and not self._fuse_mountpoint_alive(Path(root_str) / d)
                and not os.path.ismount(os.path.join(root_str, d))
            ]
            root_p = Path(root_str)
            if (self._fuse_mountpoint_alive(root_p) or os.path.ismount(root_p)) and root_p != mdir:
                continue
            for fn in files:
                if fn.startswith(".") or fn.endswith(".scpart"):
                    continue
                fp = root_p / fn
                if fp.is_symlink():
                    continue
                try:
                    st = fp.stat()
                except OSError:
                    continue
                if st.st_size > MAX_WRITEBACK_FILE_BYTES:
                    continue
                sig = (st.st_mtime, st.st_size)
                key = str(fp)
                if self._known_mtimes.get(key) == sig:
                    continue
                rel_p = "/" + str(fp.relative_to(mdir)).replace(os.sep, "/")
                if rel_p.startswith(("/phone/", "/live/")) or rel_p in ("/phone", "/live"):
                    continue
                out.append((fp, rel_p, sig))
        return out

    async def _local_writeback_watcher(self) -> None:
        with contextlib.suppress(asyncio.CancelledError):
            while self.state == "mounted":
                await asyncio.sleep(5.0)
                if self.mount_dir is None or not self.mount_dir.is_dir():
                    continue
                cl = getattr(self.h, "_pfs_client", None) or getattr(self.h, "phonefs_client", None)
                if cl is None:
                    continue
                try:
                    cands = await asyncio.to_thread(self._scan_writeback_candidates_sync)
                    for fp, rel_p, sig in cands:
                        self._known_mtimes[str(fp)] = sig
                        try:
                            data = await asyncio.to_thread(fp.read_bytes)
                            await cl.write_file(rel_p, data)
                            self.h.events.log(
                                "PHONEFS_LINUX_WRITEBACK",
                                f"Synced local file {fp.name} ({sig[1]}B) -> phone:{rel_p}",
                                feature="file_transfer",
                            )
                        except Exception as exc:
                            self.h.events.log(
                                "PHONEFS_LINUX_WRITEBACK_ERR",
                                f"{rel_p}: {exc!r}",
                                feature="file_transfer",
                            )
                except Exception:
                    pass

    def resolve_live_nfs_file_path(self, remote_path: str) -> Path | None:
        rel = (remote_path or "").strip("/")
        if not rel:
            return None
        if self.fuse_mounted_at and self._fuse_mountpoint_alive(Path(self.fuse_mounted_at)):
            return Path(self.fuse_mounted_at) / rel
        if self.kernel_mounted_at and self._fuse_mountpoint_alive(Path(self.kernel_mounted_at)):
            cand = Path(self.kernel_mounted_at) / rel
            if cand.exists():
                return cand
        uid = os.getuid() if hasattr(os, "getuid") else 1000
        gvfs_dir = Path(f"/run/user/{uid}/gvfs")
        if gvfs_dir.is_dir():
            with contextlib.suppress(OSError):
                for mdir in gvfs_dir.glob("nfs:host=127.0.0.1*"):
                    cand = mdir / rel
                    if cand.exists():
                        return cand
        return None

    async def materialize_file_in_mount(self, remote_path: str) -> dict:
        norm_rem = "/" + (remote_path or "").strip("/")
        if norm_rem == "/":
            return {"ok": False, "error": "cannot open root directory as a file"}
        live_p = self.resolve_live_nfs_file_path(norm_rem)
        if live_p is not None:
            return {
                "ok": True,
                "path": norm_rem,
                "local_path": str(live_p),
                "mode": "live-nfs-mount",
                "downloaded": False,
            }
        if self.mount_dir is None:
            self.get_mount_root()
        assert self.mount_dir is not None
        local_target = self.mount_dir / norm_rem.lstrip("/")
        local_target.parent.mkdir(parents=True, exist_ok=True)

        cl = getattr(self.h, "_pfs_client", None) or getattr(self.h, "phonefs_client", None)
        if cl is None:
            if local_target.is_file():
                return {
                    "ok": True,
                    "path": norm_rem,
                    "local_path": str(local_target),
                    "mode": "vfs-cached",
                    "downloaded": False,
                }
            return {"ok": False, "path": norm_rem, "error": "Phone NFS client not connected"}

        esize = None
        emtime = time.time()
        with contextlib.suppress(Exception):
            st_rem = await cl.stat(norm_rem)
            if isinstance(st_rem, dict):
                esize = int(st_rem.get("size") or 0)
                emtime = float(st_rem.get("mtime") or emtime)

        if local_target.is_file() and esize is not None:
            with contextlib.suppress(OSError):
                st_loc = local_target.stat()
                if st_loc.st_size == esize and esize > 0:
                    self._known_mtimes[str(local_target)] = (st_loc.st_mtime, st_loc.st_size)
                    return {
                        "ok": True,
                        "path": norm_rem,
                        "local_path": str(local_target),
                        "mode": "vfs-cached",
                        "downloaded": False,
                    }

        tmp_target = local_target.parent / f".{local_target.name}.scpart"
        try:
            total = 0
            with open(tmp_target, "wb") as fh:
                async for chunk in cl.read_iter(norm_rem, size=esize):
                    fh.write(chunk)
                    total += len(chunk)
                    await asyncio.sleep(0)
            os.replace(tmp_target, local_target)
            with contextlib.suppress(OSError):
                os.utime(local_target, (emtime, emtime))
            st_now = local_target.stat()
            self._known_mtimes[str(local_target)] = (st_now.st_mtime, st_now.st_size)
            self.state = "mounted"
            if self._watch_task is None or self._watch_task.done():
                self._watch_task = asyncio.create_task(self._local_writeback_watcher())
            return {
                "ok": True,
                "path": norm_rem,
                "local_path": str(local_target),
                "bytes": total,
                "mode": "vfs-mount-inplace",
                "downloaded": False,
            }
        except Exception as exc:
            with contextlib.suppress(OSError):
                tmp_target.unlink(missing_ok=True)
            return {"ok": False, "path": norm_rem, "error": str(exc)}

    def open_in_file_manager(self, subpath: str = "") -> dict:
        mroot = self.mount_dir or self.get_mount_root()
        sub = subpath.strip("/")
        if self.fuse_mounted_at and self._fuse_mountpoint_alive(Path(self.fuse_mounted_at)):
            base_live = Path(self.fuse_mounted_at)
            target = (base_live / sub) if sub else base_live
        elif self.kernel_mounted_at and os.path.ismount(self.kernel_mounted_at):
            base_kern = Path(self.kernel_mounted_at)
            target = (base_kern / sub) if sub else base_kern
        else:
            target = (mroot / sub) if sub else mroot
            with contextlib.suppress(OSError):
                target.mkdir(parents=True, exist_ok=True)
        for cmd in (["xdg-open", str(target)], ["gio", "open", str(target)]):
            try:
                subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                return {"ok": True, "opened": str(target), "command": " ".join(cmd)}
            except OSError:
                continue
        return {"ok": False, "path": str(target), "error": "xdg-open / gio not found"}

    async def detach(self) -> None:
        self.state = "unmounted"
        for task in (self._watch_task, self._deep_sync_task, self._fuse_supervisor):
            if task and not task.done():
                task.cancel()
        self._fuse_supervisor = None
        if self._gio_mounted_uri and shutil.which("gio"):
            with contextlib.suppress(Exception):
                proc_g = await asyncio.create_subprocess_exec(
                    "gio", "mount", "-u", self._gio_mounted_uri,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(proc_g.communicate(), timeout=4.0)
            self._gio_mounted_uri = None
        fm = getattr(self, "fuse_mounted_at", None)
        if fm:
            with contextlib.suppress(Exception):
                if self._fuse_bridge is not None:
                    await self._fuse_stop_bridge()
                if not await self._fuse_unmount(Path(fm), lazy=True):
                    self.h.events.log(
                        "PHONEFS_FUSE_UMOUNT_HINT",
                        f"FUSE mount still at {fm} — run: fusermount -u {fm}",
                        feature="file_transfer")
                else:
                    self.fuse_mounted_at = None
            self._fuse_bridge = None
        kern = getattr(self, "kernel_mounted_at", None)
        if kern:
            with contextlib.suppress(Exception):
                proc = await asyncio.create_subprocess_exec(
                    "umount", "-l", str(kern),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(proc.communicate(), timeout=5.0)
                if proc.returncode != 0 and shutil.which("fusermount3"):
                    proc_f = await asyncio.create_subprocess_exec(
                        "fusermount3", "-u", "-z", str(kern),
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    await asyncio.wait_for(proc_f.communicate(), timeout=4.0)
                if proc.returncode != 0 and os.path.ismount(str(kern)) and shutil.which("pkexec"):
                    proc_pk = await asyncio.create_subprocess_exec(
                        "pkexec", "umount", "-l", str(kern),
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    await asyncio.wait_for(proc_pk.communicate(), timeout=15.0)
                if os.path.ismount(str(kern)):
                    self.h.events.log(
                        "PHONEFS_KERNEL_UMOUNT_HINT",
                        f"kernel mount still at {kern} — run: sudo umount -l {kern}",
                        feature="file_transfer",
                    )
                else:
                    self.kernel_mounted_at = None
        await self.loopback.stop()

    def status_dict(self) -> dict:
        return {
            "state": self.state,
            "mount_path": str(self.mount_dir) if self.mount_dir else "",
            "device_label": self.device_label,
            "attach_method": getattr(self, "attach_method", "auto"),
            "fuse_mount_path": str(getattr(self, "fuse_mounted_at", "") or ""),
            "fuse_rw": bool(getattr(self, "fuse_readonly", False) is False
                            and getattr(self, "fuse_mounted_at", None)),
            "fuse_bridge_alive": bool(getattr(self, "_fuse_bridge", None) is not None
                                      and getattr(self._fuse_bridge, "running", False)),
            "loopback_port": self.loopback.port,
            "nfs_uri": f"nfs://127.0.0.1:{self.loopback.port}/sdcard" if self.loopback.port else "",
            "bookmark_registered": self.bookmark_registered,
            "synced_entries": self.synced_entries,
            "rpc_forwarded": self.loopback.rpc_forwarded,
            "loopback_reply_error": str(getattr(self.loopback, "last_local_reply_error", "") or ""),
            "last_sync_ts": self.last_sync_ts,
        }
