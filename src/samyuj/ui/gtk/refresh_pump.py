from __future__ import annotations
from samyuj.ui.gtk.assets import open_dialog_nonblocking

import asyncio
import contextlib
import time

from .gtk_compat import Gdk, GLib, Gtk

from .windows import FeatureStreamGtkWindow

class AppRefreshPumpMixin:

    def _is_service_active(self, feat_id: str, snap: dict) -> bool:
        if feat_id in ("phone_on_pc", "share_phone_screen"):
            return bool((snap.get("phone_on_pc") or {}).get("on"))
        if feat_id == "desktop":
            return bool((snap.get("desktop") or {}).get("on"))
        if feat_id == "appstream":
            return bool((snap.get("appstream") or {}).get("on"))
        if feat_id == "webcam":
            return bool((snap.get("webcam") or {}).get("on"))
        if feat_id == "share_pc_screen":
            return bool((snap.get("screenshare") or {}).get("on"))
        if feat_id == "screenshare":
            return bool((snap.get("screenshare") or {}).get("on")) or bool((snap.get("phone_on_pc") or {}).get("on"))
        if feat_id == "crosscontrol":
            return bool((snap.get("crosscontrol") or {}).get("on")) or self.edge_controller.enabled
        if feat_id == "clipboard":
            return bool(self.facade.clip_sync.enabled)
        if feat_id == "files":
            return self.facade.phonefs.get("state") == "ready" or self.facade.linux_mount_mgr.state == "mounted"
        if feat_id == "gallery":
            return bool((getattr(self.facade, "gallery_state", {}) or {}).get("mounted"))
        if feat_id == "messages":
            return bool((getattr(self.facade, "messages_state", {}) or {}).get("usable"))
        if feat_id == "control_lock":
            return bool((getattr(self.facade, "control_state", {}) or {}).get("locked"))
        return False

    def _auto_open_phone_started_windows(self, snap: dict) -> None:
        c = self.facade._live_ws()
        if not c:
            return
        now_m = time.monotonic()
        for fid in ("desktop", "phone_on_pc", "appstream", "webcam", "screenshare"):
            if now_m < self._feature_stop_cooldown.get(fid, 0.0):
                continue
            info = snap.get(fid) or {}
            if not info.get("on"):
                self._seen_session_keys.pop(fid, None)
                continue
            skey = f"{fid}:{c.get('id')}:{info.get('key') or info.get('sid') or info.get('view') or 1}"
            win = self.feature_windows.get(fid)
            win_visible = bool(win is not None and win.get_visible())
            if self._seen_session_keys.get(fid) == skey and win_visible:
                continue
            if self._closed_session_keys.get(fid) == skey:
                continue
            self._seen_session_keys[fid] = skey
            if fid in ("desktop", "phone_on_pc"):
                other = "phone_on_pc" if fid == "desktop" else "desktop"
                ow = self.feature_windows.get(other)
                if ow is not None and ow.get_visible():
                    ow.hide()
            self.open_feature_window(fid, start_session=False)

        if ((snap.get("crosscontrol") or {}).get("on")
                and not self.edge_controller.enabled
                and not getattr(self.edge_controller, "user_disabled", False)
                and self.facade.user_settings.get("cross_control_edge_capture", False)):
            self.edge_controller.start()

    def _refresh_all(self) -> None:
        c = self.facade._live_ws() if hasattr(self.facade, "_live_ws") else None
        clip_sync = getattr(self.facade, "clip_sync", None)
        if (clip_sync is not None and c is not None
                and getattr(clip_sync, "enabled", True)):
            _now_c = time.monotonic()
            _clip_event_mode = False
            with contextlib.suppress(Exception):
                _clip_event_mode = str(clip_sync.watch_method()) == "event"
            if _now_c - self._last_clip_poll >= (10.0 if _clip_event_mode else 2.0):
                self._last_clip_poll = _now_c
                with contextlib.suppress(Exception):
                    def _polled(_c, txt, *_a):
                        if isinstance(txt, str):
                            self._clip_cache = txt
                            self._clip_cache_mono = time.monotonic()
                        with contextlib.suppress(Exception):
                            clip_sync.on_pc_clipboard_polled(txt)
                    self._gtk_clipboard.request_text(_polled)
        dev_name = self.facade._best_device_name(c)
        self.home_device_name_lbl.set_text(dev_name)
        self.home_notif_sub_lbl.set_text(f"Recent activity from your {dev_name}")
        self.sharing_sub_lbl.set_text(
            f"Drag and drop multiple files or folders here to share them immediately with your {dev_name}."
        )

        is_usb = self.facade._is_usb_conn(c) if c else bool(self.facade.usb_link)
        p_ctx = self.conn_pill_box.get_style_context()
        if c is not None:
            p_ctx.remove_class("conn-pill-waiting")
            p_ctx.add_class("conn-pill-connected")
            self.conn_pill_lbl.set_text("Connected · USB" if is_usb else "Connected · Wi-Fi")
        else:
            p_ctx.remove_class("conn-pill-connected")
            p_ctx.add_class("conn-pill-waiting")
            saved = self.facade.list_saved_phones() if hasattr(self.facade, "list_saved_phones") else []
            if saved:
                self.conn_pill_lbl.set_text(f"Reconnecting to {dev_name}…")
            else:
                self.conn_pill_lbl.set_text("Ready to pair · Click Pair New Phone (QR)")

        with contextlib.suppress(Exception):
            _usbmsg = str(getattr(self.facade, "usb_status_msg", "") or "").strip()
            _cfg = self.facade.usb_config_manager.status_dict() if getattr(self.facade, "usb_config_manager", None) else {}
            if _cfg.get("status") == "needs_privilege" and _cfg.get("required_privilege_action"):
                _usbmsg = f"USB needs permission: {_cfg['required_privilege_action']}"
            self.usb_status_lbl.set_text(("USB: " + _usbmsg) if _usbmsg else "USB: standby")

        demo_mode = bool((getattr(self.facade, "user_settings", {}) or {}).get("demo_mode", False))
        bat = self.facade.phone_telemetry.get("battery")
        chg = self.facade.phone_telemetry.get("isCharging")
        if isinstance(bat, int) and bat > 0:
            self.home_battery_lbl.set_text(f"{bat}% battery{' · Charging' if chg else ''}")
        elif demo_mode:
            self.home_battery_lbl.set_text("82% battery")
        else:
            self.home_battery_lbl.set_text("Battery pending")

        st_free = int(self.facade.phone_storage.get("free_bytes") or (108 * (1024 ** 3) if demo_mode else 0))
        st_tot = int(self.facade.phone_storage.get("total_bytes") or (128 * (1024 ** 3) if demo_mode else 0))
        if st_tot > 0:
            self.home_storage_lbl.set_text(
                f"{st_free / (1024 ** 3):.0f} GB free / {st_tot / (1024 ** 3):.0f} GB"
            )
        else:
            self.home_storage_lbl.set_text("Storage pending")

        if self.facade.phone_wallpaper and self.facade.phone_wallpaper.get("saved"):
            self.handset_preview.set_wallpaper_from_file(self.facade.phone_wallpaper["saved"])

        snap = self.facade.live_feature_snapshot() if hasattr(self.facade, "live_feature_snapshot") else {}
        self._auto_open_phone_started_windows(snap)

        with contextlib.suppress(Exception):
            _ssw = self.feature_windows.get("screenshare")
            if (isinstance(_ssw, FeatureStreamGtkWindow)
                    and (snap.get("screenshare") or {}).get("on")):
                _vd = (self.facade.ss_stats or {}).get("virtualDisplay") or {}
                _cap = (self.facade.ss_stats or {}).get("capture") or ""
                if str(getattr(self.facade, "ss_share_type", "")).lower() == "extend":
                    if _vd.get("ok"):
                        _ori = str(getattr(self.facade, "ss_virtual_orient", "auto"))
                        _pos = str(_vd.get("position") or "none")
                        _att = bool(_vd.get("attached"))
                        _edge = (f"attached at the {_pos} edge"
                                 if _att else
                                 f"placed {_pos} but the pointer cannot cross onto it")
                        _ssw.status_lbl.set_text(
                            f"Virtual Display · {_vd.get('backend')} "
                            f"{_vd.get('name')} {_vd.get('width')}×{_vd.get('height')} {_ori} "
                            f"· {_edge}")
                    else:
                        _ssw.status_lbl.set_text(
                            f"Extend unavailable — mirroring instead "
                            f"({_vd.get('detail') or 'no backend'})")
                elif _cap:
                    _ssw.status_lbl.set_text(f"Mirror · {_cap}")
                if (str(getattr(self.facade, "ss_share_type", "")).lower() == "extend"
                        and not _vd.get("ok")
                        and not getattr(self, "_extend_warned", False)):
                    self._extend_warned = True
                    _hint = str((self.facade.ss_stats or {}).get("extend_fix_hint")
                                or _vd.get("detail") or "")
                    _dlg = Gtk.MessageDialog(
                        transient_for=self.win, modal=True,
                        message_type=Gtk.MessageType.WARNING,
                        buttons=Gtk.ButtonsType.OK,
                        text="Virtual Display could not be created")
                    _dlg.format_secondary_text(
                        "Share Screen is mirroring your primary display instead, "
                        "which is why the phone looks the same in both modes." "\n\n"
                        f"Reason: {_vd.get('detail') or 'unknown'}\n\n"
                        f"To fix it on this machine:\n{_hint}\n\n"
                        f"After installing, reopen Share Screen → Virtual Display.")
                    open_dialog_nonblocking(_dlg)
            with contextlib.suppress(Exception):
                if str(getattr(self.facade, "ss_share_type", "")).lower() != "extend":
                    self._extend_warned = False

        for fid, fwin in list(self.feature_windows.items()):
            if not isinstance(fwin, FeatureStreamGtkWindow):
                continue
            info = snap.get(fid) or {}
            if not info.get("on") and (
                getattr(fwin, "_cairo_surface", None) is not None
                or getattr(fwin, "_pixbuf_store", None) is not None
                or getattr(fwin, "_lazy_rgb_frame", None) is not None
                or getattr(fwin, "_pixbuf", None) is not None
            ):
                fwin._pixbuf = None
                fwin._last_pts = None
                fwin._last_frame_dims = None
                fwin._last_adapted_aspect = None
                fwin._video_rect = (0.0, 0.0, 1.0, 1.0)
                with contextlib.suppress(Exception):
                    fwin.status_lbl.set_text("Session ended — start again from the dashboard")
                    fwin.video_da.queue_draw()

        for fid, rw in self._rail_widgets.items():
            active = self._is_service_active(fid, snap)
            c_ctx = rw["container"].get_style_context()
            t_ctx = rw["title_lbl"].get_style_context()
            if active:
                c_ctx.add_class("feature-rail-item-active")
                t_ctx.add_class("feature-rail-title-active")
                rw["state_lbl"].set_text("Running")
                rw["state_lbl"].show()
                rw["cancel_btn"].show()
            else:
                c_ctx.remove_class("feature-rail-item-active")
                t_ctx.remove_class("feature-rail-title-active")
                rw["state_lbl"].hide()
                rw["cancel_btn"].hide()

        for fid, gw in self._grid_widgets.items():
            active = self._is_service_active(fid, snap)
            c_ctx = gw["card"].get_style_context()
            t_ctx = gw["title_lbl"].get_style_context()
            if active:
                c_ctx.add_class("feature-tile-card-active")
                t_ctx.add_class("feature-tile-title-active")
                gw["state_lbl"].set_text("Running")
                gw["state_lbl"].show()
                gw["cancel_btn"].show()
            else:
                c_ctx.remove_class("feature-tile-card-active")
                t_ctx.remove_class("feature-tile-title-active")
                gw["state_lbl"].hide()
                gw["cancel_btn"].hide()

        self._refresh_notifications()
        self._refresh_shares_list()
        self._refresh_android_apps_flow()
        self._pump_video()

    def _on_media_frame_ready(self, _owner_service: str, _sid, _frame, _seq: int) -> None:
        self._last_frame_mono = time.monotonic()
        with contextlib.suppress(Exception):
            ui_loop = getattr(self, "_ui_loop", None)
            evt = getattr(self, "_frame_wakeup_evt", None)
            if evt is not None and ui_loop is not None and ui_loop.is_running():
                ui_loop.call_soon_threadsafe(evt.set)
        if self._video_wakeup_pending:
            return
        self._video_wakeup_pending = True

        def _flush():
            self._video_wakeup_pending = False
            with contextlib.suppress(Exception):
                self._pump_video()
            return False

        with contextlib.suppress(Exception):
            GLib.idle_add(_flush, priority=GLib.PRIORITY_DEFAULT_IDLE)

    async def _wait_frame_or_tick(self, timeout_s: float = 1.0 / 60.0) -> None:
        evt = getattr(self, "_frame_wakeup_evt", None)
        if evt is None:
            await asyncio.sleep(1.0 / 60.0)
            return
        if evt.is_set():
            evt.clear()
            await asyncio.sleep(0)
            return
        try:
            await asyncio.wait_for(evt.wait(), timeout=timeout_s)
        except (asyncio.TimeoutError, TimeoutError):
            pass
        finally:
            evt.clear()

    def _pump_video(self) -> None:
        c = self.facade._live_ws() if hasattr(self.facade, "_live_ws") else None
        if not self.feature_windows:
            return
        reg = getattr(self.facade, "stream_registry", None)
        snap = self.facade.live_feature_snapshot() if hasattr(self.facade, "live_feature_snapshot") else {}
        for fid, fwin in list(self.feature_windows.items()):
            if not (fwin.get_visible() and isinstance(fwin, FeatureStreamGtkWindow)):
                continue
            lookup = "desktop" if fid == "phone_on_pc" else ("appstream" if str(fid).startswith("appstream:") else fid)
            if snap and not (snap.get(fid) or {}).get("on"):
                continue
            frame_for_win = None
            frame_seq = None
            frame_uv_swapped = None
            target_sid = getattr(fwin, "stream_session_id", None)
            if reg is not None:
                matched_rec = None
                with contextlib.suppress(Exception):
                    def _matches_domain(sid_k, r) -> bool:
                        is_wc_rec = (r.owner_service == "webcam" or str(sid_k) == "webcam" or str(r.session_id).startswith("wc"))
                        if lookup == "webcam":
                            return is_wc_rec
                        if is_wc_rec:
                            return False
                        return r.owner_service == lookup or str(sid_k).startswith(f"{lookup}:")

                    if target_sid is not None:
                        for sid_k, r in reversed(list(reg.streams.items())):
                            if r.last_frame is not None and r.status == "live" and _matches_domain(sid_k, r) and str(r.session_id) == str(target_sid):
                                matched_rec = r
                                break
                    if matched_rec is None:
                        for sid_k, r in reversed(list(reg.streams.items())):
                            if r.last_frame is None or r.status != "live":
                                continue
                            if _matches_domain(sid_k, r):
                                matched_rec = r
                                break
                    if matched_rec is None and target_sid is not None:
                        for sid_k, r in reversed(list(reg.streams.items())):
                            if r.last_frame is not None and r.status != "stopped" and _matches_domain(sid_k, r) and str(r.session_id) == str(target_sid):
                                matched_rec = r
                                break
                    if matched_rec is None:
                        for sid_k, r in reversed(list(reg.streams.items())):
                            if r.last_frame is None or r.status == "stopped":
                                continue
                            if _matches_domain(sid_k, r):
                                matched_rec = r
                                break
                    if matched_rec is not None:
                        frame_for_win = matched_rec.last_frame
                        frame_seq = matched_rec.seq
                        frame_uv_swapped = bool(matched_rec.uv_swap_applied)
                        if getattr(fwin, "_last_stream_sid", None) != matched_rec.session_id:
                            fwin._last_stream_sid = matched_rec.session_id
                            fwin.stream_session_id = matched_rec.session_id
                            fwin._last_pts = None
            if frame_for_win is None and c:
                by_svc = c.get("media_last_frame_by_service") or {}
                if lookup in by_svc:
                    _mlf = by_svc[lookup]
                    if isinstance(_mlf, (tuple, list)):
                        frame_for_win = _mlf[0]
                        frame_seq = _mlf[2] if len(_mlf) > 2 else None
                elif fid in ("desktop", "phone_on_pc", "appstream") or str(fid).startswith("appstream:"):
                    _mlf = c.get("media_last_frame")
                    if isinstance(_mlf, (tuple, list)):
                        _mlf_sid = str(_mlf[1]) if len(_mlf) > 1 else ""
                        if not _mlf_sid.startswith("wc") and _mlf_sid != "webcam":
                            frame_for_win = _mlf[0]
                            frame_seq = _mlf[2] if len(_mlf) > 2 else None
                    elif _mlf is not None:
                        frame_for_win = _mlf
            if frame_for_win is not None:
                fwin.update_from_frame(frame_for_win, seq=frame_seq,
                                       uv_swapped=frame_uv_swapped)
                if reg is not None and frame_seq is not None:
                    with contextlib.suppress(Exception):
                        for _sk, _r in reg.streams.items():
                            if _r.last_frame is frame_for_win:
                                _r.drawn_seq = int(frame_seq)
                                break

    def tick(self) -> None:
        try:
            self._refresh_all()
        except Exception as exc:
            with contextlib.suppress(Exception):
                self.facade.events.log("UI_TICK_ERR", repr(exc)[:300], feature="gui")
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)

    async def run_async(self) -> None:
        with contextlib.suppress(Exception):
            self._ui_loop = asyncio.get_running_loop()
            if self._frame_wakeup_evt is None:
                self._frame_wakeup_evt = asyncio.Event()
        self.win.show_all()
        self._refresh_all()
        last_slow = 0.0
        try:
            while not (self.facade._stop_evt and self.facade._stop_evt.is_set()):
                now = time.monotonic()
                gtk_deadline = now + 0.0035
                had_events = Gtk.events_pending()
                while Gtk.events_pending():
                    Gtk.main_iteration_do(False)
                    if time.monotonic() >= gtk_deadline:
                        break
                if had_events:
                    self._last_ui_event_mono = now
                try:
                    self._pump_video()
                except Exception as exc:
                    with contextlib.suppress(Exception):
                        self.facade.events.log("UI_PUMP_ERR", repr(exc)[:300], feature="gui")
                if now - last_slow >= (0.25 if self.win.get_visible() else 1.0):
                    last_slow = now
                    try:
                        self._refresh_all()
                    except Exception as exc:
                        with contextlib.suppress(Exception):
                            self.facade.events.log("UI_TICK_ERR", repr(exc)[:300], feature="gui")
                if (now - getattr(self, "_last_frame_mono", 0.0) < 1.0
                        or now - getattr(self, "_last_ui_event_mono", 0.0) < 0.5):
                    await self._wait_frame_or_tick(1.0 / 60.0)
                else:
                    await self._wait_frame_or_tick(1.0 / 20.0)
        finally:
            self.edge_controller.stop()

    def _on_destroy(self, *_args) -> None:
        self.edge_controller.stop()
        with contextlib.suppress(Exception):
            self.facade.request_stop()
