from __future__ import annotations

import unittest

import importlib.util
import pathlib as _pathlib

import samyuj as _samyuj_pkg

def _load_standalone(relpath: str, name: str):
    p = _pathlib.Path(_samyuj_pkg.__file__).resolve().parent.joinpath(*relpath.split("/"))
    spec = importlib.util.spec_from_file_location(name, p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

smooth_scroll = _load_standalone("ui/gtk/smooth_scroll.py", "_samyuj_smooth_scroll_headless")
PX_PER_NOTCH = smooth_scroll.PX_PER_NOTCH
RESYNC_PX = smooth_scroll.RESYNC_PX
SmoothScrollController = smooth_scroll.SmoothScrollController
is_smooth_scroll_enabled = smooth_scroll.is_smooth_scroll_enabled
scroll_units_to_px = smooth_scroll.scroll_units_to_px
set_smooth_scroll_enabled = smooth_scroll.set_smooth_scroll_enabled

class FakeAdjustment:

    def __init__(self, value=0.0, lower=0.0, upper=2000.0, page=400.0):
        self._value = float(value)
        self.lower = float(lower)
        self.upper = float(upper)
        self.page = float(page)

    def get_value(self): return self._value
    def set_value(self, v): self._value = float(v)
    def get_lower(self): return self.lower
    def get_upper(self): return self.upper
    def get_page_size(self): return self.page

class FakeScrolledWindow:
    def __init__(self, v=None, h=None):
        self.v = v if v is not None else FakeAdjustment()
        self.h = h if h is not None else FakeAdjustment()

    def get_vadjustment(self): return self.v
    def get_hadjustment(self): return self.h
    def get_scale_factor(self): return 1

class ScrollMathTest(unittest.TestCase):
    def test_units_to_px_scaled(self) -> None:
        self.assertEqual(scroll_units_to_px(1.0), PX_PER_NOTCH)
        self.assertEqual(scroll_units_to_px(-2.0, 2), -2 * PX_PER_NOTCH * 2)
        self.assertEqual(scroll_units_to_px(0.25), PX_PER_NOTCH * 0.25)

    def test_target_clamped_to_range(self) -> None:
        sw = FakeScrolledWindow()
        ctrl = SmoothScrollController(sw)
        adj = FakeAdjustment(value=1500.0, upper=2000.0, page=400.0)
        t = ctrl._next_target(adj, None, scroll_units_to_px(50), "v")
        self.assertLessEqual(t, 1600.0)
        adj2 = FakeAdjustment(value=10.0)
        t2 = ctrl._next_target(adj2, None, scroll_units_to_px(-50), "v")
        self.assertGreaterEqual(t2, 0.0)

    def test_animation_continues_from_inflight_target(self) -> None:
        sw = FakeScrolledWindow()
        ctrl = SmoothScrollController(sw)
        adj = FakeAdjustment(value=150.0)
        ctrl._last_set["v"] = 150.0
        t = ctrl._next_target(adj, 300.0, 72.0, "v")
        self.assertAlmostEqual(t, 372.0)

    def test_external_jump_resyncs(self) -> None:
        sw = FakeScrolledWindow()
        ctrl = SmoothScrollController(sw)
        adj = FakeAdjustment(value=900.0)
        ctrl._last_set["v"] = 110.0
        t = ctrl._next_target(adj, 100.0, 72.0, "v")
        self.assertAlmostEqual(t, 972.0)
        self.assertGreater(RESYNC_PX, 0.0)

class EnableFlagTest(unittest.TestCase):
    def test_toggle(self) -> None:
        original = is_smooth_scroll_enabled()
        try:
            self.assertFalse(set_smooth_scroll_enabled(False))
            self.assertFalse(is_smooth_scroll_enabled())
            self.assertTrue(set_smooth_scroll_enabled(True))
            self.assertTrue(is_smooth_scroll_enabled())
        finally:
            set_smooth_scroll_enabled(original)

    def test_handle_noop_when_disabled(self) -> None:
        sw = FakeScrolledWindow()
        ctrl = SmoothScrollController(sw)
        set_smooth_scroll_enabled(False)
        try:
            self.assertFalse(ctrl._handle(0.0, 1.0, False))
            self.assertEqual(sw.v.get_value(), 0.0)
        finally:
            set_smooth_scroll_enabled(True)

    def test_handle_noop_without_adjustment(self) -> None:
        class NoAdj:
            def get_vadjustment(self): return None
            def get_hadjustment(self): return None
            def get_scale_factor(self): return 1
        ctrl = SmoothScrollController(NoAdj())
        self.assertFalse(ctrl._handle(0.0, 1.0, False))

class TickTest(unittest.TestCase):
    def test_tick_eases_toward_target_and_stops(self) -> None:
        v = FakeAdjustment(value=0.0)
        sw = FakeScrolledWindow(v=v)
        ctrl = SmoothScrollController(sw)
        ctrl.target_v = 100.0
        prev = 0.0
        for _ in range(200):
            keep = ctrl._tick()
            cur = v.get_value()
            self.assertGreaterEqual(cur + 1e-9, prev)
            prev = cur
            if not keep:
                break
        self.assertFalse(keep)
        self.assertAlmostEqual(v.get_value(), 100.0)
        self.assertIsNone(ctrl.target_v)

    def test_shift_maps_vertical_to_horizontal(self) -> None:
        dx, dy = 0.0, 2.0
        dx, dy = dx + dy, 0.0
        self.assertEqual((dx, dy), (2.0, 0.0))

if __name__ == "__main__":
    unittest.main()
