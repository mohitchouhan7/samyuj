from __future__ import annotations

from .theme_dark import GTK_CSS_DARK
from .theme_light import GTK_CSS_LIGHT

UI_THEMES: tuple[tuple[str, str, str], ...] = (
    ("material", "Material You (MD3)", "Tonal Pill Surfaces · Vector Icons · 28px Radii"),
    ("tray", "Tray Concept Theme", "Flat Vector · Rounded Grid"),
    ("aurora", "Aurora Glass Neo", "Frosted Layering · Pill Nav"),
    ("gtk", "Linux GTK App Theme", "GTK3 Left-Bar Nav · 3D Icons"),
)

COLOR_PALETTES: dict[str, dict[str, dict[str, str] | str]] = {
    "material_you": {
        "name": "Material You Dynamic Iris",
        "swatch": "#d0bcff",
        "dark": {
            "bg": "#141218",
            "surf": "#1d1b20",
            "surf2": "#2b2930",
            "side": "#18161d",
            "line": "#36343b",
            "line_strong": "#49454f",
            "fg": "#e6e0e9",
            "fg_strong": "#fef7ff",
            "dim": "#cac4d0",
            "acc": "#d0bcff",
            "acc2": "#eaddff",
            "accbg": "#4f378b",
            "ok": "#82d6a6",
            "ok_text": "#a6f2c4",
            "okbg": "#193829",
            "warn": "#fbbc04",
            "warnbg": "#382c10",
            "bad": "#f2b8b5",
            "bad_text": "#f9dedc",
            "badbg": "#601410",
            "dev_grad": "linear-gradient(135deg, #211f26 0%, #1d1b20 55%, #2b2930 100%)",
        },
        "light": {
            "bg": "#fef7ff",
            "surf": "#f3edf7",
            "surf2": "#ece6f0",
            "side": "#f7f2fa",
            "line": "#e6e0e9",
            "line_strong": "#cac4d0",
            "fg": "#1d1b20",
            "fg_strong": "#141218",
            "dim": "#49454f",
            "acc": "#6750a4",
            "acc2": "#4f378b",
            "accbg": "#eaddff",
            "ok": "#146c2e",
            "ok_text": "#0f5223",
            "okbg": "#c4eed0",
            "warn": "#b06000",
            "warnbg": "#fef0d6",
            "bad": "#b3261e",
            "bad_text": "#8c1d18",
            "badbg": "#f9dedc",
            "dev_grad": "linear-gradient(135deg, #f3edf7 0%, #eaddff 100%)",
        },
    },
    "material_ocean": {
        "name": "Material You Ocean Teal",
        "swatch": "#80d4ea",
        "dark": {
            "bg": "#0f1416",
            "surf": "#191c1e",
            "surf2": "#232a2d",
            "side": "#13181a",
            "line": "#2f373b",
            "line_strong": "#3f484c",
            "fg": "#e1e3e4",
            "fg_strong": "#f5fafc",
            "dim": "#bfc8cc",
            "acc": "#4fd8eb",
            "acc2": "#97f0ff",
            "accbg": "#004f58",
            "ok": "#7fda99",
            "ok_text": "#a6f2ba",
            "okbg": "#143822",
            "warn": "#ffb951",
            "warnbg": "#382910",
            "bad": "#ffb4ab",
            "bad_text": "#ffdad6",
            "badbg": "#690005",
            "dev_grad": "linear-gradient(135deg, #172024 0%, #191c1e 60%, #232a2d 100%)",
        },
        "light": {
            "bg": "#f5fafc",
            "surf": "#eff4f6",
            "surf2": "#e3eaec",
            "side": "#eef3f5",
            "line": "#dbe4e7",
            "line_strong": "#bfc8cc",
            "fg": "#191c1e",
            "fg_strong": "#0f1416",
            "dim": "#3f484c",
            "acc": "#006874",
            "acc2": "#004f58",
            "accbg": "#97f0ff",
            "ok": "#146c2e",
            "ok_text": "#0f5223",
            "okbg": "#c4eed0",
            "warn": "#b06000",
            "warnbg": "#fef0d6",
            "bad": "#ba1a1a",
            "bad_text": "#93000a",
            "badbg": "#ffdad6",
            "dev_grad": "linear-gradient(135deg, #eff4f6 0%, #cde7ef 100%)",
        },
    },
    "carbon": {
        "name": "Linear Carbon & Sky",
        "swatch": "#0ea5e9",
        "dark": {
            "bg": "#101217",
            "surf": "#161920",
            "surf2": "#1c2029",
            "side": "#13151b",
            "line": "#232733",
            "line_strong": "#2e3444",
            "fg": "#e4e7ef",
            "fg_strong": "#f8fafc",
            "dim": "#8b93a7",
            "acc": "#0ea5e9",
            "acc2": "#38bdf8",
            "accbg": "#112638",
            "ok": "#10b981",
            "ok_text": "#34d399",
            "okbg": "#102824",
            "warn": "#f59e0b",
            "warnbg": "#2b2112",
            "bad": "#ef4444",
            "bad_text": "#fca5a5",
            "badbg": "#2c161b",
            "dev_grad": "linear-gradient(135deg, #171b24 0%, #161922 35%, #141820 70%, #13161e 100%)",
        },
        "light": {
            "bg": "#f7f8fa",
            "surf": "#ffffff",
            "surf2": "#f0f2f6",
            "side": "#f1f3f7",
            "line": "#e2e5ec",
            "line_strong": "#cfd5e0",
            "fg": "#1e232d",
            "fg_strong": "#0f131a",
            "dim": "#606878",
            "acc": "#0284c7",
            "acc2": "#0369a1",
            "accbg": "#e0f2fe",
            "ok": "#059669",
            "ok_text": "#047857",
            "okbg": "#d1fae5",
            "warn": "#d97706",
            "warnbg": "#fef3c7",
            "bad": "#dc2626",
            "bad_text": "#b91c1c",
            "badbg": "#fee2e2",
            "dev_grad": "none",
        },
    },
    "adwaita": {
        "name": "GNOME Adwaita Neutral",
        "swatch": "#3584e4",
        "dark": {
            "bg": "#1e1e1e",
            "surf": "#282828",
            "surf2": "#303030",
            "side": "#222222",
            "line": "#363636",
            "line_strong": "#454545",
            "fg": "#ebebeb",
            "fg_strong": "#ffffff",
            "dim": "#9e9e9e",
            "acc": "#3584e4",
            "acc2": "#78aeed",
            "accbg": "#22344a",
            "ok": "#2ec27e",
            "ok_text": "#57e389",
            "okbg": "#1e372b",
            "warn": "#e5a50a",
            "warnbg": "#352c15",
            "bad": "#e01b24",
            "bad_text": "#f66151",
            "badbg": "#381c1e",
            "dev_grad": "none",
        },
        "light": {
            "bg": "#fafafa",
            "surf": "#ffffff",
            "surf2": "#f0f0f0",
            "side": "#f3f3f3",
            "line": "#e0e0e0",
            "line_strong": "#cccccc",
            "fg": "#2e2e2e",
            "fg_strong": "#121212",
            "dim": "#6e6e6e",
            "acc": "#1c71d8",
            "acc2": "#1a5fb4",
            "accbg": "#dceafe",
            "ok": "#1b8553",
            "ok_text": "#166c43",
            "okbg": "#d9f5e6",
            "warn": "#b5830a",
            "warnbg": "#fdf0cc",
            "bad": "#c01c28",
            "bad_text": "#a51d2d",
            "badbg": "#fce1e3",
            "dev_grad": "none",
        },
    },
    "nord": {
        "name": "Nordic Frost",
        "swatch": "#88c0d0",
        "dark": {
            "bg": "#202530",
            "surf": "#282e3b",
            "surf2": "#303746",
            "side": "#232834",
            "line": "#353d4e",
            "line_strong": "#434c5e",
            "fg": "#e5e9f0",
            "fg_strong": "#eceff4",
            "dim": "#9aa5b8",
            "acc": "#5e81ac",
            "acc2": "#88c0d0",
            "accbg": "#2b3b4b",
            "ok": "#8fbcbb",
            "ok_text": "#a3be8c",
            "okbg": "#2a3733",
            "warn": "#ebcb8b",
            "warnbg": "#38342d",
            "bad": "#bf616a",
            "bad_text": "#d08770",
            "badbg": "#38282f",
            "dev_grad": "none",
        },
        "light": {
            "bg": "#eceff4",
            "surf": "#ffffff",
            "surf2": "#e5e9f0",
            "side": "#e5e9f0",
            "line": "#d8dee9",
            "line_strong": "#c2cad8",
            "fg": "#2e3440",
            "fg_strong": "#1f242d",
            "dim": "#4c566a",
            "acc": "#5e81ac",
            "acc2": "#44668f",
            "accbg": "#dbe6f2",
            "ok": "#4f7a5a",
            "ok_text": "#3d6347",
            "okbg": "#e1eee4",
            "warn": "#b48ead",
            "warnbg": "#f5ebd6",
            "bad": "#bf616a",
            "bad_text": "#9e444d",
            "badbg": "#f7e2e4",
            "dev_grad": "none",
        },
    },
    "mocha": {
        "name": "Catppuccin Mocha",
        "swatch": "#89b4fa",
        "dark": {
            "bg": "#181825",
            "surf": "#1e1e2e",
            "surf2": "#262639",
            "side": "#151521",
            "line": "#313244",
            "line_strong": "#45475a",
            "fg": "#cdd6f4",
            "fg_strong": "#f5e0dc",
            "dim": "#a6adc8",
            "acc": "#89b4fa",
            "acc2": "#b4befe",
            "accbg": "#242942",
            "ok": "#a6e3a1",
            "ok_text": "#a6e3a1",
            "okbg": "#1f302c",
            "warn": "#f9e2af",
            "warnbg": "#332f2c",
            "bad": "#f38ba8",
            "bad_text": "#f38ba8",
            "badbg": "#34212f",
            "dev_grad": "none",
        },
        "light": {
            "bg": "#eff1f5",
            "surf": "#ffffff",
            "surf2": "#e6e9ef",
            "side": "#e6e9ef",
            "line": "#ccd0da",
            "line_strong": "#bcc0cc",
            "fg": "#4c4f69",
            "fg_strong": "#1e1e2e",
            "dim": "#6c6f85",
            "acc": "#1e66f5",
            "acc2": "#1e66f5",
            "accbg": "#dce6fd",
            "ok": "#40a02b",
            "ok_text": "#2f7d1f",
            "okbg": "#dff3da",
            "warn": "#df8e1d",
            "warnbg": "#faefd8",
            "bad": "#d20f39",
            "bad_text": "#b50d31",
            "badbg": "#fadbe1",
            "dev_grad": "none",
        },
    },
    "emerald": {
        "name": "Pure Monochrome & Emerald",
        "swatch": "#10b981",
        "dark": {
            "bg": "#0c0d0e",
            "surf": "#141517",
            "surf2": "#1b1d20",
            "side": "#101113",
            "line": "#23262a",
            "line_strong": "#31353b",
            "fg": "#e4e6ea",
            "fg_strong": "#ffffff",
            "dim": "#8a8f98",
            "acc": "#10b981",
            "acc2": "#34d399",
            "accbg": "#112822",
            "ok": "#10b981",
            "ok_text": "#34d399",
            "okbg": "#112822",
            "warn": "#f59e0b",
            "warnbg": "#2b2010",
            "bad": "#ef4444",
            "bad_text": "#fca5a5",
            "badbg": "#2d1618",
            "dev_grad": "none",
        },
        "light": {
            "bg": "#f6f7f8",
            "surf": "#ffffff",
            "surf2": "#ededf0",
            "side": "#f0f1f3",
            "line": "#e0e2e6",
            "line_strong": "#cacdd4",
            "fg": "#1f2226",
            "fg_strong": "#0b0c0e",
            "dim": "#636870",
            "acc": "#059669",
            "acc2": "#047857",
            "accbg": "#d1fae5",
            "ok": "#059669",
            "ok_text": "#047857",
            "okbg": "#d1fae5",
            "warn": "#d97706",
            "warnbg": "#fef3c7",
            "bad": "#dc2626",
            "bad_text": "#b91c1c",
            "badbg": "#fee2e2",
            "dev_grad": "none",
        },
    },
}

