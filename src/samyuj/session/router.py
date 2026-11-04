from __future__ import annotations
import os
from samyuj.core.logging import sha256_hex
from samyuj.media.frame_hub import target_render_size
from samyuj.protocol.envelope import _env
_DRIVE_CMD = {0: 'HANDSHAKING', 1: 'SERVER_DATA', 2: 'DISCONNECT', 3: 'HEARTBEAT', 5: 'CONNECTED', 7: 'REMOVE_SERVICE'}
_DRIVE_STATUS = {-1: 'INVALID', 0: 'SUCCESS', 1: 'NOT_MATCH_VERSION', 2: 'NOT_MATCH_STATUS', 3: 'ADMIN_DISABLED', 4: 'REQUEST_DENY', 5: 'REQUEST_TIMEOUT', 6: 'BUSY', 7: 'PERMISSION_DENY'}
from samyuj.session.registry import ServiceSessionRegistry

import asyncio
import contextlib
import json
import time
from typing import Any

import datetime
import hmac
import ssl
from samyuj.core.events import _console_show, _feature_of_sid
from samyuj.core.logging import ConnLog, sha256_b64
from samyuj.core.tasks import get_mobile_desktop_service_thread
from samyuj.protocol.services import (
    build_checkin,
    build_device_status,
    build_exchange_key,
    build_reply_json,
    build_server_first_hello,
)
from samyuj.transport.connection import set_tcp_nodelay as _set_tcp_nodelay
from samyuj.transport.websocket import WsFrameParser, ws_accept_value, ws_frame
from .models import PeerSession, new_session_state

