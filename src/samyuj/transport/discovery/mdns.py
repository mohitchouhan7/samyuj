from __future__ import annotations

import asyncio
import contextlib
import re
import socket
import time
from typing import Any

from .ble import make_nonce_id, verify_nonce_id
from .usb import is_usb_ip

MDNS_ADDR = "224.0.0.251"
MDNS_PORT = 5353
USB_SVC_TYPE = "_readyfor._tcp.local."
WIFI_SVC_TYPE = "_readyformdns._tcp.local."

def dns_encode_name(name: str) -> bytes:
    out = b""
    for label in name.rstrip(".").split("."):
        lb = label.encode("utf-8")
        out += bytes([len(lb)]) + lb
    return out + b"\x00"

def dns_parse_name(data: bytes, off: int) -> tuple[str, int]:
    labels, jumped, end, hops = [], False, off, 0
    n_len = len(data)
    while True:
        if off < 0 or off >= n_len or hops > 64:
            raise ValueError("Invalid or cyclic DNS name offset")
        hops += 1
        ln = data[off]
        if ln == 0:
            off += 1
            break
        if ln & 0xC0 == 0xC0:
            if off + 2 > n_len:
                raise ValueError("Truncated DNS compression pointer")
            ptr = int.from_bytes(data[off : off + 2], "big") & 0x3FFF
            if ptr >= n_len or ptr == off:
                raise ValueError("Out-of-bounds or self-referencing DNS pointer")
            if not jumped:
                end = off + 2
            off, jumped = ptr, True
            continue
        if off + 1 + ln > n_len:
            raise ValueError("Truncated DNS label")
        labels.append(data[off + 1 : off + 1 + ln].decode("utf-8", "replace"))
        off += 1 + ln
    if not jumped:
        end = off
    return ".".join(labels), end

def dns_rr(name: str, rtype: int, ttl: int, rdata: bytes, flush: bool = True) -> bytes:
    cls = 0x0001 | (0x8000 if flush else 0)
    return (
        dns_encode_name(name)
        + rtype.to_bytes(2, "big")
        + cls.to_bytes(2, "big")
        + ttl.to_bytes(4, "big")
        + len(rdata).to_bytes(2, "big")
        + rdata
    )

def dns_txt_rdata(pairs: dict[str, Any]) -> bytes:
    out = b""
    for k, v in pairs.items():
        s = f"{k}={v}".encode("utf-8")
        while len(s) > 255:
            out += bytes([255]) + s[:255]
            s = s[255:]
        out += bytes([len(s)]) + s
    return out or b"\x00"

def dns_query(qname: str, qtype: int = 12) -> bytes:
    head = (
        (0).to_bytes(2, "big")
        + (0).to_bytes(2, "big")
        + (1).to_bytes(2, "big")
        + (0).to_bytes(2, "big")
        + (0).to_bytes(2, "big")
        + (0).to_bytes(2, "big")
    )
    return head + dns_encode_name(qname) + qtype.to_bytes(2, "big") + (1).to_bytes(2, "big")