_ACTIVE_DARK_MODE: bool = True
_ACTIVE_UI_THEME: str = "material"
_ACTIVE_COLOR_PALETTE: str = "material_you"
_ACTIVE_ICON_STYLE: str = "minimal"

def get_active_dark_mode() -> bool:
    return _ACTIVE_DARK_MODE

def set_active_dark_mode(dark: bool) -> bool:
    global _ACTIVE_DARK_MODE
    _ACTIVE_DARK_MODE = bool(dark)
    return _ACTIVE_DARK_MODE

def get_active_ui_theme() -> str:
    return _ACTIVE_UI_THEME

def set_active_ui_theme(theme_id: str) -> str:
    global _ACTIVE_UI_THEME
    tid = str(theme_id or "tray").strip().lower()
    if tid not in ("material", "tray", "aurora", "gtk"):
        tid = "tray"
    _ACTIVE_UI_THEME = tid
    return _ACTIVE_UI_THEME

def get_active_color_palette() -> str:
    return _ACTIVE_COLOR_PALETTE

def set_active_color_palette(palette_id: str) -> str:
    global _ACTIVE_COLOR_PALETTE
    pid = str(palette_id or "carbon").strip().lower()
    if pid not in COLOR_PALETTES:
        pid = "carbon"
    _ACTIVE_COLOR_PALETTE = pid
    return _ACTIVE_COLOR_PALETTE

