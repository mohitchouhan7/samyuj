from __future__ import annotations

from typing import TYPE_CHECKING, Any

from samyuj.features import FEATURES

if TYPE_CHECKING:
    from samyuj.session.lifecycle import SamyujRuntime as Harness

class SamyujService:

    service_id: int = 0
    id: str = "base"
    name: str = "Base Service"
    description: str = ""
    delegated_actions: dict[str, str] = {}

    def __init__(self, harness: "Harness") -> None:
        self.h = harness
        self.ctx = getattr(harness, "ctx", None)

    def actions(self) -> list[dict]:
        return []

    def get_state(self) -> dict:
        return {"id": self.id, "service_id": self.service_id, "name": self.name}

    async def handle_frame(self, conn: dict, frame: dict, data: Any, cmd: int | None, tag: str) -> bool:
        routes = getattr(self.h, "POST_HELLO_ROUTES", {})
        sid = frame.get("serviceId", self.service_id)
        handler_name = routes.get(int(sid)) if sid is not None else None
        if handler_name:
            handler = getattr(self.h, handler_name, None)
            if callable(handler):
                return bool(await handler(conn, frame, data, cmd, tag))
        return False

    async def invoke(self, action: str, params: dict | None = None,
                     conn: dict | None = None) -> dict:
        return {"ok": False, "error": f"unknown action {action!r} on {self.id}"}

class ServiceBus:

    def __init__(self, harness: "Harness") -> None:
        from .applist import AppListIconService
        from .appstream import AppStreamService
        from .clipboard import ClipboardService
        from .control import ControlService, TelemetryService
        from .cross_control import CrossControlService
        from .desktop import MobileDesktopService, ResolutionManagerService
        from .files import FileStationNfsService, FileTransferService
        from .gallery import FeaturesService, GalleryService
        from .input_service import InputDragDropService
        from .messages import MessagesService
        from .notifications import NotificationService
        from .pairing import PairDeviceService, PairingService
        from .screenshare import ScreenShareService
        from .transport_service import TransportService
        from .webcam import WebcamService

        self.h = harness
        self.services: dict[str, SamyujService] = {}
        self.by_sid: dict[int, list[SamyujService]] = {}
        self.feature_specs = FEATURES
        for cls in (
            TransportService,
            PairingService,
            TelemetryService,
            ResolutionManagerService,
            ScreenShareService,
            WebcamService,
            AppListIconService,
            MobileDesktopService,
            AppStreamService,
            ClipboardService,
            NotificationService,
            InputDragDropService,
            FileTransferService,
            CrossControlService,
            FileStationNfsService,
            ControlService,
            PairDeviceService,
            GalleryService,
            FeaturesService,
            MessagesService,
        ):
            svc = cls(harness)
            self.register(svc)

    def register(self, svc: SamyujService) -> None:
        self.services[svc.id] = svc
        self.by_sid.setdefault(int(svc.service_id), []).append(svc)

    def catalog(self) -> list[dict]:
        return [
            {
                "id": s.id,
                "service_id": s.service_id,
                "name": s.name,
                "description": s.description,
                "actions": s.actions(),
                "state": s.get_state(),
            }
            for s in self.services.values()
        ]

    async def invoke(
        self,
        service_id: str,
        action: str,
        params: dict | None = None,
        conn: dict | None = None,
    ) -> dict:
        key = str(service_id).lower().strip()
        svc = self.services.get(key)
        if svc is not None and action in svc.delegated_actions:
            svc = self.services.get(svc.delegated_actions[action], svc)
        if svc is None:
            return {"ok": False, "error": f"unknown service {service_id!r}"}
        return await svc.invoke(action, params or {}, conn)

    async def dispatch_inbound_frame(
        self,
        sid: int | None,
        conn: dict[str, Any],
        frame: dict[str, Any],
        data: Any,
        cmd: int | None,
        tag: str,
    ) -> bool:
        if sid is None:
            return False
        for svc in self.by_sid.get(int(sid), ()):
            if await svc.handle_frame(conn, frame, data, cmd, tag):
                return True
        routes = getattr(self.h, "POST_HELLO_ROUTES", {})
        handler_name = routes.get(int(sid))
        if not handler_name:
            return False
        handler = getattr(self.h, handler_name, None)
        if not callable(handler):
            return False
        return bool(await handler(conn, frame, data, cmd, tag))
