from __future__ import annotations
from samyuj.core.identity import make_client_ssl_context as client_pinned_ssl_context
from samyuj.core.logging import sha256_b64
from samyuj.platform.linux.adb import adb_trigger_phone
from samyuj.session.registry import ServiceSessionRegistry
from samyuj.transport.discovery.mdns import WIFI_SVC_TYPE, dns_build_query as _dns_query
HAVE_MCL = False

import asyncio
import base64
import contextlib
import json
import os
import ssl
import time

from samyuj.core.identity import make_client_ssl_context as _simple_client_ctx
from samyuj.core.logging import ConnLog
from samyuj.session.models import new_session_state
from samyuj.transport.connection import set_tcp_nodelay as _set_tcp_nodelay
from samyuj.transport.discovery import (
    CHR_LONG,
    CHR_NOTIFY,
    CHR_WRITE,
    bootstrap_remote_client_fit,
    decode_gatt_frame,
    fragment_long_gatt,
    gatt_bootstrap_frame,
    is_usb_ip,
    make_nonce_id as _make_nonce_id,
    parse_r4_service_data,
    verify_nonce_id,
)
from samyuj.transport.websocket import WsFrameParser, ws_frame

class DiscoveryServiceMixin:

    def latest_phone_discovery(self) -> dict | None:
        valid = [d for d in self.discovered_phones if isinstance(d, dict) and int(d.get("port") or 0) > 0]
        paired = [d for d in valid if d.get("real_id")]
        pool = paired or valid
        if pool:
            cand = dict(pool[-1])
            ips = list(cand.get("ips") or [])
            if not self.usb_link:
                wifi_ips = [ip for ip in ips if not is_usb_ip(str(ip), usb_link=getattr(self, 'usb_link', None))]
                if not wifi_ips:
                    with contextlib.suppress(Exception):
                        for _k, e in self.load_paired_phones().items():
                            if isinstance(e, dict) and (e.get("last_wifi_ip") or (e.get("last_ip") and not is_usb_ip(str(e.get('last_ip')), usb_link=getattr(self, 'usb_link', None)))):
                                wifi_ips.append(str(e.get("last_wifi_ip") or e["last_ip"]))
                if wifi_ips:
                    cand["ips"] = wifi_ips
            return cand
        with contextlib.suppress(Exception):
            for k, e in self.load_paired_phones().items():
                if not isinstance(e, dict) or not e.get("last_ws_port"):
                    continue
                pref_ip = (
                    (e.get("last_usb_ip") or e.get("last_ip"))
                    if self.usb_link
                    else (e.get("last_wifi_ip") or e.get("last_ip"))
                )
                if pref_ip:
                    return {
                        "ips": [str(pref_ip)],
                        "port": int(e["last_ws_port"]),
                        "real_id": e.get("real_id") or k,
                        "deviceId": e.get("last_nonce") or k,
                    }
        return None

    def _invalidate_phone_ws_port(self, ip: str, port: int) -> None:
        with contextlib.suppress(Exception):
            for d_ent in list(getattr(self, "discovered_phones", []) or []):
                if isinstance(d_ent, dict) and int(d_ent.get("port") or 0) == int(port):
                    d_ent["port"] = 0
            if getattr(self, "mdns_adv", None) and hasattr(self.mdns_adv, "peers"):
                for _p in self.mdns_adv.peers.values():
                    if isinstance(_p, dict) and int(_p.get("port") or 0) == int(port):
                        _p.pop("port", None)
                        _p.pop("reported_sig", None)
            reg = self.load_paired_phones()
            changed = False
            for _k, e in reg.items():
                if isinstance(e, dict) and int(e.get("last_ws_port") or 0) == int(port):
                    e.pop("last_ws_port", None)
                    changed = True
            if changed:
                self._registry_path.write_text(json.dumps(reg, indent=1), encoding="utf-8")
                os.chmod(self._registry_path, 0o600)
            if getattr(self, "mdns_adv", None) and self.mdns_adv.ok:
                q = _dns_query(WIFI_SVC_TYPE.rstrip("."), 12)
                wip = self.mdns_adv._wifi_ip()
                uip = self.mdns_adv._usb_ip()
                if ip and ip not in ("127.0.0.1", ""):
                    self.mdns_adv._send(q, unicast_to=(ip, 5353), mcast_if_ip=uip if is_usb_ip(str(ip), usb_link=getattr(self, 'usb_link', None)) else wip)
                if wip and wip != "127.0.0.1":
                    self.mdns_adv._send(q, mcast_if_ip=wip)
                if uip:
                    self.mdns_adv._send(q, mcast_if_ip=uip)

    async def on_phone_mdns_discovered(self, entry: dict, for_usb: bool | None = None) -> None:
        live = self._live_ws()
        ips_in = list(entry.get("ips") or [])
        if ips_in:
            with contextlib.suppress(Exception):
                self.reselect_wifi_ip_for_peer(str(ips_in[0]))
        want_usb_switch = bool(
            self.usb_link
            and (
                for_usb is True
                or (
                    self.prefer_usb
                    and not getattr(self, "_usb_takeover_exhausted", False)
                    and (self._usb_takeover is not None or (live is not None and not self._is_usb_conn(live)))
                )
            )
        )
        want_wifi_switch = bool(
            live is not None
            and self._is_usb_conn(live)
            and (for_usb is False or any(not is_usb_ip(str(x), usb_link=getattr(self, 'usb_link', None)) for x in ips_in))
        )
        if (live is not None and not (want_usb_switch or want_wifi_switch)) or self._auto_connecting:
            return
        ips = list(entry.get("ips") or [])
        port = entry.get("port")
        if not ips or not port:
            return
        use_usb = want_usb_switch if for_usb is None else bool(for_usb)
        if self.usb_link and not getattr(self, "_usb_takeover_exhausted", False) and for_usb is not False:
            ph_usb = (self.tether or {}).get("phone_ip")
            if ph_usb and ph_usb not in ips:
                ips.insert(0, ph_usb)
            ips.sort(key=lambda x: 0 if is_usb_ip(str(x), usb_link=getattr(self, 'usb_link', None)) else 1)
            if for_usb is None:
                use_usb = True
        else:
            wifi_only = [x for x in ips if not is_usb_ip(str(x), usb_link=getattr(self, 'usb_link', None))]
            if not wifi_only:
                with contextlib.suppress(Exception):
                    for _k, e in self.load_paired_phones().items():
                        wip = e.get("last_wifi_ip") or e.get("last_ip") if isinstance(e, dict) else None
                        if wip and not is_usb_ip(str(wip), usb_link=getattr(self, 'usb_link', None)):
                            wifi_only.append(str(wip))
            if wifi_only:
                ips = wifi_only
            use_usb = False
        self._auto_connecting = True
        try:
            ok, msg = await self.wifi_tunnel_push(
                f"{ips[0]}:{port}",
                wait_dial=6.0 if use_usb else 10.0,
                for_usb=use_usb,
            )
            self.events.log("AUTO_WIFI_TUNNEL_RESULT", f"ok={ok} for_usb={use_usb} {msg}")
        finally:
            self._auto_connecting = False

    async def _auto_connect_background_loop(self) -> None:
        next_ble_mono = 0.0
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.sleep(2.0)
            while not (self._stop_evt and self._stop_evt.is_set()):
                try:
                    live = self._live_ws()
                    if (live is not None and self.usb_link and self.prefer_usb
                            and not self._is_usb_conn(live)
                            and self._usb_takeover is not None):
                        if self.mdns_adv and self.mdns_adv.ok:
                            self.mdns_adv.announce_now()
                        d_usb = self.latest_phone_discovery()
                        if d_usb and d_usb.get("port") and not self._auto_connecting:
                            ph_ip = (self.tether or {}).get("phone_ip")
                            await self._proactive_usb_switch_push(self.usb_link[1], ph_ip)
                    elif (live is None and not self.pairing_mode_active
                            and self.search_window_active()):
                        if self.usb_link:
                            if self.mdns_adv and self.mdns_adv.ok:
                                self.mdns_adv.announce_now()
                                with contextlib.suppress(Exception):
                                    self.mdns_adv._send(
                                        _dns_query(WIFI_SVC_TYPE.rstrip("."), 12),
                                        mcast_if_ip=self.usb_link[1])
                            for tc in list(self._conns):
                                if (not tc.get("closed") and tc.get("tunnel")
                                        and not tc.get("bootstrap_sent_auto")):
                                    tc["bootstrap_sent_auto"] = True
                                    await self._send_tunnel_bootstrap(tc, why="usb-auto")
                            if (not self._adb_trigger_done_for_link
                                    and self.moto_adb_serial):
                                self._adb_trigger_done_for_link = True
                                ok_adb, msg_adb = await adb_trigger_phone(self.moto_adb_serial)
                                self.events.log("USB_AUTO_ADB_TRIGGER",
                                                f"ok={ok_adb} {msg_adb}", feature="core")
                            d = self.latest_phone_discovery()
                            usb_age = time.monotonic() - float(getattr(self, "_usb_link_adopted_mono", 0.0) or 0.0)
                            if d and d.get("ips") and d.get("port") and not self._auto_connecting:
                                try_usb_boot = (usb_age < 6.0 and not getattr(self, "_usb_takeover_exhausted", False))
                                await self.on_phone_mdns_discovered(d, for_usb=try_usb_boot)
                        else:
                            d = self.latest_phone_discovery()
                            if d and d.get("ips") and d.get("port") and not self._auto_connecting:
                                await self.on_phone_mdns_discovered(d, for_usb=False)
                            elif d is None and self.mdns_adv and self.mdns_adv.ok:
                                with contextlib.suppress(Exception):
                                    wip = self.mdns_adv._wifi_ip()
                                    self.mdns_adv._send(
                                        _dns_query(WIFI_SVC_TYPE.rstrip("."), 12),
                                        mcast_if_ip=wip)
                            elif (self._live_ws() is None and not self._auto_connecting
                                    and time.monotonic() >= next_ble_mono):
                                next_ble_mono = time.monotonic() + 20.0
                                devs = await self.ble_scan_devices(window=4.0)
                                if devs and self._live_ws() is None:
                                    paired = [x for x in devs if x.get("paired_real_id")]
                                    targets = paired or devs
                                    for t in targets[:1]:
                                        if self._live_ws() is not None:
                                            break
                                        await self.ble_gatt_push(t["mac"], wait_dial=10.0)
                except Exception as exc:
                    self.events.log("AUTO_CONNECT_LOOP_ERR", repr(exc)[:200], feature="core")
                await asyncio.sleep(2.0)

    async def wifi_tunnel_push(self, target: str = "", wait_dial: float = 12.0,
                               for_usb: bool | None = None) -> tuple[bool, str]:
        ip, port, fp_b64 = "", 0, None
        if target and ":" in target:
            h_part, p_part = target.split(":", 1)
            ip, port = h_part.strip(), int(p_part.strip())
        else:
            d = self.latest_phone_discovery()
            if d:
                ips = d.get("ips") or []
                ip = ips[0] if ips else ""
                port = int(d.get("port") or 0)
        if not ip or not port:
            if self.mdns_adv and self.mdns_adv.ok:
                wip = self.mdns_adv._wifi_ip()
                self.mdns_adv._send(_dns_query(WIFI_SVC_TYPE.rstrip("."), 12), mcast_if_ip=wip)
            return False, "No phone Wi-Fi ws-GATT endpoint discovered yet — enter <phone-ip>:<wsPort> in the box or ensure phone & PC are on the same Wi-Fi"

        if self.tunnel_op3_evt is None:
            self.tunnel_op3_evt = asyncio.Event()
        key = self.consistent_shared_key()
        nonce = self.mdns_nonce or _make_nonce_id(self.device_id_b64)
        self.mdns_nonce = nonce
        use_usb_boot = bool(
            (ip and is_usb_ip(str(ip), usb_link=getattr(self, 'usb_link', None)))
            or (self.usb_link and not getattr(self, "_usb_takeover_exhausted", False))
        ) if for_usb is None else bool(for_usb)
        boot_ip = (self.usb_link[1] if (use_usb_boot and self.usb_link) else (self.wifi_ip if not use_usb_boot and getattr(self, "wifi_ip", "") not in ("", "127.0.0.1") else None))
        boot = self._build_bootstrap_json(for_usb=use_usb_boot, ip_override=boot_ip)

        for attempt in (1, 2):
            self.tunnel_op3_evt.clear()
            frame = gatt_bootstrap_frame(nonce, boot, key)
            if frame is None:
                self.events.log("BLE_ENCRYPT_FAILED", "refusing to send plaintext tunnel bootstrap")
                return False, "BLE bootstrap encryption failed"
            ctx = _simple_client_ctx()
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(ip, int(port), ssl=ctx), timeout=6.0
                )
                _set_tcp_nodelay(writer)
            except Exception as exc:
                self._invalidate_phone_ws_port(ip, int(port))
                return False, f"Cannot reach wss://{ip}:{port}/readyfor/mdns: {exc!r}"

            try:
                ws_k = base64.b64encode(os.urandom(16)).decode()
                writer.write((f"GET /readyfor/mdns HTTP/1.1\r\nHost: {ip}:{port}\r\n"
                              f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
                              f"Sec-WebSocket-Key: {ws_k}\r\nSec-WebSocket-Version: 13\r\n"
                              f"User-Agent: Samyuj-PC\r\n\r\n").encode())
                await writer.drain()
                head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5.0)
                if not head.startswith(b"HTTP/1.1 101"):
                    writer.close()
                    return False, f"Phone refused /readyfor/mdns upgrade: {head.splitlines()[0]!r}"

                writer.write(ws_frame(0x2, frame, mask=True))
                await writer.drain()
                self.events.log("WIFI_TUNNEL_PUSH_SENT",
                                f"attempt={attempt} wss://{ip}:{port}/readyfor/mdns op={frame[16]} len={len(frame)}B")

                parser = WsFrameParser()
                rest = head.split(b"\r\n\r\n", 1)[1] if b"\r\n\r\n" in head else b""
                if rest:
                    parser.buf.extend(rest)

                t0 = time.monotonic()
                op3_seen = False
                reader_eof = False
                while time.monotonic() - t0 < wait_dial:
                    live_c = self._live_ws()
                    usb_c = next(
                        (c for c in reversed(self._conns)
                         if c.get("ws") and not c.get("closed") and not c.get("tunnel") and self._is_usb_conn(c)),
                        None,
                    )
                    if (use_usb_boot and usb_c is not None) or (not use_usb_boot and live_c is not None):
                        writer.close()
                        return True, f"Phone connected ({'USB' if use_usb_boot else 'Wi-Fi'}) after {time.monotonic() - t0:.1f}s (no QR needed!)"
                    if self.tunnel_op3_evt.is_set():
                        op3_seen = True
                        break
                    for op, _fin, pl in (parser.feed(b"") if not reader_eof else ()):
                        if op == 0x2:
                            dec = decode_gatt_frame(pl, self.shared_keys())
                            if dec and dec.get("op") == 3:
                                op3_seen = True
                                break
                            elif dec and (dec.get("json") or {}).get("cmd") == 0:
                                writer.write(ws_frame(0x2, frame, mask=True))
                                await writer.drain()
                    if op3_seen:
                        break
                    try:
                        if reader_eof:
                            await asyncio.sleep(0.25)
                        else:
                            chunk = await asyncio.wait_for(reader.read(4096), timeout=0.25)
                            if chunk:
                                parser.buf.extend(chunk)
                            else:
                                reader_eof = True
                    except (asyncio.TimeoutError, TimeoutError):
                        pass
                writer.close()
                if op3_seen:
                    self.events.log("WIFI_TUNNEL_OP3_REPAIR",
                                    "Phone answered op3 (cannot decrypt) — dropping stale key and re-pushing plaintext op2")
                    self._forget_shared_key()
                    key = None
                    continue
                if self._live_ws() is not None:
                    return True, "Phone connected over Wi-Fi!"
                return True, f"Pushed {len(frame)}B GATT bootstrap (op={frame[16]}) to wss://{ip}:{port}/readyfor/mdns — ensure phone 'PC all' toggle is ON"
            except Exception as exc:
                with contextlib.suppress(Exception):
                    writer.close()
                return False, f"Wi-Fi tunnel push failed: {exc!r}"
        return False, "Wi-Fi tunnel push completed without phone dial-back"

    async def ble_scan_devices(self, window: float = 6.0) -> list[dict]:
        try:
            from bleak import BleakScanner
        except ImportError:
            self.events.log("BLE_NO_BLEAK", "bleak not installed (pip install bleak)")
            return []
        found: dict[str, dict] = {}
        known = self.phone_real_ids()

        def cb(dev, adv):
            sdata = getattr(adv, "service_data", None) or {}
            suuids = [str(u).lower() for u in (getattr(adv, "service_uuids", None) or [])]
            dname = getattr(dev, "name", None) or getattr(adv, "local_name", None) or ""
            rssi = getattr(adv, "rssi", None)
            for key, val in sdata.items():
                if "5246" not in str(key).lower():
                    continue
                info = parse_r4_service_data(bytes(val))
                if not info:
                    continue
                match = next((rid for rid in known if verify_nonce_id(info["nonce_b64"], rid)), None)
                found[dev.address] = {
                    "mac": dev.address,
                    "name": dname,
                    "rssi": rssi,
                    **info,
                    "paired_real_id": match,
                }
                return
            if any("5246" in u for u in suuids):
                found.setdefault(dev.address, {
                    "mac": dev.address,
                    "name": dname,
                    "rssi": rssi,
                    "type": 1,
                    "os": 1,
                    "cap": 0,
                    "nonce_b64": "",
                    "paired_real_id": None,
                })

        try:
            async with BleakScanner(detection_callback=cb):
                await asyncio.sleep(max(1.0, float(window)))
        except Exception as exc:
            self.events.log("BLE_SCAN_ERROR", repr(exc))
            return []
        out = list(found.values())
        self.ble_scanned_devices = out
        for d in out:
            tag = f"PAIRED({d['paired_real_id'][:8]}..)" if d.get("paired_real_id") else "unpaired"
            self.events.log("BLE_SCAN_FOUND",
                            f"{d['mac']} name={d.get('name')!r} rssi={d.get('rssi')} nonce={d.get('nonce_b64')} -> {tag}")
        return out

    async def ble_gatt_push(self, mac: str = "", wait_dial: float = 15.0) -> tuple[bool, str]:
        try:
            from bleak import BleakClient
        except ImportError:
            return False, "bleak not installed (pip install bleak); use Wi-Fi Tunnel Push or USB instead"

        if self.tunnel_op3_evt is None:
            self.tunnel_op3_evt = asyncio.Event()

        targets: list[str] = []
        if mac and ":" in mac:
            targets = [mac.strip()]
        else:
            devs = await self.ble_scan_devices(window=5.0)
            paired = [d["mac"] for d in devs if d.get("paired_real_id")]
            others = [d["mac"] for d in devs if not d.get("paired_real_id")]
            targets = paired + others

        if not targets:
            return False, "No 0x5246 BLE phone found in scan — keep the phone screen UNLOCKED with Smart Connect open, or pass BLE MAC directly"

        key = self.consistent_shared_key()
        nonce = self.mdns_nonce or _make_nonce_id(self.device_id_b64)
        self.mdns_nonce = nonce
        last_err = ""

        for target_mac in targets:
            for attempt in (1, 2):
                self.tunnel_op3_evt.clear()
                try:
                    async with BleakClient(target_mac, timeout=15.0) as client:
                        mtu = 23
                        with contextlib.suppress(Exception):
                            mtu = int(getattr(client, "mtu_size", 23) or 23)
                        svc = None
                        for _ in range(4):
                            svc = next((s for s in client.services if "5246" in s.uuid.lower()), None)
                            if svc:
                                break
                            await asyncio.sleep(0.8)
                        if svc is None:
                            last_err = (f"{target_mac}: service 0x5246 not exposed — unlock phone screen "
                                        f"and open Smart Connect (the phone only exposes this GATT "
                                        f"service while its app is in the foreground)")
                            self.events.log("BLE_PUSH_NOSVC", last_err)
                            break

                        wchr = next((c for c in svc.characteristics
                                     if c.uuid.lower() == CHR_WRITE and "write" in c.properties), None)
                        lchr = next((c for c in svc.characteristics
                                     if c.uuid.lower() == CHR_LONG and "write" in c.properties), None)
                        nchr = next((c for c in svc.characteristics
                                     if c.uuid.lower() == CHR_NOTIFY), None)
                        if wchr is None and lchr is None:
                            last_err = f"{target_mac}: no writable 0x0722 or 0x0728 characteristic"
                            break

                        if nchr is not None:
                            def _on_notify(_c, data: bytearray) -> None:
                                dec = decode_gatt_frame(bytes(data), self.shared_keys())
                                self.events.log("BLE_GATT_NOTIFY", f"from {target_mac}: {dec}")
                                if dec and dec.get("op") == 3 and self.tunnel_op3_evt:
                                    self.tunnel_op3_evt.set()
                            with contextlib.suppress(Exception):
                                await client.start_notify(nchr, _on_notify)

                        eff_mtu = mtu if mtu >= 23 else 23
                        payload_cap = 15 * (eff_mtu - 7)
                        overhead = 17 + (28 if key else 0)
                        js = bootstrap_remote_client_fit(self, payload_cap - overhead)
                        frame = gatt_bootstrap_frame(nonce, js, key)
                        if frame is None:
                            self.events.log("BLE_ENCRYPT_FAILED", f"refusing plaintext GATT fallback for {mac}")
                            return False, "BLE GATT bootstrap encryption failed"
                        if len(frame) > payload_cap:
                            excess = len(frame) - payload_cap
                            shrunk_budget = max(64, (payload_cap - overhead) - excess - 8)
                            js = bootstrap_remote_client_fit(self, shrunk_budget)
                            frame = gatt_bootstrap_frame(nonce, js, key)
                            if frame is None:
                                self.events.log("BLE_ENCRYPT_FAILED", f"refusing plaintext GATT fallback for {mac}")
                                return False, "BLE GATT bootstrap encryption failed"

                        if len(frame) <= max(eff_mtu - 3, 20) and wchr is not None:
                            await client.write_gatt_char(wchr, bytearray(frame), response=True)
                            write_mode = f"0x0722 single ({len(frame)}B)"
                        elif lchr is not None:
                            frags = fragment_long_gatt(frame, eff_mtu, os.urandom(1)[0])
                            for fr in frags:
                                await client.write_gatt_char(lchr, bytearray(fr), response=True)
                                await asyncio.sleep(0.05)
                            write_mode = f"0x0728 fragmented ({len(frame)}B in {len(frags)} chunks @ MTU {eff_mtu})"
                        else:
                            await client.write_gatt_char(wchr, bytearray(frame), response=True)
                            write_mode = f"0x0722 fallback ({len(frame)}B)"

                        self.events.log("BLE_PUSH_WRITTEN", f"{target_mac} {write_mode} op={frame[16]}")
                        t0 = time.monotonic()
                        op3_seen = False
                        while time.monotonic() - t0 < wait_dial:
                            if self._live_ws() is not None:
                                return True, f"Phone connected via BLE GATT ({target_mac}, {write_mode}) after {time.monotonic() - t0:.1f}s!"
                            if self.tunnel_op3_evt.is_set():
                                op3_seen = True
                                break
                            await asyncio.sleep(0.25)
                        if op3_seen:
                            self.events.log("BLE_OP3_REPAIR", f"{target_mac} answered op3 — dropping key & retrying with plaintext op2")
                            self._forget_shared_key()
                            key = None
                            continue
                        return True, f"Wrote GATT bootstrap to {target_mac} via {write_mode} — waiting for phone dial"
                except Exception as exc:
                    last_err = f"{target_mac} attempt {attempt}: {exc!r}"
                    self.events.log("BLE_PUSH_FAIL", last_err)
                    await asyncio.sleep(1.0)
        return False, f"BLE GATT push failed: {last_err or 'unreachable'}"

    async def dial(self, hostport: str, path: str = "/readyfor/",
                   expect_fp_b64: str | None = None, send_hello: str | None = None) -> bool:
        host, _, port_s = hostport.partition(":")
        port = int(port_s)
        conn_id = self._next_conn_id
        self._next_conn_id += 1
        self.events.log("DIAL_START", f"conn{conn_id:03d} -> {hostport} path={path} "
                                      f"expectFp={expect_fp_b64}")
        try:
            ctx = client_pinned_ssl_context() if HAVE_MCL else _simple_client_ctx()
            reader, writer = await asyncio.open_connection(host, port, ssl=ctx,
                                                           server_hostname=host)
            _set_tcp_nodelay(writer)
        except OSError as exc:
            self.events.log("DIAL_FAIL", f"conn{conn_id:03d} {exc!r}")
            return False
        ssl_obj = writer.get_extra_info("ssl_object")
        conn = {
            "id": conn_id, "reader": reader, "writer": writer, "remote": f"dial:{hostport}",
            "mode": "dial-resp", "ws": False, "bytes_in": 0, "bytes_out": 0,
            "role": "dialback", "readiness": "connecting", "tunnel": False,
            "transition_lock": asyncio.Lock(),
            "log": ConnLog(self.out_dir / "connections" / f"conn_{conn_id:03d}.log",
                           log_secrets=getattr(self, "log_secrets", False)),
            "parser": WsFrameParser(), "http_buf": bytearray(), "closed": False,
            "hello": None, "replied": False, "pongs": 0, "tasks": [],
            "dial_hello": send_hello,
            "session": new_session_state(),
            "sessions": ServiceSessionRegistry(),
        }
        self._conns.append(conn)
        if ssl_obj is not None:
            peer_cert = ssl_obj.getpeercert(binary_form=True)
            got = sha256_b64(peer_cert) if peer_cert else ""
            match = "MATCH" if expect_fp_b64 and got == expect_fp_b64 else (
                "MISMATCH" if expect_fp_b64 else "UNPINNED"
            )
            conn["log"].line(f"DIALED peer cert: sha256b64={got} pin={match}")
            self.events.log("DIAL_PEER_CERT", f"conn{conn_id:03d} {got} pin={match} "
                                              f"(U13: phone listener cert)")
            if expect_fp_b64 and got != expect_fp_b64:
                self.events.log("DIAL_PIN_REJECT", f"conn{conn_id:03d} expected={expect_fp_b64} got={got}")
                conn["closed"] = True
                with contextlib.suppress(Exception):
                    writer.close()
                return False
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
               "Sec-WebSocket-Version: 13\r\n\r\n")
        conn["log"].line("WS UPGRADE REQUEST <<<\n" + req)
        await self._send_raw(conn, req.encode())
        self.events.log("DIAL_UPGRADE_SENT", f"conn{conn_id:03d} path={path}")
        asyncio.create_task(self._dial_read_loop(conn))
        return True

    async def _dial_read_loop(self, conn: dict) -> None:
        try:
            while not conn["closed"]:
                data = await conn["reader"].read(65536)
                if not data:
                    conn["log"].line("EOF from dialed peer")
                    self.events.log("DIAL_EOF", f"conn{conn['id']:03d}")
                    break
                conn["bytes_in"] += len(data)
                if conn["mode"] == "dial-resp":
                    conn["http_buf"].extend(data)
                    if b"\r\n\r\n" in conn["http_buf"]:
                        buf = bytes(conn["http_buf"])
                        idx = buf.find(b"\r\n\r\n")
                        head = buf[:idx].decode("utf-8", errors="replace")
                        conn["log"].line("HTTP RESPONSE >>>\n" + head)
                        self.events.log("DIAL_HTTP_RESPONSE", head.split("\r\n")[0])
                        rest = buf[idx + 4:]
                        if head.startswith(("HTTP/1.1 101", "HTTP/1.0 101")):
                            conn["ws"] = True
                            conn["mode"] = "ws"
                            self.events.log("WS_UPGRADE_DIAL", f"conn{conn['id']:03d} — "
                                            "phone accepted reverse WS (U13 resolved)")
                            if conn.get("dial_hello"):
                                await self._send_ws_text(conn, conn["dial_hello"],
                                                         "DIALBACK_HELLO")
                        else:
                            conn["mode"] = "raw"
                        conn["http_buf"] = bytearray()
                        if rest:
                            if conn["mode"] == "ws":
                                await self._handle_ws_frames(conn, rest)
                            else:
                                conn["log"].bytes("RAW-RECV", rest)
                elif conn["mode"] == "ws":
                    await self._handle_ws_frames(conn, data)
                else:
                    conn["log"].bytes("RAW-RECV", data)
        except (ConnectionError, ssl.SSLError, OSError) as exc:
            conn["log"].line(f"DIAL ERROR: {exc!r}")
            self.events.log("DIAL_ERROR", f"conn{conn['id']:03d} {exc!r}")
        finally:
            conn["closed"] = True

    def _live_ws(self) -> dict | None:
        candidates = [
            c for c in reversed(self._conns)
            if c.get("ws") and not c.get("closed")
            and not c.get("tunnel") and c.get("role") != "tunnel"
        ]
        if not candidates:
            return None
        for c in candidates:
            if (c.get("desktop_started") or c.get("as_session_id") is not None
                    or c.get("as_setup_seen") or c.get("wc_started") or c.get("ss_started")):
                return c
        for target_readiness in ("paired", "hello_verified", "ws_open"):
            for c in candidates:
                if c.get("readiness") == target_readiness:
                    return c
        return candidates[0]

