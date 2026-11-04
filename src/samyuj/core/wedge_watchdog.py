from __future__ import annotations

import faulthandler
import glob
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

class WedgeWatchdog:

    EXIT_CODE = 70

    def __init__(self, stall_limit_s: float = 20.0,
                 check_interval_s: float = 5.0) -> None:
        self._limit = float(stall_limit_s)
        self._interval = float(check_interval_s)
        self._last_tick = time.monotonic()
        self._stop = threading.Event()
        self._done = False
        self._thread: threading.Thread | None = None
        self.fuse_mount_glob: str | None = None
        self.dump_path: Path | None = None

    def tick(self) -> None:
        self._last_tick = time.monotonic()

    def start(self, fuse_mount_glob: str | None = None,
              dump_path: Path | str | None = None) -> None:
        self.fuse_mount_glob = fuse_mount_glob or None
        self.dump_path = Path(dump_path) if dump_path else None
        self._last_tick = time.monotonic()
        self._thread = threading.Thread(
            target=self._run, name="samyuj-wedge-watchdog", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            stalled = time.monotonic() - self._last_tick
            if stalled < self._limit:
                continue
            self._emergency(stalled)
            return

    def _emergency(self, stalled: float) -> None:
        if self._done:
            return
        self._done = True
        msg = (f"SAMYUJ MAIN LOOP WEDGED {stalled:.0f}s - dumping all thread "
               f"stacks, releasing FUSE mounts, exiting ({self.EXIT_CODE})")
        try:
            print(msg, file=sys.stderr, flush=True)
        except Exception:
            pass
        if self.dump_path is not None:
            try:
                self.dump_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.dump_path, "a", encoding="utf-8") as fh:
                    fh.write(f"=== {time.strftime('%Y-%m-%d %H:%M:%S')} {msg} ===\n")
                    faulthandler.dump_traceback(file=fh, all_threads=True)
                    fh.flush()
            except Exception:
                pass
        try:
            faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
        except Exception:
            pass
        self._release_fuse()
        os._exit(self.EXIT_CODE)

    def _release_fuse(self) -> None:
        if not self.fuse_mount_glob:
            return
        for mp in glob.glob(self.fuse_mount_glob):
            for tool in ("fusermount3", "fusermount"):
                if not shutil.which(tool):
                    continue
                try:
                    subprocess.run([tool, "-u", "-z", mp], timeout=5,
                                   stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
                except Exception:
                    pass
                break

__all__ = ["WedgeWatchdog"]
