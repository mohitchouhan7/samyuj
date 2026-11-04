from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import os
import re
import shutil
import ssl
import time
import urllib.parse
from pathlib import Path

from samyuj.protocol.constants import (
    APPLIST_CMD_BLACKLIST,
    APPLIST_CMD_RECENT,
    APPLIST_CMD_RUNNING,
    APPLIST_CMD_UPLOAD,
)
from samyuj.transport.connection import _set_tcp_nodelay

def _is_ft_error_body(blob: bytes) -> bool:
    if not blob:
        return True
    head = blob[:256]
    if head.startswith((b"downloadFile ->", b"download ->", b"404 ", b"403 ", b"400 ", b"500 ")):
        return True
    if len(blob) < 256 and (b"does not exist" in head or b"token is timeout" in head):
        return True
    return False

def _parse_ft_line(line: str) -> tuple[str, str, str, dict | None]:
    line = (line or "").strip()
    kind, _, rest = line.partition(":")
    mid, _, rest2 = rest.partition(":")
    name, _, qs = rest2.partition("?")
    payload = None
    if qs:
        with contextlib.suppress(json.JSONDecodeError):
            payload = json.loads(qs)
    return kind.lower(), mid, name, payload

def _store_icon_payload(blob: bytes, outdir, task_id: str,
                        files: list[dict], index: int = 0) -> list[str]:
    import io
    import zipfile
    if _is_ft_error_body(blob):
        return []
    outdir = Path(outdir)
    with contextlib.suppress(OSError):
        outdir.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    if blob[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(blob)) as z:
                for info_z in z.infolist():
                    n = info_z.filename
                    if n.endswith("/") or info_z.file_size > 16 * 1024 * 1024:
                        continue
                    safe = Path(n).name or f"icon_{len(saved)}.png"
                    dest = outdir / safe
                    with contextlib.suppress(OSError):
                        dest.write_bytes(z.read(n))
                        saved.append(safe)
            return saved
        except zipfile.BadZipFile:
            pass
    if blob[:8] == b"\x89PNG\r\n\x1a\n" or (
        files and 0 <= index < len(files)
        and str(files[index].get("name") or "").lower().endswith(".png")
        and len(blob) == int(files[index].get("size") or -1)
    ):
        raw_nm = (files[index].get("name") if (files and 0 <= index < len(files)) else None) or f"{task_id or 'icon'}_{index}.png"
        safe = Path(str(raw_nm)).name
        if not safe.lower().endswith(".png"):
            safe += ".png"
        with contextlib.suppress(OSError):
            (outdir / safe).write_bytes(blob)
            saved.append(safe)
        return saved
    safe_tid = re.sub(r"[^A-Za-z0-9_.-]", "_", str(task_id or "icons")).strip("._")[:64] or "icons"
    name = f"{safe_tid}.bin"
    with contextlib.suppress(OSError):
        (outdir / name).write_bytes(blob)
        saved.append(name)
    return saved

