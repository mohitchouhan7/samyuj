from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from samyuj.media.peer import (
    boost_pc_socket_buffers as _boost_pc_socket_buffers_peer,
    boost_pc_sockets_delayed as _boost_pc_sockets_delayed_peer,
)
from .websocket import WsFrameParser, ws_frame

def _set_tcp_nodelay(writer_or_sock) -> bool:
    if writer_or_sock is None:
        return False
    sock = writer_or_sock
    with contextlib.suppress(Exception):
        if hasattr(writer_or_sock, "get_extra_info"):
            s = writer_or_sock.get_extra_info("socket")
            if s is not None:
                sock = s
            else:
                tr = getattr(writer_or_sock, "transport", None) or getattr(writer_or_sock, "_transport", None)
                if tr is not None:
                    sock = getattr(tr, "_sock", None) or tr.get_extra_info("socket")
    if sock is None or not hasattr(sock, "setsockopt"):
        return False
    import socket as _sk
    ok = False
    with contextlib.suppress(Exception):
        sock.setsockopt(_sk.IPPROTO_TCP, _sk.TCP_NODELAY, 1)
        ok = True
    with contextlib.suppress(Exception):
        if hasattr(_sk, "TCP_QUICKACK"):
            sock.setsockopt(_sk.IPPROTO_TCP, getattr(_sk, "TCP_QUICKACK"), 1)
    return ok

set_tcp_nodelay = _set_tcp_nodelay

def _boost_pc_socket_buffers(pc: Any, size: int = 4 * 1024 * 1024) -> int:
    return _boost_pc_socket_buffers_peer(pc, size=size)

async def _boost_pc_sockets_delayed(h: Any, pc: Any, why: str = "") -> None:
    events = getattr(h, "events", None)
    await _boost_pc_sockets_delayed_peer(pc, why, events=events)

class TransportConnection:

    def __init__(
        self,
        conn_id: int,
        reader: asyncio.StreamReader | None,
        writer: asyncio.StreamWriter | None,
        remote: Any = None,
        role: str = "server",
        transport: str = "wifi",
    ) -> None:
        self.id = int(conn_id)
        self.reader = reader
        self.writer = writer
        self.remote = remote
        self.role = role
        self.transport = transport
        self.ws = False
        self.ws_parser = WsFrameParser()
        self.hello: dict[str, Any] | None = None
        self.replied = False
        self.is_gatt_tunnel = False
        self.closed = False
        set_tcp_nodelay(writer)

    async def send_ws_text(self, text: str, mask: bool = False) -> bool:
        if self.closed or self.writer is None:
            return False
        try:
            frame = ws_frame(0x1, text.encode("utf-8"), mask=mask)
            self.writer.write(frame)
            await self.writer.drain()
            return True
        except Exception:
            return False

    def send_ws_text_nowait(self, text: str, mask: bool = False) -> bool:
        if self.closed or self.writer is None:
            return False
        try:
            tr = getattr(self.writer, "transport", None)
            if tr is not None and hasattr(tr, "get_write_buffer_size"):
                if tr.get_write_buffer_size() > 65536:
                    return False
            frame = ws_frame(0x1, text.encode("utf-8"), mask=mask)
            self.writer.write(frame)
            return True
        except Exception:
            return False

    async def close(self) -> None:
        self.closed = True
        if self.writer is not None:
            with contextlib.suppress(Exception):
                self.writer.close()
                await self.writer.wait_closed()
