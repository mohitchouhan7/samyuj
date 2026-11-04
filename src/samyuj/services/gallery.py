from __future__ import annotations
from samyuj.protocol.envelope import _env
from samyuj.protocol.nfs.xdr import XdrReader as _Xdr, xopaque as _xopaque, xu32 as _xu32

import asyncio
import contextlib
import hashlib
import json
import os
from pathlib import Path

from samyuj.protocol.constants import FEATURES_LAUNCHABLE_SIDS
from samyuj.protocol.services import (
    build_features_streaming_list_reply,
    build_gallery_frame,
)

class GalleryFeaturesServiceMixin:

    async def refresh_gallery_widget(self, remote_path: str = "/DCIM/Camera") -> dict:
        from samyuj.files.phone_browser import (
            PhoneFileWidgetConfig,
            classify_file_kind,
            extract_timestamp_from_filename,
            sanitize_remote_phone_path,
        )
        if (
            self.phone_file_widgets.mock_fs_tree is None
            and (getattr(self, "_pfs_client", None) is None or self.phonefs.get("state") != "ready")
        ):
            c_live = self._live_ws()
            if c_live is not None and c_live.get("writer") is not None and self.device_name != "SELFTEST-PC":
                with contextlib.suppress(Exception):
                    await self.phonefs_connect(timeout=8.0)

        raw_target = (remote_path or "/DCIM/Camera").strip()
        is_all_photos = raw_target.lower() in ("__all_photos__", "all", "/__all_photos__")

        def _norm_media_entry(ent: dict) -> dict:
            ename = str(ent.get("name") or "")
            rpath = sanitize_remote_phone_path(str(ent.get("remote_path") or ent.get("path") or ("/" + ename)))
            is_dir = bool(ent.get("is_dir") or ent.get("type") == "dir")
            fkind = str(ent.get("file_kind") or classify_file_kind(ename, is_dir))
            mt = float(ent.get("mtime") or 0.0)
            if mt > 1e12:
                mt = mt / 1000.0
            if mt <= 0.0 and not is_dir:
                mt = extract_timestamp_from_filename(ename)
            return {
                **ent,
                "name": ename,
                "path": rpath,
                "remote_path": rpath,
                "is_dir": is_dir,
                "type": "dir" if is_dir else "file",
                "file_kind": fkind,
                "mtime": mt,
                "size": int(ent.get("size") or 0),
                "extension": Path(ename).suffix.lower() if not is_dir else "",
                "thumbnail_hint": rpath if fkind in ("image", "video") else "",
            }

        if is_all_photos:
            seen_paths: dict[str, dict] = {}
            any_ready = False
            last_msg = "Phone storage is not connected right now."
            last_hint = "Connect your phone over USB or Wi-Fi and grant All files access."

            if self.phone_file_widgets.mock_fs_tree is not None:
                any_ready = True
                for folder_k in list(self.phone_file_widgets.mock_fs_tree.keys()):
                    cfg_m = PhoneFileWidgetConfig(
                        widget_id="gallery_svc205",
                        source_id="gallery",
                        custom_title="All Photos",
                        remote_path=folder_k,
                        view_mode="grid",
                        sort_by="date",
                        sort_desc=True,
                        size_mode="expanded",
                    )
                    res_m = await self.phone_file_widgets.fetch_widget_state(cfg_m)
                    if res_m.status == "ready":
                        for e in res_m.entries:
                            ne = _norm_media_entry(e)
                            if not ne["is_dir"] and ne["file_kind"] in ("image", "video"):
                                seen_paths[ne["remote_path"]] = ne
            else:
                pfs = getattr(self, "_pfs_client", None)
                if pfs is not None and self.phonefs.get("state") == "ready":
                    any_ready = True
                    with contextlib.suppress(Exception):
                        cat_items = await pfs.read_category_dir(category=7, offset=0, limit=500)
                        for ce in cat_items:
                            ne = _norm_media_entry(ce)
                            if not ne["is_dir"] and ne["file_kind"] in ("image", "video") and not ne["name"].startswith("."):
                                seen_paths[ne["remote_path"]] = ne
                    scan_roots = [] if seen_paths else ["/DCIM/Camera", "/Pictures/Screenshots", "/Pictures", "/DCIM"]
                    subfolders_to_scan: list[str] = []
                    for sroot in scan_roots:
                        cfg_s = PhoneFileWidgetConfig(
                            widget_id="gallery_svc205",
                            source_id="gallery",
                            custom_title="All Photos",
                            remote_path=sroot,
                            view_mode="grid",
                            sort_by="date",
                            sort_desc=True,
                            size_mode="expanded",
                        )
                        res_s = await self.phone_file_widgets.fetch_widget_state(cfg_s)
                        if res_s.status == "ready":
                            for e in res_s.entries:
                                ne = _norm_media_entry(e)
                                if ne["name"].startswith("."):
                                    continue
                                if ne["is_dir"]:
                                    if ne["remote_path"] not in scan_roots and ne["remote_path"] not in subfolders_to_scan:
                                        subfolders_to_scan.append(ne["remote_path"])
                                elif ne["file_kind"] in ("image", "video"):
                                    seen_paths[ne["remote_path"]] = ne
                    for sub_p in subfolders_to_scan[:10]:
                        cfg_sub = PhoneFileWidgetConfig(
                            widget_id="gallery_svc205",
                            source_id="gallery",
                            custom_title="All Photos",
                            remote_path=sub_p,
                            view_mode="grid",
                            sort_by="date",
                            sort_desc=True,
                            size_mode="expanded",
                        )
                        res_sub = await self.phone_file_widgets.fetch_widget_state(cfg_sub)
                        if res_sub.status == "ready":
                            for e in res_sub.entries:
                                ne = _norm_media_entry(e)
                                if not ne["is_dir"] and not ne["name"].startswith(".") and ne["file_kind"] in ("image", "video"):
                                    seen_paths[ne["remote_path"]] = ne

            all_sorted = sorted(
                seen_paths.values(),
                key=lambda x: (float(x.get("mtime") or 0.0), str(x.get("name") or "").lower()),
                reverse=True,
            )
            st_str = "ready" if any_ready else "unavailable"
            u_msg = f"{len(all_sorted)} photo(s) & video(s) across all folders (recent first)" if any_ready else last_msg
            self.gallery_state.update({
                "active": True,
                "mounted": any_ready,
                "remote_path": "__all_photos__",
                "status": st_str,
                "entries_count": len(all_sorted),
                "entries": all_sorted,
                "user_message": u_msg,
                "next_step_hint": "" if any_ready else last_hint,
            })
            self.events.log(
                "GALLERY_REFRESH",
                f"path=__all_photos__ status={st_str} entries={len(all_sorted)}",
                feature="gallery",
            )
            return {
                "ok": any_ready,
                "status": st_str,
                "remote_path": "__all_photos__",
                "entries": all_sorted,
                "user_message": u_msg,
                "next_step_hint": "" if any_ready else last_hint,
                "error": "" if any_ready else last_msg,
            }

        target_path = raw_target or "/DCIM/Camera"
        cfg = PhoneFileWidgetConfig(
            widget_id="gallery_svc205",
            source_id="gallery",
            custom_title="Gallery",
            remote_path=target_path,
            view_mode="grid",
            sort_by="date",
            sort_desc=True,
            size_mode="expanded",
        )
        self.gallery_widget_cfg = cfg
        res = await self.phone_file_widgets.fetch_widget_state(cfg)
        if (
            self.phone_file_widgets.mock_fs_tree is None
            and getattr(self, "_pfs_client", None) is not None
            and target_path == "/DCIM/Camera"
            and (res.status != "ready" or not res.entries)
        ):
            for alt_path in ("/DCIM", "/Pictures", "/Pictures/Screenshots"):
                alt_cfg = PhoneFileWidgetConfig(
                    widget_id="gallery_svc205",
                    source_id="gallery",
                    custom_title="Gallery",
                    remote_path=alt_path,
                    view_mode="grid",
                    sort_by="date",
                    sort_desc=True,
                    size_mode="expanded",
                )
                alt_res = await self.phone_file_widgets.fetch_widget_state(alt_cfg)
                if alt_res.status == "ready" and alt_res.entries:
                    res = alt_res
                    self.gallery_widget_cfg = alt_cfg
                    break

        entries_out = [_norm_media_entry(e) for e in (res.entries or []) if not str(e.get("name") or "").startswith(".")]
        if res.status == "ready" and res.remote_path in ("/DCIM", "/Pictures"):
            sub_dirs = [e["remote_path"] for e in entries_out if e.get("is_dir") and not e["name"].startswith(".")]
            seen_sec: dict[str, dict] = {
                e["remote_path"]: e for e in entries_out if not e.get("is_dir")
            }
            dir_entries = [e for e in entries_out if e.get("is_dir")]
            for sd in sub_dirs[:8]:
                sub_cfg = PhoneFileWidgetConfig(
                    widget_id="gallery_svc205",
                    source_id="gallery",
                    custom_title="Gallery",
                    remote_path=sd,
                    view_mode="grid",
                    sort_by="date",
                    sort_desc=True,
                    size_mode="expanded",
                )
                sub_res = await self.phone_file_widgets.fetch_widget_state(sub_cfg)
                if sub_res.status == "ready":
                    for se in sub_res.entries:
                        nse = _norm_media_entry(se)
                        if not nse["is_dir"] and not nse["name"].startswith(".") and nse["file_kind"] in ("image", "video"):
                            seen_sec[nse["remote_path"]] = nse
            media_sorted = sorted(
                seen_sec.values(),
                key=lambda x: (float(x.get("mtime") or 0.0), str(x.get("name") or "").lower()),
                reverse=True,
            )
            entries_out = media_sorted + dir_entries
        else:
            media_only = [e for e in entries_out if not e.get("is_dir")]
            dirs_only = [e for e in entries_out if e.get("is_dir")]
            media_only.sort(key=lambda x: (float(x.get("mtime") or 0.0), str(x.get("name") or "").lower()), reverse=True)
            dirs_only.sort(key=lambda x: (float(x.get("mtime") or 0.0), str(x.get("name") or "").lower()), reverse=True)
            entries_out = media_only + dirs_only

        self.gallery_state.update({
            "active": True,
            "mounted": res.status == "ready",
            "remote_path": res.remote_path,
            "status": res.status,
            "entries_count": len(entries_out),
            "entries": entries_out,
            "user_message": res.user_message,
            "next_step_hint": res.next_step_hint,
        })
        self.events.log(
            "GALLERY_REFRESH",
            f"path={res.remote_path} status={res.status} entries={len(entries_out)}",
            feature="gallery",
        )
        return {
            "ok": res.status == "ready",
            "status": res.status,
            "remote_path": res.remote_path,
            "entries": entries_out,
            "user_message": res.user_message,
            "next_step_hint": res.next_step_hint,
            "error": res.user_message if res.status != "ready" else "",
        }

    @staticmethod
    def _downscale_image_bytes_to_jpeg(raw_bytes: bytes, max_w: int = 160, max_h: int = 120, quality: int = 75) -> bytes:
        if not raw_bytes:
            return b""
        with contextlib.suppress(Exception):
            import gi
            gi.require_version("GdkPixbuf", "2.0")
            from gi.repository import GdkPixbuf
            loader = GdkPixbuf.PixbufLoader()
            loader.set_size(max(32, int(max_w)), max(32, int(max_h)))
            loader.write(raw_bytes)
            loader.close()
            pb = loader.get_pixbuf()
            if pb is not None:
                ok_s, buf = pb.save_to_bufferv("jpeg", ["quality"], [str(max(30, min(95, int(quality))))])
                if ok_s and buf:
                    return bytes(buf)
        return b""

    async def fetch_gallery_thumbnail(self, remote_path: str, quality: int = 75) -> dict:
        from samyuj.files.phone_browser import sanitize_remote_phone_path
        norm_p = sanitize_remote_phone_path(remote_path or "/DCIM/Camera/IMG_0001.jpg")
        q = max(10, min(95, int(quality or 75)))
        cache_key = hashlib.sha256(f"{norm_p}|q={q}".encode("utf-8")).hexdigest()[:32]
        cache_dir = Path(
            os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
        ) / "samyuj" / "thumbnails"
        with contextlib.suppress(OSError):
            cache_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(cache_dir, 0o700)
        self.gallery_thumb_cache_dir = cache_dir
        cache_file = cache_dir / f"{cache_key}.jpg"

        if cache_file.is_file() and cache_file.stat().st_size > 0:
            sz = cache_file.stat().st_size
            self.gallery_state["cached_thumbnails"][norm_p] = str(cache_file)
            return {
                "ok": True,
                "cached": True,
                "path": norm_p,
                "cache_file": str(cache_file),
                "bytes": int(sz),
            }

        thumb_bytes = b""
        pfs = getattr(self, "_pfs_client", None)
        if pfs is not None:
            for cand_p in (norm_p, f"/sdcard{norm_p}" if not norm_p.startswith("/sdcard/") else norm_p):
                with contextlib.suppress(Exception):
                    async with pfs._lock:
                        x = await pfs._nfs(22, _xu32(q) + _xopaque(cand_p.encode("utf-8")))
                        st = x.u32()
                        if st == 0:
                            thumb_bytes = x.opaque()
                if thumb_bytes:
                    break
            ext_low = Path(norm_p).suffix.lower()
            if (
                not thumb_bytes
                and hasattr(pfs, "read_iter")
                and ext_low in (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif")
            ):
                with contextlib.suppress(Exception):
                    chunks = []
                    total_b = 0
                    async for ch in pfs.read_iter(norm_p):
                        chunks.append(ch)
                        total_b += len(ch)
                        if total_b >= 3 * 1024 * 1024:
                            break
                    if chunks:
                        raw_img = b"".join(chunks)
                        thumb_bytes = await asyncio.to_thread(
                            self._downscale_image_bytes_to_jpeg, raw_img, 160, 120, q
                        )

        if not thumb_bytes and getattr(self, "_nfs_srv", None) is not None:
            with contextlib.suppress(Exception):
                call = {
                    "xid": 1,
                    "args": _xu32(q) + _xopaque(norm_p.encode("utf-8")),
                }
                rep = self._nfs_srv._do_cnfs(call, 22, ("127.0.0.1", 0))
                if len(rep) > 32:
                    rx = _Xdr(rep[24:])
                    if rx.u32() == 0:
                        thumb_bytes = rx.opaque()

        if not thumb_bytes:
            return {
                "ok": False,
                "cached": False,
                "path": norm_p,
                "error": "thumbnail unavailable (phone storage not mounted and path not in local export)",
            }

        with contextlib.suppress(OSError):
            cache_file.write_bytes(thumb_bytes)
            os.chmod(cache_file, 0o600)
        self.gallery_state["cached_thumbnails"][norm_p] = str(cache_file)
        self.events.log(
            "GALLERY_THUMBNAIL_CACHED",
            f"path={norm_p} bytes={len(thumb_bytes)} file={cache_file.name}",
            feature="gallery",
        )
        return {
            "ok": True,
            "cached": False,
            "path": norm_p,
            "cache_file": str(cache_file),
            "bytes": len(thumb_bytes),
        }

    async def _handle_gallery(self, conn: dict, frame: dict, data, cmd: int | None, tag: str) -> None:
        d = data if isinstance(data, dict) else {}
        if cmd is None:
            cmd = d.get("cmd", 1)
        self.gallery_state["last_cmd"] = cmd
        self.gallery_state["active"] = True
        rpath = str(d.get("path") or self.gallery_state.get("remote_path") or "/DCIM/Camera")
        await self.refresh_gallery_widget(remote_path=rpath)
        self.events.log("GALLERY_MSG_RECV", f"{tag} cmd={cmd} path={rpath}", feature="gallery")
        reply = build_gallery_frame(
            self,
            cmd=int(cmd or 1),
            category=int(d.get("category", 7) or 7),
            path=rpath,
            msg_id=frame.get("msgId"),
        )
        await self._send_ws_text(conn, reply, f"GALLERY_REPLY_SENT(svc205 cmd={cmd})")
        if callable(getattr(self, "on_feature_start_cb", None)):
            with contextlib.suppress(Exception):
                self.on_feature_start_cb("gallery", path=rpath)

    async def _handle_features(self, conn: dict, frame: dict, data, cmd: int | None, tag: str) -> None:
        d = data if isinstance(data, dict) else {}
        if cmd is None:
            cmd = d.get("cmd")
        src_sid = int(frame.get("srcServiceId", 19) or 19)
        inner = d.get("data")
        self.features_state["last_cmd"] = cmd
        self.features_state["last_src_sid"] = src_sid
        self.events.log("FEATURES_SVC1001_RECV", f"{tag} srcServiceId={src_sid} cmd={cmd}", feature="features")

        if cmd == 1000:
            if src_sid not in self.features_state["listeners_1000"]:
                self.features_state["listeners_1000"].append(src_sid)
            return
        if cmd == 1001:
            if src_sid in self.features_state["listeners_1000"]:
                self.features_state["listeners_1000"].remove(src_sid)
            return
        if cmd in (1, 2):
            self.start_pairing_mode()
            self.features_state["launched_history"].append({"cmd": cmd, "target": "qr_scanner"})
            return
        if cmd == 3:
            self.features_state["launched_history"].append({"cmd": 3, "target": "devices_activity"})
            return
        if cmd == 4:
            self.features_state["launched_history"].append({"cmd": 4, "target": "applist", "src_sid": src_sid})
            await self.request("applist", conn)
            return
        if cmd == 5:
            pkg = inner.get("pkg") if isinstance(inner, dict) else ""
            self.features_state["launched_history"].append({"cmd": 5, "target": f"appstream:{pkg}"})
            if pkg:
                await self.request(f"appstream:{pkg}", conn)
            return
        if cmd == 6:
            self.features_state["stopped_history"].append({"cmd": 6, "target": "appstream", "apps": inner})
            await self.request("appstream-stop", conn)
            return
        if cmd == 7:
            self.features_state["launched_history"].append({"cmd": 7, "target": "runningapps", "src_sid": src_sid})
            await self.request("runningapps", conn)
            return
        if cmd == 8:
            rep8 = build_features_streaming_list_reply(
                self,
                dest_service_id=src_sid,
                device_id=d.get("deviceId") or frame.get("deviceId"),
                msg_id=frame.get("msgId"),
            )
            await self._send_ws_text(conn, rep8, f"FEATURES_STREAMING_LIST_SENT(svc1001->svc{src_sid} cmd8)")
            return
        if cmd == 9:
            rep9 = json.dumps(
                _env(self, frame.get("msgId") or self.next_msg_id(),
                     self.device_id_b64, src_sid, 1001,
                     {"ver": "1", "cmd": 9, "data": True}),
                separators=(",", ":"), ensure_ascii=False,
            )
            await self._send_ws_text(conn, rep9, f"FEATURES_CHECK_COND_SENT(svc1001->svc{src_sid} cmd9)")
            return
        if cmd == 10:
            keycode = int(inner) if isinstance(inner, (int, float)) or (isinstance(inner, str) and inner.isdigit()) else 85
            await self.request(f"keyevent:{keycode}", conn)
            return
        if cmd == 11:
            cast_d = inner if isinstance(inner, dict) else {}
            cast_cmd = int(cast_d.get("cmd", 0) or 0)
            if cast_cmd == 100:
                self.features_state["stopped_history"].append({"cmd": 11, "castCmd": 100, "target": "desktop"})
                await self._stop_desktop(conn, "svc1001-cast-stop")
            else:
                mode_map = {0: "mirror", 1: "desktop", 2: "mobile"}
                m_str = mode_map.get(cast_cmd, "desktop")
                self.features_state["launched_history"].append({"cmd": 11, "castCmd": cast_cmd, "target": m_str})
                await self.request(f"mobiledesktop:{m_str}", conn)
            return
        if cmd in (13, 14, 15):
            m_str = {13: "desktop", 14: "mirror", 15: "mobile"}[cmd]
            self.features_state["launched_history"].append({"cmd": cmd, "target": f"desktop:{m_str}"})
            await self.request(f"mobiledesktop:{m_str}", conn)
            return
        if cmd == 16:
            self.features_state["launched_history"].append({"cmd": 16, "target": "webcam"})
            await self.request("webcam", conn)
            return
        if cmd == 17:
            pkg = (inner or {}).get("pkg") if isinstance(inner, dict) else ""
            cmp_name = (inner or {}).get("cmp") if isinstance(inner, dict) else ""
            target_arg = f"{pkg}/{cmp_name}" if pkg and cmp_name else str(pkg or "desktop")
            self.features_state["launched_history"].append({"cmd": 17, "target": f"appstream:{target_arg}"})
            await self.request(f"appstream:{target_arg}", conn)
            return
        if cmd == 22:
            self.features_state["launched_history"].append({"cmd": 22, "target": "screenshare"})
            await self.request("screenshare", conn)
            return
        if cmd == 23:
            self.features_state["launched_history"].append({"cmd": 23, "target": "crosscontrol"})
            await self.start_cross_control(conn, trigger=1)
            return
        if cmd == 25:
            target_sid = int(inner) if isinstance(inner, (int, float)) or (isinstance(inner, str) and inner.lstrip("-").isdigit()) else 0
            sids_to_stop = list(FEATURES_LAUNCHABLE_SIDS) if target_sid <= 0 else [target_sid]
            self.features_state["stopped_history"].append({"cmd": 25, "target_sid": target_sid, "stopped_sids": sids_to_stop})
            for tsid in sids_to_stop:
                if tsid == 1:
                    await self._stop_desktop(conn, "svc1001-stop")
                elif tsid == 3:
                    await self.request("webcam-stop", conn)
                elif tsid == 15:
                    await self.request("appstream-stop", conn)
                elif tsid == 26:
                    await self.stop_cross_control(conn)
                elif tsid == 202:
                    await self.request("ss-stop", conn)
            return
        if cmd == 205 or src_sid == 205:
            self.features_state["launched_history"].append({"cmd": cmd, "target": "gallery"})
            await self._handle_gallery(conn, frame, d, 1, tag)
            return

from .base import SamyujService

class GalleryService(SamyujService):
    service_id = 205
    id = "gallery"
    name = "Photo Gallery Service (CNFSv3 & PhoneFileWidget)"
    description = (
        "svc-205 Gallery / Photos: "
        "browses phone /DCIM/Camera via PhoneFileWidgetController and serves/caches CNFSv3 "
        "proc 22 READTHUMBNAIL & proc 23 READCATEGORYDIR thumbnails in XDG_CACHE_HOME"
    )

    def actions(self) -> list[dict]:
        return [
            {"name": "refresh", "label": "Refresh Gallery (/DCIM/Camera)", "description": "Fetch gallery items via PhoneFileWidgetController"},
            {"name": "thumbnail", "label": "Fetch Thumbnail (CNFS proc 22)", "description": "Fetch and cache thumbnail via CNFS READTHUMBNAIL", "default_params": {"path": "/DCIM/Camera/IMG_0001.jpg", "quality": 75}},
            {"name": "open_pc_gallery", "label": "Launch Gallery via svc-1001", "description": "Notify phone/UI that Gallery view is active"},
        ]

    def get_state(self) -> dict:
        st = dict(getattr(self.h, "gallery_state", {}) or {})
        return {
            **super().get_state(),
            **st,
        }

    async def invoke(self, action: str, params: dict | None = None,
                     conn: dict | None = None) -> dict:
        params = params or {}
        c = conn or self.h._live_ws()
        if action in ("refresh", "list", "browse"):
            remote_path = str(params.get("path") or "/DCIM/Camera")
            return await self.h.refresh_gallery_widget(remote_path=remote_path)
        if action == "all_photos":
            return await self.h.refresh_gallery_widget(remote_path="__all_photos__")
        if action == "thumbnail":
            rpath = str(params.get("path") or "/DCIM/Camera/IMG_0001.jpg")
            qual = int(params.get("quality") or 75)
            return await self.h.fetch_gallery_thumbnail(rpath, quality=qual)
        if action == "download":
            rpath = str(params.get("path") or "")
            return await self.h.phonefs_download(rpath)
        if action == "delete":
            rpath = str(params.get("path") or "")
            return await self.h.phonefs_delete(rpath)
        if action == "rename":
            rpath = str(params.get("path") or "")
            new_name = str(params.get("new_name") or params.get("dst") or "")
            return await self.h.phonefs_rename(rpath, new_name)
        if action == "upload":
            local_p = str(params.get("local_path") or params.get("path") or "")
            remote_d = str(params.get("remote_dir") or "/DCIM/Camera")
            return await self.h.phonefs_upload(local_p, remote_d)
        if action == "mkdir":
            rpath = str(params.get("path") or "")
            return await self.h.phonefs_mkdir(rpath)
        if action == "stage_drag":
            rpath = str(params.get("path") or "")
            return await self.h.stage_phone_file_for_drag(rpath)
        if action in ("open_pc_gallery", "open", "launch"):
            return await self.h.request("gallery-open", c)
        return await super().invoke(action, params, conn)

class FeaturesService(SamyujService):
    service_id = 1001
    id = "features"
    name = "Features Launcher Service"
    description = (
        "svc-1001: "
        "launchable set D={1,3,11,13,15,26,202}, handles cmd:1..25 feature launches, "
        "cmd:8 streaming-list reply, and cmd:25 stop-service"
    )

    def actions(self) -> list[dict]:
        return [
            {"name": "query_streaming_apps", "label": "Query Streaming Apps (cmd:8)", "description": "Send svc-1001 cmd:8 getAppStreamingList"},
            {"name": "launch_desktop", "label": "Launch Desktop via svc-1001 (cmd:13)", "description": "Send svc-1001 cmd:13 launchDesktop"},
            {"name": "stop_all_features", "label": "Stop All Launchable Features (cmd:25)", "description": "Send svc-1001 cmd:25 stop-service(0) for D={1,3,11,13,15,26,202}"},
        ]

    def get_state(self) -> dict:
        st = dict(getattr(self.h, "features_state", {}) or {})
        return {
            **super().get_state(),
            **st,
        }

    async def invoke(self, action: str, params: dict | None = None,
                     conn: dict | None = None) -> dict:
        params = params or {}
        c = conn or self.h._live_ws()
        if action == "query_streaming_apps":
            return await self.h.request("features-streaming-list", c)
        if action == "launch_desktop":
            return await self.h.request("features-launch:13", c)
        if action == "stop_all_features":
            return await self.h.request("features-stop:0", c)
        return await super().invoke(action, params, conn)
