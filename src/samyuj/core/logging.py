from __future__ import annotations

import collections
import contextlib
import csv
import datetime
import hashlib
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("samyuj")

_SECRET_PATTERNS = (
    (re.compile(r'("sharedKey"\s*:\s*")([^"]+)(")'), r'\1<REDACTED>\3'),
    (re.compile(r'("key"\s*:\s*")([A-Za-z0-9+/=_\-]{8,})(")'), r'\1<REDACTED>\3'),
    (re.compile(r'("secret"\s*:\s*")([^"]+)(")'), r'\1<REDACTED>\3'),
    (re.compile(r'("pass(?:word)?"\s*:\s*")([^"]+)(")'), r'\1<REDACTED>\3'),
    (re.compile(r'("token"\s*:\s*")(?!OK")([^"]{6,})(")'), r'\1<REDACTED>\3'),
    (re.compile(r'("fp2?"\s*:\s*")([A-Za-z0-9+/=_\-]{12,})(")'), r'\1<REDACTED>\3'),
    (re.compile(r'("wallpaper"\s*:\s*")([^"]{32,})(")'), r'\1<REDACTED_B64>\3'),
    (re.compile(r'(token=)([A-Za-z0-9+/=_\-]{6,})'), r'\1<REDACTED>'),
    (re.compile(r'(pass(?:word)?=)([A-Za-z0-9+/=_\-]{6,})'), r'\1<REDACTED>'),
    (re.compile(r'(fpB64=)([A-Za-z0-9+/=_\-]{12,})'), r'\1<REDACTED>'),
)

def redact_secrets(text: str, log_secrets: bool = False) -> str:
    if log_secrets or not text:
        return str(text or "")
    out = str(text)
    for pat, repl in _SECRET_PATTERNS:
        out = pat.sub(repl, out)
    return out

redact_sensitive = redact_secrets

def verify_redaction_selftest() -> bool:
    probes = (
        ('{"token":"secretTok123","fp":"abcdef0123456789","key":"mySharedKey99"}', ("secretTok123", "abcdef0123456789", "mySharedKey99")),
        ('R4:{"ver":3,"token":"qrSecret99"}', ("qrSecret99",)),
        ("token=inlineToken777 pass=inlinePass888", ("inlineToken777", "inlinePass888")),
    )
    for sample, forbidden in probes:
        red = redact_secrets(sample, log_secrets=False)
        for f in forbidden:
            if f in red:
                raise RuntimeError(f"Log redaction self-test failed: {f!r} leaked in {red!r}")
    return True

def sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()

def sha256_b64(b: bytes) -> str:
    import base64
    return base64.b64encode(hashlib.sha256(b).digest()).decode("ascii")

def configure_logging(
    *,
    level: str = "info",
    debug: bool = False,
    log_file: str | Path | None = None,
    effective_args: dict[str, Any] | None = None,
) -> logging.Logger:
    verify_redaction_selftest()
    eff_level_name = "DEBUG" if debug else str(level or "info").upper()
    eff_level = getattr(logging, eff_level_name, logging.INFO)

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        lp = Path(log_file).expanduser().resolve()
        lp.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(lp, encoding="utf-8"))

    logging.basicConfig(
        level=eff_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )
    for noisy_logger in ("fuse", "fuse.log-mixin", "asyncio"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)
    if effective_args is not None:
        serialized = redact_secrets(json.dumps(effective_args, default=str, sort_keys=True))
        log.info("Effective startup arguments: %s", serialized)
    return log

class EventLog:

    def __init__(
        self,
        path: Path | str | None = None,
        feature: str | Path = "core",
        log_secrets: bool = False,
        disk_enabled: bool = True,
        console_print: bool = True,
    ) -> None:
        verify_redaction_selftest()
        self.path = Path(path) if path is not None else None
        self.feature = "core" if isinstance(feature, Path) else str(feature or "core")
        self.log_secrets = bool(log_secrets)
        self.disk_enabled = bool(disk_enabled and self.path is not None)
        self.console_print = bool(console_print)
        self._t0 = time.monotonic()
        self.recent_lines: collections.deque[str] = collections.deque(maxlen=400)
        self._f = None
        self._w = None
        self._jf = None
        if self.disk_enabled and self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._f = open(self.path, "w", newline="", encoding="utf-8")
            with contextlib.suppress(OSError):
                os.chmod(self.path, 0o600)
            self._w = csv.writer(self._f, quoting=csv.QUOTE_ALL)
            self._w.writerow(["UTC", "Session", "Event", "Detail"])
            self._f.flush()
            self._jsonl_path = self.path.parent / "events.jsonl"
            self._jf = open(self._jsonl_path, "a", encoding="utf-8")
            with contextlib.suppress(OSError):
                os.chmod(self._jsonl_path, 0o600)
            sys.stderr.write(
                f"[WARN] Disk event logging enabled (--debug): {self.path.parent}/events.csv and events.jsonl "
                "record diagnostic session metadata (including LAN/USB IPs and device names). "
                "Do not publish unredacted debug bundles.\n"
            )

    def log(
        self,
        event: str,
        detail: str = "",
        feature: str | None = None,
        *,
        conn_id: int | str | None = None,
        role: str | None = None,
        service_id: int | str | None = None,
        session_id: int | str | None = None,
        phase: str | None = None,
    ) -> None:
        now = datetime.datetime.now(datetime.UTC)
        safe_detail = redact_secrets(detail, self.log_secrets)
        if self._f is not None and not self._f.closed:
            with contextlib.suppress(ValueError, OSError):
                self._w.writerow([now.isoformat(), "samyuj", event, safe_detail])
                self._f.flush()
        if self._jf is not None and not self._jf.closed:
            with contextlib.suppress(Exception):
                rec = {
                    "utc": now.isoformat(),
                    "elapsed_s": round(time.monotonic() - self._t0, 4),
                    "event": event,
                    "feature": feature or self.feature,
                    "conn_id": conn_id,
                    "role": role,
                    "service_id": service_id,
                    "session_id": session_id,
                    "phase": phase,
                    "detail": safe_detail,
                }
                self._jf.write(json.dumps(rec, ensure_ascii=False) + "\n")
                self._jf.flush()
        feat = feature or self.feature
        log.debug("[%s] %s %s", feat, event, safe_detail)
        stamp = now.astimezone().strftime("%H:%M:%S.%f")[:-3]
        self.recent_lines.append(f"[{stamp}] [{feat}] {event} {safe_detail[:240]}")
        if self.console_print:
            print(f"[{stamp}] {event} {safe_detail[:400]}")

    def close(self) -> None:
        if self._f is not None and not self._f.closed:
            self._f.close()
        if self._jf is not None and not self._jf.closed:
            self._jf.close()

class ConnLog:

    def __init__(
        self,
        path: Path | str | None = None,
        log_secrets: bool = False,
        disk_enabled: bool = True,
    ) -> None:
        self.log_secrets = bool(log_secrets)
        self.disk_enabled = bool(disk_enabled and path is not None)
        self._f = None
        if self.disk_enabled and path is not None:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._f = open(p, "a", encoding="utf-8")
            with contextlib.suppress(OSError):
                os.chmod(p, 0o600)

    def line(self, text: str) -> None:
        if self._f is not None and not self._f.closed:
            now = datetime.datetime.now(datetime.UTC).isoformat()
            safe = redact_secrets(text, self.log_secrets)
            with contextlib.suppress(ValueError, OSError):
                self._f.write(f"{now}  {safe}\n")
                self._f.flush()

    def close(self) -> None:
        if self._f is not None and not self._f.closed:
            self._f.close()
