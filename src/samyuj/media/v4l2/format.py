from __future__ import annotations
import errno
from .setup import V4L2_CAP_READWRITE, V4L2_CAP_VIDEO_CAPTURE_MPLANE, V4L2_CAP_VIDEO_OUTPUT_MPLANE, _LIBSC_FAST

import contextlib
import ctypes
import fcntl
import os
import struct
from pathlib import Path

from .setup import (
    V4L2_CAP_DEVICE_CAPS,
    V4L2_CAP_VIDEO_CAPTURE,
    V4L2_CAP_VIDEO_OUTPUT,
    V4L2_PIX_FMT_YUV420,
    V4L2_PIX_FMT_YUYV,
    VIDIOC_QUERYCAP,
    V4L2DeviceCapability,
    V4L2SetupDiagnosis,
    check_v4l2loopback_module,
    detect_linux_distro_v4l2_hints,
    is_v4l2loopback_exclusive_caps_ok,
)

def _is_sysfs_virtual_v4l2(node_name: str, sysfs_label: str = "") -> bool:
    low = (sysfs_label or "").lower()
    if any(k in low for k in ("loopback", "dummy video", "smart connect")):
        return True
    sys_dir = Path("/sys/class/video4linux") / node_name
    with contextlib.suppress(OSError):
        real_sys = str(sys_dir.resolve())
        if "/devices/virtual/video4linux/" in real_sys:
            return True
    return False

def query_v4l2_node(dev_path: str | Path) -> V4L2DeviceCapability:
    p = Path(dev_path)
    name = p.name
    sys_name_file = Path("/sys/class/video4linux") / name / "name"
    sysfs_label = ""
    if sys_name_file.is_file():
        with contextlib.suppress(OSError):
            sysfs_label = sys_name_file.read_text(encoding="utf-8").strip()

    sysfs_loopback = _is_sysfs_virtual_v4l2(name, sysfs_label)
    cap_rec = V4L2DeviceCapability(
        device_path=str(p),
        sysfs_name=sysfs_label,
        card_label=sysfs_label,
        is_loopback=sysfs_loopback,
    )
    if not p.exists():
        cap_rec.error = "device node does not exist"
        return cap_rec

    try:
        fd = os.open(str(p), os.O_RDWR | os.O_NONBLOCK)
    except PermissionError:
        cap_rec.permissions_ok = False
        cap_rec.error = "permission denied (add user to 'video' group)"
        return cap_rec
    except OSError as exc:
        if exc.errno == errno.EBUSY:
            cap_rec.busy = True
            cap_rec.permissions_ok = True
            cap_rec.error = "device busy"
        else:
            cap_rec.error = f"open error: {exc.strerror or exc}"
        return cap_rec

    cap_rec.permissions_ok = True
    try:
        queried_ok = False
        if _LIBSC_FAST is not None:
            drv = ctypes.create_string_buffer(32)
            crd = ctypes.create_string_buffer(32)
            bus = ctypes.create_string_buffer(32)
            caps_u32 = ctypes.c_uint32(0)
            rc = _LIBSC_FAST.sc_v4l2_query_device(fd, drv, crd, bus, ctypes.byref(caps_u32))
            if rc == 0:
                cap_rec.driver = drv.value.decode("utf-8", "replace")
                cap_rec.card_label = crd.value.decode("utf-8", "replace") or sysfs_label
                cap_rec.bus_info = bus.value.decode("utf-8", "replace")
                eff = int(caps_u32.value)
                cap_rec.capabilities = eff
                queried_ok = True

        if not queried_ok:
            buf = bytearray(104)
            fcntl.ioctl(fd, VIDIOC_QUERYCAP, buf, True)
            drv_b, card_b, bus_b, _ver, caps, dev_caps = struct.unpack("<16s32s32sIII", bytes(buf[:92]))
            eff = dev_caps if (caps & V4L2_CAP_DEVICE_CAPS) else caps
            cap_rec.driver = drv_b.split(b"\x00", 1)[0].decode("utf-8", "replace")
            cap_rec.card_label = card_b.split(b"\x00", 1)[0].decode("utf-8", "replace") or sysfs_label
            cap_rec.bus_info = bus_b.split(b"\x00", 1)[0].decode("utf-8", "replace")
            cap_rec.capabilities = eff

        eff = cap_rec.capabilities
        cap_rec.is_loopback = (
            sysfs_loopback
            or "v4l2 loopback" in cap_rec.driver.lower()
            or "v4l2loopback" in cap_rec.driver.lower()
            or "v4l2loopback" in cap_rec.bus_info.lower()
            or "loopback" in cap_rec.card_label.lower()
            or "dummy video" in cap_rec.card_label.lower()
            or "smart connect" in cap_rec.card_label.lower()
        )
        cap_rec.can_output = bool(
            cap_rec.is_loopback
            or (eff & (V4L2_CAP_VIDEO_OUTPUT | V4L2_CAP_VIDEO_OUTPUT_MPLANE))
        )
        cap_rec.can_capture = bool(eff & (V4L2_CAP_VIDEO_CAPTURE | V4L2_CAP_VIDEO_CAPTURE_MPLANE))
        cap_rec.can_readwrite = bool(eff & V4L2_CAP_READWRITE)
    except OSError as exc:
        cap_rec.error = f"VIDIOC_QUERYCAP failed: {exc.strerror or exc}"
        if cap_rec.is_loopback:
            cap_rec.can_output = True
    finally:
        with contextlib.suppress(OSError):
            os.close(fd)
    return cap_rec

