from __future__ import annotations

from .models import ServiceSessionState

class ServiceSessionRegistry:

    SERVICE_MAP = {
        "desktop": 1,
        "filetransfer": 2,
        "webcam": 3,
        "appstream": 15,
        "input": 16,
        "filestation": 168,
        "screenshare": 202,
    }

    def __init__(self) -> None:
        self.services: dict[str, ServiceSessionState] = {
            name: ServiceSessionState(service_id=sid, name=name)
            for name, sid in self.SERVICE_MAP.items()
        }

    def get(self, name: str) -> ServiceSessionState:
        if name not in self.services:
            self.services[name] = ServiceSessionState(service_id=0, name=name)
        return self.services[name]

    def active_names(self) -> list[str]:
        return [
            name
            for name, st in self.services.items()
            if st.status in ("starting", "negotiating", "live")
        ]

    def primary_feature(self) -> str | None:
        for pref in ("desktop", "appstream", "screenshare", "webcam"):
            if self.services[pref].status in ("starting", "negotiating", "live"):
                return pref
        return None
