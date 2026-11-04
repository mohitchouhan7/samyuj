from __future__ import annotations

import collections
import threading
from typing import Any, Callable

class EventBus:

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._subscribers: dict[str, list[Callable[..., Any]]] = collections.defaultdict(list)

    def subscribe(self, topic: str, callback: Callable[..., Any]) -> Callable[[], None]:
        with self._lock:
            if callback not in self._subscribers[topic]:
                self._subscribers[topic].append(callback)

        def _unsubscribe() -> None:
            with self._lock:
                if callback in self._subscribers.get(topic, ()):
                    self._subscribers[topic].remove(callback)

        return _unsubscribe

    def publish(self, topic: str, *args: Any, **kwargs: Any) -> None:
        with self._lock:
            callbacks = list(self._subscribers.get(topic, ())) + list(self._subscribers.get("*", ()))
        for cb in callbacks:
            try:
                cb(*args, **kwargs)
            except Exception:
                pass

def feature_of_tag(tag: str) -> str:
    t = str(tag or "").upper()
    for prefix, feat in (
        ("DESKTOP", "desktop"),
        ("APPSTREAM", "appstream"),
        ("WEBCAM", "webcam"),
        ("SCREENSHARE", "screenshare"),
        ("CLIP", "clipboard"),
        ("NOTIF", "notifications"),
        ("MSG", "messages"),
        ("MESSAGES", "messages"),
        ("GALLERY", "gallery"),
        ("FILE", "files"),
        ("NFS", "nfs"),
        ("PAIR", "pairing"),
        ("CROSS", "crosscontrol"),
        ("UNIVERSAL", "crosscontrol"),
        ("INPUT", "input"),
    ):
        if prefix in t:
            return feat
    return "core"

_feature_of_tag = feature_of_tag

def _console_show(feature: str, label: str = "") -> bool:
    return False

def _feature_of_sid(frame: dict | None) -> str:
    if not isinstance(frame, dict):
        return "core"
    sid = frame.get("serviceId")
    return {
        0: "pairing",
        1: "desktop",
        2: "files",
        3: "webcam",
        5: "notifications",
        9: "clipboard",
        10: "desktop",
        15: "appstream",
        16: "input",
        17: "applist",
        19: "pairing",
        22: "control",
        25: "telemetry",
        26: "crosscontrol",
        168: "nfs",
        202: "screenshare",
        204: "messages",
        205: "gallery",
        1001: "gallery",
    }.get(sid, "core")

_SID_FEATURE = {
    0: "pairing", 1: "mobile_desktop", 3: "webcam", 9: "clipboard",
    15: "appstream", 16: "input", 17: "app_list", 19: "pairing", 20: "file_transfer",
    21: "audio", 22: "control", 25: "pairing", 27: "pairing",
    168: "file_transfer", 202: "screen_share", 204: "messages", 205: "gallery",
    1001: "features",
}
_TAG_FEATURE = (
    ("DESKTOP", "mobile_desktop"), ("APPSTREAM", "appstream"),
    ("SCREENSHARE", "screen_share"), ("SS_", "screen_share"),
    ("WEBCAM", "webcam"), ("APPLIST", "app_list"), ("APPICON", "app_list"),
    ("APP_", "app_list"), ("WALLPAPER", "wallpaper"), ("DND", "pairing"),
    ("TELEMETRY", "pairing"), ("RQ_", "core"),
    ("MEDIA", "media"), ("INPUT", "input"), ("FS_", "file_transfer"),
    ("FILESTATION", "file_transfer"), ("DEVSTATUS", "pairing"),
    ("EXCHANGE_KEY", "pairing"), ("CHECKIN", "pairing"),
    ("REPLY", "pairing"), ("SERVER_FIRST", "pairing"), ("DIAL", "pairing"),
    ("SESSION", "mobile_desktop"),
)

feature_of_tag = _feature_of_tag
