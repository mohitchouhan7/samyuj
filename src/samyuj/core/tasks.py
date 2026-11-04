from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import ctypes
import os
import threading
import time
from typing import Any, Coroutine

class TaskSupervisor:

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[Any]] = set()

    def spawn(self, coro: Coroutine[Any, Any, Any], *, name: str | None = None) -> asyncio.Task[Any]:
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def cancel_all(self) -> None:
        pending = [t for t in list(self._tasks) if not t.done()]
        for t in pending:
            t.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._tasks.clear()

class MobileDesktopServiceThread:

    def __init__(self, name: str = "sc-mobiledesktop-media") -> None:
        self.name = str(name)
        self._lock = threading.Lock()
        self._ready_evt = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self.ident: int | None = None
        self.scaler_ident: int | None = None
        self._scaler_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="sc-mobiledesktop-scaler"
        )
        self._ring_buffers: dict[tuple[str, int, int], list[bytearray]] = {}
        self._ring_idx: dict[tuple[str, int, int], int] = {}
        self.loop_lag_ms: float = 0.0
        self.worst_loop_lag_ms: float = 0.0
        self.last_heartbeat_mono: float = time.monotonic()
        self.stats: dict = {
            "started": False,
            "thread_name": self.name,
            "thread_ident": None,
            "scaler_thread_ident": None,
            "service_msgs_dispatched": 0,
            "media_sessions_negotiated": 0,
            "candidates_signaled": 0,
            "frames_pumped": 0,
            "frames_converted_bgrx": 0,
            "input_events_dispatched": 0,
            "pli_requests_dispatched": 0,
            "loop_lag_ms": 0.0,
            "worst_loop_lag_ms": 0.0,
        }

    @property
    def loop(self) -> asyncio.AbstractEventLoop | None:
        return self._loop

    def start(self) -> "MobileDesktopServiceThread":
        if self.is_alive():
            return self
        with self._lock:
            if self.is_alive():
                return self
            self._ready_evt.clear()
            th = threading.Thread(target=self._thread_main, name=self.name, daemon=True)
            self._thread = th
            th.start()
        self._ready_evt.wait(timeout=3.0)
        return self

    def _thread_main(self) -> None:
        with contextlib.suppress(Exception):
            os.nice(-5)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self.ident = threading.get_ident()
        self.stats["started"] = True
        self.stats["thread_ident"] = self.ident
        lag_task = loop.create_task(self._lag_probe())
        self._ready_evt.set()
        try:
            loop.run_forever()
        finally:
            lag_task.cancel()
            with contextlib.suppress(Exception):
                pending = asyncio.all_tasks(loop)
                for t in pending:
                    t.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            with contextlib.suppress(Exception):
                loop.close()
            self._loop = None
            self.stats["started"] = False

    async def _lag_probe(self) -> None:
        try:
            while True:
                t0 = time.monotonic()
                await asyncio.sleep(0.04)
                now = time.monotonic()
                lag = max(0.0, (now - t0 - 0.04) * 1000.0)
                self.loop_lag_ms = lag
                self.last_heartbeat_mono = now
                if lag > self.worst_loop_lag_ms:
                    self.worst_loop_lag_ms = lag
                self.stats["loop_lag_ms"] = round(self.loop_lag_ms, 2)
                self.stats["worst_loop_lag_ms"] = round(self.worst_loop_lag_ms, 2)
        except asyncio.CancelledError:
            pass

    def is_alive(self) -> bool:
        return bool(
            self._thread is not None
            and self._thread.is_alive()
            and self._loop is not None
            and self._loop.is_running()
        )

    def is_current_thread(self) -> bool:
        return bool(self.ident is not None and threading.get_ident() == self.ident)

    def is_current_loop(self) -> bool:
        if self._loop is None:
            return False
        try:
            return asyncio.get_running_loop() is self._loop
        except RuntimeError:
            return False

    async def run_coroutine(self, coro):
        self.start()
        if self.is_current_loop() or self._loop is None or not self._loop.is_running():
            return await coro
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return await asyncio.wrap_future(fut)

    def submit_coroutine(self, coro):
        self.start()
        if self._loop is not None and self._loop.is_running():
            return asyncio.run_coroutine_threadsafe(coro, self._loop)
        return None

    def call_soon(self, fn, *args) -> None:
        self.start()
        if self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(fn, *args)

    def prepare_frame_bgrx_sync(self, av_frame, tw: int, th: int,
                                stream_key: str = "desktop") -> dict | None:
        if av_frame is None or not hasattr(av_frame, "reformat"):
            return None
        try:
            from samyuj.media.frame_hub import _LIBSC_FAST, store_prepared_frame_bgrx as _store_prepared_frame_bgrx
            tid = threading.get_ident()
            self.scaler_ident = tid
            self.stats["scaler_thread_ident"] = tid
            fw = int(getattr(av_frame, "width", 0) or 0)
            fh = int(getattr(av_frame, "height", 0) or 0)
            if fw <= 0 or fh <= 0:
                return None
            tw = max(2, min(fw, int(tw or fw)))
            th = max(2, min(fh, int(th or fh)))
            if tw == fw and th == fh:
                rgb_frame = av_frame.reformat(format="rgb24")
            else:
                rgb_frame = av_frame.reformat(width=tw, height=th, format="rgb24")
            try:
                from samyuj.media.enhance import apply_enhance_to_rgb_frame
                apply_enhance_to_rgb_frame(rgb_frame, str(stream_key))
            except Exception:
                pass
            plane = rgb_frame.planes[0]
            stride = int(plane.line_size)
            try:
                mv = memoryview(plane)
            except TypeError:
                mv = memoryview(bytes(plane))
            need_bytes = tw * th * 4
            min_src_bytes = (th - 1) * stride + tw * 3 if th > 0 and tw > 0 else 0
            bgrx_buf = None
            if (
                _LIBSC_FAST is not None
                and hasattr(_LIBSC_FAST, "sc_rgb24_to_bgrx32")
                and stride >= tw * 3
                and len(mv) >= min_src_bytes
                and need_bytes > 0
            ):
                rkey = (str(stream_key), tw, th)
                slots = self._ring_buffers.get(rkey)
                if slots is None or len(slots) != 3 or len(slots[0]) != need_bytes:
                    slots = [bytearray(need_bytes), bytearray(need_bytes), bytearray(need_bytes)]
                    self._ring_buffers[rkey] = slots
                    self._ring_idx[rkey] = 0
                idx = int(self._ring_idx[rkey]) % 3
                self._ring_idx[rkey] = (idx + 1) % 3
                dst_buf = slots[idx]
                try:
                    src_c = (ctypes.c_uint8 * len(mv)).from_buffer(mv)
                except TypeError:
                    src_c = (ctypes.c_uint8 * len(mv)).from_buffer_copy(mv)
                dst_c = (ctypes.c_uint8 * need_bytes).from_buffer(dst_buf)
                _LIBSC_FAST.sc_rgb24_to_bgrx32(
                    ctypes.addressof(src_c), stride,
                    ctypes.addressof(dst_c), tw * 4,
                    tw, th,
                )
                bgrx_buf = dst_buf
            prep = {
                "tw": tw,
                "th": th,
                "fw": fw,
                "fh": fh,
                "bgrx": bgrx_buf,
                "rgb_frame": rgb_frame,
                "stride": stride,
                "tid": tid,
                "ring_slots": 3,
            }
            _store_prepared_frame_bgrx(av_frame, prep)
            self.stats["frames_converted_bgrx"] = int(self.stats.get("frames_converted_bgrx", 0)) + 1
            return prep
        except Exception:
            return None

    async def prepare_frame_bgrx_async(self, av_frame, tw: int, th: int,
                                       stream_key: str = "desktop") -> dict | None:
        if av_frame is None or not hasattr(av_frame, "reformat"):
            return None
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._scaler_executor,
            self.prepare_frame_bgrx_sync,
            av_frame,
            int(tw),
            int(th),
            str(stream_key),
        )

    def snapshot(self) -> dict:
        out = dict(self.stats)
        out["alive"] = self.is_alive()
        out["loop_lag_ms"] = round(self.loop_lag_ms, 2)
        out["worst_loop_lag_ms"] = round(self.worst_loop_lag_ms, 2)
        return out

