from __future__ import annotations

import asyncio
import contextlib
import socket
from typing import Any

from samyuj.platform.linux.setup import check_udp_buffer_headroom

_LAST_SOCKET_RCVBUF_GOT: int = 0
_ACTIVE_ICE_UDP_PORTS: set[int] = set()

def get_active_ice_udp_ports() -> set[int]:
    return set(_ACTIVE_ICE_UDP_PORTS)

def get_last_socket_rcvbuf() -> int:
    return _LAST_SOCKET_RCVBUF_GOT

def boost_pc_socket_buffers(pc: Any, size: int = 4 * 1024 * 1024, events: Any = None) -> int:
    n = 0
    seen: set[int] = set()

    def _fix_transport(tr: Any) -> None:
        nonlocal n
        global _LAST_SOCKET_RCVBUF_GOT
        if tr is None or id(tr) in seen:
            return
        seen.add(id(tr))
        with contextlib.suppress(Exception):
            sock = tr.get_extra_info("socket")
            if sock is not None:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, size)
                with contextlib.suppress(Exception):
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, size)
                got = sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
                _LAST_SOCKET_RCVBUF_GOT = int(got)
                with contextlib.suppress(Exception):
                    addr = sock.getsockname()
                    if isinstance(addr, tuple) and len(addr) >= 2 and int(addr[1]) > 0:
                        _ACTIVE_ICE_UDP_PORTS.add(int(addr[1]))
                n += 1
                if events is not None and n == 1:
                    events.log(
                        "MEDIA_SOCKET_RCVBUF",
                        f"requested {size} got {got} bytes",
                        feature="appstream",
                    )
                    uh = check_udp_buffer_headroom(size)
                    if uh.get("clamped") or got < size:
                        events.log(
                            "MEDIA_SOCKET_RCVBUF_CLAMPED",
                            f"WARNING: Linux kernel clamped WebRTC UDP receive buffer to {got} bytes",
                            feature="media",
                        )

    with contextlib.suppress(Exception):
        for t in pc.getTransceivers():
            dtls = getattr(t, "_transport", None)
            ice = getattr(dtls, "transport", None)
            conn = getattr(ice, "_connection", None)
            for proto in list(getattr(conn, "_protocols", []) or []):
                _fix_transport(getattr(proto, "transport", None))
    return n

async def boost_pc_sockets_delayed(pc: Any, why: str, events: Any = None) -> None:
    for delay in (0.08, 0.6, 3.0):
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.sleep(delay)
            try:
                cnt = await asyncio.to_thread(boost_pc_socket_buffers, pc, 4 * 1024 * 1024, events)
                if cnt and events is not None:
                    events.log("MEDIA_SOCKET_BOOST", f"{cnt} socket(s) enlarged ({why})", feature="appstream")
            except Exception:
                pass
