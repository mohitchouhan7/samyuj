from __future__ import annotations

import contextlib
import ctypes
import re
import shutil
import subprocess
import threading
import time
from typing import Callable

PR_SET_TIMERSLACK = 29

ANDROID_SMARTCONNECT_PKGS = (
    "com.motorola.mobiledesktop",
    "com.motorola.readyfor",
    "com.lenovo.lsf.device",
    "com.lenovo.hec.vault",
)

def set_current_thread_timerslack_ns(slack_ns: int = 1) -> bool:
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        rc = libc.prctl(PR_SET_TIMERSLACK, ctypes.c_ulong(max(0, int(slack_ns))), 0, 0, 0)
        return rc == 0
    except Exception:
        return False

class PowerAndRefreshManager:

    VALID_STATES = ("foreground", "background", "hidden")

    def __init__(
        self,
        harness=None,
        exclude_battery_saver: bool = True,
        background_eco_mode: bool = True,
        foreground_fps: float = 60.0,
        background_fps: float = 10.0,
        hidden_fps: float = 1.0,
        log_cb: Callable[[str, str], None] | None = None,
    ) -> None:
        self.h = harness
        self.exclude_battery_saver = bool(exclude_battery_saver)
        self.background_eco_mode = bool(background_eco_mode)
        self.foreground_fps = max(15.0, min(240.0, float(foreground_fps or 60.0)))
        self.background_fps = max(1.0, min(30.0, float(background_fps or 10.0)))
        self.hidden_fps = max(0.2, min(10.0, float(hidden_fps or 1.0)))
        self._log_cb = log_cb
        self._lock = threading.RLock()

        self.window_states: dict[str, str] = {}
        self.last_convert_mono: dict[str, float] = {}
        self.last_ui_render_mono: dict[str, float] = {}
        self.active_services: set[str] = set()

        self.performance_hold_active: bool = False
        self.performance_hold_method: str = "none"
        self._ppd_cookie: int | None = None
        self._ppd_bus_dest: str = ""
        self._ppd_obj_path: str = ""
        self._ppd_iface: str = ""
        self._screensaver_cookie: int | None = None
        self._adb_whitelisted_serials: set[str] = set()
        self._bg_worker_inflight: bool = False

        self.stats: dict[str, int] = {
            "frames_converted_foreground": 0,
            "frames_converted_background": 0,
            "frames_converted_hidden": 0,
            "frames_throttled_background": 0,
            "frames_throttled_hidden": 0,
            "ui_draws_throttled_background": 0,
            "ui_draws_throttled_hidden": 0,
            "power_holds_acquired": 0,
            "power_holds_released": 0,
            "adb_whitelist_runs": 0,
        }

    def _log(self, event: str, detail: str) -> None:
        if callable(self._log_cb):
            with contextlib.suppress(Exception):
                self._log_cb(event, detail)
                return
        h = self.h
        if h is not None and getattr(h, "events", None) is not None:
            with contextlib.suppress(Exception):
                h.events.log(event, detail, feature="media")

    ECO_THROTTLED_PREFIXES = ("desktop", "phone_on_pc", "appstream")

    @staticmethod
    def _norm_feature(feature_id: str) -> str:
        f = str(feature_id or "desktop").strip().lower()
        if f.startswith("wc") and f != "webcam":
            return "webcam"
        return f

    def is_eco_throttled_feature(self, feature_id: str, sid=None) -> bool:
        norm_f = self._norm_feature(feature_id)
        if norm_f == "webcam" or norm_f.startswith("wc"):
            return False
        h = self.h
        if sid is not None and h is not None and hasattr(h, "_live_ws"):
            with contextlib.suppress(Exception):
                c = h._live_ws()
                if isinstance(c, dict) and c.get("wc_session_id") is not None and sid == c.get("wc_session_id"):
                    return False
        return norm_f in ("desktop", "phone_on_pc", "appstream") or norm_f.startswith("appstream:")

    def set_window_state(self, feature_id: str, state: str, sid=None) -> dict:
        norm_f = self._norm_feature(feature_id)
        if not self.is_eco_throttled_feature(norm_f, sid=sid):
            st = "foreground"
        else:
            st = str(state or "foreground").strip().lower()
            if st not in self.VALID_STATES:
                st = "foreground"
        with self._lock:
            prev = self.window_states.get(norm_f)
            self.window_states[norm_f] = st
            if norm_f in ("desktop", "phone_on_pc"):
                self.window_states["desktop"] = st
            if norm_f.startswith("appstream:"):
                app_states = [v for k, v in self.window_states.items() if k.startswith("appstream:")]
                if "foreground" in app_states:
                    self.window_states["appstream"] = "foreground"
                elif "background" in app_states:
                    self.window_states["appstream"] = "background"
                elif app_states:
                    self.window_states["appstream"] = "hidden"
            if sid is not None and self.is_eco_throttled_feature(norm_f, sid=sid):
                self.window_states[f"sid:{sid}"] = st
            if st == "foreground" and prev in ("background", "hidden"):
                self.last_convert_mono.pop(norm_f, None)
                self.last_convert_mono.pop("desktop", None)
                self.last_ui_render_mono.pop(norm_f, None)
                if sid is not None:
                    self.last_convert_mono.pop(f"sid:{sid}", None)
                    self.last_ui_render_mono.pop(f"sid:{sid}", None)
        if prev != st:
            fps = self.get_target_fps(norm_f, sid=sid)
            self._log(
                "STREAM_POWER_STATE",
                f"feature={norm_f} sid={sid} state={prev or 'init'}->{st} target_fps={fps:.1f}",
            )
            self.sync_power_exclusion_async()
        return self.status_dict()

    def get_window_state(self, feature_id: str, sid=None) -> str:
        if not self.background_eco_mode:
            return "foreground"
        norm_f = self._norm_feature(feature_id)
        if not self.is_eco_throttled_feature(norm_f, sid=sid):
            return "foreground"
        with self._lock:
            if sid is not None and f"sid:{sid}" in self.window_states:
                return self.window_states[f"sid:{sid}"]
            if norm_f in self.window_states:
                return self.window_states[norm_f]
            if norm_f == "desktop" and "phone_on_pc" in self.window_states:
                return self.window_states["phone_on_pc"]
            return "foreground"

    def get_target_fps(self, feature_id: str, sid=None) -> float:
        st = self.get_window_state(feature_id, sid=sid)
        if st == "foreground":
            return self.foreground_fps
        if st == "background":
            return self.background_fps
        return self.hidden_fps

    def get_min_frame_interval(self, feature_id: str, sid=None, for_ui: bool = False) -> float:
        st = self.get_window_state(feature_id, sid=sid)
        if st == "foreground":
            return 0.004 if for_ui else 0.0
        if st == "background":
            return 1.0 / max(1.0, self.background_fps)
        return 1.0 / max(0.2, self.hidden_fps)

    def should_convert_frame(
        self,
        owner_service: str,
        sid=None,
        is_first: bool = False,
        now_mono: float | None = None,
    ) -> bool:
        now = time.monotonic() if now_mono is None else float(now_mono)
        st = self.get_window_state(owner_service, sid=sid)
        key = f"sid:{sid}" if sid is not None else self._norm_feature(owner_service)
        with self._lock:
            if is_first or st == "foreground" or not self.background_eco_mode:
                self.last_convert_mono[key] = now
                self.stats["frames_converted_foreground"] += 1
                return True
            min_iv = 1.0 / max(1.0, self.background_fps) if st == "background" else 1.0 / max(0.2, self.hidden_fps)
            last_t = self.last_convert_mono.get(key, 0.0)
            if (now - last_t) >= min_iv:
                self.last_convert_mono[key] = now
                if st == "background":
                    self.stats["frames_converted_background"] += 1
                else:
                    self.stats["frames_converted_hidden"] += 1
                return True
            if st == "background":
                self.stats["frames_throttled_background"] += 1
            else:
                self.stats["frames_throttled_hidden"] += 1
            return False

    def should_render_ui_frame(
        self,
        feature_id: str,
        last_render_mono: float,
        sid=None,
        now_mono: float | None = None,
    ) -> bool:
        now = time.monotonic() if now_mono is None else float(now_mono)
        st = self.get_window_state(feature_id, sid=sid)
        min_iv = self.get_min_frame_interval(feature_id, sid=sid, for_ui=True)
        if (now - float(last_render_mono or 0.0)) < min_iv:
            with self._lock:
                if st == "background":
                    self.stats["ui_draws_throttled_background"] += 1
                elif st == "hidden":
                    self.stats["ui_draws_throttled_hidden"] += 1
            return False
        return True

    def get_ui_poll_interval_s(self, visible_window_states: list[str] | None = None) -> float:
        return 1.0 / 60.0

    def note_service_active(self, service_name: str, active: bool = True) -> None:
        norm_s = self._norm_feature(service_name)
        with self._lock:
            if active:
                self.active_services.add(norm_s)
                if norm_s not in self.window_states:
                    self.window_states[norm_s] = "foreground"
            else:
                self.active_services.discard(norm_s)
                if norm_s == "desktop":
                    self.active_services.discard("phone_on_pc")
        self.sync_power_exclusion_async()

    def _should_hold_performance(self) -> bool:
        if not self.exclude_battery_saver:
            return False
        with self._lock:
            active = set(self.active_services)
            h = self.h
            if h is not None and hasattr(h, "_live_ws"):
                with contextlib.suppress(Exception):
                    c = h._live_ws()
                    if isinstance(c, dict):
                        active.add("connected_session")
                        if c.get("desktop_started") or c.get("as_session_id") is not None:
                            active.add("desktop")
                        if c.get("wc_started"):
                            active.add("webcam")
                        if c.get("ss_started"):
                            active.add("screenshare")
                    if getattr(h, "uc_state", {}).get("connected"):
                        active.add("crosscontrol")
            return bool(active)

    def sync_power_exclusion_async(self) -> None:
        with self._lock:
            if self._bg_worker_inflight:
                return
            self._bg_worker_inflight = True

        def _worker() -> None:
            try:
                self.sync_power_exclusion_now()
            except Exception:
                pass
            finally:
                with self._lock:
                    self._bg_worker_inflight = False

        threading.Thread(target=_worker, name="sc-power-mgr", daemon=True).start()

    def sync_power_exclusion_now(self) -> dict:
        want_hold = self._should_hold_performance()
        if want_hold:
            set_current_thread_timerslack_ns(1)
            if self.h is not None:
                dt = getattr(self.h, "desktop_service_thread", None)
                if dt is not None and getattr(dt, "loop", None) is not None and dt.loop.is_running():
                    with contextlib.suppress(Exception):
                        dt.loop.call_soon_threadsafe(set_current_thread_timerslack_ns, 1)
                serial = getattr(self.h, "moto_adb_serial", None)
                self.whitelist_phone_battery_saver_adb(serial)
            if not self.performance_hold_active:
                self._acquire_linux_performance_hold()
        else:
            set_current_thread_timerslack_ns(1)
            if self.performance_hold_active:
                self._release_linux_performance_hold()
        return self.status_dict()

    def _acquire_linux_performance_hold(self) -> bool:
        gdbus_bin = shutil.which("gdbus")
        method_used = "timerslack-1ns"
        cookie_val: int | None = None

        if gdbus_bin:
            for dest, obj_path, iface in (
                ("org.freedesktop.UPower.PowerProfiles", "/org/freedesktop/UPower/PowerProfiles", "org.freedesktop.UPower.PowerProfiles"),
                ("net.hadess.PowerProfiles", "/net/hadess/PowerProfiles", "net.hadess.PowerProfiles"),
            ):
                for prof in ("performance", "balanced"):
                    try:
                        r = subprocess.run(
                            [
                                gdbus_bin, "call", "--system",
                                "--dest", dest,
                                "--object-path", obj_path,
                                "--method", f"{iface}.HoldProfile",
                                prof,
                                "Samyuj low-latency streaming (exclude from Battery Saver)",
                                "samyuj",
                            ],
                            capture_output=True, text=True, timeout=1.5, check=False,
                        )
                        if r.returncode == 0 and r.stdout:
                            nums = re.findall(r"(\d+)", r.stdout)
                            if nums:
                                cookie_val = int(nums[-1])
                                self._ppd_cookie = cookie_val
                                self._ppd_bus_dest = dest
                                self._ppd_obj_path = obj_path
                                self._ppd_iface = iface
                                method_used = f"power-profiles-daemon:{prof}(cookie={cookie_val})"
                                break
                    except Exception:
                        continue
                if cookie_val is not None:
                    break

            try:
                r_ss = subprocess.run(
                    [
                        gdbus_bin, "call", "--session",
                        "--dest", "org.freedesktop.ScreenSaver",
                        "--object-path", "/org/freedesktop/ScreenSaver",
                        "--method", "org.freedesktop.ScreenSaver.Inhibit",
                        "samyuj",
                        "Samyuj active stream",
                    ],
                    capture_output=True, text=True, timeout=1.5, check=False,
                )
                if r_ss.returncode == 0 and r_ss.stdout:
                    m_ss = re.search(r"(\d+)", r_ss.stdout)
                    if m_ss:
                        self._screensaver_cookie = int(m_ss.group(1))
                        if method_used == "timerslack-1ns":
                            method_used = f"screensaver-inhibit(cookie={self._screensaver_cookie})"
            except Exception:
                pass

        with self._lock:
            self.performance_hold_active = True
            self.performance_hold_method = method_used
            self.stats["power_holds_acquired"] += 1
        self._log("POWER_SAVER_EXCLUSION_ON", f"method={method_used} (streaming in foreground)")
        return True

    def _release_linux_performance_hold(self) -> None:
        gdbus_bin = shutil.which("gdbus")
        with self._lock:
            cookie = self._ppd_cookie
            dest = self._ppd_bus_dest
            obj_path = self._ppd_obj_path
            iface = self._ppd_iface
            ss_cookie = self._screensaver_cookie
            self._ppd_cookie = None
            self._screensaver_cookie = None
            was_active = self.performance_hold_active
            prev_method = self.performance_hold_method
            self.performance_hold_active = False
            self.performance_hold_method = "none"
            if was_active:
                self.stats["power_holds_released"] += 1

        if gdbus_bin and cookie is not None and dest and obj_path and iface:
            with contextlib.suppress(Exception):
                subprocess.run(
                    [
                        gdbus_bin, "call", "--system",
                        "--dest", dest,
                        "--object-path", obj_path,
                        "--method", f"{iface}.ReleaseProfile",
                        str(cookie),
                    ],
                    capture_output=True, text=True, timeout=1.5, check=False,
                )
        if gdbus_bin and ss_cookie is not None:
            with contextlib.suppress(Exception):
                subprocess.run(
                    [
                        gdbus_bin, "call", "--session",
                        "--dest", "org.freedesktop.ScreenSaver",
                        "--object-path", "/org/freedesktop/ScreenSaver",
                        "--method", "org.freedesktop.ScreenSaver.UnInhibit",
                        str(ss_cookie),
                    ],
                    capture_output=True, text=True, timeout=1.5, check=False,
                )
        if was_active:
            self._log("POWER_SAVER_EXCLUSION_OFF", f"released {prev_method} (stream backgrounded or idle)")

    def whitelist_phone_battery_saver_adb(self, serial: str | None = None, force: bool = False) -> bool:
        if not self.exclude_battery_saver:
            return False
        adb_bin = shutil.which("adb")
        if not adb_bin:
            return False
        key = str(serial or "__default__")
        with self._lock:
            if not force and key in self._adb_whitelisted_serials:
                return True
            self._adb_whitelisted_serials.add(key)

        extra_pkgs = tuple(
            str(p).strip()
            for p in ((getattr(self.h, "user_settings", {}) or {}).get("extra_smartconnect_pkgs") or ())
            if str(p).strip() and re.fullmatch(r"[A-Za-z0-9_.]+", str(p).strip())
        )
        all_pkgs = tuple(dict.fromkeys(ANDROID_SMARTCONNECT_PKGS + extra_pkgs))
        cmds = []
        for pkg in all_pkgs:
            cmds.append(f"dumpsys deviceidle whitelist +{pkg} >/dev/null 2>&1 || true")
            cmds.append(f"cmd deviceidle whitelist +{pkg} >/dev/null 2>&1 || true")
            cmds.append(f"cmd appops set {pkg} RUN_IN_BACKGROUND allow >/dev/null 2>&1 || true")
            cmds.append(f"cmd appops set {pkg} RUN_ANY_IN_BACKGROUND allow >/dev/null 2>&1 || true")
            cmds.append(f"cmd appops set {pkg} WAKE_LOCK allow >/dev/null 2>&1 || true")
        shell_line = "; ".join(cmds)
        full = [adb_bin] + (["-s", serial] if serial else []) + ["shell", shell_line]
        try:
            r = subprocess.run(full, capture_output=True, text=True, timeout=4.0, check=False)
            if r.returncode == 0:
                with self._lock:
                    self.stats["adb_whitelist_runs"] += 1
                self._log(
                    "PHONE_BATTERY_SAVER_EXCLUDED",
                    f"serial={serial or 'any'} whitelisted {all_pkgs} from Android Doze/Battery Saver",
                )
                return True
        except Exception:
            pass
        with self._lock:
            self._adb_whitelisted_serials.discard(key)
        return False

    def status_dict(self) -> dict:
        with self._lock:
            return {
                "exclude_battery_saver": self.exclude_battery_saver,
                "background_eco_mode": self.background_eco_mode,
                "foreground_fps": self.foreground_fps,
                "background_fps": self.background_fps,
                "hidden_fps": self.hidden_fps,
                "performance_hold_active": self.performance_hold_active,
                "performance_hold_method": self.performance_hold_method,
                "active_services": sorted(self.active_services),
                "window_states": dict(self.window_states),
                "stats": dict(self.stats),
            }
