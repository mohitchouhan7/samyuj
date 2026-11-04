from __future__ import annotations
from dataclasses import dataclass

def discover_v4l2_devices() -> list:
    from .format import discover_v4l2_devices as _impl
    return _impl()

import contextlib
import ctypes
import dataclasses
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

def _fourcc(a: str, b: str, c: str, d: str) -> int:
    return ord(a) | (ord(b) << 8) | (ord(c) << 16) | (ord(d) << 24)

V4L2_PIX_FMT_YUV420 = _fourcc("Y", "U", "1", "2")
V4L2_PIX_FMT_YUYV = _fourcc("Y", "U", "Y", "V")
V4L2_PIX_FMT_RGB24 = _fourcc("R", "G", "B", "3")

FOURCC_NAMES = {
    V4L2_PIX_FMT_YUV420: "YU12",
    V4L2_PIX_FMT_YUYV: "YUYV",
    V4L2_PIX_FMT_RGB24: "RGB3",
}
NAME_TO_FOURCC = {
    "YU12": V4L2_PIX_FMT_YUV420,
    "I420": V4L2_PIX_FMT_YUV420,
    "YUV420": V4L2_PIX_FMT_YUV420,
    "YUYV": V4L2_PIX_FMT_YUYV,
    "YUY2": V4L2_PIX_FMT_YUYV,
    "RGB24": V4L2_PIX_FMT_RGB24,
    "RGB3": V4L2_PIX_FMT_RGB24,
}

V4L2_CAP_VIDEO_CAPTURE = 0x00000001
V4L2_CAP_VIDEO_OUTPUT = 0x00000002
V4L2_CAP_VIDEO_OVERLAY = 0x00000004
V4L2_CAP_VIDEO_CAPTURE_MPLANE = 0x00001000
V4L2_CAP_VIDEO_OUTPUT_MPLANE = 0x00002000
V4L2_CAP_VIDEO_M2M = 0x00008000
V4L2_CAP_READWRITE = 0x01000000
V4L2_CAP_STREAMING = 0x04000000
V4L2_CAP_DEVICE_CAPS = 0x80000000

VIDIOC_QUERYCAP = 0x80685600
VIDIOC_G_FMT = 0xC0D05604
VIDIOC_S_FMT = 0xC0D05605
VIDIOC_S_PARM = 0xC0CC5616
VIDIOC_S_CTRL = 0xC008561C
VIDIOC_STREAMON = 0x40045612
VIDIOC_STREAMOFF = 0x40045613
V4L2_LOOPBACK_KEEP_FORMAT_CIDS = (0x08000000, 0x0098F000, 0x0098F900)
V4L2_LOOPBACK_SUSTAIN_FPS_CIDS = (0x08000001, 0x0098F001, 0x0098F901)
V4L2_BUF_TYPE_VIDEO_CAPTURE = 1
V4L2_BUF_TYPE_VIDEO_OUTPUT = 2
V4L2_FIELD_NONE = 1
V4L2_COLORSPACE_SRGB = 8
V4L2_CAP_TIMEPERFRAME = 0x1000
V4L2_VIDEO_MAJOR = 81

_TRUSTED_SBIN_DIRS = (
    "/run/current-system/sw/bin",
    "/usr/local/sbin",
    "/usr/sbin",
    "/sbin",
    "/usr/bin",
    "/bin",
)

def _find_sbin_tool(name: str, trusted_only: bool = False) -> str | None:
    if not name or "/" in name or "\\" in name:
        return None
    for prefix in _TRUSTED_SBIN_DIRS:
        cand = Path(prefix) / name
        try:
            if cand.is_file() and os.access(str(cand), os.X_OK):
                st = cand.stat()
                if not trusted_only or (st.st_uid == 0 and (st.st_mode & 0o002) == 0):
                    return str(cand)
        except OSError:
            continue
    if trusted_only:
        return None
    return shutil.which(name)

