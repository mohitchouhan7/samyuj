from __future__ import annotations

import base64
import contextlib
import math
import urllib.parse

from .gtk_compat import Gdk, GdkPixbuf, GLib, Gtk

from .concept_assets import CONCEPT_PNG_B64
from .theme import (
    get_active_color_palette,
    get_active_dark_mode,
    get_active_palette_tokens,
    get_active_ui_theme,
)

def _clamp_window_to_screen(win: Gtk.Window, req_w: int, req_h: int) -> tuple[int, int]:
    w, h = int(req_w), int(req_h)
    with contextlib.suppress(Exception):
        disp = Gdk.Display.get_default()
        if disp is not None:
            mon = disp.get_primary_monitor() or (disp.get_monitor(0) if disp.get_n_monitors() > 0 else None)
            if mon is not None:
                wa = mon.get_workarea()
                if wa.width > 320 and wa.height > 240:
                    w = min(w, int(wa.width * 0.94))
                    h = min(h, int(wa.height * 0.92))
                    with contextlib.suppress(Exception):
                        is_wl = "wayland" in type(disp).__name__.lower()
                        if is_wl and int(mon.get_scale_factor() or 1) <= 1:
                            w = min(w, int(wa.width / 1.35))
                            h = min(h, int(wa.height / 1.30))
    win.set_default_size(max(360, w), max(280, h))
    return w, h

_PIXBUF_CACHE: dict[tuple[str, int, int], GdkPixbuf.Pixbuf] = {}

def load_concept_pixbuf(key: str, max_w: int, max_h: int) -> GdkPixbuf.Pixbuf | None:
    cache_key = (key, int(max_w), int(max_h))
    if cache_key in _PIXBUF_CACHE:
        return _PIXBUF_CACHE[cache_key]
    b64 = CONCEPT_PNG_B64.get(key)
    if not b64:
        return None
    try:
        raw = base64.b64decode(b64)
        loader = GdkPixbuf.PixbufLoader.new_with_type("png")
        loader.write(raw)
        loader.close()
        pb = loader.get_pixbuf()
        if pb is None:
            return None
        pw, ph = pb.get_width(), pb.get_height()
        if pw <= 0 or ph <= 0:
            return None
        scale = min(max_w / pw, max_h / ph)
        tw = max(1, int(round(pw * scale)))
        th = max(1, int(round(ph * scale)))
        scaled = pb.scale_simple(tw, th, GdkPixbuf.InterpType.BILINEAR)
        _PIXBUF_CACHE[cache_key] = scaled
        if len(_PIXBUF_CACHE) > 256:
            for _old_k in list(_PIXBUF_CACHE.keys())[:len(_PIXBUF_CACHE) - 256]:
                _PIXBUF_CACHE.pop(_old_k, None)
        return scaled
    except Exception:
        return None

_ICON_SYMBOL_MAP = {
    "brand": ("preferences-desktop-display", "SC", "#38bdf8"),
    "home": ("go-home-symbolic", "HM", "#38bdf8"),
    "apps": ("view-app-grid-symbolic", "AP", "#38bdf8"),
    "appstream": ("view-app-grid-symbolic", "AP", "#38bdf8"),
    "desktop": ("computer-symbolic", "DT", "#38bdf8"),
    "screenshare": ("video-display-symbolic", "SS", "#38bdf8"),
    "webcam": ("camera-web-symbolic", "WC", "#38bdf8"),
    "clipboard": ("edit-paste-symbolic", "CB", "#38bdf8"),
    "crosscontrol": ("input-mouse-symbolic", "CC", "#38bdf8"),
    "files": ("folder-symbolic", "FL", "#38bdf8"),
    "gallery": ("folder-pictures-symbolic", "GL", "#38bdf8"),
    "messages": ("mail-unread-symbolic", "MS", "#38bdf8"),
    "sharing": ("folder-publicshare-symbolic", "SH", "#38bdf8"),
    "settings": ("emblem-system-symbolic", "ST", "#38bdf8"),
    "wifi": ("network-wireless-signal-excellent-symbolic", "WF", "#34d399"),
    "usb": ("drive-harddisk-usb-symbolic", "US", "#34d399"),
    "battery": ("battery-good-symbolic", "BT", "#38bdf8"),
    "storage": ("drive-harddisk-symbolic", "SD", "#38bdf8"),
    "landscape": ("object-rotate-right-symbolic", "LS", "#38bdf8"),
    "portrait": ("object-rotate-left-symbolic", "PT", "#38bdf8"),
    "mirror": ("view-mirror-symbolic", "MR", "#38bdf8"),
    "extend": ("video-display-symbolic", "EX", "#38bdf8"),
    "phone": ("phone-symbolic", "PH", "#38bdf8"),
}