def get_active_icon_style() -> str:
    return _ACTIVE_ICON_STYLE

def set_active_icon_style(style_id: str) -> str:
    global _ACTIVE_ICON_STYLE
    sid = str(style_id or "minimal").strip().lower()
    _ACTIVE_ICON_STYLE = "toned" if sid == "toned" else "minimal"
    return _ACTIVE_ICON_STYLE

def get_active_palette_tokens(palette_id: str | None = None, dark: bool | None = None) -> dict[str, str]:
    pid = str(palette_id or _ACTIVE_COLOR_PALETTE).strip().lower()
    if pid not in COLOR_PALETTES:
        pid = "carbon"
    is_dark = _ACTIVE_DARK_MODE if dark is None else bool(dark)
    mode_key = "dark" if is_dark else "light"
    tokens = COLOR_PALETTES[pid][mode_key]
    return dict(tokens) if isinstance(tokens, dict) else {}

def build_themed_gtk_css(
    ui_theme: str | None = None,
    palette_id: str | None = None,
    dark: bool | None = None,
) -> bytes:
    tid = str(ui_theme or _ACTIVE_UI_THEME).strip().lower()
    if tid not in ("material", "tray", "aurora", "gtk"):
        tid = "tray"
    t = get_active_palette_tokens(palette_id, dark)
    bg = t["bg"]
    surf = t["surf"]
    surf2 = t["surf2"]
    side = t["side"]
    line = t["line"]
    line_strong = t["line_strong"]
    fg = t["fg"]
    fg_strong = t["fg_strong"]
    dim = t["dim"]
    acc = t["acc"]
    acc_light = t["acc2"]
    accbg = t["accbg"]
    ok = t["ok"]
    ok_text = t["ok_text"]
    okbg = t["okbg"]
    warn = t["warn"]
    warnbg = t["warnbg"]
    bad = t["bad"]
    bad_text = t["bad_text"]
    badbg = t["badbg"]
    dev_grad = t.get("dev_grad", "none")

    if tid == "material":
        radius_win = "28px"
        radius_card = "24px"
        radius_tile = "22px"
        radius_btn = "999px"
        nav_btn_rad = "999px"
        nav_active_css = f"background-color: {accbg}; color: {acc_light}; font-weight: 700; border: none; border-radius: 999px;"
        active_tile_css = (
            f"background-color: {accbg}; "
            f"border: 1.5px solid {acc}; color: {acc_light};"
        )
        active_rail_css = (
            f"background-color: {accbg}; "
            f"border: 1.5px solid {acc};"
        )
    elif tid == "tray":
        radius_win = "20px"
        radius_card = "18px"
        radius_tile = "16px"
        radius_btn = "12px"
        nav_btn_rad = "14px"
        nav_active_css = f"background-color: {accbg}; color: {acc_light}; font-weight: 700; border: none; border-radius: {nav_btn_rad};"
        active_tile_css = f"background-color: {accbg}; border: 1px solid {acc}; color: {acc_light};"
        active_rail_css = f"background-color: {accbg}; border: 1px solid {acc};"
    elif tid == "aurora":
        radius_win = "24px"
        radius_card = "20px"
        radius_tile = "18px"
        radius_btn = "999px"
        nav_btn_rad = "999px"
        nav_active_css = f"background-color: {accbg}; color: {acc_light}; font-weight: 700; border-radius: 999px;"
        active_tile_css = (
            f"background-color: {okbg}; "
            f"background-image: linear-gradient(135deg, {accbg} 0%, {okbg} 100%); "
            f"border: 1px solid {ok}; color: {ok_text};"
        )
        active_rail_css = (
            f"background-color: {okbg}; "
            f"background-image: linear-gradient(135deg, {accbg} 0%, {okbg} 100%); "
            f"border: 1px solid {ok};"
        )
    else:
        radius_win = "20px"
        radius_card = "20px"
        radius_tile = "18px"
        radius_btn = "13px"
        nav_btn_rad = "14px"
        nav_active_css = f"background-color: {accbg}; color: {fg_strong}; font-weight: 700; border-left: 4px solid {acc_light}; border-radius: {nav_btn_rad};"
        active_tile_css = f"background-color: {okbg}; border: 2px solid {ok}; color: {ok_text};"
        active_rail_css = f"background-color: {okbg}; border: 1px solid {ok};"

    dev_card_bg_css = (
        f"background-color: {surf}; background-image: {dev_grad};"
        if dev_grad != "none"
        else f"background-color: {surf}; background-image: none;"
    )

    css = f"""
window, .sc-window, dialog, messagedialog, popover, .tray-popover-window, .app-launcher-window {{
    background-color: {bg};
    background-image: none;
    color: {fg};
    font-family: "Inter", "Segoe UI", "Cantarell", "Ubuntu", sans-serif;
    font-size: 13.5px;
}}
.os-titlebar, .tray-headerbar, .app-launcher-header {{
    background-color: {side};
    background-image: none;
    border-bottom: 1px solid {line};
    padding: 7px 14px;
}}
.sidebar {{
    background-color: {side};
    background-image: none;
    border-right: 1px solid {line};
    padding: 14px 10px 12px 10px;
}}
.brand-title {{
    font-size: 16px;
    font-weight: 800;
    color: {fg_strong};
    letter-spacing: -0.3px;
}}
.nav-btn {{
    background-color: transparent;
    background-image: none;
    border: none;
    border-radius: {nav_btn_rad};
    padding: 8px 12px;
    color: {dim};
    font-size: 13.5px;
    font-weight: 600;
    box-shadow: none;
}}
.nav-btn:hover {{
    background-color: {surf2};
    color: {fg_strong};
}}
.nav-btn-active {{
    {nav_active_css}
}}
.sidebar-divider {{
    background-color: {line};
    min-height: 1px;
    margin: 6px 8px;
}}
.pair-qr-btn, .theme-toggle-btn, .min-tray-btn {{
    background-color: {surf2};
    background-image: none;
    border: 1px solid {line_strong};
    border-radius: {radius_btn};
    padding: 5px 10px;
    color: {fg};
    font-size: 12px;
    font-weight: 600;
    box-shadow: none;
}}
.pair-qr-btn:hover, .theme-toggle-btn:hover, .min-tray-btn:hover {{
    border-color: {acc_light};
    color: {fg_strong};
    background-color: {accbg};
}}
.main-area, scrolledwindow, viewport {{
    background-color: {bg};
    background-image: none;
    color: {fg};
}}
.content-area {{
    padding: 14px 18px 18px 18px;
}}
.device-card {{
    {dev_card_bg_css}
    border-radius: {radius_card};
    border: 1px solid {line};
    padding: 14px 18px;
}}
.device-heading {{
    font-size: 22px;
    font-weight: 800;
    color: {fg_strong};
    letter-spacing: -0.4px;
}}
.conn-pill-connected {{
    background-color: {okbg};
    color: {ok_text};
    border: 1px solid {ok};
    border-radius: 999px;
    padding: 3px 11px;
    font-size: 12.5px;
    font-weight: 700;
}}
.conn-pill-waiting {{
    background-color: {warnbg};
    color: {warn};
    border: 1px solid {warn};
    border-radius: 999px;
    padding: 3px 11px;
    font-size: 12.5px;
    font-weight: 700;
}}
.metric-text {{
    font-size: 13px;
    color: {dim};
    font-weight: 600;
}}
.sc-card {{
    background-color: {surf};
    background-image: none;
    border: 1px solid {line};
    border-radius: {radius_card};
    padding: 12px 14px;
}}
.panel-h2 {{
    font-size: 14px;
    font-weight: 700;
    color: {fg_strong};
}}
.panel-sub {{
    font-size: 12px;
    color: {dim};
}}
.eyebrow {{
    font-size: 10.5px;
    font-weight: 700;
    color: {acc_light};
    letter-spacing: 0.6px;
}}
.page-h1 {{
    font-size: 20px;
    font-weight: 800;
    color: {fg_strong};
}}
.page-sub {{
    font-size: 12.5px;
    color: {dim};
}}
.feature-rail-item {{
    background-color: transparent;
    background-image: none;
    border: 1px solid transparent;
    border-radius: 14px;
    padding: 5px 8px;
}}
.feature-rail-item:hover {{
    background-color: {surf2};
}}
.feature-rail-item-active {{
    {active_rail_css}
    border-radius: 14px;
    padding: 5px 8px;
}}
.feature-rail-title {{
    font-size: 13px;
    font-weight: 600;
    color: {fg_strong};
}}
.feature-rail-title-active {{
    font-size: 13px;
    font-weight: 700;
    color: {acc_light};
}}
.feature-rail-state {{
    font-size: 10.5px;
    font-weight: 600;
    color: {ok_text};
}}
.feature-tile-card {{
    background-color: {surf};
    background-image: none;
    border: 1px solid {line};
    border-radius: {radius_tile};
    padding: 12px 10px;
}}
.feature-tile-card:hover {{
    background-color: {surf2};
    border-color: {line_strong};
}}
.feature-tile-card-active {{
    {active_tile_css}
    border-radius: {radius_tile};
    padding: 12px 10px;
}}
.feature-tile-title {{
    font-size: 13.5px;
    font-weight: 700;
    color: {fg_strong};
}}
.feature-tile-title-active {{
    font-size: 13.5px;
    font-weight: 700;
    color: {acc_light};
}}
.corner-cancel-btn {{
    background-color: {badbg};
    background-image: none;
    color: {bad_text};
    border: 1px solid {bad};
    border-radius: 14px;
    min-width: 18px;
    min-height: 18px;
    padding: 0px 5px;
    font-size: 11px;
    font-weight: 800;
    box-shadow: none;
}}
.corner-cancel-btn:hover {{
    background-color: {bad};
    color: #ffffff;
}}
.clear-btn {{
    background-color: {surf2};
    background-image: none;
    border: 1px solid {line_strong};
    border-radius: {radius_btn};
    padding: 3px 9px;
    color: {fg};
    font-size: 11.5px;
    font-weight: 600;
}}
.clear-btn:hover {{
    border-color: {acc_light};
    color: {fg_strong};
}}
.notif-row {{
    border-bottom: 1px solid {line};
    padding: 6px 4px;
}}
.notif-title {{
    font-size: 12.5px;
    font-weight: 700;
    color: {fg_strong};
}}
.notif-body {{
    font-size: 11.5px;
    color: {dim};
}}
.notif-time {{
    font-size: 11px;
    color: {dim};
}}
.dropzone {{
    background-color: {bg};
    background-image: none;
    border: 1.5px dashed {line_strong};
    border-radius: {radius_tile};
    padding: 12px 10px;
    color: {dim};
}}
.dropzone:hover {{
    border-color: {acc_light};
    color: {fg_strong};
}}
.dropzone-title {{
    font-size: 14px;
    font-weight: 700;
    color: {fg_strong};
}}
.dropzone-sub {{
    font-size: 12px;
    color: {dim};
}}
"""
    css_controls = _build_controls_and_forms_css(t, radius_btn, radius_card, radius_tile)
    css_stream_and_tray = _build_stream_and_tray_css(t, radius_card, radius_tile, active_tile_css, dev_card_bg_css)
    return (css + css_controls + css_stream_and_tray).encode("utf-8")

