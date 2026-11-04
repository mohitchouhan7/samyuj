from __future__ import annotations

from typing import Callable

from samyuj.core.tasks import MobileDesktopServiceThread, get_mobile_desktop_service_thread
from .frame_hub import (
    MediaStreamRegistry,
    StreamRecord,
    apply_uv_swap_state,
    encode_jpeg,
    get_prepared_frame_bgrx,
    is_uv_swapped,
    store_prepared_frame_bgrx,
    swap_uv,
    target_render_size,
)
from .present import (
    is_decoder_gate_active,
    request_h264_keyframe,
    set_decoder_gate_active,
    set_h264_keyframe_hook,
    set_presenter_bridge,
)
from .remb import (
    adaptive_remb_ceiling_bps,
    adaptive_remb_floor_bps,
    clamp_remb_result,
    get_remb_floor_bps,
    note_link_loss_for_remb,
    remb_floor_degraded,
    set_remb_ceiling,
    set_remb_floor,
    set_remb_loss_floor,
)

class MediaRuntime:

    def __init__(self, uv_swap: bool = False, min_remb_kbps: int = 6000) -> None:
        self.stream_registry = MediaStreamRegistry()
        self.uv_swap: bool = bool(uv_swap)
        set_remb_floor(min_remb_kbps)

    @property
    def service_thread(self) -> MobileDesktopServiceThread:
        return get_mobile_desktop_service_thread()

    def set_uv_swap(self, enabled: bool) -> bool:
        self.uv_swap = bool(enabled)
        for rec in list(self.stream_registry.streams.values()):
            owner = getattr(rec, "owner_service", getattr(rec, "feature_type", ""))
            raw_frame = getattr(rec, "last_frame", getattr(rec, "last_av_frame", None))
            if owner == "webcam" and raw_frame is not None:
                flipped, swapped = apply_uv_swap_state(raw_frame, self.uv_swap)
                rec.last_frame = flipped
                rec.uv_swap_applied = bool(swapped)
                rec.latest_jpeg = encode_jpeg(flipped)
                rec.jpeg_seq += 1
        return self.uv_swap

    def set_remb_floor_kbps(self, kbps: int) -> int:
        return set_remb_floor(kbps)

    def note_link_loss(self, loss_pct: float) -> str | None:
        return note_link_loss_for_remb(loss_pct)

    def install_keyframe_hook(self, hook: Callable[[], None] | None) -> None:
        set_h264_keyframe_hook(hook)

__all__ = [
    "MediaRuntime",
    "MediaStreamRegistry",
    "StreamRecord",
    "adaptive_remb_ceiling_bps",
    "adaptive_remb_floor_bps",
    "apply_uv_swap_state",
    "clamp_remb_result",
    "encode_jpeg",
    "get_prepared_frame_bgrx",
    "get_remb_floor_bps",
    "is_decoder_gate_active",
    "is_uv_swapped",
    "note_link_loss_for_remb",
    "remb_floor_degraded",
    "request_h264_keyframe",
    "set_decoder_gate_active",
    "set_h264_keyframe_hook",
    "set_presenter_bridge",
    "set_remb_ceiling",
    "set_remb_floor",
    "set_remb_loss_floor",
    "store_prepared_frame_bgrx",
    "swap_uv",
    "target_render_size",
]
