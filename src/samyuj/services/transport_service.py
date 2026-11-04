from __future__ import annotations
import re
from samyuj.session.models import new_session_state
from samyuj.transport.network import (
    pick_local_ip_for_peer,
    pick_startup_ip,
    wifi_ipv4s,
)
from samyuj.transport.discovery import USB_IFACE_RE, UsbMdnsAdvertiser, WIFI_SVC_TYPE
from samyuj.transport.discovery.mdns import dns_build_query as _dns_query

import asyncio
import contextlib
import time

from samyuj.platform.linux.adb import adb_trigger_phone, probe_phone_ip
from samyuj.protocol.constants import VENDOR_USB_P2P_PC_CIDR
from samyuj.transport.discovery import (
    READYFOR_PIDS,
    find_tether_link,
    net_snapshot,
    usb_device_snapshot,
)
from samyuj.transport.discovery.usb import (
    ANDROID_PHONE_VENDORS,
    NEVER_PHONE_VENDORS,
    is_usb_ip,
    usb_candidate_matches_names,
    usb_phone_candidates,
)
from samyuj.transport.discovery.uevents import UeventWatcher

class TransportServiceMixin:

    _search_until: float = 0.0
    _search_secs: float = 10.0
    _search_reason: str = ""
    _window_logged: bool = False
    _uevent_watcher = None

    def search_window_active(self) -> bool:
        return time.monotonic() < float(getattr(self, "_search_until", 0.0))

    def _open_search_window(self, reason: str, secs: float = 10.0) -> None:
        if reason != self._search_reason:
            self._window_logged = False
        self._search_reason = reason
        self._search_secs = float(secs)
        self._search_until = time.monotonic() + float(secs)

    def _wake_from_thread(self, loop, kind: str, detail: dict) -> None:
        with contextlib.suppress(Exception):
            if loop.is_closed():
                return
            loop.call_soon_threadsafe(self._on_hotplug_event, kind, detail)

    def _on_hotplug_event(self, kind: str, detail: dict) -> None:
        summary = str((detail or {}).get("summary") or "")[:120]
        if kind in ("usb-add", "net-add"):
            if not self.prefer_usb:
                return
            prod = str((detail or {}).get("product") or "")
            vid = prod.split("/")[0].lower() if prod else ""
            if vid and vid in NEVER_PHONE_VENDORS and vid not in ANDROID_PHONE_VENDORS:
                return
            if self._live_ws() is None:
                self._open_search_window(f"{kind} while disconnected ({summary})")
                return
            with contextlib.suppress(Exception):
                t = asyncio.get_running_loop().create_task(
                    self._evaluate_usb_hotplug(detail))
                self._background_tasks_add(t)
        elif kind in ("usb-remove", "net-remove"):
            if self.usb_link or self._live_ws() is None:
                self._open_search_window(f"{kind} ({summary})", secs=5.0)

    async def _evaluate_usb_hotplug(self, detail: dict) -> None:
        with contextlib.suppress(Exception):
            devs = await asyncio.to_thread(usb_device_snapshot)
            cands = usb_phone_candidates(devs)
            if not cands:
                return
            names: list[str] = []
            with contextlib.suppress(Exception):
                names.append(self._best_device_name())
            with contextlib.suppress(Exception):
                for ent in (self.load_paired_phones() or {}).values():
                    if isinstance(ent, dict) and ent.get("name"):
                        names.append(str(ent.get("name")))
            extra_vids = {
                str(v).strip().lower().removeprefix("0x")
                for v in ((getattr(self, "user_settings", {}) or {}).get("extra_phone_vids") or [])
                if str(v).strip()
            }
            for cand in cands:
                label = (str(cand.get("product") or "").strip()
                         or f"{cand.get('idVendor')}:{cand.get('idProduct')}")
                if usb_candidate_matches_names(cand, names, extra_vids=extra_vids):
                    self._open_search_window(
                        f"USB device '{label}' matches the connected/paired phone — "
                        f"evaluating USB takeover")
                    return
                if str(cand.get("idVendor") or "").lower() in (set(ANDROID_PHONE_VENDORS) | extra_vids):
                    self._open_search_window(
                        f"Android-vendor USB device '{label}' appeared while connected — "
                        f"evaluating USB takeover")
                    return
            now_m = time.monotonic()
            if now_m - float(getattr(self, "_last_nonmatching_wake_mono", 0.0)) >= 30.0:
                self._last_nonmatching_wake_mono = now_m
                self._open_search_window("rate-limited USB hotplug fallback check", secs=5.0)

    async def _usb_link_monitor_loop(self) -> None:
        loop = asyncio.get_running_loop()
        self._uevent_watcher = UeventWatcher(
            lambda kind, detail: self._wake_from_thread(loop, kind, detail))
        mode = "none"
        with contextlib.suppress(Exception):
            mode = await asyncio.to_thread(self._uevent_watcher.start)
        self.events.log(
            "TRANSPORT_WATCH_MODE",
            f"hotplug source={mode} — active USB/Wi-Fi search runs ONLY inside "
            f"10 s discovery windows (startup / disconnect / USB plug / manual retry)",
            feature="core")
        self._open_search_window("startup")
        self._had_live_conn = self._live_ws() is not None
        try:
            await self._usb_link_monitor_run()
        finally:
            with contextlib.suppress(Exception):
                w = self._uevent_watcher
                if w is not None:
                    await asyncio.to_thread(w.stop)

    async def _usb_link_monitor_run(self) -> None:
        with contextlib.suppress(asyncio.CancelledError):
            while not (self._stop_evt and self._stop_evt.is_set()):
                live_now = self._live_ws() is not None
                if getattr(self, "_had_live_conn", False) and not live_now:
                    self._open_search_window("phone connection lost")
                self._had_live_conn = live_now
                busy = bool(self.usb_link
                            or getattr(self, "_usb_takeover", None)
                            or getattr(self, "_usb_force_released_mono", 0.0))
                window_open = self.search_window_active()
                if window_open and not self._window_logged:
                    self._window_logged = True
                    self.events.log(
                        "DISCOVERY_WINDOW_OPEN",
                        f"{self._search_reason} — actively searching for "
                        f"{self._search_secs:.0f}s",
                        feature="core")
                if not (window_open or busy):
                    if self._window_logged:
                        self._window_logged = False
                        self.events.log(
                            "DISCOVERY_WINDOW_END",
                            "search window closed — sleeping until the next event "
                            "(USB plug/unplug, disconnect, or manual retry)",
                            feature="core")
                    await asyncio.sleep(0.75)
                    continue
                try:
                    if not self.prefer_usb:
                        if self.usb_link or self._usb_takeover:
                            self.usb_link = None
                            self.tether = None
                            self._usb_takeover = None
                            self._usb_takeover_exhausted = False
                            self.usb_status_msg = ("USB transport disabled in Settings — "
                                                   "Wi-Fi only")
                        await asyncio.sleep(3.0)
                        continue
                    cur_usb, cur_net = await asyncio.to_thread(
                        lambda: (usb_device_snapshot(), net_snapshot())
                    )
                    self.usb_devices_count = len(cur_usb)
                    moto_devs = usb_phone_candidates(cur_usb)
                    if moto_devs:
                        vid = str(moto_devs[0].get("idVendor") or "22b8").lower()
                        pid = moto_devs[0].get("idProduct", "")
                        ser = str(moto_devs[0].get("serial") or "").strip()
                        if ser and re.fullmatch(r"0+", ser):
                            ser = ""
                        prod_nm = str(moto_devs[0].get("product") or "").strip()
                        self.moto_usb_pid = f"{vid}:{pid}"
                        if ser and ser != self.moto_adb_serial:
                            self.moto_adb_serial = ser
                            self.events.log("USB_ADB_SERIAL", f"usb device={vid}:{pid} product={prod_nm!r} serial={ser}")
                        if pid == "2e81" and not self.usb_link:
                            self.usb_status_msg = "Phone connected as 22b8:2e81 (charging/ADB only) — tap 'Samyuj' in the phone's 'Use USB for' sheet!"
                        elif pid in READYFOR_PIDS and not self.usb_link:
                            self.usb_status_msg = f"Phone in Smart Connect USB mode (22b8:{pid}) — waiting for USB network interface IPv4…"
                    else:
                        self.moto_usb_pid = None
                        if not self.usb_link:
                            self.usb_status_msg = "waiting for USB cable + 'Samyuj' sheet tap"

                    for n, rec in cur_net.items():
                        if not (rec.get("usb_device") or USB_IFACE_RE.match(n)):
                            continue
                        if rec.get("operstate") == "down" and not rec.get("usb_device"):
                            continue
                        if rec.get("ipv4"):
                            self._noip_since.pop(n, None)
                            continue
                        t0 = self._noip_since.setdefault(n, time.monotonic())
                        if time.monotonic() - t0 >= 1.2 and self.usb_config_manager.should_attempt(n):
                            addr_order = str((getattr(self, "user_settings", {}) or {}).get("usb_addr_order") or "dhcp,static-p2p")
                            self.events.log("USB_LINK_NO_IPV4",
                                            f"{n} has no IPv4 — running USB address ladder ({addr_order})")
                            cfg_res = await asyncio.to_thread(
                                self.usb_config_manager.configure_interface,
                                n,
                                False,
                                addr_order,
                            )
                            if cfg_res.get("status") == "configured":
                                assigned = cfg_res.get("configured_ip") or VENDOR_USB_P2P_PC_CIDR
                                self.events.log("USB_LINK_STATIC_CFG",
                                                f"Configured {assigned} on {n} via {cfg_res.get('method')}")
                                self.usb_status_msg = f"Auto-configured {assigned} on {n} ({cfg_res.get('method')})"
                                cur_net = await asyncio.to_thread(net_snapshot)
                            else:
                                hint_cmd = cfg_res.get("required_privilege_action") or f"nmcli dev modify {n} ipv4.method manual ipv4.addresses {VENDOR_USB_P2P_PC_CIDR}"
                                self.usb_status_msg = f"USB {n} needs IPv4 (attempt #{cfg_res.get('attempt_count')}) — run: {hint_cmd}"
                                self.events.log("USB_STATIC_HINT", self.usb_status_msg)

                    phone_hint = (self.tether or {}).get("phone_ip") if self.tether else None
                    link = find_tether_link(cur_net, phone_ips=[phone_hint] if phone_hint else ())
                    if link and self.usb_link != link:
                        iface, ip = link
                        old_ip = self.lan_ip
                        self.lan_ip = ip
                        self.usb_link = link
                        self.phone_resolved_us = False
                        self._adb_trigger_done_for_link = False
                        self.tether = {"iface": iface, "pc_ip": ip, "phone_ip": None}
                        self.usb_status_msg = f"USB link adopted on {iface} ({ip}) — probing phone IP & announcing _readyfor._tcp"
                        self.events.log("USB_LINK_ADOPTED",
                                        f"{iface} {ip} (lan_ip was {old_ip}) — announcing _readyfor._tcp on USB")
                        if self.mdns_adv and self.mdns_adv.ok:
                            self.mdns_adv.ensure_membership(ip)
                            self.mdns_adv.announce_now()
                        else:
                            if self.mdns_adv:
                                await self.mdns_adv.stop()
                            self.mdns_adv = UsbMdnsAdvertiser(self)
                            await self.mdns_adv.start()
                        ph_ip = await asyncio.to_thread(probe_phone_ip, iface, ip)
                        if self.tether:
                            self.tether["phone_ip"] = ph_ip
                        self.events.log("USB_PHONE_IP", f"phone_ip={ph_ip} on {iface} (pc={ip})")
                        if self.mdns_adv and self.mdns_adv.ok:
                            conn_t = asyncio.get_running_loop().create_task(
                                self._usb_mdns_discovery_burst(ip))
                            self._background_tasks_add(conn_t)
                        self._usb_link_adopted_mono = time.monotonic()
                        self._usb_takeover_exhausted = False
                        live_now = self._live_ws()
                        if (self.prefer_usb and live_now is not None
                                and not self._is_usb_conn(live_now)):
                            self._usb_takeover = {"since": time.monotonic(),
                                                  "wifi_conn_id": live_now.get("id")}
                            self.usb_status_msg = (f"USB link adopted on {iface} ({ip}) — "
                                                   f"switching session from Wi-Fi to USB as soon as the phone redials")
                            self.events.log("TRANSPORT_USB_TAKEOVER_ARMED",
                                            f"live Wi-Fi conn{live_now.get('id')} will be released once USB session is up "
                                            f"(force-switch after {getattr(self, 'usb_switch_timeout_s', 10.0):.0f}s)")
                        with contextlib.suppress(Exception):
                            t_push = asyncio.get_running_loop().create_task(
                                self._proactive_usb_switch_push(ip, ph_ip)
                            )
                            self._background_tasks_add(t_push)
                        if self.moto_adb_serial and not self._adb_trigger_done_for_link:
                            self._adb_trigger_done_for_link = True
                            with contextlib.suppress(Exception):
                                asyncio.get_running_loop().create_task(
                                    adb_trigger_phone(self.moto_adb_serial))
                    elif not link and self.usb_link:
                        old_link = self.usb_link
                        for c_usb in list(self._conns):
                            if not c_usb.get("closed") and self._is_usb_conn(c_usb):
                                if not c_usb.get("tunnel") and c_usb.get("role") != "tunnel":
                                    self._last_migrated_conn = c_usb
                                    c_usb["_session_migrated"] = True
                                self._close_conn(c_usb, "USB link disconnected — switching to Wi-Fi")
                        self.usb_link = None
                        self.tether = None
                        self.phone_resolved_us = False
                        self._usb_force_released_mono = 0.0
                        with contextlib.suppress(Exception):
                            cleaned_disc = []
                            for d_ent in list(getattr(self, "discovered_phones", []) or []):
                                if not isinstance(d_ent, dict):
                                    continue
                                wifi_ips = [ip for ip in (d_ent.get("ips") or []) if not is_usb_ip(str(ip), usb_link=old_link)]
                                if wifi_ips:
                                    d_ent["ips"] = wifi_ips
                                    cleaned_disc.append(d_ent)
                            self.discovered_phones = cleaned_disc
                            if self.mdns_adv:
                                if hasattr(self.mdns_adv, "_joined_ips"):
                                    self.mdns_adv._joined_ips.discard(old_link[1])
                                if hasattr(self.mdns_adv, "peers"):
                                    for _p in self.mdns_adv.peers.values():
                                        if isinstance(_p, dict):
                                            if isinstance(_p.get("ips"), set):
                                                _p["ips"] = {ip for ip in _p["ips"] if not is_usb_ip(str(ip), usb_link=old_link)}
                                            _p.pop("port", None)
                                            _p.pop("reported_sig", None)
                        if not getattr(self, "lan_ip_override", None):
                            fresh_wip = await asyncio.to_thread(self._lan_ip)
                            if fresh_wip and fresh_wip != "127.0.0.1" and not is_usb_ip(fresh_wip, usb_link=old_link):
                                self.wifi_ip = fresh_wip
                        self.lan_ip = self.wifi_ip
                        self._usb_takeover = None
                        self._usb_takeover_exhausted = False
                        self._adb_trigger_done_for_link = False
                        self.usb_status_msg = (f"USB link {old_link[0]} disconnected — "
                                               f"auto-falling back to Wi-Fi")
                        self.events.log("USB_LINK_LOST",
                                        f"{old_link} gone — reverted lan_ip={self.lan_ip}; "
                                        f"persistent auto-connect loop will redial over Wi-Fi")
                        with contextlib.suppress(Exception):
                            t_wrec = asyncio.get_running_loop().create_task(
                                self._proactive_wifi_reconnect_burst()
                            )
                            self._background_tasks_add(t_wrec)
                    await self.check_transport_switch()
                except Exception:
                    pass
                await asyncio.sleep(1.0)

    def _close_conn(self, conn: dict, reason: str = "") -> None:
        if conn is None or conn.get("closed"):
            return
        conn["closed"] = True
        with contextlib.suppress(Exception):
            conn["writer"].close()
        with contextlib.suppress(Exception):
            conn["log"].close()
        self.events.log("CONN_CLOSED_BY_PC",
                        f"conn{conn.get('id')} remote={conn.get('remote')} reason={reason}")

    def _background_tasks_add(self, task) -> None:
        with contextlib.suppress(Exception):
            if not hasattr(self, "_bg_tasks"):
                self._bg_tasks = set()
            self._bg_tasks.add(task)
            task.add_done_callback(self._bg_tasks.discard)

    async def _usb_mdns_discovery_burst(self, usb_ip: str, rounds: int = 4) -> None:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            for i in range(rounds):
                if self._stop_evt and self._stop_evt.is_set():
                    return
                if self._live_ws() is not None and self.usb_link:
                    live = self._live_ws()
                    if self._is_usb_conn(live):
                        return
                if self.mdns_adv and self.mdns_adv.ok:
                    self.mdns_adv._send(
                        _dns_query(WIFI_SVC_TYPE.rstrip("."), 12),
                        mcast_if_ip=usb_ip)
                    self.events.log("USB_MDNS_QUERY_BURST",
                                    f"#{i+1} PTR query for {WIFI_SVC_TYPE} via {usb_ip}",
                                    feature="core")
                if i < rounds - 1:
                    await asyncio.sleep(2.0)

    def _migrate_session_state(self, src_conn: dict, dst_conn: dict) -> None:
        if not isinstance(src_conn, dict) or not isinstance(dst_conn, dict) or src_conn is dst_conn:
            return
        src_conn["_session_migrated"] = True
        for k in (
            "desktop_started", "desktop_pending", "desktop_view", "desktop_as_sid",
            "as_session_id", "as_display_id", "as_geom", "as_pkg", "as_cmp",
            "as_setup_seen", "as_media_connected", "as_sessions", "app_as_sid",
            "wc_started", "wc_start_sent", "wc_trigger_answered",
            "ss_started", "input_cap_sent", "media_last_frame",
            "webcam_last_frame", "media_last_frame_by_service",
        ):
            if k in src_conn and src_conn[k] and not dst_conn.get(k):
                dst_conn[k] = src_conn[k]
        if isinstance(src_conn.get("session"), dict) and src_conn["session"].get("feature"):
            dst_conn.setdefault("session", new_session_state())["feature"] = src_conn["session"]["feature"]

    async def _proactive_usb_switch_push(self, usb_ip: str, phone_usb_ip: str | None = None) -> None:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await asyncio.sleep(0.15)
            for tc in list(self._conns):
                if not tc.get("closed") and tc.get("tunnel"):
                    await self._send_tunnel_bootstrap(tc, why="usb-adopt-switch")
            d = self.latest_phone_discovery()
            port = int((d or {}).get("port") or 0)
            targets: list[str] = []
            if phone_usb_ip and port:
                targets.append(f"{phone_usb_ip}:{port}")
            live_c = self._live_ws()
            if live_c is not None and port and hasattr(self, "_phone_ip"):
                lip = self._phone_ip(live_c)
                if lip and lip != "127.0.0.1" and f"{lip}:{port}" not in targets:
                    targets.append(f"{lip}:{port}")
            for dip in ((d or {}).get("ips") or []):
                if port and f"{dip}:{port}" not in targets:
                    targets.append(f"{dip}:{port}")
            for tgt in targets[:3]:
                live_usb = next(
                    (c for c in reversed(self._conns)
                     if c.get("ws") and not c.get("closed") and not c.get("tunnel") and self._is_usb_conn(c)),
                    None,
                )
                if live_usb is not None:
                    await self.check_transport_switch()
                    return
                await self.wifi_tunnel_push(tgt, wait_dial=3.5, for_usb=True)

    async def _proactive_wifi_reconnect_burst(self) -> None:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            if self.mdns_adv and self.mdns_adv.ok:
                wip = self.mdns_adv._wifi_ip()
                if wip and wip != "127.0.0.1":
                    self.mdns_adv.ensure_membership(wip)
                    self.mdns_adv.announce_now()
                    self.mdns_adv._send(_dns_query(WIFI_SVC_TYPE.rstrip("."), 12), mcast_if_ip=wip)
            await asyncio.sleep(0.25)
            for _ in range(4):
                if self._live_ws() is not None or self.usb_link:
                    return
                d = self.latest_phone_discovery()
                if d and d.get("ips") and d.get("port") and not self._auto_connecting:
                    await self.on_phone_mdns_discovered(d, for_usb=False)
                if self._live_ws() is not None:
                    return
                await asyncio.sleep(1.2)

    async def check_transport_switch(self) -> None:
        if not self._usb_takeover:
            fr_mono = float(getattr(self, "_usb_force_released_mono", 0.0) or 0.0)
            if fr_mono > 0.0 and not getattr(self, "_usb_takeover_exhausted", False):
                if time.monotonic() - fr_mono > 12.0 and self._live_ws() is None:
                    self._usb_takeover_exhausted = True
            return
        usb_conns = [
            c for c in reversed(self._conns)
            if c.get("ws") and not c.get("closed")
            and not c.get("tunnel") and c.get("role") != "tunnel"
            and self._is_usb_conn(c)
        ]
        live = usb_conns[0] if usb_conns else self._live_ws()
        if live is not None and self._is_usb_conn(live):
            old_id = self._usb_takeover.get("wifi_conn_id")
            for c in list(self._conns):
                if (c.get("id") != live.get("id") and not c.get("closed")
                        and c.get("ws") and not c.get("tunnel")
                        and (c.get("id") == old_id or not self._is_usb_conn(c))):
                    self._migrate_session_state(c, live)
                    self._close_conn(c, "transport switched to USB")
            self.usb_status_msg = "Session switched to USB (Wi-Fi connection released)"
            self.events.log("TRANSPORT_SWITCHED", "usb — Wi-Fi control connection released",
                            feature="core")
            self._usb_takeover = None
            self._usb_force_released_mono = 0.0
            self._usb_takeover_exhausted = False
            return
        elapsed = time.monotonic() - float(self._usb_takeover.get("since", 0.0))
        timeout_s = float(getattr(self, "usb_switch_timeout_s", 4.5))
        if timeout_s > 0 and elapsed > timeout_s:
            old_id = self._usb_takeover.get("wifi_conn_id")
            for c in list(self._conns):
                if (c.get("id") == old_id and not c.get("closed")
                        and c.get("ws") and not c.get("tunnel")
                        and not self._is_usb_conn(c)):
                    self._last_migrated_conn = c
                    c["_session_migrated"] = True
                    self._close_conn(c, "force USB switch (cable plugged)")
            self.usb_status_msg = ("Cable plugged — Wi-Fi session released to force the "
                                   "phone onto USB (re-announcing _readyfor._tcp; ADB nudge sent)")
            self.events.log("TRANSPORT_FORCE_USB_SWITCH",
                            f"no USB redial within {timeout_s:.0f}s — Wi-Fi conn released "
                            f"so the phone switches to the cable", feature="core")
            self._usb_takeover_exhausted = False
            self._usb_force_released_mono = time.monotonic()
            self._usb_link_adopted_mono = time.monotonic()
            self._usb_takeover = None
            with contextlib.suppress(Exception):
                if self.mdns_adv and self.mdns_adv.ok:
                    self.mdns_adv.announce_now()
                if self.usb_link:
                    ph_ip = (self.tether or {}).get("phone_ip")
                    t_push = asyncio.get_running_loop().create_task(
                        self._proactive_usb_switch_push(self.usb_link[1], ph_ip)
                    )
                    self._background_tasks_add(t_push)
                if self.moto_adb_serial:
                    asyncio.get_running_loop().create_task(
                        adb_trigger_phone(self.moto_adb_serial))
            return
        if timeout_s <= 0 and elapsed > 30.0:
            self.events.log("TRANSPORT_TAKEOVER_TIMEOUT",
                            "phone did not redial over USB within 30 s — keeping the Wi-Fi session",
                            feature="core")
            self._usb_takeover = None

    def _lan_ip(self) -> str:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 53))
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"
        finally:
            s.close()

    @staticmethod
    def _all_ipv4s() -> list[str]:
        import subprocess
        out: list[str] = []
        try:
            txt = subprocess.run(["ip", "-4", "-o", "addr", "show", "scope", "global"],
                                 capture_output=True, text=True, timeout=2).stdout
            out = re.findall(r"inet (\d+\.\d+\.\d+\.\d+)", txt)
        except Exception:
            pass
        return out

    @staticmethod
    def _wifi_ipv4s() -> list[str]:
        return wifi_ipv4s()

    def reselect_wifi_ip_for_peer(self, peer_ip: str) -> bool:
        cur = str(getattr(self, "wifi_ip", "") or "")
        pick = pick_local_ip_for_peer(str(peer_ip or ""), self._all_ipv4s(), cur,
                                      preferred=self._wifi_ipv4s())
        if not pick or pick == cur:
            return False
        self.wifi_ip = pick
        self.lan_ip = pick
        with contextlib.suppress(Exception):
            self.qr_payload = self._build_qr_payload_str()
            self._qr_png_cache = None
        with contextlib.suppress(Exception):
            self.events.log(
                "WIFI_IP_RESELECTED",
                f"peer {peer_ip} is reachable via {pick} (was {cur}) — "
                f"QR payload and bootstrap re-issued", feature="core")
        return True

    def _warn_lan_ip(self) -> None:
        cands = self._all_ipv4s()
        self.events.log("LAN_IP_CANDIDATES", f"chosen={self.lan_ip} all={cands} — the QR "
                        f"carries 'chosen'; the phone must be able to route to it "
                        f"(same subnet as the phone's wlan0). Override: --lan-ip <ip>")
        if cands and self.lan_ip not in cands:
            self.events.log("LAN_IP_WARNING", f"chosen {self.lan_ip} is the default-route "
                            f"interface but not among host addresses {cands} — verify the "
                            f"phone can reach it!")
        print(f"\n*** QR will advertise IP {self.lan_ip} (candidates: {cands}) ***"
              f"\n*** If the phone is on a different subnet, re-run with "
              f"--lan-ip <your-{(cands[0].rsplit('.', 1)[0] + '.x') if cands else 'LAN'}-address> ***\n")