def _build_controls_and_forms_css(t: dict[str, str], radius_btn: str, radius_card: str, radius_tile: str) -> str:
    bg, surf, surf2, side = t["bg"], t["surf"], t["surf2"], t["side"]
    line, line_strong, fg, fg_strong, dim = t["line"], t["line_strong"], t["fg"], t["fg_strong"], t["dim"]
    acc, acc_light, accbg = t["acc"], t["acc2"], t["accbg"]
    ok, ok_text, okbg = t["ok"], t["ok_text"], t["okbg"]
    bad, bad_text, badbg = t["bad"], t["bad_text"], t["badbg"]
    return f"""
.primary-btn {{
    background-color: {acc};
    background-image: none;
    color: #ffffff;
    border: 1px solid {acc};
    border-radius: {radius_btn};
    padding: 6px 13px;
    font-size: 12.5px;
    font-weight: 600;
    box-shadow: none;
}}
.primary-btn:hover {{
    background-color: {acc_light};
    border-color: {acc_light};
    color: #ffffff;
}}
.secondary-btn {{
    background-color: {surf2};
    background-image: none;
    color: {fg};
    border: 1px solid {line_strong};
    border-radius: {radius_btn};
    padding: 5px 11px;
    font-size: 12px;
    font-weight: 600;
    box-shadow: none;
}}
.secondary-btn:hover {{
    border-color: {acc_light};
    color: {fg_strong};
}}
.danger-btn {{
    background-color: {badbg};
    background-image: none;
    color: {bad_text};
    border: 1px solid {bad};
    border-radius: {radius_btn};
    padding: 4px 10px;
    font-size: 11.5px;
    font-weight: 700;
    box-shadow: none;
}}
.danger-btn:hover {{
    background-color: {bad};
    color: #ffffff;
}}
.shared-badge {{
    background-color: {okbg};
    color: {ok_text};
    border: 1px solid {ok};
    border-radius: 999px;
    padding: 3px 9px;
    font-size: 11px;
    font-weight: 600;
}}
.sent-badge {{
    background-color: {accbg};
    color: {acc_light};
    border: 1px solid {acc};
    border-radius: 999px;
    padding: 2px 8px;
    font-size: 11px;
    font-weight: 700;
}}
.phonefs-scroll {{
    background-color: {surf};
    border: 1px solid {line};
    border-radius: {radius_card};
}}
.phonefs-scroll viewport {{
    background-color: {surf};
}}
treeview.view, .phonefs-treeview {{
    background-color: {surf};
    color: {fg};
    font-size: 13px;
    padding: 5px 8px;
}}
treeview.view:hover, .phonefs-treeview:hover {{
    background-color: {surf2};
    color: {fg_strong};
}}
treeview.view:selected, .phonefs-treeview:selected {{
    background-color: {acc};
    color: #ffffff;
}}
treeview.view header button {{
    background-color: {side};
    background-image: none;
    color: {dim};
    font-weight: 700;
    font-size: 11.5px;
    border: none;
    border-bottom: 1px solid {line};
    border-right: 1px solid {line};
    padding: 6px 8px;
}}
.quick-path-pill, .cat-pill {{
    background-color: {bg};
    background-image: none;
    color: {dim};
    border: 1px solid {line};
    border-radius: 999px;
    padding: 3px 10px;
    font-size: 11.5px;
    font-weight: 600;
}}
.quick-path-pill:hover, .cat-pill:hover {{
    border-color: {acc_light};
    color: {fg_strong};
}}
.cat-pill-active {{
    background-color: {accbg};
    background-image: none;
    color: {acc_light};
    border: 1px solid {acc};
    border-radius: 999px;
    padding: 3px 10px;
    font-size: 11.5px;
    font-weight: 700;
}}
button.gallery-photo-tile {{
    padding: 0px;
    margin: 0px;
    min-width: 120px;
    min-height: 88px;
    border-radius: 14px;
    background-color: {surf2};
    background-image: none;
    border: 1px solid {line};
}}
button.gallery-photo-tile:hover {{
    border-color: {acc_light};
}}
entry, .sc-entry {{
    background-color: {bg};
    background-image: none;
    color: {fg_strong};
    border: 1px solid {line_strong};
    border-radius: {radius_btn};
    padding: 6px 10px;
    caret-color: {acc_light};
}}
entry:focus, .sc-entry:focus {{
    border-color: {acc_light};
}}
combobox button.combo {{
    background-color: {bg};
    background-image: none;
    color: {fg_strong};
    border: 1px solid {line_strong};
    border-radius: 12px;
    padding: 5px 10px;
}}
combobox button.combo:hover {{
    border-color: {acc_light};
}}
menu, .context-menu {{
    background-color: {surf};
    color: {fg};
    border: 1px solid {line_strong};
}}
menuitem {{
    color: {fg};
    padding: 6px 12px;
}}
menuitem:hover {{
    background-color: {accbg};
    color: {acc_light};
}}
.edge-hud-pill {{
    background-color: {surf};
    color: {fg_strong};
    border: 1px solid {acc};
    border-radius: 16px;
    padding: 8px 16px;
    font-size: 13px;
    font-weight: 600;
}}
button, togglebutton, spinbutton {{
    border-radius: {radius_btn};
}}
frame, frame > border {{
    border-radius: {radius_card};
    border-color: {line};
}}
popover, menu, .context-menu {{
    border-radius: 14px;
    padding: 4px;
}}
menuitem {{
    border-radius: 9px;
}}
scrollbar slider {{
    border-radius: 999px;
    min-width: 7px;
    min-height: 7px;
}}
switch {{
    border-radius: 999px;
}}
switch slider {{
    border-radius: 999px;
}}
checkbutton check {{
    border-radius: 6px;
}}
radiobutton radio {{
    border-radius: 999px;
}}
listbox, notebook {{
    border-radius: {radius_card};
}}
listbox row, treeview.view:selected, .phonefs-treeview:selected {{
    border-radius: 10px;
}}
"""