def round_pixbuf_corners(
    src_pb: GdkPixbuf.Pixbuf,
    radius: float = 12.0,
    border_rgb: tuple[int, int, int] | None = None,
) -> GdkPixbuf.Pixbuf:
    w = int(src_pb.get_width())
    h = int(src_pb.get_height())
    if w <= 4 or h <= 4:
        return src_pb
    rad = min(float(radius), w * 0.45, h * 0.45)
    src_bytes = src_pb.get_pixels()
    src_rs = int(src_pb.get_rowstride())
    src_ch = int(src_pb.get_n_channels())
    has_a = bool(src_pb.get_has_alpha()) and src_ch >= 4
    out = bytearray(w * h * 4)
    x_left = rad
    x_right = w - 1.0 - rad
    y_top = rad
    y_bot = h - 1.0 - rad
    for y in range(h):
        dy = (y_top - y) if y < y_top else ((y - y_bot) if y > y_bot else 0.0)
        row_in = y * src_rs
        row_out = y * w * 4
        for x in range(w):
            dx = (x_left - x) if x < x_left else ((x - x_right) if x > x_right else 0.0)
            if dx > 0.0 and dy > 0.0:
                dist = math.hypot(dx, dy) - rad
            else:
                dist = max(dx, dy) - rad
            if dist >= 0.75:
                continue
            alpha_f = 1.0 if dist <= -0.75 else max(0.0, min(1.0, (0.75 - dist) / 1.5))
            in_off = row_in + x * src_ch
            r = src_bytes[in_off]
            g = src_bytes[in_off + 1]
            b = src_bytes[in_off + 2]
            in_a = src_bytes[in_off + 3] if has_a else 255
            if border_rgb is not None and dist >= -1.6:
                tb = max(0.0, min(1.0, (dist + 1.6) / 1.4))
                r = int(r * (1.0 - tb) + border_rgb[0] * tb)
                g = int(g * (1.0 - tb) + border_rgb[1] * tb)
                b = int(b * (1.0 - tb) + border_rgb[2] * tb)
            out_off = row_out + x * 4
            out[out_off] = r
            out[out_off + 1] = g
            out[out_off + 2] = b
            out[out_off + 3] = int(in_a * alpha_f)
    gbytes = GLib.Bytes.new(bytes(out))
    return GdkPixbuf.Pixbuf.new_from_bytes(
        gbytes, GdkPixbuf.Colorspace.RGB, True, 8, w, h, w * 4
    )

def open_dialog_nonblocking(dlg, on_ok=None, on_response=None):
    with contextlib.suppress(Exception):
        dlg.set_modal(True)
    dlg.set_destroy_with_parent(True)

    def _resp(d, response_id):
        try:
            if on_response is not None:
                on_response(d, response_id)
            elif response_id == Gtk.ResponseType.OK and on_ok is not None:
                on_ok(d)
        except Exception:
            pass
        finally:
            with contextlib.suppress(Exception):
                d.destroy()

    dlg.connect("response", _resp)
    with contextlib.suppress(Exception):
        dlg.connect("delete-event", lambda d, _e: (d.destroy(), False)[1])
    dlg.show_all()
    return dlg

def extract_dropped_paths(data, info=None) -> list:
    uris: list = []
    with contextlib.suppress(Exception):
        uris = list(data.get_uris() or [])
    if not uris:
        txt = ""
        with contextlib.suppress(Exception):
            raw = data.get_text()
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", "replace")
            txt = raw or ""
        uris = [ln.strip() for ln in txt.splitlines() if ln.strip()]
    out: list = []
    for u in uris:
        u = str(u).strip()
        if u.startswith("file://"):
            p = urllib.parse.unquote(urllib.parse.urlsplit(u).path)
        elif u.startswith("/"):
            p = u
        else:
            continue
        if p and p not in out:
            out.append(p)
    return out

def _cairo_rrect(cr, x: float, y: float, w: float, h: float, r: float) -> None:
    rad = max(0.0, min(float(r), w * 0.48, h * 0.48))
    cr.new_sub_path()
    cr.arc(x + w - rad, y + rad, rad, -math.pi / 2, 0)
    cr.arc(x + w - rad, y + h - rad, rad, 0, math.pi / 2)
    cr.arc(x + rad, y + h - rad, rad, math.pi / 2, math.pi)
    cr.arc(x + rad, y + rad, rad, math.pi, 3 * math.pi / 2)
    cr.close_path()

