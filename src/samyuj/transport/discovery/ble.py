from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import logging
import os
import shutil
import subprocess
from typing import Any

BLE_SVC_UUID = "00005246-0000-1000-8000-00805f9b34fb"
BLE_SVC_UUID_16 = 0x5246
BLE_NAME_UUID_16 = 0x0720
CHR_WRITE = "00000722-0000-1000-8000-00805f9b34fb"
CHR_NOTIFY = "00000726-0000-1000-8000-00805f9b34fb"
CHR_LONG = "00000728-0000-1000-8000-00805f9b34fb"

def make_nonce_id(real_id_b64: str) -> str:
    real = base64.b64decode(real_id_b64)
    nonce = os.urandom(8)
    digest = hashlib.sha256(real + nonce).digest()[:8]
    return base64.b64encode(nonce + digest).decode("ascii")

def verify_nonce_id(adv_b64: str, real_id_b64: str) -> bool:
    try:
        adv = base64.b64decode(adv_b64)
        real = base64.b64decode(real_id_b64)
    except Exception:
        return False
    if len(adv) != 16 or len(real) != 16:
        return False
    nonce, tail = adv[:8], adv[8:]
    return hmac.compare_digest(hashlib.sha256(real + nonce).digest()[:8], tail)

def ble_service_data(
    nonce_id_b64: str,
    dev_type: int = 4,
    os_id: int = 4,
    cap: int = 5365,
    tablet: bool = False,
) -> bytes:
    b = bytearray()
    b.append(0x10 | ((2 if tablet else 0) & 0x0F))
    b.append(((dev_type & 0x0F) << 4) | (os_id & 0x0F))
    b += (cap & 0xFFFF).to_bytes(2, "little")
    raw = base64.b64decode(nonce_id_b64)
    if len(raw) != 16:
        raw = base64.b64decode(make_nonce_id(nonce_id_b64))
    b += raw
    return bytes(b)

def parse_r4_service_data(sd: bytes) -> dict[str, Any] | None:
    if not sd or len(sd) < 20:
        return None
    return {
        "marker": sd[0],
        "type": (sd[1] >> 4) & 0x0F,
        "os": sd[1] & 0x0F,
        "cap": int.from_bytes(sd[2:4], "little"),
        "nonce_b64": base64.b64encode(bytes(sd[4:20])).decode("ascii"),
    }

def ble_advertisement_hex(nonce_id_b64: str, device_name: str, **kw: Any) -> tuple[str, str]:
    sd = ble_service_data(nonce_id_b64, **kw)
    adv = (
        bytes([2, 1, 6])
        + bytes([3, 3, 0x46, 0x52])
        + bytes([1 + 2 + len(sd), 0x16, 0x46, 0x52])
        + sd
    )
    name = (device_name or "Samyuj-PC").encode("utf-8")[:27].ljust(27, b"\x00")
    srsp = bytes([1 + 2 + len(name), 0x16, 0x20, 0x07]) + name
    return adv.hex(), srsp.hex()

def fragment_long_gatt(frame: bytes, mtu: int, msg_id: int) -> list[bytes]:
    if mtu < 8:
        mtu = 23
    chunk = mtu - 7
    total = (len(frame) + chunk - 1) // chunk
    if not 1 <= total <= 15:
        raise ValueError(f"frame {len(frame)}B = {total} fragments at MTU {mtu} (phone allows 1..15)")
    msg_id = (msg_id - 1) % 255 + 1
    return [
        bytes([msg_id, ((total & 15) << 4) | ((i + 1) & 15)]) + frame[i * chunk : (i + 1) * chunk]
        for i in range(total)
    ]

def reassemble_long_gatt(packets: list[bytes]) -> bytes:
    if not packets:
        raise ValueError("incomplete long GATT packet set")
    parts: dict[int, bytes] = {}
    totals: set[int] = set()
    msg_ids: set[int] = set()
    for p in packets:
        if not isinstance(p, (bytes, bytearray, memoryview)) or len(p) < 2:
            raise ValueError("malformed long GATT fragment")
        msg_ids.add(int(p[0]))
        hdr = int(p[1])
        t = (hdr >> 4) & 15
        idx = hdr & 15
        if not (1 <= t <= 15 and 1 <= idx <= t):
            raise ValueError("invalid long GATT fragment header")
        totals.add(t)
        parts[idx] = bytes(p[2:])
    if len(msg_ids) != 1 or len(totals) != 1:
        raise ValueError("inconsistent long GATT packet set")
    total = next(iter(totals))
    if len(parts) != total or len(packets) != total:
        raise ValueError("incomplete long GATT packet set")
    return b"".join(parts[i] for i in range(1, total + 1))

