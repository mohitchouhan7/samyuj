from __future__ import annotations

import contextlib
import os
import subprocess
from pathlib import Path

from samyuj.ui.gtk.gtk_compat import Gdk, Gio, GLib, Gtk, Pango

from samyuj.ui.gtk.assets import (
    make_cairo_icon,
    make_setup_preview_pixbuf,
    make_themed_feature_icon,
)
from samyuj.ui.gtk.theme import (
    get_active_icon_style,
    get_active_palette_tokens,
    get_active_ui_theme,
)

DEFAULT_DEMO_APPS: list[dict[str, object]] = [
    {"name": "Messages",      "glyph": "M",  "color": "#0284c7", "cat": "Chat & Social",  "pkg": "com.google.android.apps.messaging", "pinned": True},
    {"name": "WhatsApp",      "glyph": "W",  "color": "#059669", "cat": "Chat & Social",  "pkg": "com.whatsapp", "pinned": True},
    {"name": "Telegram",      "glyph": "T",  "color": "#0369a1", "cat": "Chat & Social",  "pkg": "org.telegram.messenger", "pinned": True},
    {"name": "Signal",        "glyph": "S",  "color": "#1d4ed8", "cat": "Chat & Social",  "pkg": "org.thoughtcrime.securesms", "pinned": False},
    {"name": "Instagram",     "glyph": "IG", "color": "#9d174d", "cat": "Chat & Social",  "pkg": "com.instagram.android", "pinned": True},
    {"name": "Discord",       "glyph": "D",  "color": "#4338ca", "cat": "Chat & Social",  "pkg": "com.discord", "pinned": False},
    {"name": "Slack",         "glyph": "SL", "color": "#5b21b6", "cat": "Work & Docs",    "pkg": "com.Slack", "pinned": True},
    {"name": "Browser",       "glyph": "B",  "color": "#0369a1", "cat": "System & Tools", "pkg": "com.android.chrome", "pinned": True},
    {"name": "Firefox",       "glyph": "FF", "color": "#9a3412", "cat": "System & Tools", "pkg": "org.mozilla.firefox", "pinned": False},
    {"name": "YouTube",       "glyph": "YT",  "color": "#991b1b", "cat": "Media & Audio",  "pkg": "com.google.android.youtube", "pinned": True},
    {"name": "Spotify",       "glyph": "SP",  "color": "#047857", "cat": "Media & Audio",  "pkg": "com.spotify.music", "pinned": True},
    {"name": "Gmail",         "glyph": "M",  "color": "#991b1b", "cat": "Work & Docs",    "pkg": "com.google.android.gm", "pinned": False},
    {"name": "Maps",          "glyph": "P",  "color": "#0f766e", "cat": "System & Tools", "pkg": "com.google.android.apps.maps", "pinned": False},
    {"name": "Camera",        "glyph": "C",  "color": "#334155", "cat": "Media & Audio",  "pkg": "com.android.camera2", "pinned": False},
    {"name": "Gallery",       "glyph": "G",  "color": "#0e7490", "cat": "Media & Audio",  "pkg": "com.google.android.apps.photos", "pinned": False},
    {"name": "Files",         "glyph": "F",  "color": "#334155", "cat": "System & Tools", "pkg": "com.google.android.apps.nbu.files", "pinned": False},
    {"name": "Calendar",      "glyph": "31", "color": "#1e40af", "cat": "Work & Docs",    "pkg": "com.google.android.calendar", "pinned": False},
    {"name": "Keep Notes",    "glyph": "N",  "color": "#854d0e", "cat": "Work & Docs",    "pkg": "com.google.android.keep", "pinned": False},
    {"name": "Clock",         "glyph": "CL", "color": "#334155", "cat": "System & Tools", "pkg": "com.google.android.deskclock", "pinned": False},
    {"name": "GitHub",        "glyph": "GH", "color": "#1e293b", "cat": "Work & Docs",    "pkg": "com.github.android", "pinned": False},
    {"name": "Termux",        "glyph": ">_", "color": "#1e293b", "cat": "System & Tools", "pkg": "com.termux", "pinned": False},
    {"name": "Authenticator", "glyph": "2F", "color": "#0369a1", "cat": "System & Tools", "pkg": "com.google.android.apps.authenticator2", "pinned": False},
    {"name": "Bitwarden",     "glyph": "BW", "color": "#1e40af", "cat": "System & Tools", "pkg": "com.x8bit.bitwarden", "pinned": False},
    {"name": "VLC Player",    "glyph": "V",  "color": "#9a3412", "cat": "Media & Audio",  "pkg": "org.videolan.vlc", "pinned": False},
    {"name": "Netflix",       "glyph": "N",  "color": "#7f1d1d", "cat": "Media & Audio",  "pkg": "com.netflix.mediaclient", "pinned": False},
    {"name": "Prime Video",   "glyph": "PV", "color": "#075985", "cat": "Media & Audio",  "pkg": "com.amazon.avod.thirdpartyclient", "pinned": False},
    {"name": "Reddit",        "glyph": "R",  "color": "#9a3412", "cat": "Chat & Social",  "pkg": "com.reddit.frontpage", "pinned": False},
    {"name": "LinkedIn",      "glyph": "in", "color": "#075985", "cat": "Work & Docs",    "pkg": "com.linkedin.android", "pinned": False},
    {"name": "Google Drive",  "glyph": "GD", "color": "#0f766e", "cat": "Work & Docs",    "pkg": "com.google.android.apps.docs", "pinned": False},
    {"name": "Docs",          "glyph": "D",  "color": "#1e40af", "cat": "Work & Docs",    "pkg": "com.google.android.apps.docs.editors.docs", "pinned": False},
    {"name": "Sheets",        "glyph": "S",  "color": "#065f46", "cat": "Work & Docs",    "pkg": "com.google.android.apps.docs.editors.sheets", "pinned": False},
    {"name": "Notion",        "glyph": "N",  "color": "#334155", "cat": "Work & Docs",    "pkg": "notion.id", "pinned": False},
    {"name": "Obsidian",      "glyph": "O",  "color": "#4c1d95", "cat": "Work & Docs",    "pkg": "md.obsidian", "pinned": False},
    {"name": "Podcasts",      "glyph": "PC", "color": "#4c1d95", "cat": "Media & Audio",  "pkg": "com.bambuna.podcastaddict", "pinned": False},
    {"name": "Audible",       "glyph": "Au", "color": "#92400e", "cat": "Media & Audio",  "pkg": "com.audible.application", "pinned": False},
    {"name": "Lightroom",     "glyph": "Lr", "color": "#075985", "cat": "Media & Audio",  "pkg": "com.adobe.lrmobile", "pinned": False},
    {"name": "Snapseed",      "glyph": "Sn", "color": "#065f46", "cat": "Media & Audio",  "pkg": "com.niksoftware.snapseed", "pinned": False},
    {"name": "Canva",         "glyph": "Cv", "color": "#0e7490", "cat": "Work & Docs",    "pkg": "com.canva.editor", "pinned": False},
    {"name": "Calculator",    "glyph": "CA",  "color": "#334155", "cat": "System & Tools", "pkg": "com.google.android.calculator", "pinned": False},
    {"name": "Weather",       "glyph": "WE",  "color": "#0284c7", "cat": "System & Tools", "pkg": "com.google.android.apps.weather", "pinned": False},
    {"name": "Contacts",      "glyph": "CT", "color": "#0f766e", "cat": "Chat & Social",  "pkg": "com.google.android.contacts", "pinned": False},
    {"name": "GPay",          "glyph": "GP",  "color": "#1e40af", "cat": "System & Tools", "pkg": "com.google.android.apps.nbu.paisa.user", "pinned": False},
]

