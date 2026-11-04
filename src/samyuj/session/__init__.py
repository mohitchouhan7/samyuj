from __future__ import annotations

from typing import Any

from .compatibility import CompatibilityPolicy
from .context import ComposedControllerRegistry, RuntimeSessionContext, SessionContext
from .models import PeerSession, ServiceSessionState, TransitionResult, new_session_state
from .registry import ServiceSessionRegistry

__all__ = [
    "CompatibilityPolicy",
    "ComposedControllerRegistry",
    "PeerSession",
    "RuntimeSessionContext",
    "ServiceSessionRegistry",
    "ServiceSessionState",
    "SessionContext",
    "SamyujRuntime",
    "TransitionResult",
    "new_session_state",
]

def __getattr__(name: str) -> Any:
    if name == "SamyujRuntime":
        from .lifecycle import SamyujRuntime
        return SamyujRuntime
    raise AttributeError(name)
