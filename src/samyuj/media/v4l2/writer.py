from __future__ import annotations
import errno
import stat
from .setup import (
    V4L2_BUF_TYPE_VIDEO_CAPTURE,
    V4L2_CAP_TIMEPERFRAME,
    V4L2_COLORSPACE_SRGB,
    V4L2_LOOPBACK_KEEP_FORMAT_CIDS,
    V4L2_LOOPBACK_SUSTAIN_FPS_CIDS,
    V4L2_VIDEO_MAJOR,
    VIDIOC_S_CTRL,
    _LIBSC_FAST,
    _find_sbin_tool,
)

import contextlib
import ctypes
import fcntl
import os
import struct
import subprocess
import threading
import time
from pathlib import Path

from .format import (
    convert_frame_for_v4l2,
    diagnose_v4l2_setup,
)
from .setup import (
    NAME_TO_FOURCC,
    FOURCC_NAMES,
    V4L2_BUF_TYPE_VIDEO_OUTPUT,
    V4L2_FIELD_NONE,
    V4L2_PIX_FMT_RGB24,
    V4L2_PIX_FMT_YUV420,
    V4L2_PIX_FMT_YUYV,
    VIDIOC_G_FMT,
    VIDIOC_S_FMT,
    VIDIOC_S_PARM,
    VIDIOC_STREAMOFF,
    VIDIOC_STREAMON,
    V4L2SetupDiagnosis,
)