def bootstrap_remote_client_fit(
    runtime: Any,
    budget: int,
    ip_override: str | None = None,
) -> str:
    full = json.loads(runtime._build_bootstrap_json(for_usb=False, ip_override=ip_override))
    shed_order = [
        "sn", "verCode", "verName", "cap", "scType", "type", "os", "ver", "ip", "i3", "serviceId"
    ]

    def _sz(d: dict[str, Any]) -> int:
        return len(json.dumps(d, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))

    d = dict(full)
    if "policies" in d and len(d["policies"]) > 1:
        wifi_p = [p for p in d["policies"] if "wifi" in p]
        if wifi_p:
            d["policies"] = wifi_p
    for k in shed_order:
        if _sz(d) <= budget:
            break
        d.pop(k, None)
    while _sz(d) > budget and len(d.get("deviceName", "")) > 4:
        d["deviceName"] = d["deviceName"][:-2]
    if _sz(d) > budget:
        d.pop("deviceName", None)
    return json.dumps(d, separators=(",", ":"), ensure_ascii=False)

def gatt_bootstrap_frame(
    nonce_id_b64: str,
    remote_client_json: str,
    secret_key_b64: str | None = None,
) -> bytes | None:
    nonce = base64.b64decode(nonce_id_b64)
    if len(nonce) != 16:
        nonce = base64.b64decode(make_nonce_id(nonce_id_b64))
    if secret_key_b64 is None or str(secret_key_b64) == "":
        return nonce + b"\x02" + remote_client_json.encode("utf-8")
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        iv = os.urandom(12)
        aad = nonce + b"\x01"
        key = base64.b64decode(secret_key_b64)
        ct = AESGCM(key).encrypt(iv, remote_client_json.encode("utf-8"), aad)
        return nonce + b"\x01" + iv + ct
    except Exception as exc:
        logging.getLogger("samyuj.ble").warning("BLE_ENCRYPT_FAILED: %r", exc)
        return None

def decode_gatt_frame(data: bytes, keys: list[str] | None = None) -> dict[str, Any] | None:
    if not data:
        return None
    stripped = data.lstrip()
    if stripped[:1] in (b"{", b"[") and stripped[-1:] in (b"}", b"]"):
        with contextlib.suppress(Exception):
            text = data.decode("utf-8")
            parsed_json = json.loads(text)
            return {"op": None, "raw": True, "text": text, "json": parsed_json}
    if len(data) < 17:
        return None
    nonce, op = data[:16], data[16]
    out = {"op": op, "nonce_b64": base64.b64encode(nonce).decode("ascii")}
    if op == 2:
        out["text"] = data[17:].decode("utf-8", "replace")
        with contextlib.suppress(Exception):
            out["json"] = json.loads(out["text"])
        return out
    if op == 3:
        return out
    if op == 1 and len(data) >= 29:
        iv, ct = data[17:29], data[29:]
        for k in keys or []:
            try:
                from cryptography.hazmat.primitives.ciphers.aead import AESGCM

                pt = AESGCM(base64.b64decode(k)).decrypt(iv, ct, nonce + b"\x01")
                out["text"] = pt.decode("utf-8", "replace")
                with contextlib.suppress(Exception):
                    out["json"] = json.loads(out["text"])
                return out
            except Exception:
                continue
        out["encrypted"] = True
        return out
    return out

