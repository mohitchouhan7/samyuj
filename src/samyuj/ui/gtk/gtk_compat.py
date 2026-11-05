from __future__ import annotations

import atexit
import contextlib
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

_SHARED_HEADLESS_PROC: subprocess.Popen[bytes] | None = None


def ensure_headless_display(force_restart: bool = False) -> bool:
    global _SHARED_HEADLESS_PROC
    if not force_restart and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return True
    candidates: list[tuple[str, str, list[str]]] = [
        (
            "/usr/bin/Xvfb",
            ":99",
            ["/usr/bin/Xvfb", ":99", "-screen", "0", "1440x900x24", "-nolisten", "tcp"],
        ),
        (
            "/usr/bin/Xvnc",
            ":98",
            ["/usr/bin/Xvnc", ":98", "-rfbport", "-1", "-localhost", "-nolisten", "tcp", "-geometry", "1440x900"],
        ),
    ]
    for bin_path, disp_num, cmd in candidates:
        if not Path(bin_path).is_file():
            continue
        with contextlib.suppress(Exception):
            if _SHARED_HEADLESS_PROC is None or _SHARED_HEADLESS_PROC.poll() is not None:
                _SHARED_HEADLESS_PROC = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                atexit.register(
                    lambda p=_SHARED_HEADLESS_PROC: p.terminate() if p.poll() is None else None
                )
                time.sleep(0.35)
            os.environ["DISPLAY"] = disp_num
            return True
    return False



GTK_MAJOR: int = 3
IS_GTK4: bool = False
_HAS_REAL_GI: bool = False

try:
    import gi as _gi_mod
    _HAS_REAL_GI = True
except ModuleNotFoundError:
    for _sys_dist in ("/usr/lib/python3/dist-packages", f"/usr/lib64/python3.{sys.version_info.minor}/site-packages"):
        if Path(_sys_dist, "gi").is_dir() and _sys_dist not in sys.path:
            sys.path.append(_sys_dist)
            try:
                import gi as _gi_mod
                _HAS_REAL_GI = True
                break
            except Exception:
                sys.path.remove(_sys_dist)

if _HAS_REAL_GI:
    try:
        _use_gtk4 = os.environ.get("SAMYUJ_USE_GTK4", "").strip().lower() in ("1", "true", "yes")
        _force_gtk3 = os.environ.get("SAMYUJ_FORCE_GTK3", "").strip().lower() in ("1", "true", "yes")
        if _use_gtk4 and not _force_gtk3:
            try:
                _gi_mod.require_version("Gtk", "4.0")
                _gi_mod.require_version("Gdk", "4.0")
                GTK_MAJOR = 4
                IS_GTK4 = True
            except Exception:
                _gi_mod.require_version("Gtk", "3.0")
                _gi_mod.require_version("Gdk", "3.0")
                GTK_MAJOR = 3
                IS_GTK4 = False
        else:
            try:
                _gi_mod.require_version("Gtk", "3.0")
                _gi_mod.require_version("Gdk", "3.0")
                GTK_MAJOR = 3
                IS_GTK4 = False
            except Exception:
                _gi_mod.require_version("Gtk", "4.0")
                _gi_mod.require_version("Gdk", "4.0")
                GTK_MAJOR = 4
                IS_GTK4 = True

        _gi_mod.require_version("GdkPixbuf", "2.0")
        from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango
    except Exception:
        _HAS_REAL_GI = False

if not _HAS_REAL_GI:
    from .gtk_headless_stub import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango


def get_gtk_version_string() -> str:
    with contextlib.suppress(Exception):
        return f"{Gtk.get_major_version()}.{Gtk.get_minor_version()}.{Gtk.get_micro_version()}"
    return f"{GTK_MAJOR}.0.0"