def discover_v4l2_devices(extra_paths: list[str] | None = None) -> list[V4L2DeviceCapability]:
    paths: list[Path] = sorted(
        Path("/dev").glob("video[0-9]*"),
        key=lambda p: int("".join(ch for ch in p.name if ch.isdigit()) or "999"),
    )
    for ep in extra_paths or []:
        cand = Path(ep)
        if cand not in paths:
            paths.append(cand)
    return [query_v4l2_node(p) for p in paths]

def resolve_logical_v4l2_target(
    target_spec: str = "auto",
    devices: list[V4L2DeviceCapability] | None = None,
) -> tuple[str | None, str]:
    spec = (target_spec or "auto").strip()
    if spec.startswith("sink:"):
        sink_path = spec[len("sink:"):].strip()
        return f"sink:{sink_path}", "resolved virtual loopback sink"

    devs = devices if devices is not None else discover_v4l2_devices()
    if spec.lower().startswith("label:"):
        wanted = spec[len("label:"):].strip().lower()
        for d in devs:
            if wanted in d.card_label.lower() or wanted in d.sysfs_name.lower():
                if d.can_output and d.permissions_ok and not d.busy:
                    return d.device_path, f"matched card label '{d.card_label}'"
                return None, f"matched '{d.card_label}' ({d.device_path}) but unavailable: {d.error or 'not output-capable'}"
        return None, f"no V4L2 device matching label '{wanted}'"

    if spec.lower() in ("auto", ""):
        both = V4L2_CAP_VIDEO_CAPTURE | V4L2_CAP_VIDEO_OUTPUT
        for d in devs:
            if (d.is_loopback and d.can_output and d.permissions_ok and not d.busy
                    and (d.capabilities & both) != both
                    and ("smart connect" in d.card_label.lower() or "smart connect" in d.sysfs_name.lower())):
                return d.device_path, f"auto-selected loopback device {d.device_path} ({d.card_label})"
        for d in devs:
            if d.is_loopback and d.can_output and d.permissions_ok and not d.busy and (d.capabilities & both) != both:
                return d.device_path, f"auto-selected loopback device {d.device_path} ({d.card_label})"
        for d in devs:
            if d.is_loopback and d.can_output and d.permissions_ok and not d.busy:
                return d.device_path, f"auto-selected loopback device {d.device_path} ({d.card_label})"
        for d in devs:
            if d.can_output and d.permissions_ok and not d.busy:
                return d.device_path, f"auto-selected output device {d.device_path} ({d.card_label})"
        return None, "no writable v4l2loopback output device found (running in preview-only mode)"

    p = Path(spec)
    if p.is_symlink() or p.exists():
        real_p = str(p.resolve()) if p.is_symlink() else str(p)
        q = query_v4l2_node(real_p)
        if q.permissions_ok and not q.busy and (q.can_output or q.is_loopback):
            return real_p, f"resolved explicit device {real_p} ({q.card_label})"
        return None, f"device {real_p} not ready for V4L2 output: {q.error or 'not a loopback output node'}"
    return None, f"configured V4L2 device '{spec}' does not exist"

