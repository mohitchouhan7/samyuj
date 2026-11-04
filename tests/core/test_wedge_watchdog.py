from __future__ import annotations

import threading
import time
import unittest

from samyuj.core.wedge_watchdog import WedgeWatchdog

class WedgeWatchdogTest(unittest.TestCase):
    def test_stall_triggers_emergency_exactly_once(self) -> None:
        wd = WedgeWatchdog(stall_limit_s=0.2, check_interval_s=0.05)
        calls: list[float] = []
        wd._emergency = lambda stalled: calls.append(stalled)
        wd.start()
        time.sleep(0.6)
        wd.stop()
        self.assertEqual(len(calls), 1)

    def test_regular_ticks_prevent_emergency(self) -> None:
        wd = WedgeWatchdog(stall_limit_s=0.25, check_interval_s=0.05)
        calls: list[float] = []
        wd._emergency = lambda stalled: calls.append(stalled)
        wd.start()
        stop = threading.Event()

        def ticker() -> None:
            while not stop.is_set():
                wd.tick()
                time.sleep(0.05)

        t = threading.Thread(target=ticker, daemon=True)
        t.start()
        time.sleep(0.6)
        stop.set()
        t.join(timeout=2)
        wd.stop()
        self.assertEqual(calls, [])

if __name__ == "__main__":
    unittest.main()