def _draw_md3_glyph(cr, kind: str, cx: float, cy: float, s: float, fg_rgb: tuple[float, float, float]) -> None:
    cr.set_source_rgb(*fg_rgb)
    lw = max(1.5, s * 0.095)
    cr.set_line_width(lw)
    cr.set_line_cap(1)
    cr.set_line_join(1)
    k = str(kind or "").strip().lower()
    if k == "home":
        cr.move_to(cx - s * 0.36, cy - s * 0.02)
        cr.line_to(cx, cy - s * 0.34)
        cr.line_to(cx + s * 0.36, cy - s * 0.02)
        cr.stroke()
        _cairo_rrect(cr, cx - s * 0.26, cy - s * 0.04, s * 0.52, s * 0.38, s * 0.08)
        cr.stroke()
    elif k in ("apps", "appstream"):
        for gx in (-0.28, 0.06):
            for gy in (-0.28, 0.06):
                _cairo_rrect(cr, cx + s * gx, cy + s * gy, s * 0.22, s * 0.22, s * 0.06)
                cr.stroke()
    elif k in ("desktop", "brand"):
        _cairo_rrect(cr, cx - s * 0.36, cy - s * 0.28, s * 0.72, s * 0.46, s * 0.09)
        cr.stroke()
        cr.move_to(cx, cy + s * 0.18)
        cr.line_to(cx, cy + s * 0.32)
        cr.move_to(cx - s * 0.18, cy + s * 0.32)
        cr.line_to(cx + s * 0.18, cy + s * 0.32)
        cr.stroke()
    elif k in ("screenshare", "mirror", "extend"):
        _cairo_rrect(cr, cx - s * 0.36, cy - s * 0.26, s * 0.54, s * 0.38, s * 0.08)
        cr.stroke()
        _cairo_rrect(cr, cx - s * 0.08, cy - s * 0.08, s * 0.44, s * 0.36, s * 0.08)
        cr.stroke()
    elif k == "webcam":
        cr.arc(cx, cy - s * 0.04, s * 0.28, 0, 2 * math.pi)
        cr.stroke()
        cr.arc(cx, cy - s * 0.04, s * 0.11, 0, 2 * math.pi)
        cr.fill()
        cr.move_to(cx - s * 0.22, cy + s * 0.32)
        cr.line_to(cx + s * 0.22, cy + s * 0.32)
        cr.stroke()
    elif k == "clipboard":
        _cairo_rrect(cr, cx - s * 0.26, cy - s * 0.24, s * 0.52, s * 0.58, s * 0.09)
        cr.stroke()
        _cairo_rrect(cr, cx - s * 0.13, cy - s * 0.32, s * 0.26, s * 0.12, s * 0.05)
        cr.fill()
    elif k == "crosscontrol":
        _cairo_rrect(cr, cx - s * 0.36, cy - s * 0.24, s * 0.34, s * 0.48, s * 0.07)
        cr.stroke()
        _cairo_rrect(cr, cx + s * 0.02, cy - s * 0.24, s * 0.34, s * 0.48, s * 0.07)
        cr.stroke()
        cr.move_to(cx - s * 0.14, cy)
        cr.line_to(cx + s * 0.16, cy)
        cr.move_to(cx + s * 0.06, cy - s * 0.10)
        cr.line_to(cx + s * 0.16, cy)
        cr.line_to(cx + s * 0.06, cy + s * 0.10)
        cr.stroke()
    elif k in ("files", "storage"):
        _cairo_rrect(cr, cx - s * 0.36, cy - s * 0.22, s * 0.72, s * 0.50, s * 0.09)
        cr.stroke()
        cr.move_to(cx - s * 0.34, cy - s * 0.08)
        cr.line_to(cx + s * 0.34, cy - s * 0.08)
        cr.stroke()
    elif k == "gallery":
        _cairo_rrect(cr, cx - s * 0.34, cy - s * 0.28, s * 0.68, s * 0.56, s * 0.10)
        cr.stroke()
        cr.arc(cx - s * 0.14, cy - s * 0.10, s * 0.07, 0, 2 * math.pi)
        cr.fill()
        cr.move_to(cx - s * 0.28, cy + s * 0.22)
        cr.line_to(cx - s * 0.04, cy - s * 0.02)
        cr.line_to(cx + s * 0.10, cy + s * 0.12)
        cr.line_to(cx + s * 0.22, cy + s * 0.02)
        cr.line_to(cx + s * 0.30, cy + s * 0.22)
        cr.stroke()
    elif k in ("messages", "alerts"):
        _cairo_rrect(cr, cx - s * 0.34, cy - s * 0.28, s * 0.68, s * 0.48, s * 0.12)
        cr.stroke()
        cr.move_to(cx - s * 0.18, cy - s * 0.08)
        cr.line_to(cx + s * 0.18, cy - s * 0.08)
        cr.move_to(cx - s * 0.18, cy + s * 0.05)
        cr.line_to(cx + s * 0.08, cy + s * 0.05)
        cr.stroke()
    elif k == "sharing":
        cr.move_to(cx, cy + s * 0.16)
        cr.line_to(cx, cy - s * 0.28)
        cr.move_to(cx - s * 0.16, cy - s * 0.12)
        cr.line_to(cx, cy - s * 0.28)
        cr.line_to(cx + s * 0.16, cy - s * 0.12)
        cr.stroke()
        cr.move_to(cx - s * 0.30, cy + s * 0.08)
        cr.line_to(cx - s * 0.30, cy + s * 0.28)
        cr.line_to(cx + s * 0.30, cy + s * 0.28)
        cr.line_to(cx + s * 0.30, cy + s * 0.08)
        cr.stroke()
    elif k == "settings":
        cr.arc(cx, cy, s * 0.22, 0, 2 * math.pi)
        cr.stroke()
        cr.arc(cx, cy, s * 0.08, 0, 2 * math.pi)
        cr.fill()
        for ang_i in range(6):
            ang = ang_i * (math.pi / 3.0)
            cr.move_to(cx + math.cos(ang) * s * 0.22, cy + math.sin(ang) * s * 0.22)
            cr.line_to(cx + math.cos(ang) * s * 0.33, cy + math.sin(ang) * s * 0.33)
        cr.stroke()
    elif k == "wifi":
        for rad_f in (0.34, 0.21):
            cr.new_sub_path()
            cr.arc(cx, cy + s * 0.18, s * rad_f, -math.pi * 0.78, -math.pi * 0.22)
            cr.stroke()
        cr.arc(cx, cy + s * 0.20, s * 0.06, 0, 2 * math.pi)
        cr.fill()
    elif k == "usb":
        cr.move_to(cx, cy - s * 0.30)
        cr.line_to(cx, cy + s * 0.28)
        cr.move_to(cx, cy + s * 0.08)
        cr.line_to(cx - s * 0.20, cy - s * 0.08)
        cr.line_to(cx - s * 0.20, cy - s * 0.20)
        cr.move_to(cx, cy + s * 0.14)
        cr.line_to(cx + s * 0.20, cy - s * 0.02)
        cr.line_to(cx + s * 0.20, cy - s * 0.14)
        cr.stroke()
        cr.arc(cx, cy + s * 0.28, s * 0.07, 0, 2 * math.pi)
        cr.fill()
    else:
        _cairo_rrect(cr, cx - s * 0.22, cy - s * 0.34, s * 0.44, s * 0.68, s * 0.09)
        cr.stroke()
        cr.arc(cx, cy + s * 0.22, s * 0.04, 0, 2 * math.pi)
        cr.fill()

