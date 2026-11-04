from __future__ import annotations
import threading

import contextlib
import time

from samyuj.protocol.constants import PHONE_EXTEND_PRESETS
from samyuj.ui.gtk.gtk_compat import Gdk, GdkPixbuf, GLib, Gtk, IS_GTK4, Pango

from samyuj.media.v4l2 import is_v4l2loopback_exclusive_caps_ok
from samyuj.ui.gtk.assets import _clamp_window_to_screen, open_dialog_nonblocking
from samyuj.ui.gtk.render import (
    _LIBSC_FAST,
    apply_uv_swap_state,
    get_prepared_frame_bgrx,
    is_uv_swapped,
    target_render_size,
)
from .cross_control import _GDK_KEY_TO_ANDROID

class FeatureStreamGtkWindow(Gtk.Window):

    def __init__(self, app: "SamyujGtkDesktopApp", feature_id: str, title: str,
                 stream_session_id=None, stream_pkg: str | None = None,
                 stream_display_id: int | None = None) -> None:
        super().__init__(title=f"Samyuj — {title}")
        self.app = app
        self.facade = app.facade
        self.feature_id = feature_id
        self.base_title = title
        self.stream_session_id = stream_session_id
        self.stream_pkg = stream_pkg
        self.stream_display_id = stream_display_id
        self._pixbuf_store: GdkPixbuf.Pixbuf | None = None
        self._lazy_rgb_frame: tuple | None = None
        self._cairo_surface = None
        self._cairo_buf: bytearray | None = None
        self._cairo_dims: tuple[int, int] | None = None
        self._cairo_ring_surfaces: dict[int, tuple] = {}
        self._last_pts = None
        self._last_render_mono: float = 0.0
        self._fit_mode: bool = True
        self._rotated: bool = False
        self._video_rect: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)
        self._last_frame_dims: tuple[int, int] | None = None
        self._last_adapted_aspect: tuple[bool, int, int] | None = None

        _init_dw, _init_dh = getattr(self.facade, "desktop_res", (1920, 1080))
        if feature_id in ("phone_on_pc", "appstream") or (feature_id == "desktop" and _init_dh > _init_dw):
            _clamp_window_to_screen(self, 410, 860)
        elif feature_id == "desktop":
            _clamp_window_to_screen(self, 1360, 800)
        else:
            _clamp_window_to_screen(self, 960, 600)
        self.connect("delete-event", self._on_delete_event)

        self.header_bar = Gtk.HeaderBar()
        self.header_bar.set_show_close_button(True)
        self.header_bar.set_property("spacing", 2)
        self.header_bar.set_title("")
        self.header_bar.get_style_context().add_class("fw-headerbar")
        self.set_titlebar(self.header_bar)

        title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.title_lbl = Gtk.Label(label=title, xalign=0.0)
        self.title_lbl.get_style_context().add_class("fw-title")
        self.title_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        self.title_lbl.set_width_chars(1)
        self.title_lbl.set_max_width_chars(14)
        title_box.pack_start(self.title_lbl, True, True, 2)

        self.status_lbl = Gtk.Label(label="Connecting…", xalign=0.0)
        self.status_lbl.get_style_context().add_class("fw-sub")
        self.status_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        self.status_lbl.set_width_chars(1)
        self.status_lbl.set_max_width_chars(14)
        self.header_bar.pack_start(title_box)

        stop_btn = Gtk.Button(label="End")
        stop_btn.set_tooltip_text("End session")
        stop_btn.get_style_context().add_class("fw-end-btn")
        stop_btn.connect("clicked", lambda *_: self.app.stop_feature(self.feature_id))
        self.header_bar.pack_end(stop_btn)

        self.res_combo: Gtk.ComboBoxText | None = None
        self._updating_res_combo: bool = False
        self.view_combo: Gtk.ComboBoxText | None = None
        self._updating_view_combo: bool = False
        diag_btn = Gtk.Button(label="Diag")
        diag_btn.set_tooltip_text(
            "Media diagnostics - measure what is corrupting the video and save "
            "a report (packet loss, event-loop lag, UI drops, keyframe resets, "
            "and whether it correlates with dragging)")
        diag_btn.get_style_context().add_class("fw-ctrl-btn")
        diag_btn.connect("clicked", self._on_diagnostics_clicked)
        self.header_bar.pack_end(diag_btn)
        self._init_feature_header_controls(feature_id)
        self._init_video_canvas_and_events(feature_id)

    def _init_feature_header_controls(self, feature_id: str) -> None:
        if feature_id in ("desktop", "phone_on_pc", "appstream"):
            fs_btn = Gtk.Button(label="Full")
            fs_btn.set_tooltip_text("Toggle Full screen (F11)")
            fs_btn.get_style_context().add_class("fw-ctrl-btn")
            fs_btn.connect("clicked", self._on_fullscreen_clicked)
            self.header_bar.pack_end(fs_btn)

            rot_btn = Gtk.Button(label="Rotate")
            rot_btn.set_tooltip_text("Rotate orientation (Portrait ↔ Landscape)")
            rot_btn.get_style_context().add_class("fw-ctrl-btn")
            rot_btn.connect("clicked", self._on_rotate_clicked)
            self.header_bar.pack_end(rot_btn)

            native_btn = Gtk.Button(label="1:1")
            native_btn.set_tooltip_text("Resize window to 1:1 stream aspect")
            native_btn.get_style_context().add_class("fw-ctrl-btn")
            native_btn.connect("clicked", self._on_native_size_clicked)

            self.fit_btn = Gtk.Button(label="Fit")
            self.fit_btn.set_tooltip_text("Toggle Fit / Fill view")
            self.fit_btn.get_style_context().add_class("fw-ctrl-btn")
            self.fit_btn.connect("clicked", self._on_fit_clicked)

            self.res_combo = Gtk.ComboBoxText()
            self.res_combo.set_tooltip_text("Stream Resolution & Orientation")
            _res_presets = (
                ("1280x720", "1280×720"),
                ("1600x900", "1600×900"),
                ("1920x1080", "1920×1080"),
                ("1920x1200", "1920×1200"),
                ("2560x1440", "2560×1440"),
                ("1080x1920", "1080×1920 (P)"),
                ("1080x2400", "1080×2400 (P)"),
                ("720x1600", "720×1600 (P)"),
            )
            for _rk, _rl in _res_presets:
                self.res_combo.append(_rk, _rl)
            _cur_w, _cur_h = getattr(self.facade, "desktop_res", (1920, 1080))
            _cur_id = f"{_cur_w}x{_cur_h}"
            if not self.res_combo.set_active_id(_cur_id):
                self.res_combo.append(_cur_id, f"{_cur_w}×{_cur_h}")
                self.res_combo.set_active_id(_cur_id)
            self.res_combo.connect("changed", self._on_resolution_combo_changed)

            if feature_id in ("desktop", "phone_on_pc"):
                self.view_combo = Gtk.ComboBoxText()
                self.view_combo.set_tooltip_text("Display Mode (svc-1 cmd:30 / cmd:130 changeDisplayView)")
                self.view_combo.append("2", "Desktop")
                self.view_combo.append("3", "Virtual Phone")
                self.view_combo.append("1", "Mirror")
                self.view_combo.set_active_id("1" if feature_id == "phone_on_pc" else "2")
                self.view_combo.connect("changed", self._on_view_combo_changed)

            self.ctrl_menu_btn = Gtk.MenuButton(label="Options")
            self.ctrl_menu_btn.set_tooltip_text("Stream Resolution, Display Mode & View Controls")
            self.ctrl_menu_btn.get_style_context().add_class("fw-ctrl-btn")
            self.ctrl_popover = Gtk.Popover.new(self.ctrl_menu_btn)
            pop_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            pop_box.set_margin_top(10)
            pop_box.set_margin_bottom(10)
            pop_box.set_margin_start(12)
            pop_box.set_margin_end(12)
            pop_box.pack_start(self.status_lbl, False, False, 0)
            if self.view_combo is not None:
                v_lbl = Gtk.Label(label="Display Mode", xalign=0.0)
                v_lbl.get_style_context().add_class("panel-sub")
                pop_box.pack_start(v_lbl, False, False, 0)
                pop_box.pack_start(self.view_combo, False, False, 0)
            r_lbl = Gtk.Label(label="Resolution & Orientation", xalign=0.0)
            r_lbl.get_style_context().add_class("panel-sub")
            pop_box.pack_start(r_lbl, False, False, 0)
            pop_box.pack_start(self.res_combo, False, False, 0)
            scale_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            scale_row.pack_start(self.fit_btn, True, True, 0)
            scale_row.pack_start(native_btn, True, True, 0)
            pop_box.pack_start(scale_row, False, False, 0)
            pop_box.show_all()
            self.ctrl_popover.add(pop_box)
            self.ctrl_menu_btn.set_popover(self.ctrl_popover)
            self.header_bar.pack_end(self.ctrl_menu_btn)
        elif feature_id == "webcam":
            cam_btn = Gtk.Button(label="Switch Cam")
            cam_btn.set_tooltip_text("Switch front/back phone camera")
            cam_btn.get_style_context().add_class("fw-ctrl-btn")
            cam_btn.connect("clicked", lambda *_: self.app._spawn(self.facade.request("webcam-switch")))
            self.header_bar.pack_end(cam_btn)

            self.uv_swap_btn = Gtk.Button(
                label="UV Swap: ON" if bool(getattr(self.facade, "uv_swap", True)) else "UV Swap: OFF"
            )
            self.uv_swap_btn.set_tooltip_text(
                "Toggle webcam U/V chroma plane swap (ON by default — fixes blue/red skin tones)"
            )
            self.uv_swap_btn.get_style_context().add_class("fw-ctrl-btn")
            self.uv_swap_btn.connect("clicked", self._on_webcam_uv_swap_clicked)
            self.header_bar.pack_end(self.uv_swap_btn)

            self.v4l2_btn = Gtk.Button(label=self._compute_v4l2_btn_label())
            self.v4l2_btn.set_tooltip_text(
                "Linux V4L2 Virtual Camera (/dev/video10 'Samyuj Webcam') — click to activate or run one-time setup"
            )
            self.v4l2_btn.get_style_context().add_class("fw-ctrl-btn")
            self.v4l2_btn.connect("clicked", self._on_webcam_v4l2_clicked)
            self.header_bar.pack_end(self.v4l2_btn)
        elif feature_id == "screenshare":
            self.ss_rot_btn = Gtk.Button(label="Rotate")
            self.ss_rot_btn.set_tooltip_text(
                "Rotate extended PC display on phone (Landscape 20:9/22:9 ↔ Portrait 9:20/9:22)"
            )
            self.ss_rot_btn.get_style_context().add_class("fw-ctrl-btn")
            self.ss_rot_btn.connect("clicked", self._on_ss_rotate_clicked)
            self.header_bar.pack_end(self.ss_rot_btn)

            self.ss_res_combo = Gtk.ComboBoxText()
            self._updating_ss_res_combo = False
            self.ss_res_combo.set_tooltip_text("Extended PC Display — Mobile Phone Aspect Ratio & Resolution")
            for _rk, _rl in PHONE_EXTEND_PRESETS + (
                ("1920x1080", "1920×1080 (16:9 PC L)"),
                ("1080x1920", "1080×1920 (9:16 PC P)"),
            ):
                self.ss_res_combo.append(_rk, _rl)
            _svw, _svh = (
                self.facade._ss_virtual_size()
                if hasattr(self.facade, "_ss_virtual_size")
                else getattr(self.facade, "ss_virtual_res", (2400, 1080))
            )
            _sv_id = f"{_svw}x{_svh}"
            if not self.ss_res_combo.set_active_id(_sv_id):
                self.ss_res_combo.append(_sv_id, f"{_svw}×{_svh}")
                self.ss_res_combo.set_active_id(_sv_id)
            self.ss_res_combo.connect("changed", self._on_ss_res_combo_changed)

            self.ss_pop_rotate_btn = Gtk.Button(label="Rotate Extended Display (Landscape / Portrait)")
            self.ss_pop_rotate_btn.get_style_context().add_class("secondary-btn")
            self.ss_pop_rotate_btn.connect("clicked", self._on_ss_rotate_clicked)

            self.ss_move_win_btn = Gtk.Button(label="Move Active Window to Phone Display")
            self.ss_move_win_btn.get_style_context().add_class("secondary-btn")
            self.ss_move_win_btn.connect(
                "clicked",
                lambda *_: self.app._spawn(self.facade.request("ss-virtual:send-window")),
            )

            self.ss_ctrl_menu_btn = Gtk.MenuButton(label="Options")
            self.ss_ctrl_menu_btn.set_tooltip_text("Extended PC Display Mobile Aspect Ratio & Rotation")
            self.ss_ctrl_menu_btn.get_style_context().add_class("fw-ctrl-btn")
            self.ss_ctrl_popover = Gtk.Popover.new(self.ss_ctrl_menu_btn)
            ss_pop_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            ss_pop_box.set_margin_top(10)
            ss_pop_box.set_margin_bottom(10)
            ss_pop_box.set_margin_start(12)
            ss_pop_box.set_margin_end(12)
            ss_pop_box.pack_start(self.status_lbl, False, False, 0)
            ss_r_lbl = Gtk.Label(label="Phone Aspect Ratio & Resolution (Extend)", xalign=0.0)
            ss_r_lbl.get_style_context().add_class("panel-sub")
            ss_pop_box.pack_start(ss_r_lbl, False, False, 0)
            ss_pop_box.pack_start(self.ss_res_combo, False, False, 0)
            ss_pop_box.pack_start(self.ss_pop_rotate_btn, False, False, 0)
            ss_pop_box.pack_start(self.ss_move_win_btn, False, False, 0)
            ss_pop_box.show_all()
            self.ss_ctrl_popover.add(ss_pop_box)
            self.ss_ctrl_menu_btn.set_popover(self.ss_ctrl_popover)
            self.header_bar.pack_end(self.ss_ctrl_menu_btn)

    def _init_video_canvas_and_events(self, feature_id: str) -> None:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        root.set_margin_top(0)
        root.set_margin_bottom(0)
        root.set_margin_start(0)
        root.set_margin_end(0)
        self._root_box = root
        self.add(root)

        self.video_da = Gtk.DrawingArea()
        self.video_da.get_style_context().add_class("fw-video-canvas")
        self.video_da.set_hexpand(True)
        self.video_da.set_vexpand(True)
        self.video_da.set_can_focus(True)
        self.video_da.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.SCROLL_MASK
            | Gdk.EventMask.SMOOTH_SCROLL_MASK
        )
        import importlib.util as _ilu
        self._have_gi_cairo = _ilu.find_spec("gi._gi_cairo") is not None
        self._fallback_layout: Gtk.Layout | None = None
        self._fallback_img: Gtk.Image | None = None
        if self._have_gi_cairo:
            if IS_GTK4 and hasattr(self.video_da, "set_draw_func"):
                self.video_da.set_draw_func(lambda da, cr, _w, _h: self._on_video_draw(da, cr))
            else:
                self.video_da.connect("draw", self._on_video_draw)
        self.video_da.connect("size-allocate", self._on_video_size_allocate)
        self.video_da.connect("button-press-event", self._on_button_press)
        self.video_da.connect("button-release-event", self._on_button_release)
        self.video_da.connect("motion-notify-event", self._on_motion)
        self.video_da.connect("scroll-event", self._on_scroll)
        if self._have_gi_cairo:
            root.pack_start(self.video_da, True, True, 0)
        else:
            self.video_overlay = Gtk.Overlay()
            self.video_overlay.set_hexpand(True)
            self.video_overlay.set_vexpand(True)
            self.video_overlay.add(self.video_da)
            self._fallback_layout = Gtk.Layout()
            self._fallback_img = Gtk.Image()
            self._fallback_layout.put(self._fallback_img, 0, 0)
            self.video_overlay.add_overlay(self._fallback_layout)
            self.video_overlay.set_overlay_pass_through(self._fallback_layout, True)
            root.pack_start(self.video_overlay, True, True, 0)

        self.nav_bar: Gtk.Box | None = None
        self.nav_back_btn: Gtk.Button | None = None
        self.nav_home_btn: Gtk.Button | None = None
        self.nav_recents_btn: Gtk.Button | None = None
        if feature_id in ("desktop", "phone_on_pc", "appstream") or str(feature_id).startswith("appstream:"):
            self.nav_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
            self.nav_bar.get_style_context().add_class("fw-navbar")
            nav_inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
            nav_inner.set_halign(Gtk.Align.CENTER)
            self.nav_bar.pack_start(nav_inner, True, False, 0)

            self.nav_back_btn = Gtk.Button(label="Back")
            self.nav_back_btn.set_tooltip_text("Back (Android KEYCODE_BACK = 4)")
            self.nav_back_btn.get_style_context().add_class("fw-nav-btn")
            self.nav_back_btn.connect("clicked", lambda *_: self._on_android_nav_clicked(4))
            nav_inner.pack_start(self.nav_back_btn, False, False, 0)

            self.nav_home_btn = Gtk.Button(label="Home")
            self.nav_home_btn.set_tooltip_text("Home (Android KEYCODE_HOME = 3)")
            self.nav_home_btn.get_style_context().add_class("fw-nav-btn")
            self.nav_home_btn.connect("clicked", lambda *_: self._on_android_nav_clicked(3))
            nav_inner.pack_start(self.nav_home_btn, False, False, 0)

            self.nav_recents_btn = Gtk.Button(label="Recents")
            self.nav_recents_btn.set_tooltip_text("Recents / App Switch (Android KEYCODE_APP_SWITCH = 187)")
            self.nav_recents_btn.get_style_context().add_class("fw-nav-btn")
            self.nav_recents_btn.connect("clicked", lambda *_: self._on_android_nav_clicked(187))
            nav_inner.pack_start(self.nav_recents_btn, False, False, 0)

            root.pack_end(self.nav_bar, False, False, 0)

        self._last_motion_mono = 0.0
        self._frame_err_logged = 0.0
        self._scroll_acc_x = 0.0
        self._scroll_acc_y = 0.0
        self._last_scroll_mono = 0.0
        self._last_scroll_ev_time = -1
        self._last_smooth_scroll_mono = 0.0
        self.connect("key-press-event", self._on_win_key, True)
        self.connect("key-release-event", self._on_win_key, False)
        self._power_vis_state: str = "foreground"
        self._window_state_flags: int = 0
        self.connect("focus-in-event", self._on_focus_in)
        self.connect("focus-out-event", self._on_focus_out)
        self.connect("window-state-event", self._on_window_state_event)
        with contextlib.suppress(Exception):
            self.connect("notify::is-active", self._on_active_notify)
            self.connect("map-event", self._on_map_event)
            self.connect("unmap-event", self._on_unmap_event)
        self.sync_orientation_and_controls()

    @property
    def _pixbuf(self) -> GdkPixbuf.Pixbuf | None:
        if self._pixbuf_store is None and self._lazy_rgb_frame is not None:
            with contextlib.suppress(Exception):
                rgb_fr, tw, th, stride = self._lazy_rgb_frame
                mv = memoryview(rgb_fr.planes[0])
                gbytes = GLib.Bytes.new(mv[:stride * th])
                self._pixbuf_store = GdkPixbuf.Pixbuf.new_from_bytes(
                    gbytes, GdkPixbuf.Colorspace.RGB, False, 8, tw, th, stride
                )
        return self._pixbuf_store

    @_pixbuf.setter
    def _pixbuf(self, val: GdkPixbuf.Pixbuf | None) -> None:
        self._pixbuf_store = val
        if val is None:
            self._lazy_rgb_frame = None
            self._cairo_surface = None
            self._cairo_dims = None
            self._cairo_ring_surfaces.clear()
            if getattr(self, "_fallback_img", None) is not None:
                with contextlib.suppress(Exception):
                    self._fallback_img.clear()

    def adapt_window_to_aspect(self, stream_w: int, stream_h: int, force: bool = False) -> tuple[int, int]:
        if getattr(self, "_is_fs", False):
            return stream_w, stream_h
        sw = max(1, int(stream_w))
        sh = max(1, int(stream_h))
        is_portrait = sh > sw
        sig = (is_portrait, sw, sh)
        if not force and self._last_adapted_aspect == sig:
            return sw, sh
        self._last_adapted_aspect = sig

        wa_w, wa_h = 1920, 1080
        with contextlib.suppress(Exception):
            disp = Gdk.Display.get_default()
            if disp is not None:
                mon = disp.get_primary_monitor() or (disp.get_monitor(0) if disp.get_n_monitors() > 0 else None)
                if mon is not None:
                    wa = mon.get_workarea()
                    if wa.width > 320 and wa.height > 240:
                        wa_w, wa_h = int(wa.width), int(wa.height)

        nav_extra_h = (
            36
            if (getattr(self, "nav_bar", None) is not None and self._should_show_nav_bar())
            else 0
        )
        if is_portrait:
            avail_h = max(480, min(int(wa_h * 0.86), 920))
            canvas_h = max(400, avail_h - nav_extra_h)
            win_w = max(330, min(int(wa_w * 0.90), int(round(canvas_h * (sw / float(sh))))))
            win_h = min(int(wa_h * 0.92), canvas_h + nav_extra_h)
        else:
            avail_w = max(640, min(int(wa_w * 0.78), 1440))
            win_w = avail_w
            canvas_h = max(340, min(int(wa_h * 0.84) - nav_extra_h, int(round(win_w * (sh / float(sw))))))
            if canvas_h < int(round(win_w * (sh / float(sw)))):
                win_w = max(480, int(round(canvas_h * (sw / float(sh)))))
            win_h = canvas_h + nav_extra_h

        with contextlib.suppress(Exception):
            self.set_default_size(win_w, win_h)
            self.resize(win_w, win_h)
        return win_w, win_h

    def _should_show_nav_bar(self) -> bool:
        if bool(getattr(self, "_is_fs", False)):
            return False
        c = self.facade._live_ws() if hasattr(self.facade, "_live_ws") else None
        view = (c.get("desktop_view") if c else None)
        if self.feature_id == "desktop" and view not in (1, 3):
            return False
        return self.feature_id in ("phone_on_pc", "appstream") or str(self.feature_id).startswith("appstream:")

    def _sync_nav_bar_visibility(self) -> None:
        if getattr(self, "nav_bar", None) is None:
            return
        show = self._should_show_nav_bar()
        parent = self.nav_bar.get_parent()
        if show:
            self.nav_bar.set_no_show_all(False)
            if parent is None and getattr(self, "_root_box", None) is not None:
                self._root_box.pack_end(self.nav_bar, False, False, 0)
            self.nav_bar.set_visible(True)
            self.nav_bar.show_all()
        else:
            self.nav_bar.set_no_show_all(True)
            self.nav_bar.hide()
            self.nav_bar.set_visible(False)
            if parent is not None and getattr(self, "_root_box", None) is not None:
                with contextlib.suppress(Exception):
                    self._root_box.remove(self.nav_bar)

    def _compute_window_power_state(self, focused_hint: bool | None = None) -> str:
        if str(getattr(self, "feature_id", "")).strip().lower() == "webcam":
            return "foreground"
        with contextlib.suppress(Exception):
            if not self.get_visible():
                return "hidden"
            if hasattr(self, "get_mapped") and not self.get_mapped():
                return "hidden"
        flags = int(getattr(self, "_window_state_flags", 0) or 0)
        iconified_mask = int(getattr(Gdk.WindowState, "ICONIFIED", 2)) | int(getattr(Gdk.WindowState, "WITHDRAWN", 1))
        if flags & iconified_mask:
            return "hidden"
        if focused_hint is not None:
            return "foreground" if focused_hint else "background"
        with contextlib.suppress(Exception):
            if hasattr(self, "is_active") and bool(self.is_active()):
                return "foreground"
        focused_mask = int(getattr(Gdk.WindowState, "FOCUSED", 128))
        if flags & focused_mask:
            return "foreground"
        return "background"

    def _sync_window_power_state(self, focused_hint: bool | None = None) -> str:
        new_st = self._compute_window_power_state(focused_hint=focused_hint)
        old_st = getattr(self, "_power_vis_state", "foreground")
        self._power_vis_state = new_st
        if hasattr(self.facade, "set_stream_power_state"):
            with contextlib.suppress(Exception):
                self.facade.set_stream_power_state(
                    self.feature_id,
                    new_st,
                    sid=getattr(self, "stream_session_id", None),
                )
        if new_st == "foreground" and old_st in ("background", "hidden"):
            self._last_render_mono = 0.0
            self._last_pts = None
            with contextlib.suppress(Exception):
                GLib.idle_add(self.app._pump_video)
            c = self.facade._live_ws() if hasattr(self.facade, "_live_ws") else None
            if c is not None and hasattr(self.facade, "request_stream_pli"):
                with contextlib.suppress(Exception):
                    self.app._spawn(self.facade.request_stream_pli(c, sid=getattr(self, "stream_session_id", None), force=True))
        return new_st

    def _on_active_notify(self, *_args) -> None:
        with contextlib.suppress(Exception):
            act = bool(self.is_active()) if hasattr(self, "is_active") else None
            self._sync_window_power_state(focused_hint=act)

    def _on_map_event(self, *_args) -> bool:
        with contextlib.suppress(Exception):
            self._sync_window_power_state()
        return False

    def _on_unmap_event(self, *_args) -> bool:
        with contextlib.suppress(Exception):
            if str(getattr(self, "feature_id", "")).strip().lower() == "webcam":
                self._power_vis_state = "foreground"
                return False
            self._power_vis_state = "hidden"
            if hasattr(self.facade, "set_stream_power_state"):
                self.facade.set_stream_power_state(self.feature_id, "hidden", sid=getattr(self, "stream_session_id", None))
        return False

    def _on_window_state_event(self, _widget: Gtk.Widget, event) -> bool:
        with contextlib.suppress(Exception):
            self._window_state_flags = int(event.new_window_state)
            is_fs = bool(event.new_window_state & Gdk.WindowState.FULLSCREEN)
            if is_fs != bool(getattr(self, "_is_fs", False)):
                self._is_fs = is_fs
                self._sync_nav_bar_visibility()
            self._sync_window_power_state()
        return False

    def _compute_v4l2_btn_label(self) -> str:
        wc = getattr(self.facade, "webcam_controller", None)
        if wc is None:
            return "Virtual Cam: OFF"
        wr = getattr(wc, "v4l2_writer", None)
        if wr is not None and getattr(wr, "state", "") == "running":
            dev = getattr(wr, "resolved_device", None) or "/dev/video10"
            if not str(dev).startswith("sink:"):
                with contextlib.suppress(Exception):
                    
                    if not is_v4l2loopback_exclusive_caps_ok(devices=[]):
                        return f"Virtual Cam: Fix ({dev})"
            return f"Virtual Cam: {dev}"
        return "Virtual Cam: Setup"

    def _sync_webcam_v4l2_btn(self) -> None:
        btn = getattr(self, "v4l2_btn", None)
        if btn is not None:
            btn.set_label(self._compute_v4l2_btn_label())

    def _on_webcam_v4l2_clicked(self, *_args) -> None:
        wc = getattr(self.facade, "webcam_controller", None)
        if wc is None:
            return
        btn = getattr(self, "v4l2_btn", None)
        if btn is not None:
            btn.set_label("Virtual Cam: Starting…")
        def _bg_setup():
            try:
                wc.explicit_setup(interactive=True)
            finally:
                GLib.idle_add(self._sync_webcam_v4l2_btn)
                if hasattr(self.app, "_refresh_v4l2_ui"):
                    GLib.idle_add(self.app._refresh_v4l2_ui)
        threading.Thread(target=_bg_setup, daemon=True).start()

    def _sync_webcam_uv_swap_btn(self) -> None:
        btn = getattr(self, "uv_swap_btn", None)
        if btn is not None:
            on = bool(getattr(self.facade, "uv_swap", True))
            btn.set_label("UV Swap: ON" if on else "UV Swap: OFF")
        self._sync_webcam_v4l2_btn()

    def _on_webcam_uv_swap_clicked(self, *_args) -> None:
        new_state = not bool(getattr(self.facade, "uv_swap", True))
        if hasattr(self.facade, "set_uv_swap"):
            self.facade.set_uv_swap(new_state)
        else:
            self.facade.uv_swap = new_state
        self._sync_webcam_uv_swap_btn()
        if getattr(self.app, "uv_swap_switch", None) is not None:
            if bool(self.app.uv_swap_switch.get_active()) != new_state:
                self.app._updating_uv_swap_switch = True
                try:
                    self.app.uv_swap_switch.set_active(new_state)
                finally:
                    self.app._updating_uv_swap_switch = False
        self._last_pts = None
        GLib.idle_add(self.app._pump_video)

    def sync_orientation_and_controls(self, force_resize: bool = False) -> None:
        self._sync_nav_bar_visibility()
        self._sync_webcam_uv_swap_btn()
        c = self.facade._live_ws() if hasattr(self.facade, "_live_ws") else None
        view = (c.get("desktop_view") if c else None) or 2
        as_pkg = (c.get("as_pkg") if c else None)
        eff_pkg = self.stream_pkg or as_pkg
        if self.feature_id == "desktop":
            self.title_lbl.set_text("Mobile Desktop")
        elif self.feature_id == "phone_on_pc":
            if view == 3 or eff_pkg == "mobile" or getattr(self.app, "_phone_on_pc_mode", "mirror") == "mobile":
                self.title_lbl.set_text("Mobile Desktop · Virtual Screen")
            else:
                self.title_lbl.set_text("Mobile Desktop · Mirror")
        elif self.feature_id == "appstream" or str(self.feature_id).startswith("appstream:"):
            self.title_lbl.set_text(f"App · {eff_pkg}" if eff_pkg else "Phone App Stream")
        if getattr(self, "view_combo", None) is not None:
            self._updating_view_combo = True
            try:
                if self.feature_id == "phone_on_pc":
                    target_v = 3 if (view == 3 or eff_pkg == "mobile" or getattr(self.app, "_phone_on_pc_mode", "mirror") == "mobile") else 1
                else:
                    target_v = 3 if view == 3 else (1 if view == 1 else 2)
                self.view_combo.set_active_id(str(target_v))
            finally:
                self._updating_view_combo = False

        if self._last_frame_dims is not None and not force_resize:
            fw, fh = self._last_frame_dims
            self.adapt_window_to_aspect(fw, fh, force=False)
            return

        if self.feature_id in ("phone_on_pc", "appstream") or (self.feature_id == "desktop" and (view in (1, 3) or as_pkg in ("mirror", "mobile"))):
            pnat = ((getattr(self.facade, "phone_resolutions", {}) or {}).get("appstream") or {}).get("phoneNative") or {}
            nw = int(pnat.get("width") or 1080)
            nh = int(pnat.get("height") or 2400)
            tw, th = min(nw, nh), max(nw, nh)
        elif self.feature_id == "desktop":
            tw, th = getattr(self.facade, "desktop_res", (1920, 1080))
        elif self.feature_id == "screenshare" and str(getattr(self.facade, "ss_share_type", "extend")).lower() == "extend":
            tw, th = (
                self.facade._ss_virtual_size()
                if hasattr(self.facade, "_ss_virtual_size")
                else getattr(self.facade, "ss_virtual_res", (2400, 1080))
            )
            if getattr(self, "ss_res_combo", None) is not None:
                _scid = f"{tw}x{th}"
                self._updating_ss_res_combo = True
                try:
                    if not self.ss_res_combo.set_active_id(_scid):
                        self.ss_res_combo.append(_scid, f"{tw}×{th}")
                        self.ss_res_combo.set_active_id(_scid)
                finally:
                    self._updating_ss_res_combo = False
        else:
            tw, th = 960, 600

        if self.res_combo is not None:
            cid = f"{tw}x{th}"
            self._updating_res_combo = True
            try:
                if not self.res_combo.set_active_id(cid):
                    self.res_combo.append(cid, f"{tw}×{th}")
                    self.res_combo.set_active_id(cid)
            finally:
                self._updating_res_combo = False

        self.adapt_window_to_aspect(tw, th, force=force_resize)

    def _on_android_nav_clicked(self, keycode: int) -> None:
        c = self.facade._live_ws() if hasattr(self.facade, "_live_ws") else None
        if c is None:
            return
        disp, _geom = self._resolve_input_target(c)
        if disp is None:
            disp = 0
        self.app._spawn(self.facade.send_key_tap(c, int(keycode), display_id=disp, quiet=False))
        with contextlib.suppress(Exception):
            self.video_da.grab_focus()

    def _on_win_key(self, _w, event: Gdk.EventKey, pressed: bool) -> bool:
        kv = Gdk.keyval_name(event.keyval) or ""
        if pressed and kv == "Escape":
            if getattr(self, "_is_fs", False):
                self.unfullscreen()
                self._is_fs = False
                self._sync_nav_bar_visibility()
                return True
        if pressed and kv in ("F11", "f11"):
            self._on_fullscreen_clicked()
            return True
        if self.feature_id not in ("desktop", "phone_on_pc", "appstream"):
            return False
        _MEDIA_KEY_MAP = {
            "AudioRaiseVolume": 24,
            "AudioLowerVolume": 25,
            "AudioMute": 164,
            "AudioPlay": 85,
            "AudioPause": 127,
            "AudioStop": 86,
            "AudioNext": 87,
            "AudioPrev": 88,
        }
        is_media_key = kv in _MEDIA_KEY_MAP
        ak = _MEDIA_KEY_MAP.get(kv) if is_media_key else _GDK_KEY_TO_ANDROID.get(kv)
        if ak is None and len(kv) == 1:
            ch = kv.upper()
            if "A" <= ch <= "Z":
                ak = 29 + (ord(ch) - ord("A"))
            elif "0" <= ch <= "9":
                ak = 7 + (ord(ch) - ord("0"))
        if ak is None:
            return False
        c = self.facade._live_ws()
        if c is None:
            return False
        disp, _geom = self._resolve_input_target(c)
        if disp is None:
            disp = 0
        st_mask = getattr(event, "state", 0) or 0
        meta_state = 0
        if st_mask & Gdk.ModifierType.SHIFT_MASK:
            meta_state |= 1
        if st_mask & Gdk.ModifierType.MOD1_MASK:
            meta_state |= 2
        if st_mask & Gdk.ModifierType.CONTROL_MASK:
            meta_state |= 4096
        if st_mask & (Gdk.ModifierType.SUPER_MASK | Gdk.ModifierType.META_MASK):
            meta_state |= 65536
        k_flags = (65536 | 4096) if is_media_key else 4096
        if pressed and (meta_state & 4096) and ak == 50 and hasattr(self.app, "_paste_pc_clipboard_and_send_key"):
            self.app._spawn(self.app._paste_pc_clipboard_and_send_key(
                c, ak, pressed, display_id=disp, meta_state=meta_state, flags=k_flags))
        else:
            self.app._spawn(self.facade.send_key_event(
                c, ak, pressed, display_id=disp, meta_state=meta_state,
                flags=k_flags, quiet=True))
        return True

    def _on_resolution_combo_changed(self, combo: Gtk.ComboBoxText) -> None:
        if getattr(self, "_updating_res_combo", False):
            return
        res_id = (combo.get_active_id() or "").strip()
        if not res_id or "x" not in res_id:
            return
        w_, h_ = 1920, 1080
        with contextlib.suppress(Exception):
            w_, h_ = (int(x) for x in res_id.lower().split("x", 1))
            self.facade.desktop_res = (w_, h_)
            self.facade.save_user_settings(desktop_res=res_id)
        self._last_frame_dims = None
        self.adapt_window_to_aspect(w_, h_, force=True)
        if self.feature_id == "appstream":
            self.app._spawn(self.facade.request(f"appstream-resize:{res_id}"))
        else:
            self.app._spawn(self.facade.request(f"desktop-res:{res_id}"))
        self.status_lbl.set_text(f"{w_}×{h_}")
        self.video_da.queue_draw()

    def _on_rotate_clicked(self, *_args) -> None:
        cw, ch = (int(x) for x in getattr(self.facade, "desktop_res", (1920, 1080)))
        target = "landscape" if ch > cw else "portrait"
        self._rotated = not self._rotated
        self.status_lbl.set_text(f"Rotating to {target}…")
        self.app._spawn(self._rotate_and_apply(target))

    async def _rotate_and_apply(self, target: str) -> None:
        c = self.facade._live_ws()
        if hasattr(self.facade, "rotate_desktop"):
            res = await self.facade.rotate_desktop(c, target)
        else:
            res = await self.facade.request(f"desktop-rotate:{target}")
        nw, nh = (int(x) for x in getattr(self.facade, "desktop_res", (1920, 1080)))
        self._last_frame_dims = (nw, nh)
        self.adapt_window_to_aspect(nw, nh, force=True)
        if self.res_combo is not None:
            key = f"{nw}x{nh}"
            self._updating_res_combo = True
            try:
                if not self.res_combo.set_active_id(key):
                    self.res_combo.append(key, f"{nw}×{nh}")
                    self.res_combo.set_active_id(key)
            finally:
                self._updating_res_combo = False
        self.video_da.queue_draw()
        method = (res or {}).get("method") or ""
        nice = {"live-cmd17": "resized live", "restart": "session restarted",
                "persist-only": "saved for the next start"}.get(method, method or "done")
        verified = (res or {}).get("verified")
        msg = f"{nw}×{nh} · {nice}"
        if verified is True:
            msg += " · phone confirmed"
        elif verified is False:
            msg += " · awaiting phone confirmation"
        self.status_lbl.set_text(msg)
        with contextlib.suppress(Exception):
            self.app.facade.events.log("UI_ROTATE_RESULT",
                                  f"feature={self.feature_id} target={target} "
                                  f"applied={nw}x{nh} method={method} verified={verified} "
                                  f"strategy={(res or {}).get('strategy')}", feature="gui")

    def _on_ss_rotate_clicked(self, *_args) -> None:
        cw, ch = (
            self.facade._ss_virtual_size()
            if hasattr(self.facade, "_ss_virtual_size")
            else getattr(self.facade, "ss_virtual_res", (2400, 1080))
        )
        target = "landscape" if ch > cw else "portrait"
        self.status_lbl.set_text(f"Rotating extended display to {target}…")
        self.app._spawn(self._rotate_ss_extend_and_apply(target))

    async def _rotate_ss_extend_and_apply(self, target: str) -> None:
        c = self.facade._live_ws() if hasattr(self.facade, "_live_ws") else None
        if hasattr(self.facade, "rotate_screenshare_extend"):
            res = await self.facade.rotate_screenshare_extend(c, target=target)
        else:
            res = await self.facade.request(f"ss-rotate:{target}")
        nw, nh = (
            self.facade._ss_virtual_size()
            if hasattr(self.facade, "_ss_virtual_size")
            else getattr(self.facade, "ss_virtual_res", (2400, 1080))
        )
        self._last_frame_dims = (nw, nh)
        self.adapt_window_to_aspect(nw, nh, force=True)
        if getattr(self, "ss_res_combo", None) is not None:
            key = f"{nw}x{nh}"
            self._updating_ss_res_combo = True
            try:
                if not self.ss_res_combo.set_active_id(key):
                    self.ss_res_combo.append(key, f"{nw}×{nh}")
                    self.ss_res_combo.set_active_id(key)
            finally:
                self._updating_ss_res_combo = False
        if hasattr(self.app, "_sync_pc_screenshare_setup_ui"):
            with contextlib.suppress(Exception):
                self.app._sync_pc_screenshare_setup_ui()
        ar = (res or {}).get("aspect") or (
            self.facade.describe_aspect_ratio(nw, nh) if hasattr(self.facade, "describe_aspect_ratio") else "20:9"
        )
        ori = (res or {}).get("orient") or ("portrait" if nh > nw else "landscape")
        self.status_lbl.set_text(f"Virtual Display · {nw}×{nh} ({ar} {ori})")
        self.video_da.queue_draw()

    def _on_ss_res_combo_changed(self, combo: Gtk.ComboBoxText) -> None:
        if getattr(self, "_updating_ss_res_combo", False):
            return
        res_id = (combo.get_active_id() or "").strip()
        if not res_id or "x" not in res_id:
            return
        with contextlib.suppress(Exception):
            w_, h_ = (max(160, int(x)) for x in res_id.lower().split("x", 1))
            ori = "portrait" if h_ > w_ else "landscape"
            self.facade.set_ss_virtual(orient=ori, res=(w_, h_))
            self.app._spawn(self._rotate_ss_extend_and_apply(ori))

    def _on_diagnostics_clicked(self, *_args) -> None:
        md = getattr(self.facade, "media_diag", None)
        if md is None:
            self.status_lbl.set_text("Media diagnostics unavailable")
            return
        self.status_lbl.set_text("Collecting media diagnostics…")
        self.app._spawn(self._run_diagnostics(md))

    async def _run_diagnostics(self, md) -> None:
        verdict, path, summary = "", "", {}
        with contextlib.suppress(Exception):
            await md.poll_once()
            summary = md.summary(window=120)
            verdict = md.verdict()
            path = str(md.write_report())
        lines = [verdict or "(no verdict yet)"]
        if summary:
            drag, idle = summary.get("during_drag"), summary.get("idle")
            if drag and idle:
                lines += [
                    "",
                    f"During drag : loss max {drag['loss_pct_max']}%, "
                    f"loop lag max {drag['loop_lag_ms_max']:.0f} ms, "
                    f"{drag['frames_dropped_total']} frames dropped, "
                    f"{drag['keyframes_total']} keyframes",
                    f"Idle        : loss max {idle['loss_pct_max']}%, "
                    f"loop lag max {idle['loop_lag_ms_max']:.0f} ms, "
                    f"{idle['frames_dropped_total']} frames dropped, "
                    f"{idle['keyframes_total']} keyframes",
                ]
            if summary.get("stalls"):
                lines.append(f"Frame-arrival stalls detected: {summary['stalls']}")
        if path:
            lines += ["", "Report saved to:", path, "(and the .md beside it)"]
        text = "\n".join(lines)

        def _show() -> None:
            with contextlib.suppress(Exception):
                self.status_lbl.set_text((verdict or "diagnostics saved")[:110])
            dlg = Gtk.MessageDialog(transient_for=self, modal=True,
                                    message_type=Gtk.MessageType.INFO,
                                    buttons=Gtk.ButtonsType.CLOSE,
                                    text="Media diagnostics")
            dlg.format_secondary_text(text[:3000])
            open_dialog_nonblocking(dlg)
        GLib.idle_add(_show)

    def _on_fit_clicked(self, btn: Gtk.Button) -> None:
        self._fit_mode = not self._fit_mode
        btn.set_label("Fill" if not self._fit_mode else "Fit")
        self.video_da.queue_draw()

    def _on_native_size_clicked(self, *_args) -> None:
        self._fit_mode = True
        if hasattr(self, "fit_btn"):
            self.fit_btn.set_label("Fit")
        if self._pixbuf is not None:
            pw, ph = self._pixbuf.get_width(), self._pixbuf.get_height()
            self.adapt_window_to_aspect(pw, ph, force=True)
        self.video_da.queue_draw()

    def _on_fullscreen_clicked(self, *_args) -> None:
        if getattr(self, "_is_fs", False):
            self.unfullscreen()
            self._is_fs = False
        else:
            self.fullscreen()
            self._is_fs = True
        self._sync_nav_bar_visibility()

    def _on_view_combo_changed(self, combo: Gtk.ComboBoxText) -> None:
        if getattr(self, "_updating_view_combo", False):
            return
        vid = (combo.get_active_id() or "2").strip()
        self._last_frame_dims = None
        self.app._spawn(self.facade.request(f"desktop-view:{vid}"))

    def _update_video_rect(self, pw: int, ph: int, alloc=None) -> tuple[float, float, float, float]:
        if alloc is None:
            alloc = self.video_da.get_allocation()
        aw = max(1, int(getattr(alloc, "width", 1) or 1))
        ah = max(1, int(getattr(alloc, "height", 1) or 1))
        sf = min(2, max(1, int(self.get_scale_factor())))
        lw = max(1.0, pw / sf)
        lh = max(1.0, ph / sf)
        if self._fit_mode:
            scale = min(aw / lw, ah / lh)
        else:
            scale = max(aw / lw, ah / lh)
        dw, dh = lw * scale, lh * scale
        ox = (aw - dw) / 2.0
        oy = (ah - dh) / 2.0
        self._video_rect = (
            max(0.0, ox),
            max(0.0, oy),
            min(max(1.0, dw), aw - max(0.0, ox)),
            min(max(1.0, dh), ah - max(0.0, oy)),
        )
        return ox, oy, dw, dh

    def _on_video_size_allocate(self, _widget: Gtk.Widget, alloc) -> None:
        dims = self._cairo_dims or self._last_frame_dims
        if dims is not None:
            self._update_video_rect(dims[0], dims[1], alloc=alloc)
        elif self._pixbuf_store is not None:
            self._update_video_rect(self._pixbuf_store.get_width(), self._pixbuf_store.get_height(), alloc=alloc)

    def _on_video_draw(self, widget: Gtk.Widget, cr) -> bool:
        alloc = widget.get_allocation()
        cr.set_source_rgb(0.0, 0.0, 0.0)
        cr.paint()
        surf = self._cairo_surface
        pb = None
        if surf is not None and self._cairo_dims is not None:
            pw, ph = self._cairo_dims
        else:
            pb = self._pixbuf
            if pb is None:
                wp_pb = self.app.handset_preview._wp_pixbuf
                if wp_pb is not None:
                    pb = wp_pb
                else:
                    return False
            pw, ph = pb.get_width(), pb.get_height()
        sf = min(2, max(1, int(self.get_scale_factor())))
        lw = max(1.0, pw / sf)
        lh = max(1.0, ph / sf)
        if self._fit_mode:
            scale = min(alloc.width / lw, alloc.height / lh)
            dw, dh = lw * scale, lh * scale
            ox = (alloc.width - dw) / 2.0
            oy = (alloc.height - dh) / 2.0
        else:
            scale = max(alloc.width / lw, alloc.height / lh)
            dw, dh = lw * scale, lh * scale
            ox = (alloc.width - dw) / 2.0
            oy = (alloc.height - dh) / 2.0
        self._video_rect = (max(0.0, ox), max(0.0, oy),
                           min(max(1.0, dw), alloc.width - max(0.0, ox)),
                           min(max(1.0, dh), alloc.height - max(0.0, oy)))
        cr.save()
        if not self._fit_mode:
            cr.rectangle(0, 0, alloc.width, alloc.height)
            cr.clip()
        cr.translate(ox, oy)
        cr.scale(dw / pw, dh / ph)
        if surf is not None:
            cr.set_source_surface(surf, 0, 0)
        else:
            Gdk.cairo_set_source_pixbuf(cr, pb, 0, 0)
        with contextlib.suppress(Exception):
            import cairo as _cairo
            patt = cr.get_source()
            if abs(dw - pw) <= 1.0 and abs(dh - ph) <= 1.0:
                patt.set_filter(_cairo.Filter.FAST)
            else:
                patt.set_filter(_cairo.Filter.BILINEAR)
        cr.paint()
        cr.restore()
        return False

    def update_from_frame(self, av_frame, seq=None, uv_swapped: bool | None = None) -> None:
        now = time.monotonic()
        key = seq if seq is not None else getattr(av_frame, "pts", None)
        if key is not None and key == self._last_pts:
            return
        pm = getattr(self.facade, "power_mgr", None)
        if pm is not None and self._last_pts is not None:
            if not pm.should_render_ui_frame(
                self.feature_id,
                self._last_render_mono,
                sid=getattr(self, "stream_session_id", None),
                now_mono=now,
            ):
                return
        elif now - self._last_render_mono < 0.004:
            return
        try:
            if self.feature_id == "webcam" and True:
                want = bool(getattr(self.facade, "uv_swap", True))
                known = None if uv_swapped is None else bool(uv_swapped)
                if known is None and True:
                    with contextlib.suppress(Exception):
                        known = bool(is_uv_swapped(av_frame))
                if known is not None and known != want:
                    av_frame, _ = apply_uv_swap_state(av_frame, want)
            sf = min(2, max(1, int(self.get_scale_factor())))
            fw, fh = int(av_frame.width), int(av_frame.height)
            _alloc = None
            with contextlib.suppress(Exception):
                _alloc = self.video_da.get_allocation()
            if _alloc is not None and _alloc.width > 8 and _alloc.height > 8:
                tw, th = target_render_size(
                    fw, fh,
                    max(16, int(_alloc.width) * sf),
                    max(16, int(_alloc.height) * sf),
                    max_upscale=1.0)
            else:
                tw, th = target_render_size(fw, fh, fw * sf, fh * sf, max_upscale=2.0)
            tw = max(2, min(fw, int(tw)))
            th = max(2, min(fh, int(th)))
            if hasattr(self.facade, "set_stream_target_size"):
                with contextlib.suppress(Exception):
                    self.facade.set_stream_target_size(
                        self.feature_id, tw, th,
                        sid=getattr(self, "stream_session_id", None),
                    )
            prep = get_prepared_frame_bgrx(av_frame)
            used_offloaded_bgrx = False
            if (
                isinstance(prep, dict)
                and prep.get("rgb_frame") is not None
                and int(prep.get("tw") or 0) > 0
                and int(prep.get("th") or 0) > 0
            ):
                rgb_frame = prep["rgb_frame"]
                tw = int(prep["tw"])
                th = int(prep["th"])
                used_offloaded_bgrx = prep.get("bgrx") is not None
            elif tw == fw and th == fh:
                rgb_frame = av_frame.reformat(format="rgb24")
            else:
                rgb_frame = av_frame.reformat(width=tw, height=th, format="rgb24")
            if not prep or prep.get("rgb_frame") is None:
                with contextlib.suppress(Exception):
                    from samyuj.media.enhance import apply_enhance_to_rgb_frame
                    apply_enhance_to_rgb_frame(rgb_frame, f"ui:{self.feature_id}")
            if getattr(self, "_last_frame_dims", None) != (fw, fh):
                self._last_frame_dims = (fw, fh)
                self.adapt_window_to_aspect(fw, fh, force=True)
                if self.res_combo is not None:
                    cid = f"{fw}x{fh}"
                    self._updating_res_combo = True
                    try:
                        if not self.res_combo.set_active_id(cid):
                            self.res_combo.append(cid, f"{fw}×{fh}{' (P)' if fh > fw else ''}")
                            self.res_combo.set_active_id(cid)
                    finally:
                        self._updating_res_combo = False
                with contextlib.suppress(Exception):
                    self.status_lbl.set_text(f"{fw}×{fh}")
            plane = rgb_frame.planes[0]
            stride = int(plane.line_size)
            mv = memoryview(plane)
            self._lazy_rgb_frame = (rgb_frame, tw, th, stride)
            self._pixbuf_store = None
            if _LIBSC_FAST is not None and hasattr(_LIBSC_FAST, "sc_rgb24_to_bgrx32"):
                try:
                    import cairo as _cairo
                    import ctypes as _ct
                    need_bytes = tw * th * 4
                    min_src_bytes = (th - 1) * stride + tw * 3 if th > 0 and tw > 0 else 0
                    if used_offloaded_bgrx and len(prep["bgrx"]) == need_bytes:
                        ring_buf = prep["bgrx"]
                        ring_id = id(ring_buf)
                        ring_map = getattr(self, "_cairo_ring_surfaces", None)
                        if ring_map is None:
                            ring_map = self._cairo_ring_surfaces = {}
                        cached = ring_map.get(ring_id)
                        if cached is None or cached[2] != (tw, th) or len(cached[0]) != need_bytes:
                            if len(ring_map) >= 6:
                                ring_map.clear()
                            surf_r = _cairo.ImageSurface.create_for_data(
                                ring_buf, _cairo.Format.RGB24, tw, th, tw * 4
                            )
                            cached = (ring_buf, surf_r, (tw, th))
                            ring_map[ring_id] = cached
                        self._cairo_buf = ring_buf
                        self._cairo_surface = cached[1]
                        self._cairo_dims = (tw, th)
                        self._cairo_surface.flush()
                        self._cairo_surface.mark_dirty()
                        self._offloaded_blits = int(getattr(self, "_offloaded_blits", 0)) + 1
                    elif stride >= tw * 3 and len(mv) >= min_src_bytes and need_bytes > 0:
                        if (self._cairo_surface is None
                                or self._cairo_dims != (tw, th)
                                or self._cairo_buf is None
                                or len(self._cairo_buf) != need_bytes
                                or id(self._cairo_buf) in getattr(self, "_cairo_ring_surfaces", {})):
                            self._cairo_buf = bytearray(need_bytes)
                            self._cairo_surface = _cairo.ImageSurface.create_for_data(
                                self._cairo_buf, _cairo.Format.RGB24, tw, th, tw * 4
                            )
                            self._cairo_dims = (tw, th)
                        self._cairo_surface.flush()
                        src_c = (_ct.c_uint8 * len(mv)).from_buffer(mv)
                        dst_c = (_ct.c_uint8 * need_bytes).from_buffer(self._cairo_buf)
                        _LIBSC_FAST.sc_rgb24_to_bgrx32(
                            _ct.addressof(src_c), stride,
                            _ct.addressof(dst_c), tw * 4,
                            tw, th,
                        )
                        self._cairo_surface.mark_dirty()
                    else:
                        self._cairo_surface = None
                        self._cairo_dims = None
                except Exception:
                    self._cairo_surface = None
                    self._cairo_dims = None
            else:
                self._cairo_surface = None
                self._cairo_dims = None
            ox, oy, dw, dh = self._update_video_rect(tw, th)
            if not getattr(self, "_have_gi_cairo", True) and self._fallback_img is not None:
                with contextlib.suppress(Exception):
                    pb_fb = self._pixbuf
                    if pb_fb is not None:
                        sw_fb = max(1, int(round(dw))) if dw >= 16 else tw
                        sh_fb = max(1, int(round(dh))) if dh >= 16 else th
                        scaled_fb = (
                            pb_fb if (sw_fb == pb_fb.get_width() and sh_fb == pb_fb.get_height())
                            else pb_fb.scale_simple(sw_fb, sh_fb, GdkPixbuf.InterpType.BILINEAR)
                        )
                        if self._fallback_layout is not None:
                            self._fallback_layout.move(self._fallback_img, int(max(0.0, ox)), int(max(0.0, oy)))
                        self._fallback_img.set_from_pixbuf(scaled_fb or pb_fb)
            self._last_pts = key
            self._last_render_mono = now
            self.video_da.queue_draw()
        except Exception as exc:
            if now - self._frame_err_logged > 5.0:
                self._frame_err_logged = now
                with contextlib.suppress(Exception):
                    self.facade.events.log("GTK_FRAME_CONVERT_ERR", repr(exc)[:200], feature="gui")

    def _frac_coords(self, ex: float, ey: float) -> tuple[float, float] | None:
        ox, oy, dw, dh = self._video_rect
        if dw <= 1 or dh <= 1:
            return None
        fx = (ex - ox) / dw
        fy = (ey - oy) / dh
        if fx < 0.0 or fx > 1.0 or fy < 0.0 or fy > 1.0:
            return None
        return max(0.0, min(1.0, fx)), max(0.0, min(1.0, fy))

    def _resolve_input_target(self, c: dict | None) -> tuple[int | None, tuple[int, int] | None]:
        if not isinstance(c, dict):
            return self.stream_display_id, self._last_frame_dims
        as_map = c.get("as_sessions") or {}
        if self.feature_id in ("desktop", "phone_on_pc"):
            sid = self.stream_session_id if self.stream_session_id is not None else c.get("desktop_as_sid")
            sinfo = as_map.get(sid) if sid is not None else None
            if not isinstance(sinfo, dict):
                sinfo = next((v for v in as_map.values() if isinstance(v, dict) and v.get("is_desktop")), None)
            disp = (
                self.stream_display_id
                if isinstance(self.stream_display_id, int) and self.stream_display_id >= 0
                else ((sinfo or {}).get("display_id") if isinstance((sinfo or {}).get("display_id"), int) else c.get("desktop_display_id"))
            )
            if disp is None:
                disp = c.get("as_display_id")
            geom = self._last_frame_dims or (sinfo or {}).get("geom") or c.get("as_geom") or getattr(self.facade, "desktop_res", None)
            return disp, geom
        if self.feature_id == "appstream" or str(self.feature_id).startswith("appstream:"):
            sid = self.stream_session_id if self.stream_session_id is not None else c.get("app_as_sid")
            sinfo = as_map.get(sid) if sid is not None else None
            if not isinstance(sinfo, dict):
                sinfo = next((v for v in as_map.values() if isinstance(v, dict) and not v.get("is_desktop")), None)
            disp = (
                self.stream_display_id
                if isinstance(self.stream_display_id, int) and self.stream_display_id >= 0
                else ((sinfo or {}).get("display_id") if isinstance((sinfo or {}).get("display_id"), int) else c.get("app_display_id"))
            )
            if disp is None:
                disp = c.get("as_display_id")
            geom = self._last_frame_dims or (sinfo or {}).get("geom") or c.get("as_geom")
            return disp, geom
        return c.get("as_display_id"), self._last_frame_dims or c.get("as_geom")

    def _on_button_press(self, _w, event: Gdk.EventButton) -> bool:
        if self.feature_id not in ("desktop", "phone_on_pc", "appstream"):
            return False
        self.video_da.grab_focus()
        self._scroll_pend_x = 0.0
        self._scroll_pend_y = 0.0
        self._scroll_pend_pt = (0.5, 0.5)
        self._scroll_pend_target = (None, None)
        self._scroll_inflight = False
        self._scroll_timer = None
        self._scroll_last_flush = 0.0
        self._drag_had_motion = False
        fc = self._frac_coords(event.x, event.y)
        c = self.facade._live_ws()
        if fc and c:
            disp, geom = self._resolve_input_target(c)
            btn = int(getattr(event, "button", 1) or 1)
            if btn == 3:
                if getattr(self.facade, "stream_mouse_hover", False) and hasattr(self.facade, "send_stream_mouse_button"):
                    self.app._spawn(self.facade.send_stream_mouse_button(c, 3, True, fc[0], fc[1], display_id=disp, geom=geom))
                elif hasattr(self.facade, "send_key_tap"):
                    self.app._spawn(self.facade.send_key_tap(c, 4, display_id=disp, quiet=True))
                return True
            if btn == 2 and hasattr(self.facade, "send_stream_mouse_button"):
                self.app._spawn(self.facade.send_stream_mouse_button(c, 2, True, fc[0], fc[1], display_id=disp, geom=geom))
                return True
            self._touch_pressed = True
            self.app._spawn(self.facade.send_touch_frac(c, 1, fc[0], fc[1], display_id=disp, geom=geom))
        return True

    def _on_button_release(self, _w, event: Gdk.EventButton) -> bool:
        if self.feature_id not in ("desktop", "phone_on_pc", "appstream"):
            return False
        self._motion_pending = None
        fc = self._frac_coords(event.x, event.y)
        c = self.facade._live_ws()
        btn = int(getattr(event, "button", 1) or 1)
        if btn == 3:
            if fc and c and getattr(self.facade, "stream_mouse_hover", False) and hasattr(self.facade, "send_stream_mouse_button"):
                disp, geom = self._resolve_input_target(c)
                self.app._spawn(self.facade.send_stream_mouse_button(c, 3, False, fc[0], fc[1], display_id=disp, geom=geom))
            return True
        if btn == 2:
            if fc and c and hasattr(self.facade, "send_stream_mouse_button"):
                disp, geom = self._resolve_input_target(c)
                self.app._spawn(self.facade.send_stream_mouse_button(c, 2, False, fc[0], fc[1], display_id=disp, geom=geom))
            return True
        self._touch_pressed = False
        if fc and c:
            disp, geom = self._resolve_input_target(c)
            self.app._spawn(self.facade.send_touch_frac(c, 0, fc[0], fc[1], display_id=disp, geom=geom))
            if getattr(self, "_drag_had_motion", False) and hasattr(self.facade, "request_stream_pli"):
                self._drag_had_motion = False
                with contextlib.suppress(Exception):
                    if self.facade.is_decoder_gate_active():
                        self.app._spawn(self.facade.request_stream_pli(c, self.stream_session_id))
        return True

    async def _drain_motion_queue(self) -> None:
        try:
            while getattr(self, "_motion_pending", None) is not None:
                c, fx, fy, disp, geom = self._motion_pending
                self._motion_pending = None
                await self.facade.send_touch_frac(c, 2, fx, fy, quiet=True, display_id=disp, geom=geom)
        finally:
            self._motion_inflight = False

    def _on_focus_in(self, *_args) -> bool:
        with contextlib.suppress(Exception):
            self._sync_window_power_state(focused_hint=True)
        cb_fn = getattr(self.app, "_gtk_clip_owner_changed", None)
        cb_obj = getattr(self.app, "_gtk_clipboard", None)
        if callable(cb_fn) and cb_obj is not None:
            with contextlib.suppress(Exception):
                cb_fn(cb_obj, None)
        return False

    def _on_focus_out(self, *_args) -> bool:
        with contextlib.suppress(Exception):
            self._sync_window_power_state(focused_hint=False)
        if getattr(self, "_touch_pressed", False):
            self._touch_pressed = False
            self._motion_pending = None
            c = self.facade._live_ws()
            if c and hasattr(self.facade, "send_touch_cancel"):
                disp, _geom = self._resolve_input_target(c)
                self.app._spawn(self.facade.send_touch_cancel(c, display_id=disp))
        return False

    def _on_motion(self, _w, event: Gdk.EventMotion) -> bool:
        if self.feature_id not in ("desktop", "phone_on_pc", "appstream"):
            return False
        now = time.monotonic()
        if not (event.state & Gdk.ModifierType.BUTTON1_MASK):
            if getattr(self.facade, "stream_mouse_hover", False) and hasattr(self.facade, "send_stream_mouse_move"):
                if now - self._last_motion_mono >= (1.0 / 90.0):
                    self._last_motion_mono = now
                    fc_h = self._frac_coords(event.x, event.y)
                    c_h = self.facade._live_ws()
                    if fc_h and c_h:
                        disp_h, geom_h = self._resolve_input_target(c_h)
                        self.app._spawn(self.facade.send_stream_mouse_move(c_h, fc_h[0], fc_h[1], display_id=disp_h, geom=geom_h))
            return False
        if now - self._last_motion_mono < (1.0 / 120.0):
            return True
        self._last_motion_mono = now
        fc = self._frac_coords(event.x, event.y)
        c = self.facade._live_ws()
        if fc and c:
            self._drag_had_motion = True
            disp, geom = self._resolve_input_target(c)
            self._motion_pending = (c, fc[0], fc[1], disp, geom)
            if not getattr(self, "_motion_inflight", False):
                self._motion_inflight = True
                self.app._spawn(self._drain_motion_queue())
        return True

    def _on_scroll(self, _w, event: Gdk.EventScroll) -> bool:
        if self.feature_id not in ("desktop", "phone_on_pc", "appstream"):
            return False
        ev_time = int(getattr(event, "time", 0) or 0)
        if ev_time and ev_time == getattr(self, "_last_scroll_ev_time", -1):
            return True
        ex, ey = float(event.x), float(event.y)
        if _w is not self.video_da:
            with contextlib.suppress(Exception):
                tr = _w.translate_coordinates(self.video_da, int(ex), int(ey))
                if tr is not None:
                    ex, ey = float(tr[0]), float(tr[1])
        fc = self._frac_coords(ex, ey) or (0.5, 0.5)
        c = self.facade._live_ws()
        if c is None:
            return False
        now = time.monotonic()
        if now - getattr(self, "_last_scroll_mono", 0.0) > 0.35:
            self._scroll_acc_x = 0.0
            self._scroll_acc_y = 0.0
        self._last_scroll_mono = now
        dx, dy = 0.0, 0.0
        direction = event.direction
        if direction == Gdk.ScrollDirection.UP:
            if now - getattr(self, "_last_smooth_scroll_mono", 0.0) < 0.03:
                return True
            dy = -1.0
        elif direction == Gdk.ScrollDirection.DOWN:
            if now - getattr(self, "_last_smooth_scroll_mono", 0.0) < 0.03:
                return True
            dy = 1.0
        elif direction == Gdk.ScrollDirection.LEFT:
            if now - getattr(self, "_last_smooth_scroll_mono", 0.0) < 0.03:
                return True
            dx = -1.0
        elif direction == Gdk.ScrollDirection.RIGHT:
            if now - getattr(self, "_last_smooth_scroll_mono", 0.0) < 0.03:
                return True
            dx = 1.0
        elif direction == Gdk.ScrollDirection.SMOOTH:
            ok_d, sdx, sdy = event.get_scroll_deltas()
            if ok_d:
                self._last_smooth_scroll_mono = now
                self._scroll_acc_x += float(sdx)
                self._scroll_acc_y += float(sdy)
                if abs(self._scroll_acc_y) >= 0.12 or abs(self._scroll_acc_x) >= 0.12:
                    dx = self._scroll_acc_x
                    dy = self._scroll_acc_y
                    self._scroll_acc_x = 0.0
                    self._scroll_acc_y = 0.0
        if abs(dx) < 1e-3 and abs(dy) < 1e-3:
            return True
        if ev_time:
            self._last_scroll_ev_time = ev_time
        self._scroll_pend_y = max(-6.0, min(6.0, float(getattr(self, "_scroll_pend_y", 0.0)) + dy))
        self._scroll_pend_x = max(-6.0, min(6.0, float(getattr(self, "_scroll_pend_x", 0.0)) + dx))
        self._scroll_pend_pt = (fc[0], fc[1])
        self._scroll_pend_target = self._resolve_input_target(c)
        now_m = time.monotonic()
        if getattr(self, "_scroll_inflight", False):
            return True
        if now_m - float(getattr(self, "_scroll_last_flush", 0.0)) < self.SCROLL_FLUSH_S:
            if not getattr(self, "_scroll_timer", None):
                self._scroll_timer = True
                GLib.timeout_add(
                    int(self.SCROLL_FLUSH_S * 1000),
                    lambda: (self._flush_scroll(c), False)[1])
            return True
        self._flush_scroll(c)
        return True

    SCROLL_FLUSH_S = 0.03

    def _flush_scroll(self, c) -> bool:
        self._scroll_timer = False
        dy = float(getattr(self, "_scroll_pend_y", 0.0))
        dx = float(getattr(self, "_scroll_pend_x", 0.0))
        self._scroll_pend_y = 0.0
        self._scroll_pend_x = 0.0
        if abs(dx) < 1e-3 and abs(dy) < 1e-3:
            return False
        pt = getattr(self, "_scroll_pend_pt", (0.5, 0.5))
        disp, geom = getattr(self, "_scroll_pend_target", (None, None))
        if c is None:
            c = self.facade._live_ws()
        if c is None:
            return False
        self._scroll_last_flush = time.monotonic()
        self._scroll_inflight = True
        self.app._spawn(self._scroll_gesture(c, pt, dy, dx, disp, geom))
        return False

    async def _scroll_gesture(self, c, pt, dy, dx, disp, geom) -> None:
        try:
            await self.facade.send_stream_scroll(c, pt[0], pt[1], dy, dx,
                                            display_id=disp, geom=geom)
        finally:
            self._scroll_inflight = False
            if (abs(float(getattr(self, "_scroll_pend_y", 0.0))) > 1e-3
                    or abs(float(getattr(self, "_scroll_pend_x", 0.0))) > 1e-3):
                with contextlib.suppress(Exception):
                    self._flush_scroll(self.facade._live_ws())

    def _on_delete_event(self, *_args) -> bool:
        self.facade.events.log("UI_WINDOW_CLOSED", f"feature={self.feature_id} window closed -> stopping session cleanly")
        self.hide()
        with contextlib.suppress(Exception):
            self.app.stop_feature(self.feature_id)
        return True
