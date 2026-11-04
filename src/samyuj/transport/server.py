from __future__ import annotations

import asyncio
import base64
import contextlib
import os
import ssl
from typing import Any, Callable, Coroutine

from .connection import set_tcp_nodelay
from .websocket import WsFrameParser, ws_accept_value, ws_frame

class TlsControlServer:

    def __init__(
        self,
        host: str,
        port: int,
        ssl_context: ssl.SSLContext | None,
        on_client: Callable[[asyncio.StreamReader, asyncio.StreamWriter], Coroutine[Any, Any, None]],
    ) -> None:
        self.host = host
        self.port = int(port)
        self.ssl_context = ssl_context
        self.on_client = on_client
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> int:
        self._server = await asyncio.start_server(
            self.on_client,
            host=self.host,
            port=self.port,
            ssl=self.ssl_context,
        )
        if self._server.sockets:
            sockname = self._server.sockets[0].getsockname()
            if isinstance(sockname, tuple) and len(sockname) >= 2:
                self.port = int(sockname[1])
        return self.port

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()
            self._server = None

def build_http_101_upgrade_response(sec_websocket_key: str) -> bytes:
    accept = ws_accept_value(sec_websocket_key.strip())
    return (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
    ).encode("ascii")

async def ws_upgrade_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    host: str,
    port: int,
    path: str = "/",
) -> WsFrameParser:
    set_tcp_nodelay(writer)
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    req = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n"
    )
    writer.write(req.encode("ascii"))
    await writer.drain()
    buf = b""
    while b"\r\n\r\n" not in buf:
        if len(buf) > 65536:
            raise ConnectionError("HTTP upgrade header exceeded 64 KB")
        chunk = await asyncio.wait_for(reader.read(4096), timeout=8.0)
        if not chunk:
            raise ConnectionError("EOF before HTTP 101 upgrade")
        buf += chunk
    hdr_end = buf.index(b"\r\n\r\n") + 4
    status_line = buf[:hdr_end].splitlines()[0].decode("latin1", "replace")
    if "101" not in status_line:
        raise ConnectionError(f"WebSocket upgrade rejected: {status_line}")
    parser = WsFrameParser()
    if len(buf) > hdr_end:
        parser.buf.extend(buf[hdr_end:])
    return parser

__all__ = ["TlsControlServer", "build_http_101_upgrade_response", "ws_frame", "ws_upgrade_client"]
