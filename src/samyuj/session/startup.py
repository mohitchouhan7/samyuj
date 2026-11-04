from __future__ import annotations

import asyncio
import base64
import contextlib
import datetime
import os

from samyuj.core.identity import (
    load_or_create_device_id as _simple_device_id,
    load_or_create_identity as _simple_load_or_create,
    make_server_ssl_context as _simple_server_ctx,
)
from samyuj.core.logging import EventLog
from samyuj.protocol.constants import derive_pc_sn
from samyuj.transport.network import pick_startup_ip
from samyuj.transport.discovery import (
    BleAdvertiser,
    UsbMdnsAdvertiser,
    find_usb_tether_iface as _find_usb_tether_iface,
    make_nonce_id as _make_nonce_id,
)

class RuntimeStartupMixin:

    async def start(self) -> int:
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        with contextlib.suppress(OSError):
            os.chmod(self.state_dir, 0o700)
        for _sec_name in (
            "identity.key.pem",
            "identity.cert.pem",
            "shared_key.txt",
            "paired_phones.json",
            "token.txt",
            "device_id.txt",
            "user_settings.json",
            "nfs_shares.json",
            "clipboard_prefs.json",
        ):
            _sec_p = self.state_dir / _sec_name
            if _sec_p.exists():
                with contextlib.suppress(OSError):
                    os.chmod(_sec_p, 0o600)
        if self.debug:
            self.out_dir.mkdir(parents=True, exist_ok=True)
            (self.out_dir / "connections").mkdir(exist_ok=True)
        self._stop_evt = asyncio.Event()
        self.start_utc = datetime.datetime.now(datetime.UTC).isoformat()
        self.events = EventLog(
            (self.out_dir / "events.csv") if self.debug else None,
            log_secrets=getattr(self, "log_secrets", False),
            disk_enabled=self.debug,
            console_print=self.debug,
        )
        with contextlib.suppress(Exception):
            if getattr(self, "media_diag", None) is not None:
                self.media_diag.report_dir = self.out_dir if self.debug else self.state_dir
                self.media_diag.start()
        self.identity = _simple_load_or_create(str(self.state_dir))
        self.device_id_b64 = _simple_device_id(str(self.state_dir))
        if not getattr(self, "pc_sn", ""):
            self.pc_sn = derive_pc_sn(self.device_id_b64)
        self.fp_b64 = self.identity.fp_b64
        self.token_b64 = base64.b64encode(os.urandom(16)).decode("ascii")
        self.tunnel_op3_evt = asyncio.Event()
        self.events.log(
            "IDENTITY",
            f"deviceId={self.device_id_b64} fpB64={self.fp_b64} "
            f"fpHex={self.identity.fingerprint} token={self.token_b64} stateDir={self.state_dir}",
        )
        if self.debug:
            (self.out_dir / "cert.cer").write_bytes(self.identity.cert_pem)

        ctx = _simple_server_ctx(self.identity)
        self._server = await asyncio.start_server(self._on_connection, self.host, self.port, ssl=ctx)
        sock = self._server.sockets[0]
        self.port = sock.getsockname()[1]
        if int(getattr(self, "webcam_port", 0) or 0) <= 0:
            self.webcam_port = int(self.port)

        all_ips = self._all_ipv4s()
        wifi_ips = self._wifi_ipv4s()
        if self.lan_ip_explicit and self.lan_ip_override:
            self.wifi_ip = self.lan_ip_override
        elif self.lan_ip_override and self.lan_ip_override in all_ips:
            self.wifi_ip = self.lan_ip_override
        else:
            auto_ip = self._lan_ip()
            self.wifi_ip = pick_startup_ip(auto_ip, all_ips, wifi_ips)

        self.lan_ip = self.wifi_ip
        self.events.log(
            "WIFI_IP_AUTO",
            f"wifi_ifaces={wifi_ips} default_route={self._lan_ip()} all={all_ips} "
            f"chosen={self.wifi_ip} (Wi-Fi-assigned address preferred; override "
            f"with --lan-ip)", feature="core")
        self.events.log("LISTENER_UP", f"{self.host}:{self.port} wifi_ip={self.wifi_ip} (TLS)")
        self.qr_payload = self._build_qr_payload_str()
        if self.debug or self.pairing_mode_active:
            png = self.get_pairing_qr_png(force_refresh=True)
            if self.debug:
                (self.out_dir / "qr_payload.txt").write_text(self.qr_payload, encoding="utf-8")
                if png:
                    (self.out_dir / "qr.png").write_bytes(png)

        if self.host != "127.0.0.1":
            self.mdns_nonce = _make_nonce_id(self.device_id_b64)
            init_link = await asyncio.to_thread(_find_usb_tether_iface)
            if init_link:
                iface, uip = init_link
                self.usb_link = init_link
                self.lan_ip = uip
                self.tether = {"iface": iface, "pc_ip": uip, "phone_ip": None}
                self.usb_status_msg = f"USB link active on {iface} ({uip})"
                self.events.log("USB_LINK_DETECTED", f"{iface} {uip} present at startup")
            self.mdns_adv = UsbMdnsAdvertiser(self)
            await self.mdns_adv.start()
            self.ble_advertiser = BleAdvertiser(self)
            asyncio.get_running_loop().create_task(self.ble_advertiser.start())
            asyncio.get_running_loop().create_task(self._usb_link_monitor_loop())
            asyncio.get_running_loop().create_task(self._auto_connect_background_loop())

        self.clip_sync.start_background_poller()
        if (
            getattr(self, "webcam_controller", None) is not None
            and self.webcam_controller.v4l2_auto
            and str(getattr(self, "device_name", "") or "") != "SELFTEST-PC"
        ):
            asyncio.get_running_loop().create_task(
                asyncio.to_thread(self.webcam_controller.ensure_startup_ready, False)
            )
        return self.port

    async def stop(self) -> None:
        self._stop_presenter("runtime stop")
        if self.mdns_adv is not None:
            with contextlib.suppress(Exception):
                await self.mdns_adv.stop()
            self.mdns_adv = None
        if self.ble_advertiser is not None:
            with contextlib.suppress(Exception):
                await self.ble_advertiser.stop()
            self.ble_advertiser = None
        if self._nfs_srv is not None:
            with contextlib.suppress(Exception):
                await self._nfs_srv.stop()
            self._nfs_srv = None
        if self._xfer_srv is not None:
            with contextlib.suppress(Exception):
                self._xfer_srv.close()
            self._xfer_srv = None
        for c in list(self._conns):
            if c.get("ws") and not c.get("closed"):
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(self._stop_all(c, "runtime stop"), timeout=2.0)
        for sid, pc in list(self._pcs.items()):
            with contextlib.suppress(Exception):
                await self._close_pc_safe(pc)
            self.events.log("MEDIA_PC_CLOSED", f"sid={sid}")
        self._pcs.clear()
        if self._ss_pc is not None:
            with contextlib.suppress(Exception):
                await self._close_pc_safe(self._ss_pc)
            self._ss_pc = None
        if self._ss_track is not None:
            with contextlib.suppress(Exception):
                self._ss_track.stop()
            self._ss_track = None
        for c in list(self._conns):
            c["closed"] = True
            with contextlib.suppress(Exception):
                c["writer"].close()
            with contextlib.suppress(Exception):
                c["log"].close()
        if self._server:
            self._server.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._server.wait_closed(), timeout=2)
        if getattr(self, "webcam_controller", None) is not None:
            with contextlib.suppress(Exception):
                self.webcam_controller.stop_v4l2()
        if getattr(self, "_snap_executor", None) is not None:
            with contextlib.suppress(Exception):
                self._snap_executor.shutdown(wait=True)
        if self.events:
            self.events.log("RUNTIME_STOP", f"{len(self._conns)} connections")
            self.events.close()

    async def wait_stopped(self) -> None:
        assert self._stop_evt is not None
        await self._stop_evt.wait()

    def request_stop(self) -> None:
        if self._stop_evt:
            self._stop_evt.set()
