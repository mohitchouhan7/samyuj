from __future__ import annotations

import contextlib
import json
from pathlib import Path

from samyuj.media.frame_hub import apply_uv_swap_state, is_uv_swapped

class RuntimeSettingsMixin:

    def get_resolution_summary(self) -> dict:
        c = self._live_ws()
        st = c.get("media_last_frame") if c else None
        fr = st[0] if st else None
        pr = self.phone_resolutions
        pr["desktop"]["current"] = {"width": int(self.desktop_res[0]), "height": int(self.desktop_res[1])}
        pr["appstream"]["stream"]["width"] = int((c.get("as_geom") if c and c.get("as_geom") else self.desktop_res)[0])
        pr["appstream"]["stream"]["height"] = int((c.get("as_geom") if c and c.get("as_geom") else self.desktop_res)[1])
        pr["appstream"]["stream"]["density"] = int(self.as_density)
        pr["appstream"]["stream"]["scale"] = float(self.as_scale)
        pr["appstream"]["stream"]["displayId"] = c.get("as_display_id") if c else None
        pr["appstream"]["customDisplay"] = bool(getattr(self, "as_custom_display", True))
        if self._ss_track is not None:
            pr["screenshare"]["active"]["width"] = int(self._ss_track.width)
            pr["screenshare"]["active"]["height"] = int(self._ss_track.height)
        else:
            pr["screenshare"]["active"]["width"] = int(self.ss_res[0])
            pr["screenshare"]["active"]["height"] = int(self.ss_res[1])
        pr["webcam"]["pcOutput"] = (
            {"width": int(self.webcam_output_res[0]), "height": int(self.webcam_output_res[1])}
            if getattr(self, "webcam_output_res", None) else None
        )
        if fr is not None:
            pr["liveFrame"] = {"width": int(fr.width), "height": int(fr.height)}
        else:
            pr["liveFrame"] = None
        return pr

    def _settings_file(self) -> Path:
        return Path(self.state_dir) / "settings.json"

    def _restore_saved_geometry(self) -> None:
        if getattr(self, "_desktop_res_from_cli", False):
            return
        with contextlib.suppress(Exception):
            raw = str(self.user_settings.get("desktop_res") or "").strip().lower()
            if "x" in raw:
                rw, rh = (max(64, int(x) & ~1) for x in raw.split("x", 1))
                self.desktop_res = (rw, rh)
                self.desktop_rotation = 1 if rh > rw else 0

    def load_user_settings(self) -> dict:
        with contextlib.suppress(OSError, json.JSONDecodeError, TypeError, ValueError):
            sf = self._settings_file()
            if sf.is_file():
                loaded = json.loads(sf.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    if (loaded.get("video_codec") == "auto"
                            and not loaded.get("video_codec_r22_migrated")):
                        loaded["video_codec"] = "h264"
                        loaded["video_codec_r22_migrated"] = True
                        with contextlib.suppress(OSError):
                            sf.write_text(json.dumps(loaded, indent=2), encoding="utf-8")
                    if (loaded.get("ss_virtual_res") in ("1920x1080", "1080x1920")
                            and not loaded.get("ss_virtual_res_r26_migrated")):
                        loaded["ss_virtual_res"] = (
                            "1080x2400" if loaded.get("ss_virtual_res") == "1080x1920"
                            else "2400x1080"
                        )
                        loaded["ss_virtual_res_r26_migrated"] = True
                        with contextlib.suppress(OSError):
                            sf.write_text(json.dumps(loaded, indent=2), encoding="utf-8")
                    if not loaded.get("uv_swap_r27_migrated"):
                        loaded["uv_swap"] = True
                        loaded["uv_swap_r27_migrated"] = True
                        with contextlib.suppress(OSError):
                            sf.write_text(json.dumps(loaded, indent=2), encoding="utf-8")
                    if (loaded.get("ss_share_type") == "extend"
                            and not loaded.get("ss_share_type_r27_migrated")):
                        loaded["ss_share_type"] = "mirror"
                        loaded["ss_share_type_r27_migrated"] = True
                        with contextlib.suppress(OSError):
                            sf.write_text(json.dumps(loaded, indent=2), encoding="utf-8")
                    if not loaded.get("theme_aurora_mocha_migrated"):
                        if loaded.get("ui_theme") in (None, "", "material", "tray"):
                            loaded["ui_theme"] = "aurora"
                        if loaded.get("color_palette") in (None, "", "material_you", "carbon"):
                            loaded["color_palette"] = "mocha"
                        loaded["theme_aurora_mocha_migrated"] = True
                        with contextlib.suppress(OSError):
                            sf.write_text(json.dumps(loaded, indent=2), encoding="utf-8")
                    self.user_settings.update(loaded)
        self.prefer_usb = bool(self.user_settings.get("connect_usb", True))
        self.uv_swap = bool(self.user_settings.get("uv_swap", True))
        with contextlib.suppress(Exception):
            if str(self.user_settings.get("ss_share_type") or "") in ("extend", "mirror"):
                self.ss_share_type = str(self.user_settings["ss_share_type"])
            raw = str(self.user_settings.get("ss_virtual_res") or "").strip().lower()
            if "x" in raw:
                vw, vh = (max(160, int(x)) for x in raw.split("x", 1))
                self.ss_virtual_res = (vw, vh)
            if str(self.user_settings.get("ss_virtual_orient") or "") in (
                    "auto", "landscape", "portrait"):
                self.ss_virtual_orient = str(self.user_settings["ss_virtual_orient"])
            if str(self.user_settings.get("ss_virtual_pos") or "") in (
                    "right", "left", "above", "below"):
                self.ss_virtual_pos = str(self.user_settings["ss_virtual_pos"])
                with contextlib.suppress(Exception):
                    self.virtual_display.set_position(self.ss_virtual_pos)
        return dict(self.user_settings)

    def save_user_settings(self, **updates) -> dict:
        if updates:
            if "ss_virtual_res" in updates:
                updates["ss_virtual_res_r26_migrated"] = True
            if "uv_swap" in updates:
                updates["uv_swap_r27_migrated"] = True
                self.uv_swap = bool(updates["uv_swap"])
            if "ss_share_type" in updates:
                updates["ss_share_type_r27_migrated"] = True
            if "ui_theme" in updates or "color_palette" in updates:
                updates["theme_aurora_mocha_migrated"] = True
            self.user_settings.update(updates)
        self.prefer_usb = bool(self.user_settings.get("connect_usb", True))
        with contextlib.suppress(OSError):
            Path(self.state_dir).mkdir(parents=True, exist_ok=True)
            self._settings_file().write_text(
                json.dumps(self.user_settings, indent=2), encoding="utf-8"
            )
        return dict(self.user_settings)

    def set_uv_swap(self, on: bool | None = None) -> bool:
        new_state = (not bool(getattr(self, "uv_swap", True))) if on is None else bool(on)
        self.uv_swap = new_state
        with contextlib.suppress(Exception):
            self.save_user_settings(uv_swap=new_state)
        with contextlib.suppress(Exception):
            reg = getattr(self, "stream_registry", None)
            if reg is not None:
                for _srec in reg.streams.values():
                    if _srec.owner_service == "webcam" and _srec.last_frame is not None:
                        _was = is_uv_swapped(_srec.last_frame)
                        _flipped, swapped = apply_uv_swap_state(_srec.last_frame, new_state)
                        _srec.last_frame = _flipped
                        if swapped != _was:
                            _srec.uv_swap_applied = swapped
                            _srec.seq += 1
            c = self._live_ws()
            if c is not None:
                t = c.get("webcam_last_frame")
                if isinstance(t, (tuple, list)) and len(t) >= 1 and t[0] is not None:
                    f2, _sw2 = apply_uv_swap_state(t[0], new_state)
                    c["webcam_last_frame"] = (f2, *list(t)[1:])
                bysvc = c.get("media_last_frame_by_service")
                if isinstance(bysvc, dict) and isinstance(bysvc.get("webcam"), (tuple, list)):
                    t3 = bysvc["webcam"]
                    if t3 and t3[0] is not None:
                        f3, _sw3 = apply_uv_swap_state(t3[0], new_state)
                        bysvc["webcam"] = (f3, *list(t3)[1:])
        with contextlib.suppress(Exception):
            self.events.log("WEBCAM_UV_SWAP",
                            f"uv_swap={new_state} (persisted + cached frames flipped)",
                            feature="webcam", service_id=3)
        return new_state

    def get_incoming_files_dir(self) -> Path:
        raw = str(self.user_settings.get("incoming_files_dir") or "").strip()
        if not raw:
            raw = str(Path.home() / "Downloads" / "Samyuj")
        p = Path(raw).expanduser()
        with contextlib.suppress(OSError):
            p.mkdir(parents=True, exist_ok=True)
        return p

    def set_incoming_files_dir(self, path_str: str) -> dict:
        raw = str(path_str or "").strip()
        if not raw:
            return {"ok": False, "error": "empty path"}
        p = Path(raw).expanduser().resolve()
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        self.save_user_settings(incoming_files_dir=str(p))
        self.events.log("SETTINGS_INCOMING_DIR", f"Incoming phone files directory set to {p}", feature="file_transfer")
        return {"ok": True, "incoming_files_dir": str(p)}

    def _best_device_name(self, conn: dict | None = None) -> str:
        c = conn or self._live_ws()
        if c:
            h_fr = c.get("hello") or {}
            h_dev = ((h_fr.get("data") or {}).get("device") or {}) if isinstance(h_fr.get("data"), dict) else {}
            nm = str(h_dev.get("deviceName") or h_dev.get("model") or "").strip()
            if nm:
                return nm
        tnm = str(self.phone_telemetry.get("deviceName") or self.phone_telemetry.get("model") or "").strip()
        if tnm:
            return tnm
        try:
            saved = self.list_saved_phones()
            for sp in saved:
                snm = str(sp.get("name") or "").strip()
                if snm and snm.lower() not in ("unknown", "none"):
                    return snm
        except Exception:
            pass
        for d in reversed(getattr(self, "discovered_phones", []) or []):
            dnm = str(d.get("device_name") or d.get("name") or "").strip()
            if dnm and not dnm.startswith("_"):
                return dnm
        return "Connected Phone" if c else "Phone"
