from __future__ import annotations

import contextlib
import os
import socket
import threading
import time

NETLINK_KOBJECT_UEVENT = 15
_USB_SYS_BUS = "/sys/bus/usb/devices"

class UeventWatcher:

    def __init__(self, on_event) -> None:
        self._on_event = on_event
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = False
        self.mode: str = "none"

    def start(self) -> str:
        self._stop = False
        self.mode = "netlink" if self._try_netlink() else "sysfs-poll"
        self._thread = threading.Thread(
            target=self._run, name="sc-uevent-watch", daemon=True)
        self._thread.start()
        return self.mode

    def stop(self) -> None:
        self._stop = True
        with contextlib.suppress(Exception):
            if self._sock is not None:
                self._sock.close()
        self._sock = None
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=2.0)
        self._thread = None

    def _try_netlink(self) -> bool:
        try:
            s = socket.socket(socket.AF_NETLINK, socket.SOCK_DGRAM,
                              NETLINK_KOBJECT_UEVENT)
            s.bind((os.getpid(), 1))
            s.settimeout(1.0)
            self._sock = s
            return True
        except OSError:
            with contextlib.suppress(Exception):
                if self._sock is not None:
                    self._sock.close()
            self._sock = None
            return False

    def _run(self) -> None:
        if self.mode == "netlink" and self._sock is not None:
            self._netlink_loop()
        else:
            self._sysfs_poll_loop()

    def _netlink_loop(self) -> None:
        while not self._stop:
            try:
                data, _addr = self._sock.recvfrom(65536)
            except (socket.timeout, TimeoutError):
                continue
            except OSError:
                return
            env = self.parse_kernel_datagram(data)
            if env is None:
                continue
            kind = self.classify_event(env)
            if kind:
                self._emit(kind, env)

    @staticmethod
    def parse_kernel_datagram(data: bytes) -> dict | None:
        try:
            parts = [p for p in data.decode("utf-8", "replace").split("\x00") if p]
        except Exception:
            return None
        if not parts or parts[0].startswith("libudev"):
            return None
        env: dict[str, str] = {}
        for kv in parts[1:]:
            k, eq, v = kv.partition("=")
            if eq:
                env[k] = v
        action, _, devpath = parts[0].partition("@")
        env.setdefault("ACTION", action.lower())
        env.setdefault("DEVPATH", devpath)
        return env

    @staticmethod
    def classify_event(env: dict) -> str | None:
        action = str(env.get("ACTION") or "").lower()
        subsys = str(env.get("SUBSYSTEM") or "").lower()
        devpath = str(env.get("DEVPATH") or "")
        if action not in ("add", "remove"):
            return None
        if subsys == "usb":
            last = devpath.rsplit("/", 1)[-1]
            if ":" in last:
                return None
            return "usb-add" if action == "add" else "usb-remove"
        if subsys == "net":
            if "usb" not in devpath:
                return None
            return "net-add" if action == "add" else "net-remove"
        return None

    def _emit(self, kind: str, env: dict) -> None:
        detail = {
            "summary": " ".join(str(env.get(k, "")) for k in
                                ("SUBSYSTEM", "DEVPATH", "PRODUCT", "INTERFACE"))[:200],
            "devpath": str(env.get("DEVPATH") or ""),
            "product": str(env.get("PRODUCT") or ""),
        }
        with contextlib.suppress(Exception):
            self._on_event(kind, detail)

    def _sysfs_poll_loop(self) -> None:
        last: frozenset | None = None
        while not self._stop:
            sig = self._sysfs_signature()
            if last is not None and sig != last:
                added = sig - last
                removed = last - sig
                if added:
                    self._emit("usb-add", {"summary": f"sysfs-poll added={sorted(added)[:3]}"})
                if removed:
                    self._emit("usb-remove", {"summary": f"sysfs-poll removed={sorted(removed)[:3]}"})
            last = sig
            for _ in range(30):
                if self._stop:
                    return
                time.sleep(0.1)

    @staticmethod
    def _sysfs_signature() -> frozenset:
        sig: set[str] = set()
        with contextlib.suppress(OSError):
            for entry in os.listdir(_USB_SYS_BUS):
                if ":" in entry or entry.startswith("usb"):
                    continue
                p = os.path.join(_USB_SYS_BUS, entry)
                try:
                    with open(os.path.join(p, "idVendor"), encoding="ascii") as fh:
                        vid = fh.read().strip()
                    with open(os.path.join(p, "idProduct"), encoding="ascii") as fh:
                        pid = fh.read().strip()
                    sig.add(f"{vid}:{pid}:{entry}")
                except OSError:
                    continue
        return frozenset(sig)
