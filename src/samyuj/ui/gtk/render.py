from __future__ import annotations

import math
from pathlib import Path

from .gtk_compat import GdkPixbuf, GLib, Gtk

from samyuj.ui.gtk.assets import load_concept_pixbuf
from samyuj.ui.gtk.theme import get_active_palette_tokens

from samyuj.media.frame_hub import (
    _LIBSC_FAST,
    apply_uv_swap_state,
    get_prepared_frame_bgrx,
    is_uv_swapped,
    target_render_size,
)

def _hex_to_rgb_tuple(hex_str: str, default: tuple[int, int, int] = (46, 52, 68)) -> tuple[int, int, int]:
    s = str(hex_str or "").strip().lstrip("#")
    if len(s) == 6:
        try:
            return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
        except ValueError:
            pass
    return default

def _make_rounded_handset_pixbuf(
    src_pb: GdkPixbuf.Pixbuf,
    width: int,
    height: int,
    radius: float = 18.0,
    border_rgb: tuple[int, int, int] = (46, 52, 68),
) -> GdkPixbuf.Pixbuf:
    w = max(48, int(width))
    h = max(96, int(height))
    scaled = src_pb.scale_simple(w, h, GdkPixbuf.InterpType.BILINEAR) or src_pb
    src_bytes = scaled.get_pixels()
    src_rs = scaled.get_rowstride()
    src_ch = scaled.get_n_channels()

    out = bytearray(w * h * 4)
    rad = min(float(radius), w * 0.45, h * 0.45)
    border_w = 2.0
    br, bg, bb = border_rgb

    x_left = rad
    x_right = w - 1.0 - rad
    y_top = rad
    y_bot = h - 1.0 - rad

    pill_y = 6
    pill_x0 = (w // 2) - 10
    pill_x1 = (w // 2) + 10

    for y in range(h):
        dy = 0.0
        if y < y_top:
            dy = y_top - y
        elif y > y_bot:
            dy = y - y_bot
        row_out = y * w * 4
        row_in = y * src_rs
        for x in range(w):
            dx = 0.0
            if x < x_left:
                dx = x_left - x
            elif x > x_right:
                dx = x - x_right

            if dx > 0.0 and dy > 0.0:
                dist = math.hypot(dx, dy) - rad
            else:
                dist = max(dx, dy) - rad

            if dist >= 0.75:
                continue

            if dist <= -0.75:
                alpha = 255
            else:
                alpha = int(max(0.0, min(1.0, (0.75 - dist) / 1.5)) * 255.0)

            in_off = row_in + x * src_ch
            pr = src_bytes[in_off]
            pg = src_bytes[in_off + 1]
            pb_c = src_bytes[in_off + 2]

            if dist >= -border_w:
                t_b = max(0.0, min(1.0, (dist + border_w) / 1.25))
                r = int(pr * (1.0 - t_b) + br * t_b)
                g = int(pg * (1.0 - t_b) + bg * t_b)
                b = int(pb_c * (1.0 - t_b) + bb * t_b)
            elif y in (pill_y, pill_y + 1) and pill_x0 <= x <= pill_x1:
                r = int(pr * 0.35 + br * 0.65)
                g = int(pg * 0.35 + bg * 0.65)
                b = int(pb_c * 0.35 + bb * 0.65)
            else:
                r, g, b = pr, pg, pb_c

            out_off = row_out + x * 4
            out[out_off] = r
            out[out_off + 1] = g
            out[out_off + 2] = b
            out[out_off + 3] = alpha

    gbytes = GLib.Bytes.new(bytes(out))
    return GdkPixbuf.Pixbuf.new_from_bytes(
        gbytes, GdkPixbuf.Colorspace.RGB, True, 8, w, h, w * 4
    )

class PhoneHandsetPreview(Gtk.EventBox):

    def __init__(self, width: int = 76, height: int = 148) -> None:
        super().__init__()
        self.set_visible_window(False)
        self._w = max(56, int(width))
        self._h = max(112, int(height))
        self.set_size_request(self._w, self._h)
        self._raw_pixbuf: GdkPixbuf.Pixbuf | None = None
        self._wp_pixbuf: GdkPixbuf.Pixbuf | None = None
        self._wp_path: str = ""
        self._last_border_hex: str = ""
        self._img = Gtk.Image()
        self.add(self._img)
        self.set_default_wallpaper()

    def _apply_rounded_mask(self) -> None:
        if self._raw_pixbuf is None:
            return
        tokens = get_active_palette_tokens()
        border_hex = str(tokens.get("line_strong") or tokens.get("line") or "#2e3444")
        border_rgb = _hex_to_rgb_tuple(border_hex)
        self._last_border_hex = border_hex
        self._wp_pixbuf = _make_rounded_handset_pixbuf(
            self._raw_pixbuf, self._w, self._h, radius=18.0, border_rgb=border_rgb
        )
        self._img.set_from_pixbuf(self._wp_pixbuf)

    def refresh_theme_bezel(self) -> None:
        self._apply_rounded_mask()

    def set_default_wallpaper(self) -> None:
        pb = load_concept_pixbuf("default_wallpaper", self._w * 2, self._h * 2)
        if pb is not None:
            self._raw_pixbuf = pb
            self._wp_path = "__default__"
            self._apply_rounded_mask()

    def set_wallpaper_from_file(self, path: str | Path) -> bool:
        p = str(path or "")
        if not p or not Path(p).is_file():
            return False
        tokens = get_active_palette_tokens()
        border_hex = str(tokens.get("line_strong") or tokens.get("line") or "#2e3444")
        if p == self._wp_path and self._wp_pixbuf is not None and border_hex == self._last_border_hex:
            return True
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file(p)
            if pb is not None:
                self._raw_pixbuf = pb
                self._wp_path = p
                self._apply_rounded_mask()
                return True
        except Exception:
            pass
        return False

class GlShaderVideoRenderer:

    VERTEX_SHADER_GLSL = """#version 150 core
    in vec2 aPos;
    in vec2 aTexCoord;
    out vec2 vTexCoord;
    void main() {
        gl_Position = vec4(aPos, 0.0, 1.0);
        vTexCoord = aTexCoord;
    }
    """

    FRAGMENT_SHADER_GLSL = """#version 150 core
    in vec2 vTexCoord;
    out vec4 FragColor;
    uniform sampler2D luminanceChannel;
    uniform sampler2D chrominanceU;
    uniform sampler2D chrominanceV;
    void main() {
        float y = texture(luminanceChannel, vTexCoord).r;
        float u = texture(chrominanceU, vTexCoord).r - 0.5;
        float v = texture(chrominanceV, vTexCoord).r - 0.5;
        float r = clamp(y + 1.402 * v, 0.0, 1.0);
        float g = clamp(y - 0.344136 * u - 0.714136 * v, 0.0, 1.0);
        float b = clamp(y + 1.772 * u, 0.0, 1.0);
        FragColor = vec4(r, g, b, 1.0);
    }
    """