class V4L2LoopbackWriter:

    def __init__(
        self,
        target_spec: str = "auto",
        pix_fmt_name: str = "YU12",
        fps_cap: float = 30.0,
        keepalive_fps: float = 5.0,
        placeholder_geom: tuple[int, int] = (1280, 720),
    ) -> None:
        self.target_spec = target_spec or "auto"
        self.pix_fmt_name = (pix_fmt_name or "YU12").upper()
        self.pix_fmt = NAME_TO_FOURCC.get(self.pix_fmt_name, V4L2_PIX_FMT_YUV420)
        self.fps_cap = max(1.0, min(60.0, float(fps_cap)))
        self.keepalive_fps = max(0.0, min(15.0, float(keepalive_fps)))
        self.placeholder_geom = (int(placeholder_geom[0]), int(placeholder_geom[1]))

        self.state = "stopped"
        self.resolved_device: str | None = None
        self.width = 0
        self.height = 0
        self.frames_written = 0
        self.keepalive_frames_written = 0
        self.frames_dropped = 0
        self.bytes_written = 0
        self.last_error = ""
        self.last_write_ts = 0.0
        self.diagnosis: V4L2SetupDiagnosis | None = None
        self._last_payload: bytes | None = None
        self._last_geom: tuple[int, int] | None = None

        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._mailbox_frame = None
        self._mailbox_seq = 0
        self._consumed_seq = 0
        self._stop_flag = False
        self._thread: threading.Thread | None = None
        self._fd: int | None = None
        self._sink_path: Path | None = None
        self._configured_geom: tuple[int, int, int] | None = None
        self._device_geom_locked: bool = False
        self._notified_consumers_ready: bool = False

    def start(self, target_spec: str | None = None, pix_fmt_name: str | None = None) -> dict:
        if target_spec is not None:
            self.target_spec = target_spec
        if pix_fmt_name is not None:
            self.pix_fmt_name = pix_fmt_name.upper()
            self.pix_fmt = NAME_TO_FOURCC.get(self.pix_fmt_name, V4L2_PIX_FMT_YUV420)

        self.stop()
        self.diagnosis = diagnose_v4l2_setup(self.target_spec)
        if self.diagnosis.resolved_device is None:
            self.state = "preview_only"
            self.resolved_device = None
            self.last_error = self.diagnosis.user_message
            return self.status_dict()

        self.resolved_device = self.diagnosis.resolved_device
        if self.resolved_device.startswith("sink:"):
            sp = Path(self.resolved_device[len("sink:"):])
            if sp.is_symlink():
                self.state = "preview_only"
                self.last_error = f"Refusing symlink sink path: {sp}"
                self.resolved_device = None
                return self.status_dict()
            sp.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            self._sink_path = sp
            self._fd = None
        else:
            try:
                open_flags = os.O_RDWR | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
                fd = os.open(self.resolved_device, open_flags)
                st = os.fstat(fd)
                if not stat.S_ISCHR(st.st_mode) or os.major(st.st_rdev) != V4L2_VIDEO_MAJOR:
                    os.close(fd)
                    self.state = "preview_only"
                    self.last_error = f"Refusing non-V4L2 character device: {self.resolved_device}"
                    self.resolved_device = None
                    return self.status_dict()
                self._fd = fd
                self._sink_path = None
            except OSError as exc:
                self.state = "preview_only"
                self.last_error = f"Cannot open {self.resolved_device}: {exc.strerror or exc}"
                self.resolved_device = None
                return self.status_dict()

        with self._lock:
            self._stop_flag = False
            self._configured_geom = None
            self._device_geom_locked = False
            self._last_payload = None
            self._last_geom = None
            self._notified_consumers_ready = False
            self.state = "running"
            self.last_error = ""
            self._thread = threading.Thread(
                target=self._worker_loop,
                name="sc-v4l2-writer",
                daemon=True,
            )
            self._thread.start()

        if self._fd is not None:
            deadline_prime = time.monotonic() + 0.1
            while time.monotonic() < deadline_prime and self.frames_written == 0 and self.state == "running":
                time.sleep(0.005)
        return self.status_dict()

    def submit_frame(self, frame) -> bool:
        if self.state != "running":
            return False
        with self._cond:
            if self._mailbox_frame is not None and self._mailbox_seq != self._consumed_seq:
                self.frames_dropped += 1
            self._mailbox_frame = frame
            self._mailbox_seq += 1
            self._cond.notify()
        return True

    def clear_to_standby(self) -> dict:
        with self._lock:
            self._last_payload = None
            self._last_geom = None
            self._mailbox_frame = None
        return self.status_dict()

    def _notify_v4l2_consumers_ready(self) -> None:
        dev = self.resolved_device
        if not dev or dev.startswith("sink:"):
            return

        def _nudge() -> None:
            sysname = Path(dev).name
            with contextlib.suppress(OSError):
                fd2 = os.open(dev, os.O_RDWR | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
                os.close(fd2)
            with contextlib.suppress(OSError):
                os.utime(dev, None)
            for uev_p in (
                Path("/sys/class/video4linux") / sysname / "uevent",
                Path("/sys/devices/virtual/video4linux") / sysname / "uevent",
            ):
                if uev_p.is_file():
                    for act in (b"change\n", b"add\n"):
                        with contextlib.suppress(OSError):
                            uev_p.write_bytes(act)
                    break
            udevadm_bin = _find_sbin_tool("udevadm")
            if udevadm_bin:
                with contextlib.suppress(Exception):
                    r = subprocess.run(
                        [udevadm_bin, "trigger", "--action=change", f"--sysname-match={sysname}"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        timeout=1.5, check=False,
                    )
                    subprocess.run(
                        [udevadm_bin, "trigger", "--action=add", f"--sysname-match={sysname}"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        timeout=1.5, check=False,
                    )
                    if r.returncode != 0 and os.getuid() != 0:
                        sudo_bin = _find_sbin_tool("sudo", trusted_only=True)
                        if sudo_bin:
                            subprocess.run(
                                [sudo_bin, "-n", udevadm_bin, "trigger", "--action=change", f"--sysname-match={sysname}"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                timeout=1.5, check=False,
                            )

        threading.Thread(target=_nudge, name="sc-v4l2-nudge", daemon=True).start()

    def stop(self) -> dict:
        th = None
        with self._cond:
            self._stop_flag = True
            self._mailbox_frame = None
            self._cond.notify_all()
            th = self._thread
            self._thread = None
        if th is not None and th.is_alive():
            th.join(timeout=1.5)
        if self._fd is not None:
            self._ioctl_streamoff_output(self._fd)
            with contextlib.suppress(OSError):
                os.close(self._fd)
            self._fd = None
        self.state = "stopped"
        self._configured_geom = None
        self._device_geom_locked = False
        return self.status_dict()

    @staticmethod
    def _ioctl_streamon_output(fd: int) -> bool:
        with contextlib.suppress(OSError):
            fcntl.ioctl(fd, VIDIOC_STREAMON, struct.pack("<I", V4L2_BUF_TYPE_VIDEO_OUTPUT))
            return True
        return False

    @staticmethod
    def _ioctl_streamoff_output(fd: int) -> bool:
        with contextlib.suppress(OSError):
            fcntl.ioctl(fd, VIDIOC_STREAMOFF, struct.pack("<I", V4L2_BUF_TYPE_VIDEO_OUTPUT))
            return True
        return False

    @staticmethod
    def _ioctl_set_keep_format(fd: int, keep: int = 1) -> bool:
        any_ok = False
        kval = 1 if keep else 0
        for cid in V4L2_LOOPBACK_KEEP_FORMAT_CIDS:
            with contextlib.suppress(OSError):
                fcntl.ioctl(fd, VIDIOC_S_CTRL, struct.pack("<Ii", cid, kval))
                any_ok = True
        if kval:
            for cid in V4L2_LOOPBACK_SUSTAIN_FPS_CIDS:
                with contextlib.suppress(OSError):
                    fcntl.ioctl(fd, VIDIOC_S_CTRL, struct.pack("<Ii", cid, 1))
        return any_ok

    @staticmethod
    def _ioctl_s_fmt(fd: int, w: int, h: int, fourcc: int) -> tuple[bool, int, int, int]:
        if _LIBSC_FAST is not None:
            stride = ctypes.c_int(0)
            imgsz = ctypes.c_int(0)
            rc = _LIBSC_FAST.sc_v4l2_configure_output(
                fd, w, h, ctypes.c_uint32(fourcc),
                ctypes.byref(stride), ctypes.byref(imgsz),
            )
            if rc == 0:
                actual = V4L2LoopbackWriter._ioctl_g_fmt(fd)
                if actual is not None:
                    return True, actual[0], actual[1], actual[2]
                return True, w, h, fourcc
        if fourcc == V4L2_PIX_FMT_YUV420:
            bpl, simg = w, (w * h * 3) // 2
        elif fourcc == V4L2_PIX_FMT_YUYV:
            bpl, simg = w * 2, w * h * 2
        else:
            bpl, simg = w * 3, w * h * 3
        buf = bytearray(208)
        struct.pack_into(
            "<IIIIIIII", buf, 0,
            V4L2_BUF_TYPE_VIDEO_OUTPUT, 0,
            w, h, fourcc, V4L2_FIELD_NONE, bpl, simg,
        )
        struct.pack_into("<I", buf, 32, V4L2_COLORSPACE_SRGB)
        try:
            fcntl.ioctl(fd, VIDIOC_S_FMT, buf, True)
            _t, _pad, rw, rh, rfmt = struct.unpack_from("<IIIII", buf, 0)
            return True, (rw or w), (rh or h), (rfmt or fourcc)
        except OSError:
            return False, w, h, fourcc

    @staticmethod
    def _ioctl_g_fmt(fd: int) -> tuple[int, int, int] | None:
        for btype in (V4L2_BUF_TYPE_VIDEO_OUTPUT, V4L2_BUF_TYPE_VIDEO_CAPTURE):
            buf = bytearray(208)
            struct.pack_into("<I", buf, 0, btype)
            try:
                fcntl.ioctl(fd, VIDIOC_G_FMT, buf, True)
                _t, _pad, rw, rh, rfmt = struct.unpack_from("<IIIII", buf, 0)
                if rw > 0 and rh > 0 and rfmt in FOURCC_NAMES:
                    return rw, rh, rfmt
            except OSError:
                continue
        return None

    @staticmethod
    def _ioctl_s_parm(fd: int, fps: int) -> bool:
        fps_i = max(1, min(120, int(fps or 30)))
        buf = bytearray(204)
        struct.pack_into(
            "<IIIII", buf, 0,
            V4L2_BUF_TYPE_VIDEO_OUTPUT,
            V4L2_CAP_TIMEPERFRAME,
            0,
            1,
            fps_i,
        )
        try:
            fcntl.ioctl(fd, VIDIOC_S_PARM, buf, True)
            return True
        except OSError:
            return False

    def _configure_device_if_needed(self, w: int, h: int) -> bool:
        geom = (w, h, self.pix_fmt)
        if self._configured_geom == geom:
            return True
        prev_geom = self._configured_geom
        self._notified_consumers_ready = False
        if self._fd is not None:
            cand_fmts = [self.pix_fmt]
            for f_alt in (V4L2_PIX_FMT_YUV420, V4L2_PIX_FMT_YUYV, V4L2_PIX_FMT_RGB24):
                if f_alt not in cand_fmts:
                    cand_fmts.append(f_alt)
            configured = False
            for fmt_try in cand_fmts:
                ok, rw, rh, rfmt = self._ioctl_s_fmt(self._fd, w, h, fmt_try)
                if ok:
                    self.pix_fmt = rfmt if rfmt in FOURCC_NAMES else fmt_try
                    self.pix_fmt_name = FOURCC_NAMES.get(self.pix_fmt, self.pix_fmt_name)
                    self.width = rw
                    self.height = rh
                    self._configured_geom = (rw, rh, self.pix_fmt)
                    self._device_geom_locked = True
                    if prev_geom == self._configured_geom and prev_geom is not None:
                        self._notified_consumers_ready = True
                    configured = True
                    break
            if not configured:
                locked_pre = self._ioctl_g_fmt(self._fd)
                if (locked_pre is not None
                        and (locked_pre[0] != w or locked_pre[1] != h or locked_pre[0] < locked_pre[1])
                        and self.resolved_device
                        and not self.resolved_device.startswith("sink:")):
                    with contextlib.suppress(OSError):
                        self._ioctl_streamoff_output(self._fd)
                        self._ioctl_set_keep_format(self._fd, 0)
                        lw0, lh0, lfmt0 = locked_pre
                        self.pix_fmt = lfmt0
                        with contextlib.suppress(OSError):
                            os.write(self._fd, self._placeholder_payload(lw0, lh0))
                        os.close(self._fd)
                        open_flags = os.O_RDWR | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
                        self._fd = os.open(self.resolved_device, open_flags)
                        for fmt_try in cand_fmts:
                            ok, rw, rh, rfmt = self._ioctl_s_fmt(self._fd, w, h, fmt_try)
                            if ok:
                                self.pix_fmt = rfmt if rfmt in FOURCC_NAMES else fmt_try
                                self.pix_fmt_name = FOURCC_NAMES.get(self.pix_fmt, self.pix_fmt_name)
                                self.width = rw
                                self.height = rh
                                self._configured_geom = (rw, rh, self.pix_fmt)
                                self._device_geom_locked = True
                                configured = True
                                break
            if not configured:
                locked = self._ioctl_g_fmt(self._fd)
                if locked is not None:
                    lw, lh, lfmt = locked
                    self.pix_fmt = lfmt
                    self.pix_fmt_name = FOURCC_NAMES.get(lfmt, self.pix_fmt_name)
                    self.width = lw
                    self.height = lh
                    self._configured_geom = (lw, lh, lfmt)
                    self._device_geom_locked = True
                    if prev_geom == self._configured_geom and prev_geom is not None:
                        self._notified_consumers_ready = True
                    self._ioctl_s_parm(self._fd, round(self.fps_cap))
                    self._ioctl_set_keep_format(self._fd, 1)
                    return True
                if self._configured_geom is not None:
                    return True
                self.last_error = f"VIDIOC_S_FMT failed for {w}x{h} {FOURCC_NAMES.get(self.pix_fmt, 'YU12')}"
                self.state = "error"
                return False
            self._ioctl_s_parm(self._fd, round(self.fps_cap))
            self._ioctl_set_keep_format(self._fd, 1)
            self._ioctl_streamon_output(self._fd)
            return True
        self._configured_geom = geom
        self.width = w
        self.height = h
        return True

    def _placeholder_payload(self, w: int, h: int) -> bytes:
        if self.pix_fmt == V4L2_PIX_FMT_YUV420:
            return bytes([32]) * (w * h) + bytes([128]) * (w * h // 2)
        if self.pix_fmt == V4L2_PIX_FMT_YUYV:
            return bytes([32, 128, 32, 128]) * (w * h // 2)
        return bytes([24, 28, 34]) * (w * h)

    def _write_payload(self, payload: bytes, w: int, h: int) -> None:
        if not payload or w <= 0 or h <= 0:
            return
        if not self._configure_device_if_needed(w, h):
            return
        if self._fd is not None:
            try:
                written = os.write(self._fd, payload)
                self.bytes_written += written
            except BlockingIOError:
                return
        elif self._sink_path is not None:
            hdr = struct.pack(
                "<4sIIIIQd",
                FOURCC_NAMES.get(self.pix_fmt, "YU12").encode("ascii"),
                w, h, len(payload), self.frames_written + 1,
                int(time.time() * 1e6), self.fps_cap,
            )
            if self._sink_path.is_symlink():
                raise OSError(errno.ELOOP, "Refusing to write to symlink sink path")
            tmp_p = self._sink_path.with_suffix(".tmp")
            if tmp_p.is_symlink():
                raise OSError(errno.ELOOP, "Refusing to write to symlink temp path")
            sfd = os.open(
                str(tmp_p),
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            try:
                with contextlib.suppress(OSError):
                    os.fchmod(sfd, 0o600)
                os.write(sfd, hdr + payload)
            finally:
                os.close(sfd)
            os.replace(tmp_p, self._sink_path)
            self.bytes_written += len(payload)
        self.frames_written += 1
        self.last_write_ts = time.time()
        if self._fd is not None and not self._notified_consumers_ready:
            self._notified_consumers_ready = True
            with contextlib.suppress(Exception):
                self._notify_v4l2_consumers_ready()

    def _worker_loop(self) -> None:
        min_interval = 1.0 / max(1.0, self.fps_cap)
        ka_interval = 1.0 / max(0.5, self.keepalive_fps)
        last_ts = 0.0
        last_ka_ts = 0.0
        while True:
            with self._cond:
                frame = None
                wait_window = (
                    0.0
                    if (self._fd is not None and self.frames_written == 0 and self.keepalive_frames_written == 0)
                    else 0.25
                )
                deadline = time.monotonic() + wait_window
                while not self._stop_flag and self._mailbox_seq == self._consumed_seq:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    self._cond.wait(timeout=remaining)
                if self._stop_flag:
                    break
                if self._mailbox_seq != self._consumed_seq:
                    frame = self._mailbox_frame
                    self._consumed_seq = self._mailbox_seq

            if frame is None:
                now_ka = time.monotonic()
                if now_ka - last_ka_ts < ka_interval:
                    continue
                last_ka_ts = now_ka
                try:
                    if self._last_payload is not None and self._last_geom:
                        self._write_payload(self._last_payload, *self._last_geom)
                    else:
                        pw, ph = self.placeholder_geom
                        cfg_w = self.width if (self._device_geom_locked and self.width > 0) else pw
                        cfg_h = self.height if (self._device_geom_locked and self.height > 0) else ph
                        if self._configure_device_if_needed(cfg_w, cfg_h):
                            tw = self.width if self.width > 0 else pw
                            th = self.height if self.height > 0 else ph
                            self._write_payload(self._placeholder_payload(tw, th), tw, th)
                    self.keepalive_frames_written += 1
                except OSError as exc:
                    if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                        continue
                    self.last_error = f"V4L2 keepalive write error: {exc.strerror or exc}"
                    self.state = "error"
                    break
                except Exception:
                    pass
                continue

            now = time.monotonic()
            if last_ts and (now - last_ts) < min_interval:
                time.sleep(max(0.0, min_interval - (now - last_ts)))

            try:
                fw = int(getattr(frame, "width", 0) or 0)
                fh = int(getattr(frame, "height", 0) or 0)
                if fw <= 0 or fh <= 0:
                    continue
                if self._fd is not None:
                    cfg_w = self.width if (self._device_geom_locked and self.width > 0) else self.placeholder_geom[0]
                    cfg_h = self.height if (self._device_geom_locked and self.height > 0) else self.placeholder_geom[1]
                else:
                    cfg_w = self.width if (self._device_geom_locked and self.width > 0) else fw
                    cfg_h = self.height if (self._device_geom_locked and self.height > 0) else fh
                if not self._configure_device_if_needed(cfg_w, cfg_h):
                    continue
                tw = self.width if self.width > 0 else cfg_w
                th = self.height if self.height > 0 else cfg_h
                payload, w, h = convert_frame_for_v4l2(frame, self.pix_fmt, target_w=tw, target_h=th)
                if not payload or w <= 0 or h <= 0:
                    continue
                with contextlib.suppress(Exception):
                    from samyuj.media.enhance import enhance_payload
                    payload = enhance_payload(
                        payload, FOURCC_NAMES.get(self.pix_fmt, "YU12"), w, h)
                self._write_payload(payload, w, h)
                self._last_payload = payload
                self._last_geom = (w, h)
                last_ts = time.monotonic()
            except OSError as exc:
                if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                    continue
                self.last_error = f"V4L2 write error: {exc.strerror or exc}"
                self.state = "error"
                break
            except Exception as exc:
                self.last_error = f"V4L2 conversion error: {exc!r}"
                self.state = "error"
                break

    def status_dict(self) -> dict:
        diag = self.diagnosis or diagnose_v4l2_setup(self.target_spec)
        return {
            "state": self.state,
            "target_spec": self.target_spec,
            "resolved_device": self.resolved_device,
            "pix_fmt": FOURCC_NAMES.get(self.pix_fmt, self.pix_fmt_name),
            "width": self.width,
            "height": self.height,
            "fps_cap": self.fps_cap,
            "keepalive_fps": self.keepalive_fps,
            "keepalive_frames": self.keepalive_frames_written,
            "frames_written": self.frames_written,
            "frames_dropped": self.frames_dropped,
            "bytes_written": self.bytes_written,
            "last_error": self.last_error,
            "diagnosis": diag.to_dict(),
        }

class V4L2VirtualSinkConsumer:

    def __init__(self, name: str, device_or_sink: str) -> None:
        self.name = name
        self.device_or_sink = device_or_sink
        self.frames_read = 0
        self.last_header: dict = {}

    def read_latest_frame(self) -> tuple[dict, bytes] | None:
        spec = self.device_or_sink
        if spec.startswith("sink:"):
            spec = spec[len("sink:"):]
        p = Path(spec)
        if not p.is_file():
            return None
        raw = p.read_bytes()
        hdr_size = struct.calcsize("<4sIIIIQd")
        if len(raw) < hdr_size:
            return None
        fourcc_b, w, h, plen, seq, ts_us, fps = struct.unpack("<4sIIIIQd", raw[:hdr_size])
        payload = raw[hdr_size:hdr_size + plen]
        if len(payload) != plen:
            return None
        hdr = {
            "consumer": self.name,
            "fourcc": fourcc_b.decode("ascii", "replace"),
            "width": w,
            "height": h,
            "payload_len": plen,
            "seq": seq,
            "ts_us": ts_us,
            "fps": fps,
        }
        self.frames_read += 1
        self.last_header = hdr
        return hdr, payload
