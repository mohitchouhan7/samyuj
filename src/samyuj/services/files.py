from __future__ import annotations

import asyncio
import contextlib
import datetime
import hashlib
import hmac
import json
import os
import re
import shutil
import ssl
import time
import urllib.parse
from pathlib import Path

from samyuj.protocol.envelope import _env
from samyuj.transport.connection import _set_tcp_nodelay
from samyuj.transport.websocket import ws_accept_value, ws_frame

HAVE_MCL = False

from samyuj.core.identity import (
    make_server_ssl_context as _simple_server_ctx,
    make_server_ssl_context as server_ssl_context,
)
from samyuj.files.nfs import (
    Export,
    NfsRpcServer,
)
from samyuj.files.transfer import TransferTask
from samyuj.protocol.nfs.xdr import (
    PROG_MOUNT,
    PROG_NFS,
    PROG_PORTMAP,
)
from samyuj.protocol.services import (
    build_fileex_announce_task,
    build_filestation_announce,
    build_phonefs_handshake,
)
from samyuj.services.applist import _is_ft_error_body, _parse_ft_line
from samyuj.transport.network import resolve_session_ip

def _store_received_files(blob: bytes, outdir, task_id: str,
                          files: list[dict], index: int = 0) -> list[str]:
    import io
    import zipfile
    if _is_ft_error_body(blob):
        return []
    outdir = Path(outdir).expanduser()
    with contextlib.suppress(OSError):
        outdir.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []

    def _unique_dest(base_dir: Path, raw_name: str) -> Path:
        clean_name = Path(str(raw_name or "").replace("\\", "/")).name.strip()
        if not clean_name or clean_name in (".", ".."):
            clean_name = f"received_{task_id or int(time.time())}"
        cand = base_dir / clean_name
        if not cand.exists():
            return cand
        stem, suf = cand.stem, cand.suffix
        for k in range(1, 1000):
            alt = base_dir / f"{stem} ({k}){suf}"
            if not alt.exists():
                return alt
        return cand

    single_file_meta = files[index] if (files and 0 <= index < len(files)) else (files[0] if len(files) == 1 else None)
    single_name = str((single_file_meta or {}).get("name") or "").strip()
    announced = {str((m or {}).get("name") or "").strip() for m in (files or [])} - {""}

    def _zip_member_names(raw: bytes) -> list[str] | None:
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                names = [Path(n).name for n in z.namelist() if not n.endswith("/")]
            return names or None
        except Exception:
            return None

    if blob[:2] == b"PK":
        members = _zip_member_names(blob)
        try:
            announced_size = int((single_file_meta or {}).get("size") or -1)
        except (TypeError, ValueError):
            announced_size = -1
        size_matches = bool(single_name) and announced_size == len(blob)
        is_zip_bundle = members is not None and not size_matches and (
            not single_name or not announced or bool(set(members) & announced)
        )
        if is_zip_bundle:
            try:
                with zipfile.ZipFile(io.BytesIO(blob)) as z:
                    for info_z in z.infolist():
                        n = info_z.filename
                        if n.endswith("/") or info_z.file_size > 512 * 1024 * 1024:
                            continue
                        dest = _unique_dest(outdir, Path(n).name)
                        with contextlib.suppress(OSError):
                            with z.open(info_z, "r") as zf_in, open(dest, "wb") as f_out:
                                shutil.copyfileobj(zf_in, f_out, 262144)
                            saved.append(str(dest))
                if saved:
                    return saved
            except zipfile.BadZipFile:
                pass

    if single_name:
        dest = _unique_dest(outdir, single_name)
    elif blob[:8] == b"\x89PNG\r\n\x1a\n":
        dest = _unique_dest(outdir, f"image_{task_id or int(time.time())}.png")
    elif blob[:3] == b"\xff\xd8\xff":
        dest = _unique_dest(outdir, f"photo_{task_id or int(time.time())}.jpg")
    elif blob[:4] == b"%PDF":
        dest = _unique_dest(outdir, f"document_{task_id or int(time.time())}.pdf")
    else:
        dest = _unique_dest(outdir, f"file_{task_id or int(time.time())}")
    with contextlib.suppress(OSError):
        dest.write_bytes(blob)
        saved.append(str(dest))
    return saved

async def _fetch_task_files(http_dl, store_fn, *, files: list, file_urls: list,
                            recv_dir, task_id: str, token: str, total: int,
                            ip: str, port: int, ctx, raw_dir,
                            progress_cb=None) -> list:
    saved_all: list = []
    files = list(files or [])
    if not files:
        return saved_all

    def _is_dir_entry(meta) -> bool:
        return bool(isinstance(meta, dict) and meta.get("dir"))

    file_entries = sum(1 for m in files if not _is_dir_entry(m))

    def _make_dir_entry(meta, idx: int) -> None:
        name = Path(str((meta or {}).get("name") or f"dir_{idx}")).name
        with contextlib.suppress(OSError):
            (Path(recv_dir).expanduser() / name).mkdir(parents=True, exist_ok=True)

    if len(files) == 1:
        if _is_dir_entry(files[0]):
            _make_dir_entry(files[0], 0)
            return saved_all
        blob = await http_dl(ip, port, ctx, task_id, token, total, raw_dir,
                             file_urls=file_urls)
        if blob and not _is_ft_error_body(blob):
            saved_all.extend(store_fn(blob, recv_dir, task_id, files, index=0))
        return saved_all
    prev: bytes | None = None
    tried_bare = False
    cum_bytes = 0
    for idx in range(len(files)):
        meta = files[idx]
        if _is_dir_entry(meta):
            _make_dir_entry(meta, idx)
            continue
        blob = await http_dl(ip, port, ctx, task_id, token, total, raw_dir,
                             file_urls=file_urls, index=idx)
        if not blob or _is_ft_error_body(blob):
            if not tried_bare:
                tried_bare = True
                blob = await http_dl(ip, port, ctx, task_id, token, total,
                                     raw_dir, file_urls=file_urls)
            if not blob or _is_ft_error_body(blob):
                continue
        if prev is not None and blob == prev and not file_urls:
            break
        prev = blob
        cum_bytes += len(blob)
        got = store_fn(blob, recv_dir, task_id, files, index=idx)
        saved_all.extend(got)
        if progress_cb is not None:
            with contextlib.suppress(Exception):
                await progress_cb(cum_bytes, len(saved_all))
        if file_entries and len(saved_all) >= file_entries:
            break
    return saved_all

