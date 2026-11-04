from __future__ import annotations

import asyncio
import contextlib
import re
import shutil
import socket
import subprocess
import time

_VALID_IFACE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,31}$")
_VALID_SERIAL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")

ADB_PKG = "com.motorola.mobiledesktop"
ADB_TRIGGER_CMDS: list[tuple[str, list[str]]] = [
    ("EXTERNAL_SETUP implicit", ["am", "start", "-a", "com.motorola.mobiledesktop.action.EXTERNAL_SETUP"]),
    (
        "EXTERNAL_SETUP explicit",
        [
            "am",
            "start",
            "-a",
            "com.motorola.mobiledesktop.action.EXTERNAL_SETUP",
            "-n",
            f"{ADB_PKG}/com.motorola.readyfor.view.OutsideJumpActivity",
        ],
    ),
]

def is_private_ip(ip: str) -> bool:
    try:
        a, b = (int(x) for x in ip.split(".")[:2])
    except (ValueError, AttributeError):
        return False
    return a == 10 or (a == 192 and b == 168) or (a == 172 and 16 <= b <= 31)

def probe_phone_ip(iface: str, pc_ip: str = "", sweep: bool = False) -> str | None:
    if not iface or not _VALID_IFACE_RE.match(str(iface)):
        return None
    cands: list[str] = []
    with contextlib.suppress(Exception):
        out = subprocess.run(
            ["ip", "route", "show", "dev", iface],
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout
        for ln in out.splitlines():
            parts = ln.split()
            if "via" in parts:
                cands.append(parts[parts.index("via") + 1])
            elif parts and "." in parts[0] and "/" not in parts[0] and parts[0] != "default":
                cands.append(parts[0])
    with contextlib.suppress(Exception):
        out = subprocess.run(
            ["ip", "neigh", "show", "dev", iface],
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout
        for ln in out.splitlines():
            parts = ln.split()
            if (
                len(parts) >= 4
                and ":" not in parts[0]
                and parts[-1] in ("REACHABLE", "STALE", "DELAY", "PROBE", "PERMANENT")
            ):
                cands.append(parts[0])
    from samyuj.transport.discovery.usb import VENDOR_USB_P2P_PC_IP, VENDOR_USB_P2P_PHONE_IP
    if pc_ip == VENDOR_USB_P2P_PC_IP:
        cands.append(VENDOR_USB_P2P_PHONE_IP)
    seen: set[str] = set()
    ordered: list[str] = []
    for c in cands:
        if c and c != pc_ip and c not in seen and is_private_ip(c):
            seen.add(c)
            ordered.append(c)
    for c in ordered:
        with contextlib.suppress(Exception):
            rc = subprocess.run(
                ["ping", "-c", "1", "-W", "1", c],
                capture_output=True,
                timeout=2,
            ).returncode
            if rc == 0:
                return c
    return ordered[0] if ordered else None

async def adb_trigger_phone(serial: str | None = None) -> tuple[bool, str]:
    if not shutil.which("adb"):
        return False, "adb not installed on PATH"
    if serial is not None and (not serial or not _VALID_SERIAL_RE.match(str(serial))):
        return False, "Invalid ADB device serial"
    for label, cmd in ADB_TRIGGER_CMDS:
        full = ["adb"] + (["-s", str(serial)] if serial else []) + ["shell"] + cmd
        try:
            proc = await asyncio.create_subprocess_exec(
                *full,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            out_b, _ = await asyncio.wait_for(proc.communicate(), timeout=8.0)
            out = out_b.decode("utf-8", "replace").strip()
            low = out.lower()
            if "starting:" in low and "error" not in low and "denial" not in low:
                return True, f"{label}: {out[:140]}"
        except Exception:
            continue
    return False, "ADB trigger refused or no authorized ADB device connected"
