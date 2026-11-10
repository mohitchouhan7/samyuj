from __future__ import annotations

import asyncio
import contextlib
import shutil
import subprocess
import time
import urllib.parse
from pathlib import Path

from samyuj.ui.gtk.gtk_compat import Gdk, GLib, Gtk, Pango

from samyuj.ui.gtk.assets import (
    extract_dropped_paths,
    load_concept_pixbuf,
    make_cairo_icon,
    open_dialog_nonblocking,
)

class SharingPageMixin:

    def _build_sharing_page(self) -> Gtk.Widget:
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)

        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        eb = Gtk.Label(label="FILE SHARE (SVC-2)", xalign=0.0)
        eb.get_style_context().add_class("eyebrow")
        h1 = Gtk.Label(label="Share", xalign=0.0)
        h1.get_style_context().add_class("page-h1")
        self.sharing_sub_lbl = Gtk.Label(
            label=f"Drag and drop multiple files or folders here to share them immediately with your {self.facade._best_device_name()}.",
            xalign=0.0,
        )
        self.sharing_sub_lbl.get_style_context().add_class("page-sub")
        self.sharing_sub_lbl.set_line_wrap(True)
        head.pack_start(eb, False, False, 0)
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

        dz_title = Gtk.Label(label="Drag and drop files or folders here to send")
        dz_title.get_style_context().add_class("dropzone-title")
        drop_box.pack_start(dz_title, False, False, 0)

        dz_sub = Gtk.Label(
            label="Sends files directly to your phone's Download folder via Motorola Smart Connect file transfer (svc-2)"
        )
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

        open_recv_btn = Gtk.Button(label="Open Received Folder")
        open_recv_btn.get_style_context().add_class("secondary-btn")
        open_recv_btn.connect("clicked", self._on_open_received_folder_clicked)
        btn_row.pack_start(open_recv_btn, False, False, 0)
        drop_box.pack_start(btn_row, False, False, 4)

        self.sharing_status_lbl = Gtk.Label(
            label=f"Incoming phone files are saved to {self.facade.get_incoming_files_dir()}",
            xalign=0.5,
        )
        self.sharing_status_lbl.get_style_context().add_class("panel-sub")
        self.sharing_status_lbl.set_line_wrap(True)
        drop_box.pack_start(self.sharing_status_lbl, False, False, 2)

        targets = [
            Gtk.TargetEntry.new("text/uri-list", 0, 1),
            Gtk.TargetEntry.new("STRING", 0, 2),
            Gtk.TargetEntry.new("text/plain", 0, 3),
        ]
        drop_eb.drag_dest_set(Gtk.DestDefaults.ALL, targets, Gdk.DragAction.COPY)
        drop_eb.connect("drag-data-received", self._on_share_drag_data_received)
        vbox.pack_start(drop_eb, False, False, 0)

        sh_hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        sh_h2 = Gtk.Label(label="Recent file transfers (PC ↔ Phone)", xalign=0.0)
        sh_h2.get_style_context().add_class("panel-h2")
        self.folder_count_lbl = Gtk.Label(label="0 items", xalign=1.0)
        self.folder_count_lbl.get_style_context().add_class("panel-sub")
        clear_xfer_btn = Gtk.Button(label="Clear history")
        clear_xfer_btn.get_style_context().add_class("clear-btn")
        clear_xfer_btn.connect("clicked", self._on_clear_shared_files_clicked)
        sh_hdr.pack_start(sh_h2, True, True, 0)
        sh_hdr.pack_end(clear_xfer_btn, False, False, 0)
        sh_hdr.pack_end(self.folder_count_lbl, False, False, 4)
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

    def _on_open_received_folder_clicked(self, *_args) -> None:
        recv_dir = str(self.facade.get_incoming_files_dir())
        for cmd in (["xdg-open", recv_dir], ["gio", "open", recv_dir]):
            if shutil.which(cmd[0]):
                with contextlib.suppress(OSError):
                    subprocess.Popen(
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                    return

    def _on_clear_shared_files_clicked(self, *_args) -> None:
        self._shared_Custom_files.clear()
        if hasattr(self.facade, "recv_files") and isinstance(self.facade.recv_files, list):
            self.facade.recv_files.clear()
        self._refresh_shares_list(force=True)

    def _on_share_drag_data_received(self, _widget, drag_ctx, _x, _y, sel_data, info, time_val) -> None:
        extracted = extract_dropped_paths(sel_data, info)
        if not extracted:
            uris: list[str] = list(sel_data.get_uris() or [])
            if not uris:
                raw_txt = sel_data.get_text() or ""
                uris = [ln.strip() for ln in raw_txt.splitlines() if ln.strip() and not ln.startswith("#")]
            for u in uris:
                if u.startswith("file://"):
                    parsed = urllib.parse.unquote(urllib.parse.urlsplit(u).path)
                    p = Path(parsed)
                else:
                    p = Path(u).expanduser()
                if p.exists():
                    extracted.append(p.resolve())
        paths = [Path(p).expanduser().resolve() for p in extracted if Path(p).expanduser().exists()]
        if paths:
            self.handle_shared_paths(paths)
        with contextlib.suppress(Exception):
            Gtk.drag_finish(drag_ctx, bool(paths), False, time_val)

    def handle_shared_paths(self, paths: list[Path | str]) -> dict:
        dirs: list[Path] = []
        files: list[Path] = []
        for raw_p in paths:
            p = Path(raw_p).expanduser().resolve()
            if p.is_dir():
                dirs.append(p)
                files.extend(sorted(q for q in p.rglob("*") if q.is_file())[:200])
            elif p.is_file():
                files.append(p)

        seen_paths: set[str] = set()
        unique_files: list[Path] = []
        for f in files:
            sp = str(f)
            if sp not in seen_paths:
                seen_paths.add(sp)
                unique_files.append(f)

        if unique_files:
            now_ts = time.time()
            batch_records: list[dict] = []
            for idx_f, f in enumerate(unique_files):
                try:
                    sz = f.stat().st_size
                except OSError:
                    sz = 0
                rec = {
                    "name": f.name,
                    "path": str(f),
                    "size": sz,
                    "ts": now_ts,
                    "direction": "sent",
                    "task_id": "",
                    "file_index": idx_f,
                    "status": "Sending…",
                }
                self._shared_Custom_files.append(rec)
                batch_records.append(rec)

            async def _do_send_svc2(file_paths: list[str], records: list[dict]) -> None:
                c = self.facade._live_ws() if hasattr(self.facade, "_live_ws") else None
                task = None
                if hasattr(self.facade, "send_files_svc2"):
                    task = await self.facade.send_files_svc2(c, file_paths)
                if task is not None:
                    tid = str(getattr(task, "task_id", "") or "")
                    for r in records:
                        r["task_id"] = tid
                        r["status"] = "Sending to phone…"
                    if getattr(self, "sharing_status_lbl", None) is not None:
                        GLib.idle_add(
                            self.sharing_status_lbl.set_text,
                            f"Sending {len(file_paths)} file(s) ({getattr(task, 'size', 0) // 1024 or 1} KB) to {self.facade._best_device_name()}…",
                        )
                    GLib.idle_add(self._refresh_shares_list, True)
                    for _ in range(240):
                        await asyncio.sleep(0.25)
                        phase = str(getattr(task, "phase", "announced") or "announced")
                        if phase == "success":
                            for r in records:
                                r["status"] = "Sent to phone"
                            if getattr(self, "sharing_status_lbl", None) is not None:
                                GLib.idle_add(
                                    self.sharing_status_lbl.set_text,
                                    f"Sent {len(file_paths)} file(s) to {self.facade._best_device_name()} (/sdcard/Download)",
                                )
                            GLib.idle_add(self._refresh_shares_list, True)
                            return
                        if phase == "failed":
                            for r in records:
                                r["status"] = "Transfer failed"
                            if getattr(self, "sharing_status_lbl", None) is not None:
                                GLib.idle_add(
                                    self.sharing_status_lbl.set_text,
                                    f"Transfer failed for {len(file_paths)} file(s) — click Resend to try again",
                                )
                            GLib.idle_add(self._refresh_shares_list, True)
                            return
                    if str(getattr(task, "phase", "")) in ("announced", "negotiating", "request_sent", "downloading"):
                        for r in records:
                            r["status"] = "Sent / Ready on phone"
                        GLib.idle_add(self._refresh_shares_list, True)
                else:
                    for r in records:
                        r["status"] = "Queued (connect phone)"
                    if getattr(self, "sharing_status_lbl", None) is not None:
                        GLib.idle_add(
                            self.sharing_status_lbl.set_text,
                            "No phone connected yet — connect your phone and click Resend.",
                        )
                    GLib.idle_add(self._refresh_shares_list, True)

            self._spawn(_do_send_svc2([str(f) for f in unique_files], batch_records))

        GLib.timeout_add(100, lambda: (self._refresh_shares_list(force=True), False)[1])
        return {"ok": True, "dirs": [str(d) for d in dirs], "files": [str(f) for f in unique_files]}

    def _on_send_files_dialog(self, *_args) -> None:
        self._choose_and_send_files()

    def _choose_and_send_files(self) -> None:
        dlg = Gtk.FileChooserDialog(
            title="Select one or more files to send to your phone",
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
            title="Select folder(s) to send to your phone",
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

    def _resend_shared_file(self, fpath: str) -> None:
        p = Path(fpath).expanduser()
        if p.exists():
            self._shared_Custom_files = [x for x in self._shared_Custom_files if x.get("path") != fpath]
            self.handle_shared_paths([p])

    def _open_local_file_path(self, fpath: str) -> None:
        for cmd in (["xdg-open", fpath], ["gio", "open", fpath]):
            if shutil.which(cmd[0]):
                with contextlib.suppress(OSError):
                    subprocess.Popen(
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                    return

    def _refresh_shares_list(self, force: bool = False) -> None:
        shares = self.facade.nfs_list_shares().get("shares", []) if hasattr(self.facade, "nfs_list_shares") else []
        recv_list = list(getattr(self.facade, "recv_files", []) or [])
        xfer_tasks = getattr(self.facade, "xfer_tasks", {}) or {}
        for f_item in self._shared_Custom_files:
            tid = f_item.get("task_id")
            if tid and tid in xfer_tasks:
                t_obj = xfer_tasks[tid]
                ph = str(getattr(t_obj, "phase", "") or "")
                saved_p = getattr(t_obj, "saved_paths", {}) or {}
                f_idx = f_item.get("file_index")
                if ph == "success" or (f_idx is not None and f_idx in saved_p):
                    f_item["status"] = "Sent to phone"
                elif ph == "failed":
                    f_item["status"] = "Transfer failed"

        sig = (
            tuple((s.get("alias"), s.get("host_path"), s.get("access_mode")) for s in shares),
            tuple((f.get("path"), f.get("status")) for f in self._shared_Custom_files),
            tuple(recv_list[-25:]),
        )
        if not force and sig == self._last_shares_sig:
            return
        self._last_shares_sig = sig

        for ch in list(self.shared_list_box.get_children()):
            self.shared_list_box.remove(ch)

        total_items = len(self._shared_Custom_files) + len(recv_list)
        self.folder_count_lbl.set_text(f"{total_items} transfer{'' if total_items == 1 else 's'}")

        if getattr(self, "sharing_status_lbl", None) is not None:
            last_st = str(getattr(self.facade, "xfer_last_status", "") or "").strip()
            if last_st:
                self.sharing_status_lbl.set_text(last_st)

        if total_items == 0:
            empty_lbl = Gtk.Label(
                label="No files sent or received yet — drag files above or click 'Choose files…' to send to your phone.",
                xalign=0.0,
            )
            empty_lbl.set_line_wrap(True)
            empty_lbl.get_style_context().add_class("panel-sub")
            self.shared_list_box.pack_start(empty_lbl, False, False, 6)

        for f_item in reversed(list(self._shared_Custom_files)):
            fname = str(f_item.get("name") or "")
            fpath = str(f_item.get("path") or "")
            fsize_kb = max(1, int(f_item.get("size") or 0) // 1024)
            fstatus = str(f_item.get("status") or "Sent to phone")
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            row.get_style_context().add_class("notif-row")
            row.pack_start(Gtk.Label(label="SENT"), False, False, 4)

            det = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            t_lbl = Gtk.Label(label=fname, xalign=0.0)
            t_lbl.get_style_context().add_class("notif-title")
            t_lbl.set_ellipsize(Pango.EllipsizeMode.END)
            s_lbl = Gtk.Label(label=f"{fpath} · {fsize_kb} KB · PC → Phone", xalign=0.0)
            s_lbl.get_style_context().add_class("panel-sub")
            s_lbl.set_ellipsize(Pango.EllipsizeMode.END)
            det.pack_start(t_lbl, False, False, 0)
            det.pack_start(s_lbl, False, False, 0)
            row.pack_start(det, True, True, 0)

            badge = Gtk.Label(label=fstatus)
            badge.get_style_context().add_class("sent-badge")
            row.pack_start(badge, False, False, 4)

            resend_btn = Gtk.Button(label="Resend")
            resend_btn.get_style_context().add_class("secondary-btn")
            resend_btn.connect("clicked", lambda *_, fp=fpath: self._resend_shared_file(fp))
            row.pack_start(resend_btn, False, False, 2)

            rm_btn = Gtk.Button(label="x")
            rm_btn.get_style_context().add_class("corner-cancel-btn")
            rm_btn.connect("clicked", lambda *_, fp=fpath: self._remove_shared_file(fp))
            row.pack_end(rm_btn, False, False, 0)
            self.shared_list_box.pack_start(row, False, False, 0)

        for rpath_str in reversed(recv_list[-25:]):
            rp = Path(str(rpath_str))
            rname = rp.name
            try:
                rsize_kb = max(1, rp.stat().st_size // 1024) if rp.exists() else 1
            except OSError:
                rsize_kb = 1
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            row.get_style_context().add_class("notif-row")
            row.pack_start(Gtk.Label(label="RECV"), False, False, 4)

            det = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            t_lbl = Gtk.Label(label=rname, xalign=0.0)
            t_lbl.get_style_context().add_class("notif-title")
            t_lbl.set_ellipsize(Pango.EllipsizeMode.END)
            s_lbl = Gtk.Label(label=f"{rp} · {rsize_kb} KB · Phone → PC", xalign=0.0)
            s_lbl.get_style_context().add_class("panel-sub")
            s_lbl.set_ellipsize(Pango.EllipsizeMode.END)
            det.pack_start(t_lbl, False, False, 0)
            det.pack_start(s_lbl, False, False, 0)
            row.pack_start(det, True, True, 0)

            badge = Gtk.Label(label="Received")
            badge.get_style_context().add_class("shared-badge")
            row.pack_start(badge, False, False, 4)

            open_btn = Gtk.Button(label="Open")
            open_btn.get_style_context().add_class("secondary-btn")
            open_btn.connect("clicked", lambda *_, fp=str(rp): self._open_local_file_path(fp))
            row.pack_end(open_btn, False, False, 0)
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
        if getattr(self, "files_shared_summary_lbl", None) is not None:
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