class SessionRouterMixin:

    async def _on_connection(self, reader, writer) -> None:
        conn_id = self._next_conn_id
        self._next_conn_id += 1
        _set_tcp_nodelay(writer)
        peer = writer.get_extra_info("peername")
        ssl_obj = writer.get_extra_info("ssl_object")
        conn = PeerSession({
            "id": conn_id, "reader": reader, "writer": writer, "remote": str(peer),
            "mode": "detect", "ws": False, "bytes_in": 0, "bytes_out": 0,
            "role": "unknown", "readiness": "connecting", "tunnel": False,
            "transition_lock": asyncio.Lock(),
            "log": ConnLog((self.out_dir / "connections" / f"conn_{conn_id:03d}.log") if self.debug else None,
                           log_secrets=getattr(self, "log_secrets", False),
                           disk_enabled=self.debug),
            "parser": WsFrameParser(), "http_buf": bytearray(), "closed": False,
            "hello": None, "replied": False, "pongs": 0,
            "tasks": [],
            "session": new_session_state(),
            "sessions": ServiceSessionRegistry(),
        })
        self._conns.append(conn)
        conn["log"].line(f"ACCEPT from {peer}")
        if ssl_obj is not None:
            peer_cert = ssl_obj.getpeercert(binary_form=True)
            if peer_cert:
                conn["log"].line(f"TLS peer cert: sha256hex={sha256_hex(peer_cert)} "
                                 f"sha256b64={sha256_b64(peer_cert)}")
                self.events.log("TLS_PEER_CERT", f"conn{conn_id:03d} {sha256_b64(peer_cert)}")
            conn["log"].line(f"TLS version: {ssl_obj.version()} cipher: {ssl_obj.cipher()}")
        self.events.log("TCP_ACCEPT", f"conn{conn_id:03d} from {peer}")
        try:
            while not conn["closed"]:
                data = await reader.read(65536)
                if not data:
                    conn["log"].line("EOF from peer")
                    self.events.log("CONN_EOF", f"conn{conn_id:03d}")
                    break
                conn["bytes_in"] += len(data)
                await self._feed(conn, data)
        except (ConnectionError, ssl.SSLError, OSError) as exc:
            conn["log"].line(f"CONNECTION ERROR: {exc!r}")
            self.events.log("CONN_ERROR", f"conn{conn_id:03d} {type(exc).__name__} {exc!r}")
        finally:
            conn["closed"] = True
            conn["readiness"] = "closed"
            with contextlib.suppress(Exception):
                await self._release_local_sessions(conn)
            for t in conn["tasks"]:
                t.cancel()
            with contextlib.suppress(Exception):
                writer.close()

    def _ensure_desktop_service_thread(self) -> MobileDesktopServiceThread | None:
        if not getattr(self, "use_desktop_thread", True):
            return None
        dt = getattr(self, "desktop_service_thread", None)
        if dt is None:
            dt = self.desktop_service_thread = get_mobile_desktop_service_thread(start=True)
        elif not dt.is_alive():
            dt.start()
        return dt

    def set_stream_power_state(self, feature_id: str, state: str, sid=None) -> dict:
        pm = getattr(self, "power_mgr", None)
        if pm is not None:
            return pm.set_window_state(feature_id, state, sid=sid)
        return {"state": state}

    def get_stream_power_state(self, feature_id: str, sid=None) -> str:
        pm = getattr(self, "power_mgr", None)
        if pm is not None:
            return pm.get_window_state(feature_id, sid=sid)
        return "foreground"

    def set_stream_target_size(self, key: str, width: int, height: int, sid=None) -> None:
        w, h = max(2, int(width)), max(2, int(height))
        sizes = getattr(self, "stream_target_sizes", None)
        if sizes is None:
            sizes = self.stream_target_sizes = {}
        k = str(key or "desktop")
        sizes[k] = (w, h)
        if k.startswith("appstream"):
            sizes["appstream"] = (w, h)
        if sid is not None:
            sizes[f"sid:{sid}"] = (w, h)

    def get_stream_target_size(self, owner_service: str, sid=None,
                               fw: int = 0, fh: int = 0) -> tuple[int, int] | None:
        sizes = getattr(self, "stream_target_sizes", None) or {}
        cand = None
        if sid is not None and f"sid:{sid}" in sizes:
            cand = sizes[f"sid:{sid}"]
        elif owner_service in sizes:
            cand = sizes[owner_service]
        elif owner_service == "desktop" and "phone_on_pc" in sizes:
            cand = sizes["phone_on_pc"]
        if cand is not None:
            tw, th = cand
            if fw > 0 and fh > 0:
                if (fw > fh) != (tw > th) and fw != fh and tw != th:
                    if callable(getattr(self, "on_frame_ready_cb", None)):
                        rw, rh = (410, 860) if fh > fw else (1280, 800)
                        aw, ah = target_render_size(fw, fh, rw, rh, max_upscale=1.0)
                        return (max(2, min(fw, int(aw))), max(2, min(fh, int(ah))))
                    return None
                aw, ah = target_render_size(fw, fh, tw, th, max_upscale=1.0)
                return (max(2, min(fw, int(aw))), max(2, min(fh, int(ah))))
            return (max(2, int(tw)), max(2, int(th)))
        if fw > 0 and fh > 0:
            rw, rh = (410, 860) if fh > fw else (1280, 800)
            tw, th = target_render_size(fw, fh, rw, rh, max_upscale=1.0)
            return (max(2, min(fw, int(tw))), max(2, min(fh, int(th))))
        return None

    async def _run_on_desktop_thread(self, coro, stat_key: str = "service_msgs_dispatched"):
        with contextlib.suppress(RuntimeError):
            cur = asyncio.get_running_loop()
            dt_loop = getattr(getattr(self, "desktop_service_thread", None), "loop", None)
            if cur is not dt_loop:
                self._control_loop = cur
        dt = self._ensure_desktop_service_thread()
        if dt is None or dt.is_current_loop():
            return await coro
        if stat_key:
            dt.stats[stat_key] = int(dt.stats.get(stat_key, 0)) + 1
        return await dt.run_coroutine(coro)

    async def _send_raw(self, conn: dict, data: bytes) -> None:
        try:
            cur_loop = asyncio.get_running_loop()
        except RuntimeError:
            cur_loop = None
        dt_loop = getattr(getattr(self, "desktop_service_thread", None), "loop", None)
        ctrl_loop = conn.get("_control_loop") or getattr(self, "_control_loop", None)
        if cur_loop is not None and cur_loop is not dt_loop and conn.get("_control_loop") is None:
            conn["_control_loop"] = cur_loop
            self._control_loop = cur_loop
            ctrl_loop = cur_loop
        if (
            ctrl_loop is not None
            and cur_loop is not None
            and cur_loop is not ctrl_loop
            and ctrl_loop.is_running()
        ):
            fut = asyncio.run_coroutine_threadsafe(self._send_raw(conn, data), ctrl_loop)
            await asyncio.wrap_future(fut)
            return
        if not conn.get("_tcp_nodelay_set"):
            conn["_tcp_nodelay_set"] = True
            _set_tcp_nodelay(conn.get("writer"))
        lock = conn.get("send_lock")
        if lock is None:
            lock = conn["send_lock"] = asyncio.Lock()
        async with lock:
            conn["writer"].write(data)
            await conn["writer"].drain()
            conn["bytes_out"] = conn.get("bytes_out", 0) + len(data)

    async def _send_input_nowait(self, conn: dict, data: bytes) -> bool:
        if conn.get("closed") or conn.get("writer") is None:
            return False
        lock = conn.get("send_lock")
        if lock is not None and lock.locked():
            self.input_stats["moves_dropped_busy"] += 1
            with contextlib.suppress(Exception):
                if getattr(self, "media_diag", None) is not None:
                    self.media_diag.note_input("dropped_busy")
            return False
        try:
            cur_loop = asyncio.get_running_loop()
        except RuntimeError:
            cur_loop = None
        ctrl_loop = conn.get("_control_loop") or getattr(self, "_control_loop", None)
        if (
            ctrl_loop is not None
            and cur_loop is not None
            and cur_loop is not ctrl_loop
            and ctrl_loop.is_running()
        ):
            fut = asyncio.run_coroutine_threadsafe(self._send_input_nowait(conn, data), ctrl_loop)
            return await asyncio.wrap_future(fut)
        if not conn.get("_tcp_nodelay_set"):
            conn["_tcp_nodelay_set"] = True
            _set_tcp_nodelay(conn.get("writer"))
        if lock is None:
            lock = conn["send_lock"] = asyncio.Lock()
        if lock.locked():
            self.input_stats["moves_dropped_busy"] += 1
            with contextlib.suppress(Exception):
                if getattr(self, "media_diag", None) is not None:
                    self.media_diag.note_input("dropped_busy")
            return False
        budget = float(getattr(self, "input_send_budget", 0.0) or 0.0)
        try:
            if budget > 0:
                await asyncio.wait_for(lock.acquire(), timeout=budget)
            else:
                await lock.acquire()
        except (asyncio.TimeoutError, asyncio.CancelledError):
            self.input_stats["moves_dropped_busy"] += 1
            return False
        try:
            conn["writer"].write(data)
            await conn["writer"].drain()
            conn["bytes_out"] = conn.get("bytes_out", 0) + len(data)
            return True
        except (ConnectionError, ssl.SSLError, OSError, asyncio.CancelledError):
            return False
        finally:
            with contextlib.suppress(RuntimeError):
                lock.release()

    def _input_stats_maybe_log(self, conn: dict) -> None:
        st = self.input_stats
        now = time.monotonic()
        if now - self._last_input_stats_log < 5.0:
            return
        if not (st["moves_sent"] or st["moves_dropped_busy"]):
            return
        self._last_input_stats_log = now
        total = st["moves_sent"] + st["moves_dropped_busy"] + st["moves_suppressed_dup"]
        self.events.log(
            "INPUT_DRAG_STATS",
            f"moves sent={st['moves_sent']} dropped(socket busy)={st['moves_dropped_busy']} "
            f"suppressed(duplicate)={st['moves_suppressed_dup']} press={st['press']} "
            f"release={st['release']} pli={st['pli_during_drag']} "
            f"({100.0 * st['moves_dropped_busy'] / max(1, total):.0f}% of moves dropped)",
            feature="input")

    async def _feed(self, conn: dict, data: bytes) -> None:
        mode = conn["mode"]
        if mode == "detect":
            if len(conn["http_buf"]) + len(data) > 65536:
                conn["closed"] = True
                return
            conn["http_buf"].extend(data)
            head = bytes(conn["http_buf"][:4])
            if len(conn["http_buf"]) >= 4 and head[:4] != b"GET ":
                conn["mode"] = "raw"
                self.events.log("RAW_PROTOCOL", f"conn{conn['id']:03d} NON-HTTP first bytes: "
                                                f"{head.hex()} — U4 finding!")
                conn["log"].line("*** NON-HTTP PROTOCOL — first bytes (U4 evidence) ***")
                conn["log"].bytes("RAW-RECV", bytes(conn["http_buf"][:4096]))
                conn["http_buf"].clear()
                conn["closed"] = True
                return
            if b"\r\n\r\n" in conn["http_buf"]:
                await self._handle_http(conn)
            return
        if mode == "http-pending":
            if len(conn["http_buf"]) + len(data) > 65536:
                conn["closed"] = True
                return
            conn["http_buf"].extend(data)
            if b"\r\n\r\n" in conn["http_buf"]:
                await self._handle_http(conn)
            return
        if mode == "raw":
            conn["closed"] = True
            return
        if mode == "ws":
            await self._handle_ws_frames(conn, data)
            return

    async def _handle_http(self, conn: dict) -> None:
        buf = bytes(conn["http_buf"])
        idx = buf.find(b"\r\n\r\n")
        head, rest = buf[:idx], buf[idx + 4:]
        head_text = head.decode("utf-8", errors="replace")
        conn["log"].line("HTTP REQUEST >>>\n" + head_text)
        self.events.log("HTTP_REQUEST", head_text.split("\r\n")[0])
        ws_key = None
        for line in head_text.split("\r\n"):
            if line.lower().startswith("sec-websocket-key:"):
                ws_key = line.split(":", 1)[1].strip()
        if ws_key:
            req_path = head_text.split(" ")[1] if " " in head_text else "/readyfor/"
            clean_path = req_path.split("?", 1)[0].rstrip("/")
            is_tunnel = clean_path in ("/readyfor/rdp", "/readyfor/mdns")
            accept = ws_accept_value(ws_key)
            resp = ("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                    f"Connection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n")
            await self._send_raw(conn, resp.encode())
            conn["ws"] = True
            conn["mode"] = "ws"
            conn["tunnel"] = is_tunnel
            conn["role"] = "tunnel" if is_tunnel else "control"
            conn["readiness"] = "ws_open"
            conn["bootstraps_sent"] = 0
            conn["log"].line("WS HANDSHAKE accepted (101)")
            self.events.log("WS_UPGRADE", f"conn{conn['id']:03d} path={req_path}"
                            + (" (USB ws-GATT tunnel)" if is_tunnel else ""))
            conn["http_buf"] = bytearray()
            if is_tunnel:
                async def _delayed_tunnel_bootstrap() -> None:
                    with contextlib.suppress(asyncio.CancelledError):
                        await asyncio.sleep(0.35)
                        if not conn.get("closed") and conn.get("bootstraps_sent", 0) == 0:
                            await self._send_tunnel_bootstrap(conn, why="tunnel-open")
                conn["tasks"].append(asyncio.create_task(_delayed_tunnel_bootstrap()))
            elif self.server_first and conn["id"] not in self._server_first_done:
                self._server_first_done.add(conn["id"])
                await self._send_ws_text(conn, build_server_first_hello(self), "SERVER_FIRST_HELLO")
            if rest:
                await self._handle_ws_frames(conn, rest)
        else:
            conn["log"].line("HTTP without WS upgrade — replying 400 (recorded above)")
            self.events.log("HTTP_NO_UPGRADE", f"conn{conn['id']:03d}")
            await self._send_raw(conn, b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n"
                                       b"Connection: close\r\n\r\n")
            conn["closed"] = True

    _server_first_done: set = set()

    async def _handle_ws_frames(self, conn: dict, data: bytes) -> None:
        try:
            frames = conn["parser"].feed(data or b"")
        except ValueError:
            conn["closed"] = True
            return
        for opcode, _fin, payload in frames:
            if opcode == 0x1:
                text = payload.decode("utf-8", errors="replace")
                conn["log"].line("WS TEXT >>> " + text)
                if conn.get("tunnel"):
                    self.events.log("TUNNEL_TEXT_IN", f"conn{conn['id']:03d}: {text[:400]}")
                    await self._send_tunnel_bootstrap(conn, why="tunnel-text")
                    continue
                parsed = None
                with contextlib.suppress(Exception):
                    parsed = json.loads(text)
                    conn["last_frame"] = parsed
                self.events.log("WS_MESSAGE", f"conn{conn['id']:03d}: {text}",
                                feature=_feature_of_sid(parsed))
                if _console_show(_feature_of_sid(parsed), "WS-IN"):
                    print(f"[conn{conn['id']:03d} WS-IN] {text[:600]}")
                await self._on_ws_text(conn, text, payload)
            elif opcode == 0x2:
                conn["log"].line(f"WS BINARY {len(payload)} bytes")
                conn["log"].bytes("WS-BINARY", payload)
                if conn.get("tunnel"):
                    await self._on_tunnel_binary(conn, payload)
                    continue
                self.events.log("WS_BINARY", f"conn{conn['id']:03d} {len(payload)}B")
            elif opcode == 0x8:
                code = int.from_bytes(payload[:2], "big") if len(payload) >= 2 else None
                reason = payload[2:].decode("utf-8", "replace") if len(payload) > 2 else ""
                conn["log"].line(f"WS CLOSE <<< code={code} reason={reason!r} raw={payload.hex()}")
                rec = {"conn": conn["id"], "code": code, "reason": reason,
                       "utc": datetime.datetime.now(datetime.UTC).isoformat()}
                if conn.get("hello_recv_mono"):
                    rec["deltaAfterHelloS"] = round(time.monotonic() - conn["hello_recv_mono"], 3)
                    conn["close_delta_s"] = rec["deltaAfterHelloS"]
                conn["close_code"], conn["close_reason"] = code, reason
                self.close_records.append(rec)
                if len(self.close_records) > 256:
                    del self.close_records[:-256]
                self.events.log("CLOSE_PARSED", f"conn{conn['id']:03d} code={code} "
                                                f"reason={reason!r} "
                                                f"deltaAfterHello={rec.get('deltaAfterHelloS')}s "
                                                f"raw={payload.hex()}",
                                    feature=_feature_of_sid(conn.get("last_frame")))
                with contextlib.suppress(Exception):
                    await self._send_raw(conn, ws_frame(0x8, payload))
                conn["closed"] = True
            elif opcode == 0x9:
                conn["log"].line(f"WS PING {len(payload)}B -> auto-pong")
                with contextlib.suppress(Exception):
                    await self._send_raw(conn, ws_frame(0xA, payload))
            elif opcode == 0xA:
                conn["pongs"] = conn.get("pongs", 0) + 1
                conn["log"].line(f"WS PONG {len(payload)}B (total {conn['pongs']})")
                self.events.log("WS_PONG", f"conn{conn['id']:03d} n={conn['pongs']}")
            else:
                conn["log"].line(f"WS opcode {opcode} {len(payload)}B")
                conn["log"].bytes("WS-FRAME", payload)

    async def _on_ws_text(self, conn: dict, text: str, payload: bytes | None = None) -> None:
        if payload is None:
            payload = text.encode("utf-8")
        try:
            frame = json.loads(text)
        except json.JSONDecodeError:
            self.events.log("NON_JSON_TEXT", f"conn{conn['id']:03d}")
            return
        now = time.monotonic()
        conn["last_rx_mono"] = now
        data = frame.get("data") if isinstance(frame, dict) else None
        is_hello = (isinstance(data, dict) and data.get("cmd") == 0
                    and frame.get("serviceId") == 0 and conn["hello"] is None)
        if not is_hello:
            if conn.get("hello") is None and conn.get("role") not in ("client", "tunnel") and frame.get("serviceId") != 0:
                self.events.log("UNAUTH_PRE_HELLO", f"conn{conn['id']:03d} dropped svc={frame.get('serviceId')} before HELLO")
                return
            if conn.get("readiness") == "hello_verified":
                conn["readiness"] = "paired"
            await self._on_post_hello(conn, frame)
            return
        is_tunnel = conn.get("role") == "tunnel" or bool(conn.get("tunnel"))
        tok_in = str(data.get("token") or "") if isinstance(data, dict) else ""
        expected_tok = str(self.token_b64 or "")
        tok_ok = bool(tok_in and expected_tok and hmac.compare_digest(tok_in.encode("utf-8"), expected_tok.encode("utf-8")))
        if not is_tunnel and not tok_ok:
            self.events.log("TOKEN_MISMATCH", f"conn{conn['id']:03d} rejected control HELLO with missing/invalid token")
            conn["closed"] = True
            w = conn.get("writer")
            if w is not None:
                with contextlib.suppress(Exception):
                    w.close()
            return
        conn["hello"] = frame
        conn["role"] = "control"
        dev_h = (data.get("device") or {}) if isinstance(data, dict) else {}
        already_paired = bool(data.get("paired") == 1 or (dev_h.get("realDeviceId") and (dev_h.get("realDeviceId") in getattr(self, "paired_phones", {}) or dev_h.get("realDeviceId") in (self.load_paired_phones() if hasattr(self, "load_paired_phones") else {}))))
        conn["readiness"] = "paired" if already_paired else "hello_verified"
        for prev_c in list(self._conns):
            if (prev_c is not conn and not prev_c.get("closed")
                    and prev_c.get("ws") and not prev_c.get("tunnel")
                    and prev_c.get("role") != "tunnel"):
                if self._is_usb_conn(conn) and not self._is_usb_conn(prev_c):
                    self._migrate_session_state(prev_c, conn)
                    self._close_conn(prev_c, "transport switched to USB")
                    self._usb_takeover = None
                elif not self._is_usb_conn(conn) and self._is_usb_conn(prev_c):
                    self._migrate_session_state(prev_c, conn)
                    self._close_conn(prev_c, "transport switched to Wi-Fi")
                    self._usb_takeover = None
                elif self._is_usb_conn(conn) == self._is_usb_conn(prev_c):
                    self._migrate_session_state(prev_c, conn)
                    self._close_conn(prev_c, "superseded by newer control connection")
        if getattr(self, "_last_migrated_conn", None) is not None:
            self._migrate_session_state(self._last_migrated_conn, conn)
            self._last_migrated_conn = None
        if self._usb_takeover is not None and self._is_usb_conn(conn):
            asyncio.create_task(self.check_transport_switch())
        if not conn.get("_heal_scheduled"):
            conn["_heal_scheduled"] = True
            with contextlib.suppress(Exception):
                conn["tasks"].append(asyncio.create_task(self._connect_heal(conn)))
        conn["hello_recv_mono"] = now
        conn["hello_recv_utc"] = datetime.datetime.now(datetime.UTC).isoformat()
        with contextlib.suppress(Exception):
            peer = conn["writer"].get_extra_info("peername")
            hello_ips = (data.get("ips") or []) if isinstance(data, dict) else []
            hello_port = next((int(ep.get("port")) for ep in hello_ips if isinstance(ep, dict) and ep.get("port")), None)
            self.note_phone(real_id=dev_h.get("realDeviceId"),
                            nonce_id=frame.get("deviceId"),
                            name=dev_h.get("deviceName"),
                            ip=peer[0] if peer else None,
                            ws_port=hello_port)
        if already_paired and self.devstatus and not conn.get("telemetry_loop_started"):
            conn["telemetry_loop_started"] = True
            conn["tasks"].append(asyncio.create_task(self._post_key_telemetry_sync(conn)))
            conn["tasks"].append(asyncio.create_task(self._telemetry_sync_loop(conn)))
            conn["tasks"].append(asyncio.create_task(self._service_status_loop(conn)))
        self.events.log("HELLO_RECV", f"conn{conn['id']:03d} msgId={frame.get('msgId')} "
                                      f"senderId={frame.get('senderId')} "
                                      f"token_match={tok_ok} "
                                      f"paired={data.get('paired')} ver={data.get('ver')}")

        if self.auto_reply == "silent":
            self.events.log("REPLY_SUPPRESSED", f"variant=silent conn{conn['id']:03d}")
        else:
            conn["reply_task"] = asyncio.create_task(
                self._reply_sequence(conn, self.auto_reply))
            conn["tasks"].append(conn["reply_task"])

    async def _on_post_hello(self, conn: dict, frame: dict) -> None:
        data = frame.get("data") if isinstance(frame, dict) else None
        if isinstance(data, str) and data.lstrip().startswith("{"):
            with contextlib.suppress(Exception):
                _parsed_data = json.loads(data)
                if isinstance(_parsed_data, dict):
                    data = _parsed_data
        sid = frame.get("serviceId")
        cmd = data.get("cmd") if isinstance(data, dict) else None
        tag = f"conn{conn['id']:03d} svc={sid} cmd={cmd} msgId={frame.get('msgId')}"
        if await self.service_bus.dispatch_inbound_frame(sid, conn, frame, data, cmd, tag):
            return

    POST_HELLO_ROUTES: dict[int, str] = {
        0: "_route_sid_0",
        1: "_route_sid_1",
        2: "_route_sid_2",
        3: "_route_sid_3",
        5: "_route_sid_notifications",
        8: "_route_sid_8",
        9: "_route_sid_9",
        10: "_route_sid_10",
        15: "_route_sid_15",
        16: "_route_sid_16",
        17: "_route_sid_17",
        19: "_route_sid_19",
        20: "_route_sid_20",
        22: "_route_sid_22",
        24: "_route_sid_24",
        25: "_route_sid_25",
        26: "_route_sid_26",
        35: "_route_sid_notifications",
        100: "_route_sid_notifications",
        168: "_route_sid_168",
        202: "_route_sid_202",
        204: "_route_sid_204",
        205: "_route_sid_205",
        1001: "_route_sid_1001",
    }

    async def _route_sid_0(self, conn: dict, frame: dict, data: Any, cmd: int | None, tag: str) -> bool:
        if cmd == 1 and isinstance(data, dict) and not conn.get("key_sent"):
            rt = conn.get("reply_task")
            if rt is not None:
                with contextlib.suppress(Exception):
                    await asyncio.shield(rt)
            dev = data.get("device") or {}
            phone_key = dev.get("key")
            self.events.log("P1_EXCHANGE_KEY_IN", f"{tag} phoneKeyPresent={bool(phone_key)} paired={data.get('paired')}")
            conn["key_sent"] = True
            conn["readiness"] = "paired"
            if dev.get("deviceId"):
                self.note_phone(real_id=dev.get("deviceId"), name=dev.get("deviceName"), key=phone_key)
            if phone_key:
                with contextlib.suppress(OSError):
                    self.state_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
                    sk_p = self.state_dir / "shared_key.txt"
                    sk_p.write_text(str(phone_key))
                    os.chmod(sk_p, 0o600)
            await self._send_ws_text(conn, build_exchange_key(self, phone_key), "EXCHANGE_KEY_SENT(cmd1,auto)")
            if self.devstatus:
                conn["tasks"].append(asyncio.create_task(self._post_key_telemetry_sync(conn)))
                conn["tasks"].append(asyncio.create_task(self._telemetry_sync_loop(conn)))
                conn["tasks"].append(asyncio.create_task(self._service_status_loop(conn)))
            return True
        self.events.log("P2_SVC0_MSG", f"{tag} — logged, no reply")
        return True

    async def _route_sid_168(self, conn: dict, frame: dict, data: Any, cmd: int | None, tag: str) -> bool:
        if self.filestation in ("server", "stub"):
            await self._handle_fs(conn, frame, data, cmd, tag)
            return True
        return False

    async def _route_sid_9(self, conn: dict, frame: dict, data: Any, cmd: int | None, tag: str) -> bool:
        await self._handle_clipboard(conn, frame, data, tag)
        return True

    async def _route_sid_204(self, conn: dict, frame: dict, data: Any, cmd: int | None, tag: str) -> bool:
        await self._handle_messages(conn, frame, data, cmd, tag)
        return True

    async def _route_sid_notifications(self, conn: dict, frame: dict, data: Any, cmd: int | None, tag: str) -> bool:
        sid = frame.get("serviceId")
        if sid == 100 and int(frame.get("srcServiceId") or 0) == 204:
            await self._handle_messages(conn, frame, data, cmd, tag)
        await self._handle_notifications(conn, frame, data, cmd, tag)
        return True

    async def _route_sid_24(self, conn: dict, frame: dict, data: Any, cmd: int | None, tag: str) -> bool:
        await self._handle_dragdrop(conn, frame, data, cmd, tag)
        return True

    async def _route_sid_22(self, conn: dict, frame: dict, data: Any, cmd: int | None, tag: str) -> bool:
        await self._handle_control(conn, frame, data, cmd, tag)
        return True

    async def _route_sid_19(self, conn: dict, frame: dict, data: Any, cmd: int | None, tag: str) -> bool:
        await self._handle_pair(conn, frame, data, cmd, tag)
        return True

    async def _route_sid_205(self, conn: dict, frame: dict, data: Any, cmd: int | None, tag: str) -> bool:
        await self._handle_gallery(conn, frame, data, cmd, tag)
        return True

    async def _route_sid_1001(self, conn: dict, frame: dict, data: Any, cmd: int | None, tag: str) -> bool:
        await self._handle_features(conn, frame, data, cmd, tag)
        return True

    async def _route_sid_26(self, conn: dict, frame: dict, data: Any, cmd: int | None, tag: str) -> bool:
        await self._handle_universal_control(conn, frame, data, cmd, tag)
        return True

    async def _route_sid_2(self, conn: dict, frame: dict, data: Any, cmd: int | None, tag: str) -> bool:
        await self._handle_fileex(conn, frame, data, cmd, tag)
        return True

    async def _route_sid_16(self, conn: dict, frame: dict, data: Any, cmd: int | None, tag: str) -> bool:
        await self._handle_input_inbound(conn, frame, data, cmd, tag)
        return True

    async def _route_sid_25(self, conn: dict, frame: dict, data: Any, cmd: int | None, tag: str) -> bool:
        if cmd == 1:
            d25 = data if isinstance(data, dict) else {}
            peer_cap = d25.get("cap")
            self.phone_telemetry.update({
                "battery": d25.get("battery"),
                "isCharging": d25.get("isCharging"),
                "chargePlug": d25.get("chargePlug"),
                "wifi": d25.get("wifi"),
                "wifiSignal": d25.get("wifiSignal"),
                "data": d25.get("data"),
                "dataSignal": d25.get("dataSignal"),
                "bluetooth": d25.get("bluetooth"),
                "screen": d25.get("screen"),
                "foldState": d25.get("foldState"),
                "foldType": d25.get("foldType"),
                "usbReady": d25.get("usbReady"),
                "wallpaperState": d25.get("wallpaperState"),
                "adb": d25.get("adb"),
                "deviceName": d25.get("deviceName") or self.phone_telemetry.get("deviceName"),
                "model": d25.get("model") or self.phone_telemetry.get("model"),
                "cap": peer_cap if peer_cap is not None else self.phone_telemetry.get("cap"),
            })
            if d25.get("deviceName") or d25.get("model"):
                with contextlib.suppress(Exception):
                    self.note_phone(
                        nonce_id=frame.get("deviceId"),
                        name=d25.get("deviceName") or d25.get("model"),
                        ip=self._phone_ip(conn),
                    )
            if d25.get("freeStorage") or d25.get("totalStorage"):
                with contextlib.suppress(Exception):
                    self.phone_storage["free_bytes"] = int(d25.get("freeStorage") or self.phone_storage["free_bytes"])
                    self.phone_storage["total_bytes"] = int(d25.get("totalStorage") or self.phone_storage["total_bytes"])
            self.events.log("DEVSTATUS_RECV", f"{tag} phone battery={d25.get('battery')}% charging={d25.get('isCharging')} cap={peer_cap}")
            if self.devstatus and not conn.get("devstatus_reactive_sent"):
                conn["devstatus_reactive_sent"] = True
                conn["devstatus_sent"] = True
                await self._send_ws_text(conn, build_device_status(self, frame.get("msgId")), "DEVSTATUS_SENT(svc25 cmd1,reactive)")
            return True
        if cmd in (2, 3, 6, 20, 21, 22):
            await self._handle_devstatus_in(conn, frame, data, cmd, tag)
            return True
        return False

    async def _route_sid_3(self, conn: dict, frame: dict, data: Any, cmd: int | None, tag: str) -> bool:
        if self.webcam != "off":
            if isinstance(conn, dict) and conn.get("_control_loop") is None:
                with contextlib.suppress(RuntimeError):
                    conn["_control_loop"] = asyncio.get_running_loop()
            await self._run_on_desktop_thread(self._handle_webcam(conn, frame, data, cmd, tag), stat_key="service_msgs_dispatched")
            return True
        return False

    async def _route_sid_17(self, conn: dict, frame: dict, data: Any, cmd: int | None, tag: str) -> bool:
        await self._handle_applist(conn, frame, data, cmd, tag)
        return True

    async def _route_sid_20(self, conn: dict, frame: dict, data: Any, cmd: int | None, tag: str) -> bool:
        inner = (data or {}).get("data") if isinstance(data, dict) else None
        inner = inner if isinstance(inner, dict) else {}
        st = inner.get("status", (data or {}).get("status"))
        self.events.log(
            "FILEEXPLORER_MSG",
            f"{tag} cmd={cmd} ({_DRIVE_CMD.get(cmd, '?')}) serviceId={inner.get('serviceId')} status={st} "
            f"({_DRIVE_STATUS.get(st, '?')}) securityCode={inner.get('securityCode')} heartbeat={inner.get('heartbeat')} "
            f"keys={sorted(inner.keys()) or sorted((data or {}).keys())}",
            feature="file_transfer",
        )
        if cmd == 0:
            conn["fe_handshake_seen"] = True
            reply_inner = {
                "heartbeat": inner.get("heartbeat", 1),
                "isFileTransferStarted": True,
                "maxL": inner.get("maxL", 1),
                "minL": inner.get("minL", 1),
                "securityCode": inner.get("securityCode", ""),
                "serviceId": inner.get("serviceId", 20),
                "status": 0,
            }
            reply_env = _env(self, frame.get("msgId", self.next_msg_id()), self.device_id_b64, 20, 20, {"cmd": 0, "ver": "1.0", "data": reply_inner, "status": 0})
            await self._send_ws_text(conn, json.dumps(reply_env, separators=(",", ":")), "FILEEXPLORER_HANDSHAKE_REPLY_SENT(svc20 cmd0)")
            if self.filestation == "server" and not conn.get("fe_fs_announced"):
                conn["fe_fs_announced"] = True
                await self.send_fs_announce(conn)
        elif cmd == 1:
            conn_env = _env(self, frame.get("msgId", self.next_msg_id()), self.device_id_b64, 20, 20, {"cmd": 5, "ver": "1.0", "data": {"serviceId": inner.get("serviceId", 20), "status": 0}, "status": 0})
            await self._send_ws_text(conn, json.dumps(conn_env, separators=(",", ":")), "FILEEXPLORER_CONNECTED_REPLY_SENT(svc20 cmd5)")
        elif cmd == 3:
            hb_env = _env(self, frame.get("msgId", self.next_msg_id()), self.device_id_b64, 20, 20, {"cmd": 3, "ver": "1.0", "data": {"heartbeat": inner.get("heartbeat", 1), "serviceId": inner.get("serviceId", 1), "status": 0}, "status": 0})
            await self._send_ws_text(conn, json.dumps(hb_env, separators=(",", ":")), "FILEEXPLORER_HEARTBEAT_REPLY_SENT(svc20 cmd3)")
        elif cmd == 2:
            disc_env = _env(self, frame.get("msgId", self.next_msg_id()), self.device_id_b64, 20, 20, {"cmd": 2, "ver": "1.0", "data": {"serviceId": inner.get("serviceId", 1), "status": 0}, "status": 0})
            await self._send_ws_text(conn, json.dumps(disc_env, separators=(",", ":")), "FILEEXPLORER_DISCONNECT_REPLY_SENT(svc20 cmd2)")
        return True

    async def _route_sid_1(self, conn: dict, frame: dict, data: Any, cmd: int | None, tag: str) -> bool:
        if isinstance(data, dict) and self.desktop != "off":
            if isinstance(conn, dict) and conn.get("_control_loop") is None:
                with contextlib.suppress(RuntimeError):
                    conn["_control_loop"] = asyncio.get_running_loop()
            await self._run_on_desktop_thread(self._handle_desktop_service(conn, frame, data, tag), stat_key="service_msgs_dispatched")
            return True
        return False

    async def _route_sid_8(self, conn: dict, frame: dict, data: Any, cmd: int | None, tag: str) -> bool:
        self.events.log("PHONE_CONFIRM_PROMPT", f"{tag} data={data}", feature="desktop")
        self.as_status.update(phase="needs-accept", detail="Unlock your phone and tap Allow / Start on the confirmation prompt", updated=time.time())
        return True

    async def _route_sid_10(self, conn: dict, frame: dict, data: Any, cmd: int | None, tag: str) -> bool:
        if isinstance(conn, dict) and conn.get("_control_loop") is None:
            with contextlib.suppress(RuntimeError):
                conn["_control_loop"] = asyncio.get_running_loop()
        await self._run_on_desktop_thread(self._handle_display_service(conn, frame, data, tag), stat_key="service_msgs_dispatched")
        return True

    async def _route_sid_15(self, conn: dict, frame: dict, data: Any, cmd: int | None, tag: str) -> bool:
        if self.appstream != "off":
            if isinstance(conn, dict) and conn.get("_control_loop") is None:
                with contextlib.suppress(RuntimeError):
                    conn["_control_loop"] = asyncio.get_running_loop()
            await self._run_on_desktop_thread(self._handle_appstream(conn, frame, data, cmd, tag), stat_key="service_msgs_dispatched")
            return True
        return False

    async def _route_sid_202(self, conn: dict, frame: dict, data: Any, cmd: int | None, tag: str) -> bool:
        if self.screenshare != "off":
            if isinstance(conn, dict) and conn.get("_control_loop") is None:
                with contextlib.suppress(RuntimeError):
                    conn["_control_loop"] = asyncio.get_running_loop()
            await self._run_on_desktop_thread(self._handle_screenshare(conn, frame, data, cmd, tag), stat_key="service_msgs_dispatched")
            return True
        return False

    async def _reply_sequence(self, conn: dict, variant: str) -> None:
        try:
            if self.reply_delay > 0:
                self.events.log("REPLY_DELAY", f"conn{conn['id']:03d} sleeping {self.reply_delay}s")
                await asyncio.sleep(self.reply_delay)
            t0 = time.monotonic()
            reply = build_reply_json(self, variant, conn["hello"], conn=conn)
            await self._send_ws_text(conn, reply, f"REPLY_SENT({variant})")
            conn["replied"] = True
            conn["reply_sent_utc"] = datetime.datetime.now(datetime.UTC).isoformat()
            conn["reply_delta_ms"] = round((time.monotonic() - conn.get("hello_recv_mono", t0)) * 1000, 1)
            self.events.log("REPLY_TIMING", f"conn{conn['id']:03d} hello->reply "
                                            f"{conn['reply_delta_ms']}ms")
            if self.devstatus:
                await asyncio.sleep(0.4)
                await self._send_ws_text(conn, build_device_status(self),
                                         "DEVSTATUS_SENT(svc25 cmd1)")
                conn["devstatus_sent"] = True
            if self.checkin:
                await asyncio.sleep(0.3)
                await self._send_ws_text(conn, build_checkin(self), "CHECKIN_SENT(cmd2)")
            if self.keepalive:
                conn["tasks"].append(asyncio.create_task(self._keepalive(conn)))
            if self.rq and hasattr(self, "_rq_watch"):
                conn["rq_task"] = asyncio.create_task(getattr(self, "_rq_watch")(conn))
                conn["tasks"].append(conn["rq_task"])
            if self.ss_auto_start and self.screenshare != "off":
                await asyncio.sleep(1.0)
                conn["tasks"].append(asyncio.create_task(
                    self.start_screenshare(conn, origin="auto-start")))
            if self.dialback and conn["hello"]:
                await asyncio.sleep(0.5)
                ips = (conn["hello"].get("data") or {}).get("ips") or []
                if ips:
                    ep = ips[0]
                    dial_hello = build_server_first_hello(
                        self, nonce_phone_id=conn["hello"].get("deviceId"), sender=1)
                    target = f"{ep.get('ip')}:{ep.get('port')}"
                    ok = await self.dial(target, self.dialback_path,
                                         expect_fp_b64=ep.get("fp"), send_hello=dial_hello)
                    if not ok:
                        self.events.log("DIALBACK_RETRY", f"{target} in 2.5 s")
                        await asyncio.sleep(2.5)
                        await self.dial(target, self.dialback_path,
                                        expect_fp_b64=ep.get("fp"), send_hello=dial_hello)
                else:
                    self.events.log("DIALBACK_SKIP", "hello carried no ips[]")
        except (ConnectionError, ssl.SSLError, OSError) as exc:
            self.events.log("REPLY_FAIL", f"conn{conn['id']:03d} {exc!r}")
