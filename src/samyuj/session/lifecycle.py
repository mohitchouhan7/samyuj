from __future__ import annotations

import atexit
import asyncio
import base64
import concurrent.futures
import contextlib
import datetime
import hashlib
import os
from pathlib import Path
import sys
import time
from typing import Any

from samyuj.core.logging import EventLog
from samyuj.core.paths import get_xdg_paths
from samyuj.core.tasks import MobileDesktopServiceThread, get_mobile_desktop_service_thread
from samyuj.diagnostics import MediaDiagnostics
from samyuj.files.nfs import NfsRpcServer, PhoneLinuxMountManager, PhoneNfsClient
from samyuj.files.phone_browser import PhoneFileWidgetConfig, PhoneFileWidgetController
from samyuj.files.transfer import TransferTask
from samyuj.core.settings import UserSettings
from samyuj.media.runtime import MediaRuntime
from samyuj.platform.linux.clipboard import ClipboardSyncManager
from samyuj.platform.linux.display import VirtualDisplayManager
from samyuj.platform.linux.power import PowerAndRefreshManager
from samyuj.platform.linux.setup import collect_pc_telemetry
from samyuj.protocol.constants import (
    DEFAULT_LAN_IP,
    DEFAULT_PC_CAPS,
    DEFAULT_SS_RES_LIST,
    FEATURES_LAUNCHABLE_SIDS,
    PC_SERVICE_VERSIONS,
)
from samyuj.services.applist import AppListServiceMixin
from samyuj.services.appstream import AppStreamServiceMixin
from samyuj.services.base import ServiceBus
from samyuj.services.clipboard import ClipboardServiceMixin
from samyuj.services.control import ControlTelemetryServiceMixin
from samyuj.services.cross_control import CrossControlServiceMixin
from samyuj.services.desktop import MobileDesktopServiceMixin
from samyuj.services.discovery_service import DiscoveryServiceMixin
from samyuj.services.dispatcher import RequestDispatcherMixin
from samyuj.services.files import FileSharingServiceMixin
from samyuj.services.gallery import GalleryFeaturesServiceMixin
from samyuj.services.input_service import InputServiceMixin
from samyuj.services.media_pump import HAVE_MEDIA, MediaPumpServiceMixin
from samyuj.services.messages import MessagesServiceMixin
from samyuj.services.notifications import NotificationServiceMixin
from samyuj.services.pairing import PairingServiceMixin
from samyuj.services.phonefs import PhoneFsServiceMixin
from samyuj.services.screenshare import ScreenShareServiceMixin
from samyuj.services.transport_service import TransportServiceMixin
from samyuj.services.webcam import WebcamServiceController, WebcamServiceMixin
from samyuj.transport.discovery import (
    BleAdvertiser,
)
from samyuj.transport.network import PortReachabilityChecker, UsbConfigManager
from .router import SessionRouterMixin
from .runtime_settings import RuntimeSettingsMixin
from .startup import RuntimeStartupMixin
from .context import ComposedControllerRegistry, RuntimeSessionContext
from .teardown import SessionTeardownMixin

DOMAIN_CONTROLLER_CLASSES: tuple[type, ...] = (
    TransportServiceMixin,
    DiscoveryServiceMixin,
    PairingServiceMixin,
    MobileDesktopServiceMixin,
    AppStreamServiceMixin,
    AppListServiceMixin,
    MediaPumpServiceMixin,
    InputServiceMixin,
    ScreenShareServiceMixin,
    WebcamServiceMixin,
    FileSharingServiceMixin,
    PhoneFsServiceMixin,
    ClipboardServiceMixin,
    NotificationServiceMixin,
    CrossControlServiceMixin,
    GalleryFeaturesServiceMixin,
    MessagesServiceMixin,
    ControlTelemetryServiceMixin,
    RequestDispatcherMixin,
)

