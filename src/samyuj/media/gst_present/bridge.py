from __future__ import annotations

import collections
import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

_PRESENTER_PATH = Path(__file__).resolve().parent / "presenter.py"

def presenter_script() -> Path:
    return _PRESENTER_PATH

def presenter_available() -> bool:
    return _PRESENTER_PATH.is_file()

class PresenterBridge:
    def __init__(self, title: str = "Samyuj", sink: str = "auto",
                 decoder: str = "auto", width: int = 412, height: int = 860,
                 stats_every: float = 2.0, mailbox: int = 12,
                 ready_timeout: float = 6.0,
                 on_stats=None, on_death=None, on_drop=None,
                 python_exe: str | None = None,
                 presenter_path: str | None = None) -> None:
        self._script = Path(presenter_path) if presenter_path else _PRESENTER_PATH
        self._append_aud = (presenter_path is None)
        self.title = title
        self.sink = sink
        self.decoder = decoder
        self.width = int(width)
        self.height = int(height)
        self.stats_every = float(stats_every)
        self.ready_timeout = float(ready_timeout)
        self.on_stats = on_stats if callable(on_stats) else None
        self.on_death = on_death if callable(on_death) else None
        self.on_drop = on_drop if callable(on_drop) else None
        self.python_exe = python_exe or sys.executable or "python3"

        self._box: collections.deque = collections.deque(maxlen=max(4, int(mailbox)))
        self._box_lock = threading.Lock()
        self._wakeup = threading.Event()
        self._outq: "queue.Queue[bytes | None]" = queue.Queue()
        self._proc: subprocess.Popen | None = None
        self._writer: threading.Thread | None = None
        self._reader: threading.Thread | None = None
        self._ready = threading.Event()
        self._dead = threading.Event()
        self._death_fired = False

        self.written = 0
        self.drops = 0
        self.write_errors = 0
        self.stats: dict = {}
        self.last_error: str = ""
        self.stderr_tail: collections.deque = collections.deque(maxlen=24)
        self.sink_chosen: str | None = None
        self.decoder_chosen: str | None = None
        self.started_mono: float = 0.0

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc is not None else None

    @property
    def alive(self) -> bool:
        if self._dead.is_set():
            return False
        p = self._proc
        return bool(p is not None and p.poll() is None)

    def start(self) -> bool:
        if not self._script.is_file():
            self.last_error = f"presenter script missing: {self._script}"
            return False
        cmd = [self.python_exe, str(self._script),
               "--title", self.title, "--sink", str(self.sink),
               "--decoder", str(self.decoder),
               "--width", str(self.width), "--height", str(self.height),
               "--stats-every", f"{self.stats_every:.2f}"]
        try:
            self._proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, bufsize=0,
                cwd=str(self._script.parent))
        except Exception as exc:
            self.last_error = f"spawn failed: {exc!r}"
            return False
        self.started_mono = time.monotonic()
        self._writer = threading.Thread(target=self._writer_loop,
                                        name="sc-present-w", daemon=True)
        self._reader = threading.Thread(target=self._reader_loop,
                                        name="sc-present-r", daemon=True)
        self._writer.start()
        self._reader.start()
        if not self._ready.wait(self.ready_timeout):
            self.last_error = self.last_error or f"no ready line within {self.ready_timeout}s"
            self.stop(reason="start-timeout")
            return False
        return self.alive

    def stop(self, reason: str = "") -> None:
        self._dead.set()
        self._wakeup.set()
        self._outq.put(None)
        p = self._proc
        if p is not None and p.poll() is None:
            try:
                if p.stdin is not None:
                    p.stdin.close()
            except Exception:
                pass
            try:
                p.wait(timeout=2.0)
            except Exception:
                with suppress_all():
                    p.terminate()
                with suppress_all():
                    p.wait(timeout=1.0)
        for t in (self._writer, self._reader):
            if t is not None and t.is_alive():
                t.join(timeout=0.5)

    def write(self, data: bytes) -> bool:
        if not self.alive or not data:
            return False
        dropped_now = False
        with self._box_lock:
            if len(self._box) >= self._box.maxlen:
                self.drops += 1
                dropped_now = True
            self._box.append(data)
        if dropped_now and self.on_drop is not None:
            with suppress_all():
                self.on_drop()
        self._wakeup.set()
        return True

    def _writer_loop(self) -> None:
        stdin = self._proc.stdin if self._proc is not None else None
        if stdin is None:
            return
        while not self._dead.is_set():
            self._wakeup.wait(0.05)
            while True:
                with self._box_lock:
                    if not self._box:
                        self._wakeup.clear()
                        break
                    chunk = self._box.popleft()
                try:
                    out_bytes = (
                        chunk + b"\x00\x00\x00\x01\x09\xf0"
                        if (getattr(self, "_append_aud", False)
                            and not chunk.endswith(b"\x00\x00\x00\x01\x09\xf0"))
                        else chunk
                    )
                    stdin.write(out_bytes)
                    stdin.flush()
                    self.written += 1
                except Exception as exc:
                    self.write_errors += 1
                    self.last_error = f"stdin write failed: {exc!r}"
                    self._mark_dead()
                    return
            if self._proc is not None and self._proc.poll() is not None:
                self._mark_dead()
                return

    def _reader_loop(self) -> None:
        p = self._proc
        if p is None:
            return
        def _pump_stdout():
            try:
                for raw in iter(p.stdout.readline, b""):
                    line = raw.decode("utf-8", "replace").strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except Exception:
                        continue
                    t = msg.get("t")
                    if t == "ready":
                        self.sink_chosen = msg.get("sink")
                        self.decoder_chosen = msg.get("decoder")
                        self.stats["ready"] = msg
                        self._ready.set()
                    elif t == "stats":
                        self.stats.update({k: v for k, v in msg.items() if k != "t"})
                        if self.on_stats is not None:
                            with suppress_all():
                                self.on_stats(dict(self.stats))
                    elif t == "error":
                        self.last_error = str(msg.get("msg") or "child error")
                        self.stats["error"] = msg
                    elif t == "exit":
                        self.stats["exit"] = msg
            except Exception:
                pass
            finally:
                self._ready.set()
                self._mark_dead()

        def _pump_stderr():
            try:
                for raw in iter(p.stderr.readline, b""):
                    self.stderr_tail.append(raw.decode("utf-8", "replace").rstrip()[:300])
            except Exception:
                pass

        t_out = threading.Thread(target=_pump_stdout, name="sc-present-out", daemon=True)
        t_err = threading.Thread(target=_pump_stderr, name="sc-present-err", daemon=True)
        t_out.start()
        t_err.start()
        rc = p.wait()
        self.stats["exit_code"] = rc
        t_out.join(timeout=1.0)
        self._ready.set()
        self._mark_dead()

    def _mark_dead(self) -> None:
        self._dead.set()
        self._wakeup.set()
        if not self._death_fired:
            self._death_fired = True
            if self.on_death is not None:
                with suppress_all():
                    self.on_death()

class suppress_all:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return exc_type is not None and issubclass(exc_type, Exception)
