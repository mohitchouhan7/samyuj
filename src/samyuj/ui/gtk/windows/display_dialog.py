from __future__ import annotations
from samyuj.ui.gtk.theme import _ACTIVE_DARK_MODE

import contextlib
import math

from samyuj.protocol.constants import PHONE_EXTEND_PRESETS
from samyuj.ui.gtk.gtk_compat import Gdk, Gtk

from samyuj.ui.gtk.assets import open_dialog_nonblocking

def layout_placement(real: list, phone_w: int, phone_h: int, edge: str) -> dict:
    if not real:
        real = [(0, 0, 1920, 1080)]
    x0 = min(r[0] for r in real)
    y0 = min(r[1] for r in real)
    x1 = max(r[0] + r[2] for r in real)
    y1 = max(r[1] + r[3] for r in real)
    w, h = max(1, int(phone_w)), max(1, int(phone_h))
    dx = dy = 0
    edge = (edge or "right").lower()
    if edge == "right":
        px, py = x1, y0
    elif edge == "below":
        px, py = x0, y1
    elif edge == "left":
        px, py, dx = 0, y0, w
    elif edge == "above":
        px, py, dy = x0, 0, h
    else:
        px, py = x1, y0
    rects = [(r[0] + dx - min(0, 0), r[1] + dy, r[2], r[3]) for r in real]
    all_x = [px] + [r[0] for r in rects]
    all_y = [py] + [r[1] for r in rects]
    all_x1 = [px + w] + [r[0] + r[2] for r in rects]
    all_y1 = [py + h] + [r[1] + r[3] for r in rects]
    return {
        "edge": edge,
        "phone": (px, py, w, h),
        "real": rects,
        "shift": (dx, dy),
        "bounds": (min(all_x), min(all_y), max(all_x1), max(all_y1)),
    }

