from __future__ import annotations
import datetime
import ssl
import urllib.parse
import urllib.request
from samyuj.protocol.constants import DISPLAY_TYPE_REMOTE
from samyuj.protocol.envelope import _env
from samyuj.protocol.services import (
    build_desktop_stop,
    build_display_query,
    build_display_set_resolution,
    build_msg_notify_ready,
    build_msg_permission_request,
    build_phonefs_handshake,
)

import asyncio
import base64
import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from samyuj.protocol.constants import DEFAULT_PC_CAPS, PC_CAP_INUSE
from samyuj.protocol.services import (
    build_control_invite,
    build_control_lock_state,
    build_device_status,
    build_wallpaper_request,
)

class ControlTelemetryServiceMixin:

    def apply_wallpaper_to_linux_desktop(self, image_path: str | Path) -> bool:
        p = Path(image_path).expanduser().resolve()
        if not p.is_file():
            return False
        uri = "file://" + urllib.parse.quote(str(p), safe="/")
        applied = False
        if shutil.which("gsettings"):
            for schema, key in (
                ("org.gnome.desktop.background", "picture-uri"),
                ("org.gnome.desktop.background", "picture-uri-dark"),
                ("org.cinnamon.desktop.background", "picture-uri"),
                ("org.mate.background", "picture-filename"),
            ):
                with contextlib.suppress(Exception):
                    val = str(p) if "filename" in key else uri
                    r = subprocess.run(
                        ["gsettings", "set", schema, key, val],
                        capture_output=True, timeout=2.0,
                    )
                    if r.returncode == 0:
                        applied = True
        if not applied and shutil.which("feh"):
            with contextlib.suppress(Exception):
                r = subprocess.run(["feh", "--bg-fill", str(p)], capture_output=True, timeout=2.0)
                if r.returncode == 0:
                    applied = True
        if applied:
            self.events.log("WALLPAPER_APPLIED_LINUX", f"Applied {p} to Linux desktop wallpaper", feature="wallpaper")
        return applied

    async def _post_key_telemetry_sync(self, conn: dict) -> None:
        try:
            await asyncio.sleep(0.15)
            if conn.get("closed"):
                return
            await self._send_ws_text(
                conn, build_device_status(self),
                "DEVSTATUS_SENT(svc25 cmd1,post-exchange-key)")
            conn["devstatus_post_key_sent"] = True
            await asyncio.sleep(0.1)
            if not conn.get("closed"):
                rw, rh = int(self.desktop_res[0]) & ~1, int(self.desktop_res[1]) & ~1
                w_l, h_l = max(64, rw), max(64, rh)
                self.desktop_res = (w_l, h_l)
                self.desktop_rotation = 1 if h_l > w_l else 0
                await self._send_ws_text(
                    conn, build_display_set_resolution(self, w_l, h_l, fps=60.0),
                    f"DISPLAY_HEAL_RES_SENT(svc10 msgId3,auto,{w_l}x{h_l}"
                    f"{',portrait' if h_l > w_l else ',landscape'})")
                if not conn.get("desktop_started") and not conn.get("desktop_pending"):
                    self._as_bump_instance("post-key-sync")
                    await self._send_ws_text(
                        conn, build_desktop_stop(self),
                        "DESKTOP_RESET_ON_CONNECT_SENT(svc1 cmd5,auto)")
                await self._send_ws_text(
                    conn, build_display_query(self, DISPLAY_TYPE_REMOTE, 10),
                    "DISPLAY_QUERY_SENT(svc10 msgId1,auto)")
            if not conn.get("closed") and self.filestation == "server":
                with contextlib.suppress(Exception):
                    await self.send_fs_announce(conn)
            if not conn.get("closed"):
                conn["tasks"].append(asyncio.create_task(
                    self.clip_sync.initial_sync(conn)))
            if not conn.get("closed"):
                for ntf_sid in (100, 35):
                    ntf_en = json.dumps(
                        _env(self, self.next_msg_id(), self.device_id_b64, ntf_sid, ntf_sid,
                             {"cmd": 1018, "ver": "1.0", "enabled": True, "granted": True}),
                        separators=(",", ":"),
                    )
                    await self._send_ws_text(conn, ntf_en, f"NTF_ENABLE_SENT(svc{ntf_sid} cmd1018)")
            if not conn.get("closed") and self.user_settings.get("wallpaper_sync", True):
                await asyncio.sleep(0.15)
                await self._send_ws_text(
                    conn, build_wallpaper_request(self),
                    "WALLPAPER_AUTO_REQ_SENT(svc25 cmd3,post-key)")
            if (not conn.get("closed")
                    and self.user_settings.get("auto_mount_phone_nfs", True)
                    and not conn.get("_pfs_auto_hs_sent")
                    and not getattr(self, "_pfs_connecting", False)
                    and (self._pfs_client is None or self.phonefs.get("state") != "ready")):
                await asyncio.sleep(0.25)
                if (not conn.get("closed")
                        and not conn.get("_pfs_auto_hs_sent")
                        and not getattr(self, "_pfs_connecting", False)
                        and (self._pfs_client is None or self.phonefs.get("state") != "ready")):
                    conn["_pfs_auto_hs_sent"] = True
                    pfs_msg, _ = build_phonefs_handshake(self)
                    await self._send_ws_text(conn, pfs_msg, "PHONEFS_AUTO_HANDSHAKE_SENT(svc168 type0)")
            if not conn.get("closed"):
                await asyncio.sleep(0.15)
                await self._send_ws_text(
                    conn, build_msg_notify_ready(self),
                    "MESSAGES_AUTO_READY_SENT(svc204 pId110 cmd18,post-key)")
                await self._send_ws_text(
                    conn, build_msg_permission_request(self, prompt_ui=False),
                    "MESSAGES_AUTO_QUERY_PERM_SENT(svc204 pId110 cmd20,post-key)")
        except (ConnectionError, ssl.SSLError, OSError, asyncio.CancelledError):
            pass

    RUNNING_SIDS_BY_FEATURE = {
        "desktop":      (1, 15, 16),
        "phone_on_pc":  (1, 15, 16),
        "appstream":    (15, 16),
        "webcam":       (3,),
        "screenshare":  (202,),
        "crosscontrol": (26, 16),
        "clipboard":    (9,),
        "files":        (168, 2, 20),
    }

    def running_service_ids(self) -> set:
        sids: set = set()
        c = self._live_ws()
        snap: dict = {}
        with contextlib.suppress(Exception):
            snap = self.live_feature_snapshot() or {}
        for fid in ("desktop", "phone_on_pc", "appstream", "webcam",
                    "screenshare", "crosscontrol"):
            if bool((snap.get(fid) or {}).get("on")):
                sids.update(self.RUNNING_SIDS_BY_FEATURE.get(fid, ()))
        cs = getattr(self, "clip_sync", None)
        if cs is not None and bool(getattr(cs, "enabled", False)):
            sids.update(self.RUNNING_SIDS_BY_FEATURE["clipboard"])
        if getattr(self, "_nfs_srv", None) is not None or getattr(self, "_fs", None):
            sids.add(168)
        if getattr(self, "_xfer_srv", None) is not None:
            sids.add(2)
        if c is not None and (c.get("fe_handshake_seen") or c.get("xfer_active")):
            sids.update((2, 20))
        if bool((getattr(self, "control_state", None) or {}).get("locked")):
            sids.add(22)
        if c is not None:
            if bool((getattr(self, "pair_service_state", None) or {}).get("connected")):
                sids.add(19)
            if bool((getattr(self, "gallery_state", None) or {}).get("active")):
                sids.add(205)
            if bool((getattr(self, "messages_state", None) or {}).get("enabled") or (getattr(self, "messages_state", None) or {}).get("triggered")):
                sids.add(204)
        return sids

    def live_pc_caps(self) -> dict:
        base = dict(self.pc_caps or DEFAULT_PC_CAPS)
        running = self.running_service_ids()
        out: dict = {}
        for k, v in base.items():
            try:
                sid = int(k)
            except (TypeError, ValueError):
                out[str(k)] = int(v)
                continue
            iv = int(v)
            out[str(k)] = (iv | PC_CAP_INUSE) if sid in running else (iv & ~PC_CAP_INUSE)
        for sid in running:
            out.setdefault(str(sid), 1 | 2 | PC_CAP_INUSE)
        return out

    def service_status_signature(self) -> tuple:
        return tuple(sorted(self.running_service_ids()))

    async def push_service_status(self, conn: dict | None = None,
                                  reason: str = "") -> bool:
        if not self.devstatus:
            return False
        c = conn or self._live_ws()
        if c is None:
            return False
        sig = self.service_status_signature()
        c["_svc_status_sig"] = sig
        ok = await self._send_ws_text(
            c, build_device_status(self),
            f"DEVSTATUS_SENT(svc25 cmd1,service-status{':' + reason if reason else ''})")
        with contextlib.suppress(Exception):
            inuse = {k: v for k, v in self.live_pc_caps().items() if int(v) & PC_CAP_INUSE}
            self.events.log("SERVICE_STATUS_PUSHED",
                            f"reason={reason or 'explicit'} running={sorted(sig)} inUse={inuse}",
                            feature="pairing")
        return bool(ok)

    async def _service_status_loop(self, conn: dict, interval: float = 1.5) -> None:
        last_sig = None
        try:
            while not conn.get("closed") and not (self._stop_evt and self._stop_evt.is_set()):
                await asyncio.sleep(interval)
                if conn.get("closed") or not self.devstatus:
                    continue
                sig = self.service_status_signature()
                if last_sig is not None and sig == last_sig:
                    continue
                last_sig = sig
                conn["_svc_status_sig"] = sig
                await self._send_ws_text(
                    conn, build_device_status(self),
                    "DEVSTATUS_SENT(svc25 cmd1,service-status-change)")
                with contextlib.suppress(Exception):
                    inuse = {k: v for k, v in self.live_pc_caps().items()
                             if int(v) & PC_CAP_INUSE}
                    self.events.log("SERVICE_STATUS_CHANGED",
                                    f"conn{conn['id']:03d} running={sorted(sig)} inUse={inuse}",
                                    feature="pairing")
        except (ConnectionError, ssl.SSLError, OSError, asyncio.CancelledError):
            pass

    async def _telemetry_sync_loop(self, conn: dict, interval: float = 15.0) -> None:
        last_sig = None
        try:
            while not conn.get("closed") and not (self._stop_evt and self._stop_evt.is_set()):
                await asyncio.sleep(interval)
                if conn.get("closed"):
                    break
                snap = self.get_pc_telemetry_snapshot()
                sig = (snap.get("battery"), snap.get("isCharging"), snap.get("dndState"))
                if last_sig is not None and sig != last_sig and self.devstatus:
                    await self._send_ws_text(
                        conn, build_device_status(self),
                        "DEVSTATUS_SENT(svc25 cmd1,periodic-update)")
                last_sig = sig
                if conn.get("writer") is not None and self.device_name != "SELFTEST-PC":
                    os_locked = await asyncio.to_thread(self._query_os_screen_locked)
                    if os_locked is not None and bool(os_locked) != bool(self.control_state.get("locked", False)):
                        await self.send_control_lock_state(conn, bool(os_locked))
        except (ConnectionError, ssl.SSLError, OSError, asyncio.CancelledError):
            pass

    async def _handle_devstatus_in(self, conn: dict, frame: dict, data, cmd,
                                   tag: str) -> None:
        d = data if isinstance(data, dict) else {}
        if cmd == 2:
            b64 = str(d.get("wallpaper") or "")
            md5 = str(d.get("md5") or "")
            if not b64:
                self.events.log("WALLPAPER_RECV_EMPTY",
                                f"{tag} md5={md5!r} but no image data — the phone's "
                                f"wallpaper-sync gate is closed: it needs the "
                                f"phone's wallpaper-sync toggle ON, READ_MEDIA_IMAGES (SDK>=33) "
                                f"and All-files access. Its "
                                f"wallpaperState bitmask says which: bit1(2)=permissions, "
                                f"bit2(4)=toggle", feature="wallpaper")
                return
            try:
                raw = base64.b64decode(b64)
            except (ValueError, TypeError) as exc:
                self.events.log("WALLPAPER_DECODE_FAIL", f"{tag} {exc!r}",
                                feature="wallpaper")
                return
            wdir = Path(self.state_dir) / "wallpapers"
            with contextlib.suppress(OSError):
                wdir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            dest = wdir / "phone_wallpaper_latest.png"
            with contextlib.suppress(OSError):
                dest.write_bytes(raw)
            with contextlib.suppress(OSError):
                out_wdir = self.out_dir / "wallpapers"
                out_wdir.mkdir(parents=True, exist_ok=True)
                (out_wdir / f"phone_wallpaper_{stamp}.png").write_bytes(raw)
            digest = hashlib.md5(raw).hexdigest()
            self.phone_wallpaper = {"md5": md5 or digest, "saved": str(dest), "bytes": len(raw)}
            self.events.log("WALLPAPER_RECV",
                            f"{tag} {len(raw)} bytes -> {dest} "
                            f"md5(announced)={md5} md5(actual)={digest} "
                            f"match={md5.lower() == digest.lower() if md5 else 'n/a'}",
                            feature="wallpaper")
            if self.user_settings.get("apply_desktop_wallpaper", False):
                self.apply_wallpaper_to_linux_desktop(dest)
            if callable(self.on_wallpaper_cb):
                with contextlib.suppress(Exception):
                    self.on_wallpaper_cb(self.phone_wallpaper)
            return
        if cmd == 6:
            self.events.log("DND_RECV",
                            f"{tag} phone dndState={d.get('dndState')}", feature="pairing")
            return
        if cmd == 3:
            self.events.log("DEVSTATUS_REQ_RECV", f"{tag} phone requested status/wallpaper",
                            feature="pairing")
            if self.devstatus:
                await self._send_ws_text(conn, build_device_status(self, frame.get("msgId")),
                                         "DEVSTATUS_SENT(svc25 cmd1,on-cmd3)")
            return
        if cmd == 20:
            caps = d.get("AiCapabilities") or d.get("AICapabilities") or []
            self.phone_telemetry["aiCapabilities"] = caps if isinstance(caps, list) else []
            self.events.log("AI_CAPS_RECV",
                            f"{tag} {len(caps) if isinstance(caps, list) else '?'} "
                            f"items: {str(caps)[:200]}", feature="pairing")
            return
        if cmd == 22:
            self.phone_telemetry["untrusted"] = bool(d.get("mUntrusted"))
            self.events.log("DEVSTATUS_TRUST_RECV",
                            f"{tag} mUntrusted={d.get('mUntrusted')}", feature="pairing")
            return
        self.events.log("DEVSTATUS_MSG", f"{tag} cmd={cmd} keys={sorted(d.keys())}",
                        feature="pairing")

    async def _handle_control(self, conn: dict, frame: dict, data: dict | None,
                              cmd: int | None, tag: str) -> None:
        d = data if isinstance(data, dict) else {}
        if cmd is None and isinstance(d.get("data"), dict):
            cmd = d["data"].get("cmd")
        peer_id = str(
            ((conn.get("hello") or {}).get("data") or {}).get("device", {}).get("realDeviceId")
            or frame.get("deviceId")
            or self.pc_real_device_id
            or self.device_id_b64
        )
        self.control_state["last_cmd"] = cmd
        if cmd == 1:
            inv = self.control_state["invite"]
            inv["count"] = int(inv.get("count", 0)) + 1
            inv["last_ts"] = time.time()
            inv["last_device_id"] = peer_id
            inv["last_msg_id"] = frame.get("msgId")
            self.control_state["locked"] = True
            self.control_state["state"] = 1
            self.control_state["pc_lock_state"][peer_id] = 1
            self.events.log(
                "CONTROL_INVITE_RECV",
                f"{tag} peer={peer_id} -> locking PC & replying cmd:2 state=1",
                feature="control",
            )
            should_lock_os = bool(
                getattr(self, "control_os_lock", False)
                or (
                    self.control_state.get("os_lock_enabled", True)
                    and conn.get("writer") is not None
                    and self.device_name != "SELFTEST-PC"
                    and os.environ.get("SC_DISABLE_OS_LOCK") != "1"
                )
            )
            if should_lock_os:
                await asyncio.to_thread(self._try_os_lock_screen)
            reply = build_control_lock_state(self, 1, ver=str(d.get("ver") or "1"),
                                             msg_id=frame.get("msgId"))
            await self._send_ws_text(conn, reply, "CONTROL_LOCK_STATE_SENT(svc22 cmd2,state=1)")
            await self.push_service_status(conn, reason="svc22-locked")
            return
        if cmd == 2:
            inner = d.get("data") if isinstance(d.get("data"), dict) else d
            st = int(inner.get("state", d.get("state", 0)) or 0)
            self.control_state["state"] = st
            self.control_state["locked"] = (st == 1)
            if st == 1:
                self.control_state["pc_lock_state"][peer_id] = 1
            else:
                self.control_state["pc_lock_state"].pop(peer_id, None)
            self.events.log(
                "CONTROL_LOCK_STATE_RECV",
                f"{tag} peer={peer_id} state={st} locked={self.control_state['locked']}",
                feature="control",
            )
            await self.push_service_status(conn, reason=f"svc22-state-{st}")
            return
        self.events.log("CONTROL_MSG_UNKNOWN", f"{tag} cmd={cmd} keys={sorted(d.keys())}", feature="control")

    def _try_os_lock_screen(self) -> str:
        for cmd_args, name in (
            (["loginctl", "lock-session"], "loginctl"),
            (["xdg-screensaver", "lock"], "xdg-screensaver"),
            (
                [
                    "gdbus", "call", "--session",
                    "--dest", "org.gnome.ScreenSaver",
                    "--object-path", "/org/gnome/ScreenSaver",
                    "--method", "org.gnome.ScreenSaver.Lock",
                ],
                "gdbus-gnome-screensaver",
            ),
            (
                [
                    "gdbus", "call", "--session",
                    "--dest", "org.freedesktop.ScreenSaver",
                    "--object-path", "/org/freedesktop/ScreenSaver",
                    "--method", "org.freedesktop.ScreenSaver.Lock",
                ],
                "gdbus-freedesktop-screensaver",
            ),
            (["qdbus", "org.freedesktop.ScreenSaver", "/ScreenSaver", "Lock"], "qdbus-screensaver"),
            (["gnome-screensaver-command", "-l"], "gnome-screensaver"),
            (["cinnamon-screensaver-command", "-l"], "cinnamon-screensaver"),
            (["mate-screensaver-command", "-l"], "mate-screensaver"),
            (["dm-tool", "lock"], "dm-tool"),
        ):
            if shutil.which(cmd_args[0]):
                with contextlib.suppress(Exception):
                    r = subprocess.run(cmd_args, capture_output=True, text=True, timeout=2.5)
                    if r.returncode == 0:
                        self.control_state["last_os_lock_method"] = name
                        self.events.log("CONTROL_OS_LOCK_OK", f"locked Linux desktop via {name}", feature="control")
                        return name
        self.control_state["last_os_lock_method"] = "logged_only"
        return "logged_only"

    def _query_os_screen_locked(self) -> bool | None:
        if shutil.which("loginctl"):
            with contextlib.suppress(Exception):
                sid = os.environ.get("XDG_SESSION_ID", "").strip()
                cmd = ["loginctl", "show-session", "-p", "LockedHint", "--value"]
                if sid:
                    cmd.append(sid)
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=1.5)
                if r.returncode == 0:
                    val = r.stdout.strip().lower()
                    if val in ("yes", "true", "1"):
                        return True
                    if val in ("no", "false", "0"):
                        return False
        if shutil.which("gdbus"):
            for dest, obj_p in (
                ("org.gnome.ScreenSaver", "/org/gnome/ScreenSaver"),
                ("org.freedesktop.ScreenSaver", "/org/freedesktop/ScreenSaver"),
            ):
                with contextlib.suppress(Exception):
                    r = subprocess.run(
                        ["gdbus", "call", "--session", "--dest", dest, "--object-path", obj_p, "--method", f"{dest}.GetActive"],
                        capture_output=True, text=True, timeout=1.5,
                    )
                    if r.returncode == 0:
                        out = r.stdout.strip().lower()
                        if "true" in out:
                            return True
                        if "false" in out:
                            return False
        return None

    async def send_control_lock_state(self, conn: dict | None, locked: bool | int) -> dict:
        c = conn or self._live_ws()
        st = 1 if locked in (1, True, "1", "locked", "true", "on") else 0
        peer_id = str(
            ((c.get("hello") or {}).get("data") or {}).get("device", {}).get("realDeviceId")
            if isinstance(c, dict) else ""
        ) or str(self.pc_real_device_id or self.device_id_b64)
        self.control_state["state"] = st
        self.control_state["locked"] = (st == 1)
        if st == 1:
            self.control_state["pc_lock_state"][peer_id] = 1
        else:
            self.control_state["pc_lock_state"].pop(peer_id, None)
        if c is None:
            return {"ok": False, "state": st, "detail": "no live phone connection (state recorded locally)"}
        payload = build_control_lock_state(self, st)
        ok = await self._send_ws_text(c, payload, f"CONTROL_LOCK_STATE_SENT(svc22 cmd2,state={st})")
        await self.push_service_status(c, reason=f"svc22-lock-{st}")
        return {"ok": bool(ok), "state": st, "locked": (st == 1), "detail": f"svc-22 cmd:2 state={st}"}

    async def send_control_invite(self, conn: dict | None = None,
                                  is_cloud: bool = False) -> dict:
        c = conn or self._live_ws()
        net_type = int((c or {}).get("network_type", 0) or 0)
        if is_cloud or net_type == 3:
            self.control_state["invite"]["cloud_rejected"] = int(
                self.control_state["invite"].get("cloud_rejected", 0)
            ) + 1
            self.events.log("CONTROL_INVITE_SKIP_CLOUD", "svc-22 control invite ignored on cloud connection", feature="control")
            return {"ok": False, "skipped_cloud": True, "detail": "svc-22 control invite not supported on cloud connection"}
        if c is None:
            return {"ok": False, "detail": "no live phone connection"}
        payload = build_control_invite(self)
        ok = await self._send_ws_text(c, payload, "CONTROL_INVITE_SENT(svc22 cmd1)")
        return {"ok": bool(ok), "detail": "svc-22 cmd:1 control invite sent"}

    def _notify_ui_feature_started(self, feature_id: str, conn: dict | None = None,
                                    view: int | None = None, force_present: bool = False,
                                    **extra_kw) -> None:
        pm = getattr(self, "power_mgr", None)
        if pm is not None:
            with contextlib.suppress(Exception):
                pm.note_service_active(feature_id, True)
                if force_present:
                    pm.set_window_state(feature_id, "foreground")
        cb = getattr(self, "on_feature_start_cb", None)
        if callable(cb):
            with contextlib.suppress(Exception):
                cb(feature_id, conn=conn, view=view, force_present=force_present, **extra_kw)

    def live_feature_snapshot(self) -> dict:
        c = self._live_ws()
        s = (c.get("session") or {}) if c else {}
        as_pkg = (c.get("as_pkg") if c else None)
        as_sessions = (c.get("as_sessions") or {}) if c else {}
        app_sessions = {
            k: v for k, v in as_sessions.items()
            if isinstance(v, dict) and v.get("pkg") and v.get("pkg") not in ("desktop", "mobile", "mirror")
        }
        desk_sessions = {
            k: v for k, v in as_sessions.items()
            if isinstance(v, dict) and v.get("pkg") in ("desktop", "mobile", "mirror")
        }
        pseudo = as_pkg in (None, "desktop", "mobile", "mirror")
        stopped_cooldown = bool(c and time.monotonic() < c.get("_user_stopped_until", 0.0))
        desktop_on = bool(
            c and not stopped_cooldown and (
                c.get("desktop_started")
                or bool(desk_sessions)
                or (c.get("as_session_id") and pseudo)
                or (c.get("as_setup_seen") and as_pkg in ("desktop", "mobile", "mirror"))
            )
        )
        appstream_on = bool(
            c and (
                bool(app_sessions)
                or ((c.get("as_session_id") or c.get("as_setup_seen")) and not pseudo)
            )
        )
        view = (c.get("desktop_view") if c else None) or (
            {"mirror": 1, "desktop": 2, "mobile": 3}.get(as_pkg, 2)
        )
        _as_sid = (c.get("as_session_id") if c else None)
        _desk_sid = (c.get("desktop_as_sid") if c else None) or (next(iter(desk_sessions), None) if desk_sessions else (_as_sid if pseudo else None))
        _app_sid = (c.get("app_as_sid") if c else None) or (next(iter(app_sessions), None) if app_sessions else (_as_sid if not pseudo else None))
        _app_pkg = (c.get("app_pkg") if c else None) or (app_sessions[_app_sid].get("pkg") if _app_sid in app_sessions else (as_pkg if not pseudo else None))
        gen = s.get("gen", 0)
        return {
            "desktop": {
                "on": desktop_on and view == 2,
                "key": f"d{gen}-v{view}-{_desk_sid or _as_sid or ''}",
                "view": view,
                "sid": _desk_sid or _as_sid or gen,
                "pkg": "desktop",
            },
            "phone_on_pc": {
                "on": desktop_on and view in (1, 3),
                "key": f"m{gen}-v{view}-{_desk_sid or _as_sid or ''}",
                "view": view,
                "sid": _desk_sid or _as_sid or gen,
                "pkg": "mobile" if view == 3 else "mirror",
            },
            "appstream": {
                "on": appstream_on,
                "key": f"a{gen}-{_app_sid or _as_sid or _app_pkg or ''}",
                "pkg": _app_pkg or as_pkg,
                "sid": _app_sid or _as_sid or gen,
                "sessions": app_sessions,
            },
            "webcam": {"on": bool(c and c.get("wc_started")), "key": f"w{gen}", "sid": gen},
            "screenshare": {"on": bool(c and c.get("ss_started")), "key": f"s{self.ss_session_id}", "sid": self.ss_session_id},
            "crosscontrol": {"on": bool(self.uc_state.get("connected")), "key": f"u{self.uc_state.get('cid')}", "sid": self.uc_state.get("cid")},
        }