from .base import SamyujService

class TransportService(SamyujService):
    service_id = 0
    id = "transport"
    name = "Transport & Discovery Service (USB + Wi-Fi + BLE)"
    description = "USB RNDIS/NCM cable watcher & static .162 self-heal, dual-interface _readyfor._tcp (USB hex64 fp) + _readyformdns._tcp (Wi-Fi b64 fp) mDNS, /readyfor/rdp & /readyfor/mdns ws-GATT tunnel, BLE 0x5246 advert/scanner & fragmented GATT 0x0722/0x0728 push"

    def actions(self) -> list[dict]:
        return [
            {"name": "usb_bootstrap", "label": "Send USB Tunnel Bootstrap (cmd:1)", "description": "Send cmd:1 RemoteClient TEXT bootstrap on live /readyfor/rdp or /readyfor/mdns tunnel"},
            {"name": "adb_trigger", "label": "Fire ADB Setup Intent", "description": "Trigger phone EXTERNAL_SETUP over ADB"},
            {"name": "usb_scan", "label": "Scan USB & Network", "description": "Snapshot sysfs USB devices, self-heal USB IPv4 if needed, and adopt USB link"},
            {"name": "wifi_autoconnect", "label": "Wi-Fi Tunnel Auto-Connect", "description": "Dial phone's wss://<phone-ip>:<wsPort>/readyfor/mdns tunnel with [nonce16][op1|op2][cmd:1]"},
            {"name": "ble_scan", "label": "Scan BLE 0x5246 Phones", "description": "Scan for Motorola phones advertising 0x5246 BLE service data and verify paired nonces"},
            {"name": "ble_adv_toggle", "label": "Toggle BLE 0x5246 Advert", "description": "Start/stop BlueZ bluetoothctl / btmgmt 0x5246 presence advertisement"},
            {"name": "ble_push", "label": "BLE GATT 0x0722/0x0728 Push", "description": "Write (or 15-chunk fragment on 0x0728) GATT bootstrap frame to phone BLE service 0x5246"},
            {"name": "ping", "label": "Send WS Ping", "description": "Send RFC 6455 Ping frame on active control connection"},
            {"name": "prefer_usb_toggle", "label": "Toggle USB-Preferred Auto-Switch", "description": "Automatically move the session to USB when the cable is plugged, and back to Wi-Fi when unplugged"},
            {"name": "retry_now", "label": "Search for Phone Now (10 s window)", "description": "Manually open a 10 s discovery window: USB scan + Wi-Fi mDNS/BLE search; then sleep until the next plug/disconnect event or another retry"},
        ]

    def get_state(self) -> dict:
        t = getattr(self.h, "tether", None) or {}
        ble = getattr(self.h, "ble_advertiser", None)
        return {
            **super().get_state(),
            "lan_ip": getattr(self.h, "lan_ip", ""),
            "wifi_ip": getattr(self.h, "wifi_ip", ""),
            "port": getattr(self.h, "port", 0),
            "usb_link": list(self.h.usb_link) if getattr(self.h, "usb_link", None) else None,
            "phone_ip": t.get("phone_ip"),
            "mdns_up": bool(getattr(self.h, "mdns_adv", None) and self.h.mdns_adv.ok),
            "phone_resolved_us": bool(getattr(self.h, "phone_resolved_us", False)),
            "ble_adv_ok": bool(ble and ble.ok),
            "ble_scanned": list(getattr(self.h, "ble_scanned_devices", []) or []),
            "discovered_phones": list(getattr(self.h, "discovered_phones", []) or []),
        }

    async def invoke(self, action: str, params: dict | None = None,
                     conn: dict | None = None) -> dict:
        params = params or {}
        if action == "usb_bootstrap":
            return await self.h.request("usb-bootstrap", conn)
        if action == "adb_trigger":
            return await self.h.request("adb-trigger", conn)
        if action == "usb_scan":
            return await self.h.request("usb-scan", conn)
        if action == "wifi_autoconnect":
            target = str(params.get("target") or "")
            return await self.h.request(f"wifi-autoconnect:{target}" if target else "wifi-autoconnect", conn)
        if action == "ble_scan":
            return await self.h.request("ble-scan", conn)
        if action == "ble_adv_toggle":
            return await self.h.request("ble-adv-toggle", conn)
        if action == "ble_push":
            mac = str(params.get("mac") or "")
            return await self.h.request(f"ble-push:{mac}" if mac else "ble-push", conn)
        if action == "ping":
            return await self.h.request("ping", conn)
        if action == "prefer_usb_toggle":
            return await self.h.request("transport-prefer-usb", conn)
        if action == "retry_now":
            return await self.h.request("transport-retry", conn)
        return await super().invoke(action, params, conn)
