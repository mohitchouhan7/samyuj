from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class FeatureSpec:
    key: str
    title: str
    subtitle: str
    service_ids: tuple[int, ...]
    service_versions: dict[int, int]
    pc_caps: dict[int, int]
    gtk_page: str | None
    service_factory: str | None

FEATURES: tuple[FeatureSpec, ...] = (
    FeatureSpec(
        key="pairing",
        title="Pairing & Connect",
        subtitle="QR, Wi-Fi LAN, USB Tethering, and BLE discovery",
        service_ids=(0, 19),
        service_versions={0: 3, 19: 1, 13: 1, 18: 1, 32: 2, 36: 1},
        pc_caps={19: 3, 13: 3, 27: 3, 31: 3, 311: 3, 313: 3, 201: 3},
        gtk_page="samyuj.ui.gtk.pages.home:HomePage",
        service_factory="samyuj.services.pairing:PairingService",
    ),
    FeatureSpec(
        key="desktop",
        title="Mobile Desktop",
        subtitle="Full multi-window desktop environment from your phone",
        service_ids=(1, 15, 16, 10),
        service_versions={1: 10, 15: 17, 16: 6, 10: 1, 24: 2},
        pc_caps={1: 3, 15: 3, 16: 3, 10: 3, 24: 3},
        gtk_page="samyuj.ui.gtk.pages.stream_setup:MobileDesktopSetupPage",
        service_factory="samyuj.services.desktop:MobileDesktopService",
    ),
    FeatureSpec(
        key="appstream",
        title="App Streaming",
        subtitle="Stream individual Android apps in Linux desktop windows",
        service_ids=(15, 17),
        service_versions={15: 17, 17: 1},
        pc_caps={15: 3, 17: 3},
        gtk_page="samyuj.ui.gtk.pages.apps:AppsPage",
        service_factory="samyuj.services.appstream:AppStreamService",
    ),
    FeatureSpec(
        key="webcam",
        title="Webcam",
        subtitle="Use your phone camera as a high-definition Linux V4L2 webcam",
        service_ids=(3,),
        service_versions={3: 2},
        pc_caps={3: 3},
        gtk_page="samyuj.ui.gtk.windows.stream_window:FeatureStreamGtkWindow",
        service_factory="samyuj.services.webcam:WebcamFeatureService",
    ),
    FeatureSpec(
        key="screenshare",
        title="Screen Share",
        subtitle="Mirror or extend your Linux desktop to your phone or tablet",
        service_ids=(202,),
        service_versions={202: 2},
        pc_caps={202: 3},
        gtk_page="samyuj.ui.gtk.pages.stream_setup:ScreenShareHubPage",
        service_factory="samyuj.services.screenshare:ScreenShareService",
    ),
    FeatureSpec(
        key="files",
        title="File Sharing & NFS",
        subtitle="Bidirectional NFSv3 / CNFS v3 storage mount and file drop",
        service_ids=(168, 2),
        service_versions={168: 1, 2: 4, 20: 2},
        pc_caps={168: 3, 2: 3, 20: 3, 206: 3},
        gtk_page="samyuj.ui.gtk.pages.files:FilesPage",
        service_factory="samyuj.services.files:FileSharingService",
    ),
    FeatureSpec(
        key="clipboard",
        title="Smart Clipboard",
        subtitle="Event-driven bidirectional text & file clipboard sync",
        service_ids=(9,),
        service_versions={9: 4},
        pc_caps={9: 17},
        gtk_page="samyuj.ui.gtk.widgets.common:ClipboardDialog",
        service_factory="samyuj.services.clipboard:ClipboardService",
    ),
    FeatureSpec(
        key="notifications",
        title="Notifications",
        subtitle="Live phone notifications mirrored to your Linux desktop",
        service_ids=(5, 35, 100),
        service_versions={5: 1, 35: 1},
        pc_caps={5: 17},
        gtk_page="samyuj.ui.gtk.pages.notifications:NotificationsPage",
        service_factory="samyuj.services.notifications:NotificationService",
    ),
    FeatureSpec(
        key="gallery",
        title="Gallery",
        subtitle="Browse, preview, upload, and download phone photos & media",
        service_ids=(205, 1001, 168),
        service_versions={205: 1, 1001: 1, 168: 1},
        pc_caps={205: 3, 168: 3},
        gtk_page="samyuj.ui.gtk.pages.gallery:GalleryPage",
        service_factory="samyuj.services.gallery:GalleryService",
    ),
    FeatureSpec(
        key="messages",
        title="Messages",
        subtitle="Read and send SMS/MMS messages through your connected phone",
        service_ids=(204,),
        service_versions={204: 4},
        pc_caps={204: 17},
        gtk_page="samyuj.ui.gtk.pages.messages:MessagesPage",
        service_factory="samyuj.services.messages:MessagesService",
    ),
    FeatureSpec(
        key="cross_control",
        title="Cross Control",
        subtitle="Seamless screen-edge mouse and keyboard sharing across devices",
        service_ids=(26, 16),
        service_versions={26: 2, 16: 6},
        pc_caps={26: 3, 16: 3},
        gtk_page="samyuj.ui.gtk.windows.cross_control:ScreenEdgeCrossController",
        service_factory="samyuj.services.cross_control:CrossControlService",
    ),
    FeatureSpec(
        key="control",
        title="Control & Telemetry",
        subtitle="Device status, wallpaper sync, lock control, and diagnostics",
        service_ids=(22, 25),
        service_versions={22: 2, 25: 2},
        pc_caps={22: 3, 25: 3},
        gtk_page="samyuj.ui.gtk.pages.settings:SettingsPage",
        service_factory="samyuj.services.control:ControlTelemetryService",
    ),
)

FEATURE_BY_KEY: dict[str, FeatureSpec] = {f.key: f for f in FEATURES}

def get_feature(key: str) -> FeatureSpec | None:
    return FEATURE_BY_KEY.get(key)

def feature_for_service_id(service_id: int) -> FeatureSpec | None:
    sid = int(service_id)
    primary = {
        0: "pairing",
        19: "pairing",
        1: "desktop",
        10: "desktop",
        15: "appstream",
        17: "appstream",
        3: "webcam",
        202: "screenshare",
        168: "files",
        2: "files",
        9: "clipboard",
        5: "notifications",
        35: "notifications",
        100: "notifications",
        205: "gallery",
        1001: "gallery",
        204: "messages",
        26: "cross_control",
        16: "cross_control",
        22: "control",
        25: "control",
    }
    if sid in primary:
        return FEATURE_BY_KEY.get(primary[sid])
    for spec in FEATURES:
        if sid in spec.service_ids:
            return spec
    return None

def build_service_versions() -> dict[str, int]:
    merged: dict[str, int] = {}
    for spec in FEATURES:
        for sid, ver in spec.service_versions.items():
            merged[str(int(sid))] = int(ver)
    return merged

def build_default_pc_caps() -> dict[str, int]:
    caps: dict[str, int] = {"-1": 3}
    for spec in FEATURES:
        for sid, bits in spec.pc_caps.items():
            caps[str(int(sid))] = int(bits)
    return caps

def resolve_lazy_symbol(lazy_path: str) -> Any:
    if not lazy_path or ":" not in lazy_path:
        raise ValueError(f"Invalid lazy import path: {lazy_path!r}")
    mod_name, _, attr_name = lazy_path.partition(":")
    mod = importlib.import_module(mod_name)
    return getattr(mod, attr_name)
