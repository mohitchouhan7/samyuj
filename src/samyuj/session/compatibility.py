from __future__ import annotations

from .models import TransitionResult

class CompatibilityPolicy:

    CONFLICTS: dict[str, tuple[str, ...]] = {
        "webcam": ("screenshare",),
        "screenshare": ("desktop", "appstream", "webcam"),
        "desktop": ("screenshare", "appstream"),
        "appstream": ("screenshare", "desktop"),
    }

    @classmethod
    def evaluate_start(
        cls,
        active_services: list[str] | set[str],
        target_service: str,
        exclusive_mode: str = "auto",
    ) -> TransitionResult:
        if exclusive_mode != "auto":
            return TransitionResult(allowed=True, to_stop=[], reason="exclusive=off")
        if target_service in ("desktop", "appstream"):
            to_stop = [s for s in ("screenshare", "appstream", "desktop") if s != target_service]
        elif target_service == "webcam":
            to_stop = ["screenshare"]
        elif target_service == "screenshare":
            to_stop = ["desktop", "appstream", "webcam"]
        else:
            conflicts = cls.CONFLICTS.get(target_service, ())
            to_stop = [s for s in conflicts if s in active_services or s != "webcam"]
        return TransitionResult(
            allowed=True,
            to_stop=to_stop,
            reason=f"policy for {target_service}: stop {to_stop}",
        )