from .base import SamyujService

class TelemetryService(SamyujService):
    service_id = 25
    id = "telemetry"
    name = "PC & Phone Telemetry Service"
    description = "Real Linux PC hardware telemetry (battery, AC, CPU, RAM, temp) + svc-25 device-status, Wallpaper & DND sync"

    def actions(self) -> list[dict]:
        return [
            {"name": "sync_telemetry", "label": "Push PC Telemetry (cmd:1)", "description": "Read Linux sysfs/procfs telemetry & push svc-25 cmd:1"},
            {"name": "set_battery", "label": "Set & Push Battery %", "description": "Override PC battery percentage and push svc-25 cmd:1", "default_params": {"battery": 90}},
            {"name": "dnd_on", "label": "Sync DND ON (cmd:6)", "description": "Send svc-25 cmd:6 dndState=1"},
            {"name": "dnd_off", "label": "Sync DND OFF (cmd:6)", "description": "Send svc-25 cmd:6 dndState=0"},
            {"name": "wallpaper_pull", "label": "Pull Phone Wallpaper (cmd:3)", "description": "Request phone wallpaper PNG (svc-25 cmd:3)"},
            {"name": "wallpaper_settings", "label": "Open Wallpaper UI (cmd:4)", "description": "Open the phone's wallpaper-sync screen"},
            {"name": "dismiss_dialog", "label": "Dismiss Phone Dialog (cmd:21)", "description": "Dismiss the phone's Smart Connect dialog"},
        ]

    def get_state(self) -> dict:
        return {
            **super().get_state(),
            "pc": self.h.get_pc_telemetry_snapshot(),
            "phone": dict(getattr(self.h, "phone_telemetry", {}) or {}),
        }

    async def invoke(self, action: str, params: dict | None = None,
                     conn: dict | None = None) -> dict:
        params = params or {}
        if action == "sync_telemetry":
            return await self.h.request("telemetry", conn)
        if action == "set_battery":
            b = params.get("battery")
            self.h.pc_battery_override = int(b) if b is not None else None
            if b is not None:
                self.h.pc_battery = max(0, min(100, int(b)))
            return await self.h.request("telemetry", conn)
        if action == "dnd_on":
            return await self.h.request("dnd-on", conn)
        if action == "dnd_off":
            return await self.h.request("dnd-off", conn)
        if action == "wallpaper_pull":
            return await self.h.request("wallpaper-pull", conn)
        if action == "wallpaper_settings":
            return await self.h.request("wallpaper-settings", conn)
        if action == "dismiss_dialog":
            return await self.h.request("dialog-dismiss", conn)
        return await super().invoke(action, params, conn)