def _make_md3_icon_drawing_area(kind: str, size: int, with_tonal_container: bool = False) -> Gtk.DrawingArea:
    da = Gtk.DrawingArea()
    da.set_size_request(int(size), int(size))
    da.set_halign(Gtk.Align.CENTER)
    da.set_valign(Gtk.Align.CENTER)

    def _draw(_w, cr):
        alloc = da.get_allocation()
        w = float(alloc.width if alloc and alloc.width > 4 else size)
        h = float(alloc.height if alloc and alloc.height > 4 else size)
        tokens = get_active_palette_tokens()
        acc_hex = tokens.get("ok" if kind in ("wifi", "usb") else "acc", "#d0bcff")
        acc2_hex = tokens.get("ok_text" if kind in ("wifi", "usb") else "acc2", "#eaddff")
        bg_hex = tokens.get("okbg" if kind in ("wifi", "usb") else "accbg", "#4f378b")
        if with_tonal_container:
            br, bg_c, bb = _hex_to_rgb_bytes(bg_hex)
            ar, ag, ab = _hex_to_rgb_bytes(acc_hex)
            rad = min(w, h) * (0.38 if get_active_ui_theme() == "material" else 0.30)
            _cairo_rrect(cr, 1.0, 1.0, w - 2.0, h - 2.0, rad)
            cr.set_source_rgb(br / 255.0, bg_c / 255.0, bb / 255.0)
            cr.fill_preserve()
            cr.set_source_rgba(ar / 255.0, ag / 255.0, ab / 255.0, 0.45)
            cr.set_line_width(1.2)
            cr.stroke()
            fr, fg_c, fb = _hex_to_rgb_bytes(acc2_hex)
            _draw_md3_glyph(cr, kind, w / 2.0, h / 2.0, min(w, h) * 0.56, (fr / 255.0, fg_c / 255.0, fb / 255.0))
        else:
            fr, fg_c, fb = _hex_to_rgb_bytes(acc_hex)
            _draw_md3_glyph(cr, kind, w / 2.0, h / 2.0, min(w, h) * 0.82, (fr / 255.0, fg_c / 255.0, fb / 255.0))
        return False

    if hasattr(da, "set_draw_func"):
        da.set_draw_func(lambda w_da, cr, _w, _h: _draw(w_da, cr))
    else:
        da.connect("draw", _draw)
    return da

