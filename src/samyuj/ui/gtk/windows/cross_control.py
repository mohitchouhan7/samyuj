from __future__ import annotations

import contextlib
import os
import time

from samyuj.ui.gtk.gtk_compat import Gdk, GLib, Gtk

from samyuj.ui.gtk.assets import _clamp_window_to_screen

_EDGE_DWELL_S = 0.4
_EDGE_COOLDOWN_S = 1.0
_FAIL_COOLDOWN_S = 5.0
_ACCEPT_TIMEOUT_S = 8.0
_ACCEPT_POLL_MS = 60
_WARP_BACK_INSET = 48
_PHONE_CURSOR_INSET = 40
_RETURN_CROSS_GUARD_S = 0.35
_NEAR_EDGE_MARGIN = 40

_WM_RESERVED_KEYVALS = frozenset({
    "Super_L", "Super_R", "Meta_L", "Meta_R", "Hyper_L", "Hyper_R",
})

class ScreenEdgeCrossController:

    def __init__(self, app: "SamyujGtkDesktopApp") -> None:
        self.app = app
        self.facade = app.facade
        self.enabled: bool = False
        self.active_on_phone: bool = False
        self.user_disabled: bool = False
        self.edge: str = str(self.facade.user_settings.get("cross_control_edge") or "left").lower()
        self.phone_x: float = 540.0
        self.phone_y: float = 1200.0
        self.buttons_mask: int = 0
        self._last_move_mono: float = 0.0
        self._poll_source_id: int | None = None
        self._edge_win: Gtk.Window | None = None
        self._hud_win: Gtk.Window | None = None
        self._hud_label: Gtk.Label | None = None
        self._anchor_x: int = 500
        self._anchor_y: int = 500
        self._ignoring_warp: bool = False
        self._cooldown_until: float = 0.0
        self._edge_dwell_since: float | None = None
        self._dwell_timeout_id: int | None = None
        self._activation_in_flight: bool = False
        self._awaiting_accept: bool = False
        self._accept_poll_id: int | None = None
        self._accept_deadline: float = 0.0
        self._pending_pos: tuple[int, int] = (0, 0)
        self._last_edge_pos: tuple[int, int] = (0, 0)
        self._capture_start: float = 0.0
        self._disabled_logged: bool = False

    def _screen_geom(self) -> tuple[int, int]:
        disp = Gdk.Display.get_default()
        if disp is not None:
            mon = disp.get_primary_monitor() or (disp.get_monitor(0) if disp.get_n_monitors() > 0 else None)
            if mon is not None:
                g = mon.get_geometry()
                if g.width > 0 and g.height > 0:
                    return int(g.width), int(g.height)
        return self.facade._pc_screen_size()

    def _bbox_geom(self) -> tuple[int, int, int, int]:
        disp = Gdk.Display.get_default()
        if disp is not None:
            with contextlib.suppress(Exception):
                n = disp.get_n_monitors()
                if n > 0:
                    x0 = y0 = 10 ** 9
                    x1 = y1 = -10 ** 9
                    for i in range(n):
                        mon = disp.get_monitor(i)
                        if mon is None:
                            continue
                        g = mon.get_geometry()
                        x0 = min(x0, int(g.x))
                        y0 = min(y0, int(g.y))
                        x1 = max(x1, int(g.x) + int(g.width))
                        y1 = max(y1, int(g.y) + int(g.height))
                    if x1 > x0 and y1 > y0:
                        return x0, y0, x1 - x0, y1 - y0
        w, h = self._screen_geom()
        return 0, 0, w, h

    def _phone_geom(self) -> tuple[int, int]:
        pw = int(self.facade.uc_state.get("phone_w") or 0)
        ph = int(self.facade.uc_state.get("phone_h") or 0)
        if pw <= 0 or ph <= 0:
            pw, ph = 1080, 2400
        return pw, ph

    def _in_edge_zone(self, rx: int, ry: int) -> bool:
        bx, by, bw, bh = self._bbox_geom()
        if self.edge == "left":
            return rx <= bx + 1 and by - 8 <= ry <= by + bh + 8
        if self.edge == "right":
            return rx >= bx + bw - 2 and by - 8 <= ry <= by + bh + 8
        if self.edge == "top":
            return ry <= by + 1 and bx - 8 <= rx <= bx + bw + 8
        return ry >= by + bh - 2 and bx - 8 <= rx <= bx + bw + 8

    def _near_edge(self, rx: int, ry: int, margin: int = _NEAR_EDGE_MARGIN) -> bool:
        bx, by, bw, bh = self._bbox_geom()
        if self.edge == "left":
            return rx <= bx + margin
        if self.edge == "right":
            return rx >= bx + bw - 1 - margin
        if self.edge == "top":
            return ry <= by + margin
        return ry >= by + bh - 1 - margin

    def _root_pointer_pos(self) -> tuple[int, int] | None:
        with contextlib.suppress(Exception):
            disp = Gdk.Display.get_default()
            if disp is None:
                return None
            seat = disp.get_default_seat()
            ptr = seat.get_pointer() if seat else None
            if ptr is None:
                return None
            _scr, rx, ry = ptr.get_position()
            return int(rx), int(ry)
        return None

    @staticmethod
    def _is_native_wayland() -> bool:
        if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
            return True
        with contextlib.suppress(Exception):
            disp = Gdk.Display.get_default()
            if disp is not None and "wayland" in type(disp).__name__.lower():
                return True
        return False

    def start(self, edge: str | None = None) -> None:
        if not self.facade.user_settings.get("cross_control_edge_capture", False):
            self.enabled = False
            if not self._disabled_logged:
                self._disabled_logged = True
                self.facade.events.log(
                    "UC_EDGE_DISABLED",
                    "Screen-edge Cross Control capture is DISABLED (master switch "
                    "'cross_control_edge_capture' is off): no barrier window, no "
                    "pointer poll, no keyboard/mouse grab. Enable it under "
                    "Settings → Display Cross Control if wanted.",
                    feature="input",
                )
            return
        if edge:
            self.edge = edge.lower()
        else:
            self.edge = str(self.facade.user_settings.get("cross_control_edge") or "left").lower()
        self.enabled = True
        self.user_disabled = False
        self._ensure_edge_window()
        if not self._is_native_wayland() and self._poll_source_id is None:
            self._poll_source_id = GLib.timeout_add(16, self._tick_edge_poll)
        self.facade.events.log(
            "UC_EDGE_MONITOR_START",
            f"Cross Control screen-edge monitor armed on the {self.edge.upper()} OUTER display edge "
            f"(capture needs a {int(_EDGE_DWELL_S * 1000)} ms dwell AND the phone to accept; "
            f"Esc / Alt+Tab / Super always return to the PC)",
            feature="input",
        )

    def stop(self) -> None:
        self.enabled = False
        self._clear_accept_timer()
        self._awaiting_accept = False
        self._activation_in_flight = False
        self._edge_dwell_since = None
        if self._dwell_timeout_id is not None:
            with contextlib.suppress(Exception):
                GLib.source_remove(self._dwell_timeout_id)
            self._dwell_timeout_id = None
        if self.active_on_phone:
            self._deactivate_phone_capture(warp_back=False, reason="controller stopped")
        if self._poll_source_id is not None:
            with contextlib.suppress(Exception):
                GLib.source_remove(self._poll_source_id)
            self._poll_source_id = None
        with contextlib.suppress(Exception):
            disp = Gdk.Display.get_default()
            seat = disp.get_default_seat() if disp is not None else None
            if seat is not None:
                seat.ungrab()
        if self._edge_win is not None:
            with contextlib.suppress(Exception):
                self._edge_win.hide()
        self._hide_hud()

    def _ensure_edge_window(self) -> None:
        if self._edge_win is None:
            w = Gtk.Window(type=Gtk.WindowType.POPUP)
            w.set_decorated(False)
            w.set_skip_taskbar_hint(True)
            w.set_skip_pager_hint(True)
            w.set_keep_above(True)
            w.set_accept_focus(True)
            w.set_CanFocus(True) if hasattr(w, "set_CanFocus") else w.set_can_focus(True)
            w.set_opacity(0.01)
            w.add_events(
                Gdk.EventMask.ENTER_NOTIFY_MASK
                | Gdk.EventMask.LEAVE_NOTIFY_MASK
                | Gdk.EventMask.POINTER_MOTION_MASK
                | Gdk.EventMask.BUTTON_PRESS_MASK
                | Gdk.EventMask.BUTTON_RELEASE_MASK
                | Gdk.EventMask.SCROLL_MASK
                | Gdk.EventMask.KEY_PRESS_MASK
                | Gdk.EventMask.KEY_RELEASE_MASK
            )
            w.connect("enter-notify-event", self._on_edge_enter)
            w.connect("leave-notify-event", self._on_edge_leave)
            w.connect("motion-notify-event", self._on_capture_motion)
            w.connect("button-press-event", self._on_capture_button, True)
            w.connect("button-release-event", self._on_capture_button, False)
            w.connect("scroll-event", self._on_capture_scroll)
            w.connect("key-press-event", self._on_capture_key, True)
            w.connect("key-release-event", self._on_capture_key, False)
            self._edge_win = w

        self._position_edge_bar()
        self._edge_win.show_all()

    def _position_edge_bar(self) -> None:
        if self._edge_win is None:
            return
        bx, by, bw, bh = self._bbox_geom()
        if self.edge == "right":
            self._edge_win.resize(2, bh)
            self._edge_win.move(bx + bw - 2, by)
        elif self.edge == "top":
            self._edge_win.resize(bw, 2)
            self._edge_win.move(bx, by)
        elif self.edge == "bottom":
            self._edge_win.resize(bw, 2)
            self._edge_win.move(bx, by + bh - 2)
        else:
            self._edge_win.resize(2, bh)
            self._edge_win.move(bx, by)

    def _set_hud(self, msg: str) -> None:
        if self._hud_win is None:
            hw = Gtk.Window(type=Gtk.WindowType.POPUP)
            hw.set_decorated(False)
            hw.set_skip_taskbar_hint(True)
            hw.set_keep_above(True)
            lbl = Gtk.Label(label=msg)
            lbl.get_style_context().add_class("edge-hud-pill")
            hw.add(lbl)
            self._hud_win = hw
            self._hud_label = lbl
        elif self._hud_label is not None:
            self._hud_label.set_text(msg)
        self._hud_win.show_all()
        bx, _by, bw, _bh = self._bbox_geom()
        self._hud_win.move(bx + max(16, (bw - 580) // 2), _by + 14)

    def _hide_hud(self) -> None:
        if self._hud_win is not None:
            with contextlib.suppress(Exception):
                self._hud_win.hide()

    def _active_hud_msg(self) -> str:
        dev_name = self.facade._best_device_name()
        return_dir = {"left": "right", "right": "left", "top": "down", "bottom": "up"}.get(self.edge, "right")
        return (f"Cross Control on {dev_name} ({self.edge.capitalize()} display edge) · "
                f"Move the phone cursor {return_dir}, or press Esc / Alt+Tab, to return to the PC")

    def _on_edge_enter(self, _widget, event: Gdk.EventCrossing) -> bool:
        if (not self.enabled or self.active_on_phone or self._activation_in_flight
                or time.monotonic() < self._cooldown_until):
            return False
        self._last_edge_pos = (int(event.x_root), int(event.y_root))
        if self._edge_dwell_since is None:
            self._edge_dwell_since = time.monotonic()
        if self._dwell_timeout_id is None:
            self._dwell_timeout_id = GLib.timeout_add(int(_EDGE_DWELL_S * 1000), self._dwell_fired)
        return False

    def _on_edge_leave(self, _widget, _event) -> bool:
        self._edge_dwell_since = None
        if self._dwell_timeout_id is not None:
            with contextlib.suppress(Exception):
                GLib.source_remove(self._dwell_timeout_id)
            self._dwell_timeout_id = None
        if self._awaiting_accept:
            pos = self._root_pointer_pos()
            if pos is not None and not self._near_edge(*pos):
                self._cancel_pending_activation("user moved away before the phone accepted")
        return False

    def _dwell_fired(self) -> bool:
        self._dwell_timeout_id = None
        if (not self.enabled or self.active_on_phone or self._activation_in_flight
                or self._edge_dwell_since is None):
            return False
        if time.monotonic() - self._edge_dwell_since < _EDGE_DWELL_S - 0.05:
            return False
        if time.monotonic() < self._cooldown_until:
            return False
        rx, ry = self._last_edge_pos
        self._edge_dwell_since = None
        self._trigger_activation(rx, ry)
        return False

    def _tick_edge_poll(self) -> bool:
        if not self.enabled:
            self._poll_source_id = None
            return False
        now = time.monotonic()

        if self.active_on_phone:
            if self.facade._live_ws() is None:
                self._deactivate_phone_capture(warp_back=True, reason="phone disconnected (watchdog)")
            elif self._edge_win is None or not self._edge_win.get_visible():
                self._deactivate_phone_capture(warp_back=True, reason="capture window lost (watchdog)")
            return True

        pos = self._root_pointer_pos()
        if pos is None:
            return True
        rx, ry = pos
        self._last_edge_pos = (rx, ry)
        in_zone = self._in_edge_zone(rx, ry)

        if self._awaiting_accept:
            if not in_zone and not self._near_edge(rx, ry):
                self._cancel_pending_activation("user moved away before the phone accepted")
            return True
        if self._activation_in_flight:
            return True
        if now < self._cooldown_until:
            self._edge_dwell_since = None
            return True

        if in_zone:
            if self._edge_dwell_since is None:
                self._edge_dwell_since = now
            elif now - self._edge_dwell_since >= _EDGE_DWELL_S:
                self._edge_dwell_since = None
                self._trigger_activation(rx, ry)
        else:
            self._edge_dwell_since = None
        return True

    def _trigger_activation(self, rx: int, ry: int) -> None:
        if self._activation_in_flight or self.active_on_phone or not self.enabled:
            return
        if time.monotonic() < self._cooldown_until:
            return
        c = self.facade._live_ws()
        if c is None:
            return
        self._activation_in_flight = True
        self._pending_pos = (int(rx), int(ry))
        if self.facade.uc_state.get("connected"):
            self.facade.events.log(
                "UC_EDGE_DWELL_TRIGGER",
                f"dwell on the {self.edge.upper()} outer edge at ({rx},{ry}) — session already connected, capturing",
                feature="input", service_id=26)
            self._begin_capture(c)
            return
        self.facade.events.log(
            "UC_EDGE_DWELL_TRIGGER",
            f"dwell on the {self.edge.upper()} outer edge at ({rx},{ry}) — requesting cross control "
            f"(NO grab until the phone accepts)",
            feature="input", service_id=26)
        dev_name = "?"
        with contextlib.suppress(Exception):
            dev_name = self.facade._best_device_name()
        self._set_hud(f"Cross Control: asking {dev_name} to accept… (move the mouse away to cancel)")
        self._awaiting_accept = True
        self._accept_deadline = time.monotonic() + _ACCEPT_TIMEOUT_S
        self.app._spawn(self.facade.start_cross_control(c, trigger=1))
        self._accept_poll_id = GLib.timeout_add(_ACCEPT_POLL_MS, self._tick_accept_wait)

    def _tick_accept_wait(self) -> bool:
        if not self._awaiting_accept:
            self._accept_poll_id = None
            return False
        c = self.facade._live_ws()
        if c is None:
            self._accept_poll_id = None
            self._fail_activation("phone disconnected while waiting for the cross-control accept")
            return False
        if self.facade.uc_state.get("connected"):
            self._accept_poll_id = None
            self._begin_capture(c)
            return False
        if (self.facade.uc_state.get("phase") == "denied"
                and self.facade.uc_state.get("retried_dialog")):
            self._accept_poll_id = None
            self._fail_activation("the phone DENIED cross control")
            return False
        if time.monotonic() > self._accept_deadline:
            self._accept_poll_id = None
            self._fail_activation(
                f"the phone did not accept cross control within {_ACCEPT_TIMEOUT_S:.0f}s")
            return False
        return True

    def _clear_accept_timer(self) -> None:
        if self._accept_poll_id is not None:
            with contextlib.suppress(Exception):
                GLib.source_remove(self._accept_poll_id)
            self._accept_poll_id = None

    def _fail_activation(self, why: str) -> None:
        self._clear_accept_timer()
        self._awaiting_accept = False
        self._activation_in_flight = False
        self._edge_dwell_since = None
        self._cooldown_until = time.monotonic() + _FAIL_COOLDOWN_S
        self._hide_hud()
        self.facade.events.log(
            "UC_EDGE_ACTIVATION_FAILED",
            f"{why} — NO input grab was taken; keyboard/mouse stayed with the PC",
            feature="input", service_id=26)

    def _cancel_pending_activation(self, why: str) -> None:
        if not self._awaiting_accept:
            return
        self._clear_accept_timer()
        self._awaiting_accept = False
        self._activation_in_flight = False
        self._edge_dwell_since = None
        self._cooldown_until = time.monotonic() + _EDGE_COOLDOWN_S
        self._hide_hud()
        self.facade.events.log("UC_EDGE_ACTIVATION_CANCELLED", why,
                               feature="input", service_id=26)

    def _begin_capture(self, c: dict) -> None:
        self._clear_accept_timer()
        self._awaiting_accept = False
        rx, ry = self._pending_pos
        bx, by, bw, bh = self._bbox_geom()
        pw, ph = self._phone_geom()

        frac_y = max(0.0, min(1.0, (ry - by) / max(1, bh)))
        frac_x = max(0.0, min(1.0, (rx - bx) / max(1, bw)))
        if self.edge == "left":
            self.phone_x = float(max(1, pw - _PHONE_CURSOR_INSET))
            self.phone_y = frac_y * (ph - 1)
        elif self.edge == "right":
            self.phone_x = float(min(max(0, pw - 2), _PHONE_CURSOR_INSET))
            self.phone_y = frac_y * (ph - 1)
        elif self.edge == "top":
            self.phone_x = frac_x * (pw - 1)
            self.phone_y = float(max(1, ph - _PHONE_CURSOR_INSET))
        else:
            self.phone_x = frac_x * (pw - 1)
            self.phone_y = float(min(max(0, ph - 2), _PHONE_CURSOR_INSET))

        self._anchor_x = bx + bw // 2
        self._anchor_y = by + bh // 2

        disp = Gdk.Display.get_default()
        if self._edge_win is None or disp is None:
            self._fail_activation("capture surface unavailable")
            return
        self._edge_win.resize(bw, bh)
        self._edge_win.move(bx, by)
        self._edge_win.show_all()
        gdk_win = self._edge_win.get_window()
        if gdk_win is None:
            self._fail_activation("capture window has no GdkWindow")
            return

        status = None
        with contextlib.suppress(Exception):
            blank_cur = Gdk.Cursor.new_for_display(disp, Gdk.CursorType.BLANK_CURSOR)
            gdk_win.set_cursor(blank_cur)
            seat = disp.get_default_seat()
            if seat is not None:
                status = seat.grab(
                    gdk_win,
                    Gdk.SeatCapabilities.ALL,
                    True,
                    blank_cur,
                    None,
                    None,
                )
        if status != Gdk.GrabStatus.SUCCESS:
            with contextlib.suppress(Exception):
                seat = disp.get_default_seat()
                if seat is not None:
                    seat.ungrab()
                gdk_win.set_cursor(None)
            self._position_edge_bar()
            self._fail_activation(
                f"seat grab refused by the display server (status={status}) — "
                "another grab may be active, or the session restricts global grabs")
            return

        self.active_on_phone = True
        self._capture_start = time.monotonic()
        self._activation_in_flight = False
        self.buttons_mask = 0
        with contextlib.suppress(Exception):
            seat = disp.get_default_seat()
            ptr = seat.get_pointer() if seat is not None else None
            if ptr is not None:
                self._ignoring_warp = True
                ptr.warp(disp.get_default_screen(), self._anchor_x, self._anchor_y)
        self._set_hud(self._active_hud_msg())
        self.facade.events.log(
            "UC_EDGE_CAPTURE_ON",
            f"seat grabbed (ALL, status={status}) AFTER phone accept; phone cursor at "
            f"({int(self.phone_x)},{int(self.phone_y)}); Esc / Alt+Tab / Super return to the PC",
            feature="input", service_id=26)
        self.app._spawn(self.facade.set_cross_control_pointer(True, c))
        self.app._spawn(self.facade.send_uc_mouse_move(c, int(self.phone_x), int(self.phone_y)))

    def _deactivate_phone_capture(self, warp_back: bool = True, reason: str = "") -> None:
        self._clear_accept_timer()
        self._awaiting_accept = False
        self._activation_in_flight = False
        self._edge_dwell_since = None
        if not self.active_on_phone:
            return
        self.active_on_phone = False
        self._cooldown_until = time.monotonic() + _EDGE_COOLDOWN_S
        self.buttons_mask = 0
        bx, by, bw, bh = self._bbox_geom()
        pw, ph = self._phone_geom()
        disp = Gdk.Display.get_default()
        if disp is not None:
            with contextlib.suppress(Exception):
                seat = disp.get_default_seat()
                if seat is not None:
                    seat.ungrab()
                    if warp_back:
                        ptr = seat.get_pointer()
                        if ptr is not None:
                            if self.edge == "left":
                                tx, ty = bx + _WARP_BACK_INSET, by + int((self.phone_y / max(1, ph)) * bh)
                            elif self.edge == "right":
                                tx, ty = bx + bw - _WARP_BACK_INSET, by + int((self.phone_y / max(1, ph)) * bh)
                            elif self.edge == "top":
                                tx, ty = bx + int((self.phone_x / max(1, pw)) * bw), by + _WARP_BACK_INSET
                            else:
                                tx, ty = bx + int((self.phone_x / max(1, pw)) * bw), by + bh - _WARP_BACK_INSET
                            ptr.warp(disp.get_default_screen(), tx, ty)
        if self._edge_win is not None:
            gdk_win = self._edge_win.get_window()
            if gdk_win is not None:
                with contextlib.suppress(Exception):
                    gdk_win.set_cursor(None)
            self._position_edge_bar()
        self._hide_hud()
        c = self.facade._live_ws()
        if c:
            self.app._spawn(self.facade.set_cross_control_pointer(False, c))
        self.facade.events.log(
            "UC_EDGE_CAPTURE_OFF",
            f"grab released ({reason or 'user'}) — keyboard/mouse back to the PC",
            feature="input", service_id=26)

    def _on_capture_motion(self, _widget, event: Gdk.EventMotion) -> bool:
        if not self.active_on_phone:
            return False
        rx, ry = int(event.x_root), int(event.y_root)
        if self._ignoring_warp and abs(rx - self._anchor_x) <= 4 and abs(ry - self._anchor_y) <= 4:
            self._ignoring_warp = False
            return True
        self._ignoring_warp = False
        dx = rx - self._anchor_x
        dy = ry - self._anchor_y
        if dx == 0 and dy == 0:
            return True

        pw, ph = self._phone_geom()
        self.phone_x += dx * 1.15
        self.phone_y += dy * 1.15

        if time.monotonic() - self._capture_start > _RETURN_CROSS_GUARD_S:
            crossed_back = (
                (self.edge == "left" and self.phone_x >= pw - 1 and dx > 0)
                or (self.edge == "right" and self.phone_x <= 1 and dx < 0)
                or (self.edge == "top" and self.phone_y >= ph - 1 and dy > 0)
                or (self.edge == "bottom" and self.phone_y <= 1 and dy < 0)
            )
            if crossed_back:
                self._deactivate_phone_capture(warp_back=True,
                                               reason="cursor crossed back over the phone edge")
                return True

        self.phone_x = max(0.0, min(float(pw - 1), self.phone_x))
        self.phone_y = max(0.0, min(float(ph - 1), self.phone_y))

        disp = Gdk.Display.get_default()
        if disp is not None:
            with contextlib.suppress(Exception):
                seat = disp.get_default_seat()
                ptr = seat.get_pointer() if seat else None
                if ptr is not None:
                    self._ignoring_warp = True
                    ptr.warp(disp.get_default_screen(), self._anchor_x, self._anchor_y)

        now = time.monotonic()
        if now - self._last_move_mono >= (1.0 / 120.0):
            self._last_move_mono = now
            c = self.facade._live_ws()
            if c:
                self.app._spawn(self.facade.send_uc_mouse_move(c, int(self.phone_x), int(self.phone_y), self.buttons_mask))
        return True

    def _on_capture_button(self, _widget, event: Gdk.EventButton, pressed: bool) -> bool:
        if not self.active_on_phone:
            return False
        btn_map = {1: 1, 2: 2, 3: 3}
        b = btn_map.get(int(event.button), 1)
        if pressed:
            self.buttons_mask |= b
        else:
            self.buttons_mask &= ~b
        c = self.facade._live_ws()
        if c:
            self.app._spawn(self.facade.send_uc_mouse_button(c, b, pressed, int(self.phone_x), int(self.phone_y)))
        return True

    def _on_capture_scroll(self, _widget, event: Gdk.EventScroll) -> bool:
        if not self.active_on_phone:
            return False
        v, h_sc = 0, 0
        if event.direction == Gdk.ScrollDirection.UP:
            v = 1
        elif event.direction == Gdk.ScrollDirection.DOWN:
            v = -1
        elif event.direction == Gdk.ScrollDirection.LEFT:
            h_sc = -1
        elif event.direction == Gdk.ScrollDirection.RIGHT:
            h_sc = 1
        elif event.direction == Gdk.ScrollDirection.SMOOTH:
            ok, dx, dy = event.get_scroll_deltas()
            if ok:
                now = time.monotonic()
                if now - float(getattr(self, "_uc_scroll_last_send", 0.0)) < 0.02:
                    acc_x = float(getattr(self, "_uc_scroll_acc_x", 0.0)) + float(dx)
                    acc_y = float(getattr(self, "_uc_scroll_acc_y", 0.0)) + float(dy)
                    self._uc_scroll_acc_x = acc_x
                    self._uc_scroll_acc_y = acc_y
                    return True
                acc_x = float(getattr(self, "_uc_scroll_acc_x", 0.0)) + float(dx)
                acc_y = float(getattr(self, "_uc_scroll_acc_y", 0.0)) + float(dy)
                steps_x = int(acc_x) if abs(acc_x) >= 1.0 else 0
                steps_y = int(acc_y) if abs(acc_y) >= 1.0 else 0
                self._uc_scroll_acc_x = acc_x - steps_x
                self._uc_scroll_acc_y = acc_y - steps_y
                v = -steps_y
                h_sc = steps_x
                if not v and not h_sc:
                    return True
                self._uc_scroll_last_send = now
        if v or h_sc:
            c = self.facade._live_ws()
            if c:
                self.app._spawn(self.facade.send_uc_scroll(c, int(self.phone_x), int(self.phone_y), v, h_sc))
        return True

    @staticmethod
    def _is_wm_reserved(kv: str, state: int) -> bool:
        if kv in _WM_RESERVED_KEYVALS:
            return True
        mod1 = bool(state & Gdk.ModifierType.MOD1_MASK)
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        if mod1 and kv in ("Tab", "ISO_Left_Tab"):
            return True
        if mod1 and ctrl and kv in ("Delete", "BackSpace"):
            return True
        return False

    def _on_capture_key(self, _widget, event: Gdk.EventKey, pressed: bool) -> bool:
        if not self.active_on_phone:
            return False
        kv = Gdk.keyval_name(event.keyval) or ""
        st_mask = getattr(event, "state", 0) or 0
        if pressed and kv == "Escape":
            self._deactivate_phone_capture(warp_back=True, reason="Esc")
            return True
        if pressed and self._is_wm_reserved(kv, st_mask):
            self._deactivate_phone_capture(warp_back=True,
                                           reason=f"{kv} handed back to the window manager")
            return True
        kc = _GDK_KEY_TO_ANDROID.get(kv)
        if kc is None and len(kv) == 1:
            ch = kv.upper()
            if "A" <= ch <= "Z":
                kc = 29 + (ord(ch) - ord("A"))
            elif "0" <= ch <= "9":
                kc = 7 + (ord(ch) - ord("0"))
        if kc is not None:
            c = self.facade._live_ws()
            if c:
                meta_state = 0
                if st_mask & Gdk.ModifierType.SHIFT_MASK:
                    meta_state |= 1
                if st_mask & Gdk.ModifierType.MOD1_MASK:
                    meta_state |= 2
                if st_mask & Gdk.ModifierType.CONTROL_MASK:
                    meta_state |= 4096
                if st_mask & (Gdk.ModifierType.SUPER_MASK | Gdk.ModifierType.META_MASK):
                    meta_state |= 65536
                if pressed and (meta_state & 4096) and kc == 50 and hasattr(self.app, "_paste_pc_clipboard_and_send_uc_key"):
                    self.app._spawn(self.app._paste_pc_clipboard_and_send_uc_key(c, kc, pressed, meta_state=meta_state))
                else:
                    self.app._spawn(self.facade.send_uc_key(c, kc, pressed, meta_state=meta_state))
        return True

_GDK_KEY_TO_ANDROID = {
    "Return": 66, "KP_Enter": 66, "BackSpace": 67, "Delete": 112, "Tab": 61,
    "space": 62, "Escape": 4, "Up": 19, "Down": 20, "Left": 21, "Right": 22,
    "Home": 122, "End": 123, "Page_Up": 92, "Page_Down": 93,
    "minus": 69, "equal": 70, "bracketleft": 71, "bracketright": 72,
    "backslash": 73, "semicolon": 74, "apostrophe": 75, "slash": 76,
    "comma": 55, "period": 56, "grave": 68,
}

class CrossControlGtkWindow(Gtk.Window):

    def __init__(self, app: "SamyujGtkDesktopApp") -> None:
        super().__init__(title="Samyuj — Cross Control")
        self.app = app
        self.facade = app.facade
        _clamp_window_to_screen(self, 430, 760)
        self.connect("delete-event", self._on_delete)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_top(16)
        box.set_margin_bottom(16)
        box.set_margin_start(16)
        box.set_margin_end(16)
        lbl = Gtk.Label(
            label="Cross Control is integrated with your Linux display edge.\n"
                  "Push your mouse cursor into the left edge of your screen and HOLD it there\n"
                  "for a moment to control your phone. Press Esc or Alt+Tab to come back."
        )
        lbl.set_line_wrap(True)
        box.pack_start(lbl, True, True, 0)
        self.add(box)

    def _on_delete(self, *_args) -> bool:
        self.hide()
        return True

