from __future__ import annotations
from dataclasses import dataclass

import asyncio
import contextlib
import datetime
import re
import shutil
import socket
import subprocess
import time

from samyuj.protocol.constants import (
    VENDOR_USB_P2P_PC_CIDR,
    VENDOR_USB_P2P_PC_IP,
    VENDOR_USB_P2P_PHONE_IP,
)
from samyuj.transport.discovery.usb import is_usb_ip

WIFI_IFACE_RE = re.compile(r"^(wl|wlan|wifi)", re.I)

USB_PC_IP = VENDOR_USB_P2P_PC_IP
USB_PHONE_IP = VENDOR_USB_P2P_PHONE_IP
USB_SUBNET_PREFIXES = ("192.168.201.", "192.168.42.", "192.168.137.")

_IPV4_EXTRACT_RE = re.compile(r"(\d+\.\d+\.\d+\.\d+)")

def is_usb_peer_address(
    addr: str | None,
    net: dict | None = None,
    usb_link: tuple[str, str] | None = None,
) -> bool:
    clean = str(addr or "").strip()
    if not clean:
        return False
    m = _IPV4_EXTRACT_RE.search(clean)
    ip = m.group(1) if m else clean
    return is_usb_ip(ip, net=net, usb_link=usb_link)

def pick_local_ip_for_peer(peer_ip: str, candidates: list[str], current: str,
                           preferred: list[str] | None = None) -> str:
    if not peer_ip or peer_ip.startswith("127.") or not candidates:
        return current
    pref = set(preferred or ())

    def _score(ip: str) -> tuple[int, int]:
        if not ip or ip == peer_ip:
            return (9, 1)
        po = peer_ip.split(".")
        oo = ip.split(".")
        subnet = 2
        if len(po) == 4 and len(oo) == 4:
            if po[:3] == oo[:3]:
                subnet = 0
            elif po[:2] == oo[:2]:
                subnet = 1
        return (subnet, 0 if ip in pref else 1)

    best = min(candidates, key=_score)
    return best if _score(best)[0] < 2 else current

def iface_ipv4s() -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        txt = subprocess.run(["ip", "-4", "-o", "addr", "show", "scope", "global"],
                             capture_output=True, text=True, timeout=2).stdout
        for m in re.finditer(r"^\d+:\s+([A-Za-z0-9_.@-]+)\s+inet\s+(\d+\.\d+\.\d+\.\d+)",
                             txt, re.M):
            out.setdefault(m.group(1), m.group(2))
        if out:
            return out
    except Exception:
        pass
    import fcntl
    import os
    try:
        base = "/sys/class/net"
        for name in sorted(os.listdir(base)):
            if name == "lo":
                continue
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                try:
                    ifreq = struct_pack_256s(name)
                    ip = socket.inet_ntoa(fcntl.ioctl(s.fileno(), 0x8915, ifreq)[20:24])
                finally:
                    s.close()
                if ip and not ip.startswith("127."):
                    out.setdefault(name, ip)
            except Exception:
                continue
    except Exception:
        pass
    return out

def struct_pack_256s(name: str) -> bytes:
    import struct as _struct
    return _struct.pack("256s", name[:15].encode("utf-8"))

def wifi_ipv4s() -> list[str]:
    ifs = iface_ipv4s()
    if not ifs:
        return []
    out: list[str] = []
    for name, ip in ifs.items():
        is_wifi = bool(WIFI_IFACE_RE.match(name))
        if not is_wifi:
            with contextlib.suppress(Exception):
                import os
                is_wifi = os.path.isdir(f"/sys/class/net/{name}/wireless")
        if is_wifi and ip not in out:
            out.append(ip)
    return out

def pick_startup_ip(default_route_ip: str, all_ips: list[str], wifi_ips: list[str]) -> str:
    if wifi_ips:
        return default_route_ip if default_route_ip in wifi_ips else wifi_ips[0]
    if default_route_ip and default_route_ip in all_ips:
        return default_route_ip
    return all_ips[0] if all_ips else (default_route_ip or "127.0.0.1")

def resolve_session_ip(harness, conn: dict | None = None) -> str:
    usb_link = getattr(harness, "usb_link", None)
    usb_ip = usb_link[1] if (isinstance(usb_link, (tuple, list)) and len(usb_link) >= 2) else None
    wifi_ip = getattr(harness, "wifi_ip", None) or getattr(harness, "lan_ip", "127.0.0.1")
    if wifi_ip in ("127.0.0.1", "0.0.0.0", "") and getattr(harness, "lan_ip_override", None):
        wifi_ip = harness.lan_ip_override

    if conn is not None:
        remote_str = str(conn.get("remote") or "")
        if conn.get("transport") == "usb" or is_usb_peer_address(remote_str):
            return usb_ip or USB_PC_IP
        if conn.get("transport") == "wifi" or (
            remote_str and not is_usb_peer_address(remote_str) and "127.0.0.1" not in remote_str
        ):
            if wifi_ip and not is_usb_peer_address(wifi_ip):
                return wifi_ip

    if usb_ip and (not wifi_ip or wifi_ip == "127.0.0.1"):
        return usb_ip
    return wifi_ip or usb_ip or "127.0.0.1"

