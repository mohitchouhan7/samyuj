from __future__ import annotations

from typing import Any

from samyuj.features import FEATURE_BY_KEY, FEATURES, FeatureSpec
from samyuj.services.pairing import qr_png_bytes
from .models import (
    ConnectionSummary,
    FeatureWindowModel,
    SupportReportModel,
)

class ApplicationFacade:

    def __init__(self, runtime: Any) -> None:
        raw_rt = getattr(runtime, "_runtime", runtime)
        object.__setattr__(self, "_runtime", raw_rt)

    @property
    def runtime(self) -> Any:
        return object.__getattribute__(self, "_runtime")

    def list_features(self) -> tuple[FeatureSpec, ...]:
        return FEATURES

    feature_catalog = list_features

    def get_feature_spec(self, key: str) -> FeatureSpec | None:
        return FEATURE_BY_KEY.get(str(key).strip().lower())

    def get_connection_summary(self) -> ConnectionSummary:
        rt = self.runtime
        c = rt._live_ws() if hasattr(rt, "_live_ws") else None
        if not c:
            return ConnectionSummary(
                connected=False,
                paired=False,
                transport="standby",
                phone_name="Not connected",
                phone_ip="",
                pc_ip=str(getattr(rt, "wifi_ip", "") or ""),
                port=int(getattr(rt, "port", 0) or 0),
            )
        is_usb = bool(rt._is_usb_conn(c)) if hasattr(rt, "_is_usb_conn") else False
        peer = c.get("peer")
        ip_str = str(peer[0]) if isinstance(peer, (tuple, list)) and peer else ""
        st = getattr(rt, "phone_storage", {}) or {}
        return ConnectionSummary(
            connected=True,
            paired=bool(c.get("key_sent") or getattr(rt, "shared_key", None)),
            transport="usb" if is_usb else "wifi",
            phone_name=rt._best_device_name(c) if hasattr(rt, "_best_device_name") else "Phone",
            phone_ip=ip_str,
            pc_ip=str(getattr(rt, "wifi_ip", "") or ""),
            port=int(getattr(rt, "port", 0) or 0),
            battery_pct=int((getattr(rt, "phone_telemetry", {}) or {}).get("battery") or 0),
            is_charging=bool((getattr(rt, "phone_telemetry", {}) or {}).get("isCharging")),
            storage_free_gb=round(float(st.get("free_bytes") or 0) / (1024 ** 3), 2),
            storage_total_gb=round(float(st.get("total_bytes") or 0) / (1024 ** 3), 2),
        )

    def get_pairing_qr_png(self, force_refresh: bool = False) -> bytes:
        rt = self.runtime
        if hasattr(rt, "get_pairing_qr_png"):
            return rt.get_pairing_qr_png(force_refresh=force_refresh)
        payload = getattr(rt, "qr_payload", "") or ""
        return qr_png_bytes(payload) if payload else b""

    def get_feature_windows(self) -> list[FeatureWindowModel]:
        rt = self.runtime
        snap = rt.live_feature_snapshot() if hasattr(rt, "live_feature_snapshot") else {}
        out: list[FeatureWindowModel] = []
        for key in ("desktop", "appstream", "webcam", "screenshare", "crosscontrol"):
            info = snap.get(key) if isinstance(snap, dict) else None
            if isinstance(info, dict):
                out.append(
                    FeatureWindowModel(
                        window_id=f"win-{key}",
                        feature_type=key,
                        title=key.capitalize(),
                        status="active" if (info.get("on") or info.get("active")) else str(info.get("phase") or "idle"),
                        fps=float(info.get("fps") or 0.0),
                        width=int(info.get("width") or 1280),
                        height=int(info.get("height") or 720),
                    )
                )
        return out

    def get_support_report(self) -> SupportReportModel:
        import datetime
        rt = self.runtime
        summary = self.get_connection_summary()
        diag = getattr(rt, "media_diagnostics", None)
        verdict = diag.verdict() if diag is not None and hasattr(diag, "verdict") else {}
        ev = getattr(rt, "events", None)
        lines = tuple(list(getattr(ev, "recent_lines", []) or [])[-50:])
        return SupportReportModel(
            generated_utc=datetime.datetime.now(datetime.UTC).isoformat(),
            ok=bool(summary.connected),
            verdict=str(verdict.get("status") if isinstance(verdict, dict) and verdict.get("status") else ("connected" if summary.connected else "standby")),
            summary=summary.to_dict(),
            setup_check=verdict if isinstance(verdict, dict) else {},
            recent_events=lines,
        )

    async def invoke_service(
        self,
        service_id: str,
        action: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        bus = getattr(self.runtime, "service_bus", None)
        if bus is None:
            return {"ok": False, "error": "service_bus unavailable"}
        return await bus.invoke(service_id, action, params or {})

    async def send_files_svc2(
        self,
        conn: Any = None,
        paths: list[str] | None = None,
        mode: str = "auto",
    ) -> Any:
        return await self.runtime.send_files_svc2(conn, paths, mode=mode)

    async def request(self, command: str, conn: dict[str, Any] | None = None, arg: str = "") -> dict[str, Any]:
        return await self.runtime.request(command, conn=conn, arg=arg)

    def request_stop(self) -> None:
        self.runtime.request_stop()

    def update_setting(self, key: str, value: Any) -> None:
        rt = self.runtime
        us = getattr(rt, "user_settings", None)
        if isinstance(us, dict):
            us[key] = value
        setattr(rt, key, value)
        if hasattr(rt, "save_user_settings"):
            rt.save_user_settings()

    def set_remb_floor(self, kbps: int) -> int:
        from samyuj.media.remb import set_remb_floor as _set_remb_floor
        bps = _set_remb_floor(int(kbps))
        self.update_setting("remb_floor_kbps", int(kbps))
        return bps

    def set_uv_swap(self, enabled: bool) -> bool:
        return bool(self.runtime.set_uv_swap(bool(enabled)))

    def __getattr__(self, name: str) -> Any:
        return getattr(object.__getattribute__(self, "_runtime"), name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "_runtime":
            object.__setattr__(self, name, value)
        else:
            setattr(object.__getattribute__(self, "_runtime"), name, value)
