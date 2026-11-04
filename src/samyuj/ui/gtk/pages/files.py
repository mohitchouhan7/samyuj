from __future__ import annotations

import contextlib
from pathlib import Path

from samyuj.ui.gtk.gtk_compat import Gdk, GdkPixbuf, GLib, Gtk, Pango
from samyuj.files.nfs import default_mount_base

from samyuj.ui.gtk.assets import (
    extract_dropped_paths,
    open_dialog_nonblocking,
)

class FilesPageMixin:

    def _build_files_page(self) -> Gtk.Widget:
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        h_left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        eb = Gtk.Label(label="YOUR CONTENT", xalign=0.0)
        eb.get_style_context().add_class("eyebrow")
        h1 = Gtk.Label(label="Files", xalign=0.0)
        h1.get_style_context().add_class("page-h1")
        sub = Gtk.Label(label="Move things between your phone and computer, or open your phone directly in the Linux File Manager.", xalign=0.0)
        sub.get_style_context().add_class("page-sub")
        sub.set_ellipsize(Pango.EllipsizeMode.END)
        h_left.pack_start(eb, False, False, 0)
        h_left.pack_start(h1, False, False, 0)
        h_left.pack_start(sub, False, False, 0)
        head.pack_start(h_left, True, True, 0)

        send_btn = Gtk.Button(label="Send files")
        send_btn.get_style_context().add_class("primary-btn")
        send_btn.set_valign(Gtk.Align.START)
        send_btn.connect("clicked", lambda *_: self._choose_and_send_files())
        head.pack_end(send_btn, False, False, 0)

        open_fm_btn = Gtk.Button(label="Open in File Manager")
        open_fm_btn.get_style_context().add_class("secondary-btn")
        open_fm_btn.set_valign(Gtk.Align.START)
        open_fm_btn.connect("clicked", self._on_open_linux_file_manager)
        head.pack_end(open_fm_btn, False, False, 4)
        vbox.pack_start(head, False, False, 0)

        mount_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        mount_card.get_style_context().add_class("sc-card")
        vbox.pack_start(mount_card, True, True, 0)

        mc_top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.phonefs_status_lbl = Gtk.Label(
            label="Phone NFS Storage: Ready to attach (sidebar bookmark appears when mounted)",
            xalign=0.0,
        )
        self.phonefs_status_lbl.get_style_context().add_class("panel-h2")
        self.phonefs_status_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        mc_top.pack_start(self.phonefs_status_lbl, True, True, 0)

        mount_now_btn = Gtk.Button(label="Connect & Attach")
        mount_now_btn.get_style_context().add_class("primary-btn")
        mount_now_btn.connect("clicked", self._on_mount_phone_nfs_clicked)
        mc_top.pack_end(mount_now_btn, False, False, 0)

        up_dir_btn = Gtk.Button(label="Up")
        up_dir_btn.get_style_context().add_class("secondary-btn")
        up_dir_btn.connect("clicked", self._on_phonefs_up_clicked)
        mc_top.pack_end(up_dir_btn, False, False, 0)
        mount_card.pack_start(mc_top, False, False, 0)

        mc_actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        open_sel_btn = Gtk.Button(label="Open")
        open_sel_btn.get_style_context().add_class("primary-btn")
        open_sel_btn.set_tooltip_text("Open the selected file directly over Phone NFS without downloading to ~/Downloads")
        open_sel_btn.connect("clicked", self._on_phonefs_open_selected_clicked)
        mc_actions.pack_start(open_sel_btn, False, False, 0)

        dl_sel_btn = Gtk.Button(label="Download")
        dl_sel_btn.get_style_context().add_class("secondary-btn")
        dl_sel_btn.set_tooltip_text("Download the selected phone file into your Incoming Files folder")
        dl_sel_btn.connect("clicked", self._on_phonefs_download_selected_clicked)
        mc_actions.pack_start(dl_sel_btn, False, False, 0)

        up_here_btn = Gtk.Button(label="Upload Here")
        up_here_btn.get_style_context().add_class("secondary-btn")
        up_here_btn.set_tooltip_text("Upload local PC files directly into the current phone folder")
        up_here_btn.connect("clicked", self._on_phonefs_upload_here_clicked)
        mc_actions.pack_start(up_here_btn, False, False, 0)

        mkdir_btn = Gtk.Button(label="New Folder")
        mkdir_btn.get_style_context().add_class("secondary-btn")
        mkdir_btn.set_tooltip_text("Create a new folder inside the current phone directory")
        mkdir_btn.connect("clicked", self._on_phonefs_mkdir_clicked)
        mc_actions.pack_start(mkdir_btn, False, False, 0)

        ren_sel_btn = Gtk.Button(label="Rename")
        ren_sel_btn.get_style_context().add_class("secondary-btn")
        ren_sel_btn.set_tooltip_text("Rename the selected file or folder on your phone")
        ren_sel_btn.connect("clicked", self._on_phonefs_rename_selected_clicked)
        mc_actions.pack_start(ren_sel_btn, False, False, 0)

        del_sel_btn = Gtk.Button(label="Delete")
        del_sel_btn.get_style_context().add_class("danger-btn")
        del_sel_btn.set_tooltip_text("Delete the selected file or folder from your phone")
        del_sel_btn.connect("clicked", self._on_phonefs_delete_selected_clicked)
        mc_actions.pack_start(del_sel_btn, False, False, 0)
        mount_card.pack_start(mc_actions, False, False, 0)

        self.phonefs_path_lbl = Gtk.Label(
            label="Phone path: /sdcard  ·  Double-click to open · Click Download to save to PC",
            xalign=0.0,
        )
        self.phonefs_path_lbl.set_line_wrap(True)
        self.phonefs_path_lbl.get_style_context().add_class("panel-sub")
        self.phonefs_path_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        self.phonefs_path_lbl.set_max_width_chars(52)
        mount_card.pack_start(self.phonefs_path_lbl, False, False, 0)

        quick_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        for q_label, q_path in (
            ("/sdcard", "/"),
            ("DCIM", "/DCIM"),
            ("Pictures", "/Pictures"),
            ("Download", "/Download"),
            ("Music", "/Music"),
            ("Movies", "/Movies"),
            ("Documents", "/Documents"),
        ):
            qb = Gtk.Button(label=q_label)
            qb.get_style_context().add_class("quick-path-pill")
            qb.connect("clicked", lambda *_, qp=q_path: self._on_phonefs_quick_path_clicked(qp))
            quick_bar.pack_start(qb, False, False, 0)
        mount_card.pack_start(quick_bar, False, False, 2)

        sw = Gtk.ScrolledWindow()
        with contextlib.suppress(Exception):
            from samyuj.ui.gtk.smooth_scroll import attach_smooth_scroll
            attach_smooth_scroll(sw)
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.set_vexpand(True)
        sw.set_min_content_height(240)
        sw.get_style_context().add_class("phonefs-scroll")
        self.phonefs_store = Gtk.ListStore(str, str, str, str, str)
        self.phonefs_tree = Gtk.TreeView(model=self.phonefs_store)
        self.phonefs_tree.get_style_context().add_class("phonefs-treeview")
        self.phonefs_tree.set_activate_on_single_click(False)
        with contextlib.suppress(Exception):
            self.phonefs_tree.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE)
        for idx, col_title in enumerate(("Type", "Name", "Kind", "Size")):
            renderer = Gtk.CellRendererText()
            if idx == 1:
                renderer.set_property("weight", 600)
                renderer.set_property("ellipsize", Pango.EllipsizeMode.END)
            col = Gtk.TreeViewColumn(col_title, renderer, text=idx)
            col.set_resizable(True)
            if idx == 1:
                col.set_expand(True)
                col.set_min_width(140)
            self.phonefs_tree.append_column(col)
        self.phonefs_tree.connect("row-activated", self._on_phonefs_row_activated)
        self.phonefs_tree.connect("button-press-event", self._on_phonefs_tree_button_press)
        self._setup_remote_file_drag_source(self.phonefs_tree, self._get_selected_phonefs_remote_paths)
        pfs_drop_targets = [
            Gtk.TargetEntry.new("text/uri-list", 0, 1),
            Gtk.TargetEntry.new("STRING", 0, 2),
        ]
        self.phonefs_tree.drag_dest_set(Gtk.DestDefaults.ALL, pfs_drop_targets, Gdk.DragAction.COPY)
        self.phonefs_tree.connect("drag-data-received", self._on_phonefs_drop_received)
        sw.add(self.phonefs_tree)
        mount_card.pack_start(sw, True, True, 0)

        shared_summary_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        shared_summary_card.get_style_context().add_class("sc-card")

        s_top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        s_left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        s_h2 = Gtk.Label(label="PC Folders Shared to Phone as NFS (svc-168)", xalign=0.0)
        s_h2.get_style_context().add_class("panel-h2")
        self.files_shared_summary_lbl = Gtk.Label(
            label="Export Linux directories over NFSv3 so your phone can browse, read, and write them directly.",
            xalign=0.0,
        )
        self.files_shared_summary_lbl.set_line_wrap(True)
        self.files_shared_summary_lbl.get_style_context().add_class("panel-sub")
        self.files_shared_summary_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        s_left.pack_start(s_h2, False, False, 0)
        s_left.pack_start(self.files_shared_summary_lbl, False, False, 0)
        s_top.pack_start(s_left, True, True, 0)

        browse_nfs_btn = Gtk.Button(label="Browse Folder...")
        browse_nfs_btn.get_style_context().add_class("secondary-btn")
        browse_nfs_btn.set_valign(Gtk.Align.CENTER)
        browse_nfs_btn.connect("clicked", lambda *_: self._choose_and_share_folders())
        s_top.pack_end(browse_nfs_btn, False, False, 0)
        shared_summary_card.pack_start(s_top, False, False, 0)

        add_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.nfs_export_path_entry = Gtk.Entry()
        self.nfs_export_path_entry.get_style_context().add_class("sc-entry")
        self.nfs_export_path_entry.set_placeholder_text(f"/path/to/linux/folder (e.g. {Path.home() / 'Documents'})")
        self.nfs_export_path_entry.connect("activate", self._on_add_nfs_path_clicked)
        add_row.pack_start(self.nfs_export_path_entry, True, True, 0)

        self.nfs_export_alias_entry = Gtk.Entry()
        self.nfs_export_alias_entry.get_style_context().add_class("sc-entry")
        self.nfs_export_alias_entry.set_placeholder_text("Alias (optional)")
        self.nfs_export_alias_entry.set_width_chars(16)
        self.nfs_export_alias_entry.connect("activate", self._on_add_nfs_path_clicked)
        add_row.pack_start(self.nfs_export_alias_entry, False, False, 0)

        add_nfs_btn = Gtk.Button(label="+ Add NFS Path")
        add_nfs_btn.get_style_context().add_class("primary-btn")
        add_nfs_btn.connect("clicked", self._on_add_nfs_path_clicked)
        add_row.pack_start(add_nfs_btn, False, False, 0)
        shared_summary_card.pack_start(add_row, False, False, 0)

        self.files_nfs_shares_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        shared_summary_card.pack_start(self.files_nfs_shares_box, False, False, 0)

        vbox.pack_end(shared_summary_card, False, False, 0)

        return vbox

    def _on_mount_phone_nfs_clicked(self, *_args) -> None:
        if getattr(self, "_phonefs_attaching", False):
            return
        self._phonefs_attaching = True
        nav_gen = int(getattr(self, "_phonefs_nav_gen", 0)) + 1
        self._phonefs_nav_gen = nav_gen
        target_p = self._current_phonefs_path or "/"

        async def _do():
            try:
                GLib.idle_add(self.phonefs_status_lbl.set_text,
                              "Phone NFS: connecting to phone storage…")
                res = await self.facade.phonefs_connect(timeout=10.0)
                if res.get("ok"):
                    ls_res = await self.facade.phonefs_ls(target_p)
                    GLib.idle_add(self._populate_phonefs_tree, ls_res, nav_gen)
                    GLib.idle_add(self.phonefs_status_lbl.set_text,
                                  "Phone NFS: attaching to Linux File Manager…")
                    att = await self.facade.linux_mount_mgr.attach(
                        self.facade._best_device_name(), sync_files=False)
                    gio = att.get("gio_mount") or {}
                    mpath = att.get("mount_path") or ""
                    if gio.get("ok"):
                        _how = ("kernel mount at " + gio["path"]) if gio.get("method") == "kernel" and gio.get("path") else gio.get("uri")
                        msg = f"Phone NFS: MOUNTED in your file manager ({_how})"
                    else:
                        msg = f"Phone NFS: Attached at {mpath} (File Manager sidebar bookmark ready)"
                    GLib.idle_add(self.phonefs_status_lbl.set_text, msg)
                else:
                    GLib.idle_add(
                        self.phonefs_status_lbl.set_text,
                        f"Phone NFS: {res.get('error') or 'Connect your phone to browse storage'}",
                    )
            finally:
                self._phonefs_attaching = False

        self._spawn(_do())

    def _on_usb_retry_clicked(self, *_args) -> None:
        async def _do():
            with contextlib.suppress(Exception):
                ifaces = [n for n in getattr(self.facade, "_noip_since", {}) or {}]
                for ifc in ifaces[:1]:
                    self.facade.usb_config_manager.retry_now(ifc)
            await self.facade.request("transport-retry")
            await self.facade.request("usb-scan")
        self._spawn(_do())

    def _on_open_linux_file_manager(self, *_args) -> None:
        self.facade.linux_mount_mgr.open_in_file_manager()

    def _on_phonefs_up_clicked(self, *_args) -> None:
        cur_p = getattr(self, "_displayed_phonefs_path", None) or getattr(self, "_current_phonefs_path", "/") or "/"
        if cur_p and cur_p != "/":
            parent = "/" + cur_p.strip("/").rpartition("/")[0]
            self._refresh_files_page(target_path=parent)

    def _refresh_files_page(self, target_path: str | None = None) -> None:
        m_st = self.facade.linux_mount_mgr.status_dict()
        m_path = m_st.get("mount_path") or str(default_mount_base() / self.facade._best_device_name())
        target_p = str(target_path or getattr(self, "_current_phonefs_path", "/") or "/")
        nav_gen = int(getattr(self, "_phonefs_nav_gen", 0)) + 1
        self._phonefs_nav_gen = nav_gen
        if hasattr(self.facade, "get_cached_phonefs_ls"):
            cached = self.facade.get_cached_phonefs_ls(target_p)
            if cached is not None:
                self._populate_phonefs_tree(cached, nav_gen)
        if self.facade.phonefs.get("state") == "ready":
            if not getattr(self, "_phonefs_attaching", False):
                disp_p = "/sdcard" if target_p == "/" else f"/sdcard{target_p}"
                self.phonefs_status_lbl.set_text(f"Loading {disp_p}…  ·  Attached at {m_path}")
            async def _ls(p_req: str, gen_req: int):
                r = await self.facade.phonefs_ls(p_req)
                GLib.idle_add(self._populate_phonefs_tree, r, gen_req)
            self._spawn(_ls(target_p, nav_gen))
        elif self.facade._live_ws() is not None and not getattr(self, "_phonefs_attaching", False):
            self._current_phonefs_path = target_p
            self._on_mount_phone_nfs_clicked()
        else:
            self.phonefs_status_lbl.set_text(f"Phone NFS Mount Folder: {m_path}")

    @staticmethod
    def _file_icon_for_entry(name: str, etype: str) -> str:
        if etype == "dir":
            low_n = name.lower()
            if low_n in ("dcim", "camera"):
                return "DIR"
            if low_n in ("pictures", "photos", "screenshots"):
                return "IMG"
            if low_n in ("download", "downloads"):
                return "DIR"
            if low_n in ("music", "audio", "podcasts", "ringtones"):
                return "AUD"
            if low_n in ("movies", "video", "videos"):
                return "VID"
            if low_n in ("documents", "docs"):
                return "DIR"
            return "DIR"
        ext = Path(name).suffix.lower()
        if ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".bmp", ".svg"):
            return "IMG"
        if ext in (".mp4", ".mkv", ".mov", ".webm", ".avi", ".3gp"):
            return "VID"
        if ext in (".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".opus"):
            return "AUD"
        if ext in (".zip", ".tar", ".gz", ".7z", ".rar", ".xz"):
            return "ZIP"
        if ext in (".apk", ".aab"):
            return "APK"
        if ext in (".pdf",):
            return "PDF"
        if ext in (".py", ".js", ".ts", ".json", ".html", ".css", ".sh", ".xml", ".yaml", ".yml"):
            return "BIN"
        return "FILE"

    def _on_phonefs_quick_path_clicked(self, target_path: str) -> None:
        self._refresh_files_page(target_path=str(target_path or "/"))

    def _row_remote_path(self, model, it, name: str) -> str:
        with contextlib.suppress(Exception):
            if model.get_n_columns() >= 5:
                rp = str(model.get_value(it, 4) or "").strip()
                if rp.startswith("/"):
                    return rp
        cur_p = getattr(self, "_displayed_phonefs_path", None) or getattr(self, "_current_phonefs_path", "/") or "/"
        return (cur_p.rstrip("/") + "/" + name) if cur_p != "/" else ("/" + name)

    def _get_selected_phonefs_entries(self) -> list[tuple[str, str, str]]:
        if getattr(self, "phonefs_tree", None) is None:
            return []
        sel = self.phonefs_tree.get_selection()
        if sel is None:
            return []
        try:
            model, rows = sel.get_selected_rows()
        except Exception:
            return []
        if model is None:
            return []
        out: list[tuple[str, str, str]] = []
        for rp in rows or []:
            it = model.get_iter(rp)
            if it is None:
                continue
            name = str(model.get_value(it, 1) or "")
            etype = str(model.get_value(it, 2) or "file")
            if not name:
                continue
            out.append((name, etype, self._row_remote_path(model, it, name)))
        return out

    def _get_selected_phonefs_entry(self) -> tuple[str, str, str] | None:
        entries = self._get_selected_phonefs_entries()
        return entries[0] if entries else None

    def _get_selected_phonefs_remote_paths(self) -> list[str]:
        return [rpath for _name, etype, rpath in self._get_selected_phonefs_entries()
                if etype != "dir" and rpath]

    def _on_phonefs_open_selected_clicked(self, *_args) -> None:
        item = self._get_selected_phonefs_entry()
        if item is None:
            self.phonefs_status_lbl.set_text("Select a file or folder in the list first, then click Open.")
            return
        name, etype, rpath = item
        if etype == "dir":
            self._refresh_files_page(target_path=rpath)
        else:
            self._open_phonefs_file_in_place(rpath, name)

    def _on_phonefs_download_selected_clicked(self, *_args) -> None:
        entries = self._get_selected_phonefs_entries()
        file_entries = [(name, rpath) for name, etype, rpath in entries if etype != "dir"]
        if not file_entries:
            if entries:
                self.phonefs_status_lbl.set_text("Double-click a folder to browse inside it, or select one or more files to download.")
            else:
                self.phonefs_status_lbl.set_text("Select one or more files in the list (Ctrl/Shift-click for multiple), then click Download.")
            return
        if len(file_entries) == 1:
            name, rpath = file_entries[0]
            self._download_phonefs_file_to_pc(rpath, name)
            return
        self._download_phonefs_files_to_pc([(rpath, name) for name, rpath in file_entries])

    def _download_phonefs_files_to_pc(self, items: list[tuple[str, str]]) -> None:
        total = len(items)
        dest_dir = self.facade.get_incoming_files_dir()
        self.phonefs_status_lbl.set_text(f"Downloading {total} files to {dest_dir}…")

        async def _dl_all():
            ok_cnt = 0
            last_err = ""
            for i, (rpath, name) in enumerate(items, 1):
                GLib.idle_add(
                    self.phonefs_status_lbl.set_text,
                    f"Downloading {i}/{total}: {name}…",
                )
                r = await self.facade.phonefs_download(rpath)
                if r.get("ok"):
                    ok_cnt += 1
                else:
                    last_err = str(r.get("error") or "unknown error")
            if ok_cnt == total:
                msg = f"Downloaded {ok_cnt}/{total} files → {dest_dir}"
            else:
                msg = f"Downloaded {ok_cnt}/{total} files"
                if last_err:
                    msg += f" (last error: {last_err})"
            GLib.idle_add(self.phonefs_status_lbl.set_text, msg)

        self._spawn(_dl_all())

    def _on_phonefs_upload_here_clicked(self, *_args) -> None:
        dlg = Gtk.FileChooserDialog(
            title=f"Select files to upload to phone ({self._current_phonefs_path})",
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
                self._upload_files_to_phonefs(files)

        open_dialog_nonblocking(dlg, on_ok=_picked)

    def _upload_files_to_phonefs(self, local_paths: list[str]) -> None:
        dest_dir = getattr(self, "_current_phonefs_path", "/") or "/"
        self.phonefs_status_lbl.set_text(f"Uploading {len(local_paths)} file(s) to phone {dest_dir}…")

        async def _do_up():
            ok_cnt = 0
            for lp in local_paths:
                r = await self.facade.phonefs_upload(lp, dest_dir)
                if r.get("ok"):
                    ok_cnt += 1
            GLib.idle_add(
                self.phonefs_status_lbl.set_text,
                f"Uploaded {ok_cnt}/{len(local_paths)} file(s) to /sdcard{'' if dest_dir == '/' else dest_dir}",
            )
            GLib.idle_add(self._refresh_files_page)

        self._spawn(_do_up())

    def _extract_dropped_paths(self, data, info=None) -> list:
        return extract_dropped_paths(data, info)

    def _on_phonefs_drop_received(self, _widget, context, _x, _y, data, info, time_val) -> None:
        paths = self._extract_dropped_paths(data, info)
        if paths:
            files_only = [str(p) for p in paths if Path(p).is_file()]
            if files_only:
                self._upload_files_to_phonefs(files_only)
        Gtk.drag_finish(context, bool(paths), False, time_val)

    def _on_phonefs_mkdir_clicked(self, *_args) -> None:
        cur_p = getattr(self, "_current_phonefs_path", "/") or "/"
        dlg = Gtk.Dialog(
            title="Create New Folder on Phone",
            transient_for=self.win,
            flags=Gtk.DialogFlags.DESTROY_WITH_PARENT,
        )
        dlg.set_default_size(380, 140)
        box = dlg.get_content_area()
        box.set_spacing(8)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(14)
        box.set_margin_end(14)
        lbl = Gtk.Label(label=f"Create folder inside /sdcard{'' if cur_p == '/' else cur_p}:", xalign=0.0)
        box.pack_start(lbl, False, False, 0)
        ent = Gtk.Entry()
        ent.set_placeholder_text("New folder name…")
        box.pack_start(ent, False, False, 0)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        ok_b = Gtk.Button(label="Create Folder")
        ok_b.get_style_context().add_class("primary-btn")
        can_b = Gtk.Button(label="Cancel")
        can_b.get_style_context().add_class("secondary-btn")
        row.pack_end(ok_b, False, False, 0)
        row.pack_end(can_b, False, False, 0)
        box.pack_start(row, False, False, 0)

        def _submit(*_a):
            fname = (ent.get_text() or "").strip().strip("/")
            dlg.destroy()
            if not fname:
                return
            target = (cur_p.rstrip("/") + "/" + fname) if cur_p != "/" else ("/" + fname)
            self.phonefs_status_lbl.set_text(f"Creating folder {target}…")

            async def _do_mk():
                r = await self.facade.phonefs_mkdir(target)
                if r.get("ok"):
                    GLib.idle_add(self.phonefs_status_lbl.set_text, f"Created folder {target}")
                    GLib.idle_add(self._refresh_files_page)
                else:
                    GLib.idle_add(self.phonefs_status_lbl.set_text, f"Create folder failed: {r.get('error')}")

            self._spawn(_do_mk())

        ok_b.connect("clicked", _submit)
        ent.connect("activate", _submit)
        can_b.connect("clicked", lambda *_: dlg.destroy())
        dlg.show_all()

    def _on_phonefs_rename_selected_clicked(self, *_args) -> None:
        item = self._get_selected_phonefs_entry()
        if item is None:
            self.phonefs_status_lbl.set_text("Select a file or folder in the list first, then click Rename.")
            return
        name, _etype, rpath = item
        self._prompt_rename_phone_item(rpath, name, is_gallery=False)

    def _on_phonefs_delete_selected_clicked(self, *_args) -> None:
        item = self._get_selected_phonefs_entry()
        if item is None:
            self.phonefs_status_lbl.set_text("Select a file or folder in the list first, then click Delete.")
            return
        name, _etype, rpath = item
        self._delete_phone_item(rpath, name, is_gallery=False)

    def _prompt_rename_phone_item(self, remote_path: str, old_name: str, is_gallery: bool = False) -> None:
        dlg = Gtk.Dialog(
            title=f"Rename {old_name}",
            transient_for=self.win,
            flags=Gtk.DialogFlags.DESTROY_WITH_PARENT,
        )
        dlg.set_default_size(400, 140)
        box = dlg.get_content_area()
        box.set_spacing(8)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(14)
        box.set_margin_end(14)
        lbl = Gtk.Label(label=f"New name for {old_name}:", xalign=0.0)
        box.pack_start(lbl, False, False, 0)
        ent = Gtk.Entry()
        ent.set_text(old_name)
        box.pack_start(ent, False, False, 0)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        ok_b = Gtk.Button(label="Rename")
        ok_b.get_style_context().add_class("primary-btn")
        can_b = Gtk.Button(label="Cancel")
        can_b.get_style_context().add_class("secondary-btn")
        row.pack_end(ok_b, False, False, 0)
        row.pack_end(can_b, False, False, 0)
        box.pack_start(row, False, False, 0)

        def _submit(*_a):
            new_n = (ent.get_text() or "").strip()
            dlg.destroy()
            if not new_n or new_n == old_name:
                return

            async def _do_ren():
                r = await self.facade.phonefs_rename(remote_path, new_n)
                target_lbl = self.gallery_status_lbl if is_gallery else self.phonefs_status_lbl
                if r.get("ok"):
                    GLib.idle_add(target_lbl.set_text, f"Renamed {old_name} → {new_n}")
                    if is_gallery:
                        GLib.idle_add(self._refresh_gallery_page, True)
                    else:
                        GLib.idle_add(self._refresh_files_page)
                else:
                    GLib.idle_add(target_lbl.set_text, f"Rename failed: {r.get('error')}")

            self._spawn(_do_ren())

        ok_b.connect("clicked", _submit)
        ent.connect("activate", _submit)
        can_b.connect("clicked", lambda *_: dlg.destroy())
        dlg.show_all()

    def _delete_phone_item(self, remote_path: str, name: str, is_gallery: bool = False) -> None:
        target_lbl = self.gallery_status_lbl if is_gallery else self.phonefs_status_lbl
        target_lbl.set_text(f"Deleting {name} from phone…")

        async def _do_del():
            r = await self.facade.phonefs_delete(remote_path)
            if r.get("ok"):
                GLib.idle_add(target_lbl.set_text, f"Deleted {name} from phone")
                if is_gallery:
                    GLib.idle_add(self._refresh_gallery_page, True)
                else:
                    GLib.idle_add(self._refresh_files_page)
            else:
                GLib.idle_add(target_lbl.set_text, f"Delete failed for {name}: {r.get('error')}")

        self._spawn(_do_del())

    def _on_phonefs_tree_button_press(self, tree: Gtk.TreeView, event) -> bool:
        hit = tree.get_path_at_pos(int(event.x), int(event.y))
        if hit is not None:
            path, col, _cx, _cy = hit
            sel = tree.get_selection()
            if sel is not None and getattr(event, "button", 0) == 3:
                try:
                    already = sel.path_is_selected(path)
                except Exception:
                    already = False
                if not already:
                    sel.unselect_all()
                    sel.select_path(path)
            model = tree.get_model()
            it = model.get_iter(path) if model is not None else None
            if it is not None:
                name = str(model.get_value(it, 1) or "")
                etype = str(model.get_value(it, 2) or "file")
                rpath = self._row_remote_path(model, it, name)
                if getattr(event, "button", 0) == 3:
                    menu = Gtk.Menu()
                    if etype == "dir":
                        mi_open = Gtk.MenuItem(label=f"Open Folder {name}")
                        mi_open.connect("activate", lambda *_: self._on_phonefs_row_activated(tree, path, col))
                        menu.append(mi_open)
                    else:
                        mi_open_f = Gtk.MenuItem(label=f"Open {name} (Direct NFS)")
                        mi_open_f.connect("activate", lambda *_, rp=rpath, nm=name: self._open_phonefs_file_in_place(rp, nm))
                        menu.append(mi_open_f)
                        mi_dl = Gtk.MenuItem(label=f"Download {name} to PC")
                        mi_dl.connect("activate", lambda *_, rp=rpath, nm=name: self._download_phonefs_file_to_pc(rp, nm))
                        menu.append(mi_dl)

                    mi_up = Gtk.MenuItem(label="Upload Files Here...")
                    mi_up.connect("activate", self._on_phonefs_upload_here_clicked)
                    menu.append(mi_up)

                    mi_mk = Gtk.MenuItem(label="New Folder...")
                    mi_mk.connect("activate", self._on_phonefs_mkdir_clicked)
                    menu.append(mi_mk)

                    mi_ren = Gtk.MenuItem(label=f"Rename {name}...")
                    mi_ren.connect("activate", lambda *_, rp=rpath, nm=name: self._prompt_rename_phone_item(rp, nm, is_gallery=False))
                    menu.append(mi_ren)

                    mi_del = Gtk.MenuItem(label=f"Delete {name} from Phone")
                    mi_del.connect("activate", lambda *_, rp=rpath, nm=name: self._delete_phone_item(rp, nm, is_gallery=False))
                    menu.append(mi_del)

                    menu.show_all()
                    menu.popup_at_pointer(event)
                    return True
        return False

    def _open_phonefs_file_in_place(self, remote_path: str, name: str, is_gallery: bool = False) -> None:
        target_lbl = self.gallery_status_lbl if is_gallery else self.phonefs_status_lbl
        target_lbl.set_text(f"Opening {name} from Phone NFS…")

        async def _do_open():
            r = await self.facade.phonefs_open(remote_path, launch_app=True)
            if r.get("ok"):
                opened_at = str(r.get("opened") or "")
                mode = str(r.get("mode") or "nfs")
                GLib.idle_add(
                    target_lbl.set_text,
                    f"Opened {name} ({mode}: {opened_at})",
                )
            else:
                GLib.idle_add(
                    target_lbl.set_text,
                    f"Open failed for {name}: {r.get('error')}",
                )

        self._spawn(_do_open())

    def _download_phonefs_file_to_pc(self, remote_path: str, name: str) -> None:
        self.phonefs_status_lbl.set_text(f"Downloading {name} to {self.facade.get_incoming_files_dir()}…")

        async def _dl():
            r = await self.facade.phonefs_download(remote_path)
            if r.get("ok"):
                GLib.idle_add(
                    self.phonefs_status_lbl.set_text,
                    f"Downloaded {name} → {r.get('saved')}",
                )
            else:
                GLib.idle_add(
                    self.phonefs_status_lbl.set_text,
                    f"Download failed for {name}: {r.get('error')}",
                )

        self._spawn(_dl())

    def _download_and_open_phonefs_file(self, remote_path: str, name: str) -> None:
        self._open_phonefs_file_in_place(remote_path, name)

    def _populate_phonefs_tree(self, ls_res: dict, nav_gen: int | None = None) -> bool:
        if nav_gen is not None and nav_gen != getattr(self, "_phonefs_nav_gen", nav_gen):
            return False
        if not ls_res.get("ok"):
            err_txt = str(ls_res.get("error") or "Unable to list folder")
            self.phonefs_status_lbl.set_text(f"Phone NFS: {err_txt}")
            return False
        p = ls_res.get("path") or "/"
        self._current_phonefs_path = p
        self._displayed_phonefs_path = p
        self.phonefs_path_lbl.set_text(f"Phone path: /sdcard{'' if p == '/' else p}  ·  Saved downloads go to {self.facade.get_incoming_files_dir()}")
        entries = list(ls_res.get("entries") or [])
        self.phonefs_store.clear()
        for ent in entries:
            etype = ent.get("type", "file")
            ename = str(ent.get("name") or "")
            ico = self._file_icon_for_entry(ename, etype)
            sz = int(ent.get("size") or 0)
            sz_str = "Folder" if etype == "dir" else (f"{sz / (1024*1024):.1f} MB" if sz >= 1024*1024 else f"{max(1, sz // 1024)} KB")
            rpath = str(ent.get("remote_path") or ent.get("path") or ((p.rstrip("/") + "/" + ename) if p != "/" else ("/" + ename)))
            if self.phonefs_store.get_n_columns() >= 5:
                self.phonefs_store.append([ico, ename, etype, sz_str, rpath])
            else:
                self.phonefs_store.append([ico, ename, etype, sz_str])
        if not getattr(self, "_phonefs_attaching", False) and not ls_res.get("cached"):
            m_st = self.facade.linux_mount_mgr.status_dict()
            m_path = m_st.get("mount_path") or str(default_mount_base() / self.facade._best_device_name())
            disp_p = "/sdcard" if p == "/" else f"/sdcard{p}"
            self.phonefs_status_lbl.set_text(f"Phone NFS: {disp_p} ({len(entries)} items)  ·  Attached at {m_path}")
        return False

    def _on_phonefs_row_activated(self, tree: Gtk.TreeView, path: Gtk.TreePath, _col) -> None:
        model = tree.get_model()
        it = model.get_iter(path)
        if not it:
            return
        name = str(model.get_value(it, 1) or "")
        etype = str(model.get_value(it, 2) or "file")
        target = self._row_remote_path(model, it, name)
        if etype == "dir":
            self._refresh_files_page(target_path=target)
        else:
            self._open_phonefs_file_in_place(target, name)

FilesPage = FilesPageMixin
