from __future__ import annotations

import contextlib
import subprocess

from samyuj.ui.gtk.gtk_compat import GLib, Gtk

from samyuj.ui.gtk.assets import (
    open_dialog_nonblocking,
)
from samyuj.ui.gtk.theme import (
    COLOR_PALETTES,
    UI_THEMES,
)
from samyuj.ui.gtk.windows import (
    FeatureStreamGtkWindow,
)

class SettingsPageMixin:

    def _build_settings_page(self) -> Gtk.Widget:
        scroll = Gtk.ScrolledWindow()
        with contextlib.suppress(Exception):
            from samyuj.ui.gtk.smooth_scroll import attach_smooth_scroll
            attach_smooth_scroll(scroll)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        scroll.add(vbox)

        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        eb = Gtk.Label(label="MAKE IT YOURS", xalign=0.0)
        eb.get_style_context().add_class("eyebrow")
        h1 = Gtk.Label(label="Settings", xalign=0.0)
        h1.get_style_context().add_class("page-h1")
        sub = Gtk.Label(label="Simple controls for how your devices work together.", xalign=0.0)
        sub.get_style_context().add_class("page-sub")
        head.pack_start(eb, False, False, 0)
        head.pack_start(h1, False, False, 0)
        head.pack_start(sub, False, False, 0)
        vbox.pack_start(head, False, False, 0)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        card.get_style_context().add_class("sc-card")
        vbox.pack_start(card, False, False, 0)

        self._build_settings_connection_section(card)
        self._build_settings_video_and_scroll_section(card)
        self._build_settings_files_and_cross_control_section(card)
        self._build_settings_clipboard_and_nfs_section(card)
        self._build_settings_appearance_section(card)
        return scroll

    def _build_settings_connection_section(self, card: Gtk.Box) -> None:
        c_title = Gtk.Label(label="Connection preferences", xalign=0.0)
        c_title.get_style_context().add_class("panel-h2")
        card.pack_start(c_title, False, False, 0)

        card.pack_start(
            self._create_setting_toggle_row(
                "Connect over USB",
                "Prefer a cable when your phone is plugged in. Turning this OFF disables the "
                "whole USB link monitor and the Wi-Fi↔USB auto-switch (Wi-Fi only, least "
                "background activity).",
                bool(self.facade.user_settings.get("connect_usb", True)),
                lambda active: self.facade.save_user_settings(connect_usb=active),
            ),
            False, False, 0,
        )
        card.pack_start(
            self._create_setting_toggle_row(
                "Connect over Wi-Fi",
                "Find your phone on the same network.",
                bool(self.facade.user_settings.get("connect_wifi", True)),
                lambda active: self.facade.save_user_settings(connect_wifi=active),
            ),
            False, False, 0,
        )
        card.pack_start(
            self._create_setting_toggle_row(
                "Start in the background",
                "Keep connection services available after closing the window.",
                bool(self.facade.user_settings.get("start_background", False)),
                lambda active: self.facade.save_user_settings(start_background=active),
            ),
            False, False, 0,
        )
        min_tray_row = self._create_setting_toggle_row(
            "Minimize main window to system tray",
            "When minimizing or closing the main window, hide it into the system tray so you can restore it anytime from the tray icon.",
            bool(getattr(self, "minimize_to_tray_enabled", True)),
            self._on_toggle_minimize_to_tray_setting,
        )
        for _ch in min_tray_row.get_children():
            if isinstance(_ch, Gtk.Switch):
                self.minimize_to_tray_switch = _ch
        card.pack_start(min_tray_row, False, False, 0)

    def _build_settings_video_and_scroll_section(self, card: Gtk.Box) -> None:
        v_title = Gtk.Label(label="Video quality", xalign=0.0)
        v_title.get_style_context().add_class("panel-h2")
        v_title.set_margin_top(12)
        card.pack_start(v_title, False, False, 0)

        v_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        v_lbl = Gtk.Label(label="Codec preference for phone → PC streams (applies to the next stream start)", xalign=0.0)
        v_lbl.get_style_context().add_class("panel-sub")
        v_row.pack_start(v_lbl, False, False, 0)
        self.codec_combo = Gtk.ComboBoxText()
        for _k, _l in (("h264", "H264 — phone hardware encoder (default)"),
                       ("auto", "Native Phone Stream (Untouched)"),
                       ("vp8", "VP8 — WebRTC software codec")):
            self.codec_combo.append(_k, _l)
        _cur = str(getattr(self.facade, "media_codec_pref", "h264") or "h264")
        self.codec_combo.set_active_id(_cur if _cur in ("h264", "vp8", "auto", "vp9") else "h264")

        def _on_codec_changed(cb):
            val = cb.get_active_id() or "h264"
            self.facade.media_codec_pref = val
            with contextlib.suppress(Exception):
                self.facade.save_user_settings(video_codec=val, video_codec_r22_migrated=True)
            with contextlib.suppress(Exception):
                self.facade.events.log("MEDIA_CODEC_PREF_SET", f"preference={val} (next stream start)", feature="gui")

        self.codec_combo.connect("changed", _on_codec_changed)
        v_row.pack_start(self.codec_combo, False, False, 0)
        card.pack_start(v_row, False, False, 0)

        uv_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        uv_txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        uv_t = Gtk.Label(label="Webcam UV chroma swap", xalign=0.0)
        uv_t.get_style_context().add_class("notif-title")
        uv_s = Gtk.Label(
            label="Exchange U and V chroma planes on the phone webcam stream (ON by default to fix blue/red skin tones).",
            xalign=0.0,
        )
        uv_s.set_line_wrap(True)
        uv_s.get_style_context().add_class("panel-sub")
        uv_txt.pack_start(uv_t, False, False, 0)
        uv_txt.pack_start(uv_s, False, False, 0)
        uv_row.pack_start(uv_txt, True, True, 0)

        self.uv_swap_switch = Gtk.Switch()
        self.uv_swap_switch.set_active(bool(getattr(self.facade, "uv_swap", True)))
        self.uv_swap_switch.set_valign(Gtk.Align.CENTER)
        self._updating_uv_swap_switch = False

        def _on_uv_switch_toggled(sw, _pspec):
            if getattr(self, "_updating_uv_swap_switch", False):
                return
            active = bool(sw.get_active())
            if hasattr(self.facade, "set_uv_swap"):
                self.facade.set_uv_swap(active)
            else:
                self.facade.uv_swap = active
            wc_win = self.feature_windows.get("webcam")
            if isinstance(wc_win, FeatureStreamGtkWindow):
                wc_win._sync_webcam_uv_swap_btn()
                wc_win._last_pts = None
            GLib.idle_add(self._pump_video)

        self.uv_swap_switch.connect("notify::active", _on_uv_switch_toggled)
        uv_row.pack_end(self.uv_swap_switch, False, False, 0)
        card.pack_start(uv_row, False, False, 0)

        sc_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        sc_txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        sc_t = Gtk.Label(label="Remote scroll speed (Mobile Desktop / Phone-on-PC / App streams)", xalign=0.0)
        sc_t.get_style_context().add_class("notif-title")
        sc_s = Gtk.Label(
            label="How far ONE mouse-wheel notch scrolls the phone. 'Normal' ≈ 3-4 list lines with no fling "
                  "inertia; 'Page (legacy)' is the old aggressive full-page jump.",
            xalign=0.0,
        )
        sc_s.set_line_wrap(True)
        sc_s.get_style_context().add_class("panel-sub")
        sc_txt.pack_start(sc_t, False, False, 0)
        sc_txt.pack_start(sc_s, False, False, 0)
        sc_row.pack_start(sc_txt, True, True, 0)

        self.scroll_step_combo = Gtk.ComboBoxText()
        for _sk, _sl in (("0.055", "Gentle"), ("0.090", "Medium"),
                         ("0.120", "Normal (default)"), ("0.160", "Fast"),
                         ("0.220", "Page (legacy)")):
            self.scroll_step_combo.append(_sk, _sl)
        _cur_step = f"{float(getattr(self.facade, 'stream_scroll_step', 0.120) or 0.120):.3f}"
        if not self.scroll_step_combo.set_active_id(_cur_step):
            self.scroll_step_combo.append(_cur_step, f"Custom ({_cur_step})")
            self.scroll_step_combo.set_active_id(_cur_step)
        self.scroll_step_combo.set_valign(Gtk.Align.CENTER)

        def _on_scroll_step_changed(cb):
            try:
                v = float(cb.get_active_id() or "0.120")
            except (TypeError, ValueError):
                return
            v = min(0.5, max(0.005, v))
            self.facade.stream_scroll_step = v
            with contextlib.suppress(Exception):
                self.facade.save_user_settings(scroll_step=v)
                self.facade.save_user_settings(scroll_step_user_set=True)
            with contextlib.suppress(Exception):
                self.facade.events.log("SCROLL_STEP_SET", f"{v} of screen height per wheel notch", feature="gui")

        self.scroll_step_combo.connect("changed", _on_scroll_step_changed)
        sc_row.pack_end(self.scroll_step_combo, False, False, 0)
        card.pack_start(sc_row, False, False, 0)

        scm_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        scm_txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        scm_t = Gtk.Label(label="Remote scroll method", xalign=0.0)
        scm_t.get_style_context().add_class("notif-title")
        scm_s = Gtk.Label(
            label="Touch drag (default) synthesises a gentle swipe anywhere on screen; Native wheel sends real "
                  "Android mouse-wheel events (exact, no inertia — best in Mobile Desktop mouse mode); Hybrid sends both.",
            xalign=0.0,
        )
        scm_s.set_line_wrap(True)
        scm_s.get_style_context().add_class("panel-sub")
        scm_txt.pack_start(scm_t, False, False, 0)
        scm_txt.pack_start(scm_s, False, False, 0)
        scm_row.pack_start(scm_txt, True, True, 0)

        self.scroll_mode_combo = Gtk.ComboBoxText()
        for _mk, _ml in (("touch", "Touch drag (compatible)"),
                         ("wheel", "Native wheel (precise)"),
                         ("hybrid", "Hybrid (wheel + drag)")):
            self.scroll_mode_combo.append(_mk, _ml)
        _cur_mode = str(getattr(self.facade, "stream_scroll_mode", "touch") or "touch")
        self.scroll_mode_combo.set_active_id(_cur_mode if _cur_mode in ("touch", "wheel", "hybrid") else "touch")
        self.scroll_mode_combo.set_valign(Gtk.Align.CENTER)

        def _on_scroll_mode_changed(cb):
            val = cb.get_active_id() or "touch"
            self.facade.stream_scroll_mode = val
            with contextlib.suppress(Exception):
                self.facade.save_user_settings(scroll_mode=val)
            with contextlib.suppress(Exception):
                self.facade.events.log("SCROLL_MODE_SET", val, feature="gui")

        self.scroll_mode_combo.connect("changed", _on_scroll_mode_changed)
        scm_row.pack_end(self.scroll_mode_combo, False, False, 0)
        card.pack_start(scm_row, False, False, 0)

        def _on_smooth_scrolling_toggled(active: bool) -> None:
            with contextlib.suppress(Exception):
                from samyuj.ui.gtk.smooth_scroll import set_smooth_scroll_enabled
                set_smooth_scroll_enabled(bool(active))
            with contextlib.suppress(Exception):
                self.facade.save_user_settings(smooth_scrolling=bool(active))
            with contextlib.suppress(Exception):
                self.facade.events.log(
                    "SMOOTH_SCROLL_SET", str(bool(active)), feature="gui")

        card.pack_start(
            self._create_setting_toggle_row(
                "Smooth scrolling",
                "Animate mouse-wheel and touchpad scrolling across the app "
                "(pages, lists, conversations, tray) instead of jumping a "
                "full step per notch. Takes effect immediately.",
                bool(self.facade.user_settings.get("smooth_scrolling", True)),
                _on_smooth_scrolling_toggled,
            ),
            False, False, 0,
        )

        def _on_lowlight_toggled(active: bool) -> None:
            with contextlib.suppress(Exception):
                from samyuj.media.enhance import set_lowlight_config
                set_lowlight_config(enabled=bool(active))
            with contextlib.suppress(Exception):
                self.facade.save_user_settings(lowlight_boost=bool(active))
            with contextlib.suppress(Exception):
                self.facade.events.log(
                    "LOWLIGHT_BOOST_SET", str(bool(active)), feature="gui")

        card.pack_start(
            self._create_setting_toggle_row(
                "Low-light video boost",
                "Automatically brighten dark camera and stream video (phone "
                "webcam, Mobile Desktop, screen share) so it matches what you "
                "see on the phone in low light. Bright scenes are untouched.",
                bool(self.facade.user_settings.get("lowlight_boost", True)),
                _on_lowlight_toggled,
            ),
            False, False, 0,
        )

        res_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        res_lbl = Gtk.Label(
            label="Mobile Desktop & AppStream resolution (applies live to active sessions and future starts)",
            xalign=0.0,
        )
        res_lbl.set_line_wrap(True)
        res_lbl.get_style_context().add_class("panel-sub")
        res_row.pack_start(res_lbl, False, False, 0)
        self.desktop_res_combo = Gtk.ComboBoxText()
        for _rk, _rl in (
            ("1280x720", "1280×720 (HD 720p — lowest latency)"),
            ("1600x900", "1600×900 (HD+ 900p)"),
            ("1920x1080", "1920×1080 (Full HD 1080p — default)"),
            ("1920x1200", "1920×1200 (WUXGA 16:10)"),
            ("2560x1440", "2560×1440 (QHD 1440p)"),
        ):
            self.desktop_res_combo.append(_rk, _rl)
        _dw, _dh = getattr(self.facade, "desktop_res", (1920, 1080))
        _cur_dres = str(self.facade.user_settings.get("desktop_res") or f"{_dw}x{_dh}")
        if not self.desktop_res_combo.set_active_id(_cur_dres):
            self.desktop_res_combo.append(_cur_dres, _cur_dres)
            self.desktop_res_combo.set_active_id(_cur_dres)

        def _on_settings_res_changed(cb):
            val = (cb.get_active_id() or "1920x1080").strip()
            with contextlib.suppress(Exception):
                w_, h_ = (int(x) for x in val.lower().split("x", 1))
                self.facade.desktop_res = (w_, h_)
                self.facade.save_user_settings(desktop_res=val)
            for _fid in ("desktop", "phone_on_pc", "appstream"):
                _fw = self.feature_windows.get(_fid)
                if _fw is not None and getattr(_fw, "res_combo", None) is not None:
                    _fw._updating_res_combo = True
                    try:
                        _fw.res_combo.set_active_id(val)
                    finally:
                        _fw._updating_res_combo = False
            if self.facade._live_ws() is not None:
                self._spawn(self.facade.request(f"desktop-res:{val}"))

        self.desktop_res_combo.connect("changed", _on_settings_res_changed)
        res_row.pack_start(self.desktop_res_combo, False, False, 0)
        card.pack_start(res_row, False, False, 0)

        r_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        r_lbl = Gtk.Label(label=(
            "Minimum stream bitrate feedback (REMB floor) — stops the phone from "
            "collapsing to a low-resolution, choppy stream when the PC UI hiccups."), xalign=0.0)
        r_lbl.get_style_context().add_class("panel-sub")
        r_lbl.set_line_wrap(True)
        r_row.pack_start(r_lbl, False, False, 0)
        self.remb_combo = Gtk.ComboBoxText()
        for _k, _l in (("6000", "6 Mbps (default — LAN & USB)"),
                       ("10000", "10 Mbps (fast Wi-Fi 5 GHz / USB)"),
                       ("3000", "3 Mbps (weak Wi-Fi)"),
                       ("0", "Off (stock estimator)")):
            self.remb_combo.append(_k, _l)
        _cur_remb = str(self.facade.user_settings.get("remb_floor_kbps") or "6000")
        self.remb_combo.set_active_id(_cur_remb if _cur_remb in ("0", "3000", "6000", "10000") else "6000")

        def _on_remb_changed(cb):
            val = int(cb.get_active_id() or "6000")
            with contextlib.suppress(Exception):
                self.facade.set_remb_floor(val)
            with contextlib.suppress(Exception):
                self.facade.save_user_settings(remb_floor_kbps=str(val))
            with contextlib.suppress(Exception):
                self.facade.events.log("REMB_FLOOR_SET", f"{val} kbps", feature="gui")

        self.remb_combo.connect("changed", _on_remb_changed)
        r_row.pack_start(self.remb_combo, False, False, 0)
        card.pack_start(r_row, False, False, 0)

    def _build_settings_files_and_cross_control_section(self, card: Gtk.Box) -> None:
        f_title = Gtk.Label(label="Incoming files from phone (Storage Path)", xalign=0.0)
        f_title.get_style_context().add_class("panel-h2")
        f_title.set_margin_top(12)
        card.pack_start(f_title, False, False, 0)

        path_desc = Gtk.Label(
            label="Choose the folder on this Linux PC where files sent from your phone are saved:",
            xalign=0.0,
        )
        path_desc.set_line_wrap(True)
        path_desc.get_style_context().add_class("panel-sub")
        card.pack_start(path_desc, False, False, 0)

        path_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.incoming_dir_entry = Gtk.Entry()
        self.incoming_dir_entry.get_style_context().add_class("sc-entry")
        self.incoming_dir_entry.set_text(str(self.facade.get_incoming_files_dir()))
        self.incoming_dir_entry.set_hexpand(True)
        path_row.pack_start(self.incoming_dir_entry, True, True, 0)

        browse_dir_btn = Gtk.Button(label="Browse…")
        browse_dir_btn.get_style_context().add_class("secondary-btn")
        browse_dir_btn.connect("clicked", self._on_browse_incoming_dir)
        path_row.pack_start(browse_dir_btn, False, False, 0)

        save_dir_btn = Gtk.Button(label="Save Path")
        save_dir_btn.get_style_context().add_class("primary-btn")
        save_dir_btn.connect("clicked", self._on_save_incoming_dir)
        path_row.pack_start(save_dir_btn, False, False, 0)

        open_dir_btn = Gtk.Button(label="Open Folder")
        open_dir_btn.get_style_context().add_class("secondary-btn")
        open_dir_btn.connect(
            "clicked",
            lambda *_: subprocess.Popen(["xdg-open", str(self.facade.get_incoming_files_dir())],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True),
        )
        path_row.pack_start(open_dir_btn, False, False, 0)
        card.pack_start(path_row, False, False, 0)

        x_title = Gtk.Label(label="Display Cross Control & Wallpaper Sync", xalign=0.0)
        x_title.get_style_context().add_class("panel-h2")
        x_title.set_margin_top(12)
        card.pack_start(x_title, False, False, 0)

        card.pack_start(
            self._create_setting_toggle_row(
                "Enable Cross Control screen-edge capture",
                "OFF by default (input-hijack safeguard). When ON, pushing the mouse into the "
                "chosen display edge for ~0.4 s hands your keyboard & mouse to the phone "
                "(Esc / Alt+Tab / Super always return to the PC).",
                bool(self.facade.user_settings.get("cross_control_edge_capture", False)),
                self._on_cross_control_enable_toggled,
            ),
            False, False, 0,
        )

        edge_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        edge_lbl_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        el1 = Gtk.Label(label="Phone position relative to PC display (Cross Control)", xalign=0.0)
        el1.get_style_context().add_class("notif-title")
        el2 = Gtk.Label(label="Moving your mouse cursor to this edge of your PC display transitions control to your phone.", xalign=0.0)
        el2.get_style_context().add_class("panel-sub")
        el2.set_line_wrap(True)
        edge_lbl_box.pack_start(el1, False, False, 0)
        edge_lbl_box.pack_start(el2, False, False, 0)
        edge_row.pack_start(edge_lbl_box, True, True, 0)

        self.edge_combo = Gtk.ComboBoxText()
        for edge_id, edge_text in (
            ("left", "Left edge of PC display"),
            ("right", "Right edge of PC display"),
            ("top", "Top edge of PC display"),
            ("bottom", "Bottom edge of PC display"),
        ):
            self.edge_combo.append(edge_id, edge_text)
        self.edge_combo.set_active_id(str(self.facade.user_settings.get("cross_control_edge") or "left"))
        self.edge_combo.connect("changed", self._on_edge_combo_changed)
        edge_row.pack_end(self.edge_combo, False, False, 0)
        card.pack_start(edge_row, False, False, 0)

        card.pack_start(
            self._create_setting_toggle_row(
                "Sync phone wallpaper automatically",
                "Pull your phone's current wallpaper into Samyuj on connect.",
                bool(self.facade.user_settings.get("wallpaper_sync", True)),
                lambda active: self.facade.save_user_settings(wallpaper_sync=active),
            ),
            False, False, 0,
        )
        card.pack_start(
            self._create_setting_toggle_row(
                "Apply phone wallpaper to Linux desktop",
                "Also set your Linux desktop background when your phone wallpaper updates.",
                bool(self.facade.user_settings.get("apply_desktop_wallpaper", False)),
                self._on_toggle_linux_wallpaper_sync,
            ),
            False, False, 0,
        )
        card.pack_start(
            self._create_setting_toggle_row(
                "Attach phone NFS to Linux File Manager automatically",
                "Mount phone storage under ~/.local/share/samyuj/mounts/<DeviceName> — a hidden path that desktop file indexers (Baloo, Tracker) skip — and add sidebar bookmarks (GTK + KDE Places).",
                bool(self.facade.user_settings.get("auto_mount_phone_nfs", True)),
                lambda active: self.facade.save_user_settings(auto_mount_phone_nfs=active),
            ),
            False, False, 0,
        )
        card.pack_start(
            self._create_setting_toggle_row(
                "Pre-populate mirror with phone media (deep sync)",
                "OFF by default (performance). On connect only a fast top-level listing of the "
                "mirror folder is synced.",
                bool(self.facade.user_settings.get("nfs_deep_media_sync", False)),
                lambda active: self.facade.save_user_settings(nfs_deep_media_sync=bool(active)),
            ),
            False, False, 0,
        )
        card.pack_start(
            self._create_setting_toggle_row(
                "Arm virtual webcam at startup",
                "Keeps “Samyuj Webcam” visible to Firefox/Zoom/OBS right after launch by "
                "writing a 5 fps keepalive frame to /dev/video10.",
                bool(self.facade.user_settings.get("webcam_startup_arm", True)),
                lambda active: self.facade.save_user_settings(webcam_startup_arm=bool(active)),
            ),
            False, False, 0,
        )

    def _build_settings_clipboard_and_nfs_section(self, card: Gtk.Box) -> None:
        k_title = Gtk.Label(label="Linux Clipboard & Filesystem Integration", xalign=0.0)
        k_title.get_style_context().add_class("panel-h2")
        k_title.set_margin_top(12)
        card.pack_start(k_title, False, False, 0)

        card.pack_start(
            self._create_setting_toggle_row(
                "Clipboard: event-driven Wayland/X11 watcher",
                "DEFAULT ON WAYLAND since v10: one long-lived `wl-paste --watch` stream.",
                (getattr(self.facade, "clip_sync", None) is not None
                 and str(self.facade.clip_sync.watch_method()) == "event"),
                self._on_clip_watch_method_toggled,
            ),
            False, False, 0,
        )
        card.pack_start(
            self._create_setting_toggle_row(
                "Clipboard: also sync the PRIMARY selection (X11)",
                "Highlighting text in X11 pushes the primary selection to the phone.",
                bool(self.facade.user_settings.get("clipboard_sync_primary", False)),
                lambda active: self.facade.save_user_settings(clipboard_sync_primary=bool(active)),
            ),
            False, False, 0,
        )

        nfs_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        nfs_lbl_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        nl1 = Gtk.Label(label="Phone-files attach method", xalign=0.0)
        nl1.get_style_context().add_class("notif-title")
        nl2 = Gtk.Label(
            label="How phone storage appears on the PC (Auto, Mirror folder, GVFS, FUSE, or Kernel NFS).", xalign=0.0)
        nl2.get_style_context().add_class("panel-sub")
        nl2.set_line_wrap(True)
        nfs_lbl_box.pack_start(nl1, False, False, 0)
        nfs_lbl_box.pack_start(nl2, False, False, 0)
        nfs_row.pack_start(nfs_lbl_box, True, True, 0)
        self.nfs_method_combo = Gtk.ComboBoxText()
        for mid, mtxt in (
            ("auto", "Auto — FUSE live mount → GVFS (recommended)"),
            ("mirror", "Mirror folder only"),
            ("gvfs", "GVFS network mount (no root)"),
            ("fuse", "FUSE userspace live mount (read-write, no root)"),
            ("kernel", "Kernel NFS (one-time password)"),
        ):
            self.nfs_method_combo.append(mid, mtxt)
        self.nfs_method_combo.set_active_id(
            str(self.facade.user_settings.get("nfs_attach_method") or "auto"))
        self.nfs_method_combo.connect("changed", self._on_nfs_method_changed)
        nfs_row.pack_end(self.nfs_method_combo, False, False, 0)
        card.pack_start(nfs_row, False, False, 0)

    def _build_settings_appearance_section(self, card: Gtk.Box) -> None:
        a_title = Gtk.Label(label="Appearance & Unified Theme Concepts", xalign=0.0)
        a_title.get_style_context().add_class("panel-h2")
        a_title.set_margin_top(12)
        card.pack_start(a_title, False, False, 0)

        theme_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        theme_row.get_style_context().add_class("notif-row")
        t_left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        tl1 = Gtk.Label(label="UI Theme Concept (Tray, Main Window & Apps Window)", xalign=0.0)
        tl1.get_style_context().add_class("notif-title")
        tl2 = Gtk.Label(
            label="Switch all windows and the system tray popover simultaneously between Material You (MD3), Tray Concept, Aurora Glass Neo, and Linux GTK App themes.",
            xalign=0.0,
        )
        tl2.set_line_wrap(True)
        tl2.set_max_width_chars(56)
        tl2.get_style_context().add_class("panel-sub")
        t_left.pack_start(tl1, False, False, 0)
        t_left.pack_start(tl2, False, False, 0)
        theme_row.pack_start(t_left, True, True, 0)

        self.settings_theme_combo = Gtk.ComboBoxText()
        for tid, tname, tdesc in UI_THEMES:
            self.settings_theme_combo.append(tid, f"{tname} ({tdesc})")
        self.settings_theme_combo.set_active_id(getattr(self, "ui_theme", "tray"))
        self.settings_theme_combo.set_valign(Gtk.Align.CENTER)
        self.settings_theme_combo.connect("changed", self._on_settings_theme_combo_changed)
        theme_row.pack_end(self.settings_theme_combo, False, False, 0)
        card.pack_start(theme_row, False, False, 0)

        pal_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        pal_row.get_style_context().add_class("notif-row")
        p_left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        pl1 = Gtk.Label(label="Color Palette", xalign=0.0)
        pl1.get_style_context().add_class("notif-title")
        pl2 = Gtk.Label(
            label="Curated neutral dark & light color systems (Linear Carbon, GNOME Adwaita, Nordic Frost, Catppuccin Mocha, Pure Emerald).",
            xalign=0.0,
        )
        pl2.set_line_wrap(True)
        pl2.set_max_width_chars(56)
        pl2.get_style_context().add_class("panel-sub")
        p_left.pack_start(pl1, False, False, 0)
        p_left.pack_start(pl2, False, False, 0)
        pal_row.pack_start(p_left, True, True, 0)

        self.settings_palette_combo = Gtk.ComboBoxText()
        for pid, pinfo in COLOR_PALETTES.items():
            self.settings_palette_combo.append(pid, str(pinfo.get("name") or pid))
        self.settings_palette_combo.set_active_id(getattr(self, "color_palette", "carbon"))
        self.settings_palette_combo.set_valign(Gtk.Align.CENTER)
        self.settings_palette_combo.connect("changed", self._on_settings_palette_combo_changed)
        pal_row.pack_end(self.settings_palette_combo, False, False, 0)
        card.pack_start(pal_row, False, False, 0)

        self._updating_theme_switches = False
        dm_row = self._create_setting_toggle_row(
            "Dark mode",
            "Use the dark surface palette across all windows, the system tray popover, and the file browser.",
            bool(getattr(self, "dark_mode", True)),
            self._on_toggle_dark_mode_setting,
        )
        for _ch in dm_row.get_children():
            if isinstance(_ch, Gtk.Switch):
                self.dark_mode_switch = _ch
        card.pack_start(dm_row, False, False, 0)
        card.pack_start(
            self._create_setting_toggle_row(
                "Follow system appearance",
                "Use your desktop's light or dark theme automatically.",
                bool(self.facade.user_settings.get("follow_system_theme", False)),
                self._on_toggle_follow_system_theme,
            ),
            False, False, 0,
        )

    def _create_setting_toggle_row(self, title: str, subtitle: str, initial: bool, on_toggle) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.get_style_context().add_class("notif-row")
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        t_lbl = Gtk.Label(label=title, xalign=0.0)
        t_lbl.get_style_context().add_class("notif-title")
        t_lbl.set_line_wrap(True)
        t_lbl.set_max_width_chars(56)
        s_lbl = Gtk.Label(label=subtitle, xalign=0.0)
        s_lbl.get_style_context().add_class("panel-sub")
        s_lbl.set_line_wrap(True)
        s_lbl.set_max_width_chars(64)
        left.pack_start(t_lbl, False, False, 0)
        left.pack_start(s_lbl, False, False, 0)
        row.pack_start(left, True, True, 0)

        sw = Gtk.Switch()
        sw.set_active(bool(initial))
        sw.set_valign(Gtk.Align.CENTER)
        sw.connect("notify::active", lambda w, _pspec: on_toggle(bool(w.get_active())))
        row.pack_end(sw, False, False, 0)
        return row

    def _on_browse_incoming_dir(self, *_args) -> None:
        dlg = Gtk.FileChooserDialog(
            title="Select folder to store files received from your phone",
            parent=self.win,
            action=Gtk.FileChooserAction.SELECT_FOLDER,
        )
        dlg.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OPEN, Gtk.ResponseType.OK,
        )
        dlg.set_filename(str(self.facade.get_incoming_files_dir()))

        def _picked(d):
            chosen = d.get_filename()
            if chosen:
                self.incoming_dir_entry.set_text(chosen)
                self.facade.set_incoming_files_dir(chosen)

        open_dialog_nonblocking(dlg, on_ok=_picked)

    def _on_save_incoming_dir(self, *_args) -> None:
        txt = self.incoming_dir_entry.get_text().strip()
        if txt:
            res = self.facade.set_incoming_files_dir(txt)
            if res.get("ok"):
                self.incoming_dir_entry.set_text(res["incoming_files_dir"])

    def _on_edge_combo_changed(self, combo: Gtk.ComboBoxText) -> None:
        eid = combo.get_active_id() or "left"
        self.facade.save_user_settings(cross_control_edge=eid)
        self.edge_controller.edge = eid
        if self.edge_controller.enabled:
            self.edge_controller.start(eid)

    def _on_clip_watch_method_toggled(self, active: bool) -> None:
        self.facade.save_user_settings(
            clipboard_watch_method="event" if active else "interval")
        cs = getattr(self.facade, "clip_sync", None)
        if cs is not None:
            with contextlib.suppress(Exception):
                cs.apply_watch_method()

    def _on_nfs_method_changed(self, combo: Gtk.ComboBoxText) -> None:
        mid = combo.get_active_id() or "auto"
        self.facade.save_user_settings(nfs_attach_method=mid)
        mgr = getattr(self.facade, "linux_mount_mgr", None)
        if mgr is not None and getattr(mgr, "state", "") == "mounted":
            async def _reattach():
                with contextlib.suppress(Exception):
                    await mgr.detach()
                    await mgr.attach(mgr.device_label or None, sync_files=False)
            self._spawn(_reattach())

    def _on_cross_control_enable_toggled(self, active: bool) -> None:
        self.facade.save_user_settings(cross_control_edge_capture=bool(active))
        if active:
            self.edge_controller.user_disabled = False
            self.edge_controller.start()
        else:
            self.edge_controller.user_disabled = True
            self.edge_controller.stop()
            c = self.facade._live_ws()
            if c is not None and self.facade.uc_state.get("connected"):
                self._spawn(self.facade.stop_cross_control(c))

    def _on_toggle_linux_wallpaper_sync(self, active: bool) -> None:
        self.facade.save_user_settings(apply_desktop_wallpaper=active)
        if active and self.facade.phone_wallpaper and self.facade.phone_wallpaper.get("saved"):
            self.facade.apply_wallpaper_to_linux_desktop(self.facade.phone_wallpaper["saved"])

    def _on_toggle_minimize_to_tray_setting(self, active: bool) -> None:
        if getattr(self, "_updating_theme_switches", False):
            return
        self.set_minimize_to_tray_enabled(bool(active), persist=True)

    def _on_settings_theme_combo_changed(self, combo: Gtk.ComboBoxText) -> None:
        if getattr(self, "_updating_theme_switches", False):
            return
        self.set_ui_theme(combo.get_active_id() or "tray", persist=True)

    def _on_settings_palette_combo_changed(self, combo: Gtk.ComboBoxText) -> None:
        if getattr(self, "_updating_theme_switches", False):
            return
        self.set_color_palette(combo.get_active_id() or "carbon", persist=True)

    def _on_toggle_dark_mode_setting(self, active: bool) -> None:
        if getattr(self, "_updating_theme_switches", False):
            return
        self.set_dark_mode(bool(active), persist=True)

    def _on_toggle_follow_system_theme(self, active: bool) -> None:
        self.facade.save_user_settings(follow_system_theme=bool(active))
        if active:
            sys_dark = self._detect_system_dark_theme()
            self.set_dark_mode(sys_dark, persist=True)

SettingsPage = SettingsPageMixin