_MOBILE_DESKTOP_SERVICE_THREAD: MobileDesktopServiceThread | None = None
_MOBILE_DESKTOP_THREAD_LOCK = threading.Lock()

def get_mobile_desktop_service_thread(start: bool = True) -> MobileDesktopServiceThread:
    global _MOBILE_DESKTOP_SERVICE_THREAD
    with _MOBILE_DESKTOP_THREAD_LOCK:
        if _MOBILE_DESKTOP_SERVICE_THREAD is None:
            _MOBILE_DESKTOP_SERVICE_THREAD = MobileDesktopServiceThread()
    if start:
        _MOBILE_DESKTOP_SERVICE_THREAD.start()
    return _MOBILE_DESKTOP_SERVICE_THREAD

def _make_threadsafe_task(coro):
    task = asyncio.create_task(coro)
    with contextlib.suppress(Exception):
        loop = task.get_loop()
        orig_cancel = task.cancel
        owner_tid = threading.get_ident()

        def _ts_cancel(*args, **kwargs):
            if threading.get_ident() == owner_tid:
                return orig_cancel(*args, **kwargs)
            with contextlib.suppress(Exception):
                if loop.is_running():
                    loop.call_soon_threadsafe(lambda: orig_cancel(*args, **kwargs))
            return True

        task.cancel = _ts_cancel
    return task

def _bind_pc_to_media_loop(pc, media_thread: MobileDesktopServiceThread):
    if pc is None or media_thread is None or media_thread.loop is None:
        return pc
    mloop = media_thread.loop
    with contextlib.suppress(Exception):
        pc._sc_media_loop = mloop
        pc._sc_media_tid = media_thread.ident
        for mname in (
            "close",
            "getStats",
            "addIceCandidate",
            "setRemoteDescription",
            "createAnswer",
            "setLocalDescription",
        ):
            orig_fn = getattr(pc, mname, None)
            if not callable(orig_fn):
                continue

            def _make_wrapper(_orig):
                async def _wrapped(*args, **kwargs):
                    try:
                        cur = asyncio.get_running_loop()
                    except RuntimeError:
                        cur = None
                    if cur is mloop or not mloop.is_running():
                        return await _orig(*args, **kwargs)
                    fut = asyncio.run_coroutine_threadsafe(_orig(*args, **kwargs), mloop)
                    return await asyncio.wrap_future(fut)

                return _wrapped

            setattr(pc, mname, _make_wrapper(orig_fn))
    return pc
