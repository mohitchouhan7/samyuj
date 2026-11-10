from __future__ import annotations

import contextlib
from dataclasses import asdict, dataclass, field, fields
import json
import os
from pathlib import Path
from typing import Any

from .paths import get_xdg_paths

SETTINGS_DEFAULTS: dict[str, Any] = {
    "incoming_files_dir": str(Path.home() / "Downloads" / "Samyuj"),
    "ui_theme": "aurora",
    "color_palette": "mocha",
    "connect_usb": True,
    "connect_wifi": True,
    "start_background": False,
    "follow_system_theme": False,
    "wallpaper_sync": True,
    "apply_desktop_wallpaper": False,
    "cross_control_edge": "left",
    "auto_mount_phone_nfs": True,
    "uv_swap": True,
    "ss_share_type": "mirror",
    "scroll_mode": "touch",
    "scroll_step": 0.12,
    "scroll_step_user_set": False,
    "exclude_battery_saver": True,
    "background_eco_mode": True,
    "background_fps": 10.0,
    "hidden_fps": 1.0,
    "lowlight_boost": True,
    "lowlight_gamma_max": 1.9,
    "lowlight_luma_threshold": 84.0,
    "smooth_scrolling": True,
    "phone_at_a_glance": False,
    "usb_addr_order": "dhcp,static-p2p",
    "extra_phone_vids": [],
    "extra_smartconnect_pkgs": [],
    "demo_mode": False,
}

@dataclass
class UserSettings:

    incoming_files_dir: str = field(
        default_factory=lambda: str(Path.home() / "Downloads" / "Samyuj")
    )
    ui_theme: str = "aurora"
    color_palette: str = "mocha"
    connect_usb: bool = True
    connect_wifi: bool = True
    start_background: bool = False
    follow_system_theme: bool = False
    wallpaper_sync: bool = True
    apply_desktop_wallpaper: bool = False
    cross_control_edge: str = "left"
    auto_mount_phone_nfs: bool = True
    uv_swap: bool = True
    ss_share_type: str = "mirror"
    scroll_mode: str = "touch"
    scroll_step: float = 0.12
    scroll_step_user_set: bool = False
    exclude_battery_saver: bool = True
    background_eco_mode: bool = True
    background_fps: float = 10.0
    hidden_fps: float = 1.0
    lowlight_boost: bool = True
    lowlight_gamma_max: float = 1.9
    lowlight_luma_threshold: float = 84.0
    smooth_scrolling: bool = True
    phone_at_a_glance: bool = False
    usb_addr_order: str = "dhcp,static-p2p"
    extra_phone_vids: list[str] = field(default_factory=list)
    extra_smartconnect_pkgs: list[str] = field(default_factory=list)
    demo_mode: bool = False
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        ext = d.pop("extras", {}) or {}
        return {**ext, **d}

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "UserSettings":
        if not isinstance(raw, dict):
            return cls()
        known_names = {f.name for f in fields(cls) if f.name != "extras"}
        kw: dict[str, Any] = {}
        ext: dict[str, Any] = {}
        for k, v in raw.items():
            if k in known_names:
                kw[k] = v
            else:
                ext[k] = v
        return cls(**kw, extras=ext)

def settings_file_path(state_dir: str | Path | None = None) -> Path:
    base = Path(state_dir) if state_dir is not None else get_xdg_paths("samyuj").data_dir
    return base / "user_settings.json"

def load_user_settings(state_dir: str | Path | None = None) -> dict[str, Any]:
    merged = dict(SETTINGS_DEFAULTS)
    p = settings_file_path(state_dir)
    with contextlib.suppress(OSError, json.JSONDecodeError, ValueError, TypeError):
        if p.is_file():
            loaded = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                merged.update(loaded)
    if (
        merged.get("scroll_step") is not None
        and abs(float(merged["scroll_step"]) - 0.055) < 1e-6
        and not merged.get("scroll_step_user_set")
    ):
        merged["scroll_step"] = 0.12
    return merged

def save_user_settings(
    settings: dict[str, Any],
    state_dir: str | Path | None = None,
) -> Path:
    p = settings_file_path(state_dir)
    with contextlib.suppress(OSError):
        p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(p.parent, 0o700)
        p.write_text(json.dumps(settings, indent=2), encoding="utf-8")
        os.chmod(p, 0o600)
    return p