def detect_linux_distro_v4l2_hints(os_release_text: str | None = None) -> dict[str, str]:
    txt = os_release_text
    if txt is None:
        for p in (Path("/etc/os-release"), Path("/usr/lib/os-release")):
            if p.is_file():
                with contextlib.suppress(OSError):
                    txt = p.read_text(encoding="utf-8", errors="ignore")
                    break
    txt = (txt or "").lower()
    tokens = set(re.findall(r"[a-z0-9_.-]+", txt))
    if "nixos" in tokens or Path("/etc/NIXOS").exists():
        return {
            "distro_family": "nixos",
            "v4l2_install": (
                "Add boot.extraModulePackages = [ config.boot.kernelPackages.v4l2loopback ]; "
                "to /etc/nixos/configuration.nix && sudo nixos-rebuild switch "
                "(or Debian/Ubuntu: sudo apt install v4l2loopback-dkms v4l2loopback-utils)"
            ),
            "gi_cairo_install": "nix-shell -p python3Packages.pygobject3 python3Packages.pycairo gtk3",
        }
    if tokens & {"fedora", "rhel", "centos", "almalinux", "rocky", "nobara", "bazzite"}:
        if Path("/run/ostree-booted").exists():
            v4l2_cmd = "sudo rpm-ostree install akmod-v4l2loopback v4l2loopback"
        else:
            v4l2_cmd = "sudo dnf install v4l2loopback akmod-v4l2loopback"
        return {
            "distro_family": "fedora",
            "v4l2_install": f"{v4l2_cmd} (Debian/Ubuntu: sudo apt install v4l2loopback-dkms v4l2loopback-utils)",
            "gi_cairo_install": "sudo dnf install python3-gobject cairo-gobject gtk3",
        }
    if tokens & {"arch", "manjaro", "endeavouros", "garuda", "cachyos", "steamos"}:
        return {
            "distro_family": "arch",
            "v4l2_install": (
                "sudo pacman -S v4l2loopback-dkms v4l2loopback-utils linux-headers "
                "(Debian/Ubuntu: sudo apt install v4l2loopback-dkms v4l2loopback-utils)"
            ),
            "gi_cairo_install": "sudo pacman -S python-gobject python-cairo gtk3",
        }
    if tokens & {"opensuse", "suse", "opensuse-tumbleweed", "opensuse-leap"}:
        return {
            "distro_family": "suse",
            "v4l2_install": (
                "sudo zypper install v4l2loopback-kmp-default v4l2loopback-utils "
                "(Debian/Ubuntu: sudo apt install v4l2loopback-dkms v4l2loopback-utils)"
            ),
            "gi_cairo_install": "sudo zypper install python3-gobject python3-gobject-cairo typelib-1_0-Gtk-3_0",
        }
    if "alpine" in tokens:
        return {
            "distro_family": "alpine",
            "v4l2_install": (
                "sudo apk add v4l2loopback-lts v4l2loopback-dc "
                "(Debian/Ubuntu: sudo apt install v4l2loopback-dkms v4l2loopback-utils)"
            ),
            "gi_cairo_install": "sudo apk add py3-gobject3 py3-cairo gtk+3.0",
        }
    if "void" in tokens:
        return {
            "distro_family": "void",
            "v4l2_install": (
                "sudo xbps-install -S v4l2loopback "
                "(Debian/Ubuntu: sudo apt install v4l2loopback-dkms v4l2loopback-utils)"
            ),
            "gi_cairo_install": "sudo xbps-install -S python3-gobject python3-cairo gtk+3",
        }
    if "gentoo" in tokens:
        return {
            "distro_family": "gentoo",
            "v4l2_install": (
                "sudo emerge --ask media-video/v4l2loopback "
                "(Debian/Ubuntu: sudo apt install v4l2loopback-dkms v4l2loopback-utils)"
            ),
            "gi_cairo_install": "sudo emerge --ask dev-python/pygobject dev-python/pycairo x11-libs/gtk+:3",
        }
    return {
        "distro_family": "debian",
        "v4l2_install": (
            "sudo apt install v4l2loopback-dkms v4l2loopback-utils "
            "(Fedora: sudo dnf install v4l2loopback; Arch: sudo pacman -S v4l2loopback-dkms)"
        ),
        "gi_cairo_install": "sudo apt install python3-gi-cairo python3-cairo",
    }

