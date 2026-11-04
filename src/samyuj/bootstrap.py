from __future__ import annotations

import argparse
import contextlib
import asyncio
from pathlib import Path
from typing import Sequence

from samyuj import __version__
from samyuj.core.logging import configure_logging
from samyuj.core.settings import UserSettings
from samyuj.platform.linux.paths import ensure_user_desktop_integration

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="samyuj",
        description="Samyuj — Native GTK 3 Desktop Companion",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging and diagnostic event capture",
    )
    parser.add_argument(
        "--log-level",
        choices=("error", "warning", "info", "debug"),
        default="info",
        help="Set logging verbosity level (default: info)",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help="Write structured logs to the specified file path",
    )
    parser.add_argument(
        "--lan-ip",
        type=str,
        default=None,
        help="Override the LAN IP advertised to the phone (multi-homed PCs: "
             "pick the address on the SAME subnet as the phone)",
    )
    parser.add_argument(
        "--usb-addr-order",
        type=str,
        default=None,
        help="Comma-separated USB address assignment ladder (default: dhcp,static-p2p)",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Populate UI views with sample demonstration data when no phone is connected",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"samyuj {__version__}",
    )
    return parser

def parse_launcher_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = build_arg_parser()
    return parser.parse_args(list(argv) if argv is not None else None)

async def run_gui_application(args: argparse.Namespace) -> int:
    from samyuj.media.remb import set_remb_ceiling, set_remb_floor, set_remb_loss_floor
    from samyuj.session.lifecycle import SamyujRuntime
    from samyuj.ui.facade import ApplicationFacade
    from samyuj.ui.gtk.app import SamyujGtkDesktopApp

    runtime = SamyujRuntime(
        host="0.0.0.0",
        port=0,
        debug=bool(args.debug),
        desktop_gui=True,
        media=True,
        lan_ip=getattr(args, "lan_ip", None),
    )
    if getattr(args, "usb_addr_order", None):
        runtime.user_settings["usb_addr_order"] = str(args.usb_addr_order)
    if getattr(args, "demo", False):
        runtime.user_settings["demo_mode"] = True
    runtime.settings_model = UserSettings.from_dict(runtime.user_settings)
    set_remb_floor(6000)
    set_remb_loss_floor(1500)
    set_remb_ceiling(8000)
    facade = ApplicationFacade(runtime)
    await runtime.start()

    loop = asyncio.get_running_loop()
    if bool(args.debug):
        with contextlib.suppress(Exception):
            loop.set_debug(True)
            loop.slow_callback_duration = 0.3

    from samyuj.core.wedge_watchdog import WedgeWatchdog
    watchdog = WedgeWatchdog(stall_limit_s=20.0)
    mgr = getattr(runtime, "linux_mount_mgr", None)
    fuse_glob = None
    base = getattr(mgr, "base_dir", None)
    if base is not None:
        fuse_glob = str(Path(base) / "*" / "live")
    dump_p = Path(getattr(runtime, "out_dir", None)
                  or getattr(runtime, "state_dir", None) or Path.cwd()) / "wedge_dump.txt"
    watchdog.start(fuse_mount_glob=fuse_glob, dump_path=dump_p)

    async def _watchdog_tick() -> None:
        while True:
            watchdog.tick()
            await asyncio.sleep(2.0)

    tick_task = asyncio.create_task(_watchdog_tick())
    try:
        app = SamyujGtkDesktopApp(facade)
        await app.run_async()
    finally:
        tick_task.cancel()
        watchdog.stop()
        await runtime.stop()
    return 0

def _install_sigterm_handler() -> None:
    import signal

    def _term(signum, frame):
        raise KeyboardInterrupt

    with contextlib.suppress(ValueError, OSError):
        signal.signal(signal.SIGTERM, _term)

def _set_process_ui_name() -> None:
    with contextlib.suppress(Exception):
        import gi
        gi.require_version("GLib", "2.0")
        from gi.repository import GLib
        GLib.set_prgname("samyuj")
        GLib.set_application_name("Samyuj")

def main(argv: Sequence[str] | None = None) -> int:
    _set_process_ui_name()
    _install_sigterm_handler()
    args = parse_launcher_args(argv)
    effective_args = {
        "debug": bool(args.debug),
        "log_level": "debug" if args.debug else str(args.log_level),
        "log_file": str(args.log_file) if args.log_file else None,
        "version": __version__,
    }
    configure_logging(
        level=args.log_level,
        debug=bool(args.debug),
        log_file=args.log_file,
        effective_args=effective_args,
    )
    ensure_user_desktop_integration()
    try:
        return asyncio.run(run_gui_application(args))
    except KeyboardInterrupt:
        return 130

if __name__ == "__main__":
    raise SystemExit(main())
