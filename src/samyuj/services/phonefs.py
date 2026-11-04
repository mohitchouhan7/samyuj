from __future__ import annotations
import json
import urllib.parse
from samyuj.files.nfs.client import build_phonefs_client_ctx

import asyncio
import contextlib
import shutil
import subprocess
import time
from pathlib import Path

from samyuj.files.nfs import PhoneNfsClient
from samyuj.protocol.nfs.xdr import norm_phone_path
from samyuj.protocol.services import build_phonefs_handshake

class PhoneFsServiceMixin:

    async def _phonefs_on_server_info(self, conn: dict, info: dict) -> None:
        self.events.log("PHONEFS_SERVER_INFO", json.dumps(info), feature="file_transfer")
        info = dict(info)
        info["_peer_ip"] = self._phone_ip(conn)
        self._last_phonefs_info = info
        w = self._pfs_waiter
        if w is not None and not w.done():
            w.set_result(info)
        elif (
            (self._pfs_client is None or self.phonefs.get("state") != "ready")
            and not getattr(self, "_pfs_connecting", False)
        ):
            conn["tasks"].append(asyncio.create_task(self._phonefs_connect_from_info(info)))

    async def _phonefs_connect_from_info(self, info: dict) -> dict:
        if getattr(self, "_pfs_connecting", False):
            return {"ok": True, **self.phonefs}
        self._pfs_connecting = True
        try:
            ip = str(info.get("ip") or "")
            if not ip or ip.startswith("127."):
                ip = info.get("_peer_ip") or ip
            pm_port = int(info.get("port") or 0)
            nfs_port = int(info.get("nfsport") or info.get("nfsDataPort") or pm_port)
            nfs_data_port = int(info.get("nfsDataPort") or nfs_port)
            dt2 = int(info.get("dt2type") or 2)
            cert_b = self.identity.cert_pem if isinstance(self.identity.cert_pem, bytes) else self.identity.cert_pem.encode()
            key_b = getattr(self.identity, "key_pem", None)
            if key_b is None:
                key_b = (Path(self.state_dir) / "identity.key.pem").read_bytes()
            elif isinstance(key_b, str):
                key_b = key_b.encode()
            ctx = build_phonefs_client_ctx(cert_b, key_b)
            cl = PhoneNfsClient(
                ip, pm_port, nfs_port, str(info.get("fp") or ""), ctx,
                mount_path=str(info.get("path") or "/sdcard"),
                dt2type=dt2, nfs_data_port=nfs_data_port,
            )
            await cl.connect()
            if self._pfs_client is not None:
                with contextlib.suppress(Exception):
                    await self._pfs_client.close()
            self._pfs_client = cl
            self.phonefs.update(
                state="ready", error="", ip=ip, path=cl.mount_path,
                ports={"portmap": pm_port, "mountd": cl.mount_port, "nfsd": nfs_port},
            )
            self.events.log("PHONEFS_MOUNTED", f"Mounted phone {cl.mount_path} at {ip} ports={self.phonefs['ports']}", feature="file_transfer")
            with contextlib.suppress(Exception):
                st_cap = await cl.fsstat()
                if st_cap.get("total_bytes"):
                    self.phone_storage.update(
                        free_bytes=int(st_cap.get("avail_bytes") or st_cap.get("free_bytes") or 0),
                        total_bytes=int(st_cap["total_bytes"]),
                    )
            self._invalidate_phonefs_dir_cache()
            if self.user_settings.get("auto_mount_phone_nfs", True):
                asyncio.create_task(self.linux_mount_mgr.attach(self._best_device_name(), sync_files=False))
            return {"ok": True, **self.phonefs}
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            self.phonefs.update(state="error", error=err)
            return {"ok": False, "error": err}
        finally:
            self._pfs_connecting = False

    async def phonefs_connect(self, timeout: float = 15.0) -> dict:
        c = self._live_ws()
        if not c:
            return {"ok": False, "error": "no live phone connection"}
        async with self._pfs_conn_lock:
            if self._pfs_client is not None and self.phonefs.get("state") == "ready":
                try:
                    await self._pfs_client.stat("/")
                    return {"ok": True, **self.phonefs}
                except Exception:
                    with contextlib.suppress(Exception):
                        await self._pfs_client.close()
                    self._pfs_client = None
            if self._last_phonefs_info is not None:
                cached_res = await self._phonefs_connect_from_info(self._last_phonefs_info)
                if cached_res.get("ok"):
                    return cached_res
            loop = asyncio.get_running_loop()
            self._pfs_waiter = loop.create_future()
            info = None
            deadline = loop.time() + timeout
            try:
                while info is None and loop.time() < deadline:
                    msg, _ = build_phonefs_handshake(self)
                    await self._send_ws_text(c, msg, "PHONEFS_HANDSHAKE_SENT(svc168 type0)")
                    with contextlib.suppress(asyncio.TimeoutError):
                        info = await asyncio.wait_for(asyncio.shield(self._pfs_waiter), min(4.0, max(0.1, deadline - loop.time())))
            finally:
                self._pfs_waiter = None
            if info is None:
                err = "Phone did not answer svc-168 type:0 handshake within timeout"
                self.phonefs.update(state="error", error=err)
                return {"ok": False, "error": err}
            return await self._phonefs_connect_from_info(info)

    def get_cached_phonefs_ls(self, path: str = "/") -> dict | None:
        try:
            p = norm_phone_path(path)
        except Exception:
            p = "/"
        cache = getattr(self, "_phonefs_dir_cache", None)
        if isinstance(cache, dict) and p in cache:
            return {"ok": True, "path": p, "entries": list(cache[p]), "cached": True}
        return None

    def _invalidate_phonefs_dir_cache(self, path: str | None = None) -> None:
        cache = getattr(self, "_phonefs_dir_cache", None)
        if not isinstance(cache, dict):
            return
        if not path:
            cache.clear()
            return
        try:
            p = norm_phone_path(path)
            cache.pop(p, None)
            parent = "/" + p.strip("/").rpartition("/")[0] if "/" in p.strip("/") else "/"
            cache.pop(parent, None)
        except Exception:
            cache.clear()

    async def phonefs_ls(self, path: str = "/") -> dict:
        try:
            p = norm_phone_path(path)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        if self._pfs_client is None or self.phonefs.get("state") != "ready":
            conn_res = await self.phonefs_connect(timeout=8.0)
            if not conn_res.get("ok"):
                cached = self.get_cached_phonefs_ls(p)
                if cached is not None:
                    return cached
                return {**conn_res, "path": p}
        try:
            entries = await self._pfs_client.listdir(p)
            if not hasattr(self, "_phonefs_dir_cache") or not isinstance(self._phonefs_dir_cache, dict):
                self._phonefs_dir_cache = {}
            self._phonefs_dir_cache[p] = list(entries)
            return {"ok": True, "path": p, "entries": entries}
        except Exception as exc:
            err_s = str(exc)
            if "status 2" in err_s and self._pfs_client is not None:
                alt_map = {
                    "/Downloads": "/Download",
                    "/Download": "/Downloads",
                    "/Document": "/Documents",
                    "/Documents": "/Document",
                }
                alt_p = alt_map.get(p)
                if alt_p:
                    with contextlib.suppress(Exception):
                        entries_alt = await self._pfs_client.listdir(alt_p)
                        if not hasattr(self, "_phonefs_dir_cache") or not isinstance(self._phonefs_dir_cache, dict):
                            self._phonefs_dir_cache = {}
                        self._phonefs_dir_cache[alt_p] = list(entries_alt)
                        return {"ok": True, "path": alt_p, "entries": entries_alt}
                std_folders = {
                    "/DCIM", "/DCIM/Camera", "/Pictures", "/Pictures/Screenshots",
                    "/Download", "/Documents", "/Music", "/Movies", "/Recordings",
                }
                if p in std_folders:
                    with contextlib.suppress(Exception):
                        await self._pfs_client.mkdir(p)
                        entries_new = await self._pfs_client.listdir(p)
                        if not hasattr(self, "_phonefs_dir_cache") or not isinstance(self._phonefs_dir_cache, dict):
                            self._phonefs_dir_cache = {}
                        self._phonefs_dir_cache[p] = list(entries_new)
                        return {"ok": True, "path": p, "entries": entries_new}
                    return {"ok": True, "path": p, "entries": []}
            cached = self.get_cached_phonefs_ls(p)
            if cached is not None:
                return {**cached, "stale_warning": err_s}
            return {"ok": False, "path": p, "error": err_s}

    async def phonefs_download(self, remote_path: str) -> dict:
        if self._pfs_client is None or self.phonefs.get("state") != "ready":
            conn_res = await self.phonefs_connect(timeout=8.0)
            if not conn_res.get("ok"):
                return conn_res
        try:
            p = norm_phone_path(remote_path)
            dl_dir = self.get_incoming_files_dir()
            dl_dir.mkdir(parents=True, exist_ok=True)
            dest = dl_dir / Path(p).name
            total = 0
            with open(dest, "wb") as f:
                async for chunk in self._pfs_client.read_iter(p):
                    f.write(chunk)
                    total += len(chunk)
            self.events.log("PHONEFS_DOWNLOADED", f"{p} -> {dest} ({total}B)", feature="file_transfer")
            return {"ok": True, "path": p, "saved": str(dest), "bytes": total,
                    "detail": f"Downloaded {p} ({total}B) -> {dest}"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def phonefs_open(self, remote_path: str, launch_app: bool = True) -> dict:
        try:
            norm_p = norm_phone_path(remote_path)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

        if getattr(self, "phone_file_widgets", None) is not None and self.phone_file_widgets.mock_fs_tree is not None:
            mroot = self.linux_mount_mgr.mount_dir or self.linux_mount_mgr.get_mount_root()
            target_f = Path(mroot) / norm_p.lstrip("/")
            target_f.parent.mkdir(parents=True, exist_ok=True)
            if not target_f.is_file():
                target_f.write_bytes(b"MOCK_NFS_OPEN:" + norm_p.encode("utf-8"))
            return {
                "ok": True,
                "path": norm_p,
                "opened": str(target_f),
                "mode": "vfs-mount-inplace",
                "downloaded": False,
                "detail": f"Opened {norm_p} in-place at {target_f} (not downloaded to ~/Downloads)",
            }

        if self._pfs_client is None or self.phonefs.get("state") != "ready":
            conn_res = await self.phonefs_connect(timeout=8.0)
            if not conn_res.get("ok"):
                return conn_res

        def _launch_desktop_opener(target_uri_or_path: str, prefer_player: bool = False) -> str:
            if not launch_app:
                return "launch_skipped"
            if prefer_player:
                for player in ("mpv", "vlc", "celluloid", "totem", "ffplay"):
                    pbin = shutil.which(player)
                    if pbin:
                        with contextlib.suppress(OSError):
                            subprocess.Popen(
                                [pbin, target_uri_or_path],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                start_new_session=True,
                            )
                            return player
            for cmd in (["xdg-open", target_uri_or_path], ["gio", "open", target_uri_or_path]):
                if shutil.which(cmd[0]):
                    with contextlib.suppress(OSError):
                        subprocess.Popen(
                            cmd,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            start_new_session=True,
                        )
                        return cmd[0]
            return "none"

        live_p = self.linux_mount_mgr.resolve_live_nfs_file_path(norm_p)
        if live_p is not None:
            launcher = _launch_desktop_opener(str(live_p))
            self.events.log("PHONEFS_OPENED_LIVE_MOUNT", f"{norm_p} -> {live_p} via {launcher}", feature="file_transfer")
            return {
                "ok": True,
                "path": norm_p,
                "opened": str(live_p),
                "launcher": launcher,
                "mode": "live-nfs-mount",
                "downloaded": False,
                "detail": f"Opened {Path(norm_p).name} directly from NFS mount ({live_p})",
            }

        ext = Path(norm_p).suffix.lower()
        stream_exts = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".3gp", ".mp3", ".flac", ".wav", ".ogg", ".m4a", ".aac", ".opus"}
        gui_srv = getattr(self, "_gui", None)
        has_player = any(shutil.which(p) for p in ("mpv", "vlc", "celluloid", "totem", "ffplay"))
        if ext in stream_exts and gui_srv is not None and getattr(gui_srv, "port", 0) > 0 and has_player:
            q_path = urllib.parse.quote(norm_p, safe="")
            q_name = urllib.parse.quote(Path(norm_p).name)
            tok_q = f"&t={urllib.parse.quote(gui_srv.token)}" if getattr(gui_srv, "require_token", False) else ""
            stream_url = f"http://127.0.0.1:{gui_srv.port}/api/phonefs/stream/{q_name}?path={q_path}{tok_q}"
            launcher = _launch_desktop_opener(stream_url, prefer_player=True)
            self.events.log("PHONEFS_OPENED_STREAM", f"{norm_p} -> {stream_url} via {launcher}", feature="file_transfer")
            return {
                "ok": True,
                "path": norm_p,
                "opened": stream_url,
                "launcher": launcher,
                "mode": "http-range-stream",
                "downloaded": False,
                "detail": f"Streaming {Path(norm_p).name} directly over NFSv3 ({launcher})",
            }

        mat_res = await self.linux_mount_mgr.materialize_file_in_mount(norm_p)
        if not mat_res.get("ok"):
            return mat_res
        local_p = str(mat_res["local_path"])
        launcher = _launch_desktop_opener(local_p)
        self.events.log("PHONEFS_OPENED_INPLACE", f"{norm_p} -> {local_p} via {launcher}", feature="file_transfer")
        return {
            "ok": True,
            "path": norm_p,
            "opened": local_p,
            "launcher": launcher,
            "mode": str(mat_res.get("mode") or "vfs-mount-inplace"),
            "downloaded": False,
            "detail": f"Opened {Path(norm_p).name} in-place from Phone NFS mount ({local_p})",
        }

    async def phonefs_delete(self, remote_path: str) -> dict:
        try:
            p = norm_phone_path(remote_path)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        if getattr(self, "phone_file_widgets", None) is not None and self.phone_file_widgets.mock_fs_tree is not None:
            parent = "/" + p.strip("/").rpartition("/")[0] if "/" in p.strip("/") else "/"
            fname = Path(p).name
            items = self.phone_file_widgets.mock_fs_tree.get(parent)
            if isinstance(items, list):
                before = len(items)
                self.phone_file_widgets.mock_fs_tree[parent] = [
                    x for x in items if str(x.get("name") or "") != fname
                ]
                if len(self.phone_file_widgets.mock_fs_tree[parent]) < before:
                    self.gallery_state["cached_thumbnails"].pop(p, None)
                    self.gallery_state["entries"] = [
                        e for e in (self.gallery_state.get("entries") or [])
                        if str(e.get("remote_path") or e.get("path")) != p
                    ]
                    self.gallery_state["entries_count"] = len(self.gallery_state["entries"])
                    return {"ok": True, "path": p, "detail": f"Deleted {p} from phone"}
            return {"ok": False, "error": f"{p} not found"}
        if self._pfs_client is None or self.phonefs.get("state") != "ready":
            conn_res = await self.phonefs_connect(timeout=8.0)
            if not conn_res.get("ok"):
                return conn_res
        try:
            res = await self._pfs_client.remove(p)
            cfile = self.gallery_state["cached_thumbnails"].pop(p, None)
            if cfile:
                with contextlib.suppress(OSError):
                    Path(cfile).unlink(missing_ok=True)
            self.gallery_state["entries"] = [
                e for e in (self.gallery_state.get("entries") or [])
                if str(e.get("remote_path") or e.get("path")) != p
            ]
            self.gallery_state["entries_count"] = len(self.gallery_state["entries"])
            self._invalidate_phonefs_dir_cache(p)
            self.events.log("PHONEFS_DELETED", f"{p}", feature="file_transfer")
            return {"ok": True, "path": p, "detail": f"Deleted {p} from phone", **res}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def phonefs_rename(self, remote_path: str, new_name_or_path: str) -> dict:
        try:
            src_p = norm_phone_path(remote_path)
            raw_new = (new_name_or_path or "").strip()
            if not raw_new:
                return {"ok": False, "error": "new name cannot be empty"}
            if "/" in raw_new:
                dst_p = norm_phone_path(raw_new)
            else:
                parent = "/" + src_p.strip("/").rpartition("/")[0] if "/" in src_p.strip("/") else "/"
                dst_p = norm_phone_path((parent.rstrip("/") + "/" + raw_new) if parent != "/" else ("/" + raw_new))
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        if getattr(self, "phone_file_widgets", None) is not None and self.phone_file_widgets.mock_fs_tree is not None:
            parent = "/" + src_p.strip("/").rpartition("/")[0] if "/" in src_p.strip("/") else "/"
            old_name = Path(src_p).name
            new_name = Path(dst_p).name
            items = self.phone_file_widgets.mock_fs_tree.get(parent)
            if isinstance(items, list):
                for x in items:
                    if str(x.get("name") or "") == old_name:
                        x["name"] = new_name
                        return {"ok": True, "src": src_p, "dst": dst_p, "detail": f"Renamed {src_p} -> {dst_p}"}
            return {"ok": False, "error": f"{src_p} not found"}
        if self._pfs_client is None or self.phonefs.get("state") != "ready":
            conn_res = await self.phonefs_connect(timeout=8.0)
            if not conn_res.get("ok"):
                return conn_res
        try:
            res = await self._pfs_client.rename(src_p, dst_p)
            old_thumb = self.gallery_state["cached_thumbnails"].pop(src_p, None)
            if old_thumb:
                self.gallery_state["cached_thumbnails"][dst_p] = old_thumb
            self._invalidate_phonefs_dir_cache(src_p)
            self._invalidate_phonefs_dir_cache(dst_p)
            self.events.log("PHONEFS_RENAMED", f"{src_p} -> {dst_p}", feature="file_transfer")
            return {"ok": True, "src": src_p, "dst": dst_p, "detail": f"Renamed {src_p} -> {dst_p}", **res}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def phonefs_mkdir(self, remote_path: str) -> dict:
        try:
            p = norm_phone_path(remote_path)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        if getattr(self, "phone_file_widgets", None) is not None and self.phone_file_widgets.mock_fs_tree is not None:
            parent = "/" + p.strip("/").rpartition("/")[0] if "/" in p.strip("/") else "/"
            dname = Path(p).name
            self.phone_file_widgets.mock_fs_tree.setdefault(parent, []).append({
                "name": dname, "is_dir": True, "type": "dir", "size": 0, "mtime": time.time(),
            })
            self.phone_file_widgets.mock_fs_tree.setdefault(p, [])
            return {"ok": True, "path": p, "detail": f"Created folder {p}"}
        if self._pfs_client is None or self.phonefs.get("state") != "ready":
            conn_res = await self.phonefs_connect(timeout=8.0)
            if not conn_res.get("ok"):
                return conn_res
        try:
            res = await self._pfs_client.mkdir(p)
            self._invalidate_phonefs_dir_cache(p)
            self.events.log("PHONEFS_MKDIR", f"{p}", feature="file_transfer")
            return {"ok": True, "path": p, "detail": f"Created folder {p}", **res}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def phonefs_upload(self, local_path: str, remote_dir: str = "/Download") -> dict:
        lp = Path(str(local_path or "")).expanduser()
        if not lp.is_file():
            return {"ok": False, "error": f"local file not found: {local_path}"}
        try:
            rdir = norm_phone_path(remote_dir or "/Download")
            dest_p = norm_phone_path((rdir.rstrip("/") + "/" + lp.name) if rdir != "/" else ("/" + lp.name))
            raw_bytes = await asyncio.to_thread(lp.read_bytes)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        if getattr(self, "phone_file_widgets", None) is not None and self.phone_file_widgets.mock_fs_tree is not None:
            self.phone_file_widgets.mock_fs_tree.setdefault(rdir, []).append({
                "name": lp.name, "is_dir": False, "type": "file",
                "size": len(raw_bytes), "mtime": time.time(),
            })
            return {"ok": True, "path": dest_p, "bytes": len(raw_bytes), "detail": f"Uploaded {lp.name} -> {dest_p}"}
        if self._pfs_client is None or self.phonefs.get("state") != "ready":
            conn_res = await self.phonefs_connect(timeout=8.0)
            if not conn_res.get("ok"):
                return conn_res
        try:
            res = await self._pfs_client.write_file(dest_p, raw_bytes)
            self._invalidate_phonefs_dir_cache(dest_p)
            self.events.log("PHONEFS_UPLOADED", f"{lp} -> {dest_p} ({len(raw_bytes)}B)", feature="file_transfer")
            return {"ok": True, "path": dest_p, "bytes": len(raw_bytes), "detail": f"Uploaded {lp.name} -> {dest_p}", **res}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def stage_phone_file_for_drag(self, remote_path: str) -> dict:
        try:
            norm_p = norm_phone_path(remote_path)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        dl_dir = self.get_incoming_files_dir()
        with contextlib.suppress(OSError):
            dl_dir.mkdir(parents=True, exist_ok=True)
        dest = dl_dir / Path(norm_p).name
        if dest.is_file() and dest.stat().st_size > 0:
            return {
                "ok": True,
                "cached": True,
                "path": norm_p,
                "saved": str(dest),
                "uri": dest.resolve().as_uri(),
            }
        if getattr(self, "linux_mount_mgr", None) is not None and getattr(self.linux_mount_mgr, "mount_dir", None):
            mirror_cand = Path(self.linux_mount_mgr.mount_dir) / norm_p.lstrip("/")
            if mirror_cand.is_file() and mirror_cand.stat().st_size > 0:
                return {
                    "ok": True,
                    "cached": True,
                    "path": norm_p,
                    "saved": str(mirror_cand),
                    "uri": mirror_cand.resolve().as_uri(),
                }
        if self._pfs_client is not None and self.phonefs.get("state") == "ready":
            dl_res = await self.phonefs_download(norm_p)
            if dl_res.get("ok") and dl_res.get("saved"):
                saved_p = Path(str(dl_res["saved"]))
                if saved_p.is_file():
                    return {
                        "ok": True,
                        "cached": False,
                        "path": norm_p,
                        "saved": str(saved_p),
                        "uri": saved_p.resolve().as_uri(),
                    }
        thumb_f = (self.gallery_state.get("cached_thumbnails") or {}).get(norm_p)
        if thumb_f and Path(thumb_f).is_file():
            with contextlib.suppress(OSError):
                shutil.copyfile(thumb_f, dest)
                return {
                    "ok": True,
                    "cached": True,
                    "from_thumbnail": True,
                    "path": norm_p,
                    "saved": str(dest),
                    "uri": dest.resolve().as_uri(),
                }
        if getattr(self, "phone_file_widgets", None) is not None and self.phone_file_widgets.mock_fs_tree is not None:
            with contextlib.suppress(OSError):
                dest.write_bytes(b"SAMYUJ_STAGED_FILE:" + norm_p.encode("utf-8"))
                return {
                    "ok": True,
                    "cached": False,
                    "mock": True,
                    "path": norm_p,
                    "saved": str(dest),
                    "uri": dest.resolve().as_uri(),
                }
        return {"ok": False, "path": norm_p, "error": "phone storage not connected"}