class AppLauncherGtkWindow(Gtk.Window):

    CATEGORIES: tuple[tuple[str, str], ...] = (
        ("All", "All"),
        ("Pinned", "Pinned"),
        ("Chat & Social", "Social"),
        ("Media & Audio", "Media"),
        ("Work & Docs", "Work"),
        ("System & Tools", "Tools"),
    )

    def __init__(self, app) -> None:
        super().__init__(title="Phone Apps")
        self.app = app
        self.facade = app.facade
        self.get_style_context().add_class("app-launcher-window")
        self.set_default_size(415, 510)
        self.set_size_request(350, 390)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.connect("delete-event", self._on_delete_event)

        self.view_mode: str = "menu"
        self.layout_mode: str = "grid"
        self.active_category: str = "All"
        self.search_query: str = ""
        self._running_apps: list[dict[str, object]] = []
        self._pinned_names: set[str] = {str(a["name"]) for a in DEFAULT_DEMO_APPS if a.get("pinned")}

        self._outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(self._outer)

        self._header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._header.get_style_context().add_class("app-launcher-header")
        self._outer.pack_start(self._header, False, False, 0)

        self._ico_holder = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self._ico_holder.pack_start(make_cairo_icon("apps", 18), False, False, 0)
        self._header.pack_start(self._ico_holder, False, False, 0)

        self._title_lbl = Gtk.Label(label="Phone Apps", xalign=0.0)
        self._title_lbl.get_style_context().add_class("panel-h2")
        self._header.pack_start(self._title_lbl, False, False, 0)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        self._header.pack_start(spacer, True, True, 0)

        self.all_apps_menu_btn = Gtk.Button(label="All Apps")
        self.all_apps_menu_btn.set_tooltip_text("Toggle between Running Apps and All Apps Menu")
        self.all_apps_menu_btn.get_style_context().add_class("secondary-btn")
        self.all_apps_menu_btn.connect("clicked", self._on_toggle_view_mode_clicked)
        self._header.pack_start(self.all_apps_menu_btn, False, False, 0)

        self.layout_toggle_btn = Gtk.Button(label="List")
        self.layout_toggle_btn.set_tooltip_text("Switch between 4-column Grid and 2-column List")
        self.layout_toggle_btn.get_style_context().add_class("secondary-btn")
        self.layout_toggle_btn.connect("clicked", self._on_toggle_layout_mode_clicked)
        self._header.pack_start(self.layout_toggle_btn, False, False, 0)

        close_btn = Gtk.Button(label="x")
        close_btn.set_tooltip_text("Close Phone Apps window")
        close_btn.get_style_context().add_class("secondary-btn")
        close_btn.connect("clicked", lambda *_: self.hide())
        self._header.pack_start(close_btn, False, False, 0)

        self._body_scroll = Gtk.ScrolledWindow()
        self._body_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        with contextlib.suppress(Exception):
            from samyuj.ui.gtk.smooth_scroll import attach_smooth_scroll
            attach_smooth_scroll(self._body_scroll)
        self._outer.pack_start(self._body_scroll, True, True, 0)

        self._body_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self._body_box.set_margin_top(12)
        self._body_box.set_margin_bottom(12)
        self._body_box.set_margin_start(13)
        self._body_box.set_margin_end(13)
        self._body_scroll.add(self._body_box)

        self.rebuild_content()

    def _on_delete_event(self, *_args) -> bool:
        self.hide()
        return True

    def get_catalog_apps(self) -> list[dict[str, object]]:
        raw = list(getattr(self.facade, "app_list", []) or [])
        if raw:
            out: list[dict[str, object]] = []
            palette_cols = ("#0284c7", "#059669", "#1d4ed8", "#9d174d", "#4338ca", "#0e7490", "#1e40af", "#334155")
            cats = ("Chat & Social", "Media & Audio", "Work & Docs", "System & Tools")
            for idx, item in enumerate(raw[:96]):
                pkg = str(item.get("pkg") or "").strip()
                name = str(item.get("name") or pkg or "App").strip()
                cmp_ = str(item.get("cmp") or "").strip()
                glyph = name[:2].upper() if len(name) > 1 else (name[:1].upper() or "A")
                col = palette_cols[idx % len(palette_cols)]
                cat = cats[idx % len(cats)]
                out.append({
                    "name": name,
                    "glyph": glyph,
                    "color": col,
                    "cat": cat,
                    "pkg": pkg,
                    "cmp": cmp_,
                    "pinned": name in self._pinned_names,
                })
            if out:
                return out
        demo_mode = bool((getattr(self.facade, "user_settings", {}) or {}).get("demo_mode", False))
        if not demo_mode:
            return []
        return [
            {
                "name": str(a["name"]),
                "glyph": str(a["glyph"]),
                "color": str(a["color"]),
                "cat": str(a["cat"]),
                "pkg": str(a["pkg"]),
                "cmp": "",
                "pinned": str(a["name"]) in self._pinned_names,
            }
            for a in DEFAULT_DEMO_APPS
        ]

    def get_running_apps(self) -> list[dict[str, object]]:
        with contextlib.suppress(Exception):
            snap = self.facade.live_feature_snapshot() if hasattr(self.facade, "live_feature_snapshot") else {}
            as_info = snap.get("appstream") or {}
            if as_info.get("on") and not self._running_apps:
                pkg = str(as_info.get("pkg") or "Messages").strip()
                cat = self.get_catalog_apps()
                matched = next((a for a in cat if a["pkg"] == pkg or str(a["name"]).lower() == pkg.lower()), None)
                if matched is not None:
                    self._running_apps.append(dict(matched))
                else:
                    self._running_apps.append({
                        "name": pkg.split(".")[-1].title(),
                        "glyph": "M",
                        "color": "#0284c7",
                        "cat": "App",
                        "pkg": pkg,
                        "cmp": "",
                    })
        return list(self._running_apps)

    def _sync_all_surfaces(self) -> None:
        self.rebuild_content()
        if hasattr(self.app, "_refresh_android_apps_flow"):
            self.app._refresh_android_apps_flow(force=True)
        if getattr(self.app, "tray_popover", None) is not None:
            self.app.tray_popover.refresh_tray_ui()

    def open_smart_view(self) -> None:
        running = self.get_running_apps()
        self.view_mode = "running" if running else "menu"
        self.rebuild_content()
        self.show_all()
        self.present()

    def switch_view_mode(self, mode: str) -> None:
        self.view_mode = "running" if str(mode).strip().lower() == "running" else "menu"
        self._sync_all_surfaces()
        self.show_all()

    def _on_toggle_view_mode_clicked(self, *_args) -> None:
        if self.view_mode == "running":
            self.switch_view_mode("menu")
        else:
            if self.get_running_apps():
                self.switch_view_mode("running")
            else:
                self.switch_view_mode("menu")

    def _on_toggle_layout_mode_clicked(self, *_args) -> None:
        self.layout_mode = "list" if self.layout_mode == "grid" else "grid"
        self._sync_all_surfaces()

    def toggle_pin_app(self, app_name: str) -> None:
        if app_name in self._pinned_names:
            self._pinned_names.discard(app_name)
        else:
            self._pinned_names.add(app_name)
        self._sync_all_surfaces()

    def launch_app(self, app_entry: dict[str, object]) -> None:
        name = str(app_entry.get("name") or "App")
        pkg = str(app_entry.get("pkg") or name.lower())
        cmp_ = str(app_entry.get("cmp") or "")
        if not any(str(a.get("name")) == name for a in self._running_apps):
            self._running_apps.append(dict(app_entry))
        target_spec = f"{pkg}/{cmp_}" if cmp_ else pkg
        self.app.open_feature_window("appstream", start_session=False)
        if self.facade._live_ws() is not None:
            self.app._spawn(self.facade.request(f"appstream:{target_spec}"))
        self._sync_all_surfaces()

    def stop_app(self, app_name: str) -> None:
        self._running_apps = [a for a in self._running_apps if str(a.get("name")) != app_name]
        if not self._running_apps:
            self.app.stop_feature("appstream")
            self.view_mode = "menu"
            if hasattr(self.app, "apps_tab_view_mode"):
                self.app.apps_tab_view_mode = "menu"
        self._sync_all_surfaces()

    def stop_all_apps(self) -> None:
        self._running_apps.clear()
        self.app.stop_feature("appstream")
        self.view_mode = "menu"
        if hasattr(self.app, "apps_tab_view_mode"):
            self.app.apps_tab_view_mode = "menu"
        self._sync_all_surfaces()

    def make_squircle_badge(self, app_item: dict[str, object], is_streaming: bool) -> Gtk.Widget:
        tokens = get_active_palette_tokens()
        icon_style = get_active_icon_style()
        fg_col = "#ffffff" if (is_streaming or icon_style == "toned") else tokens.get("fg_strong", "#f8fafc")
        glyph_str = str(app_item.get("glyph") or "A")
        dot_markup = ""

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        box.get_style_context().add_class("app-squircle-streaming" if is_streaming else "app-squircle-minimal")
        box.set_size_request(38, 38)
        box.set_halign(Gtk.Align.CENTER)
        box.set_valign(Gtk.Align.CENTER)
        lbl = Gtk.Label()
        col_hex = str(app_item.get("color") or "#0284c7") if (icon_style == "toned" and not is_streaming) else fg_col
        lbl.set_markup(f'<span foreground="{col_hex}" weight="bold" size="12500">{glyph_str}</span>{dot_markup}')
        box.pack_start(lbl, True, True, 0)
        return box

    def rebuild_content(self) -> None:
        for ch in list(self._body_box.get_children()):
            self._body_box.remove(ch)

        catalog = self.get_catalog_apps()
        running = self.get_running_apps()
        if self.view_mode == "running" and not running:
            self.view_mode = "menu"

        if self.view_mode == "running":
            self._title_lbl.set_text(f"Running Apps ({len(running)})")
            self.all_apps_menu_btn.set_label("All Apps")
            self.all_apps_menu_btn.show()
            self.layout_toggle_btn.hide()

            for app_item in running:
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
                row.get_style_context().add_class("running-app-row")

                row.pack_start(self.make_squircle_badge(app_item, True), False, False, 0)

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
                    lambda *_: self.app.open_feature_window("appstream", start_session=False),
                )
                row.pack_start(show_btn, False, False, 0)

                end_btn = Gtk.Button(label="End")
                end_btn.get_style_context().add_class("danger-btn")
                end_btn.connect("clicked", lambda *_, nm=str(app_item.get("name", "")): self.stop_app(nm))
                row.pack_start(end_btn, False, False, 0)

                self._body_box.pack_start(row, False, False, 0)

            foot_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            open_menu_btn = Gtk.Button(label="Open App Menu")
            open_menu_btn.get_style_context().add_class("secondary-btn")
            open_menu_btn.connect("clicked", lambda *_: self.switch_view_mode("menu"))
            foot_row.pack_start(open_menu_btn, False, False, 0)

            end_all_btn = Gtk.Button(label="End all")
            end_all_btn.get_style_context().add_class("danger-btn")
            end_all_btn.connect("clicked", lambda *_: self.stop_all_apps())
            foot_row.pack_end(end_all_btn, False, False, 0)
            self._body_box.pack_start(foot_row, False, False, 4)

        else:
            self._title_lbl.set_text("Phone Apps")
            self.layout_toggle_btn.set_label("List" if self.layout_mode == "grid" else "Grid")
            self.layout_toggle_btn.show()
            if running:
                self.all_apps_menu_btn.set_label(f"Running ({len(running)})")
                self.all_apps_menu_btn.show()
            else:
                self.all_apps_menu_btn.set_label("All Apps")
                self.all_apps_menu_btn.show()

            search_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            search_entry = Gtk.Entry()
            search_entry.get_style_context().add_class("sc-entry")
            search_entry.set_placeholder_text("Search apps…")
            search_entry.set_text(self.search_query)
            search_row.pack_start(search_entry, True, True, 0)

            reload_btn = Gtk.Button(label="Reload")
            reload_btn.set_tooltip_text("Reload apps")
            reload_btn.get_style_context().add_class("secondary-btn")
            reload_btn.connect(
                "clicked",
                lambda *_: (
                    self.app._spawn(self.facade.request("applist")) if self.facade._live_ws() is not None else None,
                    self.rebuild_content(),
                ),
            )
            search_row.pack_end(reload_btn, False, False, 0)
            self._body_box.pack_start(search_row, False, False, 0)

            cat_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            for cat_key, cat_lbl in self.CATEGORIES:
                c_btn = Gtk.Button(label=cat_lbl)
                c_btn.set_relief(Gtk.ReliefStyle.NONE)
                c_btn.get_style_context().add_class(
                    "cat-pill-active" if self.active_category == cat_key else "cat-pill"
                )
                c_btn.connect(
                    "clicked",
                    lambda *_, ck=cat_key: (setattr(self, "active_category", ck), self.rebuild_content()),
                )
                cat_row.pack_start(c_btn, False, False, 0)
            self._body_box.pack_start(cat_row, False, False, 0)

            grid_holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

            def _populate_grid(query_str: str) -> None:
                for ch in list(grid_holder.get_children()):
                    grid_holder.remove(ch)
                q = query_str.strip().lower()
                running_names = {str(a.get("name")) for a in self.get_running_apps()}
                filtered: list[dict[str, object]] = []
                for a in catalog:
                    nm_l = str(a.get("name", "")).lower()
                    cat_l = str(a.get("cat", "")).lower()
                    if q and (q not in nm_l and q not in cat_l):
                        continue
                    if self.active_category == "Pinned" and not a.get("pinned"):
                        continue
                    if self.active_category == "Streaming" and str(a.get("name")) not in running_names:
                        continue
                    if self.active_category not in ("All", "Pinned", "Streaming") and a.get("cat") != self.active_category:
                        continue
                    filtered.append(a)

                if not filtered:
                    empty = Gtk.Label(label=f'No apps match "{query_str}"', xalign=0.5)
                    empty.get_style_context().add_class("panel-sub")
                    grid_holder.pack_start(empty, False, False, 14)
                    grid_holder.show_all()
                    return

                cols = 2 if self.layout_mode == "list" else 4
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

                    if self.layout_mode == "list":
                        c_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                        c_box.pack_start(self.make_squircle_badge(app_item, is_run), False, False, 0)
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
                        c_box.pack_start(self.make_squircle_badge(app_item, is_run), False, False, 0)
                        nm_lbl = Gtk.Label(label=nm)
                        nm_lbl.set_ellipsize(Pango.EllipsizeMode.END)
                        nm_lbl.set_max_width_chars(10)
                        nm_lbl.get_style_context().add_class("notif-title")
                        cat_lbl_w = Gtk.Label(label="Running" if is_run else str(app_item.get("cat") or "App"))
                        cat_lbl_w.set_ellipsize(Pango.EllipsizeMode.END)
                        cat_lbl_w.set_max_width_chars(11)
                        cat_lbl_w.get_style_context().add_class("feature-rail-state" if is_run else "panel-sub")
                        c_box.pack_start(nm_lbl, False, False, 0)
                        c_box.pack_start(cat_lbl_w, False, False, 0)
                        btn.add(c_box)

                    btn.connect("clicked", lambda *_, ai=app_item: self.launch_app(ai))
                    ov.add(btn)

                    pin_btn = Gtk.Button(label="Pinned" if is_pin else "Pin")
                    pin_btn.set_relief(Gtk.ReliefStyle.NONE)
                    pin_btn.set_tooltip_text("Pin / Unpin app")
                    pin_btn.set_halign(Gtk.Align.START)
                    pin_btn.set_valign(Gtk.Align.START)
                    pin_btn.connect("clicked", lambda *_, an=nm: self.toggle_pin_app(an))
                    ov.add_overlay(pin_btn)

                    if is_run:
                        stop_btn = Gtk.Button(label="x")
                        stop_btn.get_style_context().add_class("corner-cancel-btn")
                        stop_btn.set_halign(Gtk.Align.END)
                        stop_btn.set_valign(Gtk.Align.START)
                        stop_btn.connect("clicked", lambda *_, an=nm: self.stop_app(an))
                        ov.add_overlay(stop_btn)

                    grid.attach(ov, idx % cols, idx // cols, 1, 1)

                grid_holder.pack_start(grid, False, False, 0)
                grid_holder.show_all()

            def _on_search_changed(entry_w) -> None:
                self.search_query = entry_w.get_text()
                _populate_grid(self.search_query)

            search_entry.connect("changed", _on_search_changed)
            self._body_box.pack_start(grid_holder, True, True, 0)
            _populate_grid(self.search_query)

        self._body_box.show_all()

class TrayPopoverGtkWindow(Gtk.Window):

    TRAY_TILES: tuple[tuple[str, str, str], ...] = (
        ("desktop", "DT", "Desktop"),
        ("apps", "AP", "Apps"),
        ("crosscontrol", "CC", "Cross"),
        ("screenshare", "SS", "Screen"),
        ("clipboard", "CB", "Clipboard"),
        ("webcam", "WC", "Webcam"),
    )

    TRAY_NAV: tuple[tuple[str, str, str], ...] = (
        ("home", "", "Home"),
        ("apps", "AP", "Apps"),
        ("files", "", "Files"),
        ("alerts", "", "Alerts"),
        ("settings", "", "Settings"),
    )

    def __init__(self, app) -> None:
        super().__init__(title="Samyuj Tray")
        self.app = app
        self.facade = app.facade
        self.get_style_context().add_class("tray-popover-window")
        self.set_default_size(356, 560)
        self.set_size_request(336, 480)
        self.set_decorated(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_type_hint(Gdk.WindowTypeHint.POPUP_MENU)
        self.connect("delete-event", self._on_delete_event)

        self.current_view: str = "home"
        self.conn_pref: str = "Auto"
        self.files_tab: str = "phone"
        self.md_orientation: str = "Landscape"
        self.md_resolution: str = "1920x1080"
        self.phone_ss_mode: str = "mirror"

        self._outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(self._outer)

        self._body_scroll = Gtk.ScrolledWindow()
        self._body_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        with contextlib.suppress(Exception):
            from samyuj.ui.gtk.smooth_scroll import attach_smooth_scroll
            attach_smooth_scroll(self._body_scroll)
        self._outer.pack_start(self._body_scroll, True, True, 0)

        self._body_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self._body_box.set_margin_top(12)
        self._body_box.set_margin_bottom(12)
        self._body_box.set_margin_start(12)
        self._body_box.set_margin_end(12)
        self._body_scroll.add(self._body_box)

        self._nav_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        self._nav_bar.set_homogeneous(True)
        self._nav_bar.get_style_context().add_class("tray-nav-bar")
        self._outer.pack_end(self._nav_bar, False, False, 0)

        self._nav_btns: dict[str, Gtk.Button] = {}
        self._nav_lbls: dict[str, Gtk.Label] = {}
        for nav_id, glyph, label_txt in self.TRAY_NAV:
            btn = Gtk.Button()
            btn.set_relief(Gtk.ReliefStyle.NONE)
            btn.get_style_context().add_class("tray-nav-btn")
            b_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            b_box.set_halign(Gtk.Align.CENTER)
            b_box.pack_start(make_cairo_icon(nav_id, 16), False, False, 0)
            t_lbl = Gtk.Label(label=label_txt)
            b_box.pack_start(t_lbl, False, False, 0)
            btn.add(b_box)
            btn.connect("clicked", lambda *_, nid=nav_id: self.on_nav_clicked(nid))
            self._nav_bar.pack_start(btn, True, True, 0)
            self._nav_btns[nav_id] = btn
            self._nav_lbls[nav_id] = t_lbl

        self.refresh_tray_ui()

    def _on_delete_event(self, *_args) -> bool:
        self.hide()
        return True

    def position_near_tray(self, x: int | None = None, y: int | None = None) -> None:
        with contextlib.suppress(Exception):
            disp = Gdk.Display.get_default()
            if disp is None:
                return
            if x is None or y is None or (x == 0 and y == 0):
                seat = disp.get_default_seat()
                if seat is not None and seat.get_pointer() is not None:
                    _, px, py = seat.get_pointer().get_position()
                    x, y = int(px), int(py)
            mon = disp.get_primary_monitor() or (disp.get_monitor(0) if disp.get_n_monitors() > 0 else None)
            if mon is not None:
                wa = mon.get_workarea()
                win_w, win_h = 356, 560
                tx = (x - win_w // 2) if (x and x > 0) else (wa.x + wa.width - win_w - 12)
                ty = (y + 12) if (y and y > 0 and y < wa.height // 2) else (
                    (y - win_h - 12) if (y and y >= wa.height // 2) else (wa.y + 8)
                )
                tx = max(wa.x + 8, min(tx, wa.x + wa.width - win_w - 8))
                ty = max(wa.y + 8, min(ty, wa.y + wa.height - win_h - 8))
                self.move(tx, ty)

    def on_nav_clicked(self, nav_id: str) -> None:
        if nav_id == "apps":
            self.app.open_app_launcher_window()
            self.refresh_tray_ui()
            return
        self.current_view = nav_id
        self.refresh_tray_ui()

    def switch_tray_view(self, view_id: str) -> None:
        self.current_view = view_id
        self.refresh_tray_ui()

    def get_non_app_running_sessions(self) -> list[tuple[str, str]]:
        snap = self.facade.live_feature_snapshot() if hasattr(self.facade, "live_feature_snapshot") else {}
        sessions: list[tuple[str, str]] = []
        if self.app._is_service_active("desktop", snap):
            dw, dh = getattr(self.facade, "desktop_res", (1920, 1080))
            sessions.append(("desktop", f"Mobile Desktop ({dw}×{dh})"))
        if self.app._is_service_active("phone_on_pc", snap):
            mode_lbl = "Virtual display" if getattr(self.app, "_phone_on_pc_mode", "mirror") == "mobile" else "Mirror"
            sessions.append(("phone_on_pc", f"Mobile screen · {mode_lbl}"))
        elif self.app._is_service_active("share_pc_screen", snap):
            sessions.append(("screenshare", "Share PC screen"))
        if self.app._is_service_active("webcam", snap):
            sessions.append(("webcam", "Webcam"))
        return sessions

    def on_tile_clicked(self, tile_id: str) -> None:
        snap = self.facade.live_feature_snapshot() if hasattr(self.facade, "live_feature_snapshot") else {}
        if tile_id == "apps":
            self.app.open_app_launcher_window()
            self.refresh_tray_ui()
            return
        if tile_id == "desktop":
            if self.app._is_service_active("desktop", snap):
                self.app.open_feature_window("desktop", start_session=False)
            else:
                dw, dh = getattr(self.facade, "desktop_res", (1920, 1080))
                self.md_orientation = "Portrait" if dh > dw else "Landscape"
                self.md_resolution = f"{dw}x{dh}"
                self.current_view = "desktop_setup"
            self.refresh_tray_ui()
            return
        if tile_id == "screenshare":
            if self.app._is_service_active("screenshare", snap):
                target = "phone_on_pc" if self.app._is_service_active("phone_on_pc", snap) else "screenshare"
                self.app.open_feature_window(target, start_session=False)
            else:
                self.current_view = "screen_hub"
            self.refresh_tray_ui()
            return
        if tile_id == "webcam":
            if self.app._is_service_active("webcam", snap):
                self.app.stop_feature("webcam")
            else:
                self.app.open_feature_window("webcam", start_session=True)
            self.refresh_tray_ui()
            return
        if tile_id == "clipboard":
            new_state = not bool(self.facade.clip_sync.enabled)
            self.facade.clip_sync.set_enabled(new_state)
            self.app._refresh_all()
            self.refresh_tray_ui()
            return
        if tile_id == "crosscontrol":
            self.app._on_feature_clicked("crosscontrol")
            self.refresh_tray_ui()
            return

    def _build_segmented_control(self, options: tuple[str, ...], current: str, on_select) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=1)
        box.get_style_context().add_class("tray-seg-box")
        for opt in options:
            btn = Gtk.Button(label=opt)
            btn.set_relief(Gtk.ReliefStyle.NONE)
            btn.get_style_context().add_class("tray-seg-btn-active" if opt == current else "tray-seg-btn")
            btn.connect("clicked", lambda *_, val=opt: on_select(val))
            box.pack_start(btn, False, False, 0)
        return box

    def refresh_tray_ui(self) -> None:
        for ch in list(self._body_box.get_children()):
            self._body_box.remove(ch)

        active_nav = "home" if self.current_view in ("desktop_setup", "screen_hub", "screen_phone", "screen_pc") else self.current_view
        running_apps_count = len(self.app.app_launcher_win.get_running_apps()) if getattr(self.app, "app_launcher_win", None) else 0
        notif_count = len(getattr(self.facade, "notifications", []) or []) or (3 if bool((getattr(self.facade, "user_settings", {}) or {}).get("demo_mode", False)) else 0)

        if "apps" in self._nav_lbls:
            self._nav_lbls["apps"].set_text(f"Apps ({running_apps_count})" if running_apps_count else "Apps")
        if "alerts" in self._nav_lbls:
            self._nav_lbls["alerts"].set_text(f"Alerts ({notif_count})" if notif_count else "Alerts")

        for nid, nbtn in self._nav_btns.items():
            ctx = nbtn.get_style_context()
            if nid == active_nav:
                ctx.remove_class("tray-nav-btn")
                ctx.add_class("tray-nav-btn-active")
            else:
                ctx.remove_class("tray-nav-btn-active")
                ctx.add_class("tray-nav-btn")

        if self.current_view == "home":
            self._render_home_view()
        elif self.current_view == "desktop_setup":
            self._render_desktop_setup_view()
        elif self.current_view == "screen_hub":
            self._render_screen_hub_view()
        elif self.current_view == "screen_phone":
            self._render_screen_phone_view()
        elif self.current_view == "screen_pc":
            self._render_screen_pc_view()
        elif self.current_view == "files":
            self._render_files_view()
        elif self.current_view == "alerts":
            self._render_alerts_view()
        elif self.current_view == "settings":
            self._render_settings_view()

        self._body_box.show_all()

    def _render_home_view(self) -> None:
        c = self.facade._live_ws() if hasattr(self.facade, "_live_ws") else None
        raw_name = self.facade._best_device_name(c) if hasattr(self.facade, "_best_device_name") else ""
        dev_name = raw_name if (raw_name and raw_name != "Phone") else (raw_name or "Phone")
        demo_mode = bool((getattr(self.facade, "user_settings", {}) or {}).get("demo_mode", False))
        bat_raw = (getattr(self.facade, "phone_telemetry", {}) or {}).get("battery")
        bat = int(bat_raw) if bat_raw is not None else (82 if demo_mode else 0)
        st_free_raw = (getattr(self.facade, "phone_storage", {}) or {}).get("free_bytes")
        st_free = int(st_free_raw) if st_free_raw else (96 * (1024 ** 3) if demo_mode else 0)
        free_gb = f"{st_free / (1024 ** 3):.0f} GB free" if st_free > 0 else "Storage pending"

        dev_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        if get_active_ui_theme() != "tray":
            dev_row.get_style_context().add_class("tray-dev-card")
        av_btn = Gtk.Button(label=dev_name[:1].upper() if dev_name else "G")
        av_btn.set_relief(Gtk.ReliefStyle.NONE)
        av_btn.get_style_context().add_class("tray-dev-av")
        dev_row.pack_start(av_btn, False, False, 0)

        dev_info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        dn_lbl = Gtk.Label(label=dev_name, xalign=0.0)
        dn_lbl.get_style_context().add_class("notif-title")
        st_lbl = Gtk.Label(xalign=0.0)
        if c is not None or demo_mode:
            bat_part = f"{bat}% · " if bat > 0 else ""
            st_lbl.set_markup(f'<span foreground="#10b981" weight="bold">Connected</span> · {bat_part}{free_gb}')
        else:
            st_lbl.set_markup('<span foreground="#94a3b8">Disconnected</span>')
        st_lbl.get_style_context().add_class("panel-sub")
        dev_info.pack_start(dn_lbl, False, False, 0)
        dev_info.pack_start(st_lbl, False, False, 0)
        dev_row.pack_start(dev_info, True, True, 0)

        def _set_conn_pref(val: str) -> None:
            self.conn_pref = val
            self.refresh_tray_ui()

        dev_row.pack_end(
            self._build_segmented_control(("Auto", "USB", "Wi-Fi"), self.conn_pref, _set_conn_pref),
            False, False, 0,
        )
        self._body_box.pack_start(dev_row, False, False, 0)

        non_app_sessions = self.get_non_app_running_sessions()
        if non_app_sessions:
            run_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            run_box.get_style_context().add_class("tray-run-box")
            for fid, label_txt in non_app_sessions:
                r_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                r_lbl = Gtk.Label(xalign=0.0)
                r_lbl.set_markup(f'<b>{label_txt}</b>')
                r_row.pack_start(r_lbl, True, True, 0)

                s_btn = Gtk.Button(label="Show")
                s_btn.get_style_context().add_class("secondary-btn")
                s_btn.connect("clicked", lambda *_, k=fid: self.app.open_feature_window(k, start_session=False))
                r_row.pack_start(s_btn, False, False, 0)

                e_btn = Gtk.Button(label="End")
                e_btn.get_style_context().add_class("danger-btn")
                e_btn.connect("clicked", lambda *_, k=fid: (self.app.stop_feature(k), self.refresh_tray_ui()))
                r_row.pack_start(e_btn, False, False, 0)
                run_box.pack_start(r_row, False, False, 0)
            self._body_box.pack_start(run_box, False, False, 0)

        snap = self.facade.live_feature_snapshot() if hasattr(self.facade, "live_feature_snapshot") else {}
        running_apps_count = len(self.app.app_launcher_win.get_running_apps()) if getattr(self.app, "app_launcher_win", None) else 0
        dw, dh = getattr(self.facade, "desktop_res", (1920, 1080))
        edge_name = str(self.facade.user_settings.get("cross_control_edge") or "left").capitalize()

        tile_subs = {
            "desktop": f"{dw}×{dh}" if self.app._is_service_active("desktop", snap) else "Orientation & res",
            "apps": f"{running_apps_count} running" if running_apps_count > 0 else "Phone apps",
            "crosscontrol": f"{edge_name} edge",
            "screenshare": "Active" if self.app._is_service_active("screenshare", snap) else "PC or Mobile",
            "clipboard": "On" if self.app._is_service_active("clipboard", snap) else "Off",
            "webcam": "Streaming" if self.app._is_service_active("webcam", snap) else "Camera",
        }

        tiles_grid = Gtk.Grid()
        tiles_grid.set_column_spacing(8)
        tiles_grid.set_row_spacing(8)
        tiles_grid.set_column_homogeneous(True)
        for idx, (tid, _glyph, t_title) in enumerate(self.TRAY_TILES):
            is_on = (
                running_apps_count > 0
                if tid == "apps"
                else self.app._is_service_active(tid, snap)
            )
            ov = Gtk.Overlay()
            btn = Gtk.Button()
            btn.set_relief(Gtk.ReliefStyle.NONE)
            btn.get_style_context().add_class("tray-tile-active" if is_on else "tray-tile")

            t_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            t_box.set_halign(Gtk.Align.CENTER)
            t_box.pack_start(make_themed_feature_icon(tid, tid, 26), False, False, 0)
            n_lbl = Gtk.Label(label=t_title)
            n_lbl.get_style_context().add_class("notif-title")
            s_lbl = Gtk.Label(label=tile_subs.get(tid, ""))
            s_lbl.get_style_context().add_class("panel-sub")
            t_box.pack_start(n_lbl, False, False, 0)
            t_box.pack_start(s_lbl, False, False, 0)
            btn.add(t_box)
            btn.connect("clicked", lambda *_, k=tid: self.on_tile_clicked(k))
            ov.add(btn)

            if is_on and tid != "apps":
                stop_b = Gtk.Button(label="x")
                stop_b.set_tooltip_text(f"Stop {t_title}")
                stop_b.get_style_context().add_class("corner-cancel-btn")
                stop_b.set_halign(Gtk.Align.END)
                stop_b.set_valign(Gtk.Align.START)
                stop_b.set_margin_top(3)
                stop_b.set_margin_end(3)
                stop_b.connect("clicked", lambda *_, k=tid: (self.app.stop_feature(k), self.refresh_tray_ui()))
                ov.add_overlay(stop_b)

            tiles_grid.attach(ov, idx % 3, idx // 3, 1, 1)
        self._body_box.pack_start(tiles_grid, False, False, 0)

        drop_btn = Gtk.Button(label="Drop files here to send to your phone")
        drop_btn.get_style_context().add_class("dropzone")
        drop_btn.connect(
            "clicked",
            lambda *_: self.app._choose_and_send_files()
            if hasattr(self.app, "_choose_and_send_files")
            else None,
        )
        with contextlib.suppress(Exception):
            targets = [
                Gtk.TargetEntry.new("text/uri-list", 0, 1),
                Gtk.TargetEntry.new("STRING", 0, 2),
                Gtk.TargetEntry.new("text/plain", 0, 3),
            ]
            drop_btn.drag_dest_set(Gtk.DestDefaults.ALL, targets, Gdk.DragAction.COPY)
            if hasattr(self.app, "_on_share_drag_data_received"):
                drop_btn.connect("drag-data-received", self.app._on_share_drag_data_received)
        self._body_box.pack_start(drop_btn, False, False, 0)

        notifs = list(getattr(self.facade, "notifications", []) or [])
        if notifs:
            latest = notifs[-1]
            n_title = str(latest.get("title") or latest.get("app") or "Notification")
            n_body = str(latest.get("text") or latest.get("body") or "")
        elif demo_mode:
            n_title, n_body = "Demo Notification", "Sample notification preview (--demo)"
        else:
            n_title, n_body = "No new notifications", "Phone notifications will appear here when connected."
        n_card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
        n_card.get_style_context().add_class("sc-card")
        n_card.pack_start(make_cairo_icon("messages", 20), False, False, 0)
        n_txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        nt_l = Gtk.Label(label=n_title, xalign=0.0)
        nt_l.get_style_context().add_class("notif-title")
        nb_l = Gtk.Label(label=n_body, xalign=0.0)
        nb_l.set_ellipsize(Pango.EllipsizeMode.END)
        nb_l.get_style_context().add_class("notif-body")
        n_txt.pack_start(nt_l, False, False, 0)
        n_txt.pack_start(nb_l, False, False, 0)
        n_card.pack_start(n_txt, True, True, 0)

        rep_btn = Gtk.Button(label="Reply")
        rep_btn.get_style_context().add_class("secondary-btn")
        rep_btn.connect("clicked", lambda *_: self.switch_tray_view("alerts"))
        n_card.pack_end(rep_btn, False, False, 0)
        self._body_box.pack_start(n_card, False, False, 0)

        is_min = bool(getattr(self.app, "is_minimized_to_tray", False))
        bottom_act_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.restore_main_btn = Gtk.Button(
            label="Restore Main Window" if is_min else "Open Samyuj"
        )
        self.restore_main_btn.get_style_context().add_class("primary-btn")
        self.restore_main_btn.connect("clicked", lambda *_: (self.hide(), self.app.restore_from_tray()))
        bottom_act_row.pack_start(self.restore_main_btn, True, True, 0)

        self.quit_tray_btn = Gtk.Button(label="Quit")
        self.quit_tray_btn.set_tooltip_text("Quit Samyuj completely")
        self.quit_tray_btn.get_style_context().add_class("danger-btn")
        self.quit_tray_btn.connect("clicked", lambda *_: (self.hide(), self.app.quit_application()))
        bottom_act_row.pack_end(self.quit_tray_btn, False, False, 0)
        self._body_box.pack_end(bottom_act_row, False, False, 0)

    def _render_desktop_setup_view(self) -> None:
        hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        back_btn = Gtk.Button(label="Back")
        back_btn.get_style_context().add_class("setup-back-btn")
        back_btn.connect("clicked", lambda *_: self.switch_tray_view("home"))
        hdr.pack_start(back_btn, False, False, 0)
        t_lbl = Gtk.Label(label="Mobile desktop", xalign=0.0)
        t_lbl.get_style_context().add_class("panel-h2")
        hdr.pack_start(t_lbl, True, True, 0)
        info_lbl = Gtk.Label(label="i")
        info_lbl.get_style_context().add_class("setup-info-btn")
        hdr.pack_end(info_lbl, False, False, 0)
        self._body_box.pack_start(hdr, False, False, 0)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        card.get_style_context().add_class("sc-card")
        self._body_box.pack_start(card, False, False, 0)

        ori_lbl = Gtk.Label(label="Window orientation", xalign=0.0)
        ori_lbl.get_style_context().add_class("notif-title")
        card.pack_start(ori_lbl, False, False, 0)

        ori_grid = Gtk.Grid()
        ori_grid.set_column_spacing(8)
        ori_grid.set_column_homogeneous(True)
        for idx, (ori_name, p_kind) in enumerate((("Landscape", "md_landscape"), ("Portrait", "md_portrait"))):
            btn = Gtk.Button()
            btn.set_relief(Gtk.ReliefStyle.NONE)
            btn.get_style_context().add_class(
                "setup-option-card-selected" if self.md_orientation == ori_name else "setup-option-card"
            )
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            box.set_halign(Gtk.Align.CENTER)
            box.pack_start(Gtk.Image.new_from_pixbuf(make_setup_preview_pixbuf(p_kind, 112, 68)), False, False, 0)
            l_lbl = Gtk.Label(label=ori_name)
            l_lbl.get_style_context().add_class("notif-title")
            box.pack_start(l_lbl, False, False, 0)
            btn.add(box)

            def _pick_ori(_b, chosen=ori_name):
                self.md_orientation = chosen
                self.md_resolution = "1080x1920" if chosen == "Portrait" else "1920x1080"
                self.refresh_tray_ui()

            btn.connect("clicked", _pick_ori)
            ori_grid.attach(btn, idx, 0, 1, 1)
        card.pack_start(ori_grid, False, False, 0)

        res_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        res_lbl = Gtk.Label(label="Window resolution", xalign=0.0)
        res_lbl.get_style_context().add_class("notif-title")
        res_row.pack_start(res_lbl, True, True, 0)

        res_combo = Gtk.ComboBoxText()
        res_list = (
            ("600x800", "720x1280", "900x1600", "1080x1920", "1440x2560")
            if self.md_orientation == "Portrait"
            else ("800x600", "1280x720", "1600x900", "1920x1080", "2560x1440")
        )
        for r_opt in res_list:
            res_combo.append(r_opt, r_opt.replace("x", " × "))
        res_combo.set_active_id(self.md_resolution if self.md_resolution in res_list else res_list[-2])
        res_combo.connect("changed", lambda cb: setattr(self, "md_resolution", cb.get_active_id() or "1920x1080"))
        res_row.pack_end(res_combo, False, False, 0)
        card.pack_start(res_row, False, False, 0)

        start_btn = Gtk.Button(label="Start Mobile desktop")
        start_btn.get_style_context().add_class("setup-start-btn")
        start_btn.set_halign(Gtk.Align.CENTER)
        start_btn.connect(
            "clicked",
            lambda *_: (
                self.app._start_mobile_desktop(self.md_resolution),
                self.switch_tray_view("home"),
            ),
        )
        self._body_box.pack_start(start_btn, False, False, 4)

    def _render_screen_hub_view(self) -> None:
        hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        back_btn = Gtk.Button(label="Back")
        back_btn.get_style_context().add_class("setup-back-btn")
        back_btn.connect("clicked", lambda *_: self.switch_tray_view("home"))
        hdr.pack_start(back_btn, False, False, 0)
        t_lbl = Gtk.Label(label="Share Screen", xalign=0.0)
        t_lbl.get_style_context().add_class("panel-h2")
        hdr.pack_start(t_lbl, True, True, 0)
        info_lbl = Gtk.Label(label="i")
        info_lbl.get_style_context().add_class("setup-info-btn")
        hdr.pack_end(info_lbl, False, False, 0)
        self._body_box.pack_start(hdr, False, False, 0)

        sub = Gtk.Label(label="Select which display you want to share:", xalign=0.0)
        sub.get_style_context().add_class("panel-sub")
        self._body_box.pack_start(sub, False, False, 0)

        for target_view, title_txt, desc_txt, p_kind in (
            ("screen_pc", "Share PC screen", "Mirror this PC's primary display onto your phone screen", "pc_mirror"),
            ("screen_phone", "Share mobile screen", "Mirror mobile display or create a virtual display on this PC", "phone_mirror"),
        ):
            btn = Gtk.Button()
            btn.set_relief(Gtk.ReliefStyle.NONE)
            btn.get_style_context().add_class("setup-option-card")
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            row.pack_start(Gtk.Image.new_from_pixbuf(make_setup_preview_pixbuf(p_kind, 96, 58)), False, False, 0)
            txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            tl = Gtk.Label(label=title_txt, xalign=0.0)
            tl.get_style_context().add_class("notif-title")
            dl = Gtk.Label(label=desc_txt, xalign=0.0)
            dl.set_line_wrap(True)
            dl.set_max_width_chars(28)
            dl.get_style_context().add_class("panel-sub")
            txt.pack_start(tl, False, False, 0)
            txt.pack_start(dl, False, False, 0)
            row.pack_start(txt, True, True, 0)
            btn.add(row)
            btn.connect("clicked", lambda *_, tv=target_view: self.switch_tray_view(tv))
            self._body_box.pack_start(btn, False, False, 0)

    def _render_screen_phone_view(self) -> None:
        hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        back_btn = Gtk.Button(label="Back")
        back_btn.get_style_context().add_class("setup-back-btn")
        back_btn.connect("clicked", lambda *_: self.switch_tray_view("screen_hub"))
        hdr.pack_start(back_btn, False, False, 0)
        t_lbl = Gtk.Label(label="Share mobile screen", xalign=0.0)
        t_lbl.get_style_context().add_class("panel-h2")
        hdr.pack_start(t_lbl, True, True, 0)
        info_lbl = Gtk.Label(label="i")
        info_lbl.get_style_context().add_class("setup-info-btn")
        hdr.pack_end(info_lbl, False, False, 0)
        self._body_box.pack_start(hdr, False, False, 0)

        for mode_id, title_txt, desc_txt, p_kind in (
            ("mirror", "Mirror mobile display", "Show your phone's display as it appears in hand", "phone_mirror"),
            ("mobile", "Create virtual display", "Show an additional virtual phone screen on your PC", "phone_virtual"),
        ):
            btn = Gtk.Button()
            btn.set_relief(Gtk.ReliefStyle.NONE)
            btn.get_style_context().add_class(
                "setup-option-card-selected" if self.phone_ss_mode == mode_id else "setup-option-card"
            )
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            row.pack_start(Gtk.Image.new_from_pixbuf(make_setup_preview_pixbuf(p_kind, 96, 58)), False, False, 0)
            txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            tl = Gtk.Label(label=title_txt, xalign=0.0)
            tl.get_style_context().add_class("notif-title")
            dl = Gtk.Label(label=desc_txt, xalign=0.0)
            dl.set_line_wrap(True)
            dl.set_max_width_chars(28)
            dl.get_style_context().add_class("panel-sub")
            txt.pack_start(tl, False, False, 0)
            txt.pack_start(dl, False, False, 0)
            row.pack_start(txt, True, True, 0)
            btn.add(row)
            btn.connect("clicked", lambda *_, m=mode_id: (setattr(self, "phone_ss_mode", m), self.refresh_tray_ui()))
            self._body_box.pack_start(btn, False, False, 0)

        glance_card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        glance_card.get_style_context().add_class("sc-card")
        g_txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        gt = Gtk.Label(label="Phone at a glance", xalign=0.0)
        gt.get_style_context().add_class("notif-title")
        gs = Gtk.Label(label="Automatically start when your phone connects", xalign=0.0)
        gs.get_style_context().add_class("panel-sub")
        g_txt.pack_start(gt, False, False, 0)
        g_txt.pack_start(gs, False, False, 0)
        glance_card.pack_start(g_txt, True, True, 0)
        g_sw = Gtk.Switch()
        g_sw.set_active(bool(self.facade.user_settings.get("phone_at_a_glance", False)))
        g_sw.set_valign(Gtk.Align.CENTER)
        g_sw.connect("notify::active", lambda w, _p: self.app._on_toggle_phone_at_a_glance(bool(w.get_active())))
        glance_card.pack_end(g_sw, False, False, 0)
        self._body_box.pack_start(glance_card, False, False, 0)

        s_btn = Gtk.Button(label="Start screen share")
        s_btn.get_style_context().add_class("setup-start-btn")
        s_btn.set_halign(Gtk.Align.CENTER)
        s_btn.connect(
            "clicked",
            lambda *_: (
                self.app._start_phone_on_pc(self.phone_ss_mode),
                self.switch_tray_view("home"),
            ),
        )
        self._body_box.pack_start(s_btn, False, False, 4)

    def _render_screen_pc_view(self) -> None:
        hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        back_btn = Gtk.Button(label="Back")
        back_btn.get_style_context().add_class("setup-back-btn")
        back_btn.connect("clicked", lambda *_: self.switch_tray_view("screen_hub"))
        hdr.pack_start(back_btn, False, False, 0)
        t_lbl = Gtk.Label(label="Share PC screen", xalign=0.0)
        t_lbl.get_style_context().add_class("panel-h2")
        hdr.pack_start(t_lbl, True, True, 0)
        info_lbl = Gtk.Label(label="i")
        info_lbl.get_style_context().add_class("setup-info-btn")
        hdr.pack_end(info_lbl, False, False, 0)
        self._body_box.pack_start(hdr, False, False, 0)

        card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        card.get_style_context().add_class("setup-option-card-selected")
        card.pack_start(Gtk.Image.new_from_pixbuf(make_setup_preview_pixbuf("pc_mirror", 96, 58)), False, False, 0)
        txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        tl = Gtk.Label(label="Mirror PC display", xalign=0.0)
        tl.get_style_context().add_class("notif-title")
        dl = Gtk.Label(label="Show your primary PC monitor on your phone's screen at up to 60 fps", xalign=0.0)
        dl.set_line_wrap(True)
        dl.set_max_width_chars(28)
        dl.get_style_context().add_class("panel-sub")
        txt.pack_start(tl, False, False, 0)
        txt.pack_start(dl, False, False, 0)
        card.pack_start(txt, True, True, 0)
        self._body_box.pack_start(card, False, False, 0)

        s_btn = Gtk.Button(label="Start PC screen share")
        s_btn.get_style_context().add_class("setup-start-btn")
        s_btn.set_halign(Gtk.Align.CENTER)
        s_btn.connect(
            "clicked",
            lambda *_: (
                self.app._start_screenshare("mirror"),
                self.switch_tray_view("home"),
            ),
        )
        self._body_box.pack_start(s_btn, False, False, 4)

    def _render_files_view(self) -> None:
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        h_lbl = Gtk.Label(label="Files", xalign=0.0)
        h_lbl.get_style_context().add_class("panel-h2")
        top.pack_start(h_lbl, True, True, 0)
        top.pack_end(
            self._build_segmented_control(
                ("On phone", "Shared PC"),
                "On phone" if self.files_tab == "phone" else "Shared PC",
                lambda val: (setattr(self, "files_tab", "phone" if val == "On phone" else "pc"), self.refresh_tray_ui()),
            ),
            False, False, 0,
        )
        self._body_box.pack_start(top, False, False, 0)

        demo_mode = bool((getattr(self.facade, "user_settings", {}) or {}).get("demo_mode", False))
        if self.files_tab == "phone":
            crumb_dev = (self.facade._best_device_name() if hasattr(self.facade, "_best_device_name") else "") or "Phone"
            crumb = Gtk.Label(label=f"{crumb_dev} › DCIM › Camera", xalign=0.0)
            crumb.get_style_context().add_class("panel-sub")
            self._body_box.pack_start(crumb, False, False, 0)

            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            card.get_style_context().add_class("sc-card")
            live_entries = list((getattr(self.facade, "gallery_state", {}) or {}).get("entries") or [])
            if live_entries:
                file_rows = [
                    (str(e.get("name") or "Item"), "Folder" if e.get("is_dir") else f"{max(1, int(e.get('size') or 0)) / (1024 * 1024):.1f} MB")
                    for e in live_entries[:6]
                ]
            elif demo_mode:
                file_rows = [("Sample_Folder", "Folder"), ("Sample_Photo_01.jpg", "2.4 MB")]
            else:
                file_rows = [("No cached phone files", "Mount phone storage to browse")]
            for fname, fmeta in file_rows:
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                row.pack_start(make_cairo_icon("files", 16), False, False, 0)
                nl = Gtk.Label(label=fname, xalign=0.0)
                nl.get_style_context().add_class("notif-title")
                row.pack_start(nl, True, True, 0)
                ml = Gtk.Label(label=fmeta, xalign=1.0)
                ml.get_style_context().add_class("panel-sub")
                row.pack_end(ml, False, False, 0)
                card.pack_start(row, False, False, 0)
            self._body_box.pack_start(card, False, False, 0)

            mnt_lbl = Gtk.Label(xalign=0.0)
            mnt_lbl.set_markup('<span foreground="#10b981" weight="bold">Mounted:</span> Phone storage available in your file manager sidebar')
            mnt_lbl.get_style_context().add_class("panel-sub")
            self._body_box.pack_start(mnt_lbl, False, False, 0)

            act_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            send_btn = Gtk.Button(label="Send files")
            send_btn.get_style_context().add_class("primary-btn")
            send_btn.connect("clicked", lambda *_: self.app._on_send_files_dialog() if hasattr(self.app, "_on_send_files_dialog") else None)
            act_row.pack_start(send_btn, True, True, 0)

            fm_btn = Gtk.Button(label="Open in file manager")
            fm_btn.get_style_context().add_class("secondary-btn")
            fm_btn.connect(
                "clicked",
                lambda *_: subprocess.Popen(
                    ["xdg-open", str(self.facade.get_incoming_files_dir())],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                ),
            )
            act_row.pack_start(fm_btn, True, True, 0)
            self._body_box.pack_start(act_row, False, False, 2)
        else:
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            card.get_style_context().add_class("sc-card")
            live_shares = []
            if hasattr(self.facade, "nfs_list_shares"):
                with contextlib.suppress(Exception):
                    live_shares = list((self.facade.nfs_list_shares() or {}).get("shares") or [])
            if live_shares:
                share_rows = [
                    (str(s.get("alias") or "Share"), f"{s.get('host_path', '')} · {'read & write' if s.get('access_mode') == 'rw' else 'read only'}")
                    for s in live_shares
                ]
            else:
                share_rows = [
                    ("Pictures", f"{Path.home() / 'Pictures'} · read only"),
                    ("Documents", f"{Path.home() / 'Documents'} · read only"),
                ]
            for sname, spath in share_rows:
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                row.pack_start(make_cairo_icon("files", 16), False, False, 0)
                info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
                nl = Gtk.Label(label=sname, xalign=0.0)
                nl.get_style_context().add_class("notif-title")
                pl = Gtk.Label(label=spath, xalign=0.0)
                pl.get_style_context().add_class("panel-sub")
                info.pack_start(nl, False, False, 0)
                info.pack_start(pl, False, False, 0)
                row.pack_start(info, True, True, 0)
                stop_b = Gtk.Button(label="Stop")
                stop_b.get_style_context().add_class("danger-btn")
                row.pack_end(stop_b, False, False, 0)
                card.pack_start(row, False, False, 0)
            self._body_box.pack_start(card, False, False, 0)

    def _render_alerts_view(self) -> None:
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        h_lbl = Gtk.Label(label="Notifications", xalign=0.0)
        h_lbl.get_style_context().add_class("panel-h2")
        top.pack_start(h_lbl, True, True, 0)
        clr_btn = Gtk.Button(label="Clear all")
        clr_btn.get_style_context().add_class("clear-btn")
        clr_btn.connect("clicked", lambda *_: (self.app._on_clear_all_notifications(), self.refresh_tray_ui()))
        top.pack_end(clr_btn, False, False, 0)
        self._body_box.pack_start(top, False, False, 0)

        demo_mode = bool((getattr(self.facade, "user_settings", {}) or {}).get("demo_mode", False))
        notifs = list(getattr(self.facade, "notifications", []) or [])
        items = (
            [(str(n.get("title") or n.get("app") or "Alert"), str(n.get("text") or n.get("body") or ""), "now") for n in notifs]
            if notifs
            else (
                [("Demo Alert", "Sample notification item (--demo)", "now")]
                if demo_mode
                else []
            )
        )
        if not items:
            emp = Gtk.Label(label="No phone notifications.", xalign=0.0)
            emp.get_style_context().add_class("panel-sub")
            self._body_box.pack_start(emp, False, False, 8)
        for idx, (nt, nb, ntime) in enumerate(items):
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
            card.get_style_context().add_class("sc-card")
            h_r = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            tl = Gtk.Label(label=f"{nt} · {ntime}", xalign=0.0)
            tl.get_style_context().add_class("notif-title")
            h_r.pack_start(tl, True, True, 0)
            dis_b = Gtk.Button(label="x")
            dis_b.get_style_context().add_class("secondary-btn")
            h_r.pack_end(dis_b, False, False, 0)
            card.pack_start(h_r, False, False, 0)

            bl = Gtk.Label(label=nb, xalign=0.0)
            bl.get_style_context().add_class("notif-body")
            card.pack_start(bl, False, False, 0)
            if idx == 0:
                rep_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                rep = Gtk.Entry()
                rep.get_style_context().add_class("sc-entry")
                rep.set_placeholder_text("Reply…")
                rep_row.pack_start(rep, True, True, 0)
                send_b = Gtk.Button(label="Send")
                send_b.get_style_context().add_class("primary-btn")
                rep_row.pack_end(send_b, False, False, 0)
                card.pack_start(rep_row, False, False, 0)
            self._body_box.pack_start(card, False, False, 0)

    def _render_settings_view(self) -> None:
        h_lbl = Gtk.Label(label="Settings", xalign=0.0)
        h_lbl.get_style_context().add_class("panel-h2")
        self._body_box.pack_start(h_lbl, False, False, 0)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.get_style_context().add_class("sc-card")

        toggles = (
            ("Connect over USB", "Prefer a cable when plugged in", bool(self.facade.user_settings.get("connect_usb", True)),
             lambda act: self.facade.save_user_settings(connect_usb=act)),
            ("Connect over Wi-Fi", "Find your phone on the network", bool(self.facade.user_settings.get("connect_wifi", True)),
             lambda act: self.facade.save_user_settings(connect_wifi=act)),
            ("Minimize to tray & background", "Keep tray active when main window is minimized or closed", bool(getattr(self.app, "minimize_to_tray_enabled", True)),
             lambda act: self.app.set_minimize_to_tray_enabled(act)),
            ("Sync phone wallpaper", "Pull it in on connect", bool(self.facade.user_settings.get("wallpaper_sync", True)),
             lambda act: self.facade.save_user_settings(wallpaper_sync=act)),
            ("Show phone in file manager", "Mount phone storage & bookmark it in your file manager (indexer-safe hidden path)", bool(self.facade.user_settings.get("auto_mount_phone_nfs", True)),
             lambda act: self.facade.save_user_settings(auto_mount_phone_nfs=act)),
        )
        for t_title, t_sub, init_val, cb_fn in toggles:
            r = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            tx = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            tl = Gtk.Label(label=t_title, xalign=0.0)
            tl.get_style_context().add_class("notif-title")
            sl = Gtk.Label(label=t_sub, xalign=0.0)
            sl.get_style_context().add_class("panel-sub")
            tx.pack_start(tl, False, False, 0)
            tx.pack_start(sl, False, False, 0)
            r.pack_start(tx, True, True, 0)
            sw = Gtk.Switch()
            sw.set_active(init_val)
            sw.set_valign(Gtk.Align.CENTER)
            sw.connect("notify::active", lambda w, _p, fn=cb_fn: fn(bool(w.get_active())))
            r.pack_end(sw, False, False, 0)
            card.pack_start(r, False, False, 0)
        self._body_box.pack_start(card, False, False, 0)

        edge_lbl = Gtk.Label(label="Cross Control edge", xalign=0.0)
        edge_lbl.get_style_context().add_class("notif-title")
        self._body_box.pack_start(edge_lbl, False, False, 0)

        cur_edge = str(self.facade.user_settings.get("cross_control_edge") or "left").capitalize()

        def _pick_edge(val: str) -> None:
            eid = val.lower()
            self.facade.save_user_settings(cross_control_edge=eid)
            self.app.edge_controller.edge = eid
            self.refresh_tray_ui()

        self._body_box.pack_start(
            self._build_segmented_control(("Left", "Right", "Top", "Bottom"), cur_edge, _pick_edge),
            False, False, 0,
        )

_SNI_NODE_XML = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <method name="Activate">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="SecondaryActivate">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="ContextMenu">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <signal name="NewTitle"/>
    <signal name="NewIcon"/>
    <signal name="NewStatus">
      <arg name="status" type="s"/>
    </signal>
  </interface>
</node>
"""

_DBUSMENU_NODE_XML = """
<node>
  <interface name="com.canonical.dbusmenu">
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <method name="GetLayout">
      <arg type="i" name="parentId" direction="in"/>
      <arg type="i" name="recursionDepth" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="u" name="revision" direction="out"/>
      <arg type="(ia{sv}av)" name="layout" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg type="ai" name="ids" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="a(ia{sv})" name="properties" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg type="i" name="id" direction="in"/>
      <arg type="s" name="name" direction="in"/>
      <arg type="v" name="value" direction="out"/>
    </method>
    <method name="Event">
      <arg type="i" name="id" direction="in"/>
      <arg type="s" name="eventId" direction="in"/>
      <arg type="v" name="data" direction="in"/>
      <arg type="u" name="timestamp" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg type="a(isvu)" name="events" direction="in"/>
      <arg type="ai" name="idErrors" direction="out"/>
    </method>
    <method name="AboutToShow">
      <arg type="i" name="id" direction="in"/>
      <arg type="b" name="needUpdate" direction="out"/>
    </method>
    <method name="AboutToShowGroup">
      <arg type="ai" name="ids" direction="in"/>
      <arg type="ai" name="updatesNeeded" direction="out"/>
      <arg type="ai" name="idErrors" direction="out"/>
    </method>
    <signal name="ItemsPropertiesUpdated">
      <arg type="a(ia{sv})" name="updatedProps"/>
      <arg type="a(ias)" name="removedProps"/>
    </signal>
    <signal name="LayoutUpdated">
      <arg type="u" name="revision"/>
      <arg type="i" name="parent"/>
    </signal>
  </interface>
</node>
"""

class SystemTrayController:

    def __init__(self, app) -> None:
        self.app = app
        self.facade = app.facade
        self.backend_name: str = "none"
        self._sni_bus: Gio.DBusConnection | None = None
        self._sni_reg_id: int = 0
        self._sni_menu_reg_id: int = 0
        self._sni_bus_name_id: int = 0
        self._indicator = None
        self._status_icon = None
        self.menu = self._build_context_menu()
        self._init_system_indicator()

    def _build_context_menu(self) -> Gtk.Menu:
        menu = Gtk.Menu()
        menu.get_style_context().add_class("context-menu")

        item_open = Gtk.MenuItem(label="Open Samyuj")
        item_open.connect("activate", lambda *_: self.app.restore_from_tray())
        menu.append(item_open)

        item_panel = Gtk.MenuItem(label="Quick Tray Panel")
        item_panel.connect("activate", lambda *_: self.app.toggle_tray_popover())
        menu.append(item_panel)

        item_apps = Gtk.MenuItem(label="Phone Apps")
        item_apps.connect(
            "activate",
            lambda *_: (self.app.restore_from_tray(), self.app.switch_page("apps")),
        )
        menu.append(item_apps)

        menu.append(Gtk.SeparatorMenuItem())

        item_quit = Gtk.MenuItem(label="Quit Samyuj")
        item_quit.connect("activate", lambda *_: self.app.quit_application())
        menu.append(item_quit)

        menu.show_all()
        return menu

    def _trigger_menu_action(self, item_id: int) -> None:
        if item_id == 1:
            self.app.restore_from_tray()
        elif item_id == 2:
            self.app.toggle_tray_popover()
        elif item_id == 3:
            self.app.restore_from_tray()
            self.app.switch_page("apps")
        elif item_id == 5:
            self.app.quit_application()

    @staticmethod
    def _build_dbusmenu_layout() -> GLib.Variant:
        def _item(item_id: int, props: dict[str, GLib.Variant]) -> GLib.Variant:
            return GLib.Variant("(ia{sv}av)", (item_id, props, []))

        children = [
            _item(1, {
                "label": GLib.Variant("s", "Open Samyuj"),
                "enabled": GLib.Variant("b", True),
                "visible": GLib.Variant("b", True),
            }),
            _item(2, {
                "label": GLib.Variant("s", "Quick Tray Panel"),
                "enabled": GLib.Variant("b", True),
                "visible": GLib.Variant("b", True),
            }),
            _item(3, {
                "label": GLib.Variant("s", "Phone Apps"),
                "enabled": GLib.Variant("b", True),
                "visible": GLib.Variant("b", True),
            }),
            _item(4, {
                "type": GLib.Variant("s", "separator"),
                "visible": GLib.Variant("b", True),
            }),
            _item(5, {
                "label": GLib.Variant("s", "Quit Samyuj"),
                "icon-name": GLib.Variant("s", "application-exit"),
                "enabled": GLib.Variant("b", True),
                "visible": GLib.Variant("b", True),
            }),
        ]
        root_props = {"children-display": GLib.Variant("s", "submenu")}
        return GLib.Variant("(u(ia{sv}av))", (1, (0, root_props, children)))

    def _popup_context_menu_fallback(self, x: int | None = None, y: int | None = None) -> None:
        with contextlib.suppress(Exception):
            if hasattr(self.menu, "popup"):
                self.menu.popup(None, None, None, None, 3, Gtk.get_current_event_time())
                return
        with contextlib.suppress(Exception):
            pop = self.app.toggle_tray_popover()
            if pop is not None and pop.get_visible():
                pop.position_near_tray(x, y)

    def _init_dbus_sni(self) -> bool:
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            if bus is None:
                return False
            res = bus.call_sync(
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus",
                "NameHasOwner",
                GLib.Variant("(s)", ("org.kde.StatusNotifierWatcher",)),
                GLib.VariantType("(b)"),
                Gio.DBusCallFlags.NONE,
                350,
                None,
            )
            if not res or not res.unpack()[0]:
                return False

            node_info = Gio.DBusNodeInfo.new_for_xml(_SNI_NODE_XML)
            iface_info = node_info.lookup_interface("org.kde.StatusNotifierItem")
            menu_node_info = Gio.DBusNodeInfo.new_for_xml(_DBUSMENU_NODE_XML)
            menu_iface_info = menu_node_info.lookup_interface("com.canonical.dbusmenu")

            def _on_menu_method_call(_conn, _sender, _path, _iface, method_name, params, invocation):
                if method_name == "GetLayout":
                    invocation.return_value(self._build_dbusmenu_layout())
                elif method_name == "GetGroupProperties":
                    invocation.return_value(GLib.Variant("(a(ia{sv}))", ([],)))
                elif method_name == "GetProperty":
                    invocation.return_value(GLib.Variant("(v)", (GLib.Variant("s", ""),)))
                elif method_name == "Event":
                    with contextlib.suppress(Exception):
                        item_id, event_id, _data, _ts = params.unpack()
                        if event_id in ("clicked", "activate"):
                            GLib.idle_add(lambda iid=int(item_id): (self._trigger_menu_action(iid), False)[1])
                    invocation.return_value(None)
                elif method_name == "EventGroup":
                    with contextlib.suppress(Exception):
                        events = params.unpack()[0]
                        for item_id, event_id, _data, _ts in events:
                            if event_id in ("clicked", "activate"):
                                GLib.idle_add(lambda iid=int(item_id): (self._trigger_menu_action(iid), False)[1])
                    invocation.return_value(GLib.Variant("(ai)", ([],)))
                elif method_name == "AboutToShow":
                    invocation.return_value(GLib.Variant("(b)", (False,)))
                elif method_name == "AboutToShowGroup":
                    invocation.return_value(GLib.Variant("(aiai)", ([], [])))
                else:
                    invocation.return_value(None)

            def _on_menu_get_prop(_conn, _sender, _path, _iface, prop_name):
                if prop_name == "Version":
                    return GLib.Variant("u", 3)
                if prop_name == "TextDirection":
                    return GLib.Variant("s", "ltr")
                if prop_name == "Status":
                    return GLib.Variant("s", "normal")
                if prop_name == "IconThemePath":
                    return GLib.Variant("as", [])
                return None

            menu_reg_id = bus.register_object(
                "/MenuBar",
                menu_iface_info,
                _on_menu_method_call,
                _on_menu_get_prop,
                None,
            )

            def _on_method_call(_conn, _sender, _path, _iface, method_name, params, invocation):
                if method_name in ("Activate", "SecondaryActivate"):
                    GLib.idle_add(lambda: (self._on_tray_icon_left_clicked(), False)[1])
                    invocation.return_value(None)
                elif method_name == "ContextMenu":
                    cx, cy = None, None
                    with contextlib.suppress(Exception):
                        cx, cy = params.unpack()
                    GLib.idle_add(lambda: (self._popup_context_menu_fallback(cx, cy), False)[1])
                    invocation.return_value(None)
                else:
                    invocation.return_value(None)

            def _on_get_prop(_conn, _sender, _path, _iface, prop_name):
                if prop_name == "Category":
                    return GLib.Variant("s", "ApplicationStatus")
                if prop_name == "Id":
                    return GLib.Variant("s", "samyuj")
                if prop_name == "Title":
                    return GLib.Variant("s", "Samyuj")
                if prop_name == "Status":
                    return GLib.Variant("s", "Active")
                if prop_name == "IconName":
                    return GLib.Variant("s", "phone")
                if prop_name == "Menu":
                    return GLib.Variant("o", "/MenuBar")
                if prop_name == "ItemIsMenu":
                    return GLib.Variant("b", False)
                if prop_name == "ToolTip":
                    return GLib.Variant(
                        "(sa(iiay)ss)",
                        ("phone", [], "Samyuj", "Left-click to open · Right-click for menu / Quit"),
                    )
                return None

            reg_id = bus.register_object(
                "/StatusNotifierItem",
                iface_info,
                _on_method_call,
                _on_get_prop,
                None,
            )
            if not reg_id:
                if menu_reg_id:
                    with contextlib.suppress(Exception):
                        bus.unregister_object(menu_reg_id)
                return False

            bus_name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"

            def _on_name_acquired(conn, name):
                with contextlib.suppress(Exception):
                    conn.call_sync(
                        "org.kde.StatusNotifierWatcher",
                        "/StatusNotifierWatcher",
                        "org.kde.StatusNotifierWatcher",
                        "RegisterStatusNotifierItem",
                        GLib.Variant("(s)", (name,)),
                        None,
                        Gio.DBusCallFlags.NONE,
                        500,
                        None,
                    )

            self._sni_bus = bus
            self._sni_reg_id = reg_id
            self._sni_menu_reg_id = menu_reg_id
            self._sni_bus_name_id = Gio.bus_own_name_on_connection(
                bus,
                bus_name,
                Gio.BusNameOwnerFlags.NONE,
                _on_name_acquired,
                None,
            )
            self.backend_name = "DBus.StatusNotifierItem"
            return True
        except Exception:
            return False

    def shutdown(self) -> None:
        if self._sni_bus is not None:
            for rid in (self._sni_reg_id, self._sni_menu_reg_id):
                if rid:
                    with contextlib.suppress(Exception):
                        self._sni_bus.unregister_object(rid)
            self._sni_reg_id = 0
            self._sni_menu_reg_id = 0
        if self._sni_bus_name_id:
            with contextlib.suppress(Exception):
                Gio.bus_unown_name(self._sni_bus_name_id)
            self._sni_bus_name_id = 0
        if self._status_icon is not None:
            with contextlib.suppress(Exception):
                self._status_icon.set_visible(False)
            self._status_icon = None

    def _init_system_indicator(self) -> None:
        if self._init_dbus_sni():
            return

        with contextlib.suppress(Exception):
            status_icon = Gtk.StatusIcon()
            status_icon.set_from_icon_name("phone")
            status_icon.set_tooltip_text("Samyuj — Click to open main window")
            status_icon.set_visible(True)
            status_icon.connect("activate", lambda *_: self._on_tray_icon_left_clicked())
            status_icon.connect(
                "popup-menu",
                lambda icon, button, activate_time: self.menu.popup(
                    None, None, Gtk.StatusIcon.position_menu, icon, button, activate_time
                ),
            )
            self._status_icon = status_icon
            self.backend_name = "Gtk.StatusIcon"
            return

        for ns in ("AyatanaAppIndicator3", "AppIndicator3"):
            with contextlib.suppress(Exception):
                import gi
                gi.require_version(ns, "0.1")
                mod = __import__(f"gi.repository.{ns}", fromlist=[ns])
                ind = mod.Indicator.new(
                    "samyuj-tray",
                    "phone",
                    mod.IndicatorCategory.APPLICATION_STATUS,
                )
                ind.set_status(mod.IndicatorStatus.ACTIVE)
                ind.set_title("Samyuj")
                ind.set_menu(self.menu)
                self._indicator = ind
                self.backend_name = ns
                return

    def _on_tray_icon_left_clicked(self) -> None:
        if getattr(self.app, "is_minimized_to_tray", False) or not self.app.win.get_visible():
            self.app.restore_from_tray()
        else:
            self.app.restore_from_tray()

__all__ = [
    "AppLauncherGtkWindow",
    "DEFAULT_DEMO_APPS",
    "SystemTrayController",
    "TrayPopoverGtkWindow",
]
