from __future__ import annotations

from .format import (
    convert_frame_for_v4l2,
    diagnose_v4l2_setup,
    discover_v4l2_devices,
    query_v4l2_node,
    resolve_logical_v4l2_target,
)
from .setup import (
    DEFAULT_CARD_LABEL,
    DEFAULT_V4L2_VIDEO_NR,
    FOURCC_MAP,
    V4L2DeviceCapability,
    V4L2SetupDiagnosis,
    build_persistent_v4l2_setup_script,
    check_v4l2loopback_module,
    detect_linux_distro_v4l2_hints,
    install_v4l2loopback_package,
    is_v4l2_udev_hotplug_ready,
    is_v4l2loopback_exclusive_caps_ok,
    load_v4l2loopback_module,
    sanitize_v4l2_card_label,
)
from .writer import V4L2LoopbackWriter, V4L2VirtualSinkConsumer

__all__ = [
    "DEFAULT_CARD_LABEL",
    "DEFAULT_V4L2_VIDEO_NR",
    "FOURCC_MAP",
    "V4L2DeviceCapability",
    "V4L2LoopbackWriter",
    "V4L2SetupDiagnosis",
    "V4L2VirtualSinkConsumer",
    "build_persistent_v4l2_setup_script",
    "check_v4l2loopback_module",
    "convert_frame_for_v4l2",
    "detect_linux_distro_v4l2_hints",
    "diagnose_v4l2_setup",
    "discover_v4l2_devices",
    "install_v4l2loopback_package",
    "is_v4l2_udev_hotplug_ready",
    "is_v4l2loopback_exclusive_caps_ok",
    "load_v4l2loopback_module",
    "query_v4l2_node",
    "resolve_logical_v4l2_target",
    "sanitize_v4l2_card_label",
]