def dns_parse_records(data: bytes) -> list[dict[str, Any]]:
    recs: list[dict[str, Any]] = []
    try:
        if len(data) < 12:
            return recs
        qd = min(256, int.from_bytes(data[4:6], "big"))
        n = min(
            512,
            int.from_bytes(data[6:8], "big")
            + int.from_bytes(data[8:10], "big")
            + int.from_bytes(data[10:12], "big"),
        )
        off = 12
        for _ in range(qd):
            _n, off = dns_parse_name(data, off)
            off += 4
        for _ in range(n):
            name, off = dns_parse_name(data, off)
            rtype = int.from_bytes(data[off : off + 2], "big")
            off += 8
            rdlen = int.from_bytes(data[off : off + 2], "big")
            off += 2
            rdata = data[off : off + rdlen]
            off += rdlen
            rec: dict[str, Any] = {
                "name": name.rstrip(".").lower(),
                "type": rtype,
                "ttl": int.from_bytes(data[off - rdlen - 6 : off - rdlen - 2], "big"),
            }
            if rtype == 12:
                tgt, _ = dns_parse_name(data, off - rdlen)
                rec["ptr"] = tgt.rstrip(".").lower()
            elif rtype == 33 and rdlen >= 7:
                rec["port"] = int.from_bytes(rdata[4:6], "big")
                tgt, _ = dns_parse_name(data, off - rdlen + 6)
                rec["target"] = tgt.rstrip(".").lower()
            elif rtype == 16:
                strs, i2 = [], 0
                while i2 < len(rdata):
                    ln = rdata[i2]
                    strs.append(rdata[i2 + 1 : i2 + 1 + ln].decode("utf-8", "replace"))
                    i2 += 1 + ln
                rec["txt"] = strs
            elif rtype == 1 and rdlen == 4:
                rec["a"] = ".".join(str(b) for b in rdata)
            recs.append(rec)
    except Exception:
        pass
    return recs