@dataclass
class UsbConfigState:
    iface: str = ""
    status: str = "standby"
    configured_ip: str = ""
    phone_ip: str = USB_PHONE_IP
    method: str = "none"
    attempt_count: int = 0
    last_attempt_utc: str = ""
    last_result: str = ""
    next_retry_mono: float = 0.0
    retry_backoff_s: float = 3.0
    required_privilege_action: str = ""

    def to_dict(self) -> dict:
        now_m = time.monotonic()
        rem = max(0.0, round(self.next_retry_mono - now_m, 1)) if self.next_retry_mono > now_m else 0.0
        return {
            "iface": self.iface,
            "status": self.status,
            "configured_ip": self.configured_ip,
            "phone_ip": self.phone_ip,
            "method": self.method,
            "attempt_count": self.attempt_count,
            "last_attempt_utc": self.last_attempt_utc,
            "last_result": self.last_result,
            "next_retry_in_s": rem,
            "required_privilege_action": self.required_privilege_action,
        }

class UsbConfigManager:

    def __init__(self, target_ip: str = USB_PC_IP, prefix_len: int = 24) -> None:
        self.target_ip = target_ip
        self.prefix_len = prefix_len
        self.state = UsbConfigState()
        self.custom_configurator = None

    def status_dict(self) -> dict:
        return self.state.to_dict()

    def configure_interface(
        self,
        iface: str,
        force: bool = False,
        addr_order: str = "dhcp,static-p2p",
    ) -> dict:
        return self.attempt_configure(iface, force=force, addr_order=addr_order)

    def ensure_configured(
        self,
        iface: str,
        cidr: str = VENDOR_USB_P2P_PC_CIDR,
        addr_order: str = "dhcp,static-p2p",
        force: bool = False,
    ) -> dict:
        if cidr and "/" in cidr:
            ip_part, plen_part = cidr.split("/", 1)
            self.target_ip = ip_part.strip()
            with contextlib.suppress(ValueError):
                self.prefix_len = int(plen_part.strip())
        res = self.attempt_configure(iface, force=force, addr_order=addr_order)
        res["ok"] = res.get("status") == "configured"
        res["assigned_ip"] = res.get("configured_ip", "")
        return res

    def retry_now(self, iface: str = "usb0") -> dict:
        return self.attempt_configure(iface, force=True)

    def should_attempt(self, iface: str, force: bool = False) -> bool:
        if not iface:
            return False
        if force:
            return True
        if self.state.iface == iface and self.state.status == "configured":
            return False
        return time.monotonic() >= self.state.next_retry_mono

    def record_existing_link(self, iface: str, ip: str, phone_ip: str = USB_PHONE_IP) -> dict:
        self.state.iface = iface
        self.state.status = "configured"
        self.state.configured_ip = ip
        self.state.phone_ip = phone_ip
        self.state.method = "existing"
        self.state.last_result = f"USB interface {iface} already has {ip}"
        self.state.required_privilege_action = ""
        self.state.next_retry_mono = 0.0
        return self.state.to_dict()

    def attempt_configure(
        self,
        iface: str,
        force: bool = False,
        addr_order: str = "dhcp,static-p2p",
    ) -> dict:
        if not self.should_attempt(iface, force=force):
            return self.state.to_dict()

        now_utc = datetime.datetime.now(datetime.UTC).isoformat()
        self.state.iface = iface
        self.state.attempt_count += 1
        self.state.last_attempt_utc = now_utc
        cidr = f"{self.target_ip}/{self.prefix_len}"

        if self.custom_configurator is not None:
            ok, method, detail, priv_hint = self.custom_configurator(iface, cidr)
        else:
            from samyuj.platform.linux.usb_network import configure_usb_interface_linux
            ok, method, detail, priv_hint = configure_usb_interface_linux(
                iface, cidr, addr_order=addr_order
            )

        self.state.method = method
        self.state.last_result = detail
        if ok:
            self.state.status = "configured"
            self.state.configured_ip = detail if (detail and "." in detail) else self.target_ip
            self.state.required_privilege_action = ""
            self.state.next_retry_mono = 0.0
            self.state.retry_backoff_s = 3.0
        else:
            self.state.status = "needs_privilege" if priv_hint else "failed"
            self.state.configured_ip = ""
            self.state.required_privilege_action = priv_hint
            self.state.next_retry_mono = time.monotonic() + self.state.retry_backoff_s
            self.state.retry_backoff_s = min(30.0, self.state.retry_backoff_s * 1.5)
        return self.state.to_dict()