def make_cairo_icon(kind: str, size: int = 24, color: tuple[float, float, float] = (0.16, 0.22, 0.28)) -> Gtk.Widget:
    return _make_md3_icon_drawing_area(kind, size=int(size), with_tonal_container=False)

def make_themed_feature_icon(feat_id: str, png_key: str, size: int = 44) -> Gtk.Widget:
    ui_theme = get_active_ui_theme()
    if ui_theme == "gtk":
        pb = load_concept_pixbuf(png_key, size, size)
        if pb is not None:
            return Gtk.Image.new_from_pixbuf(round_pixbuf_corners(pb, radius=size * 0.28))
    return _make_md3_icon_drawing_area(feat_id or png_key, size=int(size), with_tonal_container=True)

def _hex_to_rgb_bytes(hex_str: str, fallback: tuple[int, int, int] = (30, 41, 56)) -> tuple[int, int, int]:
    s = str(hex_str or "").strip().lstrip("#")
    if len(s) == 6:
        try:
            return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
        except ValueError:
            pass
    return fallback

_SETUP_PREVIEW_CACHE: dict[tuple[str, int, int, bool, str], GdkPixbuf.Pixbuf] = {}

def make_setup_preview_pixbuf(kind: str, width: int = 164, height: int = 104) -> GdkPixbuf.Pixbuf:
    pal_id = get_active_color_palette()
    is_dark = bool(get_active_dark_mode())
    key = (str(kind), int(width), int(height), is_dark, pal_id)
    if key in _SETUP_PREVIEW_CACHE:
        return _SETUP_PREVIEW_CACHE[key]
    w, h = max(64, int(width)), max(48, int(height))
    buf = bytearray(w * h * 3)

    def _fill_rect(x0: int, y0: int, rw: int, rh: int, r: int, g: int, b: int) -> None:
        cx0 = max(0, min(w, int(x0)))
        cy0 = max(0, min(h, int(y0)))
        cx1 = max(0, min(w, int(x0 + rw)))
        cy1 = max(0, min(h, int(y0 + rh)))
        if cx1 <= cx0 or cy1 <= cy0:
            return
        row_bytes = bytes((r, g, b)) * (cx1 - cx0)
        for yy in range(cy0, cy1):
            off = (yy * w + cx0) * 3
            buf[off:off + (cx1 - cx0) * 3] = row_bytes

    tokens = get_active_palette_tokens(pal_id, is_dark)
    bg_r, bg_g, bg_b = _hex_to_rgb_bytes(tokens.get("surf2", "#181b26"))
    surf_r, surf_g, surf_b = _hex_to_rgb_bytes(tokens.get("surf", "#12141c"))
    line_r, line_g, line_b = _hex_to_rgb_bytes(tokens.get("line", "#232736"))
    acc_r, acc_g, acc_b = _hex_to_rgb_bytes(tokens.get("acc", "#38bdf8"))
    accbg_r, accbg_g, accbg_b = _hex_to_rgb_bytes(tokens.get("accbg", "#112840"))
    ok_r, ok_g, ok_b = _hex_to_rgb_bytes(tokens.get("ok", "#34d399"))

    _fill_rect(0, 0, w, h, bg_r, bg_g, bg_b)

    if kind in ("md_landscape", "md_portrait"):
        lx, ly, lw, lh = (w - 124) // 2, 10, 124, 76
        _fill_rect(lx, ly, lw, lh, line_r, line_g, line_b)
        _fill_rect(lx + 3, ly + 3, lw - 6, lh - 6, surf_r, surf_g, surf_b)
        _fill_rect(lx - 10, ly + lh, lw + 20, 6, line_r, line_g, line_b)
        if kind == "md_landscape":
            wx, wy, ww, wh = lx + 14, ly + 10, 96, 54
        else:
            wx, wy, ww, wh = lx + 42, ly + 7, 40, 60
        _fill_rect(wx, wy, ww, wh, accbg_r, accbg_g, accbg_b)
        _fill_rect(wx, wy, ww, 10, acc_r, acc_g, acc_b)
        _fill_rect(wx + 5, wy + 15, max(8, ww - 10), max(8, wh - 20), surf_r, surf_g, surf_b)
    elif kind in ("phone_mirror", "phone_virtual"):
        px, py, pw, ph = 18, 16, 36, 70
        _fill_rect(px, py, pw, ph, line_r, line_g, line_b)
        if kind == "phone_mirror":
            _fill_rect(px + 3, py + 5, pw - 6, ph - 10, acc_r, acc_g, acc_b)
            _fill_rect(px + 7, py + 12, pw - 14, 14, surf_r, surf_g, surf_b)
            _fill_rect(px + 7, py + 32, pw - 14, 22, accbg_r, accbg_g, accbg_b)
        else:
            _fill_rect(px + 3, py + 5, pw - 6, ph - 10, surf_r, surf_g, surf_b)
            _fill_rect(px + 8, py + 14, pw - 16, 8, line_r, line_g, line_b)
        lx, ly, lw, lh = 66, 14, 84, 64
        _fill_rect(lx, ly, lw, lh, line_r, line_g, line_b)
        _fill_rect(lx + 3, ly + 3, lw - 6, lh - 6, surf_r, surf_g, surf_b)
        _fill_rect(lx - 6, ly + lh, lw + 12, 5, line_r, line_g, line_b)
        vwx, vwy, vww, vwh = lx + 26, ly + 6, 32, 50
        if kind == "phone_mirror":
            _fill_rect(vwx, vwy, vww, vwh, acc_r, acc_g, acc_b)
        else:
            _fill_rect(vwx, vwy, vww, vwh, ok_r, ok_g, ok_b)
        _fill_rect(vwx + 3, vwy + 8, vww - 6, 12, surf_r, surf_g, surf_b)
        _fill_rect(vwx + 3, vwy + 24, vww - 6, 20, accbg_r, accbg_g, accbg_b)
        if kind == "phone_virtual":
            _fill_rect(lx + 8, ly + 10, 13, 13, ok_r, ok_g, ok_b)
            _fill_rect(lx + 13, ly + 12, 3, 9, 255, 255, 255)
            _fill_rect(lx + 10, ly + 15, 9, 3, 255, 255, 255)
    else:
        lx, ly, lw, lh = 14, 16, 82, 58
        _fill_rect(lx, ly, lw, lh, line_r, line_g, line_b)
        _fill_rect(lx + 3, ly + 3, lw - 6, lh - 6, acc_r, acc_g, acc_b)
        _fill_rect(lx + 10, ly + 10, lw - 20, lh - 20, surf_r, surf_g, surf_b)
        _fill_rect(lx - 6, ly + lh, lw + 12, 5, line_r, line_g, line_b)
        px, py, pw, ph = 106, 26, 46, 38
        _fill_rect(px, py, pw, ph, line_r, line_g, line_b)
        if kind == "pc_extend":
            _fill_rect(px + 3, py + 3, pw - 6, ph - 6, ok_r, ok_g, ok_b)
        else:
            _fill_rect(px + 3, py + 3, pw - 6, ph - 6, acc_r, acc_g, acc_b)
        _fill_rect(px + 8, py + 8, pw - 16, ph - 16, surf_r, surf_g, surf_b)

    gbytes = GLib.Bytes.new(bytes(buf))
    raw_pb = GdkPixbuf.Pixbuf.new_from_bytes(gbytes, GdkPixbuf.Colorspace.RGB, False, 8, w, h, w * 3)
    pb = round_pixbuf_corners(raw_pb, radius=14.0, border_rgb=(line_r, line_g, line_b))
    _SETUP_PREVIEW_CACHE[key] = pb
    return pb