class UsbMdnsAdvertiser:

    def __init__(self, runtime: Any) -> None:
        self.h = runtime
        self.sock: socket.socket | None = None
        self._tasks: list[asyncio.Task[Any]] = []
        self.instance = ""
        self.hostname = ""
        self.nonce_id = ""
        self.ok = False
        self.peers: dict[str, dict[str, Any]] = {}
        self._notified: set[str] = set()
        self.answered_at: dict[str, float] = {}
        self._resolve_logged: set[str] = set()
        self._joined_ips: set[str] = set()

    def _usb_ip(self) -> str:
        if getattr(self.h, "usb_link", None):
            return self.h.usb_link[1]
        return ""

    def _wifi_ip(self) -> str:
        wip = getattr(self.h, "wifi_ip", "") or ""
        if wip and wip not in ("127.0.0.1", ""):
            return wip
        lip = getattr(self.h, "lan_ip", "") or ""
        return lip if lip not in ("127.0.0.1", "") else "127.0.0.1"

    def _records_for_mode(
        self,
        mode: str,
        ttl_ptr_txt: int = 4500,
        ttl_srv_a: int = 120,
    ) -> list[bytes]:
        h = self.h
        srv_rdata = (
            (0).to_bytes(2, "big")
            + (0).to_bytes(2, "big")
            + h.port.to_bytes(2, "big")
            + dns_encode_name(self.hostname)
        )
        fp_hex = (getattr(h.identity, "fingerprint", "") or "").lower()
        if mode == "usb":
            ip_str = self._usb_ip() or self._wifi_ip()
            svc = USB_SVC_TYPE
            fp_val = fp_hex or h.fp_b64
        else:
            ip_str = self._wifi_ip()
            svc = WIFI_SVC_TYPE
            fp_val = h.fp_b64 or fp_hex
        inst = f"{self.instance}.{svc}"
        txt = dns_txt_rdata(
            {
                "deviceName": h.device_name,
                "deviceId": self.nonce_id,
                "cap": str(h.pc_cap),
                "type": "4",
                "os": "6",
                "timestamp": str(int(time.time() * 1000)),
                "fp": fp_val,
                "appVer": h.app_ver,
                "verCode": str(h.ver_code),
                "verName": h.app_ver,
                "scType": "2",
            }
        )
        a_rdata = socket.inet_aton(ip_str if ip_str and ip_str != "" else "127.0.0.1")
        return [
            dns_rr(svc, 12, ttl_ptr_txt, dns_encode_name(inst), flush=False),
            dns_rr(inst, 33, ttl_srv_a, srv_rdata),
            dns_rr(inst, 16, ttl_ptr_txt, txt),
            dns_rr(self.hostname, 1, ttl_srv_a, a_rdata),
        ]

    def _packet_for_mode(
        self,
        mode: str,
        questions: list[tuple[str, int]] | None = None,
    ) -> bytes:
        recs = self._records_for_mode(mode)
        head = (
            (0).to_bytes(2, "big")
            + (0x8400).to_bytes(2, "big")
            + (0).to_bytes(2, "big")
            + len(recs).to_bytes(2, "big")
            + (0).to_bytes(2, "big")
            + (0).to_bytes(2, "big")
        )
        return head + b"".join(recs)

    def _packet(self, questions: list[tuple[str, int]] | None = None) -> bytes:
        return self._packet_for_mode("usb" if self._usb_ip() else "wifi", questions)

    def ensure_membership(self, ip: str) -> None:
        if not self.sock or not ip or ip in ("127.0.0.1", "") or ip in self._joined_ips:
            return
        with contextlib.suppress(OSError):
            self.sock.setsockopt(
                socket.IPPROTO_IP,
                socket.IP_ADD_MEMBERSHIP,
                socket.inet_aton(MDNS_ADDR) + socket.inet_aton(ip),
            )
            self._joined_ips.add(ip)

    def _send(
        self,
        data: bytes,
        unicast_to: tuple[str, int] | None = None,
        mcast_if_ip: str | None = None,
    ) -> None:
        if not self.sock:
            return
        if mcast_if_ip and mcast_if_ip not in ("127.0.0.1", ""):
            self.ensure_membership(mcast_if_ip)
            with contextlib.suppress(OSError):
                self.sock.setsockopt(
                    socket.IPPROTO_IP,
                    socket.IP_MULTICAST_IF,
                    socket.inet_aton(mcast_if_ip),
                )
        with contextlib.suppress(OSError):
            self.sock.sendto(data, unicast_to or (MDNS_ADDR, MDNS_PORT))

    async def start(self) -> bool:
        h = self.h
        self.nonce_id = getattr(h, "mdns_nonce", "") or make_nonce_id(h.device_id_b64)
        h.mdns_nonce = self.nonce_id
        base = re.sub(r"[^A-Za-z0-9-]", "-", h.device_name)[:40].strip("-") or "samyuj-pc"
        self.instance = base
        self.hostname = base.lower() + "-pc.local."
        s = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            with contextlib.suppress(AttributeError, OSError):
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            s.bind(("", MDNS_PORT))
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
            s.setblocking(False)
            self.sock = s
            for ip in (self._wifi_ip(), self._usb_ip()):
                self.ensure_membership(ip)
        except OSError as exc:
            if s is not None:
                with contextlib.suppress(OSError):
                    s.close()
            h.events.log("MDNS_FAIL", f"cannot bind :5353 ({exc!r})")
            return False
        self.ok = True
        loop = asyncio.get_running_loop()
        loop.add_reader(s.fileno(), self._on_readable)
        self._tasks.append(asyncio.create_task(self._announce_loop()))
        self._tasks.append(asyncio.create_task(self._query_loop()))
        self._tasks.append(asyncio.create_task(self._resolve_watch()))
        h.events.log(
            "MDNS_UP",
            f"_readyfor._tcp + _readyformdns._tcp instance={self.instance!r} "
            f"port={h.port} wifi_ip={self._wifi_ip()} usb_ip={self._usb_ip() or 'standby'}",
        )
        return True

    def announce_now(self) -> None:
        wip = self._wifi_ip()
        uip = self._usb_ip()
        if wip and wip != "127.0.0.1":
            self._send(self._packet_for_mode("wifi", None), mcast_if_ip=wip)
        if uip:
            self._send(self._packet_for_mode("usb", None), mcast_if_ip=uip)

    async def _announce_loop(self) -> None:
        for d in (0.0, 1.0, 2.0, 4.0):
            if d:
                await asyncio.sleep(d)
            self.announce_now()
        while True:
            await asyncio.sleep(5.0)
            self.announce_now()

    async def _query_loop(self) -> None:
        q = dns_query(WIFI_SVC_TYPE.rstrip("."), 12)
        for d in (0.2, 2.0, 5.0):
            await asyncio.sleep(d)
            wip = self._wifi_ip()
            if wip and wip != "127.0.0.1":
                self._send(q, mcast_if_ip=wip)
        while True:
            await asyncio.sleep(12.0)
            wip = self._wifi_ip()
            if wip and wip != "127.0.0.1":
                self._send(q, mcast_if_ip=wip)

    async def _resolve_watch(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            now = time.monotonic()
            for ip, t_last in list(self.answered_at.items()):
                if now - t_last >= 4.0 and ip not in self._resolve_logged:
                    self._resolve_logged.add(ip)
                    self.h.phone_resolved_us = True
                    self.h.events.log(
                        "PHONE_MDNS_RESOLVED",
                        f"{ip} answered-then-quiet >=4s — phone cached our mDNS records",
                    )

    def _on_readable(self) -> None:
        if not self.sock:
            return
        try:
            data, addr = self.sock.recvfrom(4096)
        except OSError:
            return
        if len(data) < 12:
            return
        if not ((data[2] >> 7) & 1):
            self._on_query(data, addr)
            n_rr = (
                int.from_bytes(data[6:8], "big")
                + int.from_bytes(data[8:10], "big")
                + int.from_bytes(data[10:12], "big")
            )
            if n_rr > 0:
                self._on_response(data, addr)
        else:
            self._on_response(data, addr)

    def _on_query(self, data: bytes, addr: tuple[str, int]) -> None:
        local_ips = {self._wifi_ip(), self._usb_ip(), getattr(self.h, "lan_ip", "")}
        if addr[0] in local_ips:
            return
        qd = min(64, int.from_bytes(data[4:6], "big"))
        questions, off = [], 12
        wanted_usb = {
            USB_SVC_TYPE.rstrip(".").lower(),
            f"{self.instance}.{USB_SVC_TYPE}".rstrip(".").lower(),
        }
        wanted_wifi = {
            WIFI_SVC_TYPE.rstrip(".").lower(),
            f"{self.instance}.{WIFI_SVC_TYPE}".rstrip(".").lower(),
        }
        wanted_host = self.hostname.rstrip(".").lower()
        hit_usb = False
        hit_wifi = False
        try:
            for _ in range(qd):
                if off >= len(data):
                    break
                name, off = dns_parse_name(data, off)
                if off + 4 > len(data):
                    break
                qtype = int.from_bytes(data[off : off + 2], "big")
                off += 4
                questions.append((name, qtype))
                nm = name.rstrip(".").lower()
                if nm in wanted_usb:
                    hit_usb = True
                elif nm in wanted_wifi:
                    hit_wifi = True
                elif nm == wanted_host:
                    uip = self._usb_ip()
                    if uip and addr[0].rsplit(".", 1)[0] == uip.rsplit(".", 1)[0]:
                        hit_usb = True
                    else:
                        hit_wifi = True
        except Exception:
            return
        if not (hit_usb or hit_wifi):
            return
        uip = self._usb_ip()
        wip = self._wifi_ip()
        is_from_usb_subnet = bool(
            (uip and addr[0].rsplit(".", 1)[0] == uip.rsplit(".", 1)[0])
            or is_usb_ip(addr[0], usb_link=getattr(self.h, 'usb_link', None))
        )
        q_wifi_ptr = dns_query(WIFI_SVC_TYPE.rstrip("."), 12)
        if hit_usb or is_from_usb_subnet:
            pkt = self._packet_for_mode("usb", None)
            self._send(pkt, unicast_to=addr, mcast_if_ip=uip or wip)
            if uip:
                self._send(pkt, mcast_if_ip=uip)
            self._send(q_wifi_ptr, unicast_to=addr, mcast_if_ip=uip or wip)
            if uip:
                self._send(q_wifi_ptr, mcast_if_ip=uip)
            if len(self.answered_at) > 256:
                self.answered_at.clear()
            self.answered_at[addr[0]] = time.monotonic()
            self.h.phone_resolved_us = True
            if getattr(self.h, "tether", None) and addr[0] != uip:
                self.h.tether["phone_ip"] = addr[0]
            self.h.events.log(
                "MDNS_QUERY_ANSWERED(USB)",
                f"from {addr} q={[q[0] for q in questions]} A={uip or wip}",
            )
        if hit_wifi and not is_from_usb_subnet:
            pkt = self._packet_for_mode("wifi", None)
            self._send(pkt, unicast_to=addr, mcast_if_ip=wip)
            if wip and wip != "127.0.0.1":
                self._send(pkt, mcast_if_ip=wip)
            self._send(q_wifi_ptr, unicast_to=addr, mcast_if_ip=wip)
            if wip and wip != "127.0.0.1":
                self._send(q_wifi_ptr, mcast_if_ip=wip)
            self.answered_at[addr[0]] = time.monotonic()
            self.h.events.log(
                "MDNS_QUERY_ANSWERED(WIFI)",
                f"from {addr} q={[q[0] for q in questions]} A={wip}",
            )

    def _on_response(self, data: bytes, addr: tuple[str, int]) -> None:
        my_inst_wifi = f"{self.instance}.{WIFI_SVC_TYPE}".rstrip(".").lower()
        my_inst_usb = f"{self.instance}.{USB_SVC_TYPE}".rstrip(".").lower()
        my_host = self.hostname.rstrip(".").lower()
        changed = False
        for rec in dns_parse_records(data):
            if rec.get("ttl") == 0:
                name_gb = rec.get("name", "")
                if name_gb in self.peers:
                    self.peers[name_gb].pop("port", None)
                    self.peers[name_gb].pop("reported_sig", None)
                continue
            name = rec["name"]
            if name in (my_inst_wifi, my_inst_usb, my_host):
                continue
            if rec["type"] == 12 and rec.get("ptr"):
                continue
            if not name.endswith(WIFI_SVC_TYPE.rstrip(".")):
                if rec["type"] == 1 and rec.get("a"):
                    for _inst, peer in self.peers.items():
                        if peer.get("srv_host") == name:
                            peer.setdefault("ips", set()).add(rec["a"])
                            changed = True
                continue
            if len(self.peers) >= 256 and name not in self.peers:
                self.peers.pop(next(iter(self.peers)), None)
            peer = self.peers.setdefault(name, {})
            if addr and addr[0] not in (self._wifi_ip(), self._usb_ip(), "127.0.0.1"):
                peer.setdefault("ips", set()).add(addr[0])
            if rec["type"] == 33:
                peer["port"] = rec.get("port")
                peer["srv_host"] = rec.get("target")
                changed = True
            elif rec["type"] == 16 and rec.get("txt") is not None:
                kv = {}
                for s in rec["txt"]:
                    if "=" in s:
                        k, v = s.split("=", 1)
                        kv[k] = v
                peer["txt"] = kv
                changed = True
        if not changed:
            return
        for inst, peer in list(self.peers.items()):
            self._maybe_notify(inst, peer)

    def _maybe_notify(self, inst: str, peer: dict[str, Any]) -> None:
        txt = peer.get("txt") or {}
        dev = txt.get("deviceId")
        if not dev or not peer.get("port"):
            return
        cur_ips = tuple(sorted(peer.get("ips") or []))
        sig = (dev, cur_ips, peer.get("port"))
        if cur_ips and peer.get("reported_sig") == sig:
            return
        if not cur_ips and peer.get("reported") == dev:
            return
        if cur_ips:
            self._notify(inst, peer, txt, dev)
        elif not peer.get("resolving"):
            peer["resolving"] = True
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._resolve_and_notify(inst, peer, txt, dev))
            except RuntimeError:
                self._notify(inst, peer, txt, dev)

    async def _resolve_and_notify(
        self,
        inst: str,
        peer: dict[str, Any],
        txt: dict[str, str],
        dev: str,
    ) -> None:
        host = peer.get("srv_host")
        wip = self._wifi_ip()
        uip = self._usb_ip()
        for _ in range(4):
            if host and wip and wip != "127.0.0.1":
                self._send(dns_query(host, 1), mcast_if_ip=wip)
            if host and uip and uip != "127.0.0.1":
                self._send(dns_query(host, 1), mcast_if_ip=uip)
            await asyncio.sleep(0.6)
            if peer.get("ips"):
                break
        peer.pop("resolving", None)
        cur_ips = tuple(sorted(peer.get("ips") or []))
        sig = (dev, cur_ips, peer.get("port"))
        if peer.get("reported_sig") != sig:
            self._notify(inst, peer, txt, dev)

    def _notify(
        self,
        inst: str,
        peer: dict[str, Any],
        txt: dict[str, str],
        dev: str,
    ) -> None:
        h = self.h
        peer["reported"] = dev
        ips = sorted(peer.get("ips") or [])
        peer["reported_sig"] = (dev, tuple(ips), peer.get("port"))
        known_real = h.phone_real_ids() if hasattr(h, "phone_real_ids") else []
        paired_with = next((rid for rid in known_real if verify_nonce_id(dev, rid)), None)
        entry = {
            "ips": ips,
            "port": peer.get("port"),
            "fp": txt.get("fp"),
            "deviceId": dev,
            "real_id": paired_with,
            "when": time.time(),
        }
        if paired_with and peer.get("port"):
            with contextlib.suppress(Exception):
                h.note_phone(
                    real_id=paired_with,
                    nonce_id=dev,
                    ip=ips[0] if ips else None,
                    ws_port=int(peer.get("port")),
                )
        h.discovered_phones = [d for d in h.discovered_phones if d.get("deviceId") != dev]
        h.discovered_phones.append(entry)
        h.events.log(
            "PHONE_MDNS_DISCOVERED",
            f"instance={inst.split('.')[0]!r} ips={ips} wsPort={peer.get('port')} "
            f"deviceId={dev} paired={bool(paired_with)}",
        )
        live = h._live_ws()
        has_wifi_ip = any(not is_usb_ip(str(x), usb_link=getattr(h, 'usb_link', None)) for x in ips)
        has_usb_ip = any(is_usb_ip(str(x), usb_link=getattr(h, 'usb_link', None)) for x in ips) or bool(getattr(h, 'usb_link', None))
        want_usb_switch = bool(
            has_usb_ip
            and getattr(h, "usb_link", None)
            and (
                getattr(h, "_usb_takeover", None) is not None
                or (live is not None and not h._is_usb_conn(live))
            )
        )
        want_wifi_switch = bool(
            has_wifi_ip
            and live is not None
            and h._is_usb_conn(live)
        )
        if ips and peer.get("port") and (live is None or want_usb_switch or want_wifi_switch):
            for_usb_hint = True if want_usb_switch else (False if want_wifi_switch else None)
            with contextlib.suppress(RuntimeError):
                asyncio.get_running_loop().create_task(
                    h.on_phone_mdns_discovered(entry, for_usb=for_usb_hint)
                )

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        if self.sock:
            with contextlib.suppress(Exception):
                asyncio.get_running_loop().remove_reader(self.sock.fileno())
                self.sock.close()
            self.sock = None
        self.ok = False

def dns_build_query(qname: str, qtype: int = 12, unicast_response: bool = False) -> bytes:
    import struct
    qclass = 0x8001 if unicast_response else 0x0001
    return struct.pack(">HHHHHH", 0, 0, 1, 0, 0, 0) + dns_encode_name(qname) + struct.pack(">HH", qtype, qclass)

_dns_query = dns_build_query
