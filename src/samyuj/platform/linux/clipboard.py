from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

def _sha(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", "surrogatepass")).hexdigest()

class ClipboardSyncManager:

    def __init__(self, harness, enabled: bool = True, echo_ttl_s: float = 15.0,
                 poll_interval_s: float = 3.0) -> None:
        self.h = harness
        self.enabled = bool(enabled)
        self.echo_ttl_s = float(echo_ttl_s)
        self.poll_interval_s = float(poll_interval_s)

        self._recent_to_phone: dict[str, float] = {}
        self._recent_from_phone: dict[str, float] = {}

        self._gtk_getter = None
        self._gtk_setter = None
        self._gtk_primary_getter = None
        self._gtk_primary_setter = None
        self._backend = "none"
        self._cli_backend = "none"
        self._detect_cli_backend()

        self._last_pc_hash: str | None = None
        self._last_phone_hash: str | None = None
        self._watch_task: asyncio.Task | None = None
        self._safety_task: asyncio.Task | None = None
        self._wl_watch_proc = None
        self._wl_watch_deliveries: int = 0
        self._push_lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

        self.stats = {
            "pc_to_phone": 0,
            "phone_to_pc": 0,
            "echoes_suppressed": 0,
            "push_failures": 0,
            "last_direction": "",
            "last_text_preview": "",
            "last_sync_mono": 0.0,
        }

    def _detect_cli_backend(self) -> str:
        wayland_display = os.environ.get("WAYLAND_DISPLAY") or ""
        wayland_ok = False
        if (wayland_display
                and not getattr(self, "_wl_broken", False)
                and shutil.which("wl-paste")
                and shutil.which("wl-copy")):
            xdg_rt = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
            wl_sock = Path(wayland_display) if os.path.isabs(wayland_display) else (Path(xdg_rt) / wayland_display)
            wayland_ok = wl_sock.exists()
        if wayland_ok:
            self._cli_backend = "wl"
        elif bool(os.environ.get("DISPLAY")) and shutil.which("xclip"):
            self._cli_backend = "x11-xclip"
        elif bool(os.environ.get("DISPLAY")) and shutil.which("xsel"):
            self._cli_backend = "x11-xsel"
        else:
            self._cli_backend = "none"
        if self._gtk_getter is not None:
            self._backend = "gtk"
        else:
            self._backend = self._cli_backend
        return self._cli_backend

    def register_gtk_backend(self, getter, setter) -> None:
        self._gtk_getter = getter
        self._gtk_setter = setter
        self._backend = "gtk"
        self._detect_cli_backend()
        self._log("CLIP_SYNC_BACKEND",
                  f"registered native GTK clipboard backend (cli_fallback={self._cli_backend})")

    def register_gtk_primary_backend(self, getter, setter) -> None:
        self._gtk_primary_getter = getter
        self._gtk_primary_setter = setter
        self._log("CLIP_SYNC_BACKEND",
                  "registered native GTK PRIMARY-selection backend (sync gated by "
                  "'clipboard_sync_primary' setting)")

    @property
    def backend(self) -> str:
        if self._gtk_getter is not None:
            return "gtk"
        self._detect_cli_backend()
        return self._backend

    def _log(self, event: str, detail: str) -> None:
        ev = getattr(self.h, "events", None)
        if ev is not None:
            with contextlib.suppress(Exception):
                ev.log(event, detail, feature="clipboard", service_id=9)

    def read_cli_clipboard_sync(self) -> str | None:
        cli = self._cli_backend if self._cli_backend != "none" else self._detect_cli_backend()
        try:
            if cli == "wl":
                r = subprocess.run(["wl-paste", "--no-newline"],
                                   capture_output=True, text=True, timeout=1.5)
                if r.returncode == 0 and isinstance(r.stdout, str):
                    return r.stdout
                err_low = (r.stderr or "").lower()
                if "doesn't support" in err_low or "not supported" in err_low or "no compositor" in err_low:
                    self._wl_broken = True
                    self._detect_cli_backend()
                if bool(os.environ.get("DISPLAY")) and shutil.which("xclip"):
                    r2 = subprocess.run(["xclip", "-selection", "clipboard", "-o"],
                                        capture_output=True, text=True, timeout=1.5)
                    if r2.returncode == 0:
                        return r2.stdout
            elif cli == "x11-xclip":
                r = subprocess.run(["xclip", "-selection", "clipboard", "-o"],
                                   capture_output=True, text=True, timeout=1.5)
                return r.stdout if r.returncode == 0 else None
            elif cli == "x11-xsel":
                r = subprocess.run(["xsel", "--clipboard", "--output"],
                                   capture_output=True, text=True, timeout=1.5)
                return r.stdout if r.returncode == 0 else None
        except Exception:
            return None
        return None

    def read_pc_clipboard_sync(self) -> str | None:
        if self._gtk_getter is not None:
            with contextlib.suppress(Exception):
                txt = self._gtk_getter()
                if isinstance(txt, str) and txt != "":
                    return txt
            if threading.current_thread() is not threading.main_thread():
                cli_txt = self.read_cli_clipboard_sync()
                if isinstance(cli_txt, str):
                    return cli_txt
            return None
        return self.read_cli_clipboard_sync()

    def write_cli_clipboard_sync(self, text: str) -> bool:
        cli = self._cli_backend if self._cli_backend != "none" else self._detect_cli_backend()
        try:
            if cli == "wl":
                p = subprocess.run(["wl-copy", "--", text],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2.0)
                return p.returncode == 0
            if cli == "x11-xclip":
                p = subprocess.run(["xclip", "-selection", "clipboard", "-in"],
                                   input=text.encode("utf-8"),
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2.0)
                return p.returncode == 0
            if cli == "x11-xsel":
                p = subprocess.run(["xsel", "--clipboard", "--input"],
                                   input=text.encode("utf-8"),
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2.0)
                return p.returncode == 0
        except Exception:
            return False
        return False

    def write_pc_clipboard_sync(self, text: str) -> bool:
        if self._gtk_setter is not None:
            try:
                ok = bool(self._gtk_setter(text))
                if ok:
                    return True
            except Exception:
                pass
        return self.write_cli_clipboard_sync(text)

    def write_pc_primary_sync(self, text: str) -> bool:
        if self._gtk_primary_setter is not None:
            try:
                if bool(self._gtk_primary_setter(text)):
                    return True
            except Exception:
                pass
        try:
            if bool(os.environ.get("DISPLAY")) and shutil.which("xclip"):
                p = subprocess.run(["xclip", "-selection", "primary", "-in"],
                                   input=text.encode("utf-8"),
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2.0)
                return p.returncode == 0
            if bool(os.environ.get("DISPLAY")) and shutil.which("xsel"):
                p = subprocess.run(["xsel", "--primary", "--input"],
                                   input=text.encode("utf-8"),
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2.0)
                return p.returncode == 0
        except Exception:
            pass
        return False

    def watch_method(self) -> str:
        with contextlib.suppress(Exception):
            m = str((getattr(self.h, "user_settings", {}) or {})
                    .get("clipboard_watch_method") or "").lower()
            if m in ("interval", "event"):
                return m
        if self._cli_backend == "wl" or bool(os.environ.get("WAYLAND_DISPLAY")):
            return "event"
        return "interval"

    def primary_sync_enabled(self) -> bool:
        with contextlib.suppress(Exception):
            return bool((getattr(self.h, "user_settings", {}) or {})
                        .get("clipboard_sync_primary", False))
        return False

    def _prune(self, table: dict[str, float], now: float) -> None:
        for k in [k for k, ts in table.items() if now - ts > self.echo_ttl_s]:
            table.pop(k, None)

    def _note_pushed_to_phone(self, text: str) -> None:
        now = time.monotonic()
        self._prune(self._recent_to_phone, now)
        self._recent_to_phone[_sha(text)] = now

    def _note_written_from_phone(self, text: str) -> None:
        now = time.monotonic()
        self._prune(self._recent_from_phone, now)
        self._recent_from_phone[_sha(text)] = now

    def is_phone_echo(self, text: str) -> bool:
        now = time.monotonic()
        self._prune(self._recent_to_phone, now)
        return _sha(text) in self._recent_to_phone

    def is_pc_writeback_echo(self, text: str) -> bool:
        now = time.monotonic()
        self._prune(self._recent_from_phone, now)
        return _sha(text) in self._recent_from_phone

    async def on_phone_clipboard(self, text: str, kind: str = "update") -> bool:
        if not isinstance(text, str) or text == "":
            return False
        hsh = _sha(text)
        self._last_phone_hash = hsh
        self.h.pc_clipboard_text = text
        if self.is_phone_echo(text):
            self.stats["echoes_suppressed"] += 1
            self._log("CLIP_SYNC_ECHO_SUPPRESSED",
                      f"phone echoed our own push ({kind}, {len(text)} chars) — dropped")
            return False
        if not self.enabled:
            self._log("CLIP_SYNC_DISABLED_RECV",
                      f"auto-sync OFF — phone {kind} ({len(text)} chars) not written to PC clipboard")
            return False
        if self._gtk_setter is not None:
            ok = self.write_pc_clipboard_sync(text)
        else:
            ok = await asyncio.to_thread(self.write_pc_clipboard_sync, text)
        if ok:
            self._note_written_from_phone(text)
            self._last_pc_hash = hsh
            self.stats["phone_to_pc"] += 1
            self.stats["last_direction"] = "phone->PC"
            self.stats["last_text_preview"] = text[:60]
            self.stats["last_sync_mono"] = time.time()
            if self.primary_sync_enabled():
                with contextlib.suppress(Exception):
                    if self._gtk_primary_setter is not None:
                        self.write_pc_primary_sync(text)
                    else:
                        await asyncio.to_thread(self.write_pc_primary_sync, text)
            self._log("CLIP_SYNC_PHONE_TO_PC",
                      f"applied {len(text)} chars from phone ({kind}) to Linux clipboard "
                      f"via {self.backend}")
        else:
            self._log("CLIP_SYNC_WRITE_FAIL",
                      f"could not write to Linux clipboard (backend={self.backend}); "
                      f"text kept in state for CLIP_PULL answers")
        return ok

    async def push_pc_text_to_phone(self, text: str, origin: str = "auto") -> bool:
        if not isinstance(text, str) or text == "":
            return False
        hsh = _sha(text)
        self.h.pc_clipboard_text = text
        c = self.h._live_ws()
        if c is None:
            if self._last_pc_hash == hsh:
                self._last_pc_hash = None
            return False
        async with self._push_lock:
            self._note_pushed_to_phone(text)
            self.h.pc_clipboard_text = text
            rec = await self.h.request("clipboard", c, arg=text)
            ok = bool(rec.get("ok"))
            if ok:
                self._last_pc_hash = hsh
                self.stats["pc_to_phone"] += 1
                self.stats["last_direction"] = "PC->phone"
                self.stats["last_text_preview"] = text[:60]
                self.stats["last_sync_mono"] = time.time()
                self._log("CLIP_SYNC_PC_TO_PHONE",
                          f"pushed {len(text)} chars to phone ({origin})")
            else:
                if self._last_pc_hash == hsh:
                    self._last_pc_hash = None
                self.stats["push_failures"] += 1
                self._log("CLIP_SYNC_PUSH_FAIL", f"origin={origin} detail={rec.get('detail')}")
            return ok

    def _resolve_loop(self) -> asyncio.AbstractEventLoop | None:
        with contextlib.suppress(RuntimeError):
            loop = asyncio.get_running_loop()
            self._loop = loop
            return loop
        for cand in (
            self._loop,
            getattr(self.h, "_control_loop", None),
        ):
            if cand is not None and not cand.is_closed():
                return cand
        with contextlib.suppress(Exception):
            ev = asyncio.get_event_loop_policy().get_event_loop()
            if ev is not None and not ev.is_closed():
                return ev
        return None

    def _schedule_coro(self, coro) -> bool:
        loop = self._resolve_loop()
        if loop is None or loop.is_closed():
            with contextlib.suppress(Exception):
                coro.close()
            return False
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        try:
            if running is loop:
                loop.create_task(coro)
            else:
                loop.call_soon_threadsafe(lambda: loop.create_task(coro))
            return True
        except Exception:
            with contextlib.suppress(Exception):
                coro.close()
            return False

    def on_pc_clipboard_polled(self, text: str | None) -> None:
        if not isinstance(text, str) or text == "":
            return
        self.h.pc_clipboard_text = text
        if not self.enabled:
            return
        hsh = _sha(text)
        if hsh == self._last_pc_hash:
            return
        if self.is_pc_writeback_echo(text):
            self._last_pc_hash = hsh
            self.stats["echoes_suppressed"] += 1
            self._log("CLIP_SYNC_WRITEBACK_SUPPRESSED",
                      f"local clipboard change was our own phone write ({len(text)} chars) — dropped")
            return
        if self.h._live_ws() is None:
            return
        self._last_pc_hash = hsh
        if not self._schedule_coro(self.push_pc_text_to_phone(text, origin="pc-watcher")):
            self._last_pc_hash = None

    async def _bg_poll_loop(self, interval_override: float | None = None) -> None:
        interval = float(interval_override or self.poll_interval_s)
        with contextlib.suppress(asyncio.CancelledError):
            while not (getattr(self.h, "_stop_evt", None) and self.h._stop_evt.is_set()):
                await asyncio.sleep(interval)
                try:
                    if not self.enabled or self.h._live_ws() is None:
                        continue
                    if self._gtk_getter is not None:
                        if self._cli_backend == "none":
                            continue
                        text = await asyncio.to_thread(self.read_cli_clipboard_sync)
                    else:
                        text = await asyncio.to_thread(self.read_pc_clipboard_sync)
                    if isinstance(text, str) and text:
                        self.on_pc_clipboard_polled(text)
                except Exception:
                    pass

    async def _wl_watch_loop(self) -> None:
        fails = 0
        delim_cmd = ("import sys;"
                     "d=sys.stdin.buffer.read();"
                     "d=d[:-1] if d.endswith(b'\\n') else d;"
                     "sys.stdout.buffer.write(d+b'\\x00');"
                     "sys.stdout.buffer.flush()")
        with contextlib.suppress(asyncio.CancelledError):
            while not (getattr(self.h, "_stop_evt", None) and self.h._stop_evt.is_set()):
                proc = None
                spawn_mono = time.monotonic()
                try:
                    proc = await asyncio.create_subprocess_exec(
                        "wl-paste", "--no-newline", "--watch",
                        "python3", "-c", delim_cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.DEVNULL)
                    self._wl_watch_proc = proc
                    if fails == 0:
                        self._log("CLIP_SYNC_WL_WATCH_UP",
                                  f"wl-paste --watch event stream started (pid={proc.pid}) — "
                                  f"zero-poll clipboard sync active (10 s safety-net poller "
                                  f"runs alongside)")
                    buf = b""
                    while True:
                        chunk = await proc.stdout.read(65536)
                        if not chunk:
                            break
                        buf += chunk
                        while b"\0" in buf:
                            item, buf = buf.split(b"\0", 1)
                            txt = item.decode("utf-8", "replace")
                            if txt:
                                fails = 0
                                self._wl_watch_deliveries += 1
                                if (self._wl_watch_deliveries == 1
                                        or self._wl_watch_deliveries % 25 == 0):
                                    self._log("CLIP_SYNC_WL_WATCH_DATA",
                                              f"delivery #{self._wl_watch_deliveries}: "
                                              f"{len(txt)} chars from the event stream")
                                self.on_pc_clipboard_polled(txt)
                    rc = await proc.wait()
                    if (time.monotonic() - spawn_mono) < 1.5 and rc != 0:
                        self._wl_broken = True
                        self._detect_cli_backend()
                        fails = 5
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._log("CLIP_SYNC_WL_WATCH_ERR", repr(exc)[:200])
                finally:
                    self._wl_watch_proc = None
                    if proc is not None:
                        with contextlib.suppress(Exception):
                            proc.kill()
                fails += 1
                if fails >= 5:
                    safety_alive = bool(self._safety_task is not None
                                        and not self._safety_task.done())
                    if safety_alive:
                        self._log("CLIP_SYNC_WL_WATCH_RETIRED",
                                  "wl-paste --watch failed 5x — retiring it; the 10 s "
                                  "safety-net poller keeps clipboard sync alive "
                                  "(install/upgrade wl-clipboard for instant sync)")
                        return
                    self._log("CLIP_SYNC_WL_WATCH_FALLBACK",
                              "wl-paste --watch failed 5x — falling back to the interval "
                              "CLI poller (clipboard sync stays alive)")
                    self._watch_task = None
                    with contextlib.suppress(RuntimeError):
                        loop = asyncio.get_running_loop()
                        self._watch_task = loop.create_task(self._bg_poll_loop())
                    return
                await asyncio.sleep(min(8.0, 2.0 ** min(fails, 3)))

    def start_background_poller(self) -> None:
        if self._watch_task is not None and not self._watch_task.done():
            return
        with contextlib.suppress(RuntimeError):
            loop = asyncio.get_running_loop()
            self._loop = loop
            if self.watch_method() == "event":
                if self._cli_backend == "wl" and shutil.which("wl-paste"):
                    self._watch_task = loop.create_task(self._wl_watch_loop())
                    if self._safety_task is None or self._safety_task.done():
                        self._safety_task = loop.create_task(
                            self._bg_poll_loop(interval_override=10.0))
                    return
                self._log("CLIP_SYNC_WATCH_EVENT",
                          "event mode: no wl-clipboard tools found — on X11 the GTK "
                          "owner-change signal is the watcher (interval CLI poller disabled); "
                          "on Wayland install wl-clipboard for zero-poll watching")
                if self._cli_backend != "wl":
                    if self._gtk_getter is None:
                        self._watch_task = loop.create_task(self._bg_poll_loop())
                return
            self._watch_task = loop.create_task(self._bg_poll_loop())

    def apply_watch_method(self) -> None:
        for attr in ("_watch_task", "_safety_task"):
            task = getattr(self, attr, None)
            setattr(self, attr, None)
            if task is not None and not task.done():
                task.cancel()
        proc = self._wl_watch_proc
        if proc is not None:
            with contextlib.suppress(Exception):
                proc.kill()
            self._wl_watch_proc = None
        self.start_background_poller()
        self._log("CLIP_SYNC_WATCH_METHOD",
                  f"clipboard watcher reconfigured -> method={self.watch_method()}")

    async def initial_sync(self, conn=None) -> None:
        with contextlib.suppress(RuntimeError):
            self._loop = asyncio.get_running_loop()
        c = conn or self.h._live_ws()
        if c is None:
            return
        self._last_pc_hash = None
        with contextlib.suppress(Exception):
            from samyuj.protocol.services.clipboard import (
                build_clipboard_global_enable,
                build_clipboard_nego,
            )
            await self.h._send_ws_text(
                c, build_clipboard_global_enable(self.h, bool(self.enabled)),
                "CLIP_GLOBAL_ENABLE_SENT(auto,post-pairing)")
            await self.h._send_ws_text(
                c, build_clipboard_nego(self.h, 3),
                "CLIP_NEGO_SENT(auto,post-pairing)")
        await asyncio.sleep(0.45)
        if c.get("closed"):
            return
        with contextlib.suppress(Exception):
            await self.h.request("clipboard-pull", c)
        await asyncio.sleep(0.45)
        if c.get("closed") or not self.enabled:
            return
        text = None
        if self._gtk_getter is not None:
            text = self.read_pc_clipboard_sync()
            if not text and self._cli_backend != "none":
                text = await asyncio.to_thread(self.read_cli_clipboard_sync)
        else:
            text = await asyncio.to_thread(self.read_pc_clipboard_sync)
        if not text:
            pc_cached = str(getattr(self.h, "pc_clipboard_text", "") or "")
            if pc_cached and pc_cached != "Samyuj PC Clipboard":
                text = pc_cached
        if isinstance(text, str) and text and _sha(text) != self._last_phone_hash:
            await self.push_pc_text_to_phone(text, origin="initial-sync")

    def set_enabled(self, on: bool, persist: bool = True) -> dict:
        self.enabled = bool(on)
        if persist:
            self._save_pref()
        self._log("CLIP_SYNC_TOGGLE",
                  f"automatic clipboard sync {'ENABLED' if self.enabled else 'DISABLED'}")
        c = self.h._live_ws() if hasattr(self.h, "_live_ws") else None
        if c is not None:
            with contextlib.suppress(Exception):
                from samyuj.protocol.services.clipboard import build_clipboard_global_enable
                self._schedule_coro(
                    self.h._send_ws_text(
                        c, build_clipboard_global_enable(self.h, self.enabled),
                        f"CLIP_GLOBAL_{'ENABLE' if self.enabled else 'DISABLE'}_SENT")
                )
        return self.status()

    def _pref_path(self) -> Path:
        return Path(getattr(self.h, "state_dir", ".")) / "clipboard_prefs.json"

    def _save_pref(self) -> None:
        with contextlib.suppress(OSError):
            p = self._pref_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(
                json.dumps({"auto_sync": bool(self.enabled), "auto_sync_r28_migrated": True}),
                encoding="utf-8",
            )
            os.chmod(p, 0o600)

    def load_pref(self) -> None:
        with contextlib.suppress(OSError, json.JSONDecodeError, ValueError, TypeError):
            p = self._pref_path()
            if p.is_file():
                d = json.loads(p.read_text(encoding="utf-8"))
                if not d.get("auto_sync_r28_migrated") and not d.get("auto_sync", True):
                    self.enabled = True
                    self._save_pref()
                else:
                    self.enabled = bool(d.get("auto_sync", True))

    def status(self) -> dict:
        return {
            "enabled": bool(self.enabled),
            "backend": self.backend,
            **self.stats,
        }