class AppListServiceMixin:

    async def _handle_applist(self, conn: dict, frame: dict, data, cmd,
                              tag: str) -> None:
        d = data if isinstance(data, dict) else {}
        if cmd == APPLIST_CMD_UPLOAD:
            apps = d.get("apps") or []
            wps = d.get("wpApps") or []
            self.app_list = list(apps)
            self.app_wp_list = list(wps)
            self.app_icons.update({
                "state": "announced", "port": int(d.get("port") or 0),
                "fingerprint": str(d.get("fingerprint") or ""),
                "token": str(d.get("token") or ""),
                "autoDownload": bool(d.get("autoDownload")),
                "appLimit": d.get("appLimit"),
                "files": [], "saved": [], "bytes": 0, "error": ""})
            self.events.log("APPLIST_RECV",
                            f"{tag} apps={len(apps)} wpApps={len(wps)} "
                            f"appLimit={d.get('appLimit')} port={d.get('port')} "
                            f"fp={str(d.get('fingerprint'))[:16]}.. "
                            f"token={bool(d.get('token'))} "
                            f"autoDownload={d.get('autoDownload')}", feature="app_list")
            with contextlib.suppress(OSError):
                (self.out_dir / "app_list.json").write_text(
                    json.dumps({"apps": apps, "wpApps": wps,
                                "port": d.get("port"),
                                "fingerprint": d.get("fingerprint"),
                                "token": d.get("token")}, indent=2),
                    encoding="utf-8")
            names = [f"{a.get('name')} <{a.get('pkg')}>" for a in apps[:12]]
            self.events.log("APPLIST_NAMES", "; ".join(names) or "(empty)",
                            feature="app_list")
            if self.app_icons["port"] and self.app_icons["fingerprint"]:
                conn["tasks"].append(asyncio.create_task(
                    self.pull_app_icons(conn, self.app_icons)))
            else:
                self.app_icons["state"] = "no-transfer-offered"
                self.events.log("APPICON_SKIP",
                                f"{tag} the svc-17 cmd:1 reply carried no port/fingerprint — "
                                f"the phone did not offer an icon-transfer "
                                f"session", feature="app_list")
            return
        if cmd == APPLIST_CMD_RUNNING:
            run = d.get("runningAppInfos") or []
            self.app_running = list(run)
            self.events.log("RUNNINGAPPS_RECV",
                            f"{tag} {len(run)} running tasks", feature="app_list")
            for r in run[:20]:
                self.events.log("RUNNINGAPP",
                                f"pkg={r.get('pkg')} userId={r.get('userId')} "
                                f"displayId={r.get('displayId')} "
                                f"visible={r.get('isVisible')} "
                                f"focused={r.get('isFocused')}", feature="app_list")
            with contextlib.suppress(OSError):
                (self.out_dir / "running_apps.json").write_text(
                    json.dumps(run, indent=2), encoding="utf-8")
            return
        if cmd == APPLIST_CMD_RECENT:
            rec = d.get("recentApps") or []
            self.app_recent = list(rec)
            self.events.log("RECENTAPPS_RECV",
                            f"{tag} {len(rec)} recent apps: "
                            f"{[a.get('pkg') for a in rec][:12]}", feature="app_list")
            with contextlib.suppress(OSError):
                (self.out_dir / "recent_apps.json").write_text(
                    json.dumps(rec, indent=2), encoding="utf-8")
            return
        if cmd == APPLIST_CMD_BLACKLIST:
            self.events.log("APPBLACKLIST_ACK", f"{tag} {str(d)[:200]}",
                            feature="app_list")
            return
        self.events.log("APPLIST_MSG", f"{tag} cmd={cmd} keys={sorted(d.keys())} "
                                       f"— logged, no reply", feature="app_list")

    def _persist_icon_cache(self, outdir: Path, fp_key: str) -> None:
        root = Path.home() / ".cache" / "samyuj" / "app_icons_cache"
        cache_dir = root / fp_key
        cache_dir.mkdir(parents=True, exist_ok=True)
        n = 0
        for p in Path(outdir).glob("*.png"):
            shutil.copy2(p, cache_dir / p.name)
            n += 1
        if n:
            (cache_dir / ".complete").write_text(str(n), encoding="utf-8")
            with contextlib.suppress(Exception):
                entries = sorted((d for d in root.iterdir() if d.is_dir()),
                                 key=lambda d: d.stat().st_mtime)
                for stale in entries[:-4]:
                    shutil.rmtree(stale, ignore_errors=True)

    async def pull_app_icons(self, conn: dict, info: dict) -> None:
        st = self.app_icons
        st["state"] = "connecting"
        ip = self._phone_ip(conn)
        port = int(info.get("port") or 0)
        fp = str(info.get("fingerprint") or "")
        token = str(info.get("token") or "")
        outdir = (self.rq_icons_dir or (self.out_dir / "app_icons"))
        fp_key = re.sub(r"[^a-f0-9]", "", (fp or "").strip().lower())[:48]
        cache_dir = (Path.home() / ".cache" / "samyuj" / "app_icons_cache" / fp_key) if fp_key else None
        if cache_dir is not None and (cache_dir / ".complete").exists():
            with contextlib.suppress(Exception):
                cached = sorted(p.name for p in cache_dir.glob("*.png"))
                if cached:
                    self.rq_icons_dir = cache_dir
                    st.update({"state": "done", "cached": True,
                               "dir": str(cache_dir), "count": len(cached),
                               "saved": cached[:8]})
                    self.events.log(
                        "APPICON_CACHE_HIT",
                        f"{len(cached)} icon(s) served from the persistent cache "
                        f"{cache_dir} (fp={fp[:16]}..) — phone download skipped",
                        feature="app_list")
                    return
        rawdir = outdir / "raw"
        with contextlib.suppress(OSError):
            rawdir.mkdir(parents=True, exist_ok=True)
        if not ip or not port:
            st.update({"state": "error", "error": f"no phone IP/port (ip={ip} port={port})"})
            self.events.log("APPICON_FAIL", st["error"], feature="app_list")
            return
        self.events.log("APPICON_DIAL",
                        f"wss://{ip}:{port}/websocket pin={fp[:16]}.. token={bool(token)} "
                        f"-> {outdir}", feature="app_list")
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        reader = writer = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(ip, port, ssl=ctx, server_hostname=ip),
                timeout=15)
            _set_tcp_nodelay(writer)
        except (OSError, ssl.SSLError, TimeoutError, asyncio.TimeoutError) as exc:
            st.update({"state": "error", "error": f"connect failed: {exc!r}"})
            self.events.log("APPICON_FAIL", st["error"], feature="app_list")
            return
        so = writer.get_extra_info("ssl_object")
        der = None
        with contextlib.suppress(Exception):
            der = so.getpeercert(binary_form=True)
        got_fp = hashlib.sha256(der).hexdigest().upper() if der else ""
        if fp and (not got_fp or got_fp != fp.upper()):
            st.update({"state": "error",
                       "error": f"cert pin mismatch: got {got_fp[:16]}.. want {fp[:16]}.."})
            self.events.log("APPICON_PIN_REJECT", st["error"], feature="app_list")
            with contextlib.suppress(Exception):
                writer.close()
            return
        self.events.log("APPICON_TLS_OK",
                        f"pinned fingerprint matches ({got_fp[:16]}..)", feature="app_list")
        try:
            status, rest = await self._ws_upgrade_plain(reader, writer,
                                                        f"{ip}:{port}", "/websocket")
            self.events.log("APPICON_WS_HANDSHAKE",
                            f"{status or '(no response)'} — "
                            f"{len(rest)} byte(s) of WS payload already buffered",
                            feature="app_list")
            if status and not status.startswith(("HTTP/1.1 101", "HTTP/1.0 101")):
                st.update({"state": "error",
                           "error": f"the phone's transfer server answered {status!r} "
                                    f"instead of 101 Switching Protocols"})
                self.events.log("APPICON_FAIL", st["error"], feature="app_list")
                return
            wbuf = bytearray(rest)
            captured = bytearray()
            task_id, total, files = "", 0, []
            deadline = time.monotonic() + 25.0
            while time.monotonic() < deadline:
                line = await self._read_ws_text(reader, timeout=8.0, buf=wbuf)
                if line is None:
                    break
                if line == "":
                    continue
                captured.extend(line.encode() + b"\n")
                kind, mid, name, payload = _parse_ft_line(line)
                self.events.log("APPICON_FT_MSG",
                                f"{kind}:{mid}:{name} {str(payload)[:200]}",
                                feature="app_list")
                if kind == "action" and payload is None:
                    self.events.log("APPICON_FT_UNPARSED",
                                    f"{kind}:{mid}:{name} carried no parseable JSON "
                                    f"payload — raw={line[:200]!r}", feature="app_list")
                if kind == "action" and name.lower() == "versionnegotiation":
                    await self._send_ft(writer, "ack", mid, "versionNegotiation",
                                        {"version": min(int((payload or {}).get("version", 1)), 1),
                                         "threadLimit": 1, "token": token})
                elif kind == "action" and name.lower() == "sendrequest":
                    p = payload or {}
                    task_id = str(p.get("taskId") or p.get("id") or "")
                    total = int(p.get("totalSize") or 0)
                    files = list(p.get("file") or [])
                    file_urls = [u for u in str(p.get("fileUrl") or "").split("##") if u]
                    st["files"] = [{"name": f.get("name"), "size": f.get("size"),
                                    "mimeType": f.get("mimeType")} for f in files]
                    self.events.log("APPICON_SENDREQUEST",
                                    f"taskId={task_id} totalSize={total} "
                                    f"fileCount={p.get('fileCount')} files={len(files)} "
                                    f"fileUrls={len(file_urls)} "
                                    f"first={[f.get('name') for f in files[:5]]}",
                                    feature="app_list")
                    await self._send_ft(writer, "ack", mid, "sendRequest", None)
                    await asyncio.sleep(0.05)
                    blob = await self._http_download(ip, port, ctx, task_id, token,
                                                     total, rawdir, file_urls=file_urls)
                    if blob and not _is_ft_error_body(blob):
                        saved = _store_icon_payload(blob, outdir, task_id, files, index=0)
                        total_bytes = len(blob)
                        if blob[:2] != b"PK" and len(files) > 1 and saved:
                            sem = asyncio.Semaphore(6)
                            async def _pull_idx(idx: int):
                                nonlocal total_bytes
                                async with sem:
                                    b_i = await self._http_download(
                                        ip, port, ctx, task_id, token,
                                        int(files[idx].get("size") or 65536),
                                        rawdir, file_urls=file_urls, index=idx)
                                    if b_i and not _is_ft_error_body(b_i):
                                        s_i = _store_icon_payload(b_i, outdir, task_id, files, index=idx)
                                        saved.extend(s_i)
                                        total_bytes += len(b_i)
                            await asyncio.gather(*(_pull_idx(i) for i in range(1, len(files))))
                        st["saved"] = saved
                        st["bytes"] = total_bytes
                        st["state"] = "done" if saved else "error"
                        self.events.log("APPICON_SAVED",
                                        f"{total_bytes} bytes -> {len(saved)} file(s) in "
                                        f"{outdir}: {saved[:8]}", feature="app_list")
                        if fp_key and saved:
                            with contextlib.suppress(Exception):
                                await asyncio.to_thread(
                                    self._persist_icon_cache, outdir, fp_key)
                        with contextlib.suppress(Exception):
                            await self._send_ft(writer, "action", "Client1020", "message",
                                                {"msgId": 1, "length": len(saved),
                                                 "data": [{"name": n, "index": i} for i, n in enumerate(saved)]})
                            await self._send_ft(writer, "action", "Client1021", "status",
                                                {"taskId": task_id, "type": 1, "reason": "success"})
                    break
                elif kind == "action" and name.lower() == "status":
                    p = payload or {}
                    self.events.log("APPICON_STATUS",
                                    f"type={p.get('type')} reason={str(p.get('reason'))[:120]}",
                                    feature="app_list")
                    await self._send_ft(writer, "ack", mid, "status", None)
                    if p.get("type") in (1, 3):
                        break
            if st["state"] != "done":
                st["state"] = st["state"] if st.get("error") else "captured"
                with contextlib.suppress(OSError):
                    (rawdir / f"session_{int(time.time())}.txt").write_bytes(
                        bytes(captured))
                self.events.log("APPICON_CAPTURED",
                                f"{len(captured)} bytes of control-channel traffic saved "
                                f"to {rawdir} — no ZIP body arrived; inspect it to "
                                f"complete the framing", feature="app_list")
        except (OSError, ssl.SSLError, asyncio.TimeoutError, TimeoutError,
                ValueError) as exc:
            st.update({"state": "error", "error": repr(exc)})
            self.events.log("APPICON_FAIL", repr(exc), feature="app_list")
        finally:
            with contextlib.suppress(Exception):
                writer.close()

    @staticmethod
    def _phone_ip(conn: dict) -> str:
        w = conn.get("writer") if isinstance(conn, dict) else None
        if w is not None:
            with contextlib.suppress(Exception):
                pn = w.get_extra_info("peername")
                if pn:
                    return str(pn[0]).replace("::ffff:", "")
        for k in ("remote", "peer", "remote_ip"):
            v = (conn or {}).get(k)
            if v:
                m = re.search(r"(\d{1,3}(?:\.\d{1,3}){3})", str(v))
                if m:
                    return m.group(1)
        return ""

    @staticmethod
    async def _ws_upgrade_plain(reader, writer, host: str, path: str) -> str:
        _set_tcp_nodelay(writer)
        key = base64.b64encode(os.urandom(16)).decode()
        writer.write(f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\n"
                     f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                     f"Sec-WebSocket-Version: 13\r\n\r\n".encode())
        await writer.drain()
        head = b""
        try:
            while b"\r\n\r\n" not in head:
                chunk = await asyncio.wait_for(reader.read(4096), timeout=10)
                if not chunk:
                    break
                head += chunk
                if len(head) > 65536:
                    break
        except (asyncio.TimeoutError, TimeoutError, OSError):
            pass
        status = head.split(b"\r\n", 1)[0].decode("latin-1", "replace")
        rest = head.split(b"\r\n\r\n", 1)[1] if b"\r\n\r\n" in head else b""
        return status, rest

    @staticmethod
    async def _read_ws_text(reader, timeout: float = 8.0,
                            buf: bytearray | None = None) -> str | None:
        async def take(n: int) -> bytes:
            if buf is not None and len(buf) >= n:
                out = bytes(buf[:n])
                del buf[:n]
                return out
            if buf is not None and buf:
                out = bytearray(buf)
                buf.clear()
                out += await asyncio.wait_for(reader.readexactly(n - len(out)),
                                              timeout=timeout)
                return bytes(out)
            return await asyncio.wait_for(reader.readexactly(n), timeout=timeout)

        try:
            hdr = await take(2)
        except (asyncio.IncompleteReadError, asyncio.TimeoutError, TimeoutError,
                OSError):
            return None
        op = hdr[0] & 0x0F
        masked = bool(hdr[1] & 0x80)
        n = hdr[1] & 0x7F
        try:
            if n == 126:
                n = int.from_bytes(await take(2), "big")
            elif n == 127:
                n = int.from_bytes(await take(8), "big")
            if n > 16 * 1024 * 1024:
                return None
            mk = await take(4) if masked else b""
            payload = await take(n) if n else b""
        except (asyncio.IncompleteReadError, asyncio.TimeoutError, TimeoutError,
                OSError):
            return None
        if masked and mk:
            payload = bytes(b ^ mk[i % 4] for i, b in enumerate(payload))
        if op == 0x8:
            return None
        if op == 0x9:
            return ""
        return payload.decode("utf-8", "replace")

    @staticmethod
    async def _send_ft(writer, kind: str, mid, name: str, payload) -> None:
        line = f"{kind}:{mid}:{name}"
        if payload is not None:
            line += "?" + json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        raw = line.encode()
        hdr = bytes([0x81])
        n = len(raw)
        mask = os.urandom(4)
        if n < 126:
            hdr += bytes([0x80 | n])
        elif n < 65536:
            hdr += bytes([0x80 | 126]) + n.to_bytes(2, "big")
        else:
            hdr += bytes([0x80 | 127]) + n.to_bytes(8, "big")
        writer.write(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(raw)))
        await writer.drain()

    @staticmethod
    async def _http_download(ip: str, port: int, ctx, task_id: str, token: str,
                             total: int, rawdir, file_urls: list[str] | None = None,
                             index: int | None = None) -> bytes:
        q_tid = urllib.parse.quote(str(task_id or ""), safe="")
        q_tok = urllib.parse.quote(str(token or ""), safe="")
        cands: list[str] = []
        if file_urls:
            idx_to_use = index if index is not None else 0
            if 0 <= idx_to_use < len(file_urls):
                sp = urllib.parse.urlsplit(file_urls[idx_to_use])
                if sp.path:
                    cands.append(f"{sp.path}?{sp.query}" if sp.query else sp.path)
        if index is not None:
            for p in (
                f"/downloadFile?taskId={q_tid}&token={q_tok}&index={index}",
                f"/download?taskId={q_tid}&token={q_tok}&index={index}",
            ):
                if p not in cands:
                    cands.append(p)
        else:
            for p in (
                f"/downloadFile?taskId={q_tid}&token={q_tok}",
                f"/download?taskId={q_tid}&token={q_tok}",
                f"/downloadFile?taskId={q_tid}&token={q_tok}&index=0",
                f"/download?taskId={q_tid}&token={q_tok}&index=0",
            ):
                if p not in cands:
                    cands.append(p)

        for delay in (0.0, 0.15, 0.35):
            if delay:
                await asyncio.sleep(delay)
            for path in cands:
                try:
                    r, w = await asyncio.wait_for(
                        asyncio.open_connection(ip, port, ssl=ctx, server_hostname=ip),
                        timeout=15)
                except (OSError, ssl.SSLError, TimeoutError, asyncio.TimeoutError):
                    continue
                try:
                    w.write(f"GET {path} HTTP/1.1\r\nHost: {ip}:{port}\r\n"
                            f"User-Agent: Samyuj-PC/12.0\r\n"
                            f"Connection: close\r\n\r\n".encode())
                    await w.drain()
                    head = b""
                    while b"\r\n\r\n" not in head:
                        chunk = await asyncio.wait_for(r.read(4096), timeout=15)
                        if not chunk:
                            break
                        head += chunk
                        if len(head) > 65536:
                            break
                    safe_tid = re.sub(r"[^A-Za-z0-9_.-]", "_", str(task_id or "x"))[:64]
                    safe_p = re.sub(r"[^A-Za-z0-9_.-]", "_", path.split("?")[0].strip("/"))[:64] or "dl"
                    if not head.startswith(b"HTTP/1.1 200") and not head.startswith(b"HTTP/1.0 200"):
                        with contextlib.suppress(OSError):
                            (rawdir / f"get_{safe_p}.txt").write_bytes(head[:2048])
                        continue
                    pre, _, rest = head.partition(b"\r\n\r\n")
                    low_hdr = pre.lower()
                    m_cl = re.search(rb"content-length:\s*(\d+)", low_hdr)
                    cl_len = min(int(m_cl.group(1)), 512 * 1024 * 1024) if m_cl else None
                    body = bytearray(rest)
                    limit = cl_len if cl_len is not None else min(max(total, 1 << 20) * 4, 512 * 1024 * 1024)
                    while len(body) < limit:
                        chunk = await asyncio.wait_for(r.read(min(65536, max(4096, limit - len(body)))), timeout=30)
                        if not chunk:
                            break
                        body += chunk
                    data_bytes = bytes(body[:cl_len] if cl_len is not None else body)
                    if _is_ft_error_body(data_bytes):
                        with contextlib.suppress(OSError):
                            (rawdir / f"err_{safe_tid}_{safe_p}.txt").write_bytes(data_bytes[:512])
                        continue
                    with contextlib.suppress(OSError):
                        if index in (None, 0):
                            (rawdir / f"body_{safe_tid}.bin").write_bytes(data_bytes)
                    return data_bytes
                except (OSError, asyncio.TimeoutError, TimeoutError):
                    continue
                finally:
                    with contextlib.suppress(Exception):
                        w.close()
        return b""

