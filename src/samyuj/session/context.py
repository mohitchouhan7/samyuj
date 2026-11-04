from __future__ import annotations

import types
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Coroutine, Protocol, runtime_checkable

if TYPE_CHECKING:
    from samyuj.core.events import EventBus
    from samyuj.core.logging import EventLog
    from samyuj.core.settings import UserSettings

@runtime_checkable
class SessionContext(Protocol):

    @property
    def device_id_b64(self) -> str: ...

    @property
    def device_name(self) -> str: ...

    @property
    def port(self) -> int: ...

    @property
    def wifi_ip(self) -> str: ...

    @property
    def state_dir(self) -> Path: ...

    @property
    def events(self) -> "EventLog": ...

    @property
    def event_bus(self) -> "EventBus": ...

    @property
    def settings(self) -> "UserSettings": ...

    def next_msg_id(self) -> int: ...

    def active_connection(self, preferred: dict[str, Any] | None = None) -> dict[str, Any] | None: ...

    async def send_ws_text(self, conn: dict[str, Any], payload_str: str, tag: str = "WS_TEXT") -> bool: ...

    async def send_raw(self, conn: dict[str, Any], data: bytes) -> None: ...

class RuntimeSessionContext:

    def __init__(self, runtime: Any) -> None:
        self._runtime = runtime

    @property
    def runtime(self) -> Any:
        return self._runtime

    @property
    def device_id_b64(self) -> str:
        return str(getattr(self._runtime, "device_id_b64", ""))

    @property
    def device_name(self) -> str:
        return str(getattr(self._runtime, "device_name", "Linux PC"))

    @property
    def port(self) -> int:
        return int(getattr(self._runtime, "port", 0) or 0)

    @property
    def wifi_ip(self) -> str:
        return str(getattr(self._runtime, "wifi_ip", ""))

    @property
    def state_dir(self) -> Path:
        return Path(getattr(self._runtime, "state_dir", Path.home()))

    @property
    def events(self) -> Any:
        return getattr(self._runtime, "events", None)

    @property
    def event_bus(self) -> Any:
        return getattr(self._runtime, "event_bus", None)

    @property
    def settings(self) -> Any:
        return getattr(self._runtime, "user_settings", None)

    def next_msg_id(self) -> int:
        return int(self._runtime.next_msg_id())

    def active_connection(self, preferred: dict[str, Any] | None = None) -> dict[str, Any] | None:
        return self._runtime._live_ws(preferred)

    async def send_ws_text(self, conn: dict[str, Any], payload_str: str, tag: str = "WS_TEXT") -> bool:
        return bool(await self._runtime._send_ws_text(conn, payload_str, tag))

    async def send_raw(self, conn: dict[str, Any], data: bytes) -> None:
        await self._runtime._send_raw(conn, data)

    async def run_on_desktop_thread(self, coro: Coroutine[Any, Any, Any], stat_key: str = "service_msgs_dispatched") -> Any:
        return await self._runtime._run_on_desktop_thread(coro, stat_key=stat_key)

class ComposedControllerRegistry:

    def __init__(self, runtime: Any, controller_classes: tuple[type, ...]) -> None:
        self._runtime = runtime
        self.controllers: dict[str, Any] = {}
        self._methods: dict[str, Callable[..., Any]] = {}
        self._class_attrs: dict[str, Any] = {}
        for cls in controller_classes:
            inst = cls()
            inst.ctx = getattr(runtime, "ctx", None)
            self.controllers[cls.__name__] = inst
            for mro_cls in reversed(cls.__mro__):
                if mro_cls is object:
                    continue
                for attr_name, attr_val in vars(mro_cls).items():
                    if attr_name.startswith("__") and attr_name.endswith("__"):
                        continue
                    if isinstance(attr_val, staticmethod):
                        self._class_attrs[attr_name] = ("staticmethod", attr_val.__func__)
                    elif isinstance(attr_val, classmethod):
                        self._class_attrs[attr_name] = ("classmethod", attr_val.__func__, cls)
                    elif isinstance(attr_val, property):
                        self._class_attrs[attr_name] = ("property", attr_val)
                    elif inspect_is_Func(attr_val):
                        self._methods[attr_name] = attr_val
                    else:
                        self._class_attrs[attr_name] = ("value", attr_val)

    def resolve(self, name: str) -> Any:
        if name in self._methods:
            return types.MethodType(self._methods[name], self._runtime)
        if name in self._class_attrs:
            kind = self._class_attrs[name][0]
            if kind == "staticmethod":
                return self._class_attrs[name][1]
            if kind == "classmethod":
                return types.MethodType(self._class_attrs[name][1], self._class_attrs[name][2])
            if kind == "property":
                return self._class_attrs[name][1].fget(self._runtime)
            return self._class_attrs[name][1]
        raise AttributeError(name)

def inspect_is_Func(obj: Any) -> bool:
    return isinstance(obj, types.FunctionType)
