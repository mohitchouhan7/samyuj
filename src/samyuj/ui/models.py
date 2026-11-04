from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

@dataclass(frozen=True)
class ConnectionSummary:
    connected: bool = False
    paired: bool = False
    transport: str = "standby"
    phone_name: str = "Phone"
    phone_ip: str = ""
    pc_ip: str = "127.0.0.1"
    port: int = 0
    usb_status: str = "standby"
    battery_pct: int = 0
    is_charging: bool = False
    storage_free_gb: float = 0.0
    storage_total_gb: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

@dataclass(frozen=True)
class FeatureWindowModel:
    window_id: str
    feature_type: str
    title: str
    session_id: int | str | None = None
    display_id: int | None = None
    pkg: str = ""
    width: int = 1280
    height: int = 720
    fps: float = 0.0
    status: str = "idle"
    power_state: str = "foreground"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

@dataclass(frozen=True)
class DashboardWidgetModel:
    widget_id: str
    source_id: str
    title: str
    subtitle: str = ""
    active: bool = False
    items: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["items"] = list(self.items)
        return d

@dataclass(frozen=True)
class SupportReportModel:
    generated_utc: str
    ok: bool
    verdict: str
    summary: dict[str, Any] = field(default_factory=dict)
    setup_check: dict[str, Any] = field(default_factory=dict)
    recent_events: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["recent_events"] = list(self.recent_events)
        return d