def _build_stream_and_tray_css(t: dict[str, str], radius_card: str, radius_tile: str, active_tile_css: str, dev_card_bg_css: str) -> str:
    bg, surf, surf2, side = t["bg"], t["surf"], t["surf2"], t["side"]
    line, line_strong, fg, fg_strong, dim = t["line"], t["line_strong"], t["fg"], t["fg_strong"], t["dim"]
    acc, acc_light, accbg = t["acc"], t["acc2"], t["accbg"]
    ok, okbg, bad = t["ok"], t["okbg"], t["bad"]
    radius_btn = "9px"
    return f"""
.fw-toolbar, .fw-headerbar {{
    background-color: {side};
    border-bottom: 1px solid {line};
    padding: 2px 6px;
}}
.fw-headerbar button {{
    min-width: 16px;
    min-height: 28px;
    padding: 1px 6px;
    margin: 0px 1px;
    color: {fg};
}}
.fw-headerbar label {{
    color: {fg};
    font-size: 12px;
}}
.fw-headerbar .fw-title {{
    font-weight: 700;
    font-size: 12.5px;
    color: {fg_strong};
}}
.fw-headerbar .fw-sub {{
    font-size: 11px;
    color: {dim};
}}
.fw-ctrl-btn {{
    background-color: {surf2};
    color: {fg};
    border: 1px solid {line_strong};
    border-radius: 10px;
    padding: 2px 7px;
    min-height: 22px;
    min-width: 24px;
    font-size: 11.5px;
    font-weight: 600;
}}
.fw-ctrl-btn:hover {{
    border-color: {acc_light};
}}
.fw-end-btn {{
    background-color: {bad};
    color: #ffffff;
    border: 1px solid {bad};
    border-radius: 10px;
    padding: 2px 8px;
    min-height: 22px;
    font-size: 11.5px;
    font-weight: 700;
}}
.fw-video-canvas {{
    background-color: #000000;
    margin: 0px;
    padding: 0px;
}}
.fw-navbar {{
    background-color: {side};
    border-top: 1px solid {line};
    padding: 4px 8px;
}}
.fw-nav-btn {{
    background-color: {surf};
    color: {fg};
    border: 1px solid {line_strong};
    border-radius: 14px;
    padding: 2px 14px;
    min-height: 24px;
    min-width: 40px;
    font-size: 12px;
    font-weight: 600;
}}
.fw-nav-btn:hover {{
    border-color: {acc_light};
    color: {fg_strong};
}}
.setup-header-bar {{
    padding: 2px 0px 6px 0px;
}}
.setup-back-btn {{
    background-color: {surf2};
    background-image: none;
    color: {fg_strong};
    border: 1px solid {line_strong};
    border-radius: 12px;
    padding: 4px 10px;
    font-size: 14px;
    font-weight: 700;
}}
.setup-back-btn:hover {{
    border-color: {acc_light};
}}
.setup-info-btn {{
    background-color: {surf2};
    background-image: none;
    color: {acc_light};
    border: 1px solid {line_strong};
    border-radius: 999px;
    min-width: 26px;
    min-height: 26px;
    padding: 2px 6px;
    font-size: 12px;
    font-weight: 700;
}}
.setup-option-card {{
    background-color: {surf};
    background-image: none;
    border: 1px solid {line};
    border-radius: {radius_tile};
    padding: 12px 12px;
}}
.setup-option-card:hover {{
    background-color: {surf2};
    border-color: {line_strong};
}}
.setup-option-card-selected {{
    background-color: {accbg};
    background-image: none;
    border: 2px solid {acc};
    border-radius: {radius_tile};
    padding: 12px 12px;
}}
.setup-option-title {{
    font-size: 13.5px;
    font-weight: 700;
    color: {fg_strong};
}}
.setup-option-sub {{
    font-size: 11.5px;
    color: {dim};
}}
.setup-start-btn {{
    background-color: {acc};
    background-image: none;
    color: #ffffff;
    border: 1px solid {acc};
    border-radius: {radius_btn};
    padding: 9px 24px;
    font-size: 13.5px;
    font-weight: 700;
}}
.setup-start-btn:hover {{
    background-color: {acc_light};
}}
.tray-dev-card {{
    {dev_card_bg_css}
    border: 1px solid {line};
    border-radius: {radius_card};
    padding: 9px 11px;
}}
.tray-dev-av {{
    background-color: {accbg};
    color: {acc_light};
    border: 1px solid {line};
    border-radius: 999px;
    min-width: 36px;
    min-height: 36px;
    font-weight: 800;
    font-size: 14px;
}}
.tray-seg-box {{
    background-color: {bg};
    border: 1px solid {line_strong};
    border-radius: 12px;
    padding: 0px;
}}
.tray-seg-btn {{
    background-color: transparent;
    background-image: none;
    border: none;
    border-radius: 14px;
    padding: 3px 8px;
    color: {dim};
    font-size: 11.5px;
    font-weight: 600;
}}
.tray-seg-btn-active {{
    background-color: {accbg};
    background-image: none;
    color: {acc_light};
    border-radius: 14px;
    padding: 3px 8px;
    font-size: 11.5px;
    font-weight: 700;
}}
.tray-run-box {{
    background-color: {okbg};
    border: 1px solid {ok};
    border-radius: {radius_tile};
    padding: 6px 10px;
}}
.tray-tile {{
    background-color: {surf};
    background-image: none;
    border: 1px solid {line};
    border-radius: {radius_tile};
    padding: 9px 6px;
    color: {fg_strong};
    font-size: 12px;
    font-weight: 600;
}}
.tray-tile:hover {{
    background-color: {surf2};
    border-color: {line_strong};
}}
.tray-tile-active {{
    {active_tile_css}
    border-radius: {radius_tile};
    padding: 9px 6px;
    font-size: 12px;
    font-weight: 700;
}}
.tray-nav-bar {{
    background-color: {side};
    border-top: 1px solid {line};
    padding: 3px 4px;
}}
.tray-nav-btn {{
    background-color: transparent;
    background-image: none;
    border: none;
    border-radius: 14px;
    padding: 6px 2px;
    color: {dim};
    font-size: 11px;
    font-weight: 600;
}}
.tray-nav-btn:hover {{
    background-color: {surf2};
    color: {fg_strong};
}}
.tray-nav-btn-active {{
    background-color: transparent;
    background-image: none;
    color: {acc_light};
    border-radius: 14px;
    padding: 6px 2px;
    font-size: 11px;
    font-weight: 700;
}}
.tray-icon-badge {{
    background-color: {surf2};
    color: {acc_light};
    border: 1px solid {line_strong};
    border-radius: 14px;
    min-width: 30px;
    min-height: 30px;
    padding: 3px;
    font-weight: 700;
}}
.app-squircle-minimal {{
    background-color: {surf2};
    color: {fg_strong};
    border: 1px solid {line_strong};
    border-radius: 14px;
    min-width: 38px;
    min-height: 38px;
    font-weight: 700;
}}
.app-squircle-streaming {{
    background-color: {acc};
    color: #ffffff;
    border: 1px solid {acc};
    border-radius: 14px;
    min-width: 38px;
    min-height: 38px;
    font-weight: 700;
}}
.app-launcher-tile {{
    background-color: {bg};
    background-image: none;
    border: 1px solid {line};
    border-radius: {radius_tile};
    padding: 8px 6px;
}}
.app-launcher-tile:hover {{
    background-color: {surf2};
    border-color: {line_strong};
}}
.app-launcher-tile-running {{
    background-color: {accbg};
    background-image: none;
    border: 1px solid {acc};
    border-radius: {radius_tile};
    padding: 8px 6px;
}}
.running-app-row {{
    background-color: {bg};
    border: 1px solid {line_strong};
    border-radius: {radius_tile};
    padding: 9px 12px;
}}
"""

__all__ = [
    "COLOR_PALETTES",
    "GTK_CSS_DARK",
    "GTK_CSS_LIGHT",
    "UI_THEMES",
    "_ACTIVE_COLOR_PALETTE",
    "_ACTIVE_DARK_MODE",
    "_ACTIVE_ICON_STYLE",
    "_ACTIVE_UI_THEME",
    "build_themed_gtk_css",
    "get_active_color_palette",
    "get_active_dark_mode",
    "get_active_icon_style",
    "get_active_palette_tokens",
    "get_active_ui_theme",
    "set_active_color_palette",
    "set_active_dark_mode",
    "set_active_icon_style",
    "set_active_ui_theme",
]