class _FsUdpLogger(asyncio.DatagramProtocol):

    def __init__(self, harness: "Harness", holder: dict) -> None:
        self.h = harness
        self.holder = holder
        self.n = 0

    def datagram_received(self, data: bytes, addr) -> None:
        self.n += 1
        if self.n <= 20:
            hdr = ""
            if data and data[0] & 0x80 and len(data) > 5:
                ver = int.from_bytes(data[1:5], "big")
                hdr = f" QUIC-longheader ver=0x{ver:08x}"
            self.h.events.log(
                "FS_UDP_DATA",
                f"port={self.holder.get('port')} from={addr} n={self.n} "
                f"len={len(data)}{hdr} first64={data[:64].hex()}")
        if self.n <= 4:
            with contextlib.suppress(Exception):
                (self.h.out_dir / f"fs_udp_{self.holder.get('port')}_{self.n}.bin").write_bytes(data)

    def error_received(self, exc) -> None:
        self.h.events.log("FS_UDP_ERROR", f"port={self.holder.get('port')} {exc!r}")

class FileSharingServiceMixin:

    async def _start_fs_quic(self, port: int):
        from aioquic.asyncio import serve
        from aioquic.asyncio.protocol import QuicConnectionProtocol
        from aioquic.quic.configuration import QuicConfiguration
        from aioquic.quic import events as qe
        h = self

        class _Proto(QuicConnectionProtocol):
            def quic_event_received(self, event) -> None:
                try:
                    if isinstance(event, qe.ProtocolNegotiated):
                        h.events.log("FS_QUIC_ALPN", f"alpn={event.alpn_protocol}")
                    elif isinstance(event, qe.HandshakeCompleted):
                        h.events.log("FS_QUIC_HANDSHAKE_OK",
                                     f"alpn={event.alpn_protocol} resumed={event.session_resumed}")
                    elif isinstance(event, qe.StreamDataReceived):
                        h.events.log("FS_QUIC_STREAM",
                                     f"sid={event.stream_id} {len(event.data)}B "
                                     f"end={event.end_stream} first96={event.data[:96].hex()}")
                        with contextlib.suppress(Exception):
                            with open(h.out_dir / f"fs_quic_stream_{event.stream_id}.bin", "ab") as f:
                                f.write(event.data)
                    elif isinstance(event, qe.DatagramFrameReceived):
                        h.events.log("FS_QUIC_DGRAM", f"{len(event.data)}B first96={event.data[:96].hex()}")
                    elif isinstance(event, qe.StreamReset):
                        h.events.log("FS_QUIC_STREAM_RESET", f"sid={event.stream_id} err={event.error_code}")
                    elif isinstance(event, qe.ConnectionTerminated):
                        h.events.log("FS_QUIC_CLOSED",
                                     f"err={event.error_code} frame={event.frame_type} "
                                     f"reason={event.reason_phrase!r}")
                except Exception as exc:
                    h.events.log("FS_QUIC_EVENT_ERR", repr(exc))

        cfg = QuicConfiguration(is_client=False, alpn_protocols=["perf"],
                                max_datagram_frame_size=65536)
        cfg.load_cert_chain(str(self.state_dir / "identity.cert.pem"),
                            str(self.state_dir / "identity.key.pem"))
        return await serve("0.0.0.0", port, configuration=cfg, create_protocol=_Proto)

    async def _ensure_fs_listeners(self) -> dict:
        if self._fs is not None:
            return self._fs
        if self.filestation == "server":
            return await self._ensure_fs_server()
        fs: dict = {"tcp": {}, "udp": {}}
        loop = asyncio.get_running_loop()
        for role in ("portmap", "mountd", "nfsd"):
            srv = await asyncio.start_server(
                lambda r, w, role=role: self._fs_tcp_client(r, w, role), "0.0.0.0", 0)
            port = srv.sockets[0].getsockname()[1]
            fs["tcp"][role] = (srv, port)
            holder: dict = {"port": port, "role": role}
            if role == "nfsd" and self.FS_QUIC:
                try:
                    fs.setdefault("quic", {})[role] = await self._start_fs_quic(port)
                    self.events.log("FS_QUIC_UP", f"role={role} udp/{port} alpn=perf")
                    continue
                except ImportError:
                    self.events.log("FS_QUIC_UNAVAILABLE",
                                    "aioquic not installed — run: pip install aioquic "
                                    "(falling back to UDP logger)")
                except Exception as exc:
                    self.events.log("FS_QUIC_START_FAIL", repr(exc))
            try:
                transport, _p = await loop.create_datagram_endpoint(
                    lambda holder=holder: _FsUdpLogger(self, holder),
                    local_addr=("0.0.0.0", port))
                fs["udp"][role] = (transport, port)
            except OSError as exc:
                self.events.log("FS_UDP_BIND_FAIL", f"role={role} port={port} {exc!r}")
        self._fs = fs
        self.events.log("FS_LISTENERS_UP",
                        " ".join(f"{r}={p}(tcp+udp)" for r, (s, p) in fs["tcp"].items()))
        return fs

    async def _fs_tcp_client(self, reader: asyncio.StreamReader,
                             writer: asyncio.StreamWriter, role: str) -> None:
        peer = writer.get_extra_info("peername")
        self.events.log("FS_TCP_ACCEPT", f"role={role} from={peer} — mount attempt!")
        try:
            data = await asyncio.wait_for(reader.read(2048), timeout=15)
            if data:
                self.events.log("FS_TCP_DATA", f"role={role} {len(data)}B "
                                               f"first96={data[:96].hex()}")
                with contextlib.suppress(Exception):
                    (self.out_dir / "fs_first_bytes.bin").write_bytes(data)
        except (TimeoutError, asyncio.TimeoutError, OSError):
            pass
        try:
            while True:
                more = await reader.read(2048)
                if not more:
                    break
                self.events.log("FS_TCP_DATA", f"role={role} +{len(more)}B "
                                               f"first96={more[:96].hex()}")
        except (OSError, asyncio.CancelledError):
            pass
        finally:
            with contextlib.suppress(Exception):
                writer.close()

    def get_filetransfer_summary(self) -> dict:
        return {
            "xfer_port": self.xfer_port,
            "sent_count": len(self.xfer_tasks),
            "recv_count": len(self.recv_files),
            "recv_files": list(self.recv_files)[-10:],
            "received_texts": list(self.received_texts)[-10:],
            "last_status": self.xfer_last_status,
        }

    async def _ensure_xfer_server(self) -> int:
        if self._xfer_srv is not None:
            return self.xfer_port
        ctx = _simple_server_ctx(self.identity) if not HAVE_MCL else server_ssl_context(self.identity)
        self._xfer_srv = await asyncio.start_server(self._on_xfer_conn, self.host, 0, ssl=ctx)
        self.xfer_port = self._xfer_srv.sockets[0].getsockname()[1]
        self.events.log("FILEEX_SERVER_UP", f"TLS file-transfer listener on {self.host}:{self.xfer_port}", feature="file_transfer")
        return self.xfer_port

    async def create_and_send_file(self, conn: dict | None, filename: str, content: str) -> dict:
        c = conn or self._live_ws()
        if c is None:
            return {"ok": False, "error": "no live phone connection"}
        outbox = self.out_dir / "outbox"
        outbox.mkdir(parents=True, exist_ok=True)
        safe_name = Path(filename or "note.txt").name
        target = outbox / safe_name
        target.write_text(content, encoding="utf-8")
        task = await self.send_files_svc2(c, [str(target)])
        if task:
            return {"ok": True, "taskId": task.task_id, "detail": f"Created {safe_name} ({len(content)}B) & announced svc-2 transfer taskId={task.task_id}"}
        return {"ok": False, "error": "failed to start svc-2 transfer"}

    async def send_files_svc2(self, conn: dict | None, paths: list[str], mode: str = "auto") -> TransferTask | None:
        c = conn or self._live_ws()
        if c is None:
            return None
        await self._ensure_xfer_server()
        resolved: list[Path] = []
        for p_str in paths or ["@demo"]:
            if p_str in ("@demo", "@sample"):
                outbox = self.out_dir / "outbox"
                outbox.mkdir(parents=True, exist_ok=True)
                demo = outbox / f"samyuj_demo_{int(time.time())}.txt"
                demo.write_text(f"Samyuj PC File Transfer Demo ({datetime.datetime.now().isoformat()})\n", encoding="utf-8")
                resolved.append(demo)
            else:
                pp = Path(os.path.expanduser(p_str))
                if pp.is_file():
                    resolved.append(pp)
                elif pp.is_dir():
                    resolved.extend(sorted(q for q in pp.rglob("*") if q.is_file())[:50])
        if not resolved:
            return None
        actual_mode = ("zip" if len(resolved) == 1 else "files") if mode == "auto" else mode
        task = TransferTask(resolved, self.xfer_port, self.out_dir / "zips", mode=actual_mode)
        task.base_url = f"https://{self.lan_ip}:{self.xfer_port}"
        if actual_mode == "zip":
            await asyncio.to_thread(task.ensure_zip)
        self.xfer_tasks[task.task_id] = task
        while len(self.xfer_tasks) > 128:
            oldest_tid = next(iter(self.xfer_tasks))
            if oldest_tid == task.task_id:
                break
            self.xfer_tasks.pop(oldest_tid, None)
        self.active_xfer_task = task
        self.xfer_last_status = f"Announced {task.file_count} file(s) ({task.size}B) taskId={task.task_id}"
        await self._send_ws_text(c, build_fileex_announce_task(self, task), "FILEEX_ANNOUNCE_SENT(svc2 cmd1)")
        return task

    async def _on_xfer_conn(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        _set_tcp_nodelay(writer)
        buf = bytearray()
        try:
            while len(buf) < 65536 and b"\r\n\r\n" not in buf:
                chunk = await asyncio.wait_for(reader.read(16384), timeout=10.0)
                if not chunk:
                    return
                buf.extend(chunk)
            idx = buf.find(b"\r\n\r\n")
            if idx < 0:
                return
            head = bytes(buf[:idx]).decode("utf-8", "replace")
            rest = bytes(buf[idx + 4:])
            reqline = head.split("\r\n")[0]
            method, target, _ = reqline.split(" ", 2)
            parsed = urllib.parse.urlsplit(target)
            qs = urllib.parse.parse_qs(parsed.query)
            ws_key = None
            for line in head.split("\r\n"):
                if line.lower().startswith("sec-websocket-key:"):
                    ws_key = line.split(":", 1)[1].strip()
            if ws_key and parsed.path == "/websocket":
                want_tid = (qs.get("taskId") or [""])[0]
                task = self.xfer_tasks.get(want_tid) if want_tid else None
                if task is None:
                    task = self.active_xfer_task
                if task is None:
                    writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                    await writer.drain()
                    return
                accept = ws_accept_value(ws_key)
                writer.write((f"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                              f"Connection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n").encode())
                await writer.drain()
                vn = f'action:Server{task.next_server_seq()}:versionNegotiation?{{"version":1}}'
                writer.write(ws_frame(0x1, vn.encode("utf-8")))
                await writer.drain()
                wbuf = bytearray(rest)
                deadline = time.monotonic() + 30.0
                while time.monotonic() < deadline:
                    line = await self._read_ws_text(reader, timeout=8.0, buf=wbuf)
                    if line is None:
                        break
                    if not line:
                        continue
                    kind, mid, name, payload = _parse_ft_line(line)
                    if kind == "ack" and name.lower() == "versionnegotiation":
                        sr = task.send_request_json(self.device_id_b64, self.device_name)
                        msg = f"action:Server{task.next_server_seq()}:sendRequest?" + json.dumps(sr, separators=(",", ":"), ensure_ascii=False)
                        writer.write(ws_frame(0x1, msg.encode("utf-8")))
                        await writer.drain()
                    elif kind == "action" and name.lower() == "status":
                        p = payload or {}
                        if p.get("type") == 1:
                            writer.write(ws_frame(0x1, f"ack:{mid}:status".encode("utf-8")))
                            await writer.drain()
                            task.phase = "success" if str(p.get("reason")) == "success" else "failed"
                            self.xfer_last_status = f"Push {task.task_id}: {p.get('reason')}"
                            self.events.log("FILEEX_PUSH_DONE", self.xfer_last_status, feature="file_transfer")
                            break
                return
            if parsed.path.startswith("/download") and method in ("GET", "HEAD"):
                tid = (qs.get("taskId") or [""])[0]
                tok = (qs.get("token") or [""])[0]
                has_idx = "index" in qs
                try:
                    idx_n = int((qs.get("index") or ["0"])[0])
                except ValueError:
                    idx_n = -1
                task = self.xfer_tasks.get(tid) if tid else self.active_xfer_task
                tok_ok = (not task.token) or hmac.compare_digest(str(tok).encode("utf-8"), str(task.token).encode("utf-8")) if task is not None else False
                if task is None or not tok_ok or not (0 <= idx_n < task.file_count):
                    writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                    await writer.drain()
                    return
                if task.mode == "files" and has_idx:
                    src = task.paths[idx_n]
                else:
                    src = await asyncio.to_thread(task.ensure_zip)
                fsize = src.stat().st_size
                writer.write((f"HTTP/1.1 200 OK\r\nContent-Length: {fsize}\r\n"
                              f"Content-Type: application/octet-stream\r\nConnection: close\r\n\r\n").encode())
                await writer.drain()
                if method != "HEAD":
                    with open(src, "rb") as fh:
                        while True:
                            chunk_b = fh.read(262144)
                            if not chunk_b:
                                break
                            writer.write(chunk_b)
                            await writer.drain()
                            task.bytes_served += len(chunk_b)
        except Exception as exc:
            self.events.log("FILEEX_SERVER_ERR", repr(exc), feature="file_transfer")
        finally:
            with contextlib.suppress(Exception):
                writer.close()

    async def _handle_fileex(self, conn: dict, frame: dict, data, cmd, tag: str) -> None:
        d = data if isinstance(data, dict) else {}
        if isinstance(d.get("data"), dict) and "cmd" not in d:
            d = d["data"]
        if cmd in (1, 2) and d.get("port"):
            self.events.log("FILEEX_RECV_ANNOUNCE", f"{tag} port={d.get('port')} fp={str(d.get('fingerprint'))[:16]}..", feature="file_transfer")
            conn["tasks"].append(asyncio.create_task(self._pull_phone_files_svc2(conn, d)))
            return
        if cmd == 3:
            self.events.log("FILEEX_ERR_FROM_PHONE", f"{tag} errorcode={d.get('errorcode')}", feature="file_transfer")
            return
        if cmd == 4:
            txt = str(d.get("text") or "")
            rid = d.get("requestId")
            self.received_texts.append(txt)
            if len(self.received_texts) > 256:
                del self.received_texts[:-256]
            self.xfer_last_status = f"Received text from phone: {txt[:80]}"
            self.events.log("FILEEX_TEXT_FROM_PHONE", f"{tag} requestId={rid} text={txt!r}", feature="file_transfer")
            with contextlib.suppress(OSError):
                with open(self.out_dir / "received_texts.log", "a", encoding="utf-8") as f:
                    f.write(f"{datetime.datetime.now().isoformat()}\t{txt}\n")
            ack = json.dumps(_env(self, self.next_msg_id(), self.device_id_b64, 2, 2,
                                  {"ver": "1.0.0", "cmd": 5, "requestId": rid, "errorcode": 0}),
                             separators=(",", ":"), ensure_ascii=False)
            await self._send_ws_text(conn, ack, "FILEEX_TEXT_ACK_SENT(svc2 cmd5)")
            return
        self.events.log("FILEEX_MSG", f"{tag} cmd={cmd} keys={sorted(d.keys())}", feature="file_transfer")

    async def _pull_phone_files_svc2(self, conn: dict, info: dict) -> None:
        ip = self._phone_ip(conn)
        port = int(info.get("port") or 0)
        fp = str(info.get("fingerprint") or "")
        token = str(info.get("token") or "")
        recv_dir = self.get_incoming_files_dir()
        raw_dir = self.out_dir / "received" / "raw"
        with contextlib.suppress(OSError):
            raw_dir.mkdir(parents=True, exist_ok=True)
        if not ip or not port:
            return
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(ip, port, ssl=ctx, server_hostname=ip), timeout=12.0)
            _set_tcp_nodelay(writer)
            so = writer.get_extra_info("ssl_object")
            der = so.getpeercert(binary_form=True) if so else None
            got_fp = hashlib.sha256(der).hexdigest().upper() if der else ""
            if fp and (not got_fp or got_fp != fp.upper()):
                self.events.log("FILEEX_PIN_REJECT", f"got {got_fp[:16]}.. want {fp[:16]}..", feature="file_transfer")
                writer.close()
                return
            status, rest = await self._ws_upgrade_plain(reader, writer, f"{ip}:{port}", "/websocket")
            if not status.startswith("HTTP/1.1 101"):
                writer.close()
                return
            wbuf = bytearray(rest)
            deadline = time.monotonic() + 30.0
            while time.monotonic() < deadline:
                line = await self._read_ws_text(reader, timeout=8.0, buf=wbuf)
                if line is None:
                    break
                if not line:
                    continue
                kind, mid, name, payload = _parse_ft_line(line)
                if kind == "action" and name.lower() == "versionnegotiation":
                    await self._send_ft(writer, "ack", mid, "versionNegotiation",
                                        {"version": 1, "threadLimit": 1, "token": token})
                elif kind == "action" and name.lower() == "sendrequest":
                    p = payload or {}
                    task_id = str(p.get("taskId") or p.get("id") or "")
                    total = int(p.get("totalSize") or 0)
                    files = list(p.get("file") or [])
                    file_urls = [u for u in str(p.get("fileUrl") or "").split("##") if u]
                    await self._send_ft(writer, "ack", mid, "sendRequest", None)
                    await asyncio.sleep(0.05)
                    client_seq = 1020

                    async def _progress(cum_bytes: int, _saved_count: int) -> None:
                        nonlocal client_seq
                        await self._send_ft(
                            writer, "action", f"Client{client_seq}", "status",
                            {"taskId": task_id, "type": 0, "reason": str(cum_bytes)})
                        client_seq += 1

                    saved_all = await _fetch_task_files(
                        self._http_download, _store_received_files,
                        files=files, file_urls=file_urls, recv_dir=recv_dir,
                        task_id=task_id, token=token, total=total,
                        ip=ip, port=port, ctx=ctx, raw_dir=raw_dir,
                        progress_cb=_progress)
                    expected = sum(1 for m in files
                                   if not (isinstance(m, dict) and m.get("dir")))
                    if expected > 1 and len(saved_all) < expected:
                        self.events.log(
                            "FILEEX_MULTI_PARTIAL",
                            f"task={task_id} saved {len(saved_all)}/{expected} announced file(s)",
                            feature="file_transfer")
                    if saved_all:
                        self.recv_files.extend(saved_all)
                        if len(self.recv_files) > 512:
                            del self.recv_files[:-512]
                        names_only = [Path(s).name for s in saved_all]
                        self.xfer_last_status = f"Saved {len(saved_all)} file(s) to {recv_dir}: {', '.join(names_only[:4])}"
                        self.events.log("FILEEX_RECV_SAVED", self.xfer_last_status, feature="file_transfer")
                        ntf_item = {
                            "key": f"file_rx_{task_id or int(time.time()*1000)}",
                            "event": 0,
                            "pkg": "com.motorola.mobiledesktop.filetransfer",
                            "app_name": "File transfer",
                            "title": "File transfer",
                            "text": f"Saved {', '.join(names_only[:3])} to {recv_dir}",
                            "actions": [],
                            "ts": time.time(),
                            "utc": datetime.datetime.now(datetime.UTC).isoformat(),
                        }
                        self.notifications_list.append(ntf_item)
                        self._emit_desktop_notification("File transfer", "Received from phone", ntf_item["text"])
                        if callable(self.on_notification_cb):
                            with contextlib.suppress(Exception):
                                self.on_notification_cb(ntf_item)
                        await self._send_ft(writer, "action", f"Client{client_seq}", "status",
                                            {"taskId": task_id, "type": 1, "reason": "success"})
                    break
        except Exception as exc:
            self.events.log("FILEEX_RECV_ERR", repr(exc), feature="file_transfer")

    def _fs_server_ctx(self) -> ssl.SSLContext:
        return _simple_server_ctx(self.identity) if not HAVE_MCL else server_ssl_context(self.identity)

    def _on_nfs_event(self, kind: str, detail: str, rel: str | None = None) -> None:
        self.events.log(kind, detail, feature="file_transfer")
        if kind == "NFS_MOUNT":
            self.mounted_at = time.time()
            m = re.search(r"peer=(\S+)", detail)
            if m and m.group(1) not in self.mount_peers:
                self.mount_peers.append(m.group(1))
        if rel:
            self.phone_seen.add(rel.strip("/"))

    def _fs_allow_peer(self, conn: dict | None) -> None:
        ip = self._phone_ip(conn) if conn else ""
        if ip and ip not in self._fs_allowed_ips:
            self._fs_allowed_ips.add(ip)

    async def _ensure_fs_server(self) -> dict:
        if self._nfs_srv is not None and self._fs is not None:
            return self._fs
        exp = Export(self.export_dir, readonly=not self.fs_rw)
        srv = NfsRpcServer(exp, announced_path=self.fs_path,
                           on_event=self._on_nfs_event,
                           allow_ips=None if self.fs_any_peer else self._fs_allowed_ips)
        srv._unhandled_path = self.out_dir / "nfs_unhandled.log"
        restored_n = self._load_persisted_shares(srv)
        if restored_n == 0 and not self._nfs_shares_file().is_file():
            for def_alias in ("Pictures", "Documents"):
                cand_dir = Path.home() / def_alias
                if cand_dir.is_dir():
                    with contextlib.suppress(Exception):
                        srv.add_share(def_alias, cand_dir, access_mode="ro")
        ctx = self._fs_server_ctx() if self.fs_dt2type != 1 else None
        ports: dict[str, int] = {}
        for role in ("portmap", "mountd", "nfsd"):
            ports[role] = await srv.start_listener("0.0.0.0", 0, ctx)
        srv.portmap = {PROG_PORTMAP: ports["portmap"], PROG_MOUNT: ports["mountd"], PROG_NFS: ports["nfsd"]}
        self.fs_ports.update(ports)
        self._nfs_srv = srv
        self._fs = {"tcp": {r: (None, p) for r, p in ports.items()}, "udp": {}, "server": srv}
        self.events.log("FS_SERVER_UP",
                        f"NFSv3 TLS Server UP ports={ports} export={self.export_dir} path={self.fs_path}",
                        feature="file_transfer")
        return self._fs

    def _export_summary(self) -> dict:
        root = self.export_dir
        try:
            entries = sorted(root.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except OSError:
            entries = []
        n_dir = sum(1 for p in entries if p.is_dir())
        n_file = len(entries) - n_dir
        srv = self._nfs_srv
        shares_list = srv.export.list_shares() if (srv and hasattr(srv.export, "list_shares")) else []
        rev = int(getattr(srv.export, "revision", 1)) if srv else 1
        return {
            "export_root": str(root),
            "exists": root.is_dir(),
            "mode": "read-write" if self.fs_rw else "read-only",
            "server_running": srv is not None,
            "announced_path": self.fs_path,
            "ports": dict(self.fs_ports),
            "entries": len(entries),
            "dirs": n_dir,
            "files": n_file,
            "phone_mounted": bool(self.mounted_at),
            "preview": [f"{'d' if p.is_dir() else '-'} {p.name}" for p in entries[:12]],
            "rpc_stats": dict(getattr(srv, "stats", {}) or {}),
            "shares": shares_list,
            "revision": rev,
        }

    def _nfs_shares_file(self) -> Path:
        return Path(self.state_dir) / "nfs_shares.json"

    def _persist_nfs_shares(self) -> None:
        with contextlib.suppress(OSError):
            shares = self._nfs_srv.list_shares() if self._nfs_srv else []
            Path(self.state_dir).mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(self.state_dir, 0o700)
            nf = self._nfs_shares_file()
            nf.write_text(
                json.dumps({"shares": [{"alias": s.get("alias"),
                                        "path": s.get("host_path"),
                                        "access_mode": s.get("access_mode", "rw")}
                                       for s in shares]}, indent=1),
                encoding="utf-8")
            os.chmod(nf, 0o600)

    def _load_persisted_shares(self, srv) -> int:
        n = 0
        with contextlib.suppress(OSError, json.JSONDecodeError, ValueError, TypeError):
            f = self._nfs_shares_file()
            if f.is_file():
                for s in (json.loads(f.read_text(encoding="utf-8")).get("shares") or []):
                    alias, path = str(s.get("alias") or ""), str(s.get("path") or "")
                    if not alias or not path or not Path(path).is_dir():
                        continue
                    with contextlib.suppress(Exception):
                        srv.add_share(alias, path, access_mode=str(s.get("access_mode") or "rw"))
                        n += 1
        if n:
            self.events.log("FS_SHARES_RESTORED", f"{n} persisted share(s) re-added from {self._nfs_shares_file()}",
                            feature="file_transfer")
        return n

    async def nfs_add_share(self, alias: str, path: str, access_mode: str = "rw",
                            reannounce: bool = True) -> dict:
        await self._ensure_fs_server()
        try:
            entry = self._nfs_srv.add_share(alias, path, access_mode=access_mode)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        self._persist_nfs_shares()
        self.events.log("FS_SHARE_ADDED", f"alias={entry.alias} path={entry.host_path} mode={entry.access_mode}",
                        feature="file_transfer")
        announced = False
        if reannounce and self._live_ws():
            announced = await self.send_fs_announce(self._live_ws())
        return {"ok": True, "share": entry.to_dict(), "announced": announced, **self._export_summary()}

    async def nfs_remove_share(self, alias: str, reannounce: bool = True) -> dict:
        await self._ensure_fs_server()
        ok = self._nfs_srv.remove_share(alias)
        if not ok:
            return {"ok": False, "error": f"share not found: {alias!r}"}
        self._persist_nfs_shares()
        self.events.log("FS_SHARE_REMOVED", f"alias={alias}", feature="file_transfer")
        announced = False
        if reannounce and self._live_ws():
            announced = await self.send_fs_announce(self._live_ws())
        return {"ok": True, "removed": alias, "announced": announced, **self._export_summary()}

    async def nfs_set_share_mode(self, alias: str, access_mode: str,
                                 reannounce: bool = True) -> dict:
        await self._ensure_fs_server()
        ok = self._nfs_srv.set_share_access_mode(alias, access_mode)
        if not ok:
            return {"ok": False, "error": f"share not found: {alias!r}"}
        self._persist_nfs_shares()
        self.events.log("FS_SHARE_MODE", f"alias={alias} mode={access_mode}", feature="file_transfer")
        announced = False
        if reannounce and self._live_ws():
            announced = await self.send_fs_announce(self._live_ws())
        return {"ok": True, "alias": alias, "access_mode": access_mode, "announced": announced, **self._export_summary()}

    def nfs_list_shares(self) -> dict:
        shares = self._nfs_srv.list_shares() if self._nfs_srv else []
        return {"ok": True, "shares": shares, **self._export_summary()}

    async def remount(self, path: str, reannounce: bool = True) -> dict:
        raw = (path or "").strip().strip('"').strip("'")
        if not raw:
            return {"ok": False, "error": "no path given"}
        cand = Path(raw).expanduser().resolve()
        if not cand.exists() and self.fs_create_missing:
            with contextlib.suppress(OSError):
                cand.mkdir(parents=True, exist_ok=True)
        if not cand.is_dir():
            return {"ok": False, "error": f"not a directory: {cand}"}
        self.export_dir = cand
        await self._ensure_fs_server()
        if self._nfs_srv is not None:
            self._nfs_srv.set_export(cand, readonly=not self.fs_rw)
            self._nfs_srv.announced_path = self.fs_path
        self.events.log("FS_REMOUNT", f"Remounted NFSv3 export -> {cand}", feature="file_transfer")
        announced = False
        if reannounce and self._live_ws():
            announced = await self.send_fs_announce(self._live_ws())
        return {"ok": True, "announced": announced, **self._export_summary()}

    async def send_fs_announce(self, conn: dict | None = None) -> bool:
        c = conn or self._live_ws()
        if not c:
            return False
        await self._ensure_fs_server()
        self._fs_allow_peer(c)
        reply, params = build_filestation_announce(self, c.get("last_frame"), c)
        return await self._send_ws_text(c, reply, "FS_ANNOUNCE_SENT(svc168 cmd1001)")

    async def _handle_fs(self, conn: dict, frame: dict, data: dict | None,
                         cmd: int | None, tag: str) -> None:
        if not isinstance(data, dict):
            self.events.log("FS_MSG_LOGGED", f"{tag} — no reply", feature="file_transfer")
            return
        msg_id_echo = frame.get("msgId", self.next_msg_id())
        raw_inner = data.get("data")
        inner_obj = {}
        if isinstance(raw_inner, str) and raw_inner.strip():
            with contextlib.suppress(Exception):
                parsed_in = json.loads(raw_inner)
                if isinstance(parsed_in, dict):
                    inner_obj = parsed_in
        elif isinstance(raw_inner, dict):
            inner_obj = raw_inner

        if cmd == 2006:
            if self.filestation == "server":
                await self._ensure_fs_server()
                self._fs_allow_peer(conn)
            net_resp = {
                "ip": resolve_session_ip(self, conn),
                "port": int(self.fs_ports.get("portmap", 0)),
                "nfsport": int(self.fs_ports.get("mountd", 0)),
                "nfsDataPort": int(self.fs_ports.get("nfsd", 0)),
            }
            await self._send_ws_text(
                conn,
                json.dumps(
                    _env(self, msg_id_echo, self.device_id_b64, 168, 168,
                         {"data": json.dumps(net_resp, separators=(",", ":")), "cmd": 2007, "ver": "1.0"}),
                    separators=(",", ":"),
                ),
                "VAULT_2007_NETWORK_RESP",
            )
            return

        if cmd == 2005:
            vault_map = getattr(self, "vault_synced_files", None)
            if vault_map is None:
                vault_map = self.vault_synced_files = {}
            avail_ids: list[str] = []
            unavail_ids: list[str] = []
            req_ids = inner_obj.get("fileIds") or inner_obj.get("availableFileIds") or list(vault_map.keys())
            for fid in req_ids:
                fid_s = str(fid)
                meta = vault_map.get(fid_s)
                if meta and meta.get("available", True):
                    avail_ids.append(fid_s)
                elif fid_s in vault_map:
                    unavail_ids.append(fid_s)
                else:
                    avail_ids.append(fid_s)
            notify_states = {
                "size": len(avail_ids) + len(unavail_ids),
                "availableFileIds": avail_ids,
                "unavailableFileIds": unavail_ids,
            }
            await self._send_ws_text(
                conn,
                json.dumps(
                    _env(self, msg_id_echo, self.device_id_b64, 168, 168,
                         {"data": json.dumps(notify_states, separators=(",", ":")), "cmd": 2004, "ver": "1.0"}),
                    separators=(",", ":"),
                ),
                "VAULT_2005_ANSWERED",
            )
            return

        if cmd == 2001:
            vault_map = getattr(self, "vault_synced_files", None)
            if vault_map is None:
                vault_map = self.vault_synced_files = {}
            items = inner_obj.get("syncFileItems") or inner_obj.get("files") or []
            acks: list[dict] = []
            for it in (items if isinstance(items, list) else []):
                if not isinstance(it, dict):
                    continue
                fid = str(it.get("fileId") or it.get("id") or "")
                if not fid:
                    continue
                vault_map[fid] = {**it, "available": True, "synced_at": time.time()}
                acks.append({"fileId": fid, "syncStatus": 1, "extra": ""})
            ack_payload = {"fileIds": acks}
            await self._send_ws_text(
                conn,
                json.dumps(
                    _env(self, msg_id_echo, self.device_id_b64, 168, 168,
                         {"data": json.dumps(ack_payload, separators=(",", ":")), "cmd": 102001, "ver": "1.0"}),
                    separators=(",", ":"),
                ),
                "VAULT_102001_SYNC_ACK",
            )
            return

        if cmd in (2002, 2003, 2008):
            vault_map = getattr(self, "vault_synced_files", None)
            if vault_map is None:
                vault_map = self.vault_synced_files = {}
            if cmd == 2008:
                vault_map.clear()
            elif cmd == 2002:
                for fid in (inner_obj.get("fileIds") or inner_obj.get("ids") or []):
                    vault_map.pop(str(fid), None)
            elif cmd == 2003:
                fid = str(inner_obj.get("fileId") or inner_obj.get("id") or "")
                if fid in vault_map and inner_obj.get("name"):
                    vault_map[fid]["name"] = str(inner_obj["name"])
            ack_cmd = 100000 + int(cmd)
            await self._send_ws_text(
                conn,
                json.dumps(
                    _env(self, msg_id_echo, self.device_id_b64, 168, 168,
                         {"data": json.dumps({"status": 0, **inner_obj}, separators=(",", ":")), "cmd": ack_cmd, "ver": "1.0"}),
                    separators=(",", ":"),
                ),
                f"VAULT_{ack_cmd}_ACK",
            )
            return

        if cmd == 2013:
            await self._send_ws_text(
                conn,
                json.dumps(
                    _env(self, msg_id_echo, self.device_id_b64, 168, 168,
                         {"data": json.dumps({"ready": True, "status": 1}, separators=(",", ":")), "cmd": 2014, "ver": "1.0"}),
                    separators=(",", ":"),
                ),
                "VAULT_2014_SYNC_READY",
            )
            return

        if cmd == 2010:
            self.events.log("VAULT_EXT_STORAGE", json.dumps(inner_obj), feature="file_transfer")
            return

        if cmd != 1001:
            self.events.log("FS_MSG_LOGGED", f"{tag} — no reply", feature="file_transfer")
            return
        try:
            inner = json.loads(data.get("data") or "{}")
        except json.JSONDecodeError:
            inner = {}
        if not isinstance(inner, dict):
            inner = {}
        if inner.get("type") == 1:
            await self._phonefs_on_server_info(conn, inner)
            return
        if self.filestation == "server":
            await self._ensure_fs_server()
            self._fs_allow_peer(conn)
        else:
            fs_stub = await self._ensure_fs_listeners()
            for _r, (_s, _p) in (fs_stub.get("tcp") or {}).items():
                self.fs_ports[_r] = _p
            self.fs_dt2type = 3
        reply, params = build_filestation_announce(self, frame, conn)
        n = conn.get("fs_replies", 0) + 1
        conn["fs_replies"] = n
        await self._send_ws_text(conn, reply, f"FS_HANDSHAKE_REPLY(n={n})")
        if n <= 2:
            self.events.log("FS_PHONE_PARAMS", json.dumps(inner))
            self.events.log("FS_REPLY_PARAMS", json.dumps(params))
            if (self.user_settings.get("auto_mount_phone_nfs", True)
                    and not conn.get("_pfs_auto_hs_sent")
                    and (self._pfs_client is None or self.phonefs.get("state") != "ready")):
                conn["_pfs_auto_hs_sent"] = True
                pfs_q, _ = build_phonefs_handshake(self)
                await self._send_ws_text(conn, pfs_q, "PHONEFS_HANDSHAKE_ON_1001_SENT(svc168 type0)")

from .base import SamyujService

class FileTransferService(SamyujService):
    service_id = 2
    id = "filetransfer"
    name = "Native File Transfer Service"
    description = "svc-2 PC->Phone TLS batch push (TransferTask POST /websocket + /downloadFile), Phone->PC auto-receive & text share (cmd:4/5)"

    def actions(self) -> list[dict]:
        return [
            {"name": "send_demo", "label": "Send Demo File to Phone (svc-2 cmd:1)", "description": "Create and push a sample text file to phone /Download/ via the TLS file-transfer port"},
            {"name": "send_files", "label": "Send File/Folder Path", "description": "Push specified PC file or folder to phone", "default_params": {"path": "@demo"}},
            {"name": "send_text", "label": "Share Text (svc-2 cmd:4)", "description": "Send instant text share to phone", "default_params": {"text": "Shared via Samyuj"}},
        ]

    def get_state(self) -> dict:
        return {
            **super().get_state(),
            **self.h.get_filetransfer_summary(),
        }

    async def invoke(self, action: str, params: dict | None = None,
                     conn: dict | None = None) -> dict:
        params = params or {}
        c = conn or self.h._live_ws()
        if action == "send_demo":
            return await self.h.request("filesend:@demo", c)
        if action == "send_files":
            p = str(params.get("path") or "@demo")
            return await self.h.request(f"filesend:{p}", c)
        if action == "create_and_send":
            nm = str(params.get("name") or "samyuj_note.txt")
            cnt = str(params.get("content") or "Hello from Samyuj PC!")
            return await self.h.create_and_send_file(c, nm, cnt)
        if action == "send_text":
            txt = str(params.get("text") or "Hello from Samyuj PC!")
            return await self.h.request(f"fileshare-text:{txt}", c)
        return await super().invoke(action, params, conn)

class FileStationNfsService(SamyujService):
    service_id = 168
    id = "filestation"
    name = "FileStation NFSv3 Server & Phone Storage Browser"
    description = "svc-168 embedded ONC-RPC / NFSv3 Server (portmap v2 + MOUNT v3 + 22 NFSv3 procs) with runtime folder remount + PhoneNfsClient (/sdcard TLS NFSv3 browser & downloader)"

    def actions(self) -> list[dict]:
        return [
            {"name": "announce_server", "label": "Announce PC NFSv3 Server (cmd:1001 type:1)", "description": "Start embedded NFSv3 server and announce export to phone"},
            {"name": "remount", "label": "Remount PC Export Folder", "description": "Swap exported PC directory live and re-announce", "default_params": {"path": "/tmp"}},
            {"name": "phonefs_connect", "label": "Mount Phone /sdcard Storage (type:0)", "description": "Ask phone to start its TLS NFSv3 server and mount /sdcard"},
            {"name": "phonefs_ls", "label": "List Phone /sdcard Root", "description": "Browse phone storage root directory via NFSv3 READDIRPLUS", "default_params": {"path": "/"}},
        ]

    def get_state(self) -> dict:
        return {
            **super().get_state(),
            **self.h._export_summary(),
            "phonefs": dict(getattr(self.h, "phonefs", {}) or {}),
        }

    async def invoke(self, action: str, params: dict | None = None,
                     conn: dict | None = None) -> dict:
        params = params or {}
        c = conn or self.h._live_ws()
        if action == "announce_server":
            return await self.h.request("nfs-announce", c)
        if action == "remount":
            path = str(params.get("path") or "")
            if not path:
                return {"ok": False, "error": "no path provided"}
            res = await self.h.remount(path, reannounce=True)
            res["detail"] = f"NFSv3 export remounted -> {res.get('export_root')} ({res.get('entries', 0)} items)"
            return res
        if action == "phonefs_connect":
            res = await self.h.phonefs_connect()
            res["detail"] = res.get("error") or f"Mounted phone /sdcard at {res.get('ip')} ports={res.get('ports')}"
            return res
        if action == "phonefs_ls":
            path = str(params.get("path") or "/")
            res = await self.h.phonefs_ls(path)
            if res.get("ok"):
                res["detail"] = f"Phone {res.get('path')}: {len(res.get('entries') or [])} entries"
            else:
                res["detail"] = res.get("error", "ls failed")
            return res
        if action in ("phonefs_open", "open"):
            path = str(params.get("path") or "")
            launch_app = bool(params.get("launch_app", True))
            return await self.h.phonefs_open(path, launch_app=launch_app)
        if action == "phonefs_download":
            path = str(params.get("path") or "")
            return await self.h.phonefs_download(path)
        if action in ("phonefs_delete", "delete"):
            path = str(params.get("path") or "")
            return await self.h.phonefs_delete(path)
        if action in ("phonefs_rename", "rename"):
            path = str(params.get("path") or "")
            new_name = str(params.get("new_name") or params.get("dst") or "")
            return await self.h.phonefs_rename(path, new_name)
        if action in ("phonefs_mkdir", "mkdir"):
            path = str(params.get("path") or "")
            return await self.h.phonefs_mkdir(path)
        if action in ("phonefs_upload", "upload"):
            local_p = str(params.get("local_path") or params.get("path") or "")
            remote_d = str(params.get("remote_dir") or "/")
            return await self.h.phonefs_upload(local_p, remote_d)
        if action == "stage_drag":
            path = str(params.get("path") or "")
            return await self.h.stage_phone_file_for_drag(path)
        if action == "add_share":
            return await self.h.nfs_add_share(
                str(params.get("alias") or ""),
                str(params.get("path") or ""),
                access_mode=str(params.get("access_mode") or "rw"),
            )
        if action == "remove_share":
            return await self.h.nfs_remove_share(str(params.get("alias") or ""))
        if action == "set_share_mode":
            return await self.h.nfs_set_share_mode(
                str(params.get("alias") or ""),
                str(params.get("access_mode") or "rw"),
            )
        if action == "list_shares":
            return self.h.nfs_list_shares()
        return await super().invoke(action, params, conn)

FileSharingService = FileStationNfsService