def _install_gtk4_shims() -> None:
    if not IS_GTK4:
        return

    orig_init_check = getattr(Gtk, "init_check", None)
    if callable(orig_init_check):
        def _compat_init_check(argv: list[str] | None = None) -> tuple[bool, list[str]]:
            try:
                res = orig_init_check()
                if isinstance(res, tuple):
                    return bool(res[0]), list(res[1])
                return bool(res), list(argv or [])
            except TypeError:
                res = orig_init_check(argv or [])
                if isinstance(res, tuple):
                    return bool(res[0]), list(res[1])
                return bool(res), list(argv or [])
        Gtk.init_check = _compat_init_check

    if not hasattr(Gtk.Box, "pack_start"):
        def _box_pack_start(
            self: Gtk.Box,
            child: Gtk.Widget,
            expand: bool = False,
            fill: bool = True,
            padding: int = 0,
        ) -> None:
            if expand:
                if self.get_orientation() == Gtk.Orientation.HORIZONTAL:
                    child.set_hexpand(True)
                    if fill:
                        child.set_halign(Gtk.Align.FILL)
                else:
                    child.set_vexpand(True)
                    if fill:
                        child.set_valign(Gtk.Align.FILL)
            if padding > 0:
                if self.get_orientation() == Gtk.Orientation.HORIZONTAL:
                    child.set_margin_start(child.get_margin_start() + padding)
                    child.set_margin_end(child.get_margin_end() + padding)
                else:
                    child.set_margin_top(child.get_margin_top() + padding)
                    child.set_margin_bottom(child.get_margin_bottom() + padding)
            self.append(child)

        def _box_pack_end(
            self: Gtk.Box,
            child: Gtk.Widget,
            expand: bool = False,
            fill: bool = True,
            padding: int = 0,
        ) -> None:
            _box_pack_start(self, child, expand, fill, padding)

        Gtk.Box.pack_start = _box_pack_start
        Gtk.Box.pack_end = _box_pack_end

    if not hasattr(Gtk.Widget, "show_all"):
        def _widget_show_all(self: Gtk.Widget) -> None:
            self.set_visible(True)
        Gtk.Widget.show_all = _widget_show_all

    if not hasattr(Gtk.Widget, "set_no_show_all"):
        def _widget_set_no_show_all(self: Gtk.Widget, _no_show: bool) -> None:
            return None
        Gtk.Widget.set_no_show_all = _widget_set_no_show_all

    if not hasattr(Gtk.Widget, "get_children"):
        def _widget_get_children(self: Gtk.Widget) -> list[Gtk.Widget]:
            children: list[Gtk.Widget] = []
            ch = self.get_first_child()
            while ch is not None:
                children.append(ch)
                ch = self.get_next_sibling()
            return children
        Gtk.Widget.get_children = _widget_get_children

    for container_cls in (Gtk.Window, Gtk.ScrolledWindow, Gtk.Button, Gtk.Frame, Gtk.Overlay):
        if hasattr(container_cls, "set_child") and not hasattr(container_cls, "add"):
            container_cls.add = container_cls.set_child

    if not hasattr(Gtk.Button, "set_relief"):
        def _btn_set_relief(self: Gtk.Button, relief: Any) -> None:
            if hasattr(self, "set_has_frame"):
                self.set_has_frame(relief != getattr(Gtk.ReliefStyle, "NONE", 2))
        Gtk.Button.set_relief = _btn_set_relief

    if not hasattr(Gtk, "ReliefStyle"):
        class _ReliefStyle:
            NORMAL = 0
            HALF = 1
            NONE = 2
        Gtk.ReliefStyle = _ReliefStyle

    if not hasattr(Gtk, "WindowPosition"):
        class _WindowPosition:
            NONE = 0
            CENTER = 1
            MOUSE = 2
            CENTER_ALWAYS = 3
            CENTER_ON_PARENT = 4
        Gtk.WindowPosition = _WindowPosition

    if not hasattr(Gtk.Window, "set_position"):
        Gtk.Window.set_position = lambda self, _pos: None
    if not hasattr(Gtk.Window, "set_type_hint"):
        Gtk.Window.set_type_hint = lambda self, _hint: None
    if not hasattr(Gtk.Window, "set_skip_taskbar_hint"):
        def _set_skip_taskbar(self: Gtk.Window, val: bool) -> None:
            setattr(self, "_gtk4_skip_taskbar", bool(val))
        def _get_skip_taskbar(self: Gtk.Window) -> bool:
            return bool(getattr(self, "_gtk4_skip_taskbar", False))
        Gtk.Window.set_skip_taskbar_hint = _set_skip_taskbar
        Gtk.Window.get_skip_taskbar_hint = _get_skip_taskbar
    if not hasattr(Gtk.Window, "set_skip_pager_hint"):
        def _set_skip_pager(self: Gtk.Window, val: bool) -> None:
            setattr(self, "_gtk4_skip_pager", bool(val))
        def _get_skip_pager(self: Gtk.Window) -> bool:
            return bool(getattr(self, "_gtk4_skip_pager", False))
        Gtk.Window.set_skip_pager_hint = _set_skip_pager
        Gtk.Window.get_skip_pager_hint = _get_skip_pager
    if not hasattr(Gtk.Window, "set_keep_above"):
        Gtk.Window.set_keep_above = lambda self, _val: None
    if not hasattr(Gtk.Window, "deiconify"):
        Gtk.Window.deiconify = lambda self: self.unminimize() if hasattr(self, "unminimize") else None
    if not hasattr(Gtk.Window, "iconify"):
        Gtk.Window.iconify = lambda self: self.minimize() if hasattr(self, "minimize") else None
    if not hasattr(Gtk.Window, "resize"):
        def _win_resize(self: Gtk.Window, w: int, h: int) -> None:
            self.set_default_size(int(w), int(h))
        Gtk.Window.resize = _win_resize
    if not hasattr(Gtk.Window, "get_size"):
        def _win_get_size(self: Gtk.Window) -> tuple[int, int]:
            w = self.get_width() or self.get_default_size()[0] or 860
            h = self.get_height() or self.get_default_size()[1] or 540
            return int(w), int(h)
        Gtk.Window.get_size = _win_get_size

    if not hasattr(Gtk, "EventBox"):
        class _Gtk4EventBox(Gtk.Box):
            def __init__(self, **kw: Any) -> None:
                super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0, **kw)
            def set_visible_window(self, _v: bool) -> None:
                pass
            def add(self, child: Gtk.Widget) -> None:
                self.append(child)
        Gtk.EventBox = _Gtk4EventBox

    if not hasattr(Gtk.Image, "new_from_pixbuf") and hasattr(Gdk.Texture, "new_for_pixbuf"):
        def _img_new_from_pixbuf(pb: GdkPixbuf.Pixbuf | None) -> Gtk.Image:
            if pb is None:
                return Gtk.Image()
            tex = Gdk.Texture.new_for_pixbuf(pb)
            return Gtk.Image.new_from_paintable(tex)
        Gtk.Image.new_from_pixbuf = staticmethod(_img_new_from_pixbuf)

    if not hasattr(Gtk, "events_pending"):
        Gtk.events_pending = lambda: GLib.MainContext.default().pending()
    if not hasattr(Gtk, "main_iteration_do"):
        Gtk.main_iteration_do = lambda blocking=False: GLib.MainContext.default().iteration(bool(blocking))

