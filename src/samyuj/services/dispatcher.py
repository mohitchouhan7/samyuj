from __future__ import annotations
import ssl
from samyuj.platform.linux.adb import probe_phone_ip
from samyuj.protocol.constants import DISPLAY_TYPE_REMOTE
from samyuj.protocol.envelope import _env
from samyuj.protocol.services.files import gen_task_id
from samyuj.transport.connection import _boost_pc_socket_buffers
from samyuj.transport.discovery import BleAdvertiser, UsbMdnsAdvertiser, usb_device_snapshot
import datetime
from samyuj.core.events import _console_show, feature_of_tag as _feature_of_tag

import asyncio
import base64
import contextlib
import json
import time
from pathlib import Path

from samyuj.platform.linux.adb import adb_trigger_phone
from samyuj.platform.linux.setup import apply_udp_buffer_sysctl
from samyuj.protocol.constants import (
    APPSTREAM_SOURCE_DESKTOP,
    APPSTREAM_SOURCE_PC_APP,
    DESKTOP_VIEW_DESKTOP,
    DESKTOP_VIEW_MIRROR,
    DESKTOP_VIEW_MOBILE,
    DISPLAY_MODE_DESKTOP,
    DISPLAY_MODE_MIRROR,
    DISPLAY_MODE_MOBILE,
)
from samyuj.protocol.services import (
    build_ai_capabilities,
    build_app_blacklist,
    build_applist_request,
    build_appstream_app_merge,
    build_appstream_applist_sync,
    build_appstream_focus_changed,
    build_appstream_move_monitor,
    build_appstream_move_to_top,
    build_appstream_pause,
    build_appstream_resize,
    build_appstream_resume,
    build_appstream_start,
    build_checkin,
    build_clipboard_nego,
    build_clipboard_pull,
    build_clipboard_support_files,
    build_clipboard_synced,
    build_clipboard_update,
    build_desktop_autostart,
    build_desktop_lock,
    build_desktop_request,
    build_desktop_set_resolution,
    build_desktop_switch_view,
    build_dialog_dismiss,
    build_display_query,
    build_display_set_mode,
    build_display_set_resolution,
    build_dnd_sync,
    build_drag_data,
    build_drag_enter,
    build_drop_data,
    build_exchange_key,
    build_features_request,
    build_fileex_text_share,
    build_gallery_frame,
    build_input_key,
    build_input_text,
    build_msg_mark_read,
    build_msg_notify_ready,
    build_msg_permission_request,
    build_msg_request_contacts,
    build_msg_request_messages,
    build_msg_request_sim_cards,
    build_ntf_block_pkg,
    build_ntf_open_settings,
    build_ntf_pc_msg,
    build_pair_frame,
    build_pc_telemetry,
    build_rdp_client_rotation,
    build_reply_json,
    build_runningapps_request,
    build_ss_audio,
    build_ss_reconnect,
    build_ss_resolution,
    build_ss_scale,
    build_ss_switch,
    build_uc_show_pointer,
    build_wallpaper_push,
    build_wallpaper_request,
    build_wallpaper_settings,
    build_webcam_camera_query,
    build_webcam_pause,
    build_webcam_resume,
    build_webcam_set_angle,
    build_webcam_set_mute,
    build_webcam_set_tracking,
    build_webcam_set_zoom,
    build_webcam_switch_camera,
    build_webcam_toggle_flash,
)
from samyuj.transport.discovery import find_tether_link, net_snapshot
from samyuj.transport.websocket import ws_frame