class SamyujRuntime(
    RuntimeStartupMixin,
    RuntimeSettingsMixin,
    SessionRouterMixin,
    SessionTeardownMixin,
):

    FS_QUIC = False

    def __init__(
        self,
        *,
        host: str = "0.0.0.0",
        port: int = 0,
        out_dir: str | Path | None = None,
        state_dir: str | Path | None = None,
        device_name: str | None = None,
        app_ver: str = "09.0.3.013.000",
        ver_code: int = 903013000,
        auto_reply: str = "ok",
        reply_delay: float = 0.0,
        sender_id: int | None = None,
        deviceid_mode: str = "self",
        service_id: int = 0,
        msgid_mode: str = "echo",
        pc_cap: int = 5365,
        pc_sku: str | None = None,
        pc_service_versions: dict[str, int] | None = None,
        pc_rfa_service_versions: dict[str, int] | None = None,
        pc_real_device_id: str | None = None,
        reply_file: str | None = None,
        checkin: bool = False,
        checkin_fp_format: str = "hex",
        dialback: bool = False,
        dialback_path: str = "/readyfor/",
        server_first: bool = False,
        keepalive: bool = True,
        lang: str = "en",
        lan_ip: str | None = None,
        qr_ec: str = "m",
        verbose: str = "normal",
        focus: Any = None,
        filestation: str = "server",
        fs_path: str = "/C/nfs_demo",
        pc_caps: dict[str, int] | None = None,
        devstatus: bool = True,
        desktop: str = "accept",
        desktop_res: str = "1920x1080",
        appstream: str = "auto",
        as_scale: float = 1.0,
        as_density: int = 160,
        as_phy: str = "597x336",
        as_instance_id: int | None = None,
        as_answer_file: str | None = None,
        as_hb_interval: float = 20.0,
        screenshare: str = "auto",
        ss_source: str = "auto",
        ss_res: str = "1280x720",
        ss_fps: float = 30.0,
        ss_share_type: str = "mirror",
        ss_audio: bool = False,
        ss_scale: int = 200,
        ss_auto_start: bool = False,
        ss_sdp_fix: bool = True,
        ss_codec: str = "auto",
        ss_no_datachannel: bool = False,
        ss_no_audio: bool = False,
        ss_lan_ip_any: bool = False,
        exclusive: str = "auto",
        webcam: str = "auto",
        webcam_port: int = 0,
        rq: list[str] | None = None,
        rq_apps: list[str] | None = None,
        rq_icons_dir: str | Path | None = None,
        rq_delay: float = 1.5,
        wallpaper_file: str | Path | None = None,
        dnd: bool | None = None,
        pc_battery: int = 100,
        media: bool = True,
        snap_interval: float = 0.0,
        auto_swipe: str = "",
        input_cap: bool = True,
        sdp_raw: bool = False,
        log_secrets: bool = False,
        v4l2: bool = False,
        v4l2_device: str = "auto",
        v4l2_format: str = "YU12",
        v4l2_fps: float = 30.0,
        debug: bool = False,
        desktop_gui: bool = True,
        pair_on_start: bool = False,
        clipboard_sync: bool = True,
        prefer_usb: bool = True,
        appstream_source: str = "auto",
        v4l2_auto: bool = True,
        media_codec_pref: str = "h264",
        present_engine: str = "gtk",
        present_sink: str = "auto",
        desktop_fallback: str = "prompt",
        **_ignored_legacy_kwargs: Any,
    ) -> None:
        self.ctx = RuntimeSessionContext(self)
        self._component_registry = ComposedControllerRegistry(self, DOMAIN_CONTROLLER_CLASSES)
        self._init_core_and_identity(
            out_dir=out_dir, state_dir=state_dir, debug=debug, desktop_gui=desktop_gui,
            pair_on_start=pair_on_start, host=host, port=port, device_name=device_name,
            app_ver=app_ver, ver_code=ver_code, auto_reply=auto_reply, reply_delay=reply_delay,
            sender_id=sender_id, deviceid_mode=deviceid_mode, service_id=service_id,
            msgid_mode=msgid_mode, pc_cap=pc_cap, pc_sku=pc_sku,
            pc_service_versions=pc_service_versions,
            pc_rfa_service_versions=pc_rfa_service_versions,
            pc_real_device_id=pc_real_device_id, reply_file=reply_file, checkin=checkin,
            checkin_fp_format=checkin_fp_format, dialback=dialback, dialback_path=dialback_path,
            server_first=server_first, keepalive=keepalive, lang=lang, lan_ip=lan_ip,
            verbose=verbose, focus=focus, qr_ec=qr_ec, filestation=filestation,
            fs_path=fs_path, pc_caps=pc_caps, devstatus=devstatus,
        )
        self._init_display_and_stream_config(
            desktop=desktop, desktop_res=desktop_res, appstream=appstream, as_scale=as_scale,
            as_density=as_density, as_phy=as_phy, as_instance_id=as_instance_id,
            as_answer_file=as_answer_file, as_hb_interval=as_hb_interval,
            screenshare=screenshare, ss_source=ss_source, ss_res=ss_res, ss_fps=ss_fps,
            ss_share_type=ss_share_type, ss_audio=ss_audio, ss_scale=ss_scale,
            ss_auto_start=ss_auto_start, ss_sdp_fix=ss_sdp_fix, ss_codec=ss_codec,
            ss_no_datachannel=ss_no_datachannel, ss_no_audio=ss_no_audio,
            ss_lan_ip_any=ss_lan_ip_any, exclusive=exclusive,
        )
        self._init_webcam_and_apps_state(
            webcam=webcam, webcam_port=webcam_port, rq=rq, rq_apps=rq_apps,
            rq_icons_dir=rq_icons_dir, rq_delay=rq_delay, wallpaper_file=wallpaper_file,
            dnd=dnd, pc_battery=pc_battery,
        )
        self._init_phone_resolutions_state()
        self._init_clipboard_and_files_state(out_dir=out_dir, debug=debug)
        self._init_feature_and_messaging_state()
        self._init_media_and_input_controllers(
            media=media, sdp_raw=sdp_raw, log_secrets=log_secrets,
            snap_interval=snap_interval, auto_swipe=auto_swipe, input_cap=input_cap,
            v4l2=v4l2, v4l2_device=v4l2_device, v4l2_format=v4l2_format,
            v4l2_fps=v4l2_fps, v4l2_auto=v4l2_auto, clipboard_sync=clipboard_sync,
            prefer_usb=prefer_usb, appstream_source=appstream_source,
            media_codec_pref=media_codec_pref, desktop_fallback=desktop_fallback,
            present_engine=present_engine, present_sink=present_sink,
        )

    def _init_core_and_identity(self, **kw: Any) -> None:
        out_dir = kw["out_dir"]
        state_dir = kw["state_dir"]
        debug = kw["debug"]
        self.debug = bool(debug) or (out_dir is not None and str(out_dir).strip() != "")
        self.desktop_gui = bool(kw["desktop_gui"])
        self.pairing_mode_active = bool(kw["pair_on_start"])
        self._qr_png_cache: bytes | None = None
        xdg = get_xdg_paths("samyuj")
        ts = datetime.datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
        if out_dir:
            self.out_dir = Path(out_dir)
        elif self.debug:
            self.out_dir = xdg.cache_dir / "runs" / ts
        else:
            self.out_dir = xdg.cache_dir
        if state_dir:
            self.state_dir = Path(state_dir)
        elif out_dir and not debug:
            self.state_dir = self.out_dir.parent
        else:
            self.state_dir = xdg.data_dir
        self.host, self.port = kw["host"], kw["port"]
        self.device_name = kw["device_name"] or os.uname().nodename
        self.app_ver, self.ver_code = kw["app_ver"], kw["ver_code"]
        self.auto_reply = kw["auto_reply"]
        self.reply_delay = kw["reply_delay"]
        self.sender_id = kw["sender_id"]
        self.deviceid_mode = kw["deviceid_mode"]
        self.service_id = kw["service_id"]
        self.msgid_mode = "fresh" if self.auto_reply == "freshid" else kw["msgid_mode"]
        self.pc_cap = kw["pc_cap"]
        self.pc_sku = kw["pc_sku"] or self._guess_sku()
        self.pc_service_versions = kw["pc_service_versions"] or dict(PC_SERVICE_VERSIONS)
        self.pc_rfa_service_versions = kw["pc_rfa_service_versions"]
        self.pc_real_device_id = kw["pc_real_device_id"]
        self.reply_file = kw["reply_file"]
        self.checkin = kw["checkin"]
        self.checkin_fp_format = kw["checkin_fp_format"]
        self.dialback = kw["dialback"]
        self.dialback_path = kw["dialback_path"]
        self.server_first = kw["server_first"]
        self.keepalive = kw["keepalive"]
        self.lang = kw["lang"]
        self.identity = None
        self.events: EventLog = EventLog(None, disk_enabled=False, console_print=False)
        self._server: asyncio.AbstractServer | None = None
        self._conns: list[Any] = []
        self._next_conn_id = 1
        self._msg_id = 0
        self._stop_evt: asyncio.Event | None = None
        self.start_utc = ""
        self.lan_ip = "127.0.0.1"
        self.wifi_ip = "127.0.0.1"
        lan_ip = kw["lan_ip"]
        self.lan_ip_explicit = bool(lan_ip)
        self.lan_ip_override = DEFAULT_LAN_IP if lan_ip is None else lan_ip
        self.verbose = kw["verbose"]
        self.focus = kw["focus"]
        self.qr_ec = kw["qr_ec"]
        self.filestation = kw["filestation"]
        self.fs_path = kw["fs_path"]
        self.pc_caps = kw["pc_caps"] or dict(DEFAULT_PC_CAPS)
        self.desktop_fallback_delay = 5.0
        self.devstatus = kw["devstatus"]

    def _init_display_and_stream_config(self, **kw: Any) -> None:
        self.desktop = kw["desktop"]
        try:
            w, hh = (int(x) for x in str(kw["desktop_res"]).lower().split("x", 1))
        except Exception:
            w, hh = 1920, 1080
        self.desktop_res = (w, hh)
        self.appstream = kw["appstream"]
        self.as_scale = float(kw["as_scale"])
        self.as_density = int(kw["as_density"])
        try:
            pw, ph = (int(x) for x in str(kw["as_phy"]).lower().split("x", 1))
        except Exception:
            pw, ph = 597, 336
        self.as_phy = (pw, ph)
        as_instance_id = kw["as_instance_id"]
        self.as_instance_id = int(as_instance_id if as_instance_id is not None else time.time() * 1000) % 2**53
        self._as_instance_base = self.as_instance_id
        self._as_instance_seq = 0
        self.as_answer_file = kw["as_answer_file"] or None
        self.as_hb_interval = float(kw["as_hb_interval"])
        self.screenshare = kw["screenshare"]
        self.ss_source = kw["ss_source"]
        try:
            sw, sh = (int(x) for x in str(kw["ss_res"]).lower().split("x", 1))
        except Exception:
            sw, sh = 1280, 720
        self.ss_res = (sw, sh)
        self.ss_fps = max(1.0, float(kw["ss_fps"]))
        self.ss_share_type = kw["ss_share_type"]
        self.ss_virtual_pos = "right"
        self.virtual_display = VirtualDisplayManager(
            monitor_name=str(getattr(self, "ss_virtual_name", "SCPHONE")),
            position=self.ss_virtual_pos,
            log=lambda evt, msg: self.events.log(evt, msg, feature="screen_share"),
        )
        self.ss_virtual_res = (2400, 1080)
        self.ss_virtual_backend = "auto"
        self.ss_virtual_orient = "auto"
        self.ss_max_virtual_pixels = 2560 * 1440
        atexit.register(lambda vd=self.virtual_display: vd.release("process exit"))
        self.ss_audio = bool(kw["ss_audio"])
        self.ss_scale = max(100, int(kw["ss_scale"]))
        self.ss_auto_start = bool(kw["ss_auto_start"])
        self.ss_sdp_fix = bool(kw["ss_sdp_fix"])
        self.ss_codec = kw["ss_codec"]
        self.ss_no_datachannel = bool(kw["ss_no_datachannel"])
        self.ss_no_audio = bool(kw["ss_no_audio"])
        self.ss_prefer_lan_ip = not bool(kw["ss_lan_ip_any"])
        self.ss_session_id = 0
        self._ss_pc = None
        self._ss_track = None
        self.ss_stats: dict[str, Any] = {
            "offers": 0, "answers": 0, "cand_in": 0, "cand_out": 0,
            "state": "idle", "backend": "", "frames": 0,
        }
        self.exclusive = kw["exclusive"]

    def _init_webcam_and_apps_state(self, **kw: Any) -> None:
        self.webcam = kw["webcam"]
        self.webcam_port = int(kw["webcam_port"])
        self.webcam_sid = None
        self.webcam_cameras = None
        self.webcam_stats: dict[str, Any] = {
            "state": "idle", "offers": 0, "answers": 0, "cand_in": 0,
            "cand_out": 0, "frames": 0, "error": None,
        }
        self._wc_hb_task = None
        self.app_list: list[dict[str, Any]] = []
        self.app_wp_list: list[dict[str, Any]] = []
        self.app_recent: list[dict[str, Any]] = []
        self.app_running: list[dict[str, Any]] = []
        self.app_icons: dict[str, Any] = {
            "state": "idle", "port": 0, "fingerprint": "", "token": "",
            "autoDownload": False, "files": [], "saved": [], "bytes": 0, "error": "",
        }
        rq_icons_dir = kw["rq_icons_dir"]
        self.rq_icons_dir = Path(rq_icons_dir) if rq_icons_dir else None
        self.rq = list(kw["rq"] or [])
        self.rq_apps = list(kw["rq_apps"] or [])
        self.rq_delay = max(0.0, float(kw["rq_delay"]))
        self.rq_log: list[dict[str, Any]] = []
        wallpaper_file = kw["wallpaper_file"]
        self.wallpaper_file = wallpaper_file or None
        self.wallpaper_b64 = ""
        if self.wallpaper_file:
            with contextlib.suppress(OSError):
                self.wallpaper_b64 = base64.b64encode(Path(self.wallpaper_file).read_bytes()).decode("ascii")
        self.dnd = kw["dnd"]
        pc_battery = kw["pc_battery"]
        self.pc_battery = int(pc_battery) if pc_battery is not None else 100
        self.pc_battery_override: int | None = (
            int(pc_battery) if pc_battery is not None and int(pc_battery) != 100 else None
        )
        self.pc_telemetry: dict[str, Any] = collect_pc_telemetry()
        self.phone_telemetry: dict[str, Any] = {}
        self.phone_wallpaper: dict[str, Any] = {"md5": "", "saved": "", "bytes": 0}
        self.uv_swap: bool = True
        self.uv_swap_all: bool = False
        self.usb_link = None
        self.tether = None
        self.mdns_adv = None
        self.mdns_nonce = None
        self.phone_resolved_us = False
        self.moto_adb_serial = None
        self.moto_usb_pid = None
        self.usb_status_msg = "waiting for USB cable + 'Samyuj' sheet tap"
        self._noip_since: dict[str, float] = {}
        self._noip_done: set[str] = set()
        self.ble_scanned_devices: list[dict[str, Any]] = []
        self.tunnel_op3_evt: asyncio.Event | None = None
        self._auto_connecting: bool = False
        self.as_custom_display: bool = True
        self.webcam_output_res: tuple[int, int] | None = None
        self.usb_devices_count: int = 0

    def _init_phone_resolutions_state(self) -> None:
        self.phone_resolutions: dict[str, Any] = {
            "display_service": {
                "queried": False, "type": 1, "mode": 2, "supportedMode": 7,
                "supportOrientation": True,
                "current": {"width": self.desktop_res[0], "height": self.desktop_res[1], "fps": 60.0},
                "supported": [
                    {"width": 2560, "height": 1440, "fps": 60.0},
                    {"width": 1920, "height": 1080, "fps": 60.0},
                    {"width": 1600, "height": 900, "fps": 60.0},
                    {"width": 1280, "height": 720, "fps": 60.0},
                ],
            },
            "desktop": {
                "supportedViews": 7,
                "current": {"width": self.desktop_res[0], "height": self.desktop_res[1]},
                "supported": [],
            },
            "appstream": {
                "phoneNative": {"width": None, "height": None, "density": None, "naviMode": None},
                "stream": {
                    "width": self.desktop_res[0], "height": self.desktop_res[1],
                    "density": self.as_density, "scale": self.as_scale, "displayId": None,
                },
                "customDisplay": True, "rotation": 0, "lastResizeOk": None, "audioState": {},
            },
            "screenshare": {
                "phoneScreen": {"width": None, "height": None},
                "phoneRequested": {"width": self.ss_res[0], "height": self.ss_res[1], "fps": self.ss_fps, "maxHeight": None},
                "active": {"width": self.ss_res[0], "height": self.ss_res[1], "auto": 0},
                "pcSupportedList": list(DEFAULT_SS_RES_LIST),
            },
            "webcam": {
                "cameras": [{"cameraId": 0, "label": "Back Camera"}, {"cameraId": 1, "label": "Front Camera"}],
                "activeCameraId": 0,
                "nativeStream": {"width": 1280, "height": 720, "fps": 30},
                "pcOutput": None,
                "flash": {"ability": 0, "state": 0},
                "zoom": {"ability": 0, "state": 0, "max": 0},
                "angle": {"ability": 0, "state": 0, "states": 0},
                "tracking": {"ability": 0, "state": 0},
            },
        }

    def _init_clipboard_and_files_state(self, *, out_dir: Any, debug: bool) -> None:
        self.clipboard_version: int = 1
        self.clip_last_text: str = ""
        self.pc_clipboard_text: str = ""
        self.clip_seen: list[dict[str, Any]] = []
        self._clip_seq: int = 0
        self.ntf_status: dict[str, Any] = {}
        self.notifications_list: list[dict[str, Any]] = []
        self.input_seen: list[dict[str, Any]] = []
        self.drag_seen: list[dict[str, Any]] = []
        self._drag_token: int = int(time.time() * 1000) % 1000000
        self._xfer_srv: asyncio.AbstractServer | None = None
        self.xfer_port: int = 0
        self.xfer_tasks: dict[str, TransferTask] = {}
        self.active_xfer_task: TransferTask | None = None
        self.recv_files: list[str] = []
        self.received_texts: list[str] = []
        self.xfer_last_status: str = ""
        self.export_dir: Path = (self.out_dir if (out_dir and not debug) else self.state_dir) / "nfs_share"
        self.export_dir.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            readme_p = self.export_dir / "README_SMART_CONNECT_NFS.txt"
            if not readme_p.exists():
                readme_p.write_text(
                    "Samyuj PC NFSv3 Export Root — mounted over TLS/TCP on svc-168.\n",
                    encoding="utf-8",
                )
        self.fs_rw: bool = True
        self.fs_any_peer: bool = True
        self.fs_dt2type: int = 2
        self.fs_create_missing: bool = True
        self.fs_explicit_path: bool = False
        self.fs_ports: dict[str, int] = {"portmap": 0, "mountd": 0, "nfsd": 0}
        self._nfs_srv: NfsRpcServer | None = None
        self._fs_allowed_ips: set[str] = set()
        self.fs_client_fps: dict[str, str] = {}
        self.phone_seen: set[str] = set()
        self.pc_edited: set[str] = set()
        self.mounted_at: float = 0.0
        self.mount_peers: list[str] = []
        self.phonefs: dict[str, Any] = {"state": "idle", "error": "", "ip": "", "path": "/sdcard", "ports": {}}
        self._pfs_client: PhoneNfsClient | None = None
        self._pfs_conn_lock = asyncio.Lock()
        self._pfs_waiter: asyncio.Future[Any] | None = None
        self._last_phonefs_info: dict[str, Any] | None = None
        self.phone_storage: dict[str, Any] = {"free_bytes": 0, "total_bytes": 0}
        self.user_settings: dict[str, Any] = {
            "incoming_files_dir": str(Path.home() / "Downloads" / "Samyuj"),
            "ui_theme": "aurora",
            "color_palette": "mocha",
            "connect_usb": True,
            "connect_wifi": True,
            "start_background": False,
            "follow_system_theme": False,
            "wallpaper_sync": True,
            "apply_desktop_wallpaper": False,
            "cross_control_edge": "left",
            "auto_mount_phone_nfs": True,
            "uv_swap": True,
            "ss_share_type": "mirror",
            "scroll_mode": "touch",
            "scroll_step": 0.12,
            "scroll_step_user_set": False,
            "exclude_battery_saver": True,
            "background_eco_mode": True,
            "background_fps": 10.0,
            "hidden_fps": 1.0,
            "usb_addr_order": "dhcp,static-p2p",
            "extra_phone_vids": [],
            "extra_smartconnect_pkgs": [],
            "demo_mode": False,
        }
        self.load_user_settings()
        self.linux_mount_mgr = PhoneLinuxMountManager(self)
        self.on_wallpaper_cb = None
        self.on_notification_cb = None
        self.on_feature_start_cb = None
        self.on_frame_ready_cb = None
        self.on_messages_updated_cb = None
        with contextlib.suppress(OSError):
            wp_latest = Path(self.state_dir) / "wallpapers" / "phone_wallpaper_latest.png"
            if wp_latest.is_file() and wp_latest.stat().st_size > 64:
                raw_wp = wp_latest.read_bytes()
                self.phone_wallpaper = {
                    "md5": hashlib.md5(raw_wp).hexdigest(),
                    "saved": str(wp_latest),
                    "bytes": len(raw_wp),
                }

    def _init_feature_and_messaging_state(self) -> None:
        self.ble_advertiser: BleAdvertiser | None = None
        self.discovered_phones: list[dict[str, Any]] = []
        self.control_state: dict[str, Any] = {
            "locked": False, "state": 0, "pc_lock_state": {},
            "invite": {"count": 0, "last_ts": 0.0, "last_device_id": "", "last_msg_id": None, "cloud_rejected": 0},
            "last_cmd": None, "os_lock_enabled": True, "last_os_lock_method": "none",
        }
        self.pair_service_state: dict[str, Any] = {
            "connected": False, "pending_devices": [], "connected_devices": [],
            "last_source": "2", "usb_switch_requests": 0, "messages_received": 0,
            "last_msg": None, "last_device_id": "",
        }
        self.phone_file_widgets = PhoneFileWidgetController(self)
        self.gallery_widget_cfg = PhoneFileWidgetConfig(
            widget_id="gallery_svc205", source_id="gallery", custom_title="Gallery",
            remote_path="/DCIM/Camera", view_mode="grid", sort_by="date",
            sort_desc=True, size_mode="expanded",
        )
        xdg_cache_dir = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")) / "samyuj" / "thumbnails"
        with contextlib.suppress(OSError):
            xdg_cache_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(xdg_cache_dir, 0o700)
        self.gallery_thumb_cache_dir: Path = xdg_cache_dir
        self.gallery_state: dict[str, Any] = {
            "active": False, "remote_path": "/DCIM/Camera", "category": 7,
            "status": "idle", "entries_count": 0, "entries": [],
            "thumb_cache_dir": str(xdg_cache_dir), "cached_thumbnails": {}, "last_cmd": None,
        }
        self.features_state: dict[str, Any] = {
            "launchable_sids": list(FEATURES_LAUNCHABLE_SIDS),
            "listeners_1000": [], "last_cmd": None, "last_src_sid": None,
            "launched_history": [], "stopped_history": [],
        }
        xdg_msg_cache = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")) / "samyuj" / "messages"
        with contextlib.suppress(OSError):
            xdg_msg_cache.mkdir(parents=True, exist_ok=True)
            os.chmod(xdg_msg_cache, 0o700)
        self.messages_cache_root: Path = xdg_msg_cache
        self.messages_state: dict[str, Any] = {
            "deviceOs": 1, "deviceType": 1, "verCode": 90030130,
            "granted": False, "enabled": False, "triggered": False, "unread": 0,
            "simCards": [], "selected_sub_id": -1, "contacts": [], "messages": [],
            "threads": {}, "pending_sends": {}, "delivery_reports": [],
            "mms_cache_dir": str(xdg_msg_cache), "cached_mms_files": {},
            "sms_send_available": True, "availability_error": 0, "availability_msg": "",
            "schema_notices": [], "taskbar_badge": {"deviceId": "", "count": 0},
            "last_pid": None, "last_cmd": None, "vue_events": [],
        }
        self.service_bus = ServiceBus(self)

    def _init_media_and_input_controllers(self, **kw: Any) -> None:
        self.as_answer = None
        if self.as_answer_file:
            with contextlib.suppress(OSError):
                self.as_answer = Path(self.as_answer_file).read_text(encoding="utf-8").strip()
        self.media = bool(kw["media"]) and HAVE_MEDIA
        self.sdp_raw = bool(kw["sdp_raw"])
        self.log_secrets = bool(kw["log_secrets"])
        snap_interval = kw["snap_interval"]
        self.snap_interval = 0.0 if float(snap_interval) <= 0.0 else max(0.5, float(snap_interval))
        self.auto_swipe = (kw["auto_swipe"] or "").strip().lower()
        self.input_cap = bool(kw["input_cap"])
        self._pcs: dict[Any, Any] = {}
        self.media_runtime = MediaRuntime(uv_swap=self.uv_swap)
        self.stream_registry = self.media_runtime.stream_registry
        self.settings_model = UserSettings.from_dict(self.user_settings)
        self.use_desktop_thread: bool = bool(
            os.environ.get("SC_DISABLE_DESKTOP_THREAD", "") != "1"
            and "--no-desktop-thread" not in sys.argv
        )
        self.desktop_service_thread: MobileDesktopServiceThread | None = (
            get_mobile_desktop_service_thread() if self.use_desktop_thread else None
        )
        if self.desktop_service_thread is not None:
            self.desktop_service_thread.start()
        self.stream_target_sizes: dict[str, tuple[int, int]] = {}
        self._control_loop: asyncio.AbstractEventLoop | None = None
        self.media_diag = MediaDiagnostics(
            self,
            interval=1.0,
            report_dir=self.state_dir,
            log=lambda evt, msg: self.events.log(evt, msg, feature="media"),
        )
        self.webcam_controller = WebcamServiceController(
            self,
            v4l2_enabled=bool(kw["v4l2"]),
            v4l2_target=str(kw["v4l2_device"] or "auto"),
            v4l2_format=str(kw["v4l2_format"] or "YU12"),
            v4l2_fps=float(kw["v4l2_fps"] or 30.0),
            v4l2_auto=bool(kw["v4l2_auto"]),
        )
        self.clip_sync = ClipboardSyncManager(self, enabled=bool(kw["clipboard_sync"]))
        self.clip_sync.load_pref()
        self.prefer_usb = bool(kw["prefer_usb"])
        self.appstream_source = str(kw["appstream_source"] or "auto")
        self._media_codec_from_cli = False
        self.media_codec_pref = str(kw["media_codec_pref"] or "h264").lower()
        self.desktop_fallback = str(kw["desktop_fallback"] or "prompt").lower()
        self.desktop_fallback_retries = 2
        self.on_desktop_waiting_cb = None
        self.present_engine = str(kw["present_engine"] or "gtk").lower()
        self.present_sink = str(kw["present_sink"] or "auto").lower()
        self._presenter_bridge = None
        self._presenter_sid = None
        self._presenter_stats: dict[str, Any] = {}
        self.desktop_rotation = 0
        self.desktop_autostart: bool = bool(self.user_settings.get("phone_at_a_glance", False))
        self._desktop_res_from_cli = False
        self._restore_saved_geometry()
        self._resize_waiters: dict[Any, Any] = {}
        self.input_stats: dict[str, int] = {
            "moves_sent": 0, "moves_dropped_busy": 0, "moves_suppressed_dup": 0,
            "press": 0, "release": 0, "pli_during_drag": 0, "drags_skipped_overlap": 0,
            "pli_suppressed_congestion": 0, "wheel_sent": 0, "mouse_moves_sent": 0,
            "mouse_buttons_sent": 0, "keys_sent": 0, "text_committed": 0, "touch_cancels_sent": 0,
        }
        self.input_state: dict[str, Any] = {
            "input_mode": 0, "touch_mode": 0, "touch_supported": True,
            "ime_visible": False, "caps_by_display": {}, "cursor_icon": None,
        }
        _sm_env = str(os.environ.get("SC_SCROLL_MODE", "")).strip().lower()
        _sm_saved = str(self.user_settings.get("scroll_mode", "") or "").strip().lower()
        self.stream_scroll_mode: str = (
            _sm_env if _sm_env in ("touch", "wheel", "hybrid")
            else (_sm_saved if _sm_saved in ("touch", "wheel", "hybrid") else "touch")
        )
        try:
            _saved_ss = self.user_settings.get("scroll_step")
            if _saved_ss is not None and abs(float(_saved_ss) - 0.055) < 1e-6 and not self.user_settings.get("scroll_step_user_set"):
                _saved_ss = 0.12
                self.user_settings["scroll_step"] = 0.12
            _sst = float(os.environ.get("SC_SCROLL_STEP", "") or _saved_ss or 0.12)
        except (TypeError, ValueError):
            _sst = 0.12
        self.stream_scroll_step: float = min(0.5, max(0.005, _sst))
        with contextlib.suppress(Exception):
            from samyuj.media.enhance import set_lowlight_config
            set_lowlight_config(
                enabled=bool(self.user_settings.get("lowlight_boost", True)),
                gamma_max=float(self.user_settings.get("lowlight_gamma_max", 1.9) or 1.9),
                enter_luma=float(self.user_settings.get("lowlight_luma_threshold", 84.0) or 84.0),
            )
        with contextlib.suppress(Exception):
            from samyuj.ui.gtk.smooth_scroll import set_smooth_scroll_enabled
            set_smooth_scroll_enabled(bool(self.user_settings.get("smooth_scrolling", True)))
        self.power_mgr = PowerAndRefreshManager(
            self,
            exclude_battery_saver=bool(self.user_settings.get("exclude_battery_saver", True)),
            background_eco_mode=bool(self.user_settings.get("background_eco_mode", True)),
            background_fps=float(self.user_settings.get("background_fps", 10.0) or 10.0),
            hidden_fps=float(self.user_settings.get("hidden_fps", 1.0) or 1.0),
        )
        self.stream_mouse_hover: bool = os.environ.get("SC_MOUSE_HOVER", "0") == "1"
        self._drag_inflight = False
        self._last_pli_suppress_log = 0.0
        self.pli_loss_gate_pct = 3.0
        self._last_drag_pli_mono: float = 0.0
        self._last_touch_xy: tuple[int, int] | None = None
        self._last_mouse_xy: tuple[int, int] | None = None
        self._last_input_stats_log: float = 0.0
        self.input_send_budget = 0.0
        self.as_status: dict[str, Any] = {"phase": "idle", "pkg": "", "sid": None, "detail": "", "updated": 0.0}
        self.qr_payload: str = ""
        self._usb_takeover: dict[str, Any] | None = None
        self._adb_trigger_done_for_link = False
        self.usb_switch_timeout_s = 10.0
        self._last_transport_sig: str = ""
        self.uc_state: dict[str, Any] = {
            "connected": False, "phase": "idle", "cid": -1, "phone_w": 0, "phone_h": 0,
            "support_touch": False, "trigger_used": None, "retried_dialog": False,
            "pointer_shown": False, "detail": "",
        }
        self.uc_cid_counter = int(time.time()) % 100000
        self.usb_config_manager = UsbConfigManager()
        self.reachability_checker = PortReachabilityChecker(self)
        self._snap_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="sc-snap")
        self._snap_pending: set[str] = set()
        self.media_stats: dict[str, Any] = {
            "frames": 0, "snapshots": 0, "states": [],
            "candidates_in": 0, "candidates_out": 0, "sessions": [],
        }
        self._fs = None
        self.token_b64 = base64.b64encode(os.urandom(16)).decode("ascii")
        self.device_id_b64 = base64.b64encode(os.urandom(16)).decode("ascii")
        self.fp_b64 = ""
        self.close_records: list[dict[str, Any]] = []

    @staticmethod
    def _guess_sku() -> str:
        for p in ("/sys/class/dmi/id/product_name", "/sys/class/dmi/id/board_name"):
            with contextlib.suppress(Exception):
                v = Path(p).read_text().strip()
                if v:
                    return v
        return "LinuxPC"

    def next_msg_id(self) -> int:
        self._msg_id = (self._msg_id + 1) % 2147483647
        return self._msg_id

    def get_pc_telemetry_snapshot(self) -> dict[str, Any]:
        snap = collect_pc_telemetry()
        if getattr(self, "pc_battery_override", None) is not None:
            snap["battery"] = int(self.pc_battery_override)
        elif getattr(self, "pc_battery", 100) != 100:
            snap["battery"] = int(self.pc_battery)
        if getattr(self, "dnd", None) is not None:
            snap["dndState"] = 1 if self.dnd else 0
        self.pc_telemetry = snap
        self.pc_battery = int(snap.get("battery", 100))
        return snap

    def __getattr__(self, name: str) -> Any:
        reg = object.__getattribute__(self, "_component_registry")
        return reg.resolve(name)