class ControlService(SamyujService):
    service_id = 22
    id = "control"
    name = "PC Lock & Control Service"
    description = (
        "svc-22: "
        "cmd:1 control invite, "
        "cmd:2 {ver:1,cmd:2,state:0|1} (PC->phone lock state sync)"
    )

    def actions(self) -> list[dict]:
        return [
            {"name": "lock_state_locked", "label": "Report PC Locked (cmd:2 state:1)", "description": "Send svc-22 cmd:2 {state:1} so phone marks PC locked"},
            {"name": "lock_state_unlocked", "label": "Report PC Unlocked (cmd:2 state:0)", "description": "Send svc-22 cmd:2 {state:0} so phone marks PC unlocked"},
            {"name": "invite", "label": "Send Control Invite (cmd:1)", "description": "Send svc-22 cmd:1 control invite (non-cloud)"},
        ]

    def get_state(self) -> dict:
        st = dict(getattr(self.h, "control_state", {}) or {})
        return {
            **super().get_state(),
            **st,
        }

    async def invoke(self, action: str, params: dict | None = None,
                     conn: dict | None = None) -> dict:
        params = params or {}
        c = conn or self.h._live_ws()
        if action in ("lock_state", "set_lock_state"):
            val = 1 if params.get("state") in (1, True, "1", "locked", "true", "on") else 0
            return await self.h.request(f"control-lock-state:{val}", c)
        if action in ("lock_state_locked", "lock"):
            return await self.h.request("control-lock-state:1", c)
        if action in ("lock_state_unlocked", "unlock"):
            return await self.h.request("control-lock-state:0", c)
        if action == "invite":
            return await self.h.request("control-invite", c)
        return await super().invoke(action, params, conn)

ControlTelemetryService = ControlService