_install_gtk4_shims()

def apply_css_provider_to_display(provider: Gtk.CssProvider) -> None:
    if IS_GTK4 and hasattr(Gtk.StyleContext, "add_provider_for_display"):
        disp = Gdk.Display.get_default()
        if disp is not None:
            Gtk.StyleContext.add_provider_for_display(
                disp, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
        return
    scr = Gdk.Screen.get_default()
    if scr is not None:
        Gtk.StyleContext.add_provider_for_screen(
            scr, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

def load_css_bytes(provider: Gtk.CssProvider, css_bytes: bytes) -> None:
    if IS_GTK4:
        try:
            provider.load_from_bytes(GLib.Bytes.new(css_bytes))
            return
        except Exception:
            pass
    provider.load_from_data(css_bytes)

def set_widget_cursor_name(widget: Gtk.Widget, cursor_name: str = "pointer") -> None:
    with contextlib.suppress(Exception):
        if IS_GTK4 and hasattr(widget, "set_cursor_from_name"):
            widget.set_cursor_from_name(cursor_name)
            return
        win = widget.get_window() if hasattr(widget, "get_window") else None
        if win is not None and hasattr(Gdk.Cursor, "new_from_name"):
            disp = win.get_display()
            cur = Gdk.Cursor.new_from_name(disp, cursor_name)
            win.set_cursor(cur)

__all__ = [
    "GTK_MAJOR",
    "Gdk",
    "GdkPixbuf",
    "Gio",
    "GLib",
    "Gtk",
    "IS_GTK4",
    "Pango",
    "apply_css_provider_to_display",
    "ensure_headless_display",
    "get_gtk_version_string",
    "load_css_bytes",
    "set_widget_cursor_name",
]
