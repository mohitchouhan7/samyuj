from __future__ import annotations

import shutil
import subprocess
import time

from samyuj.protocol.constants import VENDOR_USB_P2P_PC_CIDR
from samyuj.transport.discovery.usb import net_snapshot, static_link_cmds

def _iface_first_ipv4(iface: str) -> str:
    snap = net_snapshot()
    ipv4s = (snap.get(iface) or {}).get("ipv4") or []
    for ip, _plen in ipv4s:
        if ip and ip != "127.0.0.1":
            return str(ip)
    return ""

def _try_dhcp_client(iface: str) -> tuple[bool, str, str, str]:
    nmcli_bin = shutil.which("nmcli")
    if nmcli_bin:
        try:
            subprocess.run(
                [nmcli_bin, "dev", "set", iface, "managed", "yes"],
                capture_output=True, text=True, timeout=4.0,
            )
            subprocess.run(
                [nmcli_bin, "dev", "connect", iface],
                capture_output=True, text=True, timeout=6.0,
            )
            for _ in range(4):
                got = _iface_first_ipv4(iface)
                if got and not got.startswith("169.254."):
                    return True, "dhcp_nmcli", got, ""
                time.sleep(0.35)
        except Exception:
            pass
    dhclient_bin = shutil.which("dhclient")
    if dhclient_bin:
        try:
            subprocess.run(
                [dhclient_bin, "-1", "-lf", f"/tmp/sc-dhclient-{iface}.leases", iface],
                capture_output=True, text=True, timeout=5.0,
            )
            got = _iface_first_ipv4(iface)
            if got and not got.startswith("169.254."):
                return True, "dhcp_dhclient", got, ""
        except Exception:
            pass
    return False, "dhcp", "", ""

def _try_link_local(iface: str) -> tuple[bool, str, str, str]:
    got = _iface_first_ipv4(iface)
    if got and got.startswith("169.254."):
        return True, "link_local", got, ""
    return False, "link_local", "", ""

def _try_static_p2p(iface: str, cidr: str) -> tuple[bool, str, str, str]:
    want_ip = cidr.split("/", 1)[0].strip()
    nmcli_bin = shutil.which("nmcli")
    if nmcli_bin:
        try:
            subprocess.run(
                [nmcli_bin, "dev", "set", iface, "managed", "yes"],
                capture_output=True, text=True, timeout=5.0,
            )
            r_mod = subprocess.run(
                [
                    nmcli_bin, "dev", "modify", iface,
                    "ipv4.method", "manual",
                    "ipv4.addresses", cidr,
                ],
                capture_output=True, text=True, timeout=6.0,
            )
            if r_mod.returncode != 0:
                con_name = f"samyuj-{iface}"
                subprocess.run(
                    [nmcli_bin, "con", "delete", con_name],
                    capture_output=True, text=True, timeout=4.0,
                )
                r_add = subprocess.run(
                    [
                        nmcli_bin, "con", "add",
                        "type", "ethernet",
                        "ifname", iface,
                        "con-name", con_name,
                        "ipv4.method", "manual",
                        "ipv4.addresses", cidr,
                        "connection.autoconnect", "yes",
                    ],
                    capture_output=True, text=True, timeout=6.0,
                )
                if r_add.returncode == 0:
                    subprocess.run(
                        [nmcli_bin, "con", "up", con_name],
                        capture_output=True, text=True, timeout=6.0,
                    )
            time.sleep(0.4)
            snap = net_snapshot()
            for ip, _plen in (snap.get(iface) or {}).get("ipv4") or []:
                if ip == want_ip:
                    return True, "nmcli", want_ip, ""
        except Exception:
            pass

    cmds = static_link_cmds(iface, cidr)
    try:
        all_ok = True
        for c in cmds:
            r = subprocess.run(c, capture_output=True, text=True, timeout=4.0)
            if r.returncode != 0:
                all_ok = False
                break
        if all_ok:
            return True, "ip_direct", want_ip, ""
    except Exception:
        pass

    pkexec_bin = shutil.which("pkexec")
    sh_bin = shutil.which("sh") or "/bin/sh"
    if pkexec_bin:
        try:
            shell_cmd = f"ip link set {iface} up && ip addr replace {cidr} dev {iface}"
            r_pk = subprocess.run(
                [pkexec_bin, sh_bin, "-c", shell_cmd],
                capture_output=True, text=True, timeout=30.0,
            )
            if r_pk.returncode == 0:
                return True, "pkexec", want_ip, ""
            return (
                False,
                "pkexec",
                "",
                (r_pk.stderr or r_pk.stdout or f"pkexec exited {r_pk.returncode}").strip(),
            )
        except Exception as exc:
            return False, "pkexec", "", repr(exc)

    return (
        False,
        "none",
        "",
        f"Could not configure {cidr} on {iface} (neither nmcli nor pkexec succeeded)",
    )

def configure_usb_interface_linux(
    iface: str,
    cidr: str = VENDOR_USB_P2P_PC_CIDR,
    addr_order: str = "dhcp,static-p2p",
) -> tuple[bool, str, str, str]:
    iface = (iface or "").strip()
    if not iface:
        return False, "none", "", "No USB interface name provided"
    eff_cidr = (cidr or "").strip() or VENDOR_USB_P2P_PC_CIDR
    steps = [s.strip().lower() for s in (addr_order or "dhcp,static-p2p").split(",") if s.strip()]
    if not steps:
        steps = ["dhcp", "static-p2p"]
    last_err = ""
    for step in steps:
        if step == "dhcp":
            ok, m, ip, err = _try_dhcp_client(iface)
            if ok and ip:
                return True, m, ip, ""
            if err:
                last_err = err
        elif step in ("link-local", "link_local", "ll"):
            ok, m, ip, err = _try_link_local(iface)
            if ok and ip:
                return True, m, ip, ""
        elif step in ("static-p2p", "static_p2p", "static"):
            ok, m, ip, err = _try_static_p2p(iface, eff_cidr)
            if ok and ip:
                return True, m, ip, ""
            if err:
                last_err = err
    return False, "none", "", last_err or f"USB address ladder ({addr_order}) exhausted on {iface}"
