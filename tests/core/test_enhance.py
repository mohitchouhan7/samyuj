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

enhance = _load_standalone("media/enhance.py", "_samyuj_enhance_headless")
LumaAdaptiveGamma = enhance.LumaAdaptiveGamma
build_gamma_lut = enhance.build_gamma_lut
enhance_payload = enhance.enhance_payload
get_enhancer = enhance.get_enhancer
get_lowlight_config = enhance.get_lowlight_config
mean_luma_sampled = enhance.mean_luma_sampled
reset_enhancer = enhance.reset_enhancer
set_lowlight_config = enhance.set_lowlight_config

class GammaLutTest(unittest.TestCase):
    def test_identity_at_gamma_one(self) -> None:
        lut = build_gamma_lut(1.0)
        self.assertEqual(lut, bytes(range(256)))

    def test_endpoints_fixed_and_monotonic(self) -> None:
        lut = build_gamma_lut(1.9)
        self.assertEqual(lut[0], 0)
        self.assertEqual(lut[255], 255)
        for i in range(1, 256):
            self.assertGreaterEqual(lut[i], lut[i - 1])

    def test_midtones_lifted_shadows_most(self) -> None:
        lut = build_gamma_lut(1.9)
        self.assertGreater(lut[40], 40 + 20)
        self.assertLessEqual(lut[250] - 250, 5)

    def test_lut_cache_consistent(self) -> None:
        a = build_gamma_lut(1.5)
        b = build_gamma_lut(1.501)
        self.assertEqual(a, b)

class LumaSamplingTest(unittest.TestCase):
    def test_uniform_buffer(self) -> None:
        buf = bytes([100]) * 10_000
        self.assertAlmostEqual(mean_luma_sampled(buf, len(buf)), 100.0, delta=0.01)

    def test_sampled_mean_close(self) -> None:
        buf = bytes((i * 7) % 256 for i in range(50_000))
        est = mean_luma_sampled(buf, len(buf), max_samples=2048)
        self.assertLess(abs(est - sum(buf) / len(buf)), 6.0)

    def test_step_hint_and_empty(self) -> None:
        self.assertEqual(mean_luma_sampled(b"", 0), 0.0)
        yuyv = bytes([200, 128]) * 5000
        self.assertAlmostEqual(mean_luma_sampled(yuyv, len(yuyv), step_hint=2),
                               200.0, delta=0.01)

class AdaptiveGammaTest(unittest.TestCase):
    def setUp(self) -> None:
        set_lowlight_config(enabled=True, gamma_max=1.9, enter_luma=84.0)
        reset_enhancer()

    def test_bright_scene_stays_identity(self) -> None:
        g = LumaAdaptiveGamma()
        for _ in range(60):
            gamma = g.observe(180.0)
        self.assertLessEqual(gamma, 1.02)

    def test_dark_scene_ramps_up(self) -> None:
        g = LumaAdaptiveGamma()
        gamma = 1.0
        for _ in range(120):
            gamma = g.observe(35.0)
        self.assertGreater(gamma, 1.4)
        self.assertLessEqual(gamma, 1.9 + 1e-6)

    def test_recovers_when_lights_come_up(self) -> None:
        g = LumaAdaptiveGamma()
        for _ in range(120):
            g.observe(30.0)
        self.assertGreater(g.gamma, 1.4)
        for _ in range(200):
            g.observe(190.0)
        self.assertLessEqual(g.gamma, 1.02)

    def test_disabled_returns_identity(self) -> None:
        set_lowlight_config(enabled=False)
        g = LumaAdaptiveGamma()
        for _ in range(120):
            self.assertEqual(g.observe(20.0), 1.0)

    def test_lut_none_when_bright(self) -> None:
        g = LumaAdaptiveGamma()
        self.assertIsNone(g.lut(200.0))

    def test_enhancer_registry(self) -> None:
        e1 = get_enhancer("webcam")
        e2 = get_enhancer("webcam")
        self.assertIs(e1, e2)
        reset_enhancer("webcam")
        self.assertIsNot(get_enhancer("webcam"), e1)

class PayloadEnhanceTest(unittest.TestCase):
    def setUp(self) -> None:
        set_lowlight_config(enabled=True, gamma_max=1.9, enter_luma=84.0)
        reset_enhancer()

    def _darken(self, name: str, w: int, h: int, payload: bytes) -> bytes:
        out = payload
        for _ in range(150):
            out = enhance_payload(payload, name, w, h, stream_key=name)
        return out

    def test_i420_chroma_untouched_length_kept(self) -> None:
        w, h = 64, 48
        y = bytes([40]) * (w * h)
        uv = bytes([128, 90] * ((w * h // 2) // 2))
        payload = y + uv
        out = self._darken("YU12", w, h, payload)
        self.assertEqual(len(out), len(payload))
        self.assertEqual(out[w * h:], uv)
        self.assertGreater(out[0], 60)

    def test_yuyv_odd_bytes_untouched(self) -> None:
        w, h = 64, 48
        payload = bytes([35, 128]) * (w * h)
        out = self._darken("YUYV", w, h, payload)
        self.assertEqual(len(out), len(payload))
        self.assertEqual(out[1::2], payload[1::2])
        self.assertGreater(out[0], 50)

    def test_bright_payload_passthrough(self) -> None:
        w, h = 32, 32
        payload = bytes([220]) * (w * h) + bytes([128]) * (w * h // 2)
        out = enhance_payload(payload, "YU12", w, h, stream_key="bright")
        self.assertIs(out, payload)

    def test_unknown_fourcc_passthrough(self) -> None:
        payload = bytes([30]) * 1000
        self.assertIs(enhance_payload(payload, "NV12", 10, 10), payload)

    def test_malformed_payload_passthrough(self) -> None:
        payload = bytes([30]) * 10
        self.assertIs(enhance_payload(payload, "YU12", 64, 64), payload)

    def test_disabled_config_passthrough(self) -> None:
        set_lowlight_config(enabled=False)
        w, h = 32, 32
        payload = bytes([30]) * (w * h) + bytes([128]) * (w * h // 2)
        self.assertIs(enhance_payload(payload, "YU12", w, h), payload)

class ConfigTest(unittest.TestCase):
    def test_config_roundtrip_and_clamps(self) -> None:
        cfg = set_lowlight_config(enabled=True, gamma_max=99.0, enter_luma=1.0)
        self.assertLessEqual(cfg["gamma_max"], 4.0)
        self.assertGreaterEqual(cfg["enter_luma"], 8.0)
        cfg = set_lowlight_config(gamma_max=1.9, enter_luma=84.0, enabled=True)
        self.assertEqual(get_lowlight_config()["gamma_max"], 1.9)

    def tearDown(self) -> None:
        set_lowlight_config(enabled=True, gamma_max=1.9, enter_luma=84.0)
        reset_enhancer()

if __name__ == "__main__":
    unittest.main()
