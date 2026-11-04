from __future__ import annotations

import contextlib
import ipaddress
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from samyuj.protocol.constants import (
    VENDOR_USB_P2P_PC_CIDR,
    VENDOR_USB_P2P_PC_IP,
    VENDOR_USB_P2P_PHONE_IP,
)

USB_IFACE_RE = re.compile(r"^(usb\d+|rndis\d+|enx[0-9a-fA-F]{12}|en[psx][0-9a-zA-Z]*u\d+.*)$")
PHONE_STATIC_IPS: tuple[str, ...] = ()

READYFOR_PIDS = {
    "2e24": "RNDIS (USB Tethering ON)",
    "2e25": "RNDIS + ADB (USB Tethering ON + USB debug)",
    "2e82": "MTP (File Transfer)",
    "2e81": "MTP + ADB",
    "2e76": "PTP (Photo Transfer)",
    "2e83": "Charging / MIDI",
}

ANDROID_PHONE_VENDORS = {
    "22b8": "Motorola",
    "17ef": "Lenovo",
    "18d1": "Google",
    "04e8": "Samsung",
    "2717": "Xiaomi",
    "2a70": "OnePlus",
    "0fce": "Sony",
    "0bb4": "HTC",
    "1004": "LG",
    "12d1": "Huawei",
    "22d9": "OPPO",
    "2d95": "vivo",
    "0b05": "ASUS",
    "0489": "Foxconn",
    "19d2": "ZTE",
    "29a9": "Nothing",
}

KNOWN_PHONE_USB_VIDS = set(ANDROID_PHONE_VENDORS.keys())

NEVER_PHONE_VENDORS = {
    "1d6b",
    "8087",
    "046d",
    "1532",
    "04f2",
    "0bda",
    "05e3",
    "2109",
    "174c",
    "0781",
    "0951",
    "045e",
    "05ac",
}

def static_link_cmds(iface: str, ip_cidr: str = VENDOR_USB_P2P_PC_CIDR) -> list[list[str]]:
    return [
        ["ip", "link", "set", iface, "up"],
        ["ip", "addr", "replace", ip_cidr, "dev", iface],
    ]