from .base import SamyujService

class AppListIconService(SamyujService):
    service_id = 17
    id = "applist"
    name = "App List & Icon Transfer Service"
    description = "svc-17 app catalog + Go FileTransfer TLS PNG/ZIP icon downloader"

    def actions(self) -> list[dict]:
        return [
            {"name": "fetch_apps", "label": "Fetch App List + Icons (cmd:2)", "description": "Request the app-list upload (cmd:1 reply) and pull PNG icons"},
            {"name": "pull_icons", "label": "Re-Pull App Icons", "description": "Download app icons from last announced transfer port"},
            {"name": "fetch_running", "label": "Fetch Running Apps (cmd:5)", "description": "Request the running-apps reply"},
        ]

    def get_state(self) -> dict:
        return {
            **super().get_state(),
            "apps_count": len(getattr(self.h, "app_list", []) or []),
            "running_count": len(getattr(self.h, "app_running", []) or []),
            "icons": dict(getattr(self.h, "app_icons", {}) or {}),
        }

    async def invoke(self, action: str, params: dict | None = None,
                     conn: dict | None = None) -> dict:
        c = conn or self.h._live_ws()
        if action == "fetch_apps":
            return await self.h.request("applist", c)
        if action == "pull_icons":
            if c is None:
                return {"ok": False, "error": "no live phone connection"}
            info = getattr(self.h, "app_icons", {}) or {}
            if not info.get("port"):
                return await self.h.request("applist", c)
            asyncio.create_task(self.h.pull_app_icons(c, info))
            return {"ok": True, "detail": f"pulling icons from port {info.get('port')}"}
        if action == "fetch_running":
            return await self.h.request("runningapps", c)
        return await super().invoke(action, params, conn)
