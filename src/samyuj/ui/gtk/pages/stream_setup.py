from __future__ import annotations

import contextlib

from samyuj.ui.gtk.gtk_compat import Gdk, GdkPixbuf, GLib, Gtk, Pango

from samyuj.ui.gtk.assets import (
    make_setup_preview_pixbuf,
)

class StreamSetupPageMixin:

    def _create_setup_header(self, title_text: str, info_text: str, back_target: str | None = None) -> Gtk.Widget:
        hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        hdr.get_style_context().add_class("setup-header-bar")

        back_btn = Gtk.Button(label="Back")
        back_btn.set_tooltip_text("Back")
        back_btn.get_style_context().add_class("setup-back-btn")
        back_btn.connect(
            "clicked",
            lambda *_, bt=back_target: self.switch_page(bt or getattr(self, "_last_main_page", "home")),
        )
        hdr.pack_start(back_btn, False, False, 0)

        t_lbl = Gtk.Label(label=title_text, xalign=0.0)
        t_lbl.get_style_context().add_class("page-h1")
        hdr.pack_start(t_lbl, True, True, 0)

        info_btn = Gtk.MenuButton(label="i")
        info_btn.set_tooltip_text(info_text)
        info_btn.get_style_context().add_class("setup-info-btn")
        pop = Gtk.Popover.new(info_btn)
        pbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        pbox.set_margin_top(10)
        pbox.set_margin_bottom(10)
        pbox.set_margin_start(12)
        pbox.set_margin_end(12)
        ilbl = Gtk.Label(label=info_text, xalign=0.0)
        ilbl.set_line_wrap(True)
        ilbl.set_max_width_chars(44)
        ilbl.get_style_context().add_class("panel-sub")
        pbox.pack_start(ilbl, False, False, 0)
        pbox.show_all()
        pop.add(pbox)
        info_btn.set_popover(pop)
        hdr.pack_end(info_btn, False, False, 0)
        return hdr

    def _build_mobile_desktop_setup_page(self) -> Gtk.Widget:
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        vbox.pack_start(
            self._create_setup_header(
                "Mobile desktop",
                "Stream a full multi-window Android desktop environment from your phone to this PC with mouse, keyboard, and audio.",
            ),
            False, False, 0,
        )

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        card.get_style_context().add_class("sc-card")
        vbox.pack_start(card, False, False, 0)

        ori_title = Gtk.Label(label="Window orientation", xalign=0.0)
        ori_title.get_style_context().add_class("panel-h2")
        card.pack_start(ori_title, False, False, 0)

        ori_grid = Gtk.Grid()
        ori_grid.set_column_spacing(16)
        ori_grid.set_column_homogeneous(True)
        card.pack_start(ori_grid, False, False, 0)

        self.md_landscape_card_btn = Gtk.Button()
        self.md_landscape_card_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.md_landscape_card_btn.get_style_context().add_class("setup-option-card-selected")
        l_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        l_box.set_halign(Gtk.Align.CENTER)
        l_box.pack_start(Gtk.Image.new_from_pixbuf(make_setup_preview_pixbuf("md_landscape", 180, 110)), False, False, 0)
        l_lbl = Gtk.Label(label="Landscape")
        l_lbl.get_style_context().add_class("setup-option-title")
        l_box.pack_start(l_lbl, False, False, 0)
        self.md_landscape_card_btn.add(l_box)
        self.md_landscape_card_btn.connect("clicked", lambda *_: self._on_md_orientation_selected("landscape"))
        ori_grid.attach(self.md_landscape_card_btn, 0, 0, 1, 1)

        self.md_portrait_card_btn = Gtk.Button()
        self.md_portrait_card_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.md_portrait_card_btn.get_style_context().add_class("setup-option-card")
        p_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        p_box.set_halign(Gtk.Align.CENTER)
        p_box.pack_start(Gtk.Image.new_from_pixbuf(make_setup_preview_pixbuf("md_portrait", 180, 110)), False, False, 0)
        p_lbl = Gtk.Label(label="Portrait")
        p_lbl.get_style_context().add_class("setup-option-title")
        p_box.pack_start(p_lbl, False, False, 0)
        self.md_portrait_card_btn.add(p_box)
        self.md_portrait_card_btn.connect("clicked", lambda *_: self._on_md_orientation_selected("portrait"))
        ori_grid.attach(self.md_portrait_card_btn, 1, 0, 1, 1)

        res_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        res_row.set_margin_top(6)
        res_lbl = Gtk.Label(label="Window resolution", xalign=0.0)
        res_lbl.get_style_context().add_class("panel-h2")
        res_row.pack_start(res_lbl, True, True, 0)

        self.md_setup_res_combo = Gtk.ComboBoxText()
        self._updating_md_setup_combo = False
        for rk, rl in self.MD_LANDSCAPE_RES_OPTIONS:
            self.md_setup_res_combo.append(rk, rl)
        self.md_setup_res_combo.set_active_id("1920x1080")
        self.md_setup_res_combo.connect("changed", self._on_md_setup_res_changed)
        res_row.pack_end(self.md_setup_res_combo, False, False, 0)
        card.pack_start(res_row, False, False, 0)

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        btn_row.set_halign(Gtk.Align.CENTER)
        btn_row.set_margin_top(12)
        self.md_start_btn = Gtk.Button(label="Start Mobile desktop")
        self.md_start_btn.get_style_context().add_class("setup-start-btn")
        self.md_start_btn.connect("clicked", self._on_start_mobile_desktop_clicked)
        btn_row.pack_start(self.md_start_btn, False, False, 0)
        vbox.pack_start(btn_row, False, False, 0)
        return vbox

    def _on_md_orientation_selected(self, orientation: str) -> None:
        ori = "portrait" if str(orientation).lower() == "portrait" else "landscape"
        self._md_selected_orientation = ori
        l_ctx = self.md_landscape_card_btn.get_style_context()
        p_ctx = self.md_portrait_card_btn.get_style_context()
        if ori == "landscape":
            l_ctx.remove_class("setup-option-card")
            l_ctx.add_class("setup-option-card-selected")
            p_ctx.remove_class("setup-option-card-selected")
            p_ctx.add_class("setup-option-card")
            opts = self.MD_LANDSCAPE_RES_OPTIONS
            cur_w, cur_h = (int(x) for x in self._md_selected_resolution.split("x", 1)) if "x" in self._md_selected_resolution else (1920, 1080)
            target_id = f"{max(cur_w, cur_h)}x{min(cur_w, cur_h)}"
            if target_id not in dict(opts):
                target_id = "1920x1080"
        else:
            p_ctx.remove_class("setup-option-card")
            p_ctx.add_class("setup-option-card-selected")
            l_ctx.remove_class("setup-option-card-selected")
            l_ctx.add_class("setup-option-card")
            opts = self.MD_PORTRAIT_RES_OPTIONS
            cur_w, cur_h = (int(x) for x in self._md_selected_resolution.split("x", 1)) if "x" in self._md_selected_resolution else (1080, 1920)
            target_id = f"{min(cur_w, cur_h)}x{max(cur_w, cur_h)}"
            if target_id not in dict(opts):
                target_id = "1080x1920"

        self._updating_md_setup_combo = True
        try:
            self.md_setup_res_combo.remove_all()
            for rk, rl in opts:
                self.md_setup_res_combo.append(rk, rl)
            self.md_setup_res_combo.set_active_id(target_id)
            self._md_selected_resolution = target_id
        finally:
            self._updating_md_setup_combo = False

    def _on_md_setup_res_changed(self, combo: Gtk.ComboBoxText) -> None:
        if getattr(self, "_updating_md_setup_combo", False):
            return
        aid = (combo.get_active_id() or "").strip()
        if aid and "x" in aid:
            self._md_selected_resolution = aid

    def _sync_mobile_desktop_setup_ui(self) -> None:
        dw, dh = getattr(self.facade, "desktop_res", (1920, 1080))
        self._md_selected_resolution = f"{dw}x{dh}"
        self._on_md_orientation_selected("portrait" if dh > dw else "landscape")

    def _on_start_mobile_desktop_clicked(self, *_args) -> None:
        res_id = getattr(self, "_md_selected_resolution", "1920x1080") or "1920x1080"
        self._start_mobile_desktop(res_id)

    def _build_screenshare_hub_page(self) -> Gtk.Widget:
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        vbox.pack_start(
            self._create_setup_header(
                "Share Screen",
                "Choose whether to mirror this PC's screen onto your phone or share your phone's display onto this PC.",
            ),
            False, False, 0,
        )

        sub = Gtk.Label(
            label="Select which display you want to share:",
            xalign=0.0,
        )
        sub.get_style_context().add_class("page-sub")
        vbox.pack_start(sub, False, False, 0)

        grid = Gtk.Grid()
        grid.set_column_spacing(18)
        grid.set_row_spacing(18)
        grid.set_column_homogeneous(True)
        vbox.pack_start(grid, False, False, 0)

        pc_tile = self._create_screenshare_subtile(
            sub_id="share_pc_screen",
            title="Share PC screen",
            subtitle="Mirror this PC's primary display onto your phone screen at high frame rate.",
            preview_kind="pc_mirror",
            stop_fid="screenshare",
        )
        grid.attach(pc_tile, 0, 0, 1, 1)

        phone_tile = self._create_screenshare_subtile(
            sub_id="share_phone_screen",
            title="Share phone screen",
            subtitle="Stream your phone's display to this PC — mirror your phone as it appears in hand or create a virtual phone screen.",
            preview_kind="phone_mirror",
            stop_fid="phone_on_pc",
        )
        grid.attach(phone_tile, 1, 0, 1, 1)
        return vbox

    def _create_screenshare_subtile(self, sub_id: str, title: str, subtitle: str,
                                    preview_kind: str, stop_fid: str) -> Gtk.Widget:
        overlay = Gtk.Overlay()
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        card.get_style_context().add_class("feature-tile-card")

        btn = Gtk.Button()
        btn.set_relief(Gtk.ReliefStyle.NONE)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        inner.set_halign(Gtk.Align.CENTER)
        inner.set_valign(Gtk.Align.CENTER)
        inner.set_margin_top(6)
        inner.set_margin_bottom(6)

        img = Gtk.Image.new_from_pixbuf(make_setup_preview_pixbuf(preview_kind, 176, 108))
        inner.pack_start(img, False, False, 0)

        t_lbl = Gtk.Label(label=title)
        t_lbl.get_style_context().add_class("feature-tile-title")
        inner.pack_start(t_lbl, False, False, 0)

        s_lbl = Gtk.Label(label=subtitle)
        s_lbl.set_line_wrap(True)
        s_lbl.set_max_width_chars(36)
        s_lbl.set_justify(Gtk.Justification.CENTER)
        s_lbl.get_style_context().add_class("panel-sub")
        inner.pack_start(s_lbl, False, False, 0)

        state_lbl = Gtk.Label(label="")
        state_lbl.get_style_context().add_class("feature-rail-state")
        state_lbl.set_no_show_all(True)
        inner.pack_start(state_lbl, False, False, 0)

        btn.add(inner)
        btn.connect("clicked", lambda *_, sid=sub_id: self._on_feature_clicked(sid))
        card.pack_start(btn, True, True, 0)
        overlay.add(card)

        cancel_btn = Gtk.Button(label="x")
        cancel_btn.set_tooltip_text(f"Stop {title}")
        cancel_btn.get_style_context().add_class("corner-cancel-btn")
        cancel_btn.set_halign(Gtk.Align.END)
        cancel_btn.set_valign(Gtk.Align.START)
        cancel_btn.set_margin_top(8)
        cancel_btn.set_margin_end(8)
        cancel_btn.set_no_show_all(True)
        cancel_btn.connect("clicked", lambda *_, fid=stop_fid: self.stop_feature(fid))
        overlay.add_overlay(cancel_btn)

        self._grid_widgets[sub_id] = {
            "card": card,
            "title_lbl": t_lbl,
            "state_lbl": state_lbl,
            "cancel_btn": cancel_btn,
        }
        return overlay

    def _build_phone_screenshare_setup_page(self) -> Gtk.Widget:
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        vbox.pack_start(
            self._create_setup_header(
                "Screen share",
                "Stream your phone's screen to this PC with full touch, mouse, keyboard, and Android navigation button controls.",
                back_target="setup_screenshare_hub",
            ),
            False, False, 0,
        )

        self.phone_ss_mirror_btn = Gtk.Button()
        self.phone_ss_mirror_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.phone_ss_mirror_btn.get_style_context().add_class("setup-option-card-selected")
        m_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        m_row.pack_start(Gtk.Image.new_from_pixbuf(make_setup_preview_pixbuf("phone_mirror", 156, 94)), False, False, 0)
        m_txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        m_txt.set_valign(Gtk.Align.CENTER)
        mt = Gtk.Label(label="Mirror phone", xalign=0.0)
        mt.get_style_context().add_class("setup-option-title")
        ms = Gtk.Label(label="Show your phone's display as it appears in hand", xalign=0.0)
        ms.set_line_wrap(True)
        ms.get_style_context().add_class("setup-option-sub")
        m_txt.pack_start(mt, False, False, 0)
        m_txt.pack_start(ms, False, False, 0)
        m_row.pack_start(m_txt, True, True, 0)
        self.phone_ss_mirror_btn.add(m_row)
        self.phone_ss_mirror_btn.connect("clicked", lambda *_: self._on_phone_ss_mode_selected("mirror"))
        vbox.pack_start(self.phone_ss_mirror_btn, False, False, 0)

        self.phone_ss_virtual_btn = Gtk.Button()
        self.phone_ss_virtual_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.phone_ss_virtual_btn.get_style_context().add_class("setup-option-card")
        v_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        v_row.pack_start(Gtk.Image.new_from_pixbuf(make_setup_preview_pixbuf("phone_virtual", 156, 94)), False, False, 0)
        v_txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        v_txt.set_valign(Gtk.Align.CENTER)
        vt = Gtk.Label(label="Virtual phone", xalign=0.0)
        vt.get_style_context().add_class("setup-option-title")
        vs = Gtk.Label(label="Show an additional phone screen on your PC", xalign=0.0)
        vs.set_line_wrap(True)
        vs.get_style_context().add_class("setup-option-sub")
        v_txt.pack_start(vt, False, False, 0)
        v_txt.pack_start(vs, False, False, 0)
        v_row.pack_start(v_txt, True, True, 0)
        self.phone_ss_virtual_btn.add(v_row)
        self.phone_ss_virtual_btn.connect("clicked", lambda *_: self._on_phone_ss_mode_selected("mobile"))
        vbox.pack_start(self.phone_ss_virtual_btn, False, False, 0)

        glance_card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        glance_card.get_style_context().add_class("sc-card")
        g_left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        gt = Gtk.Label(label="Phone at a glance", xalign=0.0)
        gt.get_style_context().add_class("setup-option-title")
        gs = Gtk.Label(
            label="Automatically start screen share when your phone connects to this PC.",
            xalign=0.0,
        )
        gs.get_style_context().add_class("panel-sub")
        g_left.pack_start(gt, False, False, 0)
        g_left.pack_start(gs, False, False, 0)
        glance_card.pack_start(g_left, True, True, 0)

        self.phone_at_a_glance_switch = Gtk.Switch()
        self.phone_at_a_glance_switch.set_active(bool(self.facade.user_settings.get("phone_at_a_glance", False)))
        self.phone_at_a_glance_switch.set_valign(Gtk.Align.CENTER)
        self.phone_at_a_glance_switch.connect(
            "notify::active",
            lambda w, _pspec: self._on_toggle_phone_at_a_glance(bool(w.get_active())),
        )
        glance_card.pack_end(self.phone_at_a_glance_switch, False, False, 0)
        vbox.pack_start(glance_card, False, False, 0)

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        btn_row.set_halign(Gtk.Align.CENTER)
        btn_row.set_margin_top(10)
        self.phone_ss_start_btn = Gtk.Button(label="Start screen share")
        self.phone_ss_start_btn.get_style_context().add_class("setup-start-btn")
        self.phone_ss_start_btn.connect(
            "clicked",
            lambda *_: self._start_phone_on_pc(getattr(self, "_selected_phone_ss_mode", "mirror")),
        )
        btn_row.pack_start(self.phone_ss_start_btn, False, False, 0)
        vbox.pack_start(btn_row, False, False, 0)
        return vbox

    def _on_phone_ss_mode_selected(self, mode: str) -> None:
        m = "mobile" if str(mode).lower() in ("mobile", "virtual", "3") else "mirror"
        self._selected_phone_ss_mode = m
        self._phone_on_pc_mode = m
        m_ctx = self.phone_ss_mirror_btn.get_style_context()
        v_ctx = self.phone_ss_virtual_btn.get_style_context()
        if m == "mirror":
            m_ctx.remove_class("setup-option-card")
            m_ctx.add_class("setup-option-card-selected")
            v_ctx.remove_class("setup-option-card-selected")
            v_ctx.add_class("setup-option-card")
        else:
            v_ctx.remove_class("setup-option-card")
            v_ctx.add_class("setup-option-card-selected")
            m_ctx.remove_class("setup-option-card-selected")
            m_ctx.add_class("setup-option-card")

    def _on_toggle_phone_at_a_glance(self, active: bool) -> None:
        self.facade.desktop_autostart = bool(active)
        with contextlib.suppress(Exception):
            self.facade.save_user_settings(phone_at_a_glance=bool(active))
        if self.facade._live_ws() is not None:
            self._spawn(self.facade.request(f"desktop-autostart:{'on' if active else 'off'}"))

    def _build_pc_screenshare_setup_page(self) -> Gtk.Widget:
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        vbox.pack_start(
            self._create_setup_header(
                "Share PC screen",
                "Mirror your Linux PC display onto your phone screen with low-latency hardware-accelerated streaming.",
                back_target="setup_screenshare_hub",
            ),
            False, False, 0,
        )

        self.pc_ss_extend_btn = None
        self.pc_ss_extend_opts_card = None

        self.pc_ss_mirror_btn = Gtk.Button()
        self.pc_ss_mirror_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.pc_ss_mirror_btn.get_style_context().add_class("setup-option-card-selected")
        m_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        m_row.pack_start(Gtk.Image.new_from_pixbuf(make_setup_preview_pixbuf("pc_mirror", 156, 94)), False, False, 0)
        m_txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        m_txt.set_valign(Gtk.Align.CENTER)
        mt = Gtk.Label(label="Mirror PC display", xalign=0.0)
        mt.get_style_context().add_class("setup-option-title")
        ms = Gtk.Label(
            label="Show your primary PC monitor on your phone's screen at up to 30–60 fps.",
            xalign=0.0,
        )
        ms.set_line_wrap(True)
        ms.get_style_context().add_class("setup-option-sub")
        m_txt.pack_start(mt, False, False, 0)
        m_txt.pack_start(ms, False, False, 0)
        m_row.pack_start(m_txt, True, True, 0)
        self.pc_ss_mirror_btn.add(m_row)
        self.pc_ss_mirror_btn.connect("clicked", lambda *_: self._on_pc_ss_mode_selected("mirror"))
        vbox.pack_start(self.pc_ss_mirror_btn, False, False, 0)

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        btn_row.set_halign(Gtk.Align.CENTER)
        btn_row.set_margin_top(10)
        self.pc_ss_start_btn = Gtk.Button(label="Start PC screen share")
        self.pc_ss_start_btn.get_style_context().add_class("setup-start-btn")
        self.pc_ss_start_btn.connect(
            "clicked",
            lambda *_: self._start_screenshare("mirror"),
        )
        btn_row.pack_start(self.pc_ss_start_btn, False, False, 0)
        vbox.pack_start(btn_row, False, False, 0)
        return vbox

    def _on_pc_ss_mode_selected(self, mode: str) -> None:
        self._selected_pc_ss_mode = "mirror"
        self.facade.ss_share_type = "mirror"
        if getattr(self, "pc_ss_mirror_btn", None) is not None:
            m_ctx = self.pc_ss_mirror_btn.get_style_context()
            m_ctx.remove_class("setup-option-card")
            m_ctx.add_class("setup-option-card-selected")

    def _sync_pc_screenshare_setup_ui(self) -> None:
        vw, vh = (
            self.facade._ss_virtual_size()
            if hasattr(self.facade, "_ss_virtual_size")
            else getattr(self.facade, "ss_virtual_res", (2400, 1080))
        )
        is_portrait = vh > vw
        self._pc_ss_selected_orientation = "portrait" if is_portrait else "landscape"
        self._pc_ss_selected_res = f"{vw}x{vh}"
        if getattr(self, "pc_ss_landscape_btn", None) is not None and getattr(self, "pc_ss_portrait_btn", None) is not None:
            l_ctx = self.pc_ss_landscape_btn.get_style_context()
            p_ctx = self.pc_ss_portrait_btn.get_style_context()
            if is_portrait:
                p_ctx.remove_class("setup-option-card")
                p_ctx.add_class("setup-option-card-selected")
                l_ctx.remove_class("setup-option-card-selected")
                l_ctx.add_class("setup-option-card")
            else:
                l_ctx.remove_class("setup-option-card")
                l_ctx.add_class("setup-option-card-selected")
                p_ctx.remove_class("setup-option-card-selected")
                p_ctx.add_class("setup-option-card")
        if getattr(self, "pc_ss_res_combo", None) is not None:
            opts = self.PC_EXTEND_PORTRAIT_OPTIONS if is_portrait else self.PC_EXTEND_LANDSCAPE_OPTIONS
            self._updating_pc_ss_res_combo = True
            try:
                self.pc_ss_res_combo.remove_all()
                for rk, rl in opts:
                    self.pc_ss_res_combo.append(rk, rl)
                if not self.pc_ss_res_combo.set_active_id(self._pc_ss_selected_res):
                    _ar = self.facade.describe_aspect_ratio(vw, vh) if hasattr(self.facade, "describe_aspect_ratio") else ("9:20" if is_portrait else "20:9")
                    self.pc_ss_res_combo.append(self._pc_ss_selected_res, f"{vw} × {vh} ({_ar})")
                    self.pc_ss_res_combo.set_active_id(self._pc_ss_selected_res)
            finally:
                self._updating_pc_ss_res_combo = False

    def _on_pc_ss_orient_selected(self, orientation: str) -> None:
        want_portrait = str(orientation).strip().lower().startswith("p")
        vw, vh = (
            self.facade._ss_virtual_size()
            if hasattr(self.facade, "_ss_virtual_size")
            else getattr(self.facade, "ss_virtual_res", (2400, 1080))
        )
        nw, nh = (min(vw, vh), max(vw, vh)) if want_portrait else (max(vw, vh), min(vw, vh))
        ori = "portrait" if want_portrait else "landscape"
        with contextlib.suppress(Exception):
            self.facade.set_ss_virtual(orient=ori, res=(nw, nh))
        self._sync_pc_screenshare_setup_ui()
        c = self.facade._live_ws() if hasattr(self.facade, "_live_ws") else None
        if c and c.get("ss_started") and hasattr(self.facade, "rotate_screenshare_extend"):
            self._spawn(self.facade.rotate_screenshare_extend(c, target=ori))

    def _on_pc_ss_rotate_clicked(self, *_args) -> None:
        vw, vh = (
            self.facade._ss_virtual_size()
            if hasattr(self.facade, "_ss_virtual_size")
            else getattr(self.facade, "ss_virtual_res", (2400, 1080))
        )
        target_ori = "landscape" if vh > vw else "portrait"
        self._on_pc_ss_orient_selected(target_ori)

    def _on_pc_ss_res_changed(self, combo: Gtk.ComboBoxText) -> None:
        if getattr(self, "_updating_pc_ss_res_combo", False):
            return
        aid = (combo.get_active_id() or "").strip()
        if not aid or "x" not in aid:
            return
        with contextlib.suppress(Exception):
            w_, h_ = (max(160, int(x)) for x in aid.lower().split("x", 1))
            ori = "portrait" if h_ > w_ else "landscape"
            self._pc_ss_selected_res = f"{w_}x{h_}"
            self._pc_ss_selected_orientation = ori
            self.facade.set_ss_virtual(orient=ori, res=(w_, h_))
            c = self.facade._live_ws() if hasattr(self.facade, "_live_ws") else None
            if c and c.get("ss_started") and hasattr(self.facade, "rotate_screenshare_extend"):
                self._spawn(self.facade.rotate_screenshare_extend(c, target=ori))

MobileDesktopSetupPage = StreamSetupPageMixin
ScreenShareHubPage = StreamSetupPageMixin
