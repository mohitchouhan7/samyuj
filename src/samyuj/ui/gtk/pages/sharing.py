from __future__ import annotations

import asyncio
import contextlib
import time
import urllib.parse
from pathlib import Path

from samyuj.ui.gtk.gtk_compat import Gdk, GdkPixbuf, GLib, Gtk, Pango

from samyuj.ui.gtk.assets import (
    load_concept_pixbuf,
    make_cairo_icon,
    open_dialog_nonblocking,
)

class SharingPageMixin:

    def _build_sharing_page(self) -> Gtk.Widget:
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)

        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        h1 = Gtk.Label(label="Share", xalign=0.0)
        h1.get_style_context().add_class("page-h1")
        self.sharing_sub_lbl = Gtk.Label(
            label=f"Drag and drop multiple files or folders here to share them immediately with your {self.facade._best_device_name()}.",
            xalign=0.0,
        )
        self.sharing_sub_lbl.get_style_context().add_class("page-sub")
        head.pack_start(h1, False, False, 0)
        head.pack_start(self.sharing_sub_lbl, False, False, 0)
        vbox.pack_start(head, False, False, 0)

        drop_eb = Gtk.EventBox()
        drop_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        drop_box.get_style_context().add_class("dropzone")
        drop_box.set_halign(Gtk.Align.FILL)
        drop_eb.add(drop_box)

        ico_wrap = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        ico_wrap.set_halign(Gtk.Align.CENTER)
        pb_sh = load_concept_pixbuf("sharing", 64, 56)
        ico_wrap.pack_start(
            Gtk.Image.new_from_pixbuf(pb_sh) if pb_sh else make_cairo_icon("sharing", 48, (0.15, 0.50, 0.87)),
            False, False, 0,
        )
        drop_box.pack_start(ico_wrap, False, False, 0)

        dz_title = Gtk.Label(label="Drag and drop files or folders here")
        dz_title.get_style_context().add_class("dropzone-title")
        drop_box.pack_start(dz_title, False, False, 0)

        dz_sub = Gtk.Label(label="Drop multiple files to send them to your phone, or drop folders to mount them via NFS")
        dz_sub.get_style_context().add_class("dropzone-sub")
        dz_sub.set_line_wrap(True)
        drop_box.pack_start(dz_sub, False, False, 0)

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btn_row.set_halign(Gtk.Align.CENTER)
        choose_files_btn = Gtk.Button(label="Choose files…")
        choose_files_btn.get_style_context().add_class("primary-btn")
        choose_files_btn.connect("clicked", lambda *_: self._choose_and_send_files())
        btn_row.pack_start(choose_files_btn, False, False, 0)

        choose_folder_btn = Gtk.Button(label="Choose folder…")
        choose_folder_btn.get_style_context().add_class("secondary-btn")
        choose_folder_btn.connect("clicked", lambda *_: self._choose_and_share_folders())
        btn_row.pack_start(choose_folder_btn, False, False, 0)
        drop_box.pack_start(btn_row, False, False, 4)

        targets = [
            Gtk.TargetEntry.new("text/uri-list", 0, 1),
            Gtk.TargetEntry.new("STRING", 0, 2),
            Gtk.TargetEntry.new("text/plain", 0, 3),
        ]
        drop_eb.drag_dest_set(Gtk.DestDefaults.ALL, targets, Gdk.DragAction.COPY)
        drop_eb.connect("drag-data-received", self._on_share_drag_data_received)
        vbox.pack_start(drop_eb, False, False, 0)

        sh_hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        sh_h2 = Gtk.Label(label="Shared folders & files", xalign=0.0)
        sh_h2.get_style_context().add_class("panel-h2")
        self.folder_count_lbl = Gtk.Label(label="0 items", xalign=1.0)
        self.folder_count_lbl.get_style_context().add_class("panel-sub")
        sh_hdr.pack_start(sh_h2, True, True, 0)
        sh_hdr.pack_end(self.folder_count_lbl, False, False, 0)
        vbox.pack_start(sh_hdr, False, False, 2)

        list_scroll = Gtk.ScrolledWindow()
        with contextlib.suppress(Exception):
            from samyuj.ui.gtk.smooth_scroll import attach_smooth_scroll
            attach_smooth_scroll(list_scroll)
        list_scroll.set_vexpand(True)
        self.shared_list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.shared_list_box.get_style_context().add_class("sc-card")
        list_scroll.add(self.shared_list_box)
        vbox.pack_start(list_scroll, True, True, 0)

        return vbox

    def _on_share_drag_data_received(self, _widget, _drag_ctx, _x, _y, sel_data, _info, _time) -> None:
        uris: list[str] = list(sel_data.get_uris() or [])
        if not uris:
            raw_txt = sel_data.get_text() or ""
            uris = [ln.strip() for ln in raw_txt.splitlines() if ln.strip() and not ln.startswith("#")]
        paths: list[Path] = []
        for u in uris:
            if u.startswith("file://"):
                parsed = urllib.parse.unquote(urllib.parse.urlsplit(u).path)
                p = Path(parsed)
            else:
                p = Path(u).expanduser()
            if p.exists():
                paths.append(p.resolve())
        if paths:
            self.handle_shared_paths(paths)

    def handle_shared_paths(self, paths: list[Path | str]) -> dict:
        dirs: list[Path] = []
        files: list[Path] = []
        for raw_p in paths:
            p = Path(raw_p).expanduser().resolve()
            if p.is_dir():
                dirs.append(p)
            elif p.is_file():
                files.append(p)

        for d in dirs:
            self._spawn(self.facade.nfs_add_share(d.name, str(d), access_mode="rw", reannounce=True))

        if files:
            for f in files:
                with contextlib.suppress(OSError):
                    dst = self.facade.export_dir / f.name
                    if dst.resolve() != f.resolve():
                        import shutil as _sh
                        if f.stat().st_size <= 256 * 1024:
                            _sh.copy2(f, dst)
                        else:
                            self._spawn(asyncio.to_thread(_sh.copy2, f, dst))
                self._shared_Custom_files.append({
                    "name": f.name,
                    "path": str(f),
                    "size": f.stat().st_size,
                    "ts": time.time(),
                })
            if hasattr(self.facade, "send_files_svc2"):
                self._spawn(self.facade.send_files_svc2([str(f) for f in files]))

        GLib.timeout_add(150, lambda: (self._refresh_shares_list(force=True), False)[1])
        return {"ok": True, "dirs": [str(d) for d in dirs], "files": [str(f) for f in files]}

    def _choose_and_send_files(self) -> None:
        dlg = Gtk.FileChooserDialog(
            title="Select one or more files to share with your phone",
            parent=self.win,
            action=Gtk.FileChooserAction.OPEN,
        )
        dlg.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OPEN, Gtk.ResponseType.OK,
        )
        dlg.set_select_multiple(True)
        open_dialog_nonblocking(
            dlg, on_ok=lambda d: self.handle_shared_paths(
                [Path(fn) for fn in (d.get_filenames() or [])])
            if d.get_filenames() else None)

    def _choose_and_share_folders(self) -> None:
        dlg = Gtk.FileChooserDialog(
            title="Select folder(s) to share with your phone",
            parent=self.win,
            action=Gtk.FileChooserAction.SELECT_FOLDER,
        )
        dlg.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OPEN, Gtk.ResponseType.OK,
        )
        dlg.set_select_multiple(True)
        open_dialog_nonblocking(
            dlg, on_ok=lambda d: self.handle_shared_paths(
                [Path(fn) for fn in (d.get_filenames() or [])])
            if d.get_filenames() else None)

    def _refresh_shares_list(self, force: bool = False) -> None:
        shares = self.facade.nfs_list_shares().get("shares", []) if hasattr(self.facade, "nfs_list_shares") else []
        sig = (tuple((s.get("alias"), s.get("host_path"), s.get("access_mode")) for s in shares),
               tuple(f["path"] for f in self._shared_Custom_files))
        if not force and sig == self._last_shares_sig:
            return
        self._last_shares_sig = sig

        for ch in list(self.shared_list_box.get_children()):
            self.shared_list_box.remove(ch)

        total_items = len(shares) + len(self._shared_Custom_files)
        self.folder_count_lbl.set_text(f"{total_items} item{'' if total_items == 1 else 's'}")

        for s in shares:
            alias = str(s.get("alias") or "")
            hpath = str(s.get("host_path") or "")
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            row.get_style_context().add_class("notif-row")
            row.pack_start(Gtk.Label(label="DIR"), False, False, 4)

            det = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            t_lbl = Gtk.Label(label=alias, xalign=0.0)
            t_lbl.get_style_context().add_class("notif-title")
            s_lbl = Gtk.Label(label=f"{hpath} · Read, write and delete", xalign=0.0)
            s_lbl.get_style_context().add_class("panel-sub")
            det.pack_start(t_lbl, False, False, 0)
            det.pack_start(s_lbl, False, False, 0)
            row.pack_start(det, True, True, 0)

            badge = Gtk.Label(label="Shared")
            badge.get_style_context().add_class("shared-badge")
            row.pack_start(badge, False, False, 4)

            rm_btn = Gtk.Button(label="x")
            rm_btn.get_style_context().add_class("corner-cancel-btn")
            rm_btn.connect("clicked", lambda *_, al=alias: self._remove_share_alias(al))
            row.pack_end(rm_btn, False, False, 0)
            self.shared_list_box.pack_start(row, False, False, 0)

        for f_item in list(self._shared_Custom_files):
            fname = f_item["name"]
            fpath = f_item["path"]
            fsize_kb = max(1, f_item["size"] // 1024)
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            row.get_style_context().add_class("notif-row")
            row.pack_start(Gtk.Label(label="FILE"), False, False, 4)

            det = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            t_lbl = Gtk.Label(label=fname, xalign=0.0)
            t_lbl.get_style_context().add_class("notif-title")
            s_lbl = Gtk.Label(label=f"{fpath} · {fsize_kb} KB", xalign=0.0)
            s_lbl.get_style_context().add_class("panel-sub")
            det.pack_start(t_lbl, False, False, 0)
            det.pack_start(s_lbl, False, False, 0)
            row.pack_start(det, True, True, 0)

            badge = Gtk.Label(label="Shared file")
            badge.get_style_context().add_class("sent-badge")
            row.pack_start(badge, False, False, 4)

            rm_btn = Gtk.Button(label="x")
            rm_btn.get_style_context().add_class("corner-cancel-btn")
            rm_btn.connect("clicked", lambda *_, fp=fpath: self._remove_shared_file(fp))
            row.pack_end(rm_btn, False, False, 0)
            self.shared_list_box.pack_start(row, False, False, 0)

        self.shared_list_box.show_all()
        if getattr(self, "files_nfs_shares_box", None) is not None:
            for ch in list(self.files_nfs_shares_box.get_children()):
                self.files_nfs_shares_box.remove(ch)
            for s in shares:
                alias = str(s.get("alias") or "")
                hpath = str(s.get("host_path") or "")
                mode = str(s.get("access_mode") or "rw").upper()
                frow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
                frow.get_style_context().add_class("notif-row")
                frow.pack_start(Gtk.Label(label="DIR"), False, False, 4)
                fdet = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
                ft = Gtk.Label(label=f"{alias}  →  {hpath}", xalign=0.0)
                ft.get_style_context().add_class("notif-title")
                fdet.pack_start(ft, False, False, 0)
                frow.pack_start(fdet, True, True, 0)
                fbadge = Gtk.Label(label=f"NFSv3 {mode}")
                fbadge.get_style_context().add_class("shared-badge")
                frow.pack_start(fbadge, False, False, 4)
                frm_btn = Gtk.Button(label="x")
                frm_btn.set_tooltip_text(f"Unexport {alias}")
                frm_btn.get_style_context().add_class("corner-cancel-btn")
                frm_btn.connect("clicked", lambda *_, al=alias: self._remove_share_alias(al))
                frow.pack_end(frm_btn, False, False, 0)
                self.files_nfs_shares_box.pack_start(frow, False, False, 0)
            self.files_nfs_shares_box.show_all()
        names_preview = [f"{s.get('alias')} ({s.get('host_path')})" for s in shares]
        self.files_shared_summary_lbl.set_text(
            ("Exported to phone via NFS: " + " · ".join(names_preview))
            if names_preview
            else "No PC folders exported via NFS yet — add a Linux directory path below."
        )

    def _on_add_nfs_path_clicked(self, *_args) -> None:
        raw_path = (self.nfs_export_path_entry.get_text() if getattr(self, "nfs_export_path_entry", None) else "").strip()
        raw_alias = (self.nfs_export_alias_entry.get_text() if getattr(self, "nfs_export_alias_entry", None) else "").strip()
        if not raw_path:
            self.files_shared_summary_lbl.set_text("Enter a valid Linux folder path first (or click Browse Folder…).")
            return
        p = Path(raw_path).expanduser()
        alias = raw_alias or p.name or "Share"

        async def _do():
            res = await self.facade.nfs_add_share(alias, str(p), access_mode="rw", reannounce=True)
            def _after():
                if res.get("ok"):
                    if getattr(self, "nfs_export_path_entry", None):
                        self.nfs_export_path_entry.set_text("")
                    if getattr(self, "nfs_export_alias_entry", None):
                        self.nfs_export_alias_entry.set_text("")
                    self._refresh_shares_list(force=True)
                else:
                    self.files_shared_summary_lbl.set_text(
                        f"Could not export NFS path: {res.get('error') or 'invalid directory'}"
                    )
                return False
            GLib.idle_add(_after)

        self._spawn(_do())

    def _remove_share_alias(self, alias: str) -> None:
        async def _do():
            await self.facade.nfs_remove_share(alias, reannounce=True)
            GLib.idle_add(self._refresh_shares_list, True)
        self._spawn(_do())

    def _remove_shared_file(self, fpath: str) -> None:
        self._shared_Custom_files = [x for x in self._shared_Custom_files if x["path"] != fpath]
        self._refresh_shares_list(force=True)

SharingPage = SharingPageMixin