class BleAdvertiser:

    REGISTER_OK = "advertising object registered"
    BAD_WORDS = ("invalid", "failed", "error", "not available", "too large", "too much")

    def __init__(self, runtime: Any) -> None:
        self.h = runtime
        self.proc: subprocess.Popen[str] | None = None
        self.ok = False
        self.lines: list[str] = []
        self._pump_task: asyncio.Task[None] | None = None
        self._btmgmt_used = False

    def payloads(self) -> tuple[str, bytes]:
        nonce = getattr(self.h, "mdns_nonce", "") or make_nonce_id(self.h.device_id_b64)
        self.h.mdns_nonce = nonce
        sd = ble_service_data(nonce, dev_type=4, os_id=4, cap=getattr(self.h, "pc_cap", 3))
        return nonce, sd

    def commands(self, sd: bytes) -> list[str]:
        sd_hex = " ".join(f"0x{b:02x}" for b in sd)
        return [
            "power on",
            "menu advertise",
            "clear uuids",
            "clear service",
            f"uuids {BLE_SVC_UUID}",
            f"service {BLE_SVC_UUID} {sd_hex}",
            "discoverable on",
            "timeout 0",
            "back",
            "advertise on",
        ]

    async def start(self) -> bool:
        nonce, sd = self.payloads()
        cmds = self.commands(sd)
        if shutil.which("bluetoothctl"):
            try:
                self.proc = subprocess.Popen(
                    ["bluetoothctl"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                loop = asyncio.get_running_loop()
                registered = asyncio.Event()
                bad: list[str] = []

                async def pump() -> None:
                    assert self.proc and self.proc.stdout
                    while True:
                        line = await loop.run_in_executor(None, self.proc.stdout.readline)
                        if not line:
                            break
                        line = line.strip()
                        if not line:
                            continue
                        if len(self.lines) < 300:
                            self.lines.append(line)
                        low = line.lower()
                        if self.REGISTER_OK in low:
                            registered.set()
                        elif any(k in low for k in self.BAD_WORDS):
                            bad.append(line)

                self._pump_task = asyncio.create_task(pump())
                for c in cmds:
                    if self.proc.poll() is not None:
                        break
                    with contextlib.suppress(Exception):
                        assert self.proc.stdin
                        self.proc.stdin.write(c + "\n")
                        self.proc.stdin.flush()
                    await asyncio.sleep(0.4)
                with contextlib.suppress(asyncio.TimeoutError, TimeoutError):
                    await asyncio.wait_for(registered.wait(), timeout=4.5)
                if registered.is_set() and not bad and self.proc.poll() is None:
                    self.ok = True
                    self.h.events.log(
                        "BLE_ADV_REGISTERED",
                        f"uuid={BLE_SVC_UUID} sd={sd.hex()} nonce={nonce}",
                    )
                    return True
                await self.stop()
            except Exception as exc:
                self.h.events.log("BLE_ADV_SPAWN_FAIL", repr(exc))
        if shutil.which("btmgmt"):
            adv_hex, srsp_hex = ble_advertisement_hex(
                nonce, self.h.device_name, cap=getattr(self.h, "pc_cap", 3)
            )
            cmd = [
                "btmgmt", "add-adv", "-u", f"0x{BLE_SVC_UUID_16:04x}",
                "-d", adv_hex[14:], "-s", srsp_hex, "1",
            ]
            with contextlib.suppress(Exception):
                r = await asyncio.to_thread(
                    subprocess.run, cmd, capture_output=True, text=True, timeout=5
                )
                if r.returncode == 0:
                    self.ok = True
                    self._btmgmt_used = True
                    self.h.events.log("BLE_ADV_BTMGMT_OK", f"adv={adv_hex} srsp={srsp_hex}")
                    return True
        self.ok = False
        return False

    async def stop(self) -> None:
        if self._pump_task:
            self._pump_task.cancel()
            self._pump_task = None
        if self.proc:
            with contextlib.suppress(Exception):
                if self.proc.stdin:
                    self.proc.stdin.write("advertise off\nquit\n")
                    self.proc.stdin.flush()
                self.proc.wait(timeout=2)
            with contextlib.suppress(Exception):
                self.proc.kill()
            self.proc = None
        if self._btmgmt_used and shutil.which("btmgmt"):
            with contextlib.suppress(Exception):
                await asyncio.to_thread(
                    subprocess.run, ["btmgmt", "rm-adv", "1"], capture_output=True, timeout=3
                )
            self._btmgmt_used = False
        self.ok = False
