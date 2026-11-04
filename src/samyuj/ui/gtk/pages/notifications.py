from __future__ import annotations

from samyuj.protocol.constants import PHONE_EXTEND_LANDSCAPE_PRESETS, PHONE_EXTEND_PORTRAIT_PRESETS
from samyuj.ui.gtk.gtk_compat import Gdk, GdkPixbuf, GLib, Gtk, Pango

from samyuj.ui.gtk.assets import (
    open_dialog_nonblocking,
)

class NotificationsPageMixin:

    def _build_notifications_tab_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.tab_notif_list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.pack_start(self.tab_notif_list_box, True, True, 0)
        return box

    MD_LANDSCAPE_RES_OPTIONS = (
        ("800x600", "800 x 600"),
        ("1280x720", "1280 x 720"),
        ("1600x900", "1600 x 900"),
        ("1920x1080", "1920 x 1080"),
        ("2560x1440", "2560 x 1440"),
    )
    MD_PORTRAIT_RES_OPTIONS = (
        ("600x800", "600 x 800"),
        ("720x1280", "720 x 1280"),
        ("900x1600", "900 x 1600"),
        ("1080x1920", "1080 x 1920"),
        ("1440x2560", "1440 x 2560"),
    )
    PC_EXTEND_LANDSCAPE_OPTIONS = PHONE_EXTEND_LANDSCAPE_PRESETS
    PC_EXTEND_PORTRAIT_OPTIONS = PHONE_EXTEND_PORTRAIT_PRESETS

    def _on_clear_all_notifications(self, *_args) -> None:
        self._spawn(self.facade.clear_all_notifications())
        self._refresh_notifications(force=True)

    def _on_notification_updated(self, _item) -> bool:
        self._refresh_notifications(force=True)
        return False

    def _on_wallpaper_updated(self, wp: dict | None) -> bool:
        if wp and wp.get("saved"):
            self.handset_preview.set_wallpaper_from_file(wp["saved"])
        return False

    def _refresh_notifications(self, force: bool = False) -> None:
        items = list(getattr(self.facade, "notifications_list", []) or [])
        sig = tuple((x.get("key"), x.get("title"), x.get("text")) for x in items[-12:])
        if not force and sig == self._last_notif_sig:
            return
        self._last_notif_sig = sig

        for container in (self.home_notif_list_box, getattr(self, "tab_notif_list_box", None)):
            if container is None:
                continue
            for ch in list(container.get_children()):
                container.remove(ch)

            if not items:
                emp = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
                emp.set_valign(Gtk.Align.CENTER)
                emp.set_halign(Gtk.Align.CENTER)
                emp.set_margin_top(36)
                emp.set_margin_bottom(36)
                chk = Gtk.Label(label="OK")
                chk.get_style_context().add_class("conn-pill-connected")
                t1 = Gtk.Label(label="You're all caught up")
                t1.get_style_context().add_class("notif-title")
                t2 = Gtk.Label(label="New phone notifications will appear here.")
                t2.get_style_context().add_class("panel-sub")
                emp.pack_start(chk, False, False, 0)
                emp.pack_start(t1, False, False, 0)
                emp.pack_start(t2, False, False, 0)
                container.pack_start(emp, True, True, 0)
            else:
                for it in reversed(items[-8:]):
                    n_key = str(it.get("key") or "")
                    app_nm = str(it.get("app_name") or it.get("title") or "Phone")
                    title = str(it.get("title") or app_nm)
                    body = str(it.get("text") or "")
                    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
                    row.get_style_context().add_class("notif-row")

                    ico_lbl = Gtk.Label(label=(app_nm[:1].upper() if app_nm else "N"))
                    ico_lbl.get_style_context().add_class("tray-icon-badge")
                    row.pack_start(ico_lbl, False, False, 4)

                    copy = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                    head_txt = f"{app_nm} · {title}" if (app_nm and title and app_nm.lower() != title.lower()) else title
                    tl = Gtk.Label(label=head_txt, xalign=0.0)
                    tl.get_style_context().add_class("notif-title")
                    tl.set_ellipsize(Pango.EllipsizeMode.END)
                    bl = Gtk.Label(label=body, xalign=0.0)
                    bl.get_style_context().add_class("notif-body")
                    bl.set_ellipsize(Pango.EllipsizeMode.END)
                    copy.pack_start(tl, False, False, 0)
                    copy.pack_start(bl, False, False, 0)
                    row.pack_start(copy, True, True, 0)

                    if n_key:
                        d_btn = Gtk.Button(label="x")
                        d_btn.set_tooltip_text("Dismiss notification")
                        d_btn.get_style_context().add_class("corner-cancel-btn")
                        d_btn.set_valign(Gtk.Align.CENTER)
                        d_btn.connect(
                            "clicked",
                            lambda *_, k=n_key: (
                                self._spawn(self.facade.request(f"ntf-dismiss:{k}")),
                                setattr(self.facade, "notifications_list", [x for x in self.facade.notifications_list if x.get("key") != k]),
                                self._refresh_notifications(True),
                            ),
                        )
                        row.pack_end(d_btn, False, False, 0)
                        if any(a.get("remote_input") for a in (it.get("actions") or [])):
                            r_btn = Gtk.Button(label="Reply")
                            r_btn.get_style_context().add_class("clear-btn")
                            r_btn.connect("clicked", lambda *_, k=n_key: self._prompt_notif_reply(k))
                            row.pack_end(r_btn, False, False, 2)
                    container.pack_start(row, False, False, 0)
            container.show_all()

    def _prompt_notif_reply(self, key: str) -> None:
        dlg = Gtk.Dialog(title="Reply to Notification", transient_for=self.win, flags=0)
        dlg.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, "Send Reply", Gtk.ResponseType.OK)
        box = dlg.get_content_area()
        entry = Gtk.Entry()
        entry.set_placeholder_text("Type your reply…")
        box.pack_start(entry, False, False, 8)
        def _sent(d):
            txt = entry.get_text().strip()
            if txt:
                self._spawn(self.facade.request(f"ntf-reply:{key}:{txt}"))

        open_dialog_nonblocking(dlg, on_ok=_sent)

NotificationsPage = NotificationsPageMixin
