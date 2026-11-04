from __future__ import annotations

from samyuj.ui.gtk.gtk_compat import Gtk

from samyuj.ui.gtk.assets import (
    make_cairo_icon,
    make_themed_feature_icon,
)
from samyuj.ui.gtk.render import PhoneHandsetPreview

class HomePageMixin:

    def _build_home_page(self) -> Gtk.Widget:
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        dev_card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)
        dev_card.get_style_context().add_class("device-card")
        vbox.pack_start(dev_card, False, False, 0)

        self.handset_preview = PhoneHandsetPreview(76, 148)
        if self.facade.phone_wallpaper and self.facade.phone_wallpaper.get("saved"):
            self.handset_preview.set_wallpaper_from_file(self.facade.phone_wallpaper["saved"])
        dev_card.pack_start(self.handset_preview, False, False, 0)

        copy_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        copy_box.set_valign(Gtk.Align.CENTER)
        dev_card.pack_start(copy_box, True, True, 0)

        self.home_device_name_lbl = Gtk.Label(label=self.facade._best_device_name(), xalign=0.0)
        self.home_device_name_lbl.get_style_context().add_class("device-heading")
        copy_box.pack_start(self.home_device_name_lbl, False, False, 0)

        pill_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.conn_pill_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.conn_pill_box.get_style_context().add_class("conn-pill-connected")
        self.conn_pill_icon = make_cairo_icon("wifi", 16, (0.075, 0.459, 0.180))
        self.conn_pill_lbl = Gtk.Label(label="Connected · Auto")
        self.conn_pill_box.pack_start(self.conn_pill_icon, False, False, 0)
        self.conn_pill_box.pack_start(self.conn_pill_lbl, False, False, 0)
        pill_row.pack_start(self.conn_pill_box, False, False, 0)

        self.usb_retry_btn = Gtk.Button(label="Search Phone")
        self.usb_retry_btn.get_style_context().add_class("secondary-btn")
        self.usb_retry_btn.connect("clicked", self._on_usb_retry_clicked)
        pill_row.pack_start(self.usb_retry_btn, False, False, 0)

        self.usb_status_lbl = Gtk.Label(label="USB: standby", xalign=0.0)
        self.usb_status_lbl.get_style_context().add_class("panel-sub")
        self.usb_status_lbl.set_line_wrap(True)
        self.usb_status_lbl.set_max_width_chars(38)
        pill_row.pack_start(self.usb_status_lbl, True, True, 0)
        copy_box.pack_start(pill_row, False, False, 0)

        metrics_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        copy_box.pack_start(metrics_row, False, False, 0)

        self.home_battery_lbl = Gtk.Label(label="Battery pending", xalign=0.0)
        self.home_battery_lbl.get_style_context().add_class("metric-text")
        metrics_row.pack_start(self.home_battery_lbl, False, False, 0)

        self.home_storage_lbl = Gtk.Label(label="Storage pending", xalign=0.0)
        self.home_storage_lbl.get_style_context().add_class("metric-text")
        metrics_row.pack_start(self.home_storage_lbl, False, False, 0)

        bottom_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        vbox.pack_start(bottom_row, True, True, 0)

        rail_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        rail_card.set_size_request(225, -1)
        rail_card.get_style_context().add_class("sc-card")
        bottom_row.pack_start(rail_card, False, False, 0)

        rail_h2 = Gtk.Label(label="Features", xalign=0.0)
        rail_h2.get_style_context().add_class("panel-h2")
        rail_card.pack_start(rail_h2, False, False, 2)

        for feat_id, feat_title, png_key in self.HOME_RAIL_FEATURES:
            row_w = self._create_feature_rail_row(feat_id, feat_title, png_key)
            rail_card.pack_start(row_w, False, False, 0)

        notif_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        notif_card.get_style_context().add_class("sc-card")
        bottom_row.pack_start(notif_card, True, True, 0)

        notif_head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        nh_title = Gtk.Label(label="Notifications", xalign=0.0)
        nh_title.get_style_context().add_class("panel-h2")
        notif_head.pack_start(nh_title, True, True, 0)

        self.home_notif_sub_lbl = Gtk.Label(label="", xalign=0.0)
        self.home_notif_sub_lbl.set_no_show_all(True)

        self.clear_notifs_btn = Gtk.Button(label="Clear all")
        self.clear_notifs_btn.get_style_context().add_class("clear-btn")
        self.clear_notifs_btn.connect("clicked", self._on_clear_all_notifications)
        notif_head.pack_end(self.clear_notifs_btn, False, False, 0)
        notif_card.pack_start(notif_head, False, False, 0)

        self.home_notif_list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        notif_card.pack_start(self.home_notif_list_box, True, True, 0)

        foot_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.notif_foot_lbl = Gtk.Label(label="Live notification sync enabled", xalign=0.0)
        self.notif_foot_lbl.get_style_context().add_class("notif-time")
        foot_row.pack_start(self.notif_foot_lbl, False, False, 0)
        notif_card.pack_end(foot_row, False, False, 0)

        return vbox

    def _create_feature_rail_row(self, feat_id: str, feat_title: str, png_key: str) -> Gtk.Widget:
        outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        outer.get_style_context().add_class("feature-rail-item")

        launch_btn = Gtk.Button()
        launch_btn.set_relief(Gtk.ReliefStyle.NONE)
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        icon_holder = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        icon_holder.pack_start(make_themed_feature_icon(feat_id, png_key, 24), False, False, 0)
        btn_box.pack_start(icon_holder, False, False, 0)

        txt_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        txt_box.set_valign(Gtk.Align.CENTER)
        title_lbl = Gtk.Label(label=feat_title, xalign=0.0)
        title_lbl.get_style_context().add_class("feature-rail-title")
        state_lbl = Gtk.Label(label="", xalign=0.0)
        state_lbl.get_style_context().add_class("feature-rail-state")
        state_lbl.set_no_show_all(True)
        txt_box.pack_start(title_lbl, False, False, 0)
        txt_box.pack_start(state_lbl, False, False, 0)
        btn_box.pack_start(txt_box, True, True, 0)

        launch_btn.add(btn_box)
        launch_btn.connect("clicked", lambda *_, fid=feat_id: self._on_feature_clicked(fid))
        outer.pack_start(launch_btn, True, True, 0)

        cancel_btn = Gtk.Button(label="x")
        cancel_btn.set_tooltip_text(f"Stop {feat_title}")
        cancel_btn.get_style_context().add_class("corner-cancel-btn")
        cancel_btn.set_valign(Gtk.Align.CENTER)
        cancel_btn.set_no_show_all(True)
        cancel_btn.connect("clicked", lambda *_, fid=feat_id: self.stop_feature(fid))
        outer.pack_end(cancel_btn, False, False, 0)

        self._rail_widgets[feat_id] = {
            "container": outer,
            "icon_holder": icon_holder,
            "png_key": png_key,
            "title_lbl": title_lbl,
            "state_lbl": state_lbl,
            "cancel_btn": cancel_btn,
        }
        return outer

HomePage = HomePageMixin
