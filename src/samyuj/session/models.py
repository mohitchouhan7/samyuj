from __future__ import annotations
from samyuj.core.logging import ConnLog

from collections.abc import MutableMapping
import contextlib
from dataclasses import dataclass, field
from typing import Any, Iterator

@dataclass
class ServiceSessionState:

    service_id: int
    name: str
    status: str = "idle"
    generation: int = 0
    session_id: int | str | None = None
    display_id: int | None = None
    instance_id: int | None = None
    pc_keys: set[Any] = field(default_factory=set)
    tasks: list[Any] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def bump(self, status: str = "starting") -> int:
        self.generation += 1
        self.status = status
        return self.generation

    def reset(self) -> int:
        self.generation += 1
        self.status = "idle"
        self.session_id = None
        self.display_id = None
        for t in list(self.tasks):
            with contextlib.suppress(Exception):
                t.cancel()
        self.tasks.clear()
        self.pc_keys.clear()
        self.metadata.clear()
        return self.generation

@dataclass
class TransitionResult:

    allowed: bool
    to_stop: list[str] = field(default_factory=list)
    reason: str = ""

def new_session_state() -> dict[str, Any]:
    return {"gen": 0, "feature": None}

class PeerSession(MutableMapping[str, Any]):

    def __init__(self, initial: dict[str, Any] | None = None, **kwargs: Any) -> None:
        self._data: dict[str, Any] = {
            "id": 0,
            "role": "server",
            "remote": None,
            "transport": "wifi",
            "ws": False,
            "replied": False,
            "hello": None,
            "session": new_session_state(),
            "tasks": [],
            "pcs": [],
            "closed": False,
            "bytes_in": 0,
            "bytes_out": 0,
            "frames_in": 0,
            "frames_out": 0,
            "log": ConnLog(disk_enabled=False),
        }
        if initial:
            self._data.update(initial)
        if kwargs:
            self._data.update(kwargs)

    @property
    def id(self) -> int:
        return int(self._data.get("id", 0) or 0)

    @property
    def role(self) -> str:
        return str(self._data.get("role", "server"))

    @property
    def remote(self) -> Any:
        return self._data.get("remote")

    @property
    def transport(self) -> str:
        return str(self._data.get("transport", "wifi"))

    @transport.setter
    def transport(self, val: str) -> None:
        self._data["transport"] = val

    @property
    def ws(self) -> bool:
        return bool(self._data.get("ws", False))

    @ws.setter
    def ws(self, val: bool) -> None:
        self._data["ws"] = bool(val)

    @property
    def replied(self) -> bool:
        return bool(self._data.get("replied", False))

    @replied.setter
    def replied(self, val: bool) -> None:
        self._data["replied"] = bool(val)

    @property
    def hello(self) -> dict[str, Any] | None:
        return self._data.get("hello")

    @hello.setter
    def hello(self, val: dict[str, Any] | None) -> None:
        self._data["hello"] = val

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self._data[key] = value

    def __delitem__(self, key: str) -> None:
        del self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def to_dict(self) -> dict[str, Any]:
        return dict(self._data)