class VirtualDisplayPlacementDialog(Gtk.Dialog):

    SIZE_PRESETS = PHONE_EXTEND_PRESETS + (
        ("1920x1080", "1920 × 1080 (16:9 Full HD landscape)"),
        ("1280x720", "1280 × 720 (16:9 HD landscape, lower latency)"),
        ("2560x1440", "2560 × 1440 (16:9 QHD landscape)"),
        ("1080x1920", "1080 × 1920 (9:16 Full HD portrait)"),
        ("720x1280", "720 × 1280 (9:16 HD portrait)"),
        ("1200x1920", "1200 × 1920 (10:16 tall portrait)"),
    )

    def __init__(self, app) -> None:
        super().__init__(title="Virtual Display — placement", transient_for=app.win,
                         flags=0)
        self.app = app
        self.facade = app.facade
        self.set_default_size(620, 560)
        self.edge = str(getattr(self.facade, "ss_virtual_pos", "right"))
        w, h = self.facade._ss_virtual_size() if hasattr(self.facade, "_ss_virtual_size") else (2400, 1080)
        self.size = (int(w), int(h))
        self.result: dict | None = None

        box = self.get_content_area()
        box.set_spacing(10)
        for m in (14, 14, 14, 14):
            pass
        box.set_margin_top(14); box.set_margin_bottom(14)
        box.set_margin_start(14); box.set_margin_end(14)

        head = Gtk.Label(label="Where should your phone attach as a second display?",
                         xalign=0.0)
        head.get_style_context().add_class("panel-h2")
        box.pack_start(head, False, False, 0)
        sub = Gtk.Label(xalign=0.0)
        sub.set_markup(
            '<span size="small">Click an edge of your display below. The phone '
            'display is created there and the pointer crosses onto it like a real '
            'monitor. Left and Above must shift your real display(s) — that is '
            'done in one step and restored exactly when the share stops.</span>')
        sub.set_line_wrap(True)
        box.pack_start(sub, False, False, 0)

        self.preview = Gtk.DrawingArea()
        self.preview.set_size_request(560, 250)
        self.preview.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        if hasattr(self.preview, "set_draw_func"):
            self.preview.set_draw_func(lambda da, cr, _w, _h: self._on_draw(da, cr))
        else:
            self.preview.connect("draw", self._on_draw)
        self.preview.connect("button-press-event", self._on_preview_click)
        self.preview.set_tooltip_text("Click an edge to attach the phone display there")
        frame = Gtk.Frame()
        frame.set_shadow_type(Gtk.ShadowType.NONE)
        frame.get_style_context().add_class("sc-card")
        frame.add(self.preview)
        box.pack_start(frame, True, True, 0)

        edge_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._edge_btns: dict = {}
        for key, label in (("left", "Left"), ("right", "Right"),
                           ("above", "Above"), ("below", "Below")):
            b = Gtk.ToggleButton(label=label)
            b.get_style_context().add_class("secondary-btn")
            b.set_active(key == self.edge)
            b.connect("toggled", self._on_edge_toggled, key)
            self._edge_btns[key] = b
            edge_row.pack_start(b, True, True, 0)
        box.pack_start(edge_row, False, False, 0)

        opts = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.orient_landscape = Gtk.RadioButton.new_with_label_from_widget(None, "Landscape")
        self.orient_portrait = Gtk.RadioButton.new_with_label_from_widget(
            self.orient_landscape, "Portrait")
        cur_o = str(getattr(self.facade, "ss_virtual_orient", "auto"))
        self.orient_portrait.set_active(cur_o == "portrait" or self.size[1] > self.size[0])
        self.orient_landscape.connect("toggled", self._on_orient_toggled)
        self.rotate_btn = Gtk.Button(label="Rotate (Landscape / Portrait)")
        self.rotate_btn.set_tooltip_text(
            "Rotate the extended mobile display between Landscape (20:9 / 22:9) and Portrait (9:20 / 9:22)"
        )
        self.rotate_btn.get_style_context().add_class("secondary-btn")
        self.rotate_btn.connect("clicked", self._on_rotate_btn_clicked)
        ov = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        ov.pack_start(self.orient_landscape, False, False, 0)
        ov.pack_start(self.orient_portrait, False, False, 0)
        ov.pack_start(self.rotate_btn, False, False, 0)
        opts.pack_start(ov, False, False, 0)

        sv = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        sv.pack_start(Gtk.Label(label="Size", xalign=0.0), False, False, 0)
        self.size_combo = Gtk.ComboBoxText()
        for key, label in self.SIZE_PRESETS:
            self.size_combo.append(key, label)
        cur = f"{self.size[0]}x{self.size[1]}"
        if not self.size_combo.set_active_id(cur):
            self.size_combo.append(cur, f"{self.size[0]} × {self.size[1]} (current)")
            self.size_combo.set_active_id(cur)
        self.size_combo.connect("changed", self._on_size_changed)
        sv.pack_start(self.size_combo, False, False, 0)
        opts.pack_start(sv, True, True, 0)
        box.pack_start(opts, False, False, 0)

        self.info_lbl = Gtk.Label(xalign=0.0)
        self.info_lbl.set_line_wrap(True)
        self.info_lbl.get_style_context().add_class("panel-sub")
        box.pack_start(self.info_lbl, False, False, 0)

        self.send_win_chk = Gtk.CheckButton(
            label="After starting, move my active window onto the phone display")
        self.send_win_chk.set_active(True)
        box.pack_start(self.send_win_chk, False, False, 0)

        self.add_buttons("Cancel", Gtk.ResponseType.CANCEL,
                         "Start Share Screen", Gtk.ResponseType.OK)
        self._refresh_info()
        self.show_all()

    def _real_rects(self) -> list:
        out = []
        with contextlib.suppress(Exception):
            disp = Gdk.Display.get_default()
            n = disp.get_n_monitors() if disp is not None else 0
            for i in range(n):
                g = disp.get_monitor(i).get_geometry()
                out.append((g.x, g.y, g.width, g.height))
        if not out:
            out = [(0, 0, 1920, 1080)]
        return out

    def _layout(self) -> dict:
        return layout_placement(self._real_rects(), self.size[0], self.size[1], self.edge)

    def _refresh_info(self) -> None:
        lay = self._layout()
        probe = {}
        with contextlib.suppress(Exception):
            probe = self.facade.virtual_display.probe() if getattr(self.facade, "virtual_display", None) else {}
        order = probe.get("order") or []
        backend = order[0] if order else "none"
        if backend == "xvfb":
            note = ("Backend available: Xvfb (a nested X screen). It CANNOT attach "
                    "to an edge and the pointer will not cross onto it — install "
                    "x11-xserver-utils for xrandr, or use sway. It still gives you a "
                    "real second desktop you can launch apps into.")
        elif backend in ("xrandr", "sway"):
            note = (f"Backend: {backend}. The display will be attached at the "
                    f"{self.edge} edge and the pointer will cross onto it.")
            if self.edge in ("left", "above"):
                note += (" Your real display(s) shift to make room; teardown "
                         "restores them exactly.")
        else:
            note = ("No virtual-display backend found on this session "
                    f"(type={probe.get('session')}). Share Screen will mirror "
                    "instead and say so. Install x11-xserver-utils (xrandr) or "
                    "Xvfb.")
        bx, by, bw, bh = lay["bounds"]
        note += f"  Total desktop after attach: {bw} × {bh}."
        self.info_lbl.set_markup(f"<small>{note}</small>")

    def _on_edge_toggled(self, btn, key) -> None:
        if btn.get_active() and key != self.edge:
            self.edge = key
            for k, b in self._edge_btns.items():
                if k != key and b.get_active():
                    b.set_active(False)
            self.preview.queue_draw()
            self._refresh_info()

    def _on_rotate_btn_clicked(self, *_a) -> None:
        if self.orient_portrait.get_active():
            self.orient_landscape.set_active(True)
        else:
            self.orient_portrait.set_active(True)
        self._on_orient_toggled()

    def _on_orient_toggled(self, *_a) -> None:
        w, h = self.size
        want_portrait = self.orient_portrait.get_active()
        if (h > w) != want_portrait:
            self.size = (min(w, h), max(w, h)) if want_portrait else (max(w, h), min(w, h))
            key = f"{self.size[0]}x{self.size[1]}"
            if not self.size_combo.set_active_id(key):
                self.size_combo.prepend(key, f"{self.size[0]} × {self.size[1]}")
                self.size_combo.set_active_id(key)
            self.preview.queue_draw()
            self._refresh_info()

    def _on_size_changed(self, combo) -> None:
        key = combo.get_active_id() or ""
        if "x" in key:
            with contextlib.suppress(ValueError):
                w, h = (int(v) for v in key.split("x", 1))
                self.size = (max(160, w), max(160, h))
                self.orient_portrait.set_active(h > w)
                self.preview.queue_draw()
                self._refresh_info()

    def _on_preview_click(self, _w, ev) -> bool:
        lay = self._layout()
        bx, by, bw, bh = lay["bounds"]
        if bw <= 0 or bh <= 0:
            return False
        alloc = self.preview.get_allocation()
        pad = 24.0
        sc = min((alloc.width - 2 * pad) / bw, (alloc.height - 2 * pad) / bh)
        ox = pad + (alloc.width - 2 * pad - bw * sc) / 2.0 - bx * sc
        oy = pad + (alloc.height - 2 * pad - bh * sc) / 2.0 - by * sc
        rects = lay["real"]
        rx0 = min(r[0] for r in rects); ry0 = min(r[1] for r in rects)
        rx1 = max(r[0] + r[2] for r in rects); ry1 = max(r[1] + r[3] for r in rects)
        cx = (ev.x - ox) / sc; cy = (ev.y - oy) / sc
        cands = {"left": abs(cx - rx0), "right": abs(cx - rx1),
                 "above": abs(cy - ry0), "below": abs(cy - ry1)}
        if cx < rx0:
            pick = "left"
        elif cx > rx1:
            pick = "right"
        elif cy < ry0:
            pick = "above"
        elif cy > ry1:
            pick = "below"
        else:
            pick = min(cands, key=cands.get)
        self.edge = pick
        for k, b in self._edge_btns.items():
            b.set_active(k == pick)
        self.preview.queue_draw()
        self._refresh_info()
        return True

    @staticmethod
    def _rounded_rect(cr, x: float, y: float, w: float, h: float, r: float = 10.0) -> None:
        rad = max(0.0, min(float(r), w * 0.45, h * 0.45))
        cr.new_sub_path()
        cr.arc(x + w - rad, y + rad, rad, -math.pi / 2, 0)
        cr.arc(x + w - rad, y + h - rad, rad, 0, math.pi / 2)
        cr.arc(x + rad, y + h - rad, rad, math.pi / 2, math.pi)
        cr.arc(x + rad, y + rad, rad, math.pi, 3 * math.pi / 2)
        cr.close_path()

    def _on_draw(self, _w, cr) -> None:
        alloc = self.preview.get_allocation()
        if _ACTIVE_DARK_MODE:
            cr.set_source_rgb(0.075, 0.105, 0.160)
        else:
            cr.set_source_rgb(0.965, 0.973, 0.984)
        self._rounded_rect(cr, 1.0, 1.0, max(8.0, alloc.width - 2.0), max(8.0, alloc.height - 2.0), 16.0)
        cr.fill()
        lay = self._layout()
        bx, by, bw, bh = lay["bounds"]
        pad = 24.0
        sc = min((alloc.width - 2 * pad) / max(1, bw),
                 (alloc.height - 2 * pad) / max(1, bh))
        ox = pad + (alloc.width - 2 * pad - bw * sc) / 2.0 - bx * sc
        oy = pad + (alloc.height - 2 * pad - bh * sc) / 2.0 - by * sc

        def rect(x, y, w, h):
            return (ox + x * sc, oy + y * sc, max(4.0, w * sc), max(4.0, h * sc))

        for i, (x, y, w, h) in enumerate(lay["real"]):
            rx, ry, rw, rh = rect(x, y, w, h)
            if _ACTIVE_DARK_MODE:
                cr.set_source_rgb(0.13, 0.19, 0.28)
            else:
                cr.set_source_rgb(0.86, 0.89, 0.93)
            self._rounded_rect(cr, rx, ry, rw, rh, 10.0); cr.fill()
            if _ACTIVE_DARK_MODE:
                cr.set_source_rgb(0.32, 0.45, 0.62); cr.set_line_width(1.4)
            else:
                cr.set_source_rgb(0.42, 0.48, 0.56); cr.set_line_width(1.4)
            self._rounded_rect(cr, rx, ry, rw, rh, 10.0); cr.stroke()
            if _ACTIVE_DARK_MODE:
                cr.set_source_rgb(0.88, 0.93, 0.98)
            else:
                cr.set_source_rgb(0.20, 0.26, 0.33)
            cr.select_font_face("Sans", 0, 0)
            cr.set_font_size(12)
            label = f"Your display {i + 1}   {w}x{h}"
            ext = cr.text_extents(label)
            cr.move_to(rx + (rw - ext.width) / 2, ry + rh / 2 + 4)
            cr.show_text(label)

        px, py, pw, ph = lay["phone"]
        qx, qy, qw, qh = rect(px, py, pw, ph)
        if _ACTIVE_DARK_MODE:
            cr.set_source_rgb(0.08, 0.24, 0.18)
        else:
            cr.set_source_rgb(0.84, 0.92, 0.86)
        self._rounded_rect(cr, qx, qy, qw, qh, 10.0); cr.fill()
        if _ACTIVE_DARK_MODE:
            cr.set_source_rgb(0.20, 0.82, 0.48); cr.set_line_width(2.2)
        else:
            cr.set_source_rgb(0.16, 0.52, 0.28); cr.set_line_width(2.2)
        self._rounded_rect(cr, qx, qy, qw, qh, 10.0); cr.stroke()
        if _ACTIVE_DARK_MODE:
            cr.set_source_rgb(0.65, 0.96, 0.78)
        else:
            cr.set_source_rgb(0.10, 0.33, 0.18)
        cr.set_font_size(12)
        _ar = self.facade.describe_aspect_ratio(pw, ph) if hasattr(self.facade, "describe_aspect_ratio") else ("9:20" if ph > pw else "20:9")
        plabel = f"Phone   {pw}×{ph} ({_ar})"
        ext = cr.text_extents(plabel)
        cr.move_to(qx + max(4, (qw - ext.width) / 2), qy + qh / 2 + 4)
        cr.show_text(plabel)
        cr.set_source_rgba(0.16, 0.52, 0.28, 0.55)
        cr.set_line_width(3.0)
        cr.set_dash([5.0, 3.0])
        if lay["edge"] == "right":
            cr.move_to(qx, qy); cr.line_to(qx, qy + qh)
        elif lay["edge"] == "left":
            cr.move_to(qx + qw, qy); cr.line_to(qx + qw, qy + qh)
        elif lay["edge"] == "below":
            cr.move_to(qx, qy); cr.line_to(qx + qw, qy)
        else:
            cr.move_to(qx, qy + qh); cr.line_to(qx + qw, qy + qh)
        cr.stroke()
        cr.set_dash([])

    def collect(self) -> dict:
        self.result = {
            "edge": self.edge,
            "size": tuple(self.size),
            "orient": "portrait" if self.size[1] > self.size[0] else "landscape",
            "send_window": bool(self.send_win_chk.get_active()),
        }
        return self.result

    def open_nonblocking(self, on_choice) -> None:
        open_dialog_nonblocking(
            self,
            on_response=lambda d, r: (on_choice(d.collect() if r == Gtk.ResponseType.OK
                                                else None)),
        )