def diagnose_v4l2_setup(target_spec: str = "auto") -> V4L2SetupDiagnosis:
    mod = check_v4l2loopback_module()
    devs = discover_v4l2_devices()
    resolved, reason = resolve_logical_v4l2_target(target_spec, devs)
    usable = [d.to_dict() for d in devs if d.can_output or d.is_loopback]

    hint_load = (
        "sudo modprobe v4l2loopback devices=1 video_nr=10 "
        'card_label="Samyuj Webcam" exclusive_caps=1'
    )
    hint_install = detect_linux_distro_v4l2_hints()["v4l2_install"]

    hint_reload = f"sudo modprobe -r v4l2loopback && {hint_load}"
    if resolved is not None:
        is_sink = str(resolved).startswith("sink:")
        excl_ok = is_sink or is_v4l2loopback_exclusive_caps_ok(devs)
        if not excl_ok:
            return V4L2SetupDiagnosis(
                status="ready",
                module_installed=mod["installed"],
                module_loaded=mod["loaded"],
                secure_boot_enabled=mod["secure_boot"],
                usable_devices=usable,
                selected_target=target_spec,
                resolved_device=resolved,
                user_message=(
                    f"Virtual camera on {resolved} has exclusive_caps=0 (Firefox/Chrome require "
                    "exclusive_caps=1; click 'Virtual Cam' to reload with exclusive_caps=1)."
                ),
                setup_command_hint=hint_reload,
            )
        return V4L2SetupDiagnosis(
            status="ready",
            module_installed=mod["installed"],
            module_loaded=mod["loaded"],
            secure_boot_enabled=mod["secure_boot"],
            usable_devices=usable,
            selected_target=target_spec,
            resolved_device=resolved,
            user_message=f"Virtual camera ready on {resolved} ({reason}).",
            setup_command_hint="",
        )

    if any(d.is_loopback and not d.permissions_ok for d in devs):
        return V4L2SetupDiagnosis(
            status="permission_denied",
            module_installed=mod["installed"],
            module_loaded=mod["loaded"],
            secure_boot_enabled=mod["secure_boot"],
            usable_devices=usable,
            selected_target=target_spec,
            resolved_device=None,
            user_message="Preview-only mode: your user account does not have permission to write to the V4L2 loopback camera.",
            setup_command_hint="sudo usermod -aG video $USER  # then log out and back in",
        )

    if any(d.is_loopback and d.busy for d in devs):
        return V4L2SetupDiagnosis(
            status="device_busy",
            module_installed=mod["installed"],
            module_loaded=mod["loaded"],
            secure_boot_enabled=mod["secure_boot"],
            usable_devices=usable,
            selected_target=target_spec,
            resolved_device=None,
            user_message="Preview-only mode: the V4L2 virtual camera device is currently in use by another producer.",
            setup_command_hint="Close the other camera producer or select a different V4L2 target device.",
        )

    if not mod["installed"]:
        return V4L2SetupDiagnosis(
            status="module_not_installed",
            module_installed=False,
            module_loaded=False,
            secure_boot_enabled=mod["secure_boot"],
            usable_devices=usable,
            selected_target=target_spec,
            resolved_device=None,
            user_message="Preview-only mode: v4l2loopback is not installed. Phone camera preview works inside Smart Connect.",
            setup_command_hint=f"{hint_install} && {hint_load}",
        )

    sb_note = (
        " (Secure Boot is enabled; ensure the DKMS module is signed with mokutil if modprobe reports Operation not permitted)"
        if mod["secure_boot"] else ""
    )
    return V4L2SetupDiagnosis(
        status="module_not_loaded",
        module_installed=mod["installed"],
        module_loaded=mod["loaded"],
        secure_boot_enabled=mod["secure_boot"],
        usable_devices=usable,
        selected_target=target_spec,
        resolved_device=None,
        user_message=f"Preview-only mode: v4l2loopback kernel module is not loaded{sb_note}.",
        setup_command_hint=hint_load,
    )

