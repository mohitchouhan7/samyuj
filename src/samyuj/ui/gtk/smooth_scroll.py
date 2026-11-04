from __future__ import annotations

import contextlib
from typing import Any

_ENABLED: bool = True

PX_PER_NOTCH: float = 72.0
ANIM_INTERVAL_MS: int = 8
ANIM_FOLLOW: float = 0.30
ANIM_SNAP_PX: float = 0.5
RESYNC_PX: float = 24.0

def set_smooth_scroll_enabled(enabled: bool) -> bool:
    global _ENABLED
    _ENABLED = bool(enabled)
    return _ENABLED

def is_smooth_scroll_enabled() -> bool:
    return _ENABLED

def scroll_units_to_px(units: float, scale: int = 1) -> float:
    return float(units) * PX_PER_NOTCH * max(1, int(scale or 1))

class SmoothScrollController:

    __slots__ = ("sw", "target_v", "target_h", "timer", "_ctrl", "_last_set")

    def __init__(self, scrolled_window: Any) -> None:
        self.sw = scrolled_window
        self.target_v: float | None = None
        self.target_h: float | None = None
        self.timer: Any = None
        self._ctrl: Any = None
        self._last_set: dict[str, float | None] = {"v": None, "h": None}

    def attach(self) -> "SmoothScrollController":
        from samyuj.ui.gtk.gtk_compat import IS_GTK4, Gdk, Gtk
        if IS_GTK4:
            ctrl = Gtk.EventControllerScroll.new(
                Gtk.EventControllerScrollFlags.BOTH_AXES)
            with contextlib.suppress(Exception):
                ctrl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            ctrl.connect("scroll", self._on_scroll_gtk4)
            self.sw.add_controller(ctrl)
            self._ctrl = ctrl
        else:
            self.sw.add_events(Gdk.EventMask.SCROLL_MASK)
            self.sw.connect("scroll-event", self._on_scroll_gtk3)
        return self

    def _on_scroll_gtk3(self, _widget: Any, event: Any) -> bool:
        if not _ENABLED:
            return False
        from samyuj.ui.gtk.gtk_compat import Gdk
        dx = dy = 0.0
        direction = event.direction
        if direction == Gdk.ScrollDirection.SMOOTH:
            ok, sdx, sdy = event.get_scroll_deltas()
            if ok:
                dx, dy = float(sdx), float(sdy)
        elif direction == Gdk.ScrollDirection.UP:
            dy = -1.0
        elif direction == Gdk.ScrollDirection.DOWN:
            dy = 1.0
        elif direction == Gdk.ScrollDirection.LEFT:
            dx = -1.0
        elif direction == Gdk.ScrollDirection.RIGHT:
            dx = 1.0
        else:
            return False
        shift = False
        with contextlib.suppress(Exception):
            shift = bool(event.state & Gdk.ModifierType.SHIFT_MASK)
        return self._handle(dx, dy, shift)

    def _on_scroll_gtk4(self, _ctrl: Any, dx: float, dy: float) -> bool:
        if not _ENABLED:
            return False
        return self._handle(float(dx), float(dy), False)

    def _handle(self, dx: float, dy: float, shift: bool) -> bool:
        if not _ENABLED:
            return False
        if abs(dx) < 1e-6 and abs(dy) < 1e-6:
            return False
        if shift:
            dx, dy = dx + dy, 0.0
        vadj = None
        hadj = None
        with contextlib.suppress(Exception):
            vadj = self.sw.get_vadjustment()
            hadj = self.sw.get_hadjustment()
        if vadj is None:
            return False
        scale = 1
        with contextlib.suppress(Exception):
            scale = int(self.sw.get_scale_factor() or 1)
        if dy:
            self.target_v = self._next_target(
                vadj, self.target_v, scroll_units_to_px(dy, scale), "v")
        if dx and hadj is not None:
            self.target_h = self._next_target(
                hadj, self.target_h, scroll_units_to_px(dx, scale), "h")
        self._ensure_timer()
        return True

    def _next_target(self, adj: Any, cur_target: float | None,
                     delta_px: float, axis: str) -> float:
        value = float(adj.get_value())
        base = value
        if cur_target is not None:
            last = self._last_set.get(axis)
            if last is None or abs(value - float(last)) <= 1.0:
                base = float(cur_target)
        lo = float(adj.get_lower())
        hi = max(lo, float(adj.get_upper()) - float(adj.get_page_size()))
        return min(hi, max(lo, base + float(delta_px)))

    def _ensure_timer(self) -> None:
        if self.timer is not None:
            return
        from samyuj.ui.gtk.gtk_compat import GLib
        self.timer = GLib.timeout_add(ANIM_INTERVAL_MS, self._tick)

    def _tick(self) -> bool:
        finished = True
        for attr, axis in (("target_v", "v"), ("target_h", "h")):
            target = getattr(self, attr)
            if target is None:
                continue
            adj = None
            with contextlib.suppress(Exception):
                adj = (self.sw.get_vadjustment() if attr == "target_v"
                       else self.sw.get_hadjustment())
            if adj is None:
                setattr(self, attr, None)
                continue
            cur = float(adj.get_value())
            new = cur + (float(target) - cur) * ANIM_FOLLOW
            if abs(float(target) - new) < ANIM_SNAP_PX:
                new = float(target)
                setattr(self, attr, None)
            else:
                finished = False
            with contextlib.suppress(Exception):
                adj.set_value(new)
                self._last_set[axis] = new
        if finished:
            self.timer = None
            return False
        return True

def attach_smooth_scroll(scrolled_window: Any) -> SmoothScrollController | None:
    if scrolled_window is None:
        return None
    existing = getattr(scrolled_window, "_samyuj_smooth_scroll", None)
    if existing is not None:
        return existing
    try:
        with contextlib.suppress(Exception):
            scrolled_window.set_kinetic_scrolling(True)
        with contextlib.suppress(Exception):
            scrolled_window.set_overlay_scrolling(True)
        ctrl = SmoothScrollController(scrolled_window).attach()
        with contextlib.suppress(Exception):
            setattr(scrolled_window, "_samyuj_smooth_scroll", ctrl)
        return ctrl
    except Exception:
        return None
