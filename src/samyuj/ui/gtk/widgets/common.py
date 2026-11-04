from __future__ import annotations

import asyncio
import contextlib
import time

from samyuj.ui.gtk.gtk_compat import Gdk, GdkPixbuf, GLib, Gtk, Pango

from samyuj.ui.gtk.assets import (
    make_cairo_icon,
    open_dialog_nonblocking,
    round_pixbuf_corners,
)
from samyuj.ui.gtk.windows import (
    CrossControlGtkWindow,
    FeatureStreamGtkWindow,
    VirtualDisplayPlacementDialog,
)

class DialogsAndClipboardMixin:

    async def _read_live_pc_clipboard_async(self) -> str:
        cs = getattr(self.facade, "clip_sync", None)
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()

        def _got(_cb, fetched_text, *_a):
            if isinstance(fetched_text, str):
                self._clip_cache = str(fetched_text)
                self._clip_cache_mono = time.monotonic()
            if not fut.done():
                fut.set_result(fetched_text if isinstance(fetched_text, str) else "")

        with contextlib.suppress(Exception):
            self._gtk_clipboard.request_text(_got)
        fetched = ""
        try:
            done, _ = await asyncio.wait({fut}, timeout=0.12)
            if done:
                fetched = fut.result() or ""
        except Exception:
            fetched = ""
        if not fetched and isinstance(self._clip_cache, str) and self._clip_cache:
            fetched = self._clip_cache
        if not fetched and cs is not None and hasattr(cs, "read_cli_clipboard_sync"):
            cli_val = await asyncio.to_thread(cs.read_cli_clipboard_sync)
            if isinstance(cli_val, str) and cli_val:
                fetched = cli_val
                self._clip_cache = str(cli_val)
                self._clip_cache_mono = time.monotonic()
        if not fetched:
            fetched = str(getattr(self.facade, "pc_clipboard_text", "") or "")
        return fetched

    def _sync_pc_clipboard_now(self, origin: str = "manual") -> None:
        async def _job():
            c = self.facade._live_ws()
            if c is None:
                return
            cs = getattr(self.facade, "clip_sync", None)
            if cs is None:
                return
            val = await self._read_live_pc_clipboard_async()
            if val:
                await cs.push_pc_text_to_phone(val, origin=origin)
        self._spawn(_job())

    async def _paste_pc_clipboard_and_send_key(
        self, conn: dict, key: int, pressed: bool,
        display_id: int = 0, meta_state: int = 0, flags: int = 4096
    ) -> None:
        cs = getattr(self.facade, "clip_sync", None)
        if cs is not None and cs.enabled:
            val = await self._read_live_pc_clipboard_async()
            if val and not cs.is_pc_writeback_echo(val):
                await cs.push_pc_text_to_phone(val, origin="ctrl-v")
                await asyncio.sleep(0.035)
        if hasattr(self.facade, "send_key_event"):
            await self.facade.send_key_event(
                conn, key, pressed, display_id=display_id,
                meta_state=meta_state, flags=flags, quiet=True)

    async def _paste_pc_clipboard_and_send_uc_key(
        self, conn: dict, key: int, pressed: bool, meta_state: int = 0
    ) -> None:
        cs = getattr(self.facade, "clip_sync", None)
        if cs is not None and cs.enabled:
            val = await self._read_live_pc_clipboard_async()
            if val and not cs.is_pc_writeback_echo(val):
                await cs.push_pc_text_to_phone(val, origin="uc-ctrl-v")
                await asyncio.sleep(0.035)
        await self.facade.send_uc_key(conn, key, pressed, meta_state=meta_state)

    def _open_smart_clipboard_dialog(self) -> None:
        existing = getattr(self, "_clip_dialog", None)
        if existing is not None:
            with contextlib.suppress(Exception):
                existing.present()
                return
        dlg = Gtk.Dialog(
            title="Smart Clipboard (PC ↔ Phone)",
            transient_for=self.win,
            flags=Gtk.DialogFlags.DESTROY_WITH_PARENT,
        )
        dlg.set_Modal(False) if hasattr(dlg, "set_Modal") else dlg.set_modal(False)
        dlg.set_default_size(520, 320)
        self._clip_dialog = dlg
        ca = dlg.get_content_area()
        ca.set_spacing(10)
        ca.set_margin_top(14)
        ca.set_margin_bottom(14)
        ca.set_margin_start(16)
        ca.set_margin_end(16)

        top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        top_lbl = Gtk.Label(label="Automatic two-way clipboard sync (PC ↔ Phone)", xalign=0.0)
        top_lbl.set_hexpand(True)
        sw = Gtk.Switch()
        sw.set_active(bool(self.facade.clip_sync.enabled))

        def _on_sw_notify(switch_w, _pspec):
            on = bool(switch_w.get_active())
            self.facade.clip_sync.set_enabled(on)
            self._refresh_all()

        sw.connect("notify::active", _on_sw_notify)
        top_row.pack_start(top_lbl, True, True, 0)
        top_row.pack_end(sw, False, False, 0)
        ca.pack_start(top_row, False, False, 0)

        sub = Gtk.Label(
            label="Text copied on your PC is pushed to your phone automatically (and before Ctrl+V). You can also type or paste below to push immediately:",
            xalign=0.0,
        )
        sub.set_line_wrap(True)
        sub.get_style_context().add_class("panel-sub")
        ca.pack_start(sub, False, False, 0)

        entry = Gtk.Entry()
        init_txt = str(self._clip_cache or getattr(self.facade, "pc_clipboard_text", "") or "")
        entry.set_text(init_txt)
        entry.set_placeholder_text("Enter or paste text to send to phone clipboard…")
        ca.pack_start(entry, False, False, 0)

        with contextlib.suppress(Exception):
            def _seed_entry(_c, fetched_val, *_a):
                if isinstance(fetched_val, str) and fetched_val and not entry.get_text():
                    entry.set_text(fetched_val)
            self._gtk_clipboard.request_text(_seed_entry)

        status_lbl = Gtk.Label(label="", xalign=0.0)
        status_lbl.get_style_context().add_class("panel-sub")
        st = self.facade.clip_sync.status()
        status_lbl.set_text(
            f"Backend: {st.get('backend')} · PC→Phone: {st.get('pc_to_phone')} · Phone→PC: {st.get('phone_to_pc')}"
        )
        ca.pack_start(status_lbl, False, False, 0)

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        push_btn = Gtk.Button(label="Push to Phone Now")
        push_btn.get_style_context().add_class("primary-btn")
        pull_btn = Gtk.Button(label="Pull from Phone")
        close_btn = Gtk.Button(label="Close")

        def _on_push(*_a):
            val = entry.get_text()
            if val:
                with contextlib.suppress(Exception):
                    self._clip_cache = str(val)
                    self._gtk_clipboard.set_text(val, -1)
                async def _do_push():
                    ok = await self.facade.clip_sync.push_pc_text_to_phone(val, origin="dialog-push")
                    GLib.idle_add(
                        status_lbl.set_text,
                        f"Pushed {len(val)} chars to phone!" if ok else "No live phone connection (pair/connect first).",
                    )
                self._spawn(_do_push())
            else:
                self._sync_pc_clipboard_now(origin="dialog-push")
                status_lbl.set_text("Pushed current PC clipboard to phone.")

        def _on_pull(*_a):
            c = self.facade._live_ws()
            if c is not None:
                async def _do_pull():
                    await self.facade.request("clipboard-pull", c)
                    await asyncio.sleep(0.35)
                    pulled = str(getattr(self.facade, "clip_last_text", "") or getattr(self.facade, "pc_clipboard_text", "") or "")
                    if pulled:
                        GLib.idle_add(entry.set_text, pulled)
                        GLib.idle_add(status_lbl.set_text, f"Pulled {len(pulled)} chars from phone.")
                self._spawn(_do_pull())
            else:
                status_lbl.set_text("No live phone connection.")

        push_btn.connect("clicked", _on_push)
        entry.connect("activate", _on_push)
        pull_btn.connect("clicked", _on_pull)
        close_btn.connect("clicked", lambda *_: dlg.destroy())
        btn_row.pack_start(push_btn, False, False, 0)
        btn_row.pack_start(pull_btn, False, False, 0)
        btn_row.pack_end(close_btn, False, False, 0)
        ca.pack_start(btn_row, False, False, 0)

        def _on_destroy(*_a):
            self._clip_dialog = None

        dlg.connect("destroy", _on_destroy)
        dlg.show_all()

    def _open_control_lock_dialog(self) -> None:
        dlg = Gtk.Dialog(title="PC Lock & Session Control (svc-22 & svc-19)", transient_for=self.win, flags=0)
        dlg.set_default_size(440, 240)
        box = dlg.get_content_area()
        box.set_spacing(10)
        box.set_margin_top(14)
        box.set_margin_bottom(14)
        box.set_margin_start(16)
        box.set_margin_end(16)
        cst = getattr(self.facade, "control_state", {}) or {}
        lbl = Gtk.Label(
            label=f"PC Lock State (svc-22 cmd:2): {'LOCKED' if cst.get('locked') else 'UNLOCKED'}",
            xalign=0.0,
        )
        box.pack_start(lbl, False, False, 0)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        b_lock = Gtk.Button(label="Announce PC Locked")
        b_lock.connect("clicked", lambda *_: self._spawn(self.facade.request("control-lock")))
        b_unlock = Gtk.Button(label="Announce PC Unlocked")
        b_unlock.connect("clicked", lambda *_: self._spawn(self.facade.request("control-unlock")))
        b_inv = Gtk.Button(label="Send Control Invite (cmd:1)")
        b_inv.connect("clicked", lambda *_: self._spawn(self.facade.request("control-invite:1")))
        row.pack_start(b_lock, False, False, 0)
        row.pack_start(b_unlock, False, False, 0)
        row.pack_start(b_inv, False, False, 0)
        box.pack_start(row, False, False, 0)
        close_btn = Gtk.Button(label="Close")
        close_btn.connect("clicked", lambda *_: dlg.destroy())
        box.pack_end(close_btn, False, False, 0)
        dlg.show_all()

    def _cross_control_disabled_dialog(self) -> None:
        if getattr(self, "_cc_disabled_dialog_open", False):
            return
        self._cc_disabled_dialog_open = True
        dlg = Gtk.MessageDialog(
            transient_for=self.win, modal=True,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text="Cross Control screen-edge capture is disabled",
        )
        dlg.format_secondary_text(
            "This feature hands your keyboard and mouse to the phone when you "
            "push the cursor into a display edge.\n\n"
            "It is DISABLED by default so it can never interfere with normal "
            "typing, Alt+Tab, or window switching.\n\n"
            "To turn it on: Settings → Display Cross Control & Wallpaper Sync → "
            "“Enable Cross Control screen-edge capture”. While captured, Esc / "
            "Alt+Tab / Super always return control to the PC.")

        def _closed(*_a):
            self._cc_disabled_dialog_open = False

        open_dialog_nonblocking(dlg, on_response=_closed)

    def _on_feature_clicked(self, tile_id: str) -> None:
        if tile_id == "gallery":
            self.switch_page("gallery")
            return
        if tile_id == "messages":
            self.switch_page("messages")
            self._spawn(self.facade.request("msg-sync"))
            return
        if tile_id == "control_lock":
            self._open_control_lock_dialog()
            return
        if tile_id == "files":
            self.switch_page("files")
            self._on_mount_phone_nfs_clicked()
            return
        if tile_id == "sharing":
            self.switch_page("sharing")
            return
        if tile_id == "clipboard":
            if not self.facade.clip_sync.enabled:
                self.facade.clip_sync.set_enabled(True)
            self._sync_pc_clipboard_now(origin="tile-click")
            self._open_smart_clipboard_dialog()
            self._refresh_all()
            return
        if tile_id == "crosscontrol":
            if not self.facade.user_settings.get("cross_control_edge_capture", False):
                self._cross_control_disabled_dialog()
                return
            if self.edge_controller.enabled or self.edge_controller.active_on_phone:
                self.edge_controller.user_disabled = True
                self.edge_controller.stop()
                c = self.facade._live_ws()
                if c:
                    self._spawn(self.facade.stop_cross_control(c))
                self._refresh_all()
                return
            c = self.facade._live_ws()
            if c:
                self._spawn(self.facade.start_cross_control(c, trigger=1))
            self.edge_controller.user_disabled = False
            self.edge_controller.start()
            self._refresh_all()
            return
        if tile_id == "desktop":
            self._sync_mobile_desktop_setup_ui()
            self.switch_page("setup_desktop")
            return
        if tile_id == "screenshare":
            self._pc_ss_options = [("extend", "Virtual Display (2nd monitor)…"),
                                   ("mirror", "Mirror PC Display")]
            self.switch_page("setup_screenshare_hub")
            return
        if tile_id == "share_pc_screen":
            self._on_pc_ss_mode_selected("mirror")
            self.switch_page("setup_pc_screenshare")
            return
        if tile_id == "share_phone_screen":
            self._on_phone_ss_mode_selected(getattr(self, "_phone_on_pc_mode", "mirror"))
            self.switch_page("setup_phone_screenshare")
            return
        if tile_id == "phone_on_pc":
            self._start_phone_on_pc("mirror")
            return
        if tile_id == "webcam":
            self.open_feature_window("webcam", start_session=True)
            return
        if tile_id == "appstream":
            if hasattr(self, "open_app_launcher_window"):
                self.open_app_launcher_window()
            else:
                self.switch_page("apps")
            self._spawn(self.facade.request("applist"))

    def _show_tile_option_dialog(self, title: str, subtitle: str, options: list[tuple[str, str]], callback) -> None:
        dlg = Gtk.Dialog(title=title, transient_for=self.win, flags=0)
        dlg.set_default_size(420, 220)
        box = dlg.get_content_area()
        box.set_spacing(12)
        box.set_margin_top(16)
        box.set_margin_bottom(16)
        box.set_margin_start(16)
        box.set_margin_end(16)

        sub = Gtk.Label(label=subtitle, xalign=0.0)
        sub.get_style_context().add_class("panel-sub")
        box.pack_start(sub, False, False, 0)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        for opt_key, opt_label in options:
            btn = Gtk.Button()
            btn.get_style_context().add_class("secondary-btn")
            b_in = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            b_in.set_margin_top(8)
            b_in.set_margin_bottom(8)
            b_in.pack_start(make_cairo_icon(opt_key, 36), False, False, 0)
            b_in.pack_start(Gtk.Label(label=opt_label), False, False, 0)
            btn.add(b_in)
            btn.connect("clicked", lambda *_, k=opt_key: (dlg.destroy(), callback(k)))
            row.pack_start(btn, True, True, 0)
        box.pack_start(row, True, True, 0)
        dlg.show_all()

    def _start_mobile_desktop(self, orientation: str = "landscape") -> None:
        ori_str = str(orientation or "landscape").strip().lower()
        if "x" in ori_str:
            try:
                rw, rh = (max(64, int(x) & ~1) for x in ori_str.split("x", 1))
                self.facade.desktop_res = (rw, rh)
                self.facade.desktop_rotation = 1 if rh > rw else 0
            except Exception:
                self.facade.desktop_res = (1920, 1080)
                self.facade.desktop_rotation = 0
        elif ori_str == "portrait":
            self.facade.desktop_res = (1080, 1920)
            self.facade.desktop_rotation = 1
        else:
            self.facade.desktop_res = (1920, 1080)
            self.facade.desktop_rotation = 0
        with contextlib.suppress(Exception):
            self.facade.save_user_settings(desktop_res=f"{self.facade.desktop_res[0]}x{self.facade.desktop_res[1]}")
        self._closed_session_keys.pop("desktop", None)
        self._feature_stop_cooldown.pop("desktop", None)
        other_win = self.feature_windows.get("phone_on_pc")
        if other_win is not None and other_win.get_visible():
            other_win.hide()
        existing = self.feature_windows.get("desktop")
        if isinstance(existing, FeatureStreamGtkWindow):
            existing._pixbuf = None
            existing._last_pts = None
            existing._last_frame_dims = None
            existing._last_adapted_aspect = None
            existing.stream_session_id = None
        win = self.open_feature_window("desktop", start_session=True)
        if isinstance(win, FeatureStreamGtkWindow):
            win.sync_orientation_and_controls(force_resize=True)

    def _start_phone_on_pc(self, mode: str = "mirror") -> None:
        self._phone_on_pc_mode = "mobile" if str(mode).lower() in ("mobile", "virtual", "3") else "mirror"
        self._selected_phone_ss_mode = self._phone_on_pc_mode
        self._closed_session_keys.pop("phone_on_pc", None)
        self._feature_stop_cooldown.pop("phone_on_pc", None)
        other_win = self.feature_windows.get("desktop")
        if other_win is not None and other_win.get_visible():
            other_win.hide()
        existing = self.feature_windows.get("phone_on_pc")
        if isinstance(existing, FeatureStreamGtkWindow):
            existing._pixbuf = None
            existing._last_pts = None
            existing._last_frame_dims = None
            existing._last_adapted_aspect = None
            existing.stream_session_id = None
        win = self.open_feature_window("phone_on_pc", start_session=True)
        if isinstance(win, FeatureStreamGtkWindow):
            win.sync_orientation_and_controls(force_resize=True)

    def _start_screenshare(self, mode: str = "mirror") -> None:
        mode = str(mode or "extend").lower()
        if mode.startswith("mirror"):
            self.facade.ss_share_type = "mirror"
            with contextlib.suppress(Exception):
                self.facade.save_user_settings(ss_share_type="mirror")
            self._closed_session_keys.pop("screenshare", None)
            self._feature_stop_cooldown.pop("screenshare", None)
            self.open_feature_window("screenshare", start_session=True)
            return
        if mode.endswith("portrait"):
            with contextlib.suppress(Exception):
                self.facade.set_ss_virtual(orient="portrait")
        elif mode.endswith("landscape"):
            with contextlib.suppress(Exception):
                self.facade.set_ss_virtual(orient="landscape")
        if mode == "extend" and not mode.endswith(("portrait", "landscape")):
            VirtualDisplayPlacementDialog(self).open_nonblocking(
                self._on_placement_chosen)
            return
        self._begin_extend_session(send_window=False)

    def _on_placement_chosen(self, choice: dict | None) -> None:
        if not choice:
            return
        with contextlib.suppress(Exception):
            self.facade.set_ss_virtual(orient=choice["orient"], res=choice["size"],
                                  pos=choice["edge"])
        self._begin_extend_session(send_window=bool(choice.get("send_window")))

    def _begin_extend_session(self, send_window: bool = False) -> None:
        self.facade.ss_share_type = "extend"
        with contextlib.suppress(Exception):
            self.facade.save_user_settings(
                ss_share_type="extend",
                ss_virtual_orient=getattr(self.facade, "ss_virtual_orient", "auto"),
                ss_virtual_pos=getattr(self.facade, "ss_virtual_pos", "right"))
        self._closed_session_keys.pop("screenshare", None)
        self._feature_stop_cooldown.pop("screenshare", None)
        self.open_feature_window("screenshare", start_session=True)
        if send_window:
            self._spawn(self._send_window_after_extend())

    async def _send_window_after_extend(self) -> None:
        await asyncio.sleep(2.0)
        res = {}
        with contextlib.suppress(Exception):
            res = await asyncio.to_thread(self.facade.move_window_to_virtual_display)
        with contextlib.suppress(Exception):
            self.facade.events.log("SS_SEND_WINDOW",
                              f"ok={res.get('ok')} {res.get('detail')}",
                              feature="screen_share")
        if not res.get("ok"):
            GLib.idle_add(self._toast_send_window_failed, str(res.get("detail") or ""))

    def _toast_send_window_failed(self, detail: str) -> None:
        with contextlib.suppress(Exception):
            dlg = Gtk.MessageDialog(transient_for=self.win, modal=True,
                                    message_type=Gtk.MessageType.WARNING,
                                    buttons=Gtk.ButtonsType.OK,
                                    text="Could not move a window onto the phone display")
            dlg.format_secondary_text(
                (detail or "unknown reason")
                + "\n\nThe display is still there - drag a window past the edge it "
                  "was attached to, or run:  !rq ss-virtual send-window")
            open_dialog_nonblocking(dlg)

    def _on_phone_feature_started(self, feature_id: str, kw: dict | None = None) -> bool:
        kw = kw or {}
        force = bool(kw.get("force_present"))
        if time.monotonic() < self._feature_stop_cooldown.get(feature_id, 0.0) and not force:
            return False
        if force:
            self._feature_stop_cooldown.pop(feature_id, None)
            if feature_id in ("desktop", "phone_on_pc"):
                c = self.facade._live_ws()
                if isinstance(c, dict):
                    c.pop("_user_stopped_until", None)
        if feature_id == "webcam":
            snap_now = self.facade.live_feature_snapshot() if hasattr(self.facade, "live_feature_snapshot") else {}
            for _dfid in ("desktop", "phone_on_pc"):
                if not (snap_now.get(_dfid) or {}).get("on"):
                    _dwin = self.feature_windows.get(_dfid)
                    if _dwin is not None and _dwin.get_visible():
                        _dwin.hide()
        if feature_id == "phone_on_pc":
            v = kw.get("view")
            pkg = str(kw.get("pkg") or "").lower()
            if v == 3 or pkg == "mobile":
                self._phone_on_pc_mode = "mobile"
            elif v == 1 or pkg == "mirror":
                self._phone_on_pc_mode = "mirror"
        self._closed_session_keys.pop(feature_id, None)
        if feature_id in ("desktop", "phone_on_pc"):
            other = "phone_on_pc" if feature_id == "desktop" else "desktop"
            other_win = self.feature_windows.get(other)
            cur_win = self.feature_windows.get(feature_id)
            if (other_win is not None and other_win.get_visible()
                    and isinstance(other_win, FeatureStreamGtkWindow)
                    and (cur_win is None or cur_win is other_win or not cur_win.get_visible())):
                self.feature_windows.pop(other, None)
                other_win.feature_id = feature_id
                other_win._pixbuf = None
                other_win._last_pts = None
                other_win._last_frame_dims = None
                other_win._last_adapted_aspect = None
                if kw.get("sid") is not None:
                    other_win.stream_session_id = kw.get("sid")
                if kw.get("pkg"):
                    other_win.stream_pkg = str(kw.get("pkg"))
                if kw.get("display_id") is not None:
                    other_win.stream_display_id = kw.get("display_id")
                self.feature_windows[feature_id] = other_win
                other_win.sync_orientation_and_controls(force_resize=True)
                other_win.present()
                return False
            if other_win is not None and other_win.get_visible():
                other_win.hide()
        win = self.open_feature_window(feature_id, start_session=False)
        if isinstance(win, FeatureStreamGtkWindow):
            if kw.get("sid") is not None:
                if win.stream_session_id != kw.get("sid"):
                    win._last_pts = None
                    win._last_frame_dims = None
                win.stream_session_id = kw.get("sid")
            if kw.get("pkg"):
                win.stream_pkg = str(kw.get("pkg"))
            if kw.get("display_id") is not None:
                win.stream_display_id = kw.get("display_id")
            win.sync_orientation_and_controls(force_resize=True)
        return False

    def open_feature_window(self, feature_id: str, start_session: bool = True) -> Gtk.Window:
        titles = {
            "desktop": "Mobile Desktop",
            "phone_on_pc": "Mobile Desktop · Mirror",
            "appstream": "Phone App Stream",
            "webcam": "Webcam",
            "screenshare": "Share Screen",
            "crosscontrol": "Cross Control",
        }
        if start_session:
            self._closed_session_keys.pop(feature_id, None)
            self._feature_stop_cooldown.pop(feature_id, None)
            if feature_id == "webcam":
                for _dfid in ("desktop", "phone_on_pc"):
                    _dwin = self.feature_windows.get(_dfid)
                    if _dwin is not None and _dwin.get_visible():
                        _dwin.hide()
        win = self.feature_windows.get(feature_id)
        if win is None:
            if feature_id == "crosscontrol":
                win = CrossControlGtkWindow(self)
            else:
                win = FeatureStreamGtkWindow(self, feature_id, titles.get(feature_id, feature_id.title()))
            self.feature_windows[feature_id] = win
        win.show_all()
        if isinstance(win, FeatureStreamGtkWindow):
            win.sync_orientation_and_controls(force_resize=True)
            with contextlib.suppress(Exception):
                win._sync_window_power_state(focused_hint=True)
        win.present()

        if start_session:
            if feature_id == "desktop":
                self._spawn_checked_request(win, "mobiledesktop:desktop")
            elif feature_id == "phone_on_pc":
                mode = getattr(self, "_phone_on_pc_mode", "mirror")
                target = "mobiledesktop:mobile" if mode == "mobile" else "mobiledesktop:mirror"
                self._spawn_checked_request(win, target)
            elif feature_id == "webcam":
                self._spawn_checked_request(win, "webcam")
            elif feature_id == "screenshare":
                self._spawn_checked_request(win, "screenshare")
            elif feature_id == "crosscontrol":
                if self.facade.user_settings.get("cross_control_edge_capture", False):
                    self._spawn(self.facade.start_cross_control(trigger=1))
                    self.edge_controller.user_disabled = False
                    self.edge_controller.start()
        return win

    def _desktop_accept_prompt(self, detail: str) -> bool:
        if getattr(self, "_accept_prompt_open", False):
            return False
        self._accept_prompt_open = True
        with contextlib.suppress(Exception):
            for _fw in (self.feature_windows.get("desktop"), self.feature_windows.get("phone_on_pc")):
                if isinstance(_fw, FeatureStreamGtkWindow):
                    _fw.status_lbl.set_text("Waiting for ALLOW on the phone…")
        dlg = Gtk.MessageDialog(
            transient_for=self.win, modal=True,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text="Look at your phone — Mobile Desktop needs one tap there")
        dlg.format_secondary_text(
            (detail or "") + "\n\n"
            "Your phone shows an \u201cAllow / Start Mobile Desktop\u201d confirmation "
            "for every PC-initiated session.\n\n"
            "• Tap ALLOW on the phone — or tap the Mobile Desktop tile in the "
            "phone's Smart Connect app.\n"
            "• The request is re-sent twice automatically; if the phone never "
            "asks, force-stop Smart Connect on the phone and try again.\n\n"
            "This window starts streaming the moment you accept.")
        def _closed(*_a):
            self._accept_prompt_open = False
        with contextlib.suppress(Exception):
            dlg.connect("destroy", lambda *_a: setattr(self, "_accept_prompt_open", False))
        open_dialog_nonblocking(dlg, on_response=_closed)
        return False

    def _spawn_checked_request(self, fwin, target: str) -> None:
        async def _rq():
            res = {}
            with contextlib.suppress(Exception):
                res = (await self.facade.request(target)) or {}
            if res.get("ok"):
                return
            detail = str(res.get("detail") or res.get("error") or "request failed")
            with contextlib.suppress(Exception):
                self.facade.events.log("UI_FEATURE_START_FAIL",
                                  f"target={target} detail={detail[:200]}",
                                  feature="gui")
            if "no live phone connection" in detail:
                with contextlib.suppress(Exception):
                    if isinstance(fwin, FeatureStreamGtkWindow):
                        fwin.status_lbl.set_text(
                            "Phone not connected — nothing was sent (see dialog)")
                dlg = Gtk.MessageDialog(
                    transient_for=self.win, modal=True,
                    message_type=Gtk.MessageType.WARNING,
                    buttons=Gtk.ButtonsType.OK,
                    text="Phone is not connected — the request was never sent")
                dlg.format_secondary_text(
                    "The PC cannot dial the phone; the PHONE dials the PC.\n\n"
                    "1. Open Samyuj on the phone once (foreground it).\n"
                    "2. Keep phone and PC on the same Wi-Fi — or plug in USB "
                    "tethering.\n"
                    "3. Still nothing? Force-stop Smart Connect on the phone "
                    "(Settings → Apps → Smart Connect → Force stop) and open it "
                    "again — this clears a stuck session left by an unclean PC "
                    "exit.\n"
                    "4. Last resort: Pair New Phone (QR) in this app.\n\n"
                    "This window keeps working: as soon as the phone connects, "
                    "tap the tile here again (or on the phone).")
                open_dialog_nonblocking(dlg)
            else:
                with contextlib.suppress(Exception):
                    if isinstance(fwin, FeatureStreamGtkWindow):
                        fwin.status_lbl.set_text(f"Start failed: {detail[:90]}")
        self._spawn(_rq())

    def stop_feature(self, feature_id: str) -> None:
        c = self.facade._live_ws()
        now_m = time.monotonic()
        self._feature_stop_cooldown[feature_id] = now_m + 2.5
        if isinstance(c, dict):
            c["_user_stopped_until"] = now_m + 2.5
        with contextlib.suppress(Exception):
            snap = self.facade.live_feature_snapshot() if hasattr(self.facade, "live_feature_snapshot") else {}
            targets = ("desktop", "phone_on_pc") if feature_id in ("desktop", "phone_on_pc") else (feature_id,)
            for tfid in targets:
                self._feature_stop_cooldown[tfid] = now_m + 2.5
                info = snap.get(tfid) or {}
                skey = self._seen_session_keys.pop(tfid, None)
                if not skey and c:
                    skey = f"{tfid}:{c.get('id')}:{info.get('key') or info.get('sid') or info.get('view') or 1}"
                if skey:
                    self._closed_session_keys[tfid] = skey
        if feature_id in ("desktop", "phone_on_pc"):
            if c:
                _prev_desk_sid = c.get("desktop_as_sid") or c.get("as_session_id")
                self._spawn(self.facade.stop_desktop(c, "ui-corner-cancel"))
                self._spawn(self.facade.stop_appstream(c, "ui-corner-cancel", sid=_prev_desk_sid, only_kind="desktop"))
                c["desktop_started"] = False
                c["desktop_pending"] = False
                c["desktop_as_sid"] = None
                c.pop("as_session_id", None)
                c.pop("as_setup_seen", None)
                c.pop("as_pkg", None)
                c.pop("media_last_frame", None)
                if isinstance(c.get("media_last_frame_by_service"), dict):
                    c["media_last_frame_by_service"].pop("desktop", None)
                if isinstance(c.get("as_sessions"), dict):
                    for _ks in [k for k, v in c["as_sessions"].items() if isinstance(v, dict) and v.get("pkg") in (None, "desktop", "mobile", "mirror")]:
                        c["as_sessions"].pop(_ks, None)
            reg_s = getattr(self.facade, "stream_registry", None)
            if reg_s is not None:
                for _sk, _sr in list(reg_s.streams.items()):
                    if _sr.owner_service == "desktop" or str(_sk).startswith("desktop:"):
                        _sr.status = "stopped"
                        _sr.last_frame = None
        elif feature_id == "appstream":
            if c:
                self._spawn(self.facade.stop_appstream(c, "ui-corner-cancel", only_kind="app"))
                c["app_as_sid"] = None
        elif feature_id == "webcam":
            if c:
                self._spawn(self.facade.stop_webcam(c, "ui-corner-cancel"))
                c["wc_started"] = False
            self._spawn(asyncio.to_thread(self.facade.webcam_controller.standby_v4l2))
        elif feature_id == "screenshare":
            if c:
                self._spawn(self.facade.stop_screenshare(c, "ui-corner-cancel"))
                c["ss_started"] = False
        elif feature_id == "crosscontrol":
            self.edge_controller.user_disabled = True
            self.edge_controller.stop()
            if c:
                self._spawn(self.facade.stop_cross_control(c))
        elif feature_id == "clipboard":
            self.facade.clip_sync.set_enabled(False)
        elif feature_id == "files":
            self._spawn(self.facade.linux_mount_mgr.detach())
        elif feature_id == "control_lock":
            self._spawn(self.facade.request("control-unlock"))

        win_targets = ("desktop", "phone_on_pc") if feature_id in ("desktop", "phone_on_pc") else (feature_id,)
        for _wfid in win_targets:
            win = self.feature_windows.get(_wfid)
            if win is not None:
                if isinstance(win, FeatureStreamGtkWindow):
                    win._pixbuf = None
                    win._last_pts = None
                    win._last_frame_dims = None
                    win._last_adapted_aspect = None
                    win._video_rect = (0.0, 0.0, 1.0, 1.0)
                    win.stream_session_id = None
                    win.stream_display_id = None
                    win.stream_pkg = None
                win.hide()
        self._refresh_all()

    def open_pair_dialog(self) -> Gtk.Dialog:
        self.facade.pairing_mode_active = True
        dlg = Gtk.Dialog(title="Pair New Phone with QR Code", transient_for=self.win, flags=0)
        dlg.set_default_size(420, 480)
        box = dlg.get_content_area()
        box.set_spacing(12)
        box.set_margin_top(16)
        box.set_margin_bottom(16)
        box.set_margin_start(20)
        box.set_margin_end(20)

        h_lbl = Gtk.Label(label="Scan with Smart Connect on your phone")
        h_lbl.get_style_context().add_class("panel-h2")
        box.pack_start(h_lbl, False, False, 0)

        sub_lbl = Gtk.Label(
            label="After scanning once, Smart Connect saves your phone's real device name and key for automatic USB & Wi-Fi reconnects."
        )
        sub_lbl.set_line_wrap(True)
        sub_lbl.set_justify(Gtk.Justification.CENTER)
        sub_lbl.get_style_context().add_class("panel-sub")
        box.pack_start(sub_lbl, False, False, 0)

        try:
            png_bytes = self.facade.get_pairing_qr_png() if hasattr(self.facade, "get_pairing_qr_png") else b""
            if png_bytes:
                loader = GdkPixbuf.PixbufLoader.new_with_type("png")
                loader.write(png_bytes)
                loader.close()
                pb = loader.get_pixbuf()
                if pb is not None:
                    scaled = pb.scale_simple(260, 260, GdkPixbuf.InterpType.NEAREST)
                    if scaled is not None:
                        scaled = round_pixbuf_corners(scaled, radius=16.0)
                    qr_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                    qr_card.set_halign(Gtk.Align.CENTER)
                    qr_card.get_style_context().add_class("sc-card")
                    qr_card.pack_start(Gtk.Image.new_from_pixbuf(scaled), False, False, 0)
                    box.pack_start(qr_card, False, False, 4)
        except Exception:
            pass

        ip_lbl = Gtk.Label(label=f"PC Address: {self.facade.lan_ip}:{self.facade.port}")
        ip_lbl.get_style_context().add_class("panel-sub")
        box.pack_start(ip_lbl, False, False, 0)

        close_btn = Gtk.Button(label="Close")
        close_btn.get_style_context().add_class("secondary-btn")
        close_btn.connect("clicked", lambda *_: dlg.destroy())
        box.pack_end(close_btn, False, False, 0)

        def _on_dlg_destroy(*_a):
            self.facade.pairing_mode_active = False

        dlg.connect("destroy", _on_dlg_destroy)
        dlg.show_all()
        return dlg

ClipboardDialog = DialogsAndClipboardMixin