def net_snapshot() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    sys_net = Path("/sys/class/net")
    if sys_net.is_dir():
        for p in sorted(sys_net.iterdir()):
            name = p.name
            if name == "lo":
                continue
            try:
                state = (p / "operstate").read_text().strip()
            except OSError:
                state = "?"
            try:
                mac = (p / "address").read_text().strip()
            except OSError:
                mac = "?"
            usb_marker = None
            with contextlib.suppress(OSError):
                dev_link = (p / "device").resolve()
                if "usb" in str(dev_link).lower():
                    usb_marker = str(dev_link)
            out[name] = {
                "operstate": state,
                "mac": mac,
                "ipv4": [],
                "usb_device": usb_marker,
            }
    try:
        r = subprocess.run(
            ["ip", "-o", "-4", "addr", "show"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        for line in r.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 4 and parts[2] == "inet":
                ifname = parts[1]
                cidr = parts[3]
                ip = cidr.split("/")[0]
                plen = int(cidr.split("/")[1]) if "/" in cidr else 24
                out.setdefault(
                    ifname,
                    {"operstate": "?", "mac": "?", "ipv4": [], "usb_device": None},
                )["ipv4"].append((ip, plen))
    except Exception:
        pass
    return out

_NET_SNAP_CACHE: tuple[int, float, dict[str, dict[str, Any]]] = (0, 0.0, {})

def _cached_net_snapshot(max_age: float = 0.75) -> dict[str, dict[str, Any]]:
    global _NET_SNAP_CACHE
    fn_id = id(net_snapshot)
    now = time.monotonic()
    c_id, c_ts, c_snap = _NET_SNAP_CACHE
    if c_id == fn_id and (now - c_ts) <= max_age and c_snap:
        return c_snap
    fresh = net_snapshot()
    _NET_SNAP_CACHE = (fn_id, now, fresh)
    return fresh

def iface_for_ip(ip: str, net: dict[str, dict[str, Any]] | None = None) -> str | None:
    raw = str(ip or "").strip()
    if not raw or raw == "127.0.0.1":
        return None
    try:
        addr = ipaddress.ip_address(raw)
    except ValueError:
        return None
    snap = net if net is not None else _cached_net_snapshot()
    for name, meta in snap.items():
        for if_ip, plen in meta.get("ipv4") or []:
            if if_ip == raw:
                return name
            with contextlib.suppress(ValueError):
                net_obj = ipaddress.ip_interface(f"{if_ip}/{plen}").network
                if addr in net_obj:
                    return name
    return None

def is_usb_ip(
    ip: str,
    net: dict[str, dict[str, Any]] | None = None,
    usb_link: tuple[str, str] | None = None,
) -> bool:
    raw = str(ip or "").strip()
    if not raw or raw == "127.0.0.1":
        return False
    if usb_link and len(usb_link) >= 2 and usb_link[1]:
        u_ip = str(usb_link[1]).strip()
        if raw == u_ip:
            return True
        if "." in raw and "." in u_ip and raw.rsplit(".", 1)[0] == u_ip.rsplit(".", 1)[0]:
            return True
    snap = net if net is not None else _cached_net_snapshot()
    ifname = iface_for_ip(raw, snap)
    if ifname is not None:
        meta = snap.get(ifname) or {}
        return bool(meta.get("usb_device") or USB_IFACE_RE.match(ifname))
    return raw in (VENDOR_USB_P2P_PHONE_IP, VENDOR_USB_P2P_PC_IP)

def usb_device_snapshot() -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    base = Path("/sys/bus/usb/devices")
    if not base.is_dir():
        return out
    for d in sorted(base.iterdir()):
        vid_f = d / "idVendor"
        pid_f = d / "idProduct"
        if not (vid_f.is_file() and pid_f.is_file()):
            continue
        try:
            vid = vid_f.read_text().strip().lower()
            pid = pid_f.read_text().strip().lower()
        except OSError:
            continue
        prod = ""
        mfg = ""
        serial = ""
        b_class = ""
        with contextlib.suppress(OSError):
            if (d / "product").is_file():
                prod = (d / "product").read_text().strip()
            if (d / "manufacturer").is_file():
                mfg = (d / "manufacturer").read_text().strip()
            if (d / "serial").is_file():
                serial = (d / "serial").read_text().strip()
            if (d / "bDeviceClass").is_file():
                b_class = (d / "bDeviceClass").read_text().strip().lower()
        has_net_if = any((c / "net").is_dir() for c in d.glob(f"{d.name}:*"))
        out.append(
            {
                "node": d.name,
                "vid": vid,
                "pid": pid,
                "idVendor": vid,
                "idProduct": pid,
                "bDeviceClass": b_class,
                "product": prod,
                "manufacturer": mfg,
                "serial": serial,
                "mode_hint": READYFOR_PIDS.get(pid, "RNDIS/NCM" if has_net_if else ""),
                "has_net_iface": "1" if has_net_if else "0",
            }
        )
    return out

def usb_phone_candidates(
    devs: list[dict[str, str]] | None = None,
    extra_vids: list[str] | set[str] | tuple[str, ...] = (),
) -> list[dict[str, str]]:
    if devs is None:
        devs = usb_device_snapshot()
    extra = {str(v).strip().lower().removeprefix("0x") for v in (extra_vids or ()) if str(v).strip()}
    cands: list[dict[str, str]] = []
    for d in devs:
        vid = str(d.get("idVendor") or d.get("vid") or "").lower()
        b_cls = str(d.get("bDeviceClass") or "").lower()
        if b_cls == "09" or (vid in NEVER_PHONE_VENDORS and vid not in extra):
            continue
        if vid in ANDROID_PHONE_VENDORS or vid in extra or d.get("has_net_iface") == "1" or d.get("serial"):
            cands.append(d)
    cands.sort(key=lambda x: (0 if str(x.get("idVendor") or x.get("vid") or "").lower() in (set(ANDROID_PHONE_VENDORS) | extra) else 1))
    return cands

def usb_candidate_matches_names(
    cand_or_devs: dict[str, str] | list[dict[str, str]],
    paired_names: list[str] | set[str] | tuple[str, ...] = (),
    extra_vids: list[str] | set[str] | tuple[str, ...] = (),
) -> bool:
    items = [cand_or_devs] if isinstance(cand_or_devs, dict) else list(cand_or_devs or [])
    extra = {str(v).strip().lower().removeprefix("0x") for v in (extra_vids or ()) if str(v).strip()}
    name_tokens = {
        tok.lower()
        for nm in (paired_names or ())
        for tok in re.split(r"[^a-zA-Z0-9]+", str(nm or ""))
        if len(tok) >= 3
    }
    for d in items:
        vid = str(d.get("idVendor") or d.get("vid") or "").lower()
        if extra and vid in extra:
            return True
        if d.get("has_net_iface") == "1":
            return True
        if name_tokens:
            hay = f"{d.get('product', '')} {d.get('manufacturer', '')}".lower()
            if any(tok in hay for tok in name_tokens):
                return True
    return False

def find_usb_tether_iface(
    phone_ips: set[str] | list[str] | None = None,
) -> tuple[str, str] | None:
    return find_tether_link(net_snapshot(), phone_ips=phone_ips)

def find_tether_link(
    net: dict[str, dict[str, Any]] | None = None,
    phone_ips: set[str] | list[str] | None = None,
) -> tuple[str, str] | None:
    if net is None:
        net = net_snapshot()
    skip = {str(x).strip() for x in (phone_ips or ()) if str(x).strip()}
    for name, meta in net.items():
        is_usb = meta.get("usb_device") is not None or bool(USB_IFACE_RE.match(name))
        if not is_usb:
            continue
        for ip, _plen in meta.get("ipv4", []):
            if ip not in skip and ip != "127.0.0.1":
                return (name, ip)
    return None

def wait_for_tether_ipv4(
    iface: str,
    timeout: float = 15.0,
    phone_ips: set[str] | list[str] | None = None,
) -> str | None:
    deadline = time.monotonic() + timeout
    skip = {str(x).strip() for x in (phone_ips or ()) if str(x).strip()}
    while time.monotonic() < deadline:
        snap = net_snapshot()
        for ip, _plen in snap.get(iface, {}).get("ipv4", []):
            if ip not in skip and ip != "127.0.0.1":
                return ip
        time.sleep(0.5)
    return None