class RequestDispatcherMixin:

    _RQ_ORDER: tuple[str, ...] = ()

    def _rq_sort(self, names: list[str]) -> list[str]:
        def rank(n: str) -> int:
            base = n.split(":", 1)[0]
            try:
                return self._RQ_ORDER.index(base)
            except ValueError:
                return len(self._RQ_ORDER)
        return sorted(names, key=rank)

    LOCAL_COMMAND_ROUTES: dict[str, str] = {       'adb-trigger': '_rq_local_adb_trigger',
        'ble-adv-toggle': '_rq_local_ble_adv_toggle',
        'ble-push': '_rq_local_ble_push',
        'ble-scan': '_rq_local_ble_scan',
        'fix-udp-buf': '_rq_local_fix_udp_buf',
        'forget-key': '_rq_local_forget_key',
        'gallery-list': '_rq_local_gallery_list',
        'gallery-refresh': '_rq_local_gallery_list',
        'gallery-thumb': '_rq_local_gallery_thumb',
        'gallery-thumbnail': '_rq_local_gallery_thumb',
        'messages-select-sim': '_rq_local_messages_select_sim',
        'pair-audit': '_rq_local_pair_audit',
        'pair-list': '_rq_local_pair_list',
        'scroll-mode': '_rq_local_scroll_mode',
        'scroll-step': '_rq_local_scroll_step',
        'stream-scroll-mode': '_rq_local_scroll_mode',
        'stream-scroll-step': '_rq_local_scroll_step',
        'sysctl-udp': '_rq_local_fix_udp_buf',
        'udp-buf': '_rq_local_fix_udp_buf',
        'usb-bootstrap': '_rq_local_usb_bootstrap',
        'usb-scan': '_rq_local_usb_scan',
        'uv-swap': '_rq_local_uv_swap',
        'uvswap': '_rq_local_uv_swap',
        'webcam-res': '_rq_local_webcam_res',
        'wifi-autoconnect': '_rq_local_wifi_autoconnect'}

    ONLINE_COMMAND_ROUTES: dict[str, str] = {       'aicaps': '_rq_cmd_aicaps',
        'app': '_rq_cmd_appstream',
        'applist': '_rq_cmd_applist',
        'applist-sync': '_rq_cmd_as_applist_sync',
        'apps': '_rq_cmd_applist',
        'appstream': '_rq_cmd_appstream',
        'appstream-focus': '_rq_cmd_as_focus',
        'appstream-merge': '_rq_cmd_as_merge',
        'appstream-pause': '_rq_cmd_appstream_pause',
        'appstream-resize': '_rq_cmd_appstream_resize',
        'appstream-resume': '_rq_cmd_appstream_resume',
        'appstream-top': '_rq_cmd_as_top',
        'as': '_rq_cmd_appstream',
        'as-applist-sync': '_rq_cmd_as_applist_sync',
        'as-focus': '_rq_cmd_as_focus',
        'as-merge': '_rq_cmd_as_merge',
        'as-move-monitor': '_rq_cmd_as_move_monitor',
        'as-pause': '_rq_cmd_appstream_pause',
        'as-resize': '_rq_cmd_appstream_resize',
        'as-resume': '_rq_cmd_appstream_resume',
        'as-top': '_rq_cmd_as_top',
        'autostart': '_rq_cmd_desktop_autostart',
        'blacklist': '_rq_cmd_blacklist',
        'camera': '_rq_cmd_webcam',
        'camera-switch': '_rq_cmd_webcam_switch',
        'cameras': '_rq_cmd_webcam_cameras',
        'change-display-view': '_rq_cmd_desktop_view',
        'checkin': '_rq_cmd_checkin',
        'clip-sync': '_rq_cmd_clipboard_sync',
        'clipboard': '_rq_cmd_clipboard',
        'clipboard-files': '_rq_cmd_clipboard_files',
        'clipboard-nego': '_rq_cmd_clipboard_nego',
        'clipboard-pull': '_rq_cmd_clipboard_pull',
        'clipboard-sync': '_rq_cmd_clipboard_sync',
        'cmd130': '_rq_cmd_desktop_view',
        'control-invite': '_rq_cmd_control_invite',
        'control-lock': '_rq_cmd_control_lock_state',
        'control-lock-state': '_rq_cmd_control_lock_state',
        'control-unlock': '_rq_cmd_control_lock_state',
        'cross-control': '_rq_cmd_crosscontrol',
        'crosscontrol': '_rq_cmd_crosscontrol',
        'crosscontrol-pointer': '_rq_cmd_uc_pointer',
        'crosscontrol-stop': '_rq_cmd_uc_stop',
        'desktop': '_rq_cmd_mobiledesktop',
        'desktop-autostart': '_rq_cmd_desktop_autostart',
        'desktop-res': '_rq_cmd_desktop_res',
        'desktop-rotate': '_rq_cmd_desktop_rotate',
        'desktop-rotation': '_rq_cmd_desktop_rotation',
        'desktop-view': '_rq_cmd_desktop_view',
        'devstatus': '_rq_cmd_telemetry',
        'diag': '_rq_cmd_media_diag',
        'dialog-dismiss': '_rq_cmd_dialog_dismiss',
        'dismiss': '_rq_cmd_dialog_dismiss',
        'display-mode': '_rq_cmd_display_mode',
        'display-query': '_rq_cmd_res_query',
        'display-res': '_rq_cmd_display_res',
        'dnd': '_rq_cmd_dnd',
        'dnd-off': '_rq_cmd_dnd',
        'dnd-on': '_rq_cmd_dnd',
        'drag-enter': '_rq_cmd_drag_enter',
        'drag-text': '_rq_cmd_drag_text',
        'drop': '_rq_cmd_drop',
        'exchange-key': '_rq_cmd_exchange_key',
        'features-launch': '_rq_cmd_features_launch',
        'features-stop': '_rq_cmd_features_stop',
        'features-streaming-list': '_rq_cmd_features_streaming_list',
        'filesend': '_rq_cmd_filesend',
        'fileshare-text': '_rq_cmd_fileshare_text',
        'gallery': '_rq_cmd_gallery_open',
        'gallery-open': '_rq_cmd_gallery_open',
        'icons': '_rq_cmd_applist',
        'input-mode': '_rq_cmd_input_mode',
        'keyevent': '_rq_cmd_keyevent',
        'lock': '_rq_cmd_lock',
        'md': '_rq_cmd_mobiledesktop',
        'media-diag': '_rq_cmd_media_diag',
        'mediadiag': '_rq_cmd_media_diag',
        'messages': '_rq_cmd_messages_sync',
        'messages-contacts': '_rq_cmd_messages_contacts',
        'messages-list': '_rq_cmd_messages_list',
        'messages-mms': '_rq_cmd_messages_mms',
        'messages-permission': '_rq_cmd_messages_permission',
        'messages-read': '_rq_cmd_messages_read',
        'messages-search': '_rq_cmd_messages_search',
        'messages-send': '_rq_cmd_messages_send',
        'messages-sims': '_rq_cmd_messages_sims',
        'messages-sync': '_rq_cmd_messages_sync',
        'mobiledesktop': '_rq_cmd_mobiledesktop',
        'move-monitor': '_rq_cmd_as_move_monitor',
        'move-to-top': '_rq_cmd_as_top',
        'msg-attachment': '_rq_cmd_messages_mms',
        'msg-contacts': '_rq_cmd_messages_contacts',
        'msg-list': '_rq_cmd_messages_list',
        'msg-permission': '_rq_cmd_messages_permission',
        'msg-read': '_rq_cmd_messages_read',
        'msg-search': '_rq_cmd_messages_search',
        'msg-send': '_rq_cmd_messages_send',
        'msg-sims': '_rq_cmd_messages_sims',
        'msg-sync': '_rq_cmd_messages_sync',
        'nfs-announce': '_rq_cmd_nfs_announce',
        'nfs-mount': '_rq_cmd_nfs_mount',
        'ntf-action': '_rq_cmd_ntf_action',
        'ntf-block': '_rq_cmd_ntf_block',
        'ntf-click': '_rq_cmd_ntf_click',
        'ntf-dismiss': '_rq_cmd_ntf_dismiss',
        'ntf-reply': '_rq_cmd_ntf_reply',
        'ntf-settings': '_rq_cmd_ntf_settings',
        'pair-device': '_rq_cmd_pair_device',
        'pair-handshake': '_rq_cmd_pair_device',
        'phone-at-a-glance': '_rq_cmd_desktop_autostart',
        'ping': '_rq_cmd_ping',
        'prefer-usb': '_rq_cmd_transport_prefer_usb',
        'res-query': '_rq_cmd_res_query',
        'rotate': '_rq_cmd_desktop_rotate',
        'running': '_rq_cmd_runningapps',
        'runningapps': '_rq_cmd_runningapps',
        'screenshare': '_rq_cmd_screenshare',
        'screenshare-rotate': '_rq_cmd_ss_rotate',
        'set-input-mode': '_rq_cmd_input_mode',
        'share': '_rq_cmd_screenshare',
        'ss': '_rq_cmd_screenshare',
        'ss-audio': '_rq_cmd_ss_audio',
        'ss-extend-status': '_rq_cmd_ss_virtual',
        'ss-mode': '_rq_cmd_ss_mode',
        'ss-reconnect': '_rq_cmd_ss_reconnect',
        'ss-res': '_rq_cmd_ss_res',
        'ss-rotate': '_rq_cmd_ss_rotate',
        'ss-scale': '_rq_cmd_ss_scale',
        'ss-virtual': '_rq_cmd_ss_virtual',
        'status': '_rq_cmd_telemetry',
        'stop': '_rq_cmd_stop',
        'stop-all': '_rq_cmd_stop',
        'stopall': '_rq_cmd_stop',
        'telemetry': '_rq_cmd_telemetry',
        'text': '_rq_cmd_text',
        'transport-prefer-usb': '_rq_cmd_transport_prefer_usb',
        'transport-retry': '_rq_cmd_transport_retry',
        'discovery-retry': '_rq_cmd_transport_retry',
        'rescan': '_rq_cmd_transport_retry',
        'uc-pointer': '_rq_cmd_uc_pointer',
        'uc-start': '_rq_cmd_crosscontrol',
        'uc-stop': '_rq_cmd_uc_stop',
        'uv-swap': '_rq_cmd_uv_swap',
        'uvswap': '_rq_cmd_uv_swap',
        'v4l2-setup': '_rq_cmd_v4l2_setup',
        'vault-query': '_rq_cmd_vault_query',
        'view': '_rq_cmd_desktop_view',
        'virtual-display': '_rq_cmd_ss_virtual',
        'wallpaper': '_rq_cmd_wallpaper_push',
        'wallpaper-pull': '_rq_cmd_wallpaper_pull',
        'wallpaper-push': '_rq_cmd_wallpaper_push',
        'wallpaper-settings': '_rq_cmd_wallpaper_settings',
        'wc': '_rq_cmd_webcam',
        'webcam': '_rq_cmd_webcam',
        'webcam-angle': '_rq_cmd_webcam_angle',
        'webcam-cameras': '_rq_cmd_webcam_cameras',
        'webcam-flash': '_rq_cmd_webcam_flash',
        'webcam-flip': '_rq_cmd_webcam_flip',
        'webcam-mute': '_rq_cmd_webcam_mute',
        'webcam-pause': '_rq_cmd_webcam_pause',
        'webcam-res': '_rq_cmd_webcam_res',
        'webcam-resume': '_rq_cmd_webcam_resume',
        'webcam-stop': '_rq_cmd_webcam_stop',
        'webcam-switch': '_rq_cmd_webcam_switch',
        'webcam-tracking': '_rq_cmd_webcam_tracking',
        'webcam-zoom': '_rq_cmd_webcam_zoom',
        'wheel': '_rq_cmd_wheel',
        'wp-pull': '_rq_cmd_wallpaper_pull',
        'wp-push': '_rq_cmd_wallpaper_push',
        'wp-ui': '_rq_cmd_wallpaper_settings'}

    async def request(self, name: str, conn: dict | None = None, arg: str = "") -> dict:
        c = conn or self._live_ws()
        base, _, extra = (name or "").partition(":")
        base = base.strip().lower()
        extra = (extra or arg or "").strip()
        rec: dict = {
            "name": base,
            "arg": extra,
            "utc": datetime.datetime.now(datetime.UTC).isoformat(),
            "ok": False,
            "detail": "",
        }

        if base in ("messages-search", "msg-search") and c is None:
            q_str = str(extra or "").strip().lower()
            all_msgs = list(self.messages_state.get("messages") or [])
            matches = [
                m for m in all_msgs
                if not q_str
                or q_str in str(m.get("body") or "").lower()
                or q_str in str(m.get("fromAddress") or "").lower()
                or any(q_str in str(a).lower() for a in (m.get("toAddress") or []))
            ]
            rec.update(ok=True, count=len(matches), detail=f"matched {len(matches)} cached message(s) for {extra!r}")
            self.rq_log.append(rec)
            return rec

        local_handler_name = self.LOCAL_COMMAND_ROUTES.get(base)
        if local_handler_name is not None:
            res = await getattr(self, local_handler_name)(name, base, extra, c, rec)
            if res is not None:
                return res

        if c is None:
            rec["detail"] = "no live phone connection (pair first)"
            self.events.log("RQ_FAIL", rec["detail"], feature="core")
            self.rq_log.append(rec)
            return rec

        try:
            online_handler_name = self.ONLINE_COMMAND_ROUTES.get(base)
            if online_handler_name is not None:
                await getattr(self, online_handler_name)(name, base, extra, c, rec)
            else:
                rec["detail"] = f"unknown request {name!r}"
                self.events.log("RQ_UNKNOWN", rec["detail"], feature="core")
        except Exception as exc:
            rec["error"] = repr(exc)
            self.events.log("RQ_ERR", f"{name}: {exc!r}")
        self.rq_log.append(rec)
        return rec

    async def _rq_local_webcam_res(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        if extra.lower() in ("native", "auto", "off", "0x0", ""):
            self.webcam_output_res = None
            rec.update(ok=True, detail="webcam PC output rescaling reset to native")
        else:
            with contextlib.suppress(ValueError):
                w_, h_ = (max(160, int(x)) for x in extra.lower().split("x", 1))
                self.webcam_output_res = (w_, h_)
            rec.update(ok=True, detail=f"webcam PC output rescaling set to {self.webcam_output_res}")
        self.rq_log.append(rec)
        return rec
        return None

    async def _rq_local_scroll_step(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        try:
            _sv = float(extra.strip())
            if not (0.005 <= _sv <= 0.5):
                raise ValueError(_sv)
            self.stream_scroll_step = _sv
            with contextlib.suppress(Exception):
                self.save_user_settings(scroll_step=_sv, scroll_step_user_set=True)
            rec.update(ok=True,
                       detail=f"stream_scroll_step={_sv} of screen height per wheel notch (persisted)")
        except (TypeError, ValueError):
            rec.update(ok=False,
                       detail="usage: !rq scroll-step <0.005-0.5> (default 0.055; old page-fling was 0.22)")
        self.rq_log.append(rec)
        return rec
        return None

    async def _rq_local_scroll_mode(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        _sm = (extra.strip().lower() or "touch")
        if _sm in ("touch", "wheel", "hybrid"):
            self.stream_scroll_mode = _sm
            with contextlib.suppress(Exception):
                self.save_user_settings(scroll_mode=_sm)
            rec.update(ok=True, detail=f"stream_scroll_mode={self.stream_scroll_mode} (persisted)")
        else:
            rec.update(ok=False, detail="usage: !rq scroll-mode <touch|wheel|hybrid>")
        self.rq_log.append(rec)
        return rec
        return None

    async def _rq_local_uv_swap(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        if extra.lower() in ("on", "1", "true"):
            self.set_uv_swap(True)
        elif extra.lower() in ("off", "0", "false"):
            self.set_uv_swap(False)
        else:
            self.set_uv_swap(None)
        rec.update(ok=True, detail=f"webcam uv_swap={self.uv_swap} (persisted + cached frame flipped)")
        self.rq_log.append(rec)
        return rec
        return None

    async def _rq_local_usb_bootstrap(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        n = 0
        for tc in self._conns:
            if not tc.get("closed") and tc.get("tunnel"):
                if await self._send_tunnel_bootstrap(tc, why="rq"):
                    n += 1
        rec.update(ok=(n > 0), detail=f"sent cmd:1 bootstrap on {n} tunnel connection(s)" if n else "no active USB tunnel connection")
        self.rq_log.append(rec)
        return rec
        return None

    async def _rq_local_fix_udp_buf(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        target_b = 8 * 1024 * 1024
        if extra.isdigit():
            target_b = max(4 * 1024 * 1024, int(extra) * 1024)
        res_u = await asyncio.to_thread(apply_udp_buffer_sysctl, target_b, True)
        for _pc in list((getattr(self, "_pcs", {}) or {}).values()):
            with contextlib.suppress(Exception):
                await asyncio.to_thread(_boost_pc_socket_buffers, _pc)
        rec.update(
            ok=bool(res_u.get("ok")),
            detail=(f"rmem_max {res_u.get('rmem_max_before')} -> {res_u.get('rmem_max_after')} "
                    f"({res_u.get('method')}): {res_u.get('hint')}"),
        )
        self.rq_log.append(rec)
        return rec
        return None

    async def _rq_local_pair_list(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        saved_p = self.list_saved_phones()
        rec.update(ok=True, paired_phones=saved_p, detail=f"{len(saved_p)} paired phone(s) in paired_phones.json")
        self.rq_log.append(rec)
        return rec
        return None

    async def _rq_local_pair_audit(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        aud = self.audit_src_service_ids()
        rec.update(ok=True, audit=aud, detail=f"audited {len(aud['audited_routes'])} service routes")
        self.rq_log.append(rec)
        return rec
        return None

    async def _rq_local_gallery_list(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        rpath = extra or "/DCIM/Camera"
        res_g = await self.refresh_gallery_widget(remote_path=rpath)
        rec.update(ok=bool(res_g.get("ok") or res_g.get("status") in ("ready", "unavailable")),
                   status=res_g.get("status"), entries=res_g.get("entries", []),
                   detail=f"gallery {res_g.get('remote_path')}: status={res_g.get('status')} ({len(res_g.get('entries', []))} items)")
        self.rq_log.append(rec)
        return rec
        return None

    async def _rq_local_gallery_thumb(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        rpath = extra or "/DCIM/Camera/IMG_0001.jpg"
        res_t = await self.fetch_gallery_thumbnail(rpath)
        rec.update(ok=bool(res_t.get("ok")), cache_file=res_t.get("cache_file"),
                   detail=res_t.get("cache_file") or res_t.get("error", ""))
        self.rq_log.append(rec)
        return rec
        return None

    async def _rq_local_messages_select_sim(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        sub_id = int(extra) if extra.lstrip("-").isdigit() else -1
        self.messages_state["selected_sub_id"] = sub_id
        rec.update(ok=True, selected_sub_id=sub_id, detail=f"selected SIM subId={sub_id}")
        self.rq_log.append(rec)
        return rec
        return None

    async def _rq_local_forget_key(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        self._forget_shared_key()
        rec.update(ok=True, detail="cleared stored shared_key.txt & paired_phones.json keys (ready for fresh re-pair)")
        self.rq_log.append(rec)
        return rec
        return None

    async def _rq_local_adb_trigger(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        ok_adb, msg_adb = await adb_trigger_phone(self.moto_adb_serial)
        self.events.log("USB_ADB_TRIGGER", f"ok={ok_adb} {msg_adb}")
        rec.update(ok=ok_adb, detail=msg_adb)
        self.rq_log.append(rec)
        return rec
        return None

    async def _rq_local_usb_scan(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        cur_usb, cur_net = await asyncio.to_thread(lambda: (usb_device_snapshot(), net_snapshot()))
        self.usb_devices_count = len(cur_usb)
        link = find_tether_link(cur_net)
        if link:
            iface, ip = link
            self.lan_ip = ip
            self.usb_link = link
            ph_ip = await asyncio.to_thread(probe_phone_ip, iface, ip)
            self.tether = {"iface": iface, "pc_ip": ip, "phone_ip": ph_ip}
            self.usb_status_msg = f"USB link active on {iface} ({ip}) -> phone={ph_ip}"
            if self.mdns_adv and self.mdns_adv.ok:
                self.mdns_adv.ensure_membership(ip)
                self.mdns_adv.announce_now()
            else:
                if self.mdns_adv:
                    await self.mdns_adv.stop()
                self.mdns_adv = UsbMdnsAdvertiser(self)
                await self.mdns_adv.start()
        cands = self._all_ipv4s()
        rec.update(ok=True, detail=f"usb_link={self.usb_link} tether={self.tether} moto={self.moto_usb_pid} status={self.usb_status_msg} local_IPv4s={cands}")
        self.rq_log.append(rec)
        return rec
        return None

    async def _rq_local_ble_scan(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        devs = await self.ble_scan_devices(window=5.0)
        rec.update(ok=bool(devs),
                   detail=(f"Found {len(devs)} BLE 0x5246 phone(s): " + ", ".join(f"{d['mac']} ({d.get('name') or '0x5246'})" for d in devs))
                   if devs else "No 0x5246 BLE adverts seen (ensure phone Bluetooth is ON, screen unlocked, Smart Connect open, and bleak installed)")
        self.rq_log.append(rec)
        return rec
        return None

    async def _rq_local_ble_adv_toggle(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        if self.ble_advertiser and self.ble_advertiser.ok:
            await self.ble_advertiser.stop()
            self.ble_advertiser = None
            rec.update(ok=True, detail="BLE 0x5246 presence advertiser stopped")
        else:
            self.ble_advertiser = BleAdvertiser(self)
            ok_ble = await self.ble_advertiser.start()
            rec.update(ok=ok_ble, detail="BLE 0x5246 presence advertiser registered" if ok_ble else "bluetoothctl/btmgmt failed or unavailable (Wi-Fi/USB unaffected)")
        self.rq_log.append(rec)
        return rec
        return None

    async def _rq_local_wifi_autoconnect(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        ok_wf, msg_wf = await self.wifi_tunnel_push(extra)
        rec.update(ok=ok_wf, detail=msg_wf)
        self.rq_log.append(rec)
        return rec
        return None

    async def _rq_local_ble_push(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        ok_bp, msg_bp = await self.ble_gatt_push(extra)
        rec.update(ok=ok_bp, detail=msg_bp)
        self.rq_log.append(rec)
        return rec
        return None

    async def _rq_cmd_telemetry(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        snap = self.get_pc_telemetry_snapshot()
        if extra and extra.isdigit():
            self.pc_battery_override = max(0, min(100, int(extra)))
            snap = self.get_pc_telemetry_snapshot()
        ok = await self._send_ws_text(
            c, build_pc_telemetry(
                self,
                battery=snap.get("battery", self.pc_battery),
                charging=snap.get("isCharging", True),
                dnd=snap.get("dndState", (1 if self.dnd else 0)),
                wallpaper_b64=self.wallpaper_b64),
            "RQ_TELEMETRY_SENT(svc25 cmd1)")
        rec.update(ok=ok, detail=f"cap={len(self.pc_caps)} sids, "
                                 f"battery={snap.get('battery')}% "
                                 f"charging={snap.get('isCharging')} "
                                 f"cpu={snap.get('cpuPercent')}% "
                                 f"ram={snap.get('memPercent')}%")
        return None

    async def _rq_cmd_aicaps(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        caps = json.loads(extra) if extra else []
        ok = await self._send_ws_text(c, build_ai_capabilities(self, caps),
                                      "RQ_AICAPS_SENT(svc25 cmd20)")
        rec.update(ok=ok, detail=f"{len(caps)} AI capability item(s)")
        return None

    async def _rq_cmd_dnd(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        on = (base == "dnd-on") if base != "dnd" else extra.lower() in ("1", "on", "true", "yes")
        ok = await self._send_ws_text(c, build_dnd_sync(self, on),
                                      f"RQ_DND_SENT(svc25 cmd6,{on})")
        rec.update(ok=ok, detail=f"dndState={1 if on else 0}")
        return None

    async def _rq_cmd_wallpaper_push(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        b64 = self.wallpaper_b64
        if extra and Path(extra).is_file():
            b64 = base64.b64encode(Path(extra).read_bytes()).decode()
        if not b64:
            rec["detail"] = ("no wallpaper to push — pass --wallpaper FILE "
                             "or !rq wallpaper-push /path/to.png")
            self.events.log("RQ_FAIL", rec["detail"], feature="wallpaper")
            self.rq_log.append(rec)
            return rec
        ok = await self._send_ws_text(c, build_wallpaper_push(self, b64),
                                      "RQ_WALLPAPER_PUSH_SENT(svc25 cmd2)")
        rec.update(ok=ok, detail=f"{len(base64.b64decode(b64))} bytes of PNG "
                                 f"— the phone saves it as its Smart Connect "
                                 f"wallpaper")
        return None

    async def _rq_cmd_wallpaper_pull(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        ok = await self._send_ws_text(c, build_wallpaper_request(self),
                                      "RQ_WALLPAPER_REQUEST_SENT(svc25 cmd3)")
        rec.update(ok=ok, detail="the phone should answer svc-25 cmd:2 "
                                 "{md5,wallpaper}; if nothing arrives its "
                                 "wallpaper-sync gate is closed — "
                                 "see WALLPAPER_RECV_EMPTY")
        return None

    async def _rq_cmd_wallpaper_settings(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        ok = await self._send_ws_text(c, build_wallpaper_settings(self),
                                      "RQ_WALLPAPER_UI_SENT(svc25 cmd4)")
        rec.update(ok=ok, detail="the phone should open its wallpaper-sync screen")
        return None

    async def _rq_cmd_applist(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        ok = await self._send_ws_text(c, build_applist_request(self),
                                      "RQ_APPLIST_REQUEST_SENT(svc17 cmd2)")
        rec.update(ok=ok, detail="the phone regenerates its app-icon cache "
                                 "and answers svc-17 cmd:1; "
                                 "icons are then pulled from its TLS port "
                                 "(icon regeneration self-throttles to ~1 call / 6 s)")
        return None

    async def _rq_cmd_runningapps(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        ok = await self._send_ws_text(c, build_runningapps_request(self),
                                      "RQ_RUNNINGAPPS_SENT(svc17 cmd5)")
        rec.update(ok=ok, detail="expect the svc-17 cmd:6 running-apps reply")
        return None

    async def _rq_cmd_blacklist(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        pkgs = [p for p in extra.replace(",", " ").split() if p]
        ok = await self._send_ws_text(c, build_app_blacklist(self, pkgs),
                                      "RQ_BLACKLIST_SENT(svc17 cmd4)")
        rec.update(ok=ok, detail=f"blacklist={pkgs}")
        return None

    async def _rq_cmd_desktop_autostart(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        arg_as = extra.strip().lower()
        on_as = arg_as in ("1", "on", "true", "yes") if arg_as else (not bool(getattr(self, "desktop_autostart", False)))
        self.desktop_autostart = on_as
        with contextlib.suppress(Exception):
            self.save_user_settings(phone_at_a_glance=on_as)
        ok = await self._send_ws_text(
            c, build_desktop_autostart(self, on_as),
            f"RQ_DESKTOP_AUTOSTART_SENT(svc1 cmd36,autoStart={on_as})")
        rec.update(ok=ok, detail=f"svc-1 cmd:36 autoStart={on_as}")
        return None

    async def _rq_cmd_mobiledesktop(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        view = DESKTOP_VIEW_DESKTOP
        extra_low = extra.strip().lower()
        v_tok, _, res_tok = extra_low.partition(":")
        if v_tok in ("mirror", "desktop", "mobile", "virtual"):
            view = {"mirror": DESKTOP_VIEW_MIRROR,
                    "desktop": DESKTOP_VIEW_DESKTOP,
                    "mobile": DESKTOP_VIEW_MOBILE,
                    "virtual": DESKTOP_VIEW_MOBILE}[v_tok]
        elif v_tok.isdigit():
            view = int(v_tok)
        if res_tok and "x" in res_tok:
            with contextlib.suppress(ValueError):
                _rw_arg, _rh_arg = (max(64, int(x) & ~1) for x in res_tok.split("x", 1))
                self.desktop_res = (_rw_arg, _rh_arg)
                self.desktop_rotation = 1 if _rh_arg > _rw_arg else 0
        c.pop("_user_stopped_until", None)
        if self.exclusive == "auto" and c.get("ss_started"):
            await self._stop_screenshare(c, "before !rq desktop")
        await self._stop_appstream(c, "before !rq desktop", send=True, only_kind="desktop")
        await self._stop_desktop(c, "before !rq desktop", send=True)
        self._session_reset(c, tag=f"rq desktop view={view}")
        self._as_bump_instance(f"rq desktop view={view}")
        if view == DESKTOP_VIEW_DESKTOP:
            rw, rh = int(self.desktop_res[0]), int(self.desktop_res[1])
            self.desktop_res = self.fit_desktop_resolution(rw, rh)
        await asyncio.sleep(0.15)
        c["as_pkg"] = {1: "mirror", 2: "desktop", 3: "mobile"}.get(view, "desktop")
        ok = await self._send_ws_text(c, build_desktop_request(self, view),
                                      f"RQ_MOBILEDESKTOP_SENT(svc1 cmd1,view={view})")
        if ok:
            c["desktop_pending"] = True
            c["desktop_started"] = True
            c["desktop_view"] = view
            c["desktop_progress"] = False
            c["session"]["feature"] = "desktop"
            c["tasks"].append(asyncio.create_task(
                self._proactive_peer_start(c, c["session"]["gen"])))
        rec.update(ok=ok, detail=f"view={view} "
                                 f"({{1: 'mirror', 2: 'desktop', 3: 'mobile'}}[view]); "
                                 f"the phone shows an accept dialog — TAP IT, "
                                 f"then the desktop session starts and svc-1 "
                                 f"cmd:56/34 display info arrives. Silence "
                                 f"usually means the dialog was missed or "
                                 f"the phone still considers a previous "
                                 f"session active (send !stop desktop first)")
        return None

    async def _rq_cmd_appstream(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        if not extra:
            rec["detail"] = ("usage: !rq appstream <pkg>[/<component>] "
                             "(or 'desktop'/'mobile'/'mirror' for the "
                             "pseudo-packages that start with no dialog)")
            self.events.log("RQ_FAIL", rec["detail"], feature="appstream")
            self.rq_log.append(rec)
            return rec
        pkg, _, cmp_ = extra.partition("/")
        pseudo = pkg in ("desktop", "mobile", "mirror")
        use_dialog = (self.appstream_source == "dialog") and not pseudo
        src = APPSTREAM_SOURCE_PC_APP if use_dialog else APPSTREAM_SOURCE_DESKTOP
        coexist_desk = bool(c.get("desktop_started") and not pseudo)
        saved_d_view = c.get("desktop_view")
        saved_d_sid = c.get("desktop_as_sid")
        saved_d_disp = c.get("desktop_display_id")
        saved_as_map = dict(c.get("as_sessions") or {})
        if coexist_desk:
            if self.exclusive == "auto" and c.get("ss_started"):
                await self._stop_screenshare(c, f"before !rq appstream {pkg}")
            await self._stop_appstream(c, f"replace-app before {pkg}", send=True, only_kind="app")
        else:
            await self._exclusive_stop(c, keep="appstream",
                                       tag=f"before !rq appstream {pkg}")
            self._session_reset(c, tag=f"rq appstream {pkg}")
            self._as_bump_instance(f"rq appstream {pkg}")
        if coexist_desk:
            c["desktop_started"] = True
            if saved_d_view is not None:
                c["desktop_view"] = saved_d_view
            if saved_d_sid is not None:
                c["desktop_as_sid"] = saved_d_sid
            if saved_d_disp is not None:
                c["desktop_display_id"] = saved_d_disp
            c["as_sessions"] = {
                k: v for k, v in saved_as_map.items()
                if isinstance(v, dict) and v.get("is_desktop")
            }
        c["as_pkg"] = pkg
        c["as_cmp"] = cmp_ or None
        c["as_last_source"] = src
        c["as_dialog_retry_done"] = bool(use_dialog)
        c["as_media_connected"] = False
        self.as_status.update(phase="starting", pkg=pkg, sid=None,
                              detail=("dialog start — accept on the phone" if use_dialog
                                      else "silent start (source=1)"),
                              updated=time.time())
        ok = await self._send_ws_text(
            c, build_appstream_start(self, pkg, cmp_ or None, source=src),
            f"RQ_APPSTREAM_SENT(svc15 cmd1,pkg={pkg},source={src})")
        if ok:
            c["session"]["feature"] = "appstream"
            c["as_setup_seen"] = True
        rec.update(ok=ok, detail=f"pkg={pkg} cmp={cmp_ or '-'} "
                                 f"{'(pseudo-package: starts with NO dialog)' if pseudo else ('(dialog start)' if use_dialog else '(silent start, source=1 — auto-retries with the phone dialog if denied)')} "
                                 f"instanceId={self.as_instance_id}")
        return None

    async def _rq_cmd_screenshare(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        ok = await self.start_screenshare(c, origin="!rq")
        rec.update(ok=ok, detail=f"svc-202 cmd:0 sessionId={self.ss_session_id} "
                                 f"shareType={self.ss_share_type}")
        return None

    async def _rq_cmd_webcam(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        ok = await self.start_webcam(c, origin="!rq")
        rec.update(ok=ok, detail="svc-3 cmd:0 trigger (no isReply) — accept the "
                                 "dialog on the phone, then cmd:1 START follows "
                                 "automatically")
        return None

    async def _rq_cmd_stop(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        await self._stop_all(c, "!rq stop")
        rec.update(ok=True, detail="svc-1 cmd:5 + svc-15 cmd:7/25 + svc-202 cmd:26 "
                                 "+ svc-3 cmd:2 sent")
        return None

    async def _rq_cmd_webcam_stop(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        ok = await self.stop_webcam(c, "!rq webcam-stop")
        rec.update(ok=ok, detail="svc-3 cmd:2 sent")
        return None

    async def _rq_cmd_desktop_view(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        v_part, _, res_part = extra.partition(":")
        v_map = {"mirror": DESKTOP_VIEW_MIRROR, "desktop": DESKTOP_VIEW_DESKTOP,
                 "mobile": DESKTOP_VIEW_MOBILE, "virtual": DESKTOP_VIEW_MOBILE,
                 "1": 1, "2": 2, "3": 3}
        v = v_map.get(v_part.strip().lower(), int(v_part) if v_part.strip().isdigit() else DESKTOP_VIEW_DESKTOP)
        w_v, h_v = self.desktop_res
        if res_part and "x" in res_part.lower():
            with contextlib.suppress(ValueError):
                w_v, h_v = (max(240, int(x)) for x in res_part.lower().split("x", 1))
        w_v, h_v = max(w_v, h_v), min(w_v, h_v)
        self.desktop_res = (w_v, h_v)
        self.desktop_rotation = 0 if v == 2 else 1
        c["desktop_view"] = v
        c["as_pkg"] = {1: "mirror", 2: "desktop", 3: "mobile"}.get(v, "desktop")
        ok = await self._send_ws_text(
            c, build_desktop_switch_view(self, v, width=w_v, height=h_v),
            f"RQ_DESKTOP_VIEW_SENT(svc1 cmd30,view={v},{w_v}x{h_v})")
        mode_val = {1: DISPLAY_MODE_MIRROR, 2: DISPLAY_MODE_DESKTOP, 3: DISPLAY_MODE_MOBILE}.get(v, DISPLAY_MODE_DESKTOP)
        await self._send_ws_text(
            c, build_display_set_mode(self, mode=mode_val),
            f"RQ_DISPLAY_SET_MODE_SENT(svc10 msgId4,mode={mode_val})")
        sid_v = c.get("desktop_as_sid") or c.get("as_session_id")
        disp_v = c.get("desktop_display_id") if c.get("desktop_display_id") is not None else c.get("as_display_id")
        if sid_v is not None or disp_v is not None:
            await self._send_ws_text(
                c,
                build_appstream_resize(
                    self, sid_v if sid_v is not None else 0,
                    disp_v if disp_v is not None else 0,
                    w_v, h_v, density=self.as_density,
                    scale=self.as_scale, custom_display=(v == 2),
                    pkg=c["as_pkg"]),
                f"RQ_DESKTOP_VIEW_RESIZE_SENT(svc15 cmd17,view={v},{w_v}x{h_v})")
        self._notify_ui_feature_started(
            "phone_on_pc" if v in (1, 3) else "desktop",
            conn=c, view=v, force_present=True
        )
        rec.update(ok=ok, detail=f"switch running session to view={v} ({w_v}x{h_v})")
        return None

    async def _rq_cmd_lock(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        ok = await self._send_ws_text(c, build_desktop_lock(self, True),
                                      "RQ_LOCK_SENT(svc1 cmd20)")
        rec.update(ok=ok, detail="phone display -> sleep")
        return None

    async def _rq_cmd_dialog_dismiss(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        ok = await self._send_ws_text(c, build_dialog_dismiss(self),
                                      "RQ_DIALOG_DISMISS_SENT(svc25 cmd21)")
        rec.update(ok=ok, detail="phone's ActionNoticeCommonDialog dismissed")
        return None

    async def _rq_cmd_uv_swap(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        if extra.lower() in ("on", "1", "true"):
            self.set_uv_swap(True)
        elif extra.lower() in ("off", "0", "false"):
            self.set_uv_swap(False)
        else:
            self.set_uv_swap(None)
        rec.update(ok=True, detail=f"webcam uv_swap={self.uv_swap}")
        return None

    async def _rq_cmd_webcam_cameras(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        ok = await self._send_ws_text(c, build_webcam_camera_query(self),
                                      "RQ_WEBCAM_CAMERAS_SENT(svc3 cmd3)")
        rec.update(ok=ok, detail="svc-3 cmd:3 camera list requested")
        return None

    async def _rq_cmd_webcam_switch(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        wc_res = (self.phone_resolutions.get("webcam") or {})
        cur_cam = int(wc_res.get("activeCameraId") or 0)
        cam_id = int(extra) if extra.isdigit() else (0 if cur_cam else 1)
        wc_res["activeCameraId"] = cam_id
        await self._send_ws_text(c, build_webcam_switch_camera(self),
                                 f"RQ_WEBCAM_FLIP_SENT(svc3 cmd4,cam={cam_id})")
        frame_str = json.dumps(_env(self, self.next_msg_id(), self.device_id_b64,
                                    3, 3, {"cmd": 5, "ver": "1", "data": {"cameraId": cam_id}}),
                               separators=(",", ":"))
        ok = await self._send_ws_text(c, frame_str, f"RQ_WEBCAM_SWITCH_SENT(svc3 cmd5,cam={cam_id})")
        rec.update(ok=ok, detail=f"switched webcam to cameraId={cam_id}")
        return None

    async def _rq_cmd_webcam_pause(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        ok = await self._send_ws_text(c, build_webcam_pause(self), "RQ_WEBCAM_PAUSE_SENT(svc3 cmd11)")
        rec.update(ok=ok, detail="svc-3 cmd:11 pause sent")
        return None

    async def _rq_cmd_webcam_resume(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        ok = await self._send_ws_text(c, build_webcam_resume(self), "RQ_WEBCAM_RESUME_SENT(svc3 cmd12)")
        rec.update(ok=ok, detail="svc-3 cmd:12 resume sent")
        return None

    async def _rq_cmd_media_diag(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        md = getattr(self, "media_diag", None)
        if md is None:
            rec.update(ok=False, detail="media diagnostics unavailable "
                                        "(services/media_diagnostics.py missing)")
        else:
            arg_l = (extra or "").strip().lower()
            if arg_l in ("on", "off"):
                md.enabled = (arg_l == "on")
                rec.update(ok=True, detail=f"media diagnostics {arg_l}")
            elif arg_l.startswith("interval"):
                with contextlib.suppress(ValueError):
                    md.interval = max(0.25, float(arg_l.split()[1]))
                rec.update(ok=True, detail=f"poll interval = {md.interval}s")
            elif arg_l.startswith("dump"):
                parts = arg_l.split(None, 1)
                dest = parts[1].strip() if len(parts) > 1 else None
                jp = md.write_report(dest)
                rec.update(ok=True,
                           detail=f"report written to {jp} and "
                                  f"{Path(str(jp)).with_suffix('.md')} | "
                                  f"VERDICT: {md.verdict()}")
            elif arg_l.startswith("raw"):
                parts = arg_l.split(None, 1)
                cnt = 6
                with contextlib.suppress(ValueError, IndexError):
                    cnt = max(1, min(200, int(parts[1])))
                rec.update(ok=True,
                           detail=json.dumps(md.samples(cnt), default=str)[:4000])
            else:
                rec.update(ok=True,
                           detail=f"VERDICT: {md.verdict()} | SUMMARY: "
                                  + json.dumps(md.summary(), default=str)[:2500])
        return None

    async def _rq_cmd_ss_virtual(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        _w = extra.strip().lower()
        if _w in ("off", "release", "stop"):
            self.ss_virtual_backend = "off"
            self._release_ss_virtual_display("!rq ss-virtual off")
            rec.update(ok=True, detail="virtual display disabled - Share "
                                       "Screen will mirror the primary monitor")
        elif _w in ("rebuild", "retry", "on", "auto"):
            if _w in ("on", "auto"):
                self.ss_virtual_backend = "auto"
            info = await self._ensure_ss_virtual_display()
            rec.update(ok=bool(info.get("ok")),
                       detail=json.dumps(info, default=str)[:600])
        elif _w.startswith("send-window") or _w.startswith("move-window"):
            _ttl = _w.split(None, 1)[1].strip() if " " in _w else ""
            res_w = await asyncio.to_thread(self.move_window_to_virtual_display, _ttl)
            rec.update(ok=bool(res_w.get("ok")), detail=json.dumps(res_w)[:500])
        elif _w in ("right", "left", "above", "below"):
            self.set_ss_virtual(pos=_w)
            info = await self._ensure_ss_virtual_display()
            rec.update(ok=bool(info.get("ok")), detail=json.dumps(info)[:600])
        elif _w.startswith("rotate") or _w in ("landscape", "portrait"):
            _rot_t = "toggle"
            if ":" in _w:
                _rot_t = _w.split(":", 1)[1].strip() or "toggle"
            elif _w in ("landscape", "portrait"):
                _rot_t = _w
            rot_res = await self.rotate_screenshare_extend(c, target=_rot_t)
            rec.update(ok=bool(rot_res.get("ok")), detail=json.dumps(rot_res, default=str)[:600])
        else:
            if _w and "x" in _w:
                with contextlib.suppress(Exception):
                    _a, _b2 = (max(160, int(x)) for x in _w.split("x", 1))
                    self.ss_virtual_res = (_a, _b2)
            probe = self.virtual_display.probe()
            info = await self._ensure_ss_virtual_display()
            rec.update(ok=True,
                       detail=f"probe={probe} current={info}")
        return None

    async def _rq_cmd_ss_mode(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        _mode_part, _, _orient_part = str(extra or "").partition(":")
        mode = _mode_part.strip().lower()
        if mode not in ("extend", "mirror"):
            mode = "mirror"
        self.ss_share_type = mode
        if _orient_part.strip():
            _o = _orient_part.strip().lower()
            if "x" in _o:
                with contextlib.suppress(Exception):
                    _ow, _oh = (max(160, int(v)) for v in _o.split("x", 1))
                    self.set_ss_virtual(res=(_ow, _oh))
            elif _o in ("right", "left", "above", "below"):
                self.set_ss_virtual(pos=_o)
            else:
                self.set_ss_virtual(orient=_o)
        await self._ensure_ss_virtual_display()
        if self._ss_track is not None:
            with contextlib.suppress(Exception):
                _reg, _disp, _out = self._ss_capture_source()
                self._ss_track.set_source(region=_reg, display=_disp,
                                          output=_out)
                self.ss_stats["backend"] = self._ss_track.backend
                self.ss_stats["capture"] = ("virtual-display"
                                            if (_reg or _disp or _out)
                                            else "primary-monitor")
        ok = await self._send_ws_text(c, build_ss_switch(self, self.ss_session_id, mode, self.ss_audio, self.ss_scale),
                                      f"RQ_SS_SWITCH_SENT(svc202 cmd14,{mode})")
        _vd = self.ss_stats.get("virtualDisplay") or {}
        rec.update(ok=ok, detail=f"svc-202 cmd:14 shareType={mode} scale={self.ss_scale} "
                                 f"orient={self.ss_virtual_orient} "
                                 f"virtual={self._ss_virtual_size()[0]}x{self._ss_virtual_size()[1]} "
                                 f"capture={self.ss_stats.get('capture', 'primary-monitor')}"
                                 + ("" if _vd.get("ok") or mode == "mirror"
                                    else f" | EXTEND UNAVAILABLE: {_vd.get('detail')}"))
        return None

    async def _rq_cmd_ss_audio(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        on = extra.lower() in ("1", "on", "true", "yes")
        self.ss_audio = on
        ok = await self._send_ws_text(c, build_ss_audio(self, self.ss_session_id, on),
                                      f"RQ_SS_AUDIO_SENT(svc202 cmd18,{on})")
        rec.update(ok=ok, detail=f"svc-202 cmd:18 setAudio={on}")
        return None

    async def _rq_cmd_ss_rotate(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        rot_res = await self.rotate_screenshare_extend(c, target=extra.strip() or "toggle")
        rec.update(
            ok=bool(rot_res.get("ok")),
            detail=(
                f"svc-202 extend rotated to {rot_res.get('width')}x{rot_res.get('height')} "
                f"({rot_res.get('orient')}, {rot_res.get('aspect')})"
            ),
        )
        return None

    async def _rq_cmd_ss_res(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        auto_mode = 0
        res_part = extra
        if ":" in extra:
            res_part, mode_str = extra.split(":", 1)
            auto_mode = 1 if mode_str.strip().lower() in ("1", "auto", "true") else 0
        with contextlib.suppress(Exception):
            w_, h_ = (int(x) for x in res_part.lower().split("x", 1))
            self.ss_res = (max(64, w_), max(64, h_))
        w_now, h_now = self.ss_res
        self.phone_resolutions["screenshare"]["active"] = {
            "width": w_now, "height": h_now, "auto": auto_mode,
        }
        if self._ss_track is not None and hasattr(self._ss_track, "set_resolution"):
            self._ss_track.set_resolution(w_now, h_now)
        ok = await self._send_ws_text(
            c, build_ss_resolution(
                self, self.ss_session_id, w_now, h_now, auto=auto_mode,
                res_list=self.phone_resolutions["screenshare"]["pcSupportedList"]),
            f"RQ_SS_RES_SENT(svc202 cmd35,{w_now}x{h_now},auto={auto_mode})")
        rec.update(ok=ok, detail=f"svc-202 cmd:35 {w_now}x{h_now} (auto={auto_mode}) + track resized")
        return None

    async def _rq_cmd_res_query(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        ok1 = await self._send_ws_text(
            c, build_display_query(self, DISPLAY_TYPE_REMOTE, 10),
            "RQ_DISPLAY_QUERY_SENT(svc10 msgId1)")
        ok2 = await self._send_ws_text(
            c, build_webcam_camera_query(self),
            "RQ_WEBCAM_CAMERAS_SENT(svc3 cmd3)")
        rec.update(ok=(ok1 or ok2), detail="queried svc-10 DisplayInfo (msgId:1) & svc-3 cameras (cmd:3)")
        return None

    async def _rq_cmd_display_res(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        fps = 60.0
        wh = extra
        if "@" in extra:
            wh, fps_s = extra.split("@", 1)
            with contextlib.suppress(ValueError):
                fps = float(fps_s)
        w_, h_ = 1920, 1080
        with contextlib.suppress(ValueError):
            w_, h_ = (max(320, int(x)) for x in wh.lower().split("x", 1))
        self.desktop_res = (w_, h_)
        self.phone_resolutions["display_service"]["current"] = {"width": w_, "height": h_, "fps": fps}
        ok = await self._send_ws_text(
            c, build_display_set_resolution(self, w_, h_, fps=fps),
            f"RQ_DISPLAY_SET_RES_SENT(svc10 msgId3,{w_}x{h_}@{fps})")
        rec.update(ok=ok, detail=f"svc-10 msgId:3 set remote display resolution {w_}x{h_}@{fps}fps")
        return None

    async def _rq_cmd_display_mode(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        m_map = {"mirror": 1, "desktop": 2, "mobile": 4, "1": 1, "2": 2, "4": 4}
        m_val = m_map.get(extra.strip().lower(), 2)
        ok = await self._send_ws_text(
            c, build_display_set_mode(self, mode=m_val),
            f"RQ_DISPLAY_SET_MODE_SENT(svc10 msgId4,mode={m_val})")
        rec.update(ok=ok, detail=f"svc-10 msgId:4 set display mode={m_val}")
        return None

    async def _rq_cmd_desktop_rotate(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        res = await self.rotate_desktop(c, extra or "toggle")
        rec.update(ok=bool(res.get("ok")),
                   detail=res.get("detail") or "rotate failed",
                   **{k: v for k, v in res.items()
                      if k in ("target", "method", "verified", "strategy")})
        return None

    async def _rq_cmd_desktop_res(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        w_, h_ = 1920, 1080
        with contextlib.suppress(ValueError):
            w_, h_ = (max(320, int(x)) for x in extra.lower().split("x", 1))
        self.desktop_res = (w_, h_)
        self.desktop_rotation = 1 if h_ > w_ else 0
        self.phone_resolutions["desktop"]["current"] = {"width": w_, "height": h_}
        c["as_geom"] = (w_, h_)
        ok1 = await self._send_ws_text(
            c, build_desktop_set_resolution(self, w_, h_),
            f"RQ_DESKTOP_SET_RES_SENT(svc1 cmd33,{w_}x{h_})")
        ok2 = await self._send_ws_text(
            c, build_display_set_resolution(self, w_, h_, fps=60.0),
            f"RQ_DISPLAY_SET_RES_SENT(svc10 msgId3,{w_}x{h_})")
        ok3 = False
        sid_r = c.get("as_session_id")
        disp_r = c.get("as_display_id")
        if sid_r is not None or disp_r is not None:
            ok3 = await self._send_ws_text(
                c,
                build_appstream_resize(
                    self, sid_r if sid_r is not None else 0,
                    disp_r if disp_r is not None else 0,
                    w_, h_, density=self.as_density,
                    scale=self.as_scale, custom_display=True,
                    pkg=(c.get("as_pkg") or "desktop")),
                f"RQ_DESKTOP_LIVE_RESIZE_SENT(svc15 cmd17,{w_}x{h_},"
                f"sid={sid_r},disp={disp_r})")
        rec.update(ok=(ok1 or ok2 or ok3),
                   detail=f"resized Mobile Desktop to {w_}x{h_} "
                          f"(svc-1 cmd:33 + svc-10 msgId:3 + svc-15 cmd:17 live={ok3}; "
                          f"watch for APPSTREAM_RESIZE_RESULT cmd:26)")
        return None

    async def _rq_cmd_appstream_resize(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        dpi = self.as_density
        wh = extra
        if "@" in extra:
            wh, dpi_s = extra.split("@", 1)
            with contextlib.suppress(ValueError):
                dpi = max(120, min(640, int(dpi_s)))
        w_, h_ = 1280, 720
        with contextlib.suppress(ValueError):
            w_, h_ = (max(240, int(x)) for x in wh.lower().split("x", 1))
        self.desktop_res = (w_, h_)
        self.as_density = dpi
        c["as_geom"] = (w_, h_)
        sid_as = c.get("as_session_id") or 0
        disp_id = c.get("as_display_id") if c.get("as_display_id") is not None else 0
        pkg_as = c.get("as_pkg")
        ok = await self._send_ws_text(
            c, build_appstream_resize(self, sid_as, disp_id, w_, h_, density=dpi,
                                      scale=self.as_scale, custom_display=True, pkg=pkg_as),
            f"RQ_AS_RESIZE_SENT(svc15 cmd17,{w_}x{h_}@{dpi}dpi)")
        rec.update(ok=ok, detail=f"svc-15 cmd:17 resize {w_}x{h_}@{dpi}dpi (sid={sid_as}, displayId={disp_id})")
        return None

    async def _rq_cmd_webcam_flip(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        ok = await self._send_ws_text(
            c, build_webcam_switch_camera(self),
            "RQ_WEBCAM_FLIP_SENT(svc3 cmd4)")
        rec.update(ok=ok, detail="svc-3 cmd:4 camera front/back flip sent")
        return None

    async def _rq_cmd_webcam_flash(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        ok = await self._send_ws_text(
            c, build_webcam_toggle_flash(self),
            "RQ_WEBCAM_FLASH_SENT(svc3 cmd7)")
        rec.update(ok=ok, detail="svc-3 cmd:7 camera flash/torch toggle sent")
        return None

    async def _rq_cmd_webcam_zoom(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        lvl = int(extra) if extra.lstrip("-").isdigit() else 0
        ok = await self._send_ws_text(
            c, build_webcam_set_zoom(self, lvl),
            f"RQ_WEBCAM_ZOOM_SENT(svc3 cmd9,state={lvl})")
        rec.update(ok=ok, detail=f"svc-3 cmd:9 camera zoom state={lvl} sent")
        return None

    async def _rq_cmd_webcam_res(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        if extra.lower() in ("native", "auto", "off", "0x0", ""):
            self.webcam_output_res = None
            rec.update(ok=True, detail="webcam PC output rescaling reset to native phone stream")
        else:
            with contextlib.suppress(ValueError):
                w_, h_ = (max(160, int(x)) for x in extra.lower().split("x", 1))
                self.webcam_output_res = (w_, h_)
            rec.update(ok=True, detail=f"webcam PC output rescaling set to {self.webcam_output_res}")
        return None

    async def _rq_cmd_appstream_pause(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        sid = c.get("as_session_id") or 0
        ok = await self._send_ws_text(c, build_appstream_pause(self, sid), "RQ_AS_PAUSE_SENT(svc15 cmd5)")
        rec.update(ok=ok, detail=f"svc-15 cmd:5 pause sid={sid}")
        return None

    async def _rq_cmd_appstream_resume(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        sid = c.get("as_session_id") or 0
        ok = await self._send_ws_text(c, build_appstream_resume(self, sid), "RQ_AS_RESUME_SENT(svc15 cmd6)")
        rec.update(ok=ok, detail=f"svc-15 cmd:6 resume sid={sid}")
        return None

    async def _rq_cmd_clipboard(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        self._clip_seq += 1
        self.pc_clipboard_text = extra
        ok = await self._send_ws_text(
            c, build_clipboard_update(self, self._clip_seq, extra),
            "RQ_CLIPBOARD_UPDATE_SENT(svc9 msgId3)")
        if ok:
            await self._send_ws_text(
                c, build_clipboard_synced(self, self._clip_seq, True),
                "RQ_CLIPBOARD_SYNCED_SENT(svc9 msgId12)")
        rec.update(ok=ok, detail=f"pushed {len(extra)} chars to phone clipboard (CLIP_UPDATE msgId:3)")
        return None

    async def _rq_cmd_clipboard_sync(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        want = str(extra).strip().lower()
        if want in ("1", "on", "true", "yes", "enable"):
            st_cs = self.clip_sync.set_enabled(True)
        elif want in ("0", "off", "false", "no", "disable"):
            st_cs = self.clip_sync.set_enabled(False)
        else:
            st_cs = self.clip_sync.set_enabled(not self.clip_sync.enabled)
        rec.update(ok=True, detail=f"automatic clipboard sync {'ON' if st_cs['enabled'] else 'OFF'} "
                                   f"(backend={st_cs['backend']}, "
                                   f"PC->phone={st_cs['pc_to_phone']}, phone->PC={st_cs['phone_to_pc']}, "
                                   f"echoes suppressed={st_cs['echoes_suppressed']})")
        return None

    async def _rq_cmd_v4l2_setup(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        res_v = await asyncio.to_thread(self.webcam_controller.explicit_setup, True)
        setup_v = res_v.get("setup") or {}
        rec.update(ok=(res_v.get("state") == "running"),
                   detail=f"v4l2 state={res_v.get('state')} device={res_v.get('resolved_device')} "
                          f"module_method={setup_v.get('method')} — {setup_v.get('hint', '')}".strip())
        return None

    async def _rq_cmd_crosscontrol(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        trg = 0 if str(extra).strip().lower() in ("dialog", "0") else 1
        r_uc = await self.start_cross_control(c, trigger=trg)
        rec.update(ok=bool(r_uc.get("ok")),
                   detail=(f"svc-26 cmd:1 sent (cid={r_uc.get('cid')}, "
                           f"trigger={r_uc.get('trigger')}) — "
                           + ("phone auto-accepts; watch for cmd:2" if trg else
                              "accept the dialog on the phone")))
        return None

    async def _rq_cmd_uc_stop(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        r_uc = await self.stop_cross_control(c)
        rec.update(ok=bool(r_uc.get("ok")), detail=str(r_uc.get("detail")))
        return None

    async def _rq_cmd_uc_pointer(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        if c is None:
            rec["detail"] = "no live phone connection (pair first)"
            self.rq_log.append(rec)
            return rec
        want = str(extra).strip().lower() not in ("0", "off", "false", "hide", "no")
        self.uc_state["pointer_shown"] = want
        ok = await self._send_ws_text(c, build_uc_show_pointer(self, want),
                                      f"UC_SHOW_POINTER_SENT(cmd4,{want})")
        rec.update(ok=ok, detail=f"phone RDP pointer show={want}")
        return None

    async def _rq_cmd_transport_prefer_usb(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        want = str(extra).strip().lower()
        self.prefer_usb = (want not in ("0", "off", "false", "no")) if want else (not self.prefer_usb)
        rec.update(ok=True, detail=f"USB-preferred auto transport switching {'ON' if self.prefer_usb else 'OFF'}")
        return None

    async def _rq_cmd_transport_retry(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        try:
            secs = float(str(extra).strip() or 10.0)
        except ValueError:
            secs = 10.0
        secs = max(3.0, min(60.0, secs))
        with contextlib.suppress(Exception):
            self._open_search_window(f"manual retry ({name})", secs=secs)
        rec.update(ok=True,
                   detail=(f"discovery window open for {secs:.0f}s — active USB scan + "
                           f"Wi-Fi mDNS/BLE search now; afterwards it sleeps until an "
                           f"event (USB plug/unplug, disconnect) or another retry"))
        return None

    async def _rq_cmd_clipboard_pull(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        req_id = gen_task_id()
        ok = await self._send_ws_text(
            c, build_clipboard_pull(self, req_id, 1),
            "RQ_CLIPBOARD_PULL_SENT(svc9 msgId4)")
        rec.update(ok=ok, detail=f"requested phone clipboard (requestId={req_id})")
        return None

    async def _rq_cmd_clipboard_nego(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        ok = await self._send_ws_text(
            c, build_clipboard_nego(self, 3),
            "RQ_CLIPBOARD_NEGO_SENT(svc9 msgId1)")
        rec.update(ok=ok, detail="sent CLIP_NEGO version=3")
        return None

    async def _rq_cmd_clipboard_files(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        on = extra.lower() not in ("0", "off", "false")
        ok = await self._send_ws_text(
            c, build_clipboard_support_files(self, on),
            f"RQ_CLIPBOARD_FILES_SENT(svc9 msgId10,enabled={on})")
        rec.update(ok=ok, detail=f"clipboard file sync enabled={on}")
        return None

    async def _rq_cmd_ntf_settings(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        ok = await self._send_ws_text(
            c, build_ntf_open_settings(self),
            "RQ_NTF_SETTINGS_SENT(svc5 cmd2)")
        rec.update(ok=ok, detail="opened phone notification settings (svc-5 cmd:2)")
        return None

    async def _rq_cmd_ntf_dismiss(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        ok = await self.send_ntf_dismiss(c, extra)
        rec.update(ok=ok, detail=f"dismissed/clicked notification key={extra}")
        return None

    async def _rq_cmd_ntf_click(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        ok = await self._send_ws_text(
            c, build_ntf_pc_msg(self, extra, 2),
            f"RQ_NTF_CLICK_SENT(svc5 event2,key={extra})")
        rec.update(ok=ok, detail=f"opened notification on the phone key={extra}")
        return None

    async def _rq_cmd_ntf_reply(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        nkey, _, rtxt = extra.partition(":")
        ok = await self._send_ws_text(
            c, build_ntf_pc_msg(self, nkey, 0, {"reply_content": rtxt, "smart_reply": False}),
            f"RQ_NTF_REPLY_SENT(svc5 event0,key={nkey})")
        rec.update(ok=ok, detail=f"replied to notification {nkey}: {rtxt!r}")
        return None

    async def _rq_cmd_ntf_action(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        nkey, _, aid_s = extra.partition(":")
        aid = int(aid_s) if aid_s.isdigit() else 0
        ok = await self._send_ws_text(
            c, build_ntf_pc_msg(self, nkey, 1, {"action_id": aid}),
            f"RQ_NTF_ACTION_SENT(svc5 event1,key={nkey},aid={aid})")
        rec.update(ok=ok, detail=f"clicked action {aid} on notification {nkey}")
        return None

    async def _rq_cmd_ntf_block(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        ok = await self._send_ws_text(
            c, build_ntf_block_pkg(self, extra),
            f"RQ_NTF_BLOCK_SENT(svc5 cmd1,pkg={extra})")
        rec.update(ok=ok, detail=f"blocked package notifications for {extra}")
        return None

    async def _rq_cmd_keyevent(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        kc = int(extra) if extra.lstrip("-").isdigit() else 4
        disp = c.get("as_display_id") or 0
        kd = build_input_key(self, disp, kc, True)
        ku = build_input_key(self, disp, kc, False)
        ok = await self._send_ws_text(c, kd, f"RQ_KEY_DOWN(svc16 cmd3,key={kc})")
        await self._send_ws_text(c, ku, f"RQ_KEY_UP(svc16 cmd3,key={kc})")
        rec.update(ok=ok, detail=f"sent key={kc} down+up on displayId={disp} (svc-16 cmd:3)")
        return None

    async def _rq_cmd_text(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        disp = c.get("as_display_id") or 0
        tf = build_input_text(self, disp, extra)
        ok = await self._send_ws_text(c, tf, "RQ_TEXT_INPUT_SENT(svc16 cmd7)")
        rec.update(ok=ok, detail=f"committed text ({len(extra)} chars) on displayId={disp} (svc-16 cmd:7)")
        return None

    async def _rq_cmd_drag_enter(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        disp = c.get("as_display_id") or 0
        enter = extra.lower() not in ("0", "false", "leave")
        ok = await self._send_ws_text(
            c, build_drag_enter(self, disp, enter=enter, is_text=True),
            f"RQ_DRAG_ENTER_SENT(svc24 cmd3,enter={enter})")
        rec.update(ok=ok, detail=f"svc-24 cmd:3 DragEnter enter={enter} displayId={disp}")
        return None

    async def _rq_cmd_drag_text(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        disp = c.get("as_display_id") or 0
        self._drag_token += 1
        ok = await self._send_ws_text(
            c, build_drag_data(self, self._drag_token, disp, 400, 400, text=extra),
            f"RQ_DRAG_DATA_SENT(svc24 cmd1,tok={self._drag_token})")
        rec.update(ok=ok, detail=f"svc-24 cmd:1 R4DragData text={extra[:40]!r} token={self._drag_token}")
        return None

    async def _rq_cmd_drop(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        disp = c.get("as_display_id") or 0
        x, y = 500, 500
        if "x" in extra.lower():
            with contextlib.suppress(ValueError):
                x, y = (int(p) for p in extra.lower().split("x", 1))
        ok = await self._send_ws_text(
            c, build_drop_data(self, self._drag_token, disp, x, y),
            f"RQ_DROP_DATA_SENT(svc24 cmd2,xy={x},{y})")
        rec.update(ok=ok, detail=f"svc-24 cmd:2 R4DropData at ({x},{y}) token={self._drag_token}")
        return None

    async def _rq_cmd_filesend(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        task = await self.send_files_svc2(c, [extra] if extra else ["@demo"])
        rec.update(ok=(task is not None),
                   detail=(f"svc-2 cmd:1 announced batch taskId={task.task_id} ({task.file_count} file(s), {task.size}B) on TLS port {task.server_port}"
                           if task else "failed to announce file batch"))
        return None

    async def _rq_cmd_fileshare_text(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        ok = await self._send_ws_text(
            c, build_fileex_text_share(self, extra),
            "RQ_FILEEX_TEXT_SENT(svc2 cmd4)")
        rec.update(ok=ok, detail=f"svc-2 cmd:4 text share sent ({len(extra)} chars)")
        return None

    async def _rq_cmd_nfs_announce(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        ok = await self.send_fs_announce(c)
        rec.update(ok=ok, detail=f"svc-168 cmd:1001 NFSv3 server announced ({self.export_dir} -> {self.fs_path}, ports={self.fs_ports})")
        return None

    async def _rq_cmd_nfs_mount(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        res = await self.remount(extra, reannounce=True)
        rec.update(ok=bool(res.get("ok")), detail=res.get("error") or f"remounted NFSv3 export to {res.get('export_root')}")
        return None

    async def _rq_cmd_vault_query(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        fp_up = self.identity.fingerprint.upper()
        params = {"type": 0, "path": "", "port": 0, "fp": fp_up, "fp2": fp_up,
                  "deviceId": self.device_id_b64, "capability": 1,
                  "timestamp": int(time.time() * 1000), "nfs_version": 8,
                  "nfs_dependency": 0, "dt2type": 2}
        vf = json.dumps(_env(self, self.next_msg_id(), self.device_id_b64, 168, 168,
                             {"data": json.dumps(params, separators=(",", ":")), "cmd": 1001, "ver": "1.0"}),
                        separators=(",", ":"))
        ok = await self._send_ws_text(c, vf, "RQ_VAULT_QUERY_SENT(svc168 cmd1001)")
        rec.update(ok=ok, detail="svc-168 cmd:1001 vault query sent")
        return None

    async def _rq_cmd_checkin(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        ok = await self._send_ws_text(c, build_checkin(self), "RQ_CHECKIN_SENT(svc0 cmd2)")
        rec.update(ok=ok, detail="svc-0 cmd:2 check-in sent")
        return None

    async def _rq_cmd_exchange_key(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        ok = await self._send_ws_text(c, build_exchange_key(self, None), "RQ_EXCHANGE_KEY_SENT(svc0 cmd1)")
        rec.update(ok=ok, detail="svc-0 cmd:1 exchange-key sent")
        return None

    async def _rq_cmd_desktop_autostart_2(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        on = extra.strip().lower() not in ("0", "off", "false", "no")
        ok = await self._send_ws_text(
            c, build_desktop_autostart(self, on),
            f"RQ_DESKTOP_AUTOSTART_SENT(svc1 cmd36,on={on})")
        rec.update(ok=ok, detail=f"svc-1 cmd:36 autoStart={on}")
        return None

    async def _rq_cmd_desktop_rotation(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        rot_v = int(extra) if extra.lstrip("-").isdigit() else 0
        ok = await self._send_ws_text(
            c, build_rdp_client_rotation(self, rot_v),
            f"RQ_DESKTOP_ROTATION_SENT(svc1 cmd3,rot={rot_v})")
        rec.update(ok=ok, detail=f"svc-1 cmd:3 display rotation={rot_v}")
        return None

    async def _rq_cmd_as_focus(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        sid_f = c.get("as_session_id") or 0
        disp_f = c.get("as_display_id") or 0
        foc = extra.strip().lower() not in ("0", "off", "false", "unfocus")
        ok = await self._send_ws_text(
            c, build_appstream_focus_changed(self, sid_f, disp_f, foc, pkg=c.get("as_pkg")),
            f"RQ_AS_FOCUS_SENT(svc15 cmd13,focused={foc})")
        rec.update(ok=ok, detail=f"svc-15 cmd:13 focus={foc} sid={sid_f} disp={disp_f}")
        return None

    async def _rq_cmd_as_top(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        sid_t = c.get("as_session_id") or 0
        disp_t = c.get("as_display_id") or 0
        ok = await self._send_ws_text(
            c, build_appstream_move_to_top(self, sid_t, disp_t, pkg=c.get("as_pkg")),
            f"RQ_AS_TOP_SENT(svc15 cmd14,sid={sid_t})")
        rec.update(ok=ok, detail=f"svc-15 cmd:14 move-to-top sid={sid_t} disp={disp_t}")
        return None

    async def _rq_cmd_as_merge(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        sid_m = c.get("as_session_id") or 0
        m_on = extra.strip().lower() not in ("0", "off", "false")
        ok = await self._send_ws_text(
            c, build_appstream_app_merge(self, sid_m, m_on),
            f"RQ_AS_MERGE_SENT(svc15 cmd12,merge={m_on})")
        rec.update(ok=ok, detail=f"svc-15 cmd:12 merge={m_on} sid={sid_m}")
        return None

    async def _rq_cmd_as_move_monitor(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        sid_mm = c.get("as_session_id") or 0
        disp_mm = int(extra) if extra.lstrip("-").isdigit() else (c.get("as_display_id") or 0)
        ok = await self._send_ws_text(
            c, build_appstream_move_monitor(self, sid_mm, disp_mm),
            f"RQ_AS_MOVE_MONITOR_SENT(svc15 cmd15,disp={disp_mm})")
        rec.update(ok=ok, detail=f"svc-15 cmd:15 move-monitor sid={sid_mm} disp={disp_mm}")
        return None

    async def _rq_cmd_as_applist_sync(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        apps_l = json.loads(extra) if extra.strip().startswith("[") else []
        ok = await self._send_ws_text(
            c, build_appstream_applist_sync(self, apps_l),
            f"RQ_AS_APPLIST_SYNC_SENT(svc15 cmd24,n={len(apps_l)})")
        rec.update(ok=ok, detail=f"svc-15 cmd:24 synced {len(apps_l)} app(s)")
        return None

    async def _rq_cmd_webcam_angle(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        st_a = int(extra) if extra.lstrip("-").isdigit() else 0
        ok = await self._send_ws_text(
            c, build_webcam_set_angle(self, st_a),
            f"RQ_WEBCAM_ANGLE_SENT(svc3 cmd6,state={st_a})")
        rec.update(ok=ok, detail=f"svc-3 cmd:6 angle state={st_a}")
        return None

    async def _rq_cmd_webcam_tracking(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        st_t = 0 if extra.strip().lower() in ("0", "off", "false") else (int(extra) if extra.lstrip("-").isdigit() else 1)
        ok = await self._send_ws_text(
            c, build_webcam_set_tracking(self, st_t),
            f"RQ_WEBCAM_TRACKING_SENT(svc3 cmd8,state={st_t})")
        rec.update(ok=ok, detail=f"svc-3 cmd:8 tracking state={st_t}")
        return None

    async def _rq_cmd_webcam_mute(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        m_on = extra.strip().lower() not in ("0", "off", "false", "unmute")
        ok = await self._send_ws_text(
            c, build_webcam_set_mute(self, m_on),
            f"RQ_WEBCAM_MUTE_SENT(svc3 cmd10,mute={m_on})")
        rec.update(ok=ok, detail=f"svc-3 cmd:10 mute={m_on}")
        return None

    async def _rq_cmd_ss_scale(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        sc_v = max(100, int(extra) if extra.isdigit() else 200)
        self.ss_scale = sc_v
        ok = await self._send_ws_text(
            c, build_ss_scale(self, self.ss_session_id, sc_v),
            f"RQ_SS_SCALE_SENT(svc202 cmd28,scale={sc_v})")
        rec.update(ok=ok, detail=f"svc-202 cmd:28 setScale={sc_v}")
        return None

    async def _rq_cmd_ss_reconnect(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        ok = await self._send_ws_text(
            c, build_ss_reconnect(self, self.ss_session_id),
            "RQ_SS_RECONNECT_SENT(svc202 cmd18)")
        rec.update(ok=ok, detail=f"svc-202 cmd:18 reconnect sessionId={self.ss_session_id}")
        return None

    async def _rq_cmd_input_mode(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        parts_im = [int(x) for x in extra.replace(":", " ").split() if x.lstrip("-").isdigit()]
        im = parts_im[0] if parts_im else 0
        tm = parts_im[1] if len(parts_im) > 1 else 0
        ok = await self.send_input_mode(c, im, tm)
        rec.update(ok=ok, detail=f"svc-16 cmd:12 inputMode={im} touchMode={tm}")
        return None

    async def _rq_cmd_wheel(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        parts_w = [float(x) for x in extra.replace(":", " ").split() if x]
        dy_w = parts_w[0] if parts_w else 1.0
        dx_w = parts_w[1] if len(parts_w) > 1 else 0.0
        ok = await self.send_stream_wheel(c, 0.5, 0.5, dy_w, dx_w)
        rec.update(ok=ok, detail=f"svc-16 cmd:5 native wheel dy={dy_w} dx={dx_w}")
        return None

    async def _rq_cmd_control_lock_state(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        if base == "control-lock":
            st_val = 1
        elif base == "control-unlock":
            st_val = 0
        else:
            st_val = 1 if extra.lower() in ("1", "locked", "true", "on", "yes") else 0
        res_c = await self.send_control_lock_state(c, st_val)
        rec.update(ok=bool(res_c.get("ok")), state=st_val, detail=res_c.get("detail", ""))
        return None

    async def _rq_cmd_control_invite(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        is_cld = extra.lower() in ("cloud", "3")
        res_ci = await self.send_control_invite(c, is_cloud=is_cld)
        rec.update(ok=bool(res_ci.get("ok")), detail=res_ci.get("detail", ""))
        return None

    async def _rq_cmd_pair_device(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        hello_dev = ((c.get("hello") or {}).get("data") or {}).get("device") or {}
        real_id = str(hello_dev.get("realDeviceId") or hello_dev.get("deviceId") or c.get("hello", {}).get("deviceId") or "")
        dev_name = str(hello_dev.get("deviceName") or self.phone_telemetry.get("deviceName") or "Motorola Phone")
        if real_id:
            self.note_phone(real_id=real_id, nonce_id=c.get("hello", {}).get("deviceId"), name=dev_name, ip=self._phone_ip(c))
        self.pair_service_state["connected"] = True
        if real_id and real_id not in self.pair_service_state["connected_devices"]:
            self.pair_service_state["connected_devices"].append(real_id)
        ok_p = await self._send_ws_text(c, build_pair_frame(self, ""), "RQ_PAIR_SVC19_SENT(svc19)")
        rec.update(ok=bool(ok_p), detail=f"svc-19 pair frame sent & persisted {real_id or dev_name}")
        return None

    async def _rq_cmd_gallery_open(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        rpath = extra or "/DCIM/Camera"
        await self.refresh_gallery_widget(remote_path=rpath)
        ok_g = await self._send_ws_text(c, build_gallery_frame(self, cmd=1, category=7, path=rpath),
                                        "RQ_GALLERY_OPEN_SENT(svc205 cmd1)")
        rec.update(ok=bool(ok_g), detail=f"svc-205 gallery open sent ({rpath})")
        return None

    async def _rq_cmd_features_streaming_list(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        ok_f8 = await self._send_ws_text(c, build_features_request(self, 8, src_service_id=19),
                                         "RQ_FEATURES_CMD8_SENT(svc1001 cmd8)")
        rec.update(ok=bool(ok_f8), detail="svc-1001 cmd:8 getAppStreamingList sent")
        return None

    async def _rq_cmd_features_launch(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        fcmd = int(extra) if extra.isdigit() else 13
        ok_fl = await self._send_ws_text(c, build_features_request(self, fcmd, src_service_id=19),
                                         f"RQ_FEATURES_LAUNCH_SENT(svc1001 cmd{fcmd})")
        rec.update(ok=bool(ok_fl), detail=f"svc-1001 cmd:{fcmd} launch sent")
        return None

    async def _rq_cmd_features_stop(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        tsid = int(extra) if extra.lstrip("-").isdigit() else 0
        ok_fs = await self._send_ws_text(c, build_features_request(self, 25, data_payload=tsid, src_service_id=19),
                                         f"RQ_FEATURES_STOP_SENT(svc1001 cmd25 target={tsid})")
        rec.update(ok=bool(ok_fs), detail=f"svc-1001 cmd:25 stop-service({tsid}) sent")
        return None

    async def _rq_cmd_messages_sync(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        self.messages_state["triggered"] = True
        await self._send_ws_text(c, build_msg_notify_ready(self), "RQ_MESSAGES_READY_SENT(svc204 cmd18)")
        if not self.messages_state.get("granted", False):
            await self._send_ws_text(c, build_msg_permission_request(self, prompt_ui=True), "RQ_MESSAGES_PERM_REQ_SENT(svc204 cmd16)")
        else:
            await self._send_ws_text(c, build_msg_permission_request(self, prompt_ui=False), "RQ_MESSAGES_PERM_QUERY_SENT(svc204 cmd20)")
        await self._send_ws_text(c, build_msg_request_contacts(self), "RQ_MESSAGES_CONTACTS_SENT(svc204 cmd1)")
        await self._send_ws_text(c, build_msg_request_messages(self, msg_type=1, count=100), "RQ_MESSAGES_SMS_SENT(svc204 cmd10)")
        await self._send_ws_text(c, build_msg_request_messages(self, msg_type=3, count=50), "RQ_MESSAGES_MMS_SENT(svc204 cmd10)")
        ok_m = await self._send_ws_text(c, build_msg_request_sim_cards(self), "RQ_MESSAGES_SIMS_SENT(svc204 cmd12)")
        if c.get("writer") is not None and not self.messages_state.get("enabled", False):
            c.setdefault("tasks", []).append(asyncio.create_task(self._delayed_messages_sync_burst(c, delay=0.35)))
        rec.update(ok=bool(ok_m), detail="svc-204 pId:110 cmd:18/16/1/10/12 sync sent")
        return None

    async def _rq_cmd_messages_contacts(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        ok_mc = await self._send_ws_text(c, build_msg_request_contacts(self), "RQ_MESSAGES_CONTACTS_SENT(svc204 cmd1)")
        rec.update(ok=bool(ok_mc), detail="svc-204 pId:110 cmd:1 requestContactList sent")
        return None

    async def _rq_cmd_messages_list(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        ok_ml = await self._send_ws_text(c, build_msg_request_messages(self, msg_type=1, count=100), "RQ_MESSAGES_SMS_SENT(svc204 cmd10)")
        rec.update(ok=bool(ok_ml), detail="svc-204 pId:110 cmd:10 requestMessageList sent")
        return None

    async def _rq_cmd_messages_sims(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        ok_ms = await self._send_ws_text(c, build_msg_request_sim_cards(self), "RQ_MESSAGES_SIMS_SENT(svc204 cmd12)")
        rec.update(ok=bool(ok_ms), detail="svc-204 pId:110 cmd:12 requestSimCards sent")
        return None

    async def _rq_cmd_messages_permission(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        await self._send_ws_text(c, build_msg_notify_ready(self), "RQ_MESSAGES_READY_SENT(svc204 cmd18)")
        ok_mp = await self._send_ws_text(c, build_msg_permission_request(self, prompt_ui=True), "RQ_MESSAGES_PERM_SENT(svc204 cmd16)")
        rec.update(ok=bool(ok_mp), detail="svc-204 pId:110 cmd:18+16 requestPermission sent")
        return None

    async def _rq_cmd_messages_send(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        to_num, _, rest_s = extra.partition(":")
        sub_id_arg = -1
        msg_txt = rest_s
        if ":" in rest_s:
            body_cand, _, tail_sub = rest_s.rpartition(":")
            if tail_sub.strip().lstrip("-").isdigit():
                msg_txt = body_cand
                sub_id_arg = int(tail_sub.strip())
        res_ms = await self.send_android_sms(
            c, to_num.strip(),
            msg_txt.strip() or "Hello from Samyuj",
            sub_id=sub_id_arg,
        )
        rec.update(ok=bool(res_ms.get("ok")), detail=res_ms.get("detail") or res_ms.get("error", ""))
        return None

    async def _rq_cmd_messages_mms(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        res_mm = await self.download_mms_media(c, extra)
        rec.update(ok=bool(res_mm.get("ok")), detail=str(res_mm))
        return None

    async def _rq_cmd_messages_read(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        target_arg = extra.strip()
        threads_map = self.messages_state.get("threads") or {}
        sent_any = False
        if target_arg in threads_map:
            for m_item in threads_map[target_arg]:
                m_item["read"] = 1
                mid_i = m_item.get("msgId")
                if mid_i:
                    await self._send_ws_text(c, build_msg_mark_read(self, str(mid_i), status=1, msg_type=int(m_item.get("messageType", 1) or 1)), "RQ_MESSAGES_READ_SENT(svc204 cmd8)")
                    sent_any = True
            self._rebuild_message_threads()
        if not sent_any:
            for m_item in self.messages_state.get("messages") or []:
                if str(m_item.get("msgId")) == target_arg:
                    m_item["read"] = 1
            self._rebuild_message_threads()
            sent_any = await self._send_ws_text(c, build_msg_mark_read(self, target_arg, status=1, msg_type=1), "RQ_MESSAGES_READ_SENT(svc204 cmd8)")
        rec.update(ok=bool(sent_any), detail=f"svc-204 pId:110 cmd:8 mark read target={target_arg}")
        return None

    async def _rq_cmd_messages_search(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        q_str = extra.strip().lower()
        self.messages_state["search_query"] = extra.strip()
        matches = [
            m for m in (self.messages_state.get("messages") or [])
            if not q_str
            or q_str in str(m.get("body") or "").lower()
            or q_str in str(m.get("fromAddress") or "").lower()
            or any(q_str in str(a).lower() for a in (m.get("toAddress") or []))
        ]
        if callable(getattr(self, "on_messages_updated_cb", None)):
            with contextlib.suppress(Exception):
                self.on_messages_updated_cb()
        rec.update(ok=True, count=len(matches), detail=f"matched {len(matches)} message(s) for {extra!r}")
        return None

    async def _rq_cmd_ping(self, name: str, base: str, extra: str, c: dict | None, rec: dict) -> dict | None:
        await self._send_raw(c, ws_frame(0x9, b"sc-keepalive"))
        rec.update(ok=True, detail="WS ping sent")
        return None

    async def _keepalive(self, conn: dict) -> None:
        n = 0
        try:
            while not conn["closed"]:
                await asyncio.sleep(2.0)
                if conn["closed"]:
                    break
                n += 1
                await self._send_raw(conn, ws_frame(0x9, b"sc-keepalive"))
                conn["log"].line(f"WS PING >>> #{n}")
                self.events.log("WS_PING_SENT", f"conn{conn['id']:03d} n={n}")
        except (ConnectionError, ssl.SSLError, OSError, asyncio.CancelledError):
            pass

    async def _send_ws_text(self, conn: dict, text: str, tag: str) -> bool:
        if conn["closed"]:
            return False
        payload = text.encode("utf-8")
        await self._send_raw(conn, ws_frame(0x1, payload))
        conn["log"].line(f"WS TEXT <<< [{tag}] {text}")
        self.events.log(tag, f"conn{conn['id']:03d}: {text}",
                        feature=_feature_of_tag(tag))
        if _console_show(_feature_of_tag(tag), f"WS-OUT/{tag}"):
            print(f"[conn{conn['id']:03d} WS-OUT/{tag}] {text[:600]}")
        return True

    async def send_ws_text(self, text: str, conn: dict | None = None) -> bool:
        c = conn or self._live_ws()
        if not c:
            print("no live WS connection")
            return False
        return await self._send_ws_text(c, text, "MANUAL_SEND")

    async def send_wsdata(self, service_id: int, data_json: str) -> bool:
        envelope = json.dumps(_env(self, self.next_msg_id(), self.device_id_b64,
                                   service_id, 0, json.loads(data_json)),
                              separators=(",", ":"))
        return await self.send_ws_text(envelope)

    async def send_reply_variant(self, variant: str | None = None) -> bool:
        c = self._live_ws()
        if not c:
            print("no live WS connection")
            return False
        v = variant or self.auto_reply
        return await self._send_ws_text(c, build_reply_json(self, v, c.get("hello")),
                                        f"REPLY_SENT({v})")

    async def send_checkin(self) -> bool:
        c = self._live_ws()
        if not c:
            print("no live WS connection")
            return False
        return await self._send_ws_text(c, build_checkin(self), "CHECKIN_SENT(cmd2)")

    async def send_exchange_key(self, key_b64: str | None = None) -> bool:
        c = self._live_ws()
        if not c:
            print("no live WS connection")
            return False
        return await self._send_ws_text(c, build_exchange_key(self, key_b64 or None),
                                        "EXCHANGE_KEY_SENT(cmd1)")