def _pack_raw_frame_planes(vf, w: int, h: int, pix_fmt: int) -> bytes:
    if pix_fmt in (V4L2_PIX_FMT_YUV420, V4L2_PIX_FMT_YUYV):
        if hasattr(vf, "planes") and len(vf.planes) >= 3:
            p0, p1, p2 = vf.planes[0], vf.planes[1], vf.planes[2]
            if _LIBSC_FAST is not None and hasattr(p0, "buffer_ptr"):
                _py_bytes_alloc = ctypes.pythonapi.PyBytes_FromStringAndSize
                _py_bytes_alloc.argtypes = [ctypes.c_char_p, ctypes.c_ssize_t]
                _py_bytes_alloc.restype = ctypes.py_object
                if pix_fmt == V4L2_PIX_FMT_YUV420:
                    out_bytes = _py_bytes_alloc(None, (w * h * 3) // 2)
                    _LIBSC_FAST.sc_i420_pack_contiguous(
                        p0.buffer_ptr, p0.line_size,
                        p1.buffer_ptr, p1.line_size,
                        p2.buffer_ptr, p2.line_size,
                        out_bytes, w, h,
                    )
                    return out_bytes
                else:
                    out_bytes = _py_bytes_alloc(None, w * h * 2)
                    _LIBSC_FAST.sc_i420_to_yuyv(
                        p0.buffer_ptr, p0.line_size,
                        p1.buffer_ptr, p1.line_size,
                        p2.buffer_ptr, p2.line_size,
                        out_bytes, w, h,
                    )
                    return out_bytes
            cw, ch = w // 2, h // 2
            b0, b1, b2 = bytes(p0), bytes(p1), bytes(p2)
            s0, s1, s2 = p0.line_size, p1.line_size, p2.line_size
            y_rows = b0[:w * h] if s0 == w else b"".join(b0[r * s0:r * s0 + w] for r in range(h))
            u_rows = b1[:cw * ch] if s1 == cw else b"".join(b1[r * s1:r * s1 + cw] for r in range(ch))
            v_rows = b2[:cw * ch] if s2 == cw else b"".join(b2[r * s2:r * s2 + cw] for r in range(ch))
            if pix_fmt == V4L2_PIX_FMT_YUV420:
                return y_rows + u_rows + v_rows
            out = bytearray(w * h * 2)
            for r in range(h):
                y_off = r * w
                uv_off = (r // 2) * cw
                d_off = r * w * 2
                for c in range(cw):
                    out[d_off + c * 4 + 0] = y_rows[y_off + c * 2 + 0]
                    out[d_off + c * 4 + 1] = u_rows[uv_off + c]
                    out[d_off + c * 4 + 2] = y_rows[y_off + c * 2 + 1]
                    out[d_off + c * 4 + 3] = v_rows[uv_off + c]
            return bytes(out)
        if hasattr(vf, "y") and hasattr(vf, "u") and hasattr(vf, "v"):
            y_b, u_b, v_b = bytes(vf.y), bytes(vf.u), bytes(vf.v)
            if pix_fmt == V4L2_PIX_FMT_YUV420:
                return y_b + u_b + v_b
            cw = w // 2
            out = bytearray(w * h * 2)
            for r in range(h):
                y_off = r * w
                uv_off = (r // 2) * cw
                d_off = r * w * 2
                for c in range(cw):
                    out[d_off + c * 4 + 0] = y_b[y_off + c * 2 + 0]
                    out[d_off + c * 4 + 1] = u_b[uv_off + c]
                    out[d_off + c * 4 + 2] = y_b[y_off + c * 2 + 1]
                    out[d_off + c * 4 + 3] = v_b[uv_off + c]
            return bytes(out)
    if hasattr(vf, "reformat"):
        rgb = vf.reformat(format="rgb24")
        p0 = rgb.planes[0]
        raw = bytes(p0)
        stride = p0.line_size
        payload = raw[:w * h * 3] if stride == w * 3 else b"".join(
            raw[r * stride:r * stride + w * 3] for r in range(h)
        )
        return payload
    if hasattr(vf, "rgb_bytes"):
        return bytes(vf.rgb_bytes)
    return b""

def convert_frame_for_v4l2(
    frame,
    pix_fmt: int = V4L2_PIX_FMT_YUV420,
    target_w: int = 0,
    target_h: int = 0,
) -> tuple[bytes, int, int]:
    w = int(getattr(frame, "width", 0) or 0)
    h = int(getattr(frame, "height", 0) or 0)
    if w <= 0 or h <= 0:
        return b"", 0, 0

    if target_w > 0 and target_h > 0 and (w, h) != (target_w, target_h) and hasattr(frame, "reformat"):
        if (w < h) != (target_w < target_h) and pix_fmt == V4L2_PIX_FMT_YUV420:
            with contextlib.suppress(Exception):
                scale = min(target_w / float(w), target_h / float(h))
                iw = max(2, min(target_w, (int(round(w * scale)) // 2) * 2))
                ih = max(2, min(target_h, (int(round(h * scale)) // 2) * 2))
                vf_scaled = frame.reformat(width=iw, height=ih, format="yuv420p")
                inner_b = _pack_raw_frame_planes(vf_scaled, iw, ih, V4L2_PIX_FMT_YUV420)
                if len(inner_b) == (iw * ih * 3) // 2:
                    tw, th = target_w, target_h
                    tcw, tch = tw // 2, th // 2
                    icw, ich = iw // 2, ih // 2
                    x_off = ((tw - iw) // 4) * 2
                    y_off = ((th - ih) // 4) * 2
                    cx_off = x_off // 2
                    cy_off = y_off // 2
                    out = bytearray((tw * th * 3) // 2)
                    y_size = tw * th
                    uv_size = tcw * tch
                    out[:y_size] = b"\x10" * y_size
                    out[y_size:] = b"\x80" * (uv_size * 2)
                    iy_end = iw * ih
                    iu_end = iy_end + icw * ich
                    if _LIBSC_FAST is not None and hasattr(_LIBSC_FAST, "sc_copy_strided_rows"):
                        c_in = (ctypes.c_uint8 * len(inner_b)).from_buffer_copy(inner_b)
                        c_out = (ctypes.c_uint8 * len(out)).from_buffer(out)
                        in_base = ctypes.addressof(c_in)
                        out_base = ctypes.addressof(c_out)
                        _LIBSC_FAST.sc_copy_strided_rows(
                            in_base, iw,
                            out_base + y_off * tw + x_off, tw,
                            iw, ih,
                        )
                        _LIBSC_FAST.sc_copy_strided_rows(
                            in_base + iy_end, icw,
                            out_base + y_size + cy_off * tcw + cx_off, tcw,
                            icw, ich,
                        )
                        _LIBSC_FAST.sc_copy_strided_rows(
                            in_base + iu_end, icw,
                            out_base + y_size + uv_size + cy_off * tcw + cx_off, tcw,
                            icw, ich,
                        )
                    else:
                        for r in range(ih):
                            dst_i = (y_off + r) * tw + x_off
                            src_i = r * iw
                            out[dst_i:dst_i + iw] = inner_b[src_i:src_i + iw]
                        for r in range(ich):
                            dst_u = y_size + (cy_off + r) * tcw + cx_off
                            src_u = iy_end + r * icw
                            out[dst_u:dst_u + icw] = inner_b[src_u:src_u + icw]
                            dst_v = y_size + uv_size + (cy_off + r) * tcw + cx_off
                            src_v = iu_end + r * icw
                            out[dst_v:dst_v + icw] = inner_b[src_v:src_v + icw]
                    return bytes(out), tw, th
        with contextlib.suppress(Exception):
            frame = frame.reformat(width=target_w, height=target_h, format="yuv420p")
            w = int(getattr(frame, "width", target_w) or target_w)
            h = int(getattr(frame, "height", target_h) or target_h)

    fmt_name = getattr(getattr(frame, "format", None), "name", "")
    vf = frame
    if pix_fmt in (V4L2_PIX_FMT_YUV420, V4L2_PIX_FMT_YUYV):
        if fmt_name not in ("yuv420p", "yuvj420p") and hasattr(frame, "reformat"):
            vf = frame.reformat(format="yuv420p")
    payload = _pack_raw_frame_planes(vf, w, h, pix_fmt)
    if payload:
        return payload, w, h
    return b"", 0, 0