_LIBSC_FAST = None
with contextlib.suppress(Exception):
    for _so in (
        Path(__file__).resolve().parents[4] / "libsc_fast.so",
        Path(__file__).resolve().parents[3] / "libsc_fast.so",
        Path("/usr/lib/samyuj/libsc_fast.so"),
    ):
        if _so.is_file():
            _lib = ctypes.CDLL(str(_so))
            break
    else:
        _lib = None
    if _lib is not None:
        _lib.sc_i420_pack_contiguous.argtypes = [
            ctypes.c_void_p, ctypes.c_int,
            ctypes.c_void_p, ctypes.c_int,
            ctypes.c_void_p, ctypes.c_int,
            ctypes.c_char_p, ctypes.c_int, ctypes.c_int,
        ]
        _lib.sc_i420_pack_contiguous.restype = ctypes.c_size_t
        _lib.sc_i420_to_yuyv.argtypes = [
            ctypes.c_void_p, ctypes.c_int,
            ctypes.c_void_p, ctypes.c_int,
            ctypes.c_void_p, ctypes.c_int,
            ctypes.c_char_p, ctypes.c_int, ctypes.c_int,
        ]
        _lib.sc_i420_to_yuyv.restype = ctypes.c_size_t
        _lib.sc_v4l2_query_device.argtypes = [
            ctypes.c_int, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        _lib.sc_v4l2_query_device.restype = ctypes.c_int
        _lib.sc_v4l2_configure_output.argtypes = [
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
        ]
        _lib.sc_v4l2_configure_output.restype = ctypes.c_int
        if hasattr(_lib, "sc_copy_strided_rows"):
            _lib.sc_copy_strided_rows.argtypes = [
                ctypes.c_void_p, ctypes.c_int,
                ctypes.c_void_p, ctypes.c_int,
                ctypes.c_int, ctypes.c_int,
            ]
            _lib.sc_copy_strided_rows.restype = None
        _LIBSC_FAST = _lib

@dataclass
class V4L2DeviceCapability:
    device_path: str
    sysfs_name: str = ""
    driver: str = ""
    card_label: str = ""
    bus_info: str = ""
    capabilities: int = 0
    is_loopback: bool = False
    can_output: bool = False
    can_capture: bool = False
    can_readwrite: bool = False
    permissions_ok: bool = False
    busy: bool = False
    error: str = ""

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

@dataclass
class V4L2SetupDiagnosis:
    status: str
    module_installed: bool
    module_loaded: bool
    secure_boot_enabled: bool
    usable_devices: list[dict]
    selected_target: str
    resolved_device: str | None
    user_message: str
    setup_command_hint: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

def check_v4l2loopback_module() -> dict:
    loaded = Path("/sys/module/v4l2loopback").is_dir()
    if not loaded:
        with contextlib.suppress(OSError):
            loaded = "v4l2loopback" in Path("/proc/modules").read_text(encoding="utf-8")
    installed = loaded
    modinfo_bin = _find_sbin_tool("modinfo")
    if not installed and modinfo_bin:
        with contextlib.suppress(Exception):
            r = subprocess.run(
                [modinfo_bin, "v4l2loopback"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2.0,
                check=False,
            )
            installed = (r.returncode == 0)
    if not installed:
        with contextlib.suppress(Exception):
            rel = os.uname().release
            for mroot in (
                Path("/lib/modules") / rel,
                Path("/usr/lib/modules") / rel,
                Path("/run/booted-system/kernel-modules/lib/modules") / rel,
                Path("/run/current-system/kernel-modules/lib/modules") / rel,
            ):
                if mroot.is_dir():
                    dep_f = mroot / "modules.dep"
                    if dep_f.is_file() and "v4l2loopback" in dep_f.read_text(encoding="utf-8", errors="ignore"):
                        installed = True
                        break
                    if any(mroot.rglob("v4l2loopback.ko*")):
                        installed = True
                        break
    sb_enabled = False
    with contextlib.suppress(OSError):
        sb_files = list(Path("/sys/firmware/efi/efivars").glob("SecureBoot-*"))
        if sb_files:
            raw = sb_files[0].read_bytes()
            sb_enabled = bool(raw and raw[-1] == 1)
    return {
        "installed": installed,
        "loaded": loaded,
        "secure_boot": sb_enabled,
    }

def _wait_for_udev_loopback_node(timeout_s: float = 0.8) -> None:
    udevadm_bin = _find_sbin_tool("udevadm")
    if udevadm_bin:
        with contextlib.suppress(Exception):
            subprocess.run([udevadm_bin, "settle", "--timeout=1"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=1.2, check=False)
    deadline = time.monotonic() + max(0.05, float(timeout_s))
    while time.monotonic() < deadline:
        devs = discover_v4l2_devices()
        if any(d.is_loopback and d.permissions_ok for d in devs):
            return
        time.sleep(0.05)

def sanitize_v4l2_card_label(card_label: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9 _.-]", "", str(card_label or "")).strip()
    return (cleaned[:31] or "Samyuj Webcam")

def is_v4l2loopback_exclusive_caps_ok(devices: list[V4L2DeviceCapability] | None = None) -> bool:
    if devices is None or len(devices) == 0:
        param_f = Path("/sys/module/v4l2loopback/parameters/exclusive_caps")
        if param_f.is_file():
            with contextlib.suppress(OSError):
                raw = param_f.read_text(encoding="utf-8", errors="ignore").strip().upper()
                tokens = [t.strip() for t in raw.split(",") if t.strip()]
                if any(t in ("N", "0") for t in tokens):
                    return False
                if devices is not None and len(devices) == 0:
                    return True
    from .format import discover_v4l2_devices
    devs = devices if devices is not None else discover_v4l2_devices()
    loopbacks = [d for d in devs if d.is_loopback and d.permissions_ok]
    both = V4L2_CAP_VIDEO_CAPTURE | V4L2_CAP_VIDEO_OUTPUT
    sc_loopbacks = [
        d for d in loopbacks
        if "smart connect" in d.card_label.lower() or "smart connect" in d.sysfs_name.lower()
    ]
    target_devs = sc_loopbacks if sc_loopbacks else loopbacks
    for d in target_devs:
        if (d.capabilities & both) == both:
            return False
    return True

def is_v4l2_udev_hotplug_ready() -> bool:
    rule_p = Path("/etc/udev/rules.d/70-samyuj-v4l2.rules")
    prime_p = Path("/usr/local/bin/samyuj-v4l2-prime")
    if not rule_p.is_file() or not prime_p.is_file():
        return False
    with contextlib.suppress(OSError):
        txt = rule_p.read_text(encoding="utf-8", errors="ignore")
        if "ID_V4L_CAPABILITIES" not in txt and "samyuj-v4l2-prime" not in txt:
            return False
    return True

def build_persistent_v4l2_setup_script(
    card_label: str = "Samyuj Webcam",
    video_nr: int = 10,
    devices: int = 1,
    uid: int | None = None,
    username: str | None = None,
) -> str:
    safe_label = sanitize_v4l2_card_label(card_label)
    safe_video_nr = max(0, min(63, int(video_nr)))
    safe_devices = max(1, min(8, int(devices)))
    eff_uid = max(0, int(os.getuid() if uid is None else uid))
    raw_user = str(username if username is not None else (os.environ.get("USER") or "")).strip()
    safe_user = re.sub(r"[^a-zA-Z0-9_.-]", "", raw_user)
    modprobe_bin = _find_sbin_tool("modprobe", trusted_only=True) or _find_sbin_tool("modprobe") or "/sbin/modprobe"
    udevadm_bin = _find_sbin_tool("udevadm", trusted_only=True) or _find_sbin_tool("udevadm") or "udevadm"
    setfacl_bin = _find_sbin_tool("setfacl", trusted_only=True) or _find_sbin_tool("setfacl") or "setfacl"
    usermod_bin = _find_sbin_tool("usermod", trusted_only=True) or _find_sbin_tool("usermod") or "usermod"
    ctl_bin = _find_sbin_tool("v4l2loopback-ctl", trusted_only=True) or _find_sbin_tool("v4l2loopback-ctl") or "v4l2loopback-ctl"

    prime_py = (
        "#!/usr/bin/env python3\\n"
        "import fcntl, os, struct, sys\\n"
        "for dev in sys.argv[1:]:\\n"
        "    try:\\n"
        "        fd = os.open(dev, os.O_RDWR | os.O_NONBLOCK)\\n"
        "    except OSError:\\n"
        "        continue\\n"
        "    try:\\n"
        "        w, h, fourcc = 1280, 720, 0x32315559\\n"
        "        bpl, simg = w, (w * h * 3) // 2\\n"
        "        buf = bytearray(208)\\n"
        "        struct.pack_into('<IIIIIIII', buf, 0, 2, 0, w, h, fourcc, 1, bpl, simg)\\n"
        "        struct.pack_into('<I', buf, 32, 8)\\n"
        "        try:\\n"
        "            fcntl.ioctl(fd, 0xC0D05605, buf, True)\\n"
        "        except OSError:\\n"
        "            for cid in (0x08000000, 0x0098F000, 0x0098F900):\\n"
        "                try:\\n"
        "                    fcntl.ioctl(fd, 0xC008561C, struct.pack('<Ii', cid, 0))\\n"
        "                except OSError:\\n"
        "                    pass\\n"
        "            os.close(fd)\\n"
        "            fd = os.open(dev, os.O_RDWR | os.O_NONBLOCK)\\n"
        "            try:\\n"
        "                fcntl.ioctl(fd, 0xC0D05605, buf, True)\\n"
        "            except OSError:\\n"
        "                pass\\n"
        "        parm = bytearray(204)\\n"
        "        struct.pack_into('<IIIII', parm, 0, 2, 0x1000, 0, 1, 30)\\n"
        "        try:\\n"
        "            fcntl.ioctl(fd, 0xC0CC5616, parm, True)\\n"
        "        except OSError:\\n"
        "            pass\\n"
        "        for cid in (0x08000000, 0x0098F000, 0x0098F900, 0x08000001, 0x0098F001, 0x0098F901):\\n"
        "            try:\\n"
        "                fcntl.ioctl(fd, 0xC008561C, struct.pack('<Ii', cid, 1))\\n"
        "            except OSError:\\n"
        "                pass\\n"
        "        frame = (b'\\\\x20' * (w * h)) + (b'\\\\x80' * ((w * h) // 2))\\n"
        "        try:\\n"
        "            os.write(fd, frame)\\n"
        "        except OSError:\\n"
        "            pass\\n"
        "    finally:\\n"
        "        os.close(fd)\\n"
    )

    lines = [
        "set -e",
        "mkdir -p /etc/modules-load.d /etc/modprobe.d /etc/udev/rules.d /usr/local/bin",
        "printf \x27v4l2loopback\\n\x27 > /etc/modules-load.d/samyuj-v4l2loopback.conf",
        f'printf "options v4l2loopback devices={safe_devices} video_nr={safe_video_nr} card_label=\\"{safe_label}\\" exclusive_caps=1\\n" > /etc/modprobe.d/samyuj-v4l2loopback.conf',
        f'printf "{prime_py}" > /usr/local/bin/samyuj-v4l2-prime',
        "chmod 0755 /usr/local/bin/samyuj-v4l2-prime 2>/dev/null || true",
        (
            f'printf "SUBSYSTEM==\\"video4linux\\", KERNEL==\\"video[0-9]*\\", ATTR{{name}}==\\"{safe_label}\\", ENV{{ID_V4L_CAPABILITIES}}=\\":capture:\\", TAG+=\\"uaccess\\", GROUP=\\"video\\", MODE=\\"0660\\", RUN+=\\"/bin/chmod 0660 /sys%p/uevent\\", RUN+=\\"/usr/local/bin/samyuj-v4l2-prime %N\\"\\n'
            'SUBSYSTEM==\\"video4linux\\", KERNEL==\\"video[0-9]*\\", ENV{ID_V4L_PRODUCT}==\\"*Samyuj*\\", ENV{ID_V4L_CAPABILITIES}=\\":capture:\\", TAG+=\\"uaccess\\", GROUP=\\"video\\", MODE=\\"0660\\", RUN+=\\"/bin/chmod 0660 /sys%p/uevent\\", RUN+=\\"/usr/local/bin/samyuj-v4l2-prime %N\\"\\n'
            'SUBSYSTEM==\\"video4linux\\", KERNEL==\\"video[0-9]*\\", DEVPATH==\\"/devices/virtual/video4linux/*\\", ENV{ID_V4L_CAPABILITIES}=\\":capture:\\", TAG+=\\"uaccess\\", GROUP=\\"video\\", MODE=\\"0660\\", RUN+=\\"/bin/chmod 0660 /sys%p/uevent\\", RUN+=\\"/usr/local/bin/samyuj-v4l2-prime %N\\"\\n" '
            "> /etc/udev/rules.d/70-samyuj-v4l2.rules"
        ),
                "if [ -d /sys/module/v4l2loopback ]; then",
        "  EX_VAL=$(cat /sys/module/v4l2loopback/parameters/exclusive_caps 2>/dev/null || echo Y)",
        '  if [ -z "$(ls -A /sys/devices/virtual/video4linux 2>/dev/null)" ] || echo "$EX_VAL" | grep -qE "(^|,)(N|0)"; then',
        f'    {modprobe_bin} -r v4l2loopback 2>/dev/null || rmmod v4l2loopback 2>/dev/null || true',
        "  fi",
        "fi",
        "if [ ! -d /sys/module/v4l2loopback ]; then",
        f'  {modprobe_bin} v4l2loopback devices={safe_devices} video_nr={safe_video_nr} card_label="{safe_label}" exclusive_caps=1 max_buffers=4 || '
        f'{modprobe_bin} v4l2loopback devices={safe_devices} video_nr={safe_video_nr} card_label="{safe_label}" exclusive_caps=1 || '
        f'{modprobe_bin} v4l2loopback devices={safe_devices} card_label="{safe_label}" exclusive_caps=1 || true',
        "else",
        "  EX_VAL2=$(cat /sys/module/v4l2loopback/parameters/exclusive_caps 2>/dev/null || echo Y)",
        '  if [ -z "$(ls -A /sys/devices/virtual/video4linux 2>/dev/null)" ] || echo "$EX_VAL2" | grep -qE "(^|,)(N|0)"; then',
        f'    {ctl_bin} delete /dev/video{safe_video_nr} 2>/dev/null || true',
        f'    {ctl_bin} add -x 1 -n "{safe_label}" /dev/video{safe_video_nr} 2>/dev/null || '
        f'{ctl_bin} add -n "{safe_label}" /dev/video{safe_video_nr} 2>/dev/null || true',
        "  fi",
        "fi",
        "for sysdev in /sys/devices/virtual/video4linux/video*; do",
        '  [ -e "$sysdev" ] || continue',
        '  true',
        '  vname=$(basename "$sysdev")',
        '  if [ -e "/dev/$vname" ]; then',
        f'    {ctl_bin} set-caps "/dev/$vname" "YU12:1280x720@30/1" 2>/dev/null || {ctl_bin} set-caps "video/x-raw,format=I420,width=1280,height=720,framerate=30/1" "/dev/$vname" 2>/dev/null || true',
        '    /usr/local/bin/samyuj-v4l2-prime "/dev/$vname" 2>/dev/null || true',
        '    chmod 0660 "/dev/$vname" 2>/dev/null || true',
        "  fi",
        "done",
    ]
    if safe_user:
        lines.append(f'{usermod_bin} -aG video {safe_user} 2>/dev/null || true')
    if eff_uid > 0:
        lines.extend([
            "for sysdev in /sys/devices/virtual/video4linux/video*; do",
            '  [ -e "$sysdev" ] || continue',
            '  vname=$(basename "$sysdev")',
            '  if [ -e "/dev/$vname" ]; then',
            f'    chown {eff_uid}:video "/dev/$vname" 2>/dev/null || chown {eff_uid} "/dev/$vname" 2>/dev/null || true',
            '    chmod 0660 "/dev/$vname" 2>/dev/null || true',
            f'    {setfacl_bin} -m u:{eff_uid}:rw "/dev/$vname" 2>/dev/null || true',
            "  fi",
            "done",
        ])
    lines.extend([
        f'{udevadm_bin} control --reload-rules 2>/dev/null || true',
        f'{udevadm_bin} trigger --action=add --subsystem-match=video4linux 2>/dev/null || true',
        f'{udevadm_bin} trigger --action=change --subsystem-match=video4linux 2>/dev/null || true',
        f'{udevadm_bin} settle --timeout=1 2>/dev/null || true',
        "for sysdev in /sys/devices/virtual/video4linux/video*; do [ -e \"$sysdev/uevent\" ] && true; done",
        "if command -v snap >/dev/null 2>&1; then",
        "  snap connect firefox:camera 2>/dev/null || true",
        "  snap connect chromium:camera 2>/dev/null || true",
        "fi",
    ])
    return "\n".join(lines)

def _try_grant_user_acl_on_loopback() -> None:
    uid = os.getuid()
    if uid == 0:
        return
    sudo_bin = _find_sbin_tool("sudo", trusted_only=True)
    if not sudo_bin:
        return
    setfacl_bin = _find_sbin_tool("setfacl", trusted_only=True)
    chown_bin = _find_sbin_tool("chown", trusted_only=True)
    for d in discover_v4l2_devices():
        if d.is_loopback and not d.permissions_ok and d.device_path.startswith("/dev/video"):
            if setfacl_bin:
                with contextlib.suppress(Exception):
                    subprocess.run(
                        [sudo_bin, "-n", setfacl_bin, "-m", f"u:{uid}:rw", d.device_path],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        timeout=3.0, check=False,
                    )
            if chown_bin:
                with contextlib.suppress(Exception):
                    subprocess.run(
                        [sudo_bin, "-n", chown_bin, f"{uid}:video", d.device_path],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        timeout=3.0, check=False,
                    )

def install_v4l2loopback_package(interactive: bool = True) -> dict:
    mod = check_v4l2loopback_module()
    if mod["installed"]:
        return {"ok": True, "method": "already-installed", **mod}
    fam = str(detect_linux_distro_v4l2_hints().get("distro_family", "debian"))
    if fam == "nixos":
        return {"ok": False, "method": "none", **mod,
                "error": "nixos_config_required",
                "hint": ("NixOS: add `boot.extraModulePackages = [ config.boot.kernelPackages.v4l2loopback ];` "
                         "to /etc/nixos/configuration.nix, then `sudo nixos-rebuild switch`")}
    if fam == "fedora" and Path("/run/ostree-booted").exists():
        return {"ok": False, "method": "none", **mod,
                "error": "ostree_reboot_required",
                "hint": ("Atomic desktop (rpm-ostree): `sudo rpm-ostree install akmod-v4l2loopback v4l2loopback` "
                         "then reboot — the virtual camera arms itself on the next start")}
    pm_cmd = {
        "debian": ('apt-get update && apt-get install -y v4l2loopback-dkms v4l2loopback-utils '
                   '|| apt-get install -y v4l2loopback-dkms v4l2loopback-utils '
                   '|| apt-get install -y v4l2loopback-dkms '
                   '|| apt-get install -y v4l2loopback-utils'),
        "fedora": 'dnf install -y v4l2loopback akmod-v4l2loopback || dnf install -y v4l2loopback',
        "arch": ('pacman -Sy --noconfirm v4l2loopback-dkms v4l2loopback-utils linux-headers '
                 '|| pacman -Sy --noconfirm v4l2loopback-dkms '
                 '|| pacman -Sy --noconfirm v4l2loopback-utils'),
        "suse": ('zypper --non-interactive install -y v4l2loopback-kmp-default v4l2loopback-utils '
                 '|| zypper --non-interactive install -y v4l2loopback-kmp-default'),
        "alpine": 'apk add --no-cache v4l2loopback-lts v4l2loopback-dc || apk add --no-cache v4l2loopback-lts',
        "void": 'xbps-install -Sy v4l2loopback',
        "gentoo": 'emerge --ask=n media-video/v4l2loopback',
    }.get(fam)
    if pm_cmd is None:
        pm_cmd = 'apt-get install -y v4l2loopback-dkms v4l2loopback-utils'
    setup_script = build_persistent_v4l2_setup_script()
    full_cmd = f"({pm_cmd}) && ({setup_script})"
    sh_bin = _find_sbin_tool("sh", trusted_only=True) or "/bin/sh"
    sudo_bin = _find_sbin_tool("sudo", trusted_only=True)
    pkexec_bin = _find_sbin_tool("pkexec", trusted_only=True)
    have_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))

    def _run(argv, timeout):
        try:
            return subprocess.run(argv, capture_output=True, text=True,
                                  timeout=timeout, check=False)
        except Exception:
            return None

    ok = False
    method = "none"
    if os.getuid() == 0:
        r = _run([sh_bin, "-c", full_cmd], 900)
        method = "root"
        ok = bool(r is not None and r.returncode == 0)
    if not ok and sudo_bin:
        r = _run([sudo_bin, "-n", sh_bin, "-c", full_cmd], 900)
        method = "sudo-n"
        ok = bool(r is not None and r.returncode == 0)
    if not ok and interactive and have_display and pkexec_bin:
        r = _run([pkexec_bin, sh_bin, "-c", full_cmd], 900)
        method = "pkexec"
        ok = bool(r is not None and r.returncode == 0)
    mod_after = check_v4l2loopback_module()
    installed_now = bool(mod_after["installed"])
    return {
        "ok": bool(ok or installed_now),
        "method": method if (ok or installed_now) else "failed",
        "distro_family": fam,
        **mod_after,
        "error": "" if (ok or installed_now) else "package_install_failed",
        "hint": detect_linux_distro_v4l2_hints()["v4l2_install"],
    }

def load_v4l2loopback_module(interactive: bool = True,
                             card_label: str = "Samyuj Webcam",
                             video_nr: int = 10, devices: int = 1) -> dict:
    safe_label = sanitize_v4l2_card_label(card_label)
    safe_video_nr = max(0, min(63, int(video_nr)))
    safe_devices = max(1, min(8, int(devices)))
    hints = detect_linux_distro_v4l2_hints()
    mod = check_v4l2loopback_module()
    hotplug_ok = is_v4l2_udev_hotplug_ready()
    if mod["loaded"]:
        devs_now = discover_v4l2_devices()
        excl_ok = is_v4l2loopback_exclusive_caps_ok(devs_now)
        if any(d.is_loopback for d in devs_now) and excl_ok:
            if not any(d.is_loopback and d.permissions_ok for d in devs_now):
                _try_grant_user_acl_on_loopback()
                devs_now = discover_v4l2_devices()
            if any(d.is_loopback and d.permissions_ok for d in devs_now) or not interactive:
                return {"ok": True, "method": "already-loaded", **mod}
        elif not any(d.is_loopback for d in devs_now):
            ctl_bin = _find_sbin_tool("v4l2loopback-ctl", trusted_only=True) or _find_sbin_tool("v4l2loopback-ctl")
            if ctl_bin:
                with contextlib.suppress(Exception):
                    r_ctl = subprocess.run(
                        [ctl_bin, "add", "-x", "1", "-n", safe_label, f"/dev/video{safe_video_nr}"],
                        capture_output=True, text=True, timeout=5,
                    )
                    if r_ctl.returncode != 0:
                        subprocess.run(
                            [ctl_bin, "add", "-n", safe_label, f"/dev/video{safe_video_nr}"],
                            capture_output=True, text=True, timeout=5,
                        )
                    _wait_for_udev_loopback_node(0.5)
                    _try_grant_user_acl_on_loopback()
                    devs_after = discover_v4l2_devices()
                    if (any(d.is_loopback and d.permissions_ok for d in devs_after)
                            and is_v4l2loopback_exclusive_caps_ok(devs_after)
                            and (hotplug_ok or not interactive)):
                        return {"ok": True, "method": "v4l2loopback-ctl", **check_v4l2loopback_module()}
            if not interactive:
                return {"ok": True, "method": "already-loaded", **mod}
    if not mod["installed"]:
        if interactive:
            inst = install_v4l2loopback_package(interactive=True)
            mod = check_v4l2loopback_module()
            if not mod["installed"]:
                return {
                    "ok": False, "method": "none", **mod,
                    "error": "module_not_installed",
                    "install_result": inst,
                    "hint": f"v4l2loopback is not installed. Run: {hints['v4l2_install']}",
                }
            devs_post_inst = discover_v4l2_devices()
            if (mod["loaded"]
                    and any(d.is_loopback and d.permissions_ok for d in devs_post_inst)
                    and is_v4l2loopback_exclusive_caps_ok(devs_post_inst)
                    and is_v4l2_udev_hotplug_ready()):
                return {"ok": True, "method": inst.get("method", "pkexec"), "persisted": True, **mod}
        else:
            return {
                "ok": False, "method": "none", **mod,
                "error": "module_not_installed",
                "hint": f"v4l2loopback is not installed. Run: {hints['v4l2_install']}",
            }
    sh_bin = _find_sbin_tool("sh", trusted_only=True) or "/bin/sh"
    sudo_bin = _find_sbin_tool("sudo", trusted_only=True)
    pkexec_bin = _find_sbin_tool("pkexec", trusted_only=True)
    setup_script = build_persistent_v4l2_setup_script(
        card_label=safe_label, video_nr=safe_video_nr, devices=safe_devices,
    )
    have_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))

    if os.getuid() == 0:
        try:
            r = subprocess.run([sh_bin, "-c", setup_script], capture_output=True, text=True, timeout=20)
            if r.returncode == 0 and check_v4l2loopback_module()["loaded"]:
                _wait_for_udev_loopback_node(0.8)
                return {"ok": True, "method": "root-setup", "persisted": True, **check_v4l2loopback_module()}
        except Exception:
            pass

    if sudo_bin:
        try:
            r = subprocess.run([sudo_bin, "-n", sh_bin, "-c", setup_script], capture_output=True, text=True, timeout=12)
            if r.returncode == 0 and check_v4l2loopback_module()["loaded"]:
                _wait_for_udev_loopback_node(0.8)
                return {"ok": True, "method": "sudo-n", "persisted": True, **check_v4l2loopback_module()}
        except Exception:
            pass

    if interactive and have_display and pkexec_bin:
        try:
            r = subprocess.run([pkexec_bin, sh_bin, "-c", setup_script], capture_output=True, text=True, timeout=120)
            if r.returncode == 0 and check_v4l2loopback_module()["loaded"]:
                _wait_for_udev_loopback_node(0.8)
                _try_grant_user_acl_on_loopback()
                return {"ok": True, "method": "pkexec", "persisted": True, **check_v4l2loopback_module()}
        except Exception:
            pass

    return {
        "ok": False, "method": "failed", **check_v4l2loopback_module(),
        "error": "modprobe_failed",
        "hint": (f'Load it manually once: sudo modprobe -r v4l2loopback 2>/dev/null; '
                 f'sudo modprobe v4l2loopback devices=1 '
                 f'video_nr={safe_video_nr} card_label="{safe_label}" exclusive_caps=1 '
                 f'— and make it persist: '
                 f'echo v4l2loopback | sudo tee /etc/modules-load.d/v4l2loopback.conf'),
    }

DEFAULT_CARD_LABEL = "Samyuj Webcam"
DEFAULT_V4L2_VIDEO_NR = 10
FOURCC_MAP = NAME_TO_FOURCC
V4L2_PIX_FMT_NV12 = _fourcc("N", "V", "1", "2")
V4L2_CAP_VIDEO_M2M_MPLANE = 0x00004000
_FAST_LIB = _LIBSC_FAST