class PortReachabilityChecker:

    def __init__(self, harness=None) -> None:
        self.harness = harness

    async def check_all(self, harness=None) -> dict:
        h = harness or self.harness
        return await self.verify_harness_listeners(h)

    @staticmethod
    async def check_tcp_port(host: str, port: int, timeout: float = 1.5) -> dict:
        if port <= 0:
            return {"port": port, "proto": "tcp", "reachable": False, "error": "port not bound (0)"}
        target_host = "127.0.0.1" if host in ("0.0.0.0", "", "::") else host
        t0 = time.perf_counter()
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(target_host, port), timeout=timeout
            )
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            return {
                "port": port,
                "proto": "tcp",
                "host": target_host,
                "reachable": True,
                "latency_ms": round((time.perf_counter() - t0) * 1000.0, 2),
                "error": "",
            }
        except Exception as exc:
            if target_host != "127.0.0.1":
                try:
                    reader, writer = await asyncio.wait_for(
                        asyncio.open_connection("127.0.0.1", port), timeout=timeout
                    )
                    writer.close()
                    with contextlib.suppress(Exception):
                        await writer.wait_closed()
                    return {
                        "port": port,
                        "proto": "tcp",
                        "host": "127.0.0.1",
                        "reachable": True,
                        "latency_ms": round((time.perf_counter() - t0) * 1000.0, 2),
                        "note": f"bound on 0.0.0.0 (verified via 127.0.0.1; {target_host} not local)",
                        "error": "",
                    }
                except Exception:
                    pass
            return {
                "port": port,
                "proto": "tcp",
                "host": target_host,
                "reachable": False,
                "latency_ms": round((time.perf_counter() - t0) * 1000.0, 2),
                "error": f"{type(exc).__name__}: {exc}",
            }

    @staticmethod
    def detect_firewall_status() -> dict:
        active_fw = "none_detected"
        hint = ""
        if shutil.which("ufw"):
            active_fw = "ufw_installed"
            hint = "If the phone cannot reach PC media/NFS ports, allow the subnet: sudo ufw allow from 192.168.0.0/16"
        elif shutil.which("firewall-cmd"):
            active_fw = "firewalld_installed"
            hint = "If the phone cannot reach PC media/NFS ports, trust the interface: sudo firewall-cmd --add-Rich-rule=..."
        elif shutil.which("nft"):
            active_fw = "nftables_installed"
        return {
            "firewall": active_fw,
            "remediation_hint": hint,
        }

    @classmethod
    async def verify_harness_listeners(cls, harness) -> dict:
        host = getattr(harness, "lan_ip", "127.0.0.1")
        ports_to_check: dict[str, int] = {
            "control_ws": int(getattr(harness, "port", 0) or 0),
        }
        fs_ports = getattr(harness, "fs_ports", {}) or {}
        for role in ("portmap", "mountd", "nfsd"):
            if fs_ports.get(role):
                ports_to_check[f"nfs_{role}"] = int(fs_ports[role])
        if getattr(harness, "xfer_port", 0):
            ports_to_check["filetransfer_tls"] = int(harness.xfer_port)
        if getattr(harness, "_gui", None) and getattr(harness._gui, "port", 0):
            ports_to_check["gui_http"] = int(harness._gui.port)

        results = {}
        for name, p in ports_to_check.items():
            results[name] = await cls.check_tcp_port(host, p)
        return {
            "all_reachable": all(r["reachable"] for r in results.values()) if results else False,
            "listeners": results,
            "firewall": cls.detect_firewall_status(),
        }

def sync_active_capabilities(harness) -> tuple[dict[str, int], dict[str, int]]:
    caps = dict(getattr(harness, "pc_caps", {}) or {})
    vers = dict(getattr(harness, "pc_service_versions", None) or getattr(harness, "PC_SERVICE_VERSIONS", {}) or {})

    if getattr(harness, "desktop", "accept") == "off":
        caps.pop("1", None)
        vers.pop("1", None)
    if getattr(harness, "appstream", "auto") == "off":
        caps.pop("15", None)
        vers.pop("15", None)
    if getattr(harness, "screenshare", "auto") == "off":
        caps.pop("202", None)
        vers.pop("202", None)
    if getattr(harness, "webcam", "auto") == "off":
        caps.pop("3", None)
        vers.pop("3", None)
    if getattr(harness, "audio", "on") == "off":
        caps.pop("21", None)
        vers.pop("21", None)
    if getattr(harness, "filestation", "server") == "off":
        caps.pop("168", None)
        vers.pop("168", None)
    else:
        vers["168"] = 1
        caps.setdefault("168", 3)

    harness.pc_caps = caps
    harness.pc_service_versions = vers
    return caps, vers

def detect_lan_ip(fallback: str = "127.0.0.1") -> str:
    with contextlib.suppress(OSError):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("192.0.2.1", 80))
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
        finally:
            s.close()
    return fallback

def list_all_ipv4s(lan_ip: str = "127.0.0.1") -> list[str]:
    out: list[str] = []
    with contextlib.suppress(Exception):
        r = subprocess.run(
            ["ip", "-4", "-o", "addr", "show"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
        for line in (r.stdout or "").splitlines():
            parts = line.split()
            if "inet" in parts:
                idx = parts.index("inet")
                if idx + 1 < len(parts):
                    ip = parts[idx + 1].split("/")[0]
                    if ip and not ip.startswith("127.") and ip not in out:
                        out.append(ip)
    if lan_ip and not lan_ip.startswith("127.") and lan_ip not in out:
        out.insert(0, lan_ip)
    return out or [lan_ip]
