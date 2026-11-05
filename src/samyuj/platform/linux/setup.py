from __future__ import annotations

import contextlib
import importlib.util
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

try:
    import av
    HAVE_TRACK = True
except Exception:
    HAVE_TRACK = False

from samyuj.core.paths import get_xdg_paths

_CPU_PREV_STAT: tuple[int, int] | None = None

def check_udp_buffer_headroom(target_bytes: int = 4 * 1024 * 1024) -> dict:
    from samyuj.media.peer import _LAST_SOCKET_RCVBUF_GOT
    rmem_max = 212992
    wmem_max = 212992
    with contextlib.suppress(Exception):
        rmem_p = Path("/proc/sys/net/core/rmem_max")
        if rmem_p.is_file():
            rmem_max = int(rmem_p.read_text(encoding="utf-8").strip())
        wmem_p = Path("/proc/sys/net/core/wmem_max")
        if wmem_p.is_file():
            wmem_max = int(wmem_p.read_text(encoding="utf-8").strip())
    clamped = rmem_max < (int(target_bytes) // 2)
    return {
        "rmem_max": rmem_max,
        "wmem_max": wmem_max,
        "target_bytes": int(target_bytes),
        "actual_socket_rcvbuf": int(_LAST_SOCKET_RCVBUF_GOT),
        "clamped": clamped,
        "sysctl_hint": "sudo sysctl -w net.core.rmem_max=8388608 net.core.wmem_max=8388608",
        "pkexec_cmd": "pkexec sysctl -w net.core.rmem_max=8388608 net.core.wmem_max=8388608",
    }

def read_kernel_udp_drop_counters(local_ports: set[int] | None = None) -> dict:
    from samyuj.media.peer import _ACTIVE_ICE_UDP_PORTS
    ports = set(local_ports) if local_ports is not None else set(_ACTIVE_ICE_UDP_PORTS)
    rcvbuf_errors = 0
    in_errors = 0
    in_datagrams = 0
    with contextlib.suppress(Exception):
        snmp_p = Path("/proc/net/snmp")
        if snmp_p.is_file():
            lines = snmp_p.read_text(encoding="utf-8", errors="replace").splitlines()
            for i in range(len(lines) - 1):
                if lines[i].startswith("Udp:") and lines[i + 1].startswith("Udp:"):
                    hdr = lines[i].split()[1:]
                    vals = lines[i + 1].split()[1:]
                    if len(hdr) == len(vals):
                        row = dict(zip(hdr, vals))
                        rcvbuf_errors = int(row.get("RcvbufErrors") or 0)
                        in_errors = int(row.get("InErrors") or 0)
                        in_datagrams = int(row.get("InDatagrams") or 0)
                    break
    socket_drops = 0
    all_udp_drops = 0
    for proc_name in ("/proc/net/udp", "/proc/net/udp6"):
        with contextlib.suppress(Exception):
            p = Path(proc_name)
            if not p.is_file():
                continue
            for ln in p.read_text(encoding="utf-8", errors="replace").splitlines()[1:]:
                cols = ln.split()
                if len(cols) < 13:
                    continue
                local_addr = cols[1]
                _, _, port_hex = local_addr.partition(":")
                if not port_hex:
                    continue
                port_num = int(port_hex, 16)
                drops_val = int(cols[-1])
                all_udp_drops += drops_val
                if port_num in ports:
                    socket_drops += drops_val
    return {
        "rcvbuf_errors": rcvbuf_errors,
        "in_errors": in_errors,
        "in_datagrams": in_datagrams,
        "socket_drops": socket_drops,
        "all_udp_drops": all_udp_drops,
        "tracked_ports": sorted(ports),
    }

def apply_udp_buffer_sysctl(target_bytes: int = 8 * 1024 * 1024,
                            interactive: bool = True) -> dict:
    before = check_udp_buffer_headroom(target_bytes // 2)
    if not before["clamped"]:
        return {
            "ok": True,
            "already_ok": True,
            "rmem_max_before": before["rmem_max"],
            "rmem_max_after": before["rmem_max"],
            "clamped": False,
            "method": "already-sufficient",
            "hint": f"net.core.rmem_max is already {before['rmem_max']} bytes",
        }
    tb = max(4 * 1024 * 1024, int(target_bytes))
    hint = f"sudo sysctl -w net.core.rmem_max={tb} net.core.wmem_max={tb}"
    if not interactive:
        return {
            "ok": False,
            "already_ok": False,
            "rmem_max_before": before["rmem_max"],
            "rmem_max_after": before["rmem_max"],
            "clamped": True,
            "method": "dry-run",
            "hint": hint,
        }
    sysctl_bin = shutil.which("sysctl") or "/sbin/sysctl"
    if not Path(sysctl_bin).exists():
        return {
            "ok": False,
            "rmem_max_before": before["rmem_max"],
            "rmem_max_after": before["rmem_max"],
            "clamped": True,
            "method": "missing-sysctl",
            "hint": hint,
        }
    cmd = [sysctl_bin, "-w", f"net.core.rmem_max={tb}", f"net.core.wmem_max={tb}"]
    method = "root-sysctl"
    if os.geteuid() != 0:
        pkexec_bin = shutil.which("pkexec")
        if not pkexec_bin:
            return {
                "ok": False,
                "rmem_max_before": before["rmem_max"],
                "rmem_max_after": before["rmem_max"],
                "clamped": True,
                "method": "no-pkexec",
                "hint": hint,
            }
        cmd = [pkexec_bin] + cmd
        method = "pkexec-sysctl"
    with contextlib.suppress(Exception):
        subprocess.run(cmd, capture_output=True, text=True, timeout=30.0)
    after = check_udp_buffer_headroom(target_bytes // 2)
    return {
        "ok": not after["clamped"],
        "already_ok": False,
        "rmem_max_before": before["rmem_max"],
        "rmem_max_after": after["rmem_max"],
        "clamped": after["clamped"],
        "method": method,
        "hint": "UDP receive buffer ceiling raised to 8 MB" if not after["clamped"] else hint,
    }

def probe_linux_hw_decoder() -> dict:
    from samyuj.core.tasks import _MOBILE_DESKTOP_SERVICE_THREAD
    from samyuj.media.present import _H264_GATE_STATS, _PRESENTER_BRIDGE
    render_nodes = sorted(str(p) for p in Path("/dev/dri").glob("renderD*")) if Path("/dev/dri").is_dir() else []
    hw_codecs: list[str] = []
    if HAVE_TRACK:
        for cname in ("h264_vaapi", "hevc_vaapi", "h264_nvdec", "hevc_nvdec", "h264_v4l2m2m", "vp8_vaapi", "vp8_v4l2m2m"):
            with contextlib.suppress(Exception):
                c = av.Codec(cname, "r")
                if c is not None:
                    hw_codecs.append(cname)
    return {
        "dri_render_nodes": render_nodes,
        "hw_decoders": hw_codecs,
        "low_delay_slice_active": True,
        "deblocking_preserved": os.environ.get("SC_H264_FAST_FLAG2") != "1",
        "udp_buffer": check_udp_buffer_headroom(),
        "idr_gate": dict(_H264_GATE_STATS),
        "presenter": {
            "enabled": bool(_PRESENTER_BRIDGE is not None),
            "alive": bool(_PRESENTER_BRIDGE is not None
                          and getattr(_PRESENTER_BRIDGE, "alive", False)),
            "engine": ("gst" if _PRESENTER_BRIDGE is not None else "gtk"),
            "stats": dict(getattr(_PRESENTER_BRIDGE, "stats", {}) or {})
            if _PRESENTER_BRIDGE is not None else {},
        },
        "desktop_service_thread": (
            _MOBILE_DESKTOP_SERVICE_THREAD.snapshot()
            if _MOBILE_DESKTOP_SERVICE_THREAD is not None
            else {"started": False, "alive": False, "thread_name": "sc-mobiledesktop-media"}
        ),
    }

def collect_pc_telemetry(battery_override: int | None = None,
                         charging_override: bool | None = None,
                         dnd_override: bool | None = None) -> dict:
    global _CPU_PREV_STAT
    bat_pct: int | None = None
    charging: bool | None = None
    ac_online: bool | None = None
    bat_status = "unknown"

    ps_root = Path("/sys/class/power_supply")
    if ps_root.is_dir():
        for d in sorted(ps_root.iterdir()):
            name = d.name.upper()
            with contextlib.suppress(OSError, ValueError):
                typ = (d / "type").read_text().strip().lower() if (d / "type").is_file() else ""
                if typ == "battery" or name.startswith("BAT"):
                    if (d / "capacity").is_file():
                        bat_pct = max(0, min(100, int((d / "capacity").read_text().strip())))
                    elif (d / "energy_now").is_file() and (d / "energy_full").is_file():
                        en = int((d / "energy_now").read_text().strip())
                        ef = max(1, int((d / "energy_full").read_text().strip()))
                        bat_pct = max(0, min(100, int(round(100.0 * en / ef))))
                    if (d / "status").is_file():
                        bat_status = (d / "status").read_text().strip()
                        if bat_status.lower() in ("charging", "full"):
                            charging = True
                        elif bat_status.lower() == "discharging":
                            charging = False
                elif typ in ("mains", "usb") or name.startswith(("AC", "ADP", "ACAD", " UCSI")):
                    if (d / "online").is_file():
                        ac_online = ((d / "online").read_text().strip() == "1")

    if charging is None and ac_online is not None:
        charging = ac_online
    if bat_pct is None:
        bat_pct = 100
    if charging is None:
        charging = True

    if battery_override is not None:
        bat_pct = max(0, min(100, int(battery_override)))
    if charging_override is not None:
        charging = bool(charging_override)

    cpu_pct = 0.0
    with contextlib.suppress(OSError, ValueError, IndexError):
        first = Path("/proc/stat").read_text().splitlines()[0].split()
        if first[0] == "cpu":
            vals = [int(x) for x in first[1:9]]
            idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
            total = sum(vals)
            if _CPU_PREV_STAT is not None:
                dt = total - _CPU_PREV_STAT[0]
                di = idle - _CPU_PREV_STAT[1]
                if dt > 0:
                    cpu_pct = round(100.0 * max(0, dt - di) / dt, 1)
            _CPU_PREV_STAT = (total, idle)

    mem_total_mb = 0
    mem_used_mb = 0
    mem_pct = 0.0
    with contextlib.suppress(OSError, ValueError):
        kv = {}
        for ln in Path("/proc/meminfo").read_text().splitlines():
            if ":" in ln:
                k, v = ln.split(":", 1)
                kv[k.strip()] = int(v.strip().split()[0])
        tot_kb = kv.get("MemTotal", 0)
        avail_kb = kv.get("MemAvailable", kv.get("MemFree", 0))
        if tot_kb > 0:
            mem_total_mb = tot_kb // 1024
            mem_used_mb = max(0, (tot_kb - avail_kb) // 1024)
            mem_pct = round(100.0 * (tot_kb - avail_kb) / tot_kb, 1)

    temp_c: float | None = None
    with contextlib.suppress(OSError, ValueError):
        tz_root = Path("/sys/class/thermal")
        if tz_root.is_dir():
            for tz in sorted(tz_root.glob("thermal_zone*/temp")):
                raw_t = int(tz.read_text().strip())
                if 15000 <= raw_t <= 115000:
                    temp_c = round(raw_t / 1000.0, 1)
                    break

    load_1m = 0.0
    with contextlib.suppress(OSError, AttributeError):
        load_1m = round(os.getloadavg()[0], 2)

    uptime_s = 0
    with contextlib.suppress(OSError, ValueError):
        uptime_s = int(float(Path("/proc/uptime").read_text().split()[0]))

    return {
        "battery": int(bat_pct),
        "isCharging": bool(charging),
        "chargePlug": 1 if charging else 0,
        "batteryStatus": bat_status,
        "dndState": 1 if dnd_override else 0,
        "cpuPercent": cpu_pct,
        "memUsedMb": mem_used_mb,
        "memTotalMb": mem_total_mb,
        "memPercent": mem_pct,
        "tempC": temp_c,
        "load1m": load_1m,
        "uptimeSec": uptime_s,
        "timestamp": int(time.time() * 1000),
    }

def _safe_find_spec(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ModuleNotFoundError, ValueError):
        return False

async def run_linux_setup_check(runtime: Any, root_dir: str | Path | None = None) -> dict[str, Any]:
    from samyuj.media.v4l2 import detect_linux_distro_v4l2_hints, diagnose_v4l2_setup
    from samyuj.transport.network import PortReachabilityChecker

    base = Path(root_dir) if root_dir else Path(__file__).resolve().parents[4]
    xdg = get_xdg_paths()
    desktop_file = base / "packaging" / "samyuj.desktop"
    for _cand_df in (
        base / "packaging" / "samyuj.desktop",
        base / "packaging" / "linux" / "samyuj.desktop",
        xdg.data_dir.parent / "applications" / "samyuj.desktop",
        Path("/usr/share/applications/samyuj.desktop"),
    ):
        if _cand_df.is_file():
            desktop_file = _cand_df
            break

    xdg_ok = all(
        d.is_dir() for d in (xdg.config_dir, xdg.data_dir, xdg.cache_dir, xdg.runtime_dir)
    )
    desktop_ok = False
    if desktop_file.is_file():
        txt = desktop_file.read_text(encoding="utf-8", errors="replace")
        desktop_ok = "[Desktop Entry]" in txt and "Type=Application" in txt and "Exec=" in txt

    usb_cfg = getattr(runtime, "usb_config_manager", None)
    usb_state = usb_cfg.state.to_dict() if usb_cfg is not None else {"status": "standby"}

    wc_ctrl = getattr(runtime, "webcam_controller", None)
    target_spec = wc_ctrl.v4l2_writer.target_spec if wc_ctrl is not None else "auto"
    v4l2_diag = diagnose_v4l2_setup(target_spec).to_dict()

    nfs_srv = getattr(runtime, "_nfs_srv", None)
    exp = getattr(nfs_srv, "export", None)
    shares = exp.list_shares() if (exp is not None and hasattr(exp, "list_shares")) else []

    reachability = await PortReachabilityChecker.verify_harness_listeners(runtime)
    udp_buf = check_udp_buffer_headroom()
    cairo_bridge_ok = _safe_find_spec("gi._gi_cairo")
    py_cairo_ok = _safe_find_spec("cairo")
    distro_hints = detect_linux_distro_v4l2_hints()

    return {
        "ok": bool(xdg_ok and reachability.get("all_listeners_reachable", True)),
        "xdg": {
            "ok": xdg_ok,
            "config_dir": str(xdg.config_dir),
            "data_dir": str(xdg.data_dir),
            "cache_dir": str(xdg.cache_dir),
            "runtime_dir": str(xdg.runtime_dir),
        },
        "desktop_entry": {
            "ok": desktop_ok,
            "path": str(desktop_file),
        },
        "gtk_cairo_bridge": {
            "ok": bool(cairo_bridge_ok and py_cairo_ok),
            "gi_cairo": cairo_bridge_ok,
            "pycairo": py_cairo_ok,
            "install_hint": distro_hints.get("gi_cairo_install", "sudo apt install -y python3-gi-cairo"),
        },
        "udp_socket_buffers": udp_buf,
        "usb_network": usb_state,
        "v4l2_webcam": v4l2_diag,
        "v4l2_distro_hints": distro_hints,
        "nfs_shares": shares,
        "reachability": reachability,
    }
