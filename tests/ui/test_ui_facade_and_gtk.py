from __future__ import annotations

import tempfile
import unittest

from samyuj.session.lifecycle import SamyujRuntime
from samyuj.ui.facade import ApplicationFacade
from samyuj.ui.models import (
    ConnectionSummary,
    DashboardWidgetModel,
    FeatureWindowModel,
    SupportReportModel,
)

class UiFacadeAndModelsTest(unittest.TestCase):
    def test_immutable_view_models(self) -> None:
        cs = ConnectionSummary(connected=True, paired=True, transport="usb", phone_name="Test Phone B")
        self.assertTrue(cs.connected)
        self.assertEqual(cs.to_dict()["transport"], "usb")

        fw = FeatureWindowModel(window_id="w1", feature_type="desktop", title="Mobile Desktop")
        self.assertEqual(fw.to_dict()["feature_type"], "desktop")

        dw = DashboardWidgetModel(widget_id="g1", source_id="gallery", title="Gallery")
        self.assertEqual(dw.to_dict()["source_id"], "gallery")

        sr = SupportReportModel(generated_utc="2026-10-22T00:00:00Z", ok=True, verdict="healthy")
        self.assertTrue(sr.to_dict()["ok"])

    def test_application_facade_queries_and_settings(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sc-ui-test-") as td:
            rt = SamyujRuntime(host="127.0.0.1", port=0, out_dir=td, state_dir=td, media=False)
            facade = ApplicationFacade(rt)

            catalog = facade.feature_catalog()
            self.assertEqual(len(catalog), 12)
            self.assertIsNone(facade.get_feature_spec("audio"))
            self.assertIsNotNone(facade.get_feature_spec("desktop"))

            summary = facade.get_connection_summary()
            self.assertFalse(summary.connected)
            self.assertEqual(summary.transport, "standby")

            self.assertEqual(facade.set_remb_floor(4500), 4_500_000)
            new_uv = facade.set_uv_swap(False)
            self.assertFalse(new_uv)
            self.assertFalse(rt.uv_swap)

    def test_live_gtk_desktop_app_themes_tray_apps_and_minimize_to_tray(self) -> None:
        from samyuj.ui.gtk.app import SamyujGtkDesktopApp
        from samyuj.ui.gtk.gtk_compat import GTK_MAJOR, get_gtk_version_string
        from samyuj.ui.gtk.theme import COLOR_PALETTES, UI_THEMES, build_themed_gtk_css
        from samyuj.ui.gtk.windows.stream_window import FeatureStreamGtkWindow

        with tempfile.TemporaryDirectory(prefix="sc-gtk-live-") as td:
            rt = SamyujRuntime(host="127.0.0.1", port=0, out_dir=td, state_dir=td, media=False)
            facade = ApplicationFacade(rt)
            app = SamyujGtkDesktopApp(facade)
            try:
                self.assertIn(GTK_MAJOR, (3, 4))
                self.assertEqual(app.gtk_major_version, GTK_MAJOR)
                self.assertTrue(bool(get_gtk_version_string()))

                app._refresh_all()
                app._pump_video()
                app.tick()
                for page_name in ("home", "apps", "gallery", "messages", "files", "sharing", "settings", "notifications"):
                    app.switch_page(page_name)

                for tid, _tname, _tdesc in UI_THEMES:
                    app.set_ui_theme(tid, persist=False)
                    self.assertEqual(app.ui_theme, tid)
                for pid in COLOR_PALETTES:
                    app.set_color_palette(pid, persist=False)
                    self.assertEqual(app.color_palette, pid)
                    css_dark = build_themed_gtk_css(app.ui_theme, pid, True)
                    css_light = build_themed_gtk_css(app.ui_theme, pid, False)
                    self.assertIn(b".tray-popover-window", css_dark)
                    self.assertIn(b".app-launcher-window", css_light)

                app.set_dark_mode(False, persist=False)
                app.set_dark_mode(True, persist=False)

                app.set_minimize_to_tray_enabled(True, persist=False)
                self.assertFalse(app.is_minimized_to_tray)
                handled = app._on_main_window_delete_event(app.win, None)
                self.assertTrue(handled)
                self.assertTrue(app.is_minimized_to_tray)
                self.assertTrue(app.win.get_skip_taskbar_hint())
                self.assertTrue(app.win.get_skip_pager_hint())

                dbus_layout = app.tray_controller._build_dbusmenu_layout().unpack()
                self.assertEqual(dbus_layout[0], 1)
                menu_items = dbus_layout[1][2]
                labels = [it[1].get("label") for it in menu_items if "label" in it[1]]
                self.assertIn("Quit Samyuj", labels)
                self.assertIsNotNone(getattr(app.tray_popover, "quit_tray_btn", None))
                app.tray_controller._on_tray_icon_left_clicked()
                self.assertFalse(app.is_minimized_to_tray)
                self.assertFalse(app.win.get_skip_taskbar_hint())
                self.assertFalse(app.win.get_skip_pager_hint())

                launcher = app.open_app_launcher_window()
                self.assertEqual(launcher.view_mode, "menu")
                app.switch_page("apps")
                self.assertEqual(app.apps_tab_view_mode, "menu")

                launcher.launch_app({"name": "Messages", "glyph": "M", "color": "#0284c7", "pkg": "com.messages"})
                self.assertEqual(len(launcher.get_running_apps()), 1)

                self.assertEqual(app.tray_popover.get_non_app_running_sessions(), [])

                app.switch_page("apps")
                self.assertEqual(app.apps_tab_view_mode, "running")
                self.assertEqual(app.apps_tab_title_lbl.get_text(), "Running Apps (1)")
                app._on_apps_tab_toggle_mode_clicked()
                self.assertEqual(app.apps_tab_view_mode, "menu")
                self.assertEqual(app.apps_tab_title_lbl.get_text(), "Phone Apps")

                app.open_app_launcher_window()
                self.assertEqual(launcher.view_mode, "running")
                launcher._on_toggle_view_mode_clicked()
                self.assertEqual(launcher.view_mode, "menu")
                launcher.stop_all_apps()
                self.assertEqual(len(launcher.get_running_apps()), 0)
                self.assertEqual(app.apps_tab_view_mode, "menu")

                app.set_window_size(1040, 680, persist=True)
                self.assertEqual(app.get_remembered_window_size(), (1040, 680))
                self.assertEqual(int(facade.user_settings.get("window_width")), 1040)
                self.assertEqual(int(facade.user_settings.get("window_height")), 680)

                app.tray_popover.on_tile_clicked("desktop")
                self.assertEqual(app.tray_popover.current_view, "desktop_setup")
                app.tray_popover.on_tile_clicked("screenshare")
                self.assertEqual(app.tray_popover.current_view, "screen_hub")
                app.tray_popover.switch_tray_view("screen_phone")
                self.assertEqual(app.tray_popover.current_view, "screen_phone")
                app.tray_popover.switch_tray_view("home")

                sw = FeatureStreamGtkWindow(app, "desktop", "Mobile Desktop")
                try:
                    sw.sync_orientation_and_controls()
                finally:
                    sw.destroy()
            finally:
                app.quit_application()

            rt2 = SamyujRuntime(host="127.0.0.1", port=0, out_dir=td, state_dir=td, media=False)
            facade2 = ApplicationFacade(rt2)
            app2 = SamyujGtkDesktopApp(facade2)
            try:
                self.assertEqual(app2.get_remembered_window_size(), (1040, 680))
            finally:
                app2.quit_application()
