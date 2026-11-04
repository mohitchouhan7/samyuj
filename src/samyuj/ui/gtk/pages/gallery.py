from __future__ import annotations

import asyncio
import collections
import contextlib
import shutil
import subprocess
from pathlib import Path

from samyuj.ui.gtk.gtk_compat import Gdk, GdkPixbuf, GLib, Gtk, Pango

from samyuj.ui.gtk.assets import (
    extract_dropped_paths,
    open_dialog_nonblocking,
    round_pixbuf_corners,
)

class GalleryPageMixin:

    def _get_cached_gallery_pixbuf(self, cache_file: str) -> GdkPixbuf.Pixbuf | None:
        if not cache_file:
            return None
        lru = getattr(self, "_gallery_pixbuf_lru", None)
        if lru is None:
            lru = self._gallery_pixbuf_lru = collections.OrderedDict()
        if cache_file in lru:
            lru.move_to_end(cache_file)
            return lru[cache_file]
        if not Path(cache_file).is_file():
            return None
        try:
            tw = int(self.GALLERY_THUMB_W)
            th = int(self.GALLERY_THUMB_H)
            raw_pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                str(cache_file),
                tw * 2,
                th * 2,
                True,
            )
            if raw_pb is None:
                return None
            sw, sh = int(raw_pb.get_width()), int(raw_pb.get_height())
            if sw > 0 and sh > 0 and (sw != tw or sh != th):
                scale = max(tw / float(sw), th / float(sh))
                cw = max(tw, int(round(sw * scale)))
                ch = max(th, int(round(sh * scale)))
                scaled = raw_pb.scale_simple(cw, ch, GdkPixbuf.InterpType.BILINEAR)
                if scaled is not None:
                    ox = max(0, (cw - tw) // 2)
                    oy = max(0, (ch - th) // 2)
                    pb = scaled.new_subpixbuf(ox, oy, tw, th).copy()
                else:
                    pb = raw_pb.scale_simple(tw, th, GdkPixbuf.InterpType.BILINEAR)
            else:
                pb = raw_pb
            if pb is not None:
                pb = round_pixbuf_corners(pb, radius=12.0)
                lru[cache_file] = pb
                while len(lru) > int(self.GALLERY_MAX_CACHED_PIXBUFS):
                    lru.popitem(last=False)
                return pb
        except Exception:
            pass
        return None

    def _evict_all_gallery_pixbufs(self) -> int:
        lru = getattr(self, "_gallery_pixbuf_lru", None)
        freed = len(lru) if lru else 0
        if lru is not None:
            lru.clear()
        for rec in list(getattr(self, "_gallery_tile_records", []) or []):
            if rec.get("loaded"):
                holder = rec.get("holder")
                if holder is not None:
                    for ch in list(holder.get_children()):
                        holder.remove(ch)
                    ico_lbl = Gtk.Label()
                    ico_lbl.set_markup('<span size="10500" weight="bold">IMG</span>')
                    holder.pack_start(ico_lbl, True, True, 0)
                    holder.show_all()
                rec["loaded"] = False
        return freed

    def _evict_offscreen_gallery_tiles(self) -> dict:
        records = list(getattr(self, "_gallery_tile_records", []) or [])
        lru = getattr(self, "_gallery_pixbuf_lru", None)
        if lru is None:
            lru = self._gallery_pixbuf_lru = collections.OrderedDict()
        if not records:
            return {"in_view": 0, "evicted": 0, "ram_cached": len(lru)}

        sw = getattr(self, "gallery_scroll", None)
        vadj = sw.get_vadjustment() if sw is not None else None
        scroll_y = float(vadj.get_value()) if vadj is not None else 0.0
        page_h = float(vadj.get_page_size()) if vadj is not None else 0.0
        if page_h <= 40.0:
            page_h = 520.0
        margin_px = float(self.GALLERY_THUMB_H) * 1.5
        vis_top = max(0.0, scroll_y - margin_px)
        vis_bot = scroll_y + page_h + margin_px
        row_pitch = float(self.GALLERY_THUMB_H + 10)

        evicted_cnt = 0
        in_view_cnt = 0
        need_fetch: list[dict] = []

        for idx, rec in enumerate(records):
            if rec.get("is_dir"):
                continue
            btn = rec.get("btn")
            holder = rec.get("holder")
            if btn is None or holder is None:
                continue
            alloc = btn.get_allocation()
            if alloc is not None and alloc.height > 10 and (alloc.y > 0 or idx < 6):
                tile_top = float(alloc.y)
                tile_bot = tile_top + float(alloc.height)
            else:
                row_i = idx // 6
                tile_top = row_i * row_pitch
                tile_bot = tile_top + float(self.GALLERY_THUMB_H)

            is_visible = (tile_bot >= vis_top) and (tile_top <= vis_bot)
            cfile = str(rec.get("cache_file") or (self.facade.gallery_state.get("cached_thumbnails") or {}).get(rec["rpath"]) or "")
            if cfile:
                rec["cache_file"] = cfile

            if not is_visible:
                if rec.get("loaded"):
                    for ch in list(holder.get_children()):
                        holder.remove(ch)
                    ico_lbl = Gtk.Label()
                    ico_lbl.set_markup('<span size="10500" weight="bold">IMG</span>')
                    holder.pack_start(ico_lbl, True, True, 0)
                    holder.show_all()
                    rec["loaded"] = False
                    if cfile:
                        lru.pop(cfile, None)
                    evicted_cnt += 1
            else:
                in_view_cnt += 1
                if not rec.get("loaded"):
                    pb = self._get_cached_gallery_pixbuf(cfile) if cfile else None
                    if pb is not None:
                        for ch in list(holder.get_children()):
                            holder.remove(ch)
                        img_w = Gtk.Image.new_from_pixbuf(pb)
                        holder.pack_start(img_w, True, True, 0)
                        holder.show_all()
                        rec["loaded"] = True
                    elif not rec.get("fetching"):
                        need_fetch.append(rec)

        if need_fetch:
            my_gen = int(getattr(self, "_gallery_load_gen", 0))

            async def _fetch_visible_batch():
                sem = asyncio.Semaphore(3)

                async def _one(r_item: dict):
                    r_item["fetching"] = True
                    try:
                        async with sem:
                            if getattr(self, "_gallery_load_gen", 0) != my_gen:
                                return
                            tr = await self.facade.fetch_gallery_thumbnail(r_item["rpath"])
                            cf = str(tr.get("cache_file") or "")
                            if not tr.get("ok") or not cf:
                                return
                            r_item["cache_file"] = cf
                            if getattr(self, "_gallery_load_gen", 0) != my_gen:
                                return

                            def _apply():
                                if getattr(self, "_gallery_load_gen", 0) != my_gen:
                                    return False
                                pb_l = self._get_cached_gallery_pixbuf(cf)
                                h_box = r_item.get("holder")
                                if pb_l is not None and h_box is not None:
                                    for ch in list(h_box.get_children()):
                                        h_box.remove(ch)
                                    h_box.pack_start(Gtk.Image.new_from_pixbuf(pb_l), True, True, 0)
                                    h_box.show_all()
                                    r_item["loaded"] = True
                                return False

                            GLib.idle_add(_apply)
                    finally:
                        r_item["fetching"] = False

                await asyncio.gather(*(_one(ri) for ri in need_fetch))

            self._spawn(_fetch_visible_batch())

        return {"in_view": in_view_cnt, "evicted": evicted_cnt, "ram_cached": len(lru)}

    def _on_gallery_scroll_changed(self, *_args) -> None:
        if getattr(self, "_gallery_scroll_timer_id", 0):
            return

        def _tick():
            self._gallery_scroll_timer_id = 0
            stats = self._evict_offscreen_gallery_tiles()
            sw = getattr(self, "gallery_scroll", None)
            vadj = sw.get_vadjustment() if sw is not None else None
            if vadj is not None:
                val = float(vadj.get_value())
                psz = float(vadj.get_page_size())
                upp = float(vadj.get_upper())
                rendered_end = int(getattr(self, "_gallery_rendered_end_idx", 0))
                all_items = list(getattr(self, "_gallery_all_entries", []) or [])
                if psz > 0 and (val + psz >= upp - 140.0) and rendered_end < len(all_items):
                    self._append_next_gallery_batch()
            if stats.get("evicted", 0) > 0 and getattr(self, "gallery_status_lbl", None) is not None:
                folder = getattr(self, "_current_gallery_path", "__all_photos__")
                disp_f = "All Photos (Recent First)" if folder == "__all_photos__" else folder
                all_cnt = len(getattr(self, "_gallery_all_entries", []) or [])
                self.gallery_status_lbl.set_text(
                    f"{disp_f} · {all_cnt} items · In-view RAM: {stats['ram_cached']} tile(s) (off-screen previews freed)"
                )
            return False

        self._gallery_scroll_timer_id = GLib.timeout_add(65, _tick)

    def _resolve_local_uris_for_drag(self, remote_paths: list[str]) -> list[str]:
        uris: list[str] = []
        dl_dir = self.facade.get_incoming_files_dir()
        with contextlib.suppress(OSError):
            dl_dir.mkdir(parents=True, exist_ok=True)
        for rp in remote_paths or []:
            if not rp:
                continue
            fname = Path(rp).name
            dest = dl_dir / fname
            if not (dest.is_file() and dest.stat().st_size > 0):
                if getattr(self.facade, "linux_mount_mgr", None) is not None and getattr(self.facade.linux_mount_mgr, "mount_dir", None):
                    mcand = Path(self.facade.linux_mount_mgr.mount_dir) / rp.lstrip("/")
                    if mcand.is_file() and mcand.stat().st_size > 0:
                        dest = mcand
                if not (dest.is_file() and dest.stat().st_size > 0):
                    loop = getattr(self, "_ui_loop", None)
                    try:
                        running_loop = asyncio.get_running_loop()
                    except RuntimeError:
                        running_loop = None
                    if loop is not None and loop.is_running() and running_loop is not loop:
                        with contextlib.suppress(Exception):
                            fut = asyncio.run_coroutine_threadsafe(self.facade.stage_phone_file_for_drag(rp), loop)
                            res_st = fut.result(timeout=3.5)
                            if isinstance(res_st, dict) and res_st.get("saved"):
                                dest = Path(str(res_st["saved"]))
                if not (dest.is_file() and dest.stat().st_size > 0):
                    thumb_f = (self.facade.gallery_state.get("cached_thumbnails") or {}).get(rp)
                    if thumb_f and Path(thumb_f).is_file():
                        with contextlib.suppress(OSError):
                            shutil.copyfile(thumb_f, dest)
            if dest.is_file():
                with contextlib.suppress(Exception):
                    uris.append(dest.resolve().as_uri())
        return uris

    def _setup_remote_file_drag_source(self, widget: Gtk.Widget, get_paths_fn) -> None:
        drag_targets = [
            Gtk.TargetEntry.new("text/uri-list", 0, 0),
            Gtk.TargetEntry.new("text/plain", 0, 1),
            Gtk.TargetEntry.new("STRING", 0, 1),
        ]
        widget.drag_source_set(
            Gdk.ModifierType.BUTTON1_MASK,
            drag_targets,
            Gdk.DragAction.COPY,
        )

        def _on_drag_begin(_w, _ctx):
            paths = list(get_paths_fn() or [])
            for rp in paths:
                if rp:
                    self._spawn(self.facade.stage_phone_file_for_drag(rp))

        def _on_drag_data_get(_w, _ctx, selection_data, info, _time):
            paths = list(get_paths_fn() or [])
            uris = self._resolve_local_uris_for_drag(paths)
            if not uris:
                return
            if info == 0:
                selection_data.set_uris(uris)
            else:
                selection_data.set_text("\n".join(uris), -1)
            names = ", ".join(Path(p).name for p in paths[:2])
            if getattr(self, "gallery_status_lbl", None) is not None and self._last_main_page == "gallery":
                self.gallery_status_lbl.set_text(f"Dragged & exported {names} to desktop/app")
            elif getattr(self, "phonefs_status_lbl", None) is not None:
                self.phonefs_status_lbl.set_text(f"Dragged & exported {names} to desktop/app")

        widget.connect("drag-begin", _on_drag_begin)
        widget.connect("drag-data-get", _on_drag_data_get)

    def _build_gallery_page(self) -> Gtk.Widget:
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self._current_gallery_path = "__all_photos__"
        self._gallery_tile_records: list[dict] = []
        self._gallery_scroll_timer_id: int = 0
        self._gallery_rendered_end_idx: int = 0

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        h_left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        eb = Gtk.Label(label="PHOTOS & VIDEOS (SVC-205)", xalign=0.0)
        eb.get_style_context().add_class("eyebrow")
        h1 = Gtk.Label(label="Gallery", xalign=0.0)
        h1.get_style_context().add_class("page-h1")
        self.gallery_status_lbl = Gtk.Label(
            label="Recent photos first · Full-tile borderless view · Drag any photo out to your desktop or file manager.",
            xalign=0.0,
        )
        self.gallery_status_lbl.set_line_wrap(True)
        self.gallery_status_lbl.get_style_context().add_class("page-sub")
        self.gallery_status_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        self.gallery_status_lbl.set_max_width_chars(36)
        h_left.pack_start(eb, False, False, 0)
        h_left.pack_start(h1, False, False, 0)
        h_left.pack_start(self.gallery_status_lbl, False, False, 0)
        head.pack_start(h_left, True, True, 0)

        refresh_btn = Gtk.Button(label="Refresh")
        refresh_btn.get_style_context().add_class("primary-btn")
        refresh_btn.set_valign(Gtk.Align.START)
        refresh_btn.connect("clicked", lambda *_: self._refresh_gallery_page(force=True))
        head.pack_end(refresh_btn, False, False, 0)

        upload_gal_btn = Gtk.Button(label="Upload Photo")
        upload_gal_btn.get_style_context().add_class("secondary-btn")
        upload_gal_btn.set_valign(Gtk.Align.START)
        upload_gal_btn.set_tooltip_text("Upload photos or videos from this PC to your phone's Camera/Pictures folder")
        upload_gal_btn.connect("clicked", lambda *_: self._on_gallery_upload_clicked())
        head.pack_end(upload_gal_btn, False, False, 0)

        dl_page_btn = Gtk.Button(label="Download Visible")
        dl_page_btn.get_style_context().add_class("secondary-btn")
        dl_page_btn.set_valign(Gtk.Align.START)
        dl_page_btn.set_tooltip_text("Download current page photos into your PC Incoming Files folder")
        dl_page_btn.connect("clicked", lambda *_: self._on_gallery_download_page_clicked())
        head.pack_end(dl_page_btn, False, False, 0)

        open_dl_btn = Gtk.Button(label="Open Saved")
        open_dl_btn.get_style_context().add_class("secondary-btn")
        open_dl_btn.set_valign(Gtk.Align.START)
        open_dl_btn.set_tooltip_text("Open your PC Incoming Files folder in the Linux File Manager")
        open_dl_btn.connect("clicked", lambda *_: self._open_incoming_files_folder())
        head.pack_end(open_dl_btn, False, False, 0)
        vbox.pack_start(head, False, False, 0)

        folder_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        for label_txt, fpath in (
            ("All Photos (Recent)", "__all_photos__"),
            ("Camera", "/DCIM/Camera"),
            ("Screenshots", "/Pictures/Screenshots"),
            ("Pictures", "/Pictures"),
            ("DCIM", "/DCIM"),
            ("Videos", "/Movies"),
        ):
            fb = Gtk.Button(label=label_txt)
            fb.get_style_context().add_class("quick-path-pill")
            fb.connect("clicked", lambda *_, p=fpath: self._on_gallery_folder_selected(p))
            folder_bar.pack_start(fb, False, False, 0)

        nav_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.gallery_prev_btn = Gtk.Button(label="Prev")
        self.gallery_prev_btn.get_style_context().add_class("secondary-btn")
        self.gallery_prev_btn.set_sensitive(False)
        self.gallery_prev_btn.connect("clicked", lambda *_: self._on_gallery_page_step(-1))
        nav_bar.pack_start(self.gallery_prev_btn, False, False, 0)

        self.gallery_page_lbl = Gtk.Label(label="Page 1 / 1")
        self.gallery_page_lbl.get_style_context().add_class("panel-sub")
        nav_bar.pack_start(self.gallery_page_lbl, False, False, 4)

        self.gallery_next_btn = Gtk.Button(label="Next")
        self.gallery_next_btn.get_style_context().add_class("secondary-btn")
        self.gallery_next_btn.set_sensitive(False)
        self.gallery_next_btn.connect("clicked", lambda *_: self._on_gallery_page_step(1))
        nav_bar.pack_start(self.gallery_next_btn, False, False, 0)
        folder_bar.pack_end(nav_bar, False, False, 0)
        vbox.pack_start(folder_bar, False, False, 0)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.get_style_context().add_class("sc-card")

        self.gallery_scroll = Gtk.ScrolledWindow()
        with contextlib.suppress(Exception):
            from samyuj.ui.gtk.smooth_scroll import attach_smooth_scroll
            attach_smooth_scroll(self.gallery_scroll)
        self.gallery_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.gallery_scroll.set_vexpand(True)
        self.gallery_scroll.set_min_content_height(420)
        vadj = self.gallery_scroll.get_vadjustment()
        if vadj is not None:
            vadj.connect("value-changed", self._on_gallery_scroll_changed)

        self.gallery_flow = Gtk.FlowBox()
        self.gallery_flow.set_max_children_per_line(6)
        self.gallery_flow.set_column_spacing(8)
        self.gallery_flow.set_row_spacing(8)
        self.gallery_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        drop_targets = [
            Gtk.TargetEntry.new("text/uri-list", 0, 1),
            Gtk.TargetEntry.new("STRING", 0, 2),
        ]
        self.gallery_flow.drag_dest_set(Gtk.DestDefaults.ALL, drop_targets, Gdk.DragAction.COPY)
        self.gallery_flow.connect("drag-data-received", self._on_gallery_drop_received)

        self.gallery_scroll.add(self.gallery_flow)
        card.pack_start(self.gallery_scroll, True, True, 0)
        vbox.pack_start(card, True, True, 0)
        return vbox

    def _open_incoming_files_folder(self) -> None:
        dl_dir = self.facade.get_incoming_files_dir()
        with contextlib.suppress(OSError):
            dl_dir.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(Exception):
            subprocess.Popen(["xdg-open", str(dl_dir)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)

    def _on_gallery_upload_clicked(self) -> None:
        dlg = Gtk.FileChooserDialog(
            title="Select photos or videos to upload to your phone",
            parent=self.win,
            action=Gtk.FileChooserAction.OPEN,
        )
        dlg.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OPEN, Gtk.ResponseType.OK,
        )
        dlg.set_select_multiple(True)

        def _picked(d):
            files = d.get_filenames() or []
            if files:
                self._upload_files_to_gallery(files)

        open_dialog_nonblocking(dlg, on_ok=_picked)

    def _upload_files_to_gallery(self, local_paths: list[str]) -> None:
        cur_f = getattr(self, "_current_gallery_path", "__all_photos__")
        dest_dir = "/DCIM/Camera" if cur_f in ("__all_photos__", "all", "") else cur_f
        self.gallery_status_lbl.set_text(f"Uploading {len(local_paths)} file(s) to phone {dest_dir}…")

        async def _do_up():
            ok_cnt = 0
            for lp in local_paths:
                r = await self.facade.phonefs_upload(lp, dest_dir)
                if r.get("ok"):
                    ok_cnt += 1
            GLib.idle_add(
                self.gallery_status_lbl.set_text,
                f"Uploaded {ok_cnt}/{len(local_paths)} file(s) to {dest_dir}",
            )
            GLib.idle_add(self._refresh_gallery_page, True)

        self._spawn(_do_up())

    def _extract_dropped_paths(self, data, info=None) -> list:
        return extract_dropped_paths(data, info)

    def _on_gallery_drop_received(self, _widget, context, _x, _y, data, info, time_val) -> None:
        paths = self._extract_dropped_paths(data, info)
        if paths:
            files_only = [str(p) for p in paths if Path(p).is_file()]
            if files_only:
                self._upload_files_to_gallery(files_only)
        Gtk.drag_finish(context, bool(paths), False, time_val)

    def _on_gallery_download_page_clicked(self) -> None:
        all_items = list(getattr(self, "_gallery_all_entries", []) or [])
        page_sz = max(1, int(self.GALLERY_PAGE_SIZE))
        page_idx = max(0, int(getattr(self, "_gallery_page_idx", 0)))
        page_items = [
            e for e in all_items[page_idx * page_sz:(page_idx + 1) * page_sz]
            if not e.get("is_dir")
        ]
        if not page_items:
            self.gallery_status_lbl.set_text("No photos on this page to download.")
            return
        self.gallery_status_lbl.set_text(f"Downloading {len(page_items)} photo(s) to {self.facade.get_incoming_files_dir()}…")

        async def _dl_batch():
            ok_c = 0
            for ent in page_items:
                rp = str(ent.get("remote_path") or ent.get("path") or "")
                if rp:
                    r = await self.facade.phonefs_download(rp)
                    if r.get("ok"):
                        ok_c += 1
            GLib.idle_add(
                self.gallery_status_lbl.set_text,
                f"Downloaded {ok_c}/{len(page_items)} photo(s) → {self.facade.get_incoming_files_dir()}",
            )

        self._spawn(_dl_batch())

    def _on_gallery_folder_selected(self, folder_path: str) -> None:
        self._evict_all_gallery_pixbufs()
        self._current_gallery_path = str(folder_path or "__all_photos__")
        self._gallery_page_idx = 0
        self._refresh_gallery_page(force=True)

    def _on_gallery_page_step(self, delta: int) -> None:
        entries = list(getattr(self, "_gallery_all_entries", []) or [])
        page_sz = max(1, int(self.GALLERY_PAGE_SIZE))
        total_pages = max(1, (len(entries) + page_sz - 1) // page_sz)
        new_idx = max(0, min(total_pages - 1, int(getattr(self, "_gallery_page_idx", 0)) + int(delta)))
        if new_idx != getattr(self, "_gallery_page_idx", 0):
            self._evict_all_gallery_pixbufs()
            self._gallery_page_idx = new_idx
            self._render_gallery_current_page()

    def _refresh_gallery_page(self, force: bool = False) -> None:
        flow = getattr(self, "gallery_flow", None)
        if flow is None:
            return
        folder = getattr(self, "_current_gallery_path", "__all_photos__")
        disp_folder = "All Photos (Recent First)" if folder == "__all_photos__" else folder
        self.gallery_status_lbl.set_text(f"Loading Gallery ({disp_folder})…")

        async def _load():
            res = await self.facade.service_bus.invoke("gallery", "refresh", {"path": folder})
            actual_folder = str(res.get("remote_path") or folder)
            self._current_gallery_path = actual_folder
            items = list(res.get("entries") or [])
            self._gallery_all_entries = items
            page_sz = max(1, int(self.GALLERY_PAGE_SIZE))
            max_page = max(0, (len(items) - 1) // page_sz) if items else 0
            if getattr(self, "_gallery_page_idx", 0) > max_page:
                self._gallery_page_idx = 0

            def _after_list():
                if not res.get("ok"):
                    self._evict_all_gallery_pixbufs()
                    for ch in list(flow.get_children()):
                        flow.remove(ch)
                    msg = str(res.get("user_message") or res.get("error") or "Connect your phone to browse photos")
                    self.gallery_status_lbl.set_text(f"Gallery ({disp_folder}): {msg}")
                    emp = Gtk.Label(
                        label=f"{msg} — Click 'Refresh' once your phone is connected and unlocked.",
                        xalign=0.0,
                    )
                    emp.get_style_context().add_class("panel-sub")
                    flow.add(emp)
                    flow.show_all()
                    return False
                self._render_gallery_current_page()
                return False

            GLib.idle_add(_after_list)

        self._spawn(_load())

    def _create_gallery_tile_widget(self, ent: dict, folder: str, pending_thumbs: list) -> Gtk.Widget:
        ename = str(ent.get("name") or "")
        rpath = str(ent.get("remote_path") or ent.get("path") or (folder.rstrip("/") + "/" + ename))
        is_dir = bool(ent.get("is_dir") or ent.get("type") == "dir")
        fkind = str(ent.get("file_kind") or ("dir" if is_dir else "file"))
        sz_bytes = int(ent.get("size") or 0)
        sz_kb = f"{sz_bytes / (1024 * 1024):.1f} MB" if sz_bytes >= 1024 * 1024 else f"{max(1, sz_bytes // 1024)} KB"

        btn = Gtk.Button()
        if is_dir:
            btn.get_style_context().add_class("secondary-btn")
            btn.set_tooltip_text(f"Open folder {ename} ({rpath})")
        else:
            btn.get_style_context().add_class("gallery-photo-tile")
            btn.set_relief(Gtk.ReliefStyle.NONE)
            btn.set_tooltip_text(
                f"{ename} ({sz_kb})\nPath: {rpath}\nLeft-click: Preview · Right-click: Manage · Drag: Copy to Desktop/Files"
            )

        b_in = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        b_in.set_size_request(int(self.GALLERY_THUMB_W), int(self.GALLERY_THUMB_H))
        b_in.set_halign(Gtk.Align.FILL)
        b_in.set_valign(Gtk.Align.FILL)

        img_holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        img_holder.set_size_request(int(self.GALLERY_THUMB_W), int(self.GALLERY_THUMB_H))
        img_holder.set_halign(Gtk.Align.FILL)
        img_holder.set_valign(Gtk.Align.FILL)

        cached_thumb_file = (self.facade.gallery_state.get("cached_thumbnails") or {}).get(rpath, "")
        pb = self._get_cached_gallery_pixbuf(cached_thumb_file) if (cached_thumb_file and not is_dir) else None
        loaded_now = False
        if pb is not None:
            img_w = Gtk.Image.new_from_pixbuf(pb)
            img_holder.pack_start(img_w, True, True, 0)
            loaded_now = True
        else:
            ico_txt = self._file_icon_for_entry(ename, "dir" if is_dir else "file")
            ico_lbl = Gtk.Label()
            ico_lbl.set_markup(f'<span size="22000">{ico_txt}</span>')
            img_holder.pack_start(ico_lbl, True, True, 0)
            if not is_dir and fkind in ("image", "video", "file"):
                pending_thumbs.append((rpath, img_holder, ent))

        b_in.pack_start(img_holder, True, True, 0)
        if is_dir:
            lbl = Gtk.Label(label=ename)
            lbl.set_ellipsize(Pango.EllipsizeMode.END)
            lbl.set_max_width_chars(14)
            b_in.pack_start(lbl, False, False, 0)
        btn.add(b_in)

        tile_rec = {
            "btn": btn,
            "holder": img_holder,
            "rpath": rpath,
            "ename": ename,
            "ent": ent,
            "is_dir": is_dir,
            "fkind": fkind,
            "loaded": loaded_now,
            "cache_file": cached_thumb_file,
            "fetching": False,
        }
        self._gallery_tile_records.append(tile_rec)

        if is_dir:
            btn.connect("clicked", lambda *_, rp=rpath: self._on_gallery_folder_selected(rp))
        else:
            btn.connect("clicked", lambda *_, e_d=ent, rp=rpath: self._on_gallery_item_clicked(rp, e_d))
            btn.connect("button-press-event", lambda w, ev, e_d=ent, rp=rpath: self._on_gallery_tile_button_press(w, ev, rp, e_d))
            self._setup_remote_file_drag_source(btn, lambda rp=rpath: [rp])
        return btn

    def _on_gallery_tile_button_press(self, widget: Gtk.Widget, event, remote_path: str, ent: dict) -> bool:
        if getattr(event, "button", 0) != 3:
            return False
        ename = str(ent.get("name") or Path(remote_path).name)
        menu = Gtk.Menu()

        mi_prev = Gtk.MenuItem(label=f"Preview {ename}")
        mi_prev.connect("activate", lambda *_: self._on_gallery_item_clicked(remote_path, ent))
        menu.append(mi_prev)

        mi_dl = Gtk.MenuItem(label="Download to PC")
        mi_dl.connect("activate", lambda *_: self._download_gallery_item(remote_path, ename, open_ext=False))
        menu.append(mi_dl)

        mi_ext = Gtk.MenuItem(label="Download & Open in External Viewer")
        mi_ext.connect("activate", lambda *_: self._download_gallery_item(remote_path, ename, open_ext=True))
        menu.append(mi_ext)

        mi_ren = Gtk.MenuItem(label="Rename on Phone...")
        mi_ren.connect("activate", lambda *_: self._prompt_rename_phone_item(remote_path, ename, is_gallery=True))
        menu.append(mi_ren)

        mi_del = Gtk.MenuItem(label="Delete from Phone")
        mi_del.connect("activate", lambda *_: self._delete_phone_item(remote_path, ename, is_gallery=True))
        menu.append(mi_del)

        menu.show_all()
        menu.popup_at_pointer(event)
        return True

    def _download_gallery_item(self, remote_path: str, ename: str, open_ext: bool = False) -> None:
        self.gallery_status_lbl.set_text(f"Downloading {ename}…")

        async def _do():
            res = await self.facade.service_bus.invoke("gallery", "download", {"path": remote_path})
            saved = str(res.get("saved") or "")
            if res.get("ok") and saved:
                GLib.idle_add(self.gallery_status_lbl.set_text, f"Downloaded {ename} → {saved}")
                if open_ext:
                    with contextlib.suppress(Exception):
                        subprocess.Popen(["xdg-open", saved], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            else:
                GLib.idle_add(self.gallery_status_lbl.set_text, f"Download failed: {res.get('error')}")

        self._spawn(_do())

    def _append_next_gallery_batch(self) -> None:
        flow = getattr(self, "gallery_flow", None)
        if flow is None:
            return
        all_items = list(getattr(self, "_gallery_all_entries", []) or [])
        start_i = int(getattr(self, "_gallery_rendered_end_idx", 0))
        if start_i >= len(all_items):
            return
        batch_sz = max(1, int(self.GALLERY_PAGE_SIZE))
        next_items = all_items[start_i:start_i + batch_sz]
        if not next_items:
            return
        self._gallery_rendered_end_idx = start_i + len(next_items)
        folder = getattr(self, "_current_gallery_path", "__all_photos__")
        my_gen = int(getattr(self, "_gallery_load_gen", 0))
        pending_thumbs: list[tuple[str, Gtk.Box, dict]] = []
        for ent in next_items:
            btn = self._create_gallery_tile_widget(ent, folder, pending_thumbs)
            flow.add(btn)
        flow.show_all()
        self._evict_offscreen_gallery_tiles()
        if pending_thumbs:
            self._spawn_gallery_thumb_fetcher(pending_thumbs, my_gen)

    def _spawn_gallery_thumb_fetcher(self, pending_thumbs: list[tuple[str, Gtk.Box, dict]], my_gen: int) -> None:
        async def _fetch_page_thumbs():
            sem = asyncio.Semaphore(3)

            async def _one(rp: str, holder: Gtk.Box):
                async with sem:
                    if getattr(self, "_gallery_load_gen", 0) != my_gen:
                        return
                    tr = await self.facade.fetch_gallery_thumbnail(rp)
                    cfile = str(tr.get("cache_file") or "")
                    if not tr.get("ok") or not cfile:
                        return
                    if getattr(self, "_gallery_load_gen", 0) != my_gen:
                        return

                    def _apply_thumb():
                        if getattr(self, "_gallery_load_gen", 0) != my_gen:
                            return False
                        pb_loaded = self._get_cached_gallery_pixbuf(cfile)
                        if pb_loaded is not None:
                            for ch in list(holder.get_children()):
                                holder.remove(ch)
                            holder.pack_start(Gtk.Image.new_from_pixbuf(pb_loaded), True, True, 0)
                            holder.show_all()
                            for rec in getattr(self, "_gallery_tile_records", []) or []:
                                if rec.get("rpath") == rp:
                                    rec["loaded"] = True
                                    rec["cache_file"] = cfile
                        return False

                    GLib.idle_add(_apply_thumb)

            await asyncio.gather(*(_one(rp, h_box) for rp, h_box, _e in pending_thumbs))

        self._spawn(_fetch_page_thumbs())

    def _render_gallery_current_page(self) -> None:
        flow = getattr(self, "gallery_flow", None)
        if flow is None:
            return
        self._gallery_tile_records = []
        for ch in list(flow.get_children()):
            flow.remove(ch)

        folder = getattr(self, "_current_gallery_path", "__all_photos__")
        disp_folder = "All Photos (Recent First)" if folder == "__all_photos__" else folder
        all_items = list(getattr(self, "_gallery_all_entries", []) or [])
        page_sz = max(1, int(self.GALLERY_PAGE_SIZE))
        total_pages = max(1, (len(all_items) + page_sz - 1) // page_sz)
        page_idx = max(0, min(total_pages - 1, int(getattr(self, "_gallery_page_idx", 0))))
        self._gallery_page_idx = page_idx
        start_i = page_idx * page_sz
        page_items = all_items[start_i:start_i + page_sz]
        self._gallery_rendered_end_idx = start_i + len(page_items)

        if getattr(self, "gallery_prev_btn", None) is not None:
            self.gallery_prev_btn.set_sensitive(page_idx > 0)
        if getattr(self, "gallery_next_btn", None) is not None:
            self.gallery_next_btn.set_sensitive(page_idx + 1 < total_pages)
        if getattr(self, "gallery_page_lbl", None) is not None:
            self.gallery_page_lbl.set_text(f"Page {page_idx + 1} / {total_pages}")

        self.gallery_status_lbl.set_text(
            f"{disp_folder} · Showing {len(page_items)} of {len(all_items)} items (newest first) · "
            f"Page {page_idx + 1}/{total_pages} · Drag any photo to desktop"
        )

        if not page_items:
            emp = Gtk.Label(
                label=f"No photos or videos in {disp_folder} — choose another section pill above or click 'Refresh'.",
                xalign=0.0,
            )
            emp.get_style_context().add_class("panel-sub")
            flow.add(emp)
            flow.show_all()
            return

        self._gallery_load_gen = int(getattr(self, "_gallery_load_gen", 0)) + 1
        my_gen = self._gallery_load_gen
        pending_thumbs: list[tuple[str, Gtk.Box, dict]] = []

        for ent in page_items:
            btn = self._create_gallery_tile_widget(ent, folder, pending_thumbs)
            flow.add(btn)

        flow.show_all()
        if pending_thumbs:
            self._spawn_gallery_thumb_fetcher(pending_thumbs, my_gen)

    def _on_gallery_item_clicked(self, remote_path: str, entry: dict | None = None) -> None:
        ent = entry or {"name": Path(remote_path).name, "path": remote_path}
        cfile = (self.facade.gallery_state.get("cached_thumbnails") or {}).get(remote_path, "")
        self._open_gallery_preview_dialog(ent, remote_path, cfile)

    def _open_gallery_preview_dialog(self, ent: dict, remote_path: str, cache_file: str = "") -> None:
        ename = str(ent.get("name") or Path(remote_path).name)
        dlg = Gtk.Dialog(
            title=f"Gallery Preview — {ename}",
            transient_for=self.win,
            flags=Gtk.DialogFlags.DESTROY_WITH_PARENT,
        )
        dlg.set_modal(False)
        dlg.set_default_size(680, 540)
        box = dlg.get_content_area()
        box.set_spacing(10)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(14)
        box.set_margin_end(14)

        preview_holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        preview_holder.set_size_request(620, 390)
        preview_holder.set_halign(Gtk.Align.CENTER)
        preview_holder.set_valign(Gtk.Align.CENTER)
        box.pack_start(preview_holder, True, True, 0)

        def _set_preview_from_file(fpath: str, max_w: int = 620, max_h: int = 390) -> bool:
            if not fpath or not Path(fpath).is_file():
                return False
            try:
                pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(fpath), max_w, max_h, True)
                if pb is not None:
                    for ch in list(preview_holder.get_children()):
                        preview_holder.remove(ch)
                    preview_holder.pack_start(Gtk.Image.new_from_pixbuf(pb), True, True, 0)
                    preview_holder.show_all()
                    return True
            except Exception:
                pass
            return False

        if not _set_preview_from_file(cache_file, 620, 390):
            ph_lbl = Gtk.Label(label=f"Loading preview for {ename}…")
            ph_lbl.get_style_context().add_class("panel-sub")
            preview_holder.pack_start(ph_lbl, True, True, 0)

            async def _load_initial_thumb():
                tr = await self.facade.fetch_gallery_thumbnail(remote_path, quality=85)
                cf = str(tr.get("cache_file") or "")
                if tr.get("ok") and cf:
                    GLib.idle_add(_set_preview_from_file, cf, 620, 390)
                else:
                    GLib.idle_add(ph_lbl.set_text, f"{ename} — Click 'Download to PC & HQ Preview' below")

            self._spawn(_load_initial_thumb())

        status_lbl = Gtk.Label(label=f"Phone path: {remote_path}", xalign=0.0)
        status_lbl.get_style_context().add_class("panel-sub")
        box.pack_start(status_lbl, False, False, 0)

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        dl_preview_btn = Gtk.Button(label="Download to PC & HQ Preview")
        dl_preview_btn.get_style_context().add_class("primary-btn")
        ext_btn = Gtk.Button(label="Open in External Viewer")
        ext_btn.get_style_context().add_class("secondary-btn")
        ren_btn = Gtk.Button(label="Rename")
        ren_btn.get_style_context().add_class("secondary-btn")
        del_btn = Gtk.Button(label="Delete from Phone")
        del_btn.get_style_context().add_class("danger-btn")
        close_btn = Gtk.Button(label="Close")
        close_btn.get_style_context().add_class("secondary-btn")

        def _on_dl(open_external: bool = False):
            status_lbl.set_text(f"Downloading {ename}…")

            async def _do_dl():
                res = await self.facade.service_bus.invoke("gallery", "download", {"path": remote_path})
                saved = str(res.get("saved") or "")
                if res.get("ok") and saved:
                    GLib.idle_add(status_lbl.set_text, f"Saved to {saved}")
                    GLib.idle_add(self.gallery_status_lbl.set_text, f"Downloaded {ename} → {saved}")
                    GLib.idle_add(_set_preview_from_file, saved, 640, 420)
                    if open_external:
                        with contextlib.suppress(Exception):
                            subprocess.Popen(["xdg-open", saved], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                else:
                    GLib.idle_add(status_lbl.set_text, f"Download failed: {res.get('error')}")

            self._spawn(_do_dl())

        def _on_del():
            dlg.destroy()
            self._delete_phone_item(remote_path, ename, is_gallery=True)

        def _on_ren():
            dlg.destroy()
            self._prompt_rename_phone_item(remote_path, ename, is_gallery=True)

        dl_preview_btn.connect("clicked", lambda *_: _on_dl(False))
        ext_btn.connect("clicked", lambda *_: _on_dl(True))
        ren_btn.connect("clicked", lambda *_: _on_ren())
        del_btn.connect("clicked", lambda *_: _on_del())
        close_btn.connect("clicked", lambda *_: dlg.destroy())

        btn_row.pack_start(dl_preview_btn, False, False, 0)
        btn_row.pack_start(ext_btn, False, False, 0)
        btn_row.pack_start(ren_btn, False, False, 0)
        btn_row.pack_start(del_btn, False, False, 0)
        btn_row.pack_end(close_btn, False, False, 0)
        box.pack_start(btn_row, False, False, 0)

        def _cleanup(*_a):
            for ch in list(preview_holder.get_children()):
                preview_holder.remove(ch)

        dlg.connect("destroy", _cleanup)
        dlg.show_all()

GalleryPage = GalleryPageMixin
