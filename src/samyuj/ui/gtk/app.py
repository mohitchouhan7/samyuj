from __future__ import annotations
from pathlib import Path
import threading

import asyncio
import collections
import contextlib
import os
import subprocess
import time

from .gtk_compat import (
    GTK_MAJOR,
    Gdk,
    GdkPixbuf,
    GLib,
    Gtk,
    IS_GTK4,
    apply_css_provider_to_display,
    ensure_headless_display,
    get_gtk_version_string,
    load_css_bytes,
)

from samyuj.ui.facade import ApplicationFacade
from .assets import (
    _clamp_window_to_screen,
    make_cairo_icon,
    make_setup_preview_pixbuf,
    make_themed_feature_icon,
)
from .pages import (
    AppsPageMixin,
    FilesPageMixin,
    GalleryPageMixin,
    HomePageMixin,
    MessagesPageMixin,
    NotificationsPageMixin,
    SettingsPageMixin,
    SharingPageMixin,
    StreamSetupPageMixin,
)
from .theme import (
    COLOR_PALETTES,
    build_themed_gtk_css,
    get_active_dark_mode,
    set_active_color_palette,
    set_active_dark_mode,
    set_active_icon_style,
    set_active_ui_theme,
)
from .refresh_pump import AppRefreshPumpMixin
from .widgets import DialogsAndClipboardMixin
from .windows import (
    AppLauncherGtkWindow,
    ScreenEdgeCrossController,
    SystemTrayController,
    TrayPopoverGtkWindow,
)

