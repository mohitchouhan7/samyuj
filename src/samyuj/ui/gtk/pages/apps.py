from __future__ import annotations

from samyuj.ui.gtk.gtk_compat import Gtk, Pango

from samyuj.ui.gtk.assets import (
    make_cairo_icon,
)
from samyuj.ui.gtk.windows.tray_window import AppLauncherGtkWindow

class AppsPageMixin:

    def _build_apps_page(self) -> Gtk.Widget:
        self.apps_tab_view_mode: str = "menu"
        self.apps_tab_layout_mode: str = "grid"
        self.apps_tab_category: str = "All"
        self.apps_tab_query: str = ""

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        head.pack_start(make_cairo_icon("apps", 20), False, False, 0)

        self.apps_tab_title_lbl = Gtk.Label(label="Phone Apps", xalign=0.0)
        self.apps_tab_title_lbl.get_style_context().add_class("page-h1")
        head.pack_start(self.apps_tab_title_lbl, False, False, 0)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        head.pack_start(spacer, True, True, 0)

        self.apps_tab_mode_btn = Gtk.Button(label="All Apps")
        self.apps_tab_mode_btn.get_style_context().add_class("secondary-btn")
        self.apps_tab_mode_btn.set_tooltip_text("Switch between Running Apps and All Apps Menu")
        self.apps_tab_mode_btn.connect("clicked", self._on_apps_tab_toggle_mode)
        head.pack_start(self.apps_tab_mode_btn, False, False, 0)

        self.apps_tab_layout_btn = Gtk.Button(label="List")
        self.apps_tab_layout_btn.get_style_context().add_class("secondary-btn")
        self.apps_tab_layout_btn.set_tooltip_text("Toggle between 4-column Grid and 2-column List")
        self.apps_tab_layout_btn.connect("clicked", self._on_apps_tab_toggle_layout)
        head.pack_start(self.apps_tab_layout_btn, False, False, 0)

        vbox.pack_start(head, False, False, 0)

        self.apps_tab_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        vbox.pack_start(self.apps_tab_body, True, True, 0)

        self.android_apps_flow = Gtk.FlowBox()
        return vbox

    def _get_app_launcher(self) -> AppLauncherGtkWindow | None:
        return getattr(self, "app_launcher_win", None)

    def _on_apps_tab_toggle_mode_clicked(self, *_args) -> None:
        self._on_apps_tab_toggle_mode(*_args)

    def _on_apps_tab_toggle_mode(self, *_args) -> None:
        launcher = self._get_app_launcher()
        running = launcher.get_running_apps() if launcher is not None else []
        if self.apps_tab_view_mode == "running":
            self.apps_tab_view_mode = "menu"
        else:
            self.apps_tab_view_mode = "running" if running else "menu"
        self._refresh_android_apps_flow(force=True)

    def _on_apps_tab_toggle_layout(self, *_args) -> None:
        self.apps_tab_layout_mode = "list" if self.apps_tab_layout_mode == "grid" else "grid"
        self._refresh_android_apps_flow(force=True)

    def on_apps_tab_opened(self) -> None:
        launcher = self._get_app_launcher()
        running = launcher.get_running_apps() if launcher is not None else []
        self.apps_tab_view_mode = "running" if running else "menu"
        self._refresh_android_apps_flow(force=True)

    def _refresh_android_apps_flow(self, force: bool = False) -> None:
        body = getattr(self, "apps_tab_body", None)
        if body is None:
            return
        launcher = self._get_app_launcher()
        if launcher is None:
            return

        catalog = launcher.get_catalog_apps()
        running = launcher.get_running_apps()
        if self.apps_tab_view_mode == "running" and not running:
            self.apps_tab_view_mode = "menu"

        sig = (
            self.apps_tab_view_mode,
            self.apps_tab_layout_mode,
            self.apps_tab_category,
            self.apps_tab_query,
            tuple(str(a.get("name")) for a in running),
            tuple((str(a.get("name")), bool(a.get("pinned"))) for a in catalog[:48]),
            getattr(self, "ui_theme", "tray"),
            getattr(self, "color_palette", "carbon"),
            getattr(self, "icon_style", "minimal"),
            bool(getattr(self, "dark_mode", True)),
        )
        if not force and getattr(self, "_last_apps_flow_sig", None) == sig:
            return
        self._last_apps_flow_sig = sig

        for ch in list(body.get_children()):
            body.remove(ch)

        if self.apps_tab_view_mode == "running":
            self.apps_tab_title_lbl.set_text(f"Running Apps ({len(running)})")
            self.apps_tab_mode_btn.set_label("All Apps")
            self.apps_tab_mode_btn.show()
            self.apps_tab_layout_btn.hide()

            list_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            list_card.get_style_context().add_class("sc-card")

            for app_item in running:
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
                row.get_style_context().add_class("running-app-row")

                row.pack_start(launcher.make_squircle_badge(app_item, True), False, False, 0)

                info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
                n_lbl = Gtk.Label(label=str(app_item.get("name") or "App"), xalign=0.0)
                n_lbl.get_style_context().add_class("notif-title")
                s_lbl = Gtk.Label(label="Streaming", xalign=0.0)
                s_lbl.get_style_context().add_class("feature-rail-state")
                info_box.pack_start(n_lbl, False, False, 0)
                info_box.pack_start(s_lbl, False, False, 0)
                row.pack_start(info_box, True, True, 0)

                show_btn = Gtk.Button(label="Show")
                show_btn.get_style_context().add_class("secondary-btn")
                show_btn.connect(
                    "clicked",
                    lambda *_: self.open_feature_window("appstream", start_session=False),
                )
                row.pack_start(show_btn, False, False, 0)

                end_btn = Gtk.Button(label="End")
                end_btn.get_style_context().add_class("danger-btn")
                end_btn.connect("clicked", lambda *_, nm=str(app_item.get("name", "")): launcher.stop_app(nm))
                row.pack_start(end_btn, False, False, 0)

                list_card.pack_start(row, False, False, 0)

            foot_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            open_menu_btn = Gtk.Button(label="Open App Menu")
            open_menu_btn.get_style_context().add_class("secondary-btn")
            open_menu_btn.connect(
                "clicked",
                lambda *_: (setattr(self, "apps_tab_view_mode", "menu"), self._refresh_android_apps_flow(force=True)),
            )
            foot_row.pack_start(open_menu_btn, False, False, 0)

            end_all_btn = Gtk.Button(label="End all")
            end_all_btn.get_style_context().add_class("danger-btn")
            end_all_btn.connect("clicked", lambda *_: launcher.stop_all_apps())
            foot_row.pack_end(end_all_btn, False, False, 0)
            list_card.pack_start(foot_row, False, False, 4)

            body.pack_start(list_card, False, False, 0)

        else:
            self.apps_tab_title_lbl.set_text("Phone Apps")
            self.apps_tab_layout_btn.set_label("List" if self.apps_tab_layout_mode == "grid" else "Grid")
            self.apps_tab_layout_btn.show()
            if running:
                self.apps_tab_mode_btn.set_label(f"Running ({len(running)})")
                self.apps_tab_mode_btn.show()
            else:
                self.apps_tab_mode_btn.set_label("All Apps")
                self.apps_tab_mode_btn.show()

            menu_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            menu_card.get_style_context().add_class("sc-card")

            search_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            search_entry = Gtk.Entry()
            search_entry.get_style_context().add_class("sc-entry")
            search_entry.set_placeholder_text("Search apps…")
            search_entry.set_text(self.apps_tab_query)
            search_row.pack_start(search_entry, True, True, 0)

            reload_btn = Gtk.Button(label="Reload")
            reload_btn.get_style_context().add_class("secondary-btn")
            reload_btn.connect(
                "clicked",
                lambda *_: (
                    self._spawn(self.facade.request("applist")) if self.facade._live_ws() is not None else None,
                    self._refresh_android_apps_flow(force=True),
                ),
            )
            search_row.pack_end(reload_btn, False, False, 0)
            menu_card.pack_start(search_row, False, False, 0)

            cat_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            for cat_key, cat_lbl in AppLauncherGtkWindow.CATEGORIES:
                c_btn = Gtk.Button(label=cat_lbl)
                c_btn.set_relief(Gtk.ReliefStyle.NONE)
                c_btn.get_style_context().add_class(
                    "cat-pill-active" if self.apps_tab_category == cat_key else "cat-pill"
                )
                c_btn.connect(
                    "clicked",
                    lambda *_, ck=cat_key: (
                        setattr(self, "apps_tab_category", ck),
                        self._refresh_android_apps_flow(force=True),
                    ),
                )
                cat_row.pack_start(c_btn, False, False, 0)
            menu_card.pack_start(cat_row, False, False, 0)

            grid_holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

            def _populate_tab_grid(query_str: str) -> None:
                for ch in list(grid_holder.get_children()):
                    grid_holder.remove(ch)
                q = query_str.strip().lower()
                running_names = {str(a.get("name")) for a in launcher.get_running_apps()}
                filtered: list[dict[str, object]] = []
                for a in catalog:
                    nm_l = str(a.get("name", "")).lower()
                    cat_l = str(a.get("cat", "")).lower()
                    if q and (q not in nm_l and q not in cat_l):
                        continue
                    if self.apps_tab_category == "Pinned" and not a.get("pinned"):
                        continue
                    if self.apps_tab_category == "Streaming" and str(a.get("name")) not in running_names:
                        continue
                    if self.apps_tab_category not in ("All", "Pinned", "Streaming") and a.get("cat") != self.apps_tab_category:
                        continue
                    filtered.append(a)

                if not filtered:
                    empty = Gtk.Label(label=f'No apps match "{query_str}"', xalign=0.5)
                    empty.get_style_context().add_class("panel-sub")
                    grid_holder.pack_start(empty, False, False, 14)
                    grid_holder.show_all()
                    return

                cols = 2 if self.apps_tab_layout_mode == "list" else 4
                grid = Gtk.Grid()
                grid.set_column_spacing(8)
                grid.set_row_spacing(8)
                grid.set_column_homogeneous(True)

                for idx, app_item in enumerate(filtered):
                    nm = str(app_item.get("name") or "App")
                    is_run = nm in running_names
                    is_pin = bool(app_item.get("pinned"))

                    ov = Gtk.Overlay()
                    btn = Gtk.Button()
                    btn.set_relief(Gtk.ReliefStyle.NONE)
                    btn.get_style_context().add_class("app-launcher-tile-running" if is_run else "app-launcher-tile")

                    if self.apps_tab_layout_mode == "list":
                        c_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                        c_box.pack_start(launcher.make_squircle_badge(app_item, is_run), False, False, 0)
                        t_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
                        t_col.set_valign(Gtk.Align.CENTER)
                        nm_lbl = Gtk.Label(label=nm, xalign=0.0)
                        nm_lbl.set_ellipsize(Pango.EllipsizeMode.END)
                        nm_lbl.get_style_context().add_class("notif-title")
                        cat_lbl_w = Gtk.Label(
                            label="Running" if is_run else str(app_item.get("cat") or "App"),
                            xalign=0.0,
                        )
                        cat_lbl_w.get_style_context().add_class("feature-rail-state" if is_run else "panel-sub")
                        t_col.pack_start(nm_lbl, False, False, 0)
                        t_col.pack_start(cat_lbl_w, False, False, 0)
                        c_box.pack_start(t_col, True, True, 0)
                        btn.add(c_box)
                    else:
                        c_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
                        c_box.set_halign(Gtk.Align.CENTER)
                        c_box.pack_start(launcher.make_squircle_badge(app_item, is_run), False, False, 0)
                        nm_lbl = Gtk.Label(label=nm)
                        nm_lbl.set_ellipsize(Pango.EllipsizeMode.END)
                        nm_lbl.set_max_width_chars(12)
                        nm_lbl.get_style_context().add_class("notif-title")
                        cat_lbl_w = Gtk.Label(label="Running" if is_run else str(app_item.get("cat") or "App"))
                        cat_lbl_w.set_ellipsize(Pango.EllipsizeMode.END)
                        cat_lbl_w.set_max_width_chars(12)
                        cat_lbl_w.get_style_context().add_class("feature-rail-state" if is_run else "panel-sub")
                        c_box.pack_start(nm_lbl, False, False, 0)
                        c_box.pack_start(cat_lbl_w, False, False, 0)
                        btn.add(c_box)

                    btn.connect("clicked", lambda *_, ai=app_item: launcher.launch_app(ai))
                    ov.add(btn)

                    pin_btn = Gtk.Button(label="Pinned" if is_pin else "Pin")
                    pin_btn.set_relief(Gtk.ReliefStyle.NONE)
                    pin_btn.set_tooltip_text("Pin / Unpin app")
                    pin_btn.set_halign(Gtk.Align.START)
                    pin_btn.set_valign(Gtk.Align.START)
                    pin_btn.connect("clicked", lambda *_, an=nm: launcher.toggle_pin_app(an))
                    ov.add_overlay(pin_btn)

                    if is_run:
                        stop_btn = Gtk.Button(label="x")
                        stop_btn.get_style_context().add_class("corner-cancel-btn")
                        stop_btn.set_halign(Gtk.Align.END)
                        stop_btn.set_valign(Gtk.Align.START)
                        stop_btn.connect("clicked", lambda *_, an=nm: launcher.stop_app(an))
                        ov.add_overlay(stop_btn)

                    grid.attach(ov, idx % cols, idx // cols, 1, 1)

                grid_holder.pack_start(grid, False, False, 0)
                grid_holder.show_all()

            def _on_tab_search_changed(entry_w) -> None:
                self.apps_tab_query = entry_w.get_text()
                _populate_tab_grid(self.apps_tab_query)

            search_entry.connect("changed", _on_tab_search_changed)
            menu_card.pack_start(grid_holder, True, True, 0)
            body.pack_start(menu_card, True, True, 0)
            _populate_tab_grid(self.apps_tab_query)

        body.show_all()

AppsPage = AppsPageMixin