class SamyujGtkDesktopApp(
    HomePageMixin,
    AppsPageMixin,
    GalleryPageMixin,
    MessagesPageMixin,
    FilesPageMixin,
    SharingPageMixin,
    SettingsPageMixin,
    NotificationsPageMixin,
    StreamSetupPageMixin,
    DialogsAndClipboardMixin,
    AppRefreshPumpMixin,
    Gtk.Window,
):

    GTK_MAJOR_VERSION = GTK_MAJOR
    IS_GTK4_BACKEND = IS_GTK4

    TILES = [
        ("appstream", "App streaming", "Stream individual phone apps"),
        ("desktop", "Mobile Desktop", "Full desktop environment from phone"),
        ("screenshare", "Share Screen", "Mirror your PC display on the phone or share phone screen"),
        ("webcam", "Webcam", "Use phone camera as Linux V4L2 webcam"),
        ("crosscontrol", "Cross Control", "Seamless screen-edge mouse & keyboard"),
        ("clipboard", "Smart Clipboard", "Two-way clipboard sync"),
    ]

    GALLERY_PAGE_SIZE = 24
    GALLERY_THUMB_W = 128
    GALLERY_THUMB_H = 96
    GALLERY_MAX_CACHED_PIXBUFS = 48

    NAV_ITEMS = [
        ("home", "Home", "desktop"),
        ("apps", "Phone Apps", "apps"),
        ("gallery", "Gallery", "files"),
        ("messages", "Messages", "apps"),
        ("files", "Browse Files", "files"),
        ("sharing", "Share", "sharing"),
        ("notifications", "Notifications"),
    ]

    HOME_RAIL_FEATURES = [
        ("desktop", "Mobile Desktop", "desktop"),
        ("screenshare", "Share Screen", "desktop"),
        ("appstream", "Phone Apps", "phone"),
        ("webcam", "Webcam", "webcam"),
        ("clipboard", "Smart Clipboard", "clipboard"),
        ("crosscontrol", "Cross Control", "desktop"),
    ]

    PHONE_APPS_GRID = [
        ("desktop", "Mobile Desktop", "desktop"),
        ("screenshare", "Share Screen", "desktop"),
        ("appstream", "App Streaming", "phone"),
        ("webcam", "Webcam", "webcam"),
        ("clipboard", "Smart Clipboard", "clipboard"),
        ("crosscontrol", "Cross Control", "phone"),
    ]

    def __init__(self, harness) -> None:
        self.facade = harness if isinstance(harness, ApplicationFacade) else ApplicationFacade(harness)
        self.gtk_major_version: int = GTK_MAJOR
        self.gtk_version_string: str = get_gtk_version_string()
        self._xvnc_proc: subprocess.Popen | None = None
        ok_init, _ = Gtk.init_check([])
        if not ok_init and ensure_headless_display(force_restart=True):
            ok_init, _ = Gtk.init_check([])
        if ok_init:
            with contextlib.suppress(Exception):
                import gi.overrides.Gtk as _gtk_ov
                _gtk_ov.initialized = True

        self._css_provider: Gtk.CssProvider | None = None
        self.ui_theme: str = str(self.facade.user_settings.get("ui_theme") or "aurora").strip().lower()
        if self.ui_theme in ("aurora_glass", "aurora-glass"):
            self.ui_theme = "aurora"
        if self.ui_theme not in ("material", "tray", "aurora", "gtk"):
            self.ui_theme = "aurora"
        self.color_palette: str = str(self.facade.user_settings.get("color_palette") or "mocha").strip().lower()
        if self.color_palette in ("catppuccin", "catppuccin_mocha", "catppuccin-mocha"):
            self.color_palette = "mocha"
        if self.color_palette not in COLOR_PALETTES:
            self.color_palette = "mocha"
        self.icon_style: str = str(self.facade.user_settings.get("icon_style") or "minimal").strip().lower()
        if self.icon_style not in ("minimal", "toned"):
            self.icon_style = "minimal"
        self.dark_mode: bool = self._resolve_initial_dark_mode()
        self.minimize_to_tray_enabled: bool = bool(self.facade.user_settings.get("minimize_to_tray", True))
        self.is_minimized_to_tray: bool = False
        self._force_quit: bool = False
        self._resize_save_source_id: int = 0

        try:
            saved_w = int(self.facade.user_settings.get("window_width") or 860)
        except Exception:
            saved_w = 860
        try:
            saved_h = int(self.facade.user_settings.get("window_height") or 540)
        except Exception:
            saved_h = 540
        self._saved_win_w: int = max(360, saved_w)
        self._saved_win_h: int = max(280, saved_h)

        self._apply_css()
        self.win = Gtk.Window(title="Samyuj")
        self.win.get_style_context().add_class("sc-window")
        _rw, _rh = _clamp_window_to_screen(self.win, self._saved_win_w, self._saved_win_h)
        self._saved_win_w = max(360, _rw)
        self._saved_win_h = max(280, _rh)
        self.win.set_size_request(360, 280)
        self.win.set_default_size(self._saved_win_w, self._saved_win_h)
        self.win.resize(self._saved_win_w, self._saved_win_h)
        if IS_GTK4:
            with contextlib.suppress(Exception):
                self.win.connect("close-request", lambda w: self._on_main_window_delete_event(w, None))
                self.win.connect("notify::default-width", lambda *_a: self._on_gtk4_size_notify())
                self.win.connect("notify::default-height", lambda *_a: self._on_gtk4_size_notify())
        else:
            self.win.connect("configure-event", self._on_window_configure_event)
            self.win.connect("window-state-event", self._on_window_state_event)
            self.win.connect("delete-event", self._on_main_window_delete_event)
        self.win.connect("destroy", self._on_destroy)

        self.feature_windows: dict[str, Gtk.Window] = {}
        self._seen_session_keys: dict[str, str] = {}
        self._closed_session_keys: dict[str, str] = {}
        self._feature_stop_cooldown: dict[str, float] = {}
        self._phone_on_pc_mode: str = "mirror"
        self._last_main_page: str = "home"
        self._md_selected_orientation: str = "landscape"
        self._md_selected_resolution: str = "1920x1080"
        self._selected_phone_ss_mode: str = "mirror"
        self._selected_pc_ss_mode: str = "mirror"
        self._rail_widgets: dict[str, dict] = {}
        self._grid_widgets: dict[str, dict] = {}
        self._nav_buttons: dict[str, Gtk.Button] = {}
        self._nav_icon_holders: dict[str, tuple[Gtk.Box, str]] = {}
        self._shared_Custom_files: list[dict] = []
        self._current_phonefs_path: str = "/"
        self._last_notif_sig = None
        self._last_shares_sig = None

        self.edge_controller = ScreenEdgeCrossController(self)

        with contextlib.suppress(Exception):
            if not getattr(self.facade, "_media_codec_from_cli", False):
                _pref = str(self.facade.user_settings.get("video_codec") or "").lower()
                if _pref in ("auto", "h264", "vp8", "vp9"):
                    if (str(getattr(self.facade, "present_engine", "gtk")).lower() == "gst"
                            and _pref != "h264"):
                        self.facade.media_codec_pref = "h264"
                        self.facade.events.log(
                            "MEDIA_CODEC_GST_OVERRIDE",
                            f"forcing H264 over saved video_codec={_pref!r} because "
                            f"--present gst decodes H.264 access units",
                            feature="media",
                        )
                    else:
                        self.facade.media_codec_pref = _pref

        with contextlib.suppress(Exception):
            _remb = int(self.facade.user_settings.get("remb_floor_kbps") or 0)
            if _remb > 0:
                self.facade.set_remb_floor(_remb)

        self._init_clipboard_backends()

        self._video_wakeup_pending = False
        self._frame_wakeup_evt: asyncio.Event | None = None
        self._ui_loop: asyncio.AbstractEventLoop | None = None
        self._gallery_pixbuf_lru: "collections.OrderedDict[str, GdkPixbuf.Pixbuf]" = collections.OrderedDict()
        self._gallery_page_idx: int = 0
        self._gallery_all_entries: list[dict] = []
        self._gallery_load_gen: int = 0
        self.facade.on_wallpaper_cb = lambda wp: GLib.idle_add(self._on_wallpaper_updated, wp)
        self.facade.on_notification_cb = lambda item: GLib.idle_add(self._on_notification_updated, item)
        self.facade.on_feature_start_cb = lambda fid, **kw: GLib.idle_add(self._on_phone_feature_started, fid, kw)
        self.facade.on_messages_updated_cb = lambda: GLib.idle_add(self._on_messages_updated)
        self.facade.on_frame_ready_cb = self._on_media_frame_ready
        self.facade.on_desktop_waiting_cb = (
            lambda detail="": GLib.idle_add(self._desktop_accept_prompt, str(detail)))

        self.app_launcher_win = AppLauncherGtkWindow(self)
        self._build_shell()
        self.tray_popover = TrayPopoverGtkWindow(self)
        self.tray_controller = SystemTrayController(self)

        self._refresh_all()
        GLib.timeout_add(350, self._auto_arm_v4l2_on_startup)

    def _init_clipboard_backends(self) -> None:
        self._last_clip_poll = 0.0
        self._clip_cache: str | None = None
        self._clip_cache_mono: float = 0.0
        self._clip_refresh_inflight: bool = False
        self._clip_refresh_since: float = 0.0

        if IS_GTK4:
            disp = Gdk.Display.get_default()
            self._gtk_clipboard = disp.get_clipboard() if disp is not None else None
            clip_sync = getattr(self.facade, "clip_sync", None)
            if clip_sync is not None and self._gtk_clipboard is not None:
                def _clip_getter_gtk4():
                    return self._clip_cache

                def _clip_setter_gtk4(text: str) -> bool:
                    try:
                        self._clip_cache = str(text or "")
                        self._clip_cache_mono = time.monotonic()
                        self._gtk_clipboard.set(self._clip_cache)
                        return True
                    except Exception:
                        return False

                clip_sync.register_gtk_backend(_clip_getter_gtk4, _clip_setter_gtk4)
            return

        self._gtk_clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)

        def _clip_cache_cb(_cb, txt, *_a):
            self._clip_refresh_inflight = False
            if isinstance(txt, str):
                self._clip_cache = txt
                self._clip_cache_mono = time.monotonic()
                cs = getattr(self.facade, "clip_sync", None)
                if cs is not None and self.facade._live_ws() is not None:
                    with contextlib.suppress(Exception):
                        cs.on_pc_clipboard_polled(txt)

        def _clip_refresh(self_=None):
            now_m = time.monotonic()
            if self._clip_refresh_inflight and (now_m - self._clip_refresh_since) < 1.0:
                return
            self._clip_refresh_inflight = True
            self._clip_refresh_since = now_m
            with contextlib.suppress(Exception):
                if threading.current_thread() is threading.main_thread():
                    self._gtk_clipboard.request_text(_clip_cache_cb)
                else:
                    GLib.idle_add(lambda: (self._gtk_clipboard.request_text(_clip_cache_cb), False)[1])

        clip_sync = getattr(self.facade, "clip_sync", None)
        if clip_sync is not None:
            def _clip_getter():
                _clip_refresh()
                return self._clip_cache

            def _clip_setter(text: str) -> bool:
                try:
                    self._clip_cache = str(text or "")
                    self._clip_cache_mono = time.monotonic()
                    self._gtk_clipboard.set_text(text or "", -1)
                    self._gtk_clipboard.store()
                    return True
                except Exception:
                    return False

            clip_sync.register_gtk_backend(_clip_getter, _clip_setter)
            with contextlib.suppress(Exception):
                def _owner_changed(cb, _ev=None):
                    _clip_refresh()
                    if self.facade._live_ws() is not None:
                        with contextlib.suppress(Exception):
                            cb.request_text(
                                lambda _c, txt, *_a: clip_sync.on_pc_clipboard_polled(txt))
                self._gtk_clipboard.connect("owner-change", _owner_changed)
                self._gtk_clip_owner_changed = _owner_changed
                self.win.connect("focus-in-event", lambda *_a: (_owner_changed(self._gtk_clipboard, None), False)[1])

            with contextlib.suppress(Exception):
                self._gtk_primary_clipboard = Gtk.Clipboard.get(Gdk.SELECTION_PRIMARY)
                self._primary_clip_cache: str | None = None

                def _primary_setter(text: str) -> bool:
                    try:
                        self._gtk_primary_clipboard.set_text(text or "", -1)
                        return True
                    except Exception:
                        return False

                def _primary_getter():
                    return self._primary_clip_cache

                def _primary_owner_changed(cb, _ev=None):
                    with contextlib.suppress(Exception):
                        if not bool((getattr(self.facade, "user_settings", {}) or {})
                                    .get("clipboard_sync_primary", False)):
                            return
                        def _prim_cb(_c, txt, *_a):
                            if not isinstance(txt, str) or not txt:
                                return
                            self._primary_clip_cache = txt
                            cs_p = getattr(self.facade, "clip_sync", None)
                            if cs_p is not None and self.facade._live_ws() is not None:
                                with contextlib.suppress(Exception):
                                    cs_p.on_pc_clipboard_polled(txt)
                        cb.request_text(_prim_cb)

                self._gtk_primary_clipboard.connect("owner-change", _primary_owner_changed)
                clip_sync.register_gtk_primary_backend(_primary_getter, _primary_setter)

    def set_window_size(self, width: int, height: int, persist: bool = True) -> tuple[int, int]:
        w = max(360, int(width))
        h = max(280, int(height))
        self._saved_win_w = w
        self._saved_win_h = h
        with contextlib.suppress(Exception):
            self.win.set_default_size(w, h)
            self.win.resize(w, h)
        if persist:
            self._persist_window_size_now()
        return w, h

    def get_remembered_window_size(self) -> tuple[int, int]:
        return int(self._saved_win_w), int(self._saved_win_h)

    def _persist_window_size_now(self) -> bool:
        self._resize_save_source_id = 0
        with contextlib.suppress(Exception):
            self.facade.save_user_settings(
                window_width=int(self._saved_win_w),
                window_height=int(self._saved_win_h),
            )
        return False

    def _on_gtk4_size_notify(self) -> None:
        if getattr(self, "is_minimized_to_tray", False):
            return
        with contextlib.suppress(Exception):
            w, h = self.win.get_size()
            if w >= 360 and h >= 280 and (w != self._saved_win_w or h != self._saved_win_h):
                self._saved_win_w = w
                self._saved_win_h = h
                self._persist_window_size_now()

    def _on_window_configure_event(self, _win: Gtk.Widget, event) -> bool:
        if getattr(self, "is_minimized_to_tray", False):
            return False
        with contextlib.suppress(Exception):
            w = int(getattr(event, "width", 0) or 0)
            h = int(getattr(event, "height", 0) or 0)
            if w >= 360 and h >= 280 and (w != self._saved_win_w or h != self._saved_win_h):
                self._saved_win_w = w
                self._saved_win_h = h
                if self._resize_save_source_id:
                    with contextlib.suppress(Exception):
                        GLib.source_remove(self._resize_save_source_id)
                self._resize_save_source_id = GLib.timeout_add(250, self._persist_window_size_now)
        return False

    def set_minimize_to_tray_enabled(self, enabled: bool, persist: bool = True) -> None:
        self.minimize_to_tray_enabled = bool(enabled)
        if persist:
            with contextlib.suppress(Exception):
                self.facade.save_user_settings(minimize_to_tray=self.minimize_to_tray_enabled)
        if getattr(self, "minimize_to_tray_switch", None) is not None:
            if bool(self.minimize_to_tray_switch.get_active()) != self.minimize_to_tray_enabled:
                self._updating_theme_switches = True
                try:
                    self.minimize_to_tray_switch.set_active(self.minimize_to_tray_enabled)
                finally:
                    self._updating_theme_switches = False
        if getattr(self, "tray_popover", None) is not None:
            self.tray_popover.refresh_tray_ui()

    def minimize_to_tray(self, reason: str = "user") -> bool:
        with contextlib.suppress(Exception):
            cur_w, cur_h = self.win.get_size()
            if cur_w >= 360 and cur_h >= 280:
                self._saved_win_w = int(cur_w)
                self._saved_win_h = int(cur_h)
        self._persist_window_size_now()
        self.is_minimized_to_tray = True
        with contextlib.suppress(Exception):
            self.win.set_skip_taskbar_hint(True)
            self.win.set_skip_pager_hint(True)
            self.win.hide()
        with contextlib.suppress(Exception):
            self.facade.events.log("UI_MINIMIZE_TO_TRAY", f"reason={reason}", feature="gui")
        if getattr(self, "tray_popover", None) is not None:
            self.tray_popover.refresh_tray_ui()
        return True

    def restore_from_tray(self) -> bool:
        self.is_minimized_to_tray = False
        with contextlib.suppress(Exception):
            self.win.set_skip_taskbar_hint(False)
            self.win.set_skip_pager_hint(False)
            self.win.deiconify()
            self.win.resize(self._saved_win_w, self._saved_win_h)
            self.win.show_all()
            self.win.present()
        with contextlib.suppress(Exception):
            self.facade.events.log("UI_RESTORE_FROM_TRAY", "main window restored", feature="gui")
        if getattr(self, "tray_popover", None) is not None:
            self.tray_popover.refresh_tray_ui()
        return True

    def _on_window_state_event(self, _win: Gtk.Widget, event) -> bool:
        with contextlib.suppress(Exception):
            changed = bool(event.changed_mask & Gdk.WindowState.ICONIFIED)
            iconified = bool(event.new_window_state & Gdk.WindowState.ICONIFIED)
            if changed and iconified and getattr(self, "minimize_to_tray_enabled", True):
                GLib.idle_add(lambda: (self.minimize_to_tray(reason="window-state-iconified"), False)[1])
        return False

    def _on_main_window_delete_event(self, _win: Gtk.Widget, _event=None) -> bool:
        if getattr(self, "_force_quit", False):
            self._persist_window_size_now()
            return False
        if getattr(self, "minimize_to_tray_enabled", True) or bool(
            self.facade.user_settings.get("start_background", False)
        ):
            self.minimize_to_tray(reason="delete-event")
            return True
        self._persist_window_size_now()
        return False

    def quit_application(self) -> None:
        self._force_quit = True
        self._persist_window_size_now()
        with contextlib.suppress(Exception):
            self.edge_controller.stop()
        with contextlib.suppress(Exception):
            if getattr(self, "tray_controller", None) is not None:
                self.tray_controller.shutdown()
        for w_attr in ("tray_popover", "app_launcher_win", "win"):
            w_obj = getattr(self, w_attr, None)
            if w_obj is not None:
                with contextlib.suppress(Exception):
                    w_obj.destroy()
        with contextlib.suppress(Exception):
            self.facade.request_stop()
        if getattr(self, "_frame_wakeup_evt", None) is not None:
            with contextlib.suppress(Exception):
                self._frame_wakeup_evt.set()

    def open_app_launcher_window(self) -> AppLauncherGtkWindow:
        if getattr(self, "app_launcher_win", None) is None:
            self.app_launcher_win = AppLauncherGtkWindow(self)
        if self.facade._live_ws() is not None and not getattr(self.facade, "app_list", None):
            self._spawn(self.facade.request("applist"))
        self.app_launcher_win.open_smart_view()
        if hasattr(self, "on_apps_tab_opened"):
            self.on_apps_tab_opened()
        return self.app_launcher_win

    def toggle_tray_popover(self) -> TrayPopoverGtkWindow:
        if getattr(self, "tray_popover", None) is None:
            self.tray_popover = TrayPopoverGtkWindow(self)
        if self.tray_popover.get_visible():
            self.tray_popover.hide()
        else:
            self.tray_popover.refresh_tray_ui()
            self.tray_popover.show_all()
            self.tray_popover.present()
        return self.tray_popover

    def _refresh_v4l2_ui(self) -> bool:
        for fw in list(getattr(self, "feature_windows", {}).values()):
            if hasattr(fw, "_sync_webcam_v4l2_btn"):
                with contextlib.suppress(Exception):
                    fw._sync_webcam_v4l2_btn()
        return False

    def _auto_arm_v4l2_on_startup(self) -> bool:
        wc = getattr(self.facade, "webcam_controller", None)
        with contextlib.suppress(Exception):
            if not bool((getattr(self.facade, "user_settings", {}) or {})
                        .get("webcam_startup_arm", True)):
                return False
        if wc is not None and wc.v4l2_auto and not wc.v4l2_user_stopped:
            async def _arm_and_sync():
                await asyncio.to_thread(wc.ensure_startup_ready, False)
                GLib.idle_add(self._refresh_v4l2_ui)
            self._spawn(_arm_and_sync())
        return False

    @staticmethod
    def _detect_system_dark_theme() -> bool:
        with contextlib.suppress(Exception):
            out = subprocess.run(
                ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
                capture_output=True, text=True, timeout=0.6,
            ).stdout.strip().lower()
            if "dark" in out:
                return True
            if "light" in out or "default" in out:
                return False
        with contextlib.suppress(Exception):
            gtk_theme = os.environ.get("GTK_THEME", "").lower()
            if "dark" in gtk_theme:
                return True
        return True

    def _resolve_initial_dark_mode(self) -> bool:
        if "dark_mode" in self.facade.user_settings:
            return bool(self.facade.user_settings.get("dark_mode"))
        if bool(self.facade.user_settings.get("follow_system_theme", False)):
            return self._detect_system_dark_theme()
        return True

    def _apply_css(self) -> None:
        set_active_dark_mode(bool(getattr(self, "dark_mode", True)))
        set_active_ui_theme(str(getattr(self, "ui_theme", "aurora")))
        set_active_color_palette(str(getattr(self, "color_palette", "mocha")))
        set_active_icon_style(str(getattr(self, "icon_style", "minimal")))
        with contextlib.suppress(Exception):
            settings = Gtk.Settings.get_default()
            if settings is not None:
                settings.set_property("gtk-application-prefer-dark-theme", get_active_dark_mode())
        if self._css_provider is None:
            self._css_provider = Gtk.CssProvider()
            apply_css_provider_to_display(self._css_provider)
        css_bytes = build_themed_gtk_css(self.ui_theme, self.color_palette, self.dark_mode)
        load_css_bytes(self._css_provider, css_bytes)

    def _refresh_themed_icons_and_previews(self) -> None:
        if getattr(self, "handset_preview", None) is not None and hasattr(self.handset_preview, "refresh_theme_bezel"):
            with contextlib.suppress(Exception):
                self.handset_preview.refresh_theme_bezel()

        for _pid, (holder, ico_kind) in getattr(self, "_nav_icon_holders", {}).items():
            for ch in list(holder.get_children()):
                holder.remove(ch)
            holder.pack_start(make_cairo_icon(ico_kind, 18), False, False, 0)
            holder.show_all()

        for feat_id, rw in self._rail_widgets.items():
            icon_holder = rw.get("icon_holder")
            png_key = rw.get("png_key", "desktop")
            if isinstance(icon_holder, Gtk.Box):
                for ch in list(icon_holder.get_children()):
                    icon_holder.remove(ch)
                icon_holder.pack_start(make_themed_feature_icon(feat_id, png_key, 24), False, False, 0)
                icon_holder.show_all()

        for feat_id, gw in self._grid_widgets.items():
            icon_holder = gw.get("icon_holder")
            png_key = gw.get("png_key", "desktop")
            if isinstance(icon_holder, Gtk.Box):
                for ch in list(icon_holder.get_children()):
                    icon_holder.remove(ch)
                if feat_id in ("share_pc_screen", "share_phone_screen"):
                    p_kind = "pc_mirror" if feat_id == "share_pc_screen" else "phone_mirror"
                    icon_holder.pack_start(
                        Gtk.Image.new_from_pixbuf(make_setup_preview_pixbuf(p_kind, 148, 92)),
                        False, False, 0,
                    )
                else:
                    icon_holder.pack_start(make_themed_feature_icon(feat_id, png_key, 38), False, False, 0)
                icon_holder.show_all()

        if getattr(self, "settings_theme_combo", None) is not None:
            if self.settings_theme_combo.get_active_id() != self.ui_theme:
                self._updating_theme_switches = True
                try:
                    self.settings_theme_combo.set_active_id(self.ui_theme)
                finally:
                    self._updating_theme_switches = False

        if getattr(self, "settings_palette_combo", None) is not None:
            if self.settings_palette_combo.get_active_id() != self.color_palette:
                self._updating_theme_switches = True
                try:
                    self.settings_palette_combo.set_active_id(self.color_palette)
                finally:
                    self._updating_theme_switches = False

        if getattr(self, "tray_popover", None) is not None:
            self.tray_popover.refresh_tray_ui()
        if getattr(self, "app_launcher_win", None) is not None:
            self.app_launcher_win.rebuild_content()
        if hasattr(self, "_refresh_android_apps_flow"):
            self._refresh_android_apps_flow(force=True)
        if getattr(self, "win", None) is not None:
            with contextlib.suppress(Exception):
                self.win.queue_draw()

    def set_ui_theme(self, theme_id: str, persist: bool = True) -> None:
        tid = str(theme_id or "aurora").strip().lower()
        if tid in ("aurora_glass", "aurora-glass"):
            tid = "aurora"
        if tid not in ("material", "tray", "aurora", "gtk"):
            tid = "aurora"
        self.ui_theme = tid
        if persist:
            with contextlib.suppress(Exception):
                self.facade.save_user_settings(ui_theme=self.ui_theme)
        self._apply_css()
        self._refresh_themed_icons_and_previews()

    def set_color_palette(self, palette_id: str, persist: bool = True) -> None:
        pid = str(palette_id or "mocha").strip().lower()
        if pid in ("catppuccin", "catppuccin_mocha", "catppuccin-mocha"):
            pid = "mocha"
        if pid not in COLOR_PALETTES:
            pid = "mocha"
        self.color_palette = pid
        if persist:
            with contextlib.suppress(Exception):
                self.facade.save_user_settings(color_palette=self.color_palette)
        self._apply_css()
        self._refresh_themed_icons_and_previews()

    def set_icon_style(self, style_id: str, persist: bool = True) -> None:
        sid = "toned" if str(style_id or "minimal").strip().lower() == "toned" else "minimal"
        self.icon_style = sid
        if persist:
            with contextlib.suppress(Exception):
                self.facade.save_user_settings(icon_style=self.icon_style)
        self._apply_css()
        self._refresh_themed_icons_and_previews()

    def set_dark_mode(self, enabled: bool, persist: bool = True) -> None:
        self.dark_mode = bool(enabled)
        if persist:
            with contextlib.suppress(Exception):
                self.facade.save_user_settings(dark_mode=self.dark_mode)
        self._apply_css()
        if getattr(self, "theme_toggle_btn", None) is not None:
            self.theme_toggle_btn.set_label("Light" if self.dark_mode else "Dark")
        if getattr(self, "dark_mode_switch", None) is not None:
            if bool(self.dark_mode_switch.get_active()) != self.dark_mode:
                self._updating_theme_switches = True
                try:
                    self.dark_mode_switch.set_active(self.dark_mode)
                finally:
                    self._updating_theme_switches = False
        self._refresh_themed_icons_and_previews()

    def toggle_dark_mode(self, *_args) -> None:
        self.set_dark_mode(not bool(getattr(self, "dark_mode", False)), persist=True)

    def _spawn(self, coro) -> None:
        with contextlib.suppress(Exception):
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(coro)
                return
        with contextlib.suppress(Exception):
            asyncio.run(coro)

    def _build_shell(self) -> None:
        outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.win.add(outer)

        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        sidebar.set_size_request(200, -1)
        sidebar.get_style_context().add_class("sidebar")
        outer.pack_start(sidebar, False, False, 0)

        brand_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
        brand_row.set_margin_start(8)
        brand_row.set_margin_bottom(10)
        brand_row.pack_start(make_cairo_icon("desktop", 20), False, False, 0)
        brand_lbl = Gtk.Label(label="Samyuj", xalign=0.0)
        brand_lbl.get_style_context().add_class("brand-title")
        brand_row.pack_start(brand_lbl, True, True, 0)
        sidebar.pack_start(brand_row, False, False, 0)

        for item in self.NAV_ITEMS:
            if len(item) == 2:
                continue
            page_id, label_txt, ico_kind = item
            if page_id == "notifications":
                continue
            btn = self._create_nav_button(page_id, label_txt, ico_kind)
            sidebar.pack_start(btn, False, False, 1)

        div = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        div.get_style_context().add_class("sidebar-divider")
        sidebar.pack_start(div, False, False, 4)

        settings_btn = self._create_nav_button("settings", "Settings", "settings")
        sidebar.pack_start(settings_btn, False, False, 1)

        spacer = Gtk.Box()
        spacer.set_vexpand(True)
        sidebar.pack_start(spacer, True, True, 0)

        pair_btn = Gtk.Button(label="Pair with QR")
        pair_btn.get_style_context().add_class("pair-qr-btn")
        pair_btn.connect("clicked", lambda *_: self.open_pair_dialog())
        sidebar.pack_end(pair_btn, False, False, 2)

        bottom_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.min_to_tray_btn = Gtk.Button(label="Tray")
        self.min_to_tray_btn.set_tooltip_text("Minimize Main Window to System Tray Icon")
        self.min_to_tray_btn.get_style_context().add_class("min-tray-btn")
        self.min_to_tray_btn.connect("clicked", lambda *_: self.minimize_to_tray(reason="sidebar-button"))
        bottom_row.pack_start(self.min_to_tray_btn, True, True, 0)

        self.theme_toggle_btn = Gtk.Button(
            label="Light" if getattr(self, "dark_mode", False) else "Dark"
        )
        self.theme_toggle_btn.set_tooltip_text("Switch between Light Mode and Dark Mode")
        self.theme_toggle_btn.get_style_context().add_class("theme-toggle-btn")
        self.theme_toggle_btn.connect("clicked", self.toggle_dark_mode)
        bottom_row.pack_start(self.theme_toggle_btn, True, True, 0)
        sidebar.pack_end(bottom_row, False, False, 2)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        main_box.get_style_context().add_class("main-area")
        outer.pack_start(main_box, True, True, 0)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(140)
        self.stack.get_style_context().add_class("content-area")
        main_box.pack_start(self.stack, True, True, 0)

        def _scrolled(widget: Gtk.Widget) -> Gtk.ScrolledWindow:
            sw = Gtk.ScrolledWindow()
            sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            sw.add(widget)
            with contextlib.suppress(Exception):
                from samyuj.ui.gtk.smooth_scroll import attach_smooth_scroll
                attach_smooth_scroll(sw)
            return sw

        self.stack.add_named(_scrolled(self._build_home_page()), "home")
        self.stack.add_named(_scrolled(self._build_apps_page()), "apps")
        self.stack.add_named(_scrolled(self._build_gallery_page()), "gallery")
        self.stack.add_named(_scrolled(self._build_messages_page()), "messages")
        self.stack.add_named(_scrolled(self._build_files_page()), "files")
        self.stack.add_named(_scrolled(self._build_sharing_page()), "sharing")
        self.stack.add_named(_scrolled(self._build_settings_page()), "settings")
        self.stack.add_named(_scrolled(self._build_notifications_tab_page()), "notifications")
        self.stack.add_named(_scrolled(self._build_mobile_desktop_setup_page()), "setup_desktop")
        self.stack.add_named(_scrolled(self._build_screenshare_hub_page()), "setup_screenshare_hub")
        self.stack.add_named(_scrolled(self._build_phone_screenshare_setup_page()), "setup_phone_screenshare")
        self.stack.add_named(_scrolled(self._build_pc_screenshare_setup_page()), "setup_pc_screenshare")

        self.switch_page("home")

    def _create_nav_button(self, page_id: str, label_txt: str, ico_kind: str) -> Gtk.Button:
        btn = Gtk.Button()
        btn.get_style_context().add_class("nav-btn")
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        ico_holder = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        ico_holder.pack_start(make_cairo_icon(ico_kind, 18), False, False, 0)
        row.pack_start(ico_holder, False, False, 0)
        lbl = Gtk.Label(label=label_txt, xalign=0.0)
        row.pack_start(lbl, True, True, 0)
        btn.add(row)
        btn.connect("clicked", lambda *_, pid=page_id: self.switch_page(pid))
        self._nav_buttons[page_id] = btn
        self._nav_icon_holders[page_id] = (ico_holder, ico_kind)
        return btn

    def switch_page(self, page_id: str) -> None:
        if page_id in ("home", "apps", "gallery", "messages", "files", "sharing", "settings"):
            self._last_main_page = page_id
        child = self.stack.get_child_by_name(page_id)
        if child is not None:
            child.show_all()
            self.stack.set_visible_child_name(page_id)
        for pid, btn in self._nav_buttons.items():
            ctx = btn.get_style_context()
            if pid == page_id:
                ctx.add_class("nav-btn-active")
            else:
                ctx.remove_class("nav-btn-active")
        if page_id != "gallery" and hasattr(self, "_evict_all_gallery_pixbufs"):
            self._evict_all_gallery_pixbufs()
        if page_id == "sharing":
            self._refresh_shares_list(force=True)
        elif page_id == "files":
            self._refresh_files_page()
        elif page_id == "apps":
            if hasattr(self, "on_apps_tab_opened"):
                self.on_apps_tab_opened()
            else:
                self._refresh_android_apps_flow(force=True)
            if self.facade._live_ws() is not None and not getattr(self.facade, "app_list", None):
                self._spawn(self.facade.request("applist"))
        elif page_id == "gallery":
            self._refresh_gallery_page()
        elif page_id == "messages":
            if self.facade._live_ws() is not None and not (getattr(self.facade, "messages_state", {}) or {}).get("messages"):
                self._spawn(self.facade.request("messages-sync"))
            self._refresh_messages_page(force=True)
