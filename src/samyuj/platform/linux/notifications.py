from __future__ import annotations

import base64
import contextlib
import shutil
import subprocess
from pathlib import Path

def emit_desktop_notification(
    title: str,
    body: str,
    app_name: str = "Samyuj",
    urgency: str = "normal",
) -> bool:
    bin_path = shutil.which("notify-send")
    if not bin_path:
        return False
    try:
        r = subprocess.run(
            [bin_path, "-a", str(app_name or "Samyuj"), "-u", urgency, "--", str(title), str(body)],
            capture_output=True,
            text=True,
            timeout=3.0,
            check=False,
        )
        return r.returncode == 0
    except Exception:
        return False

def apply_wallpaper_to_linux_desktop(
    png_bytes_or_b64: bytes | str,
    cache_dir: str | Path,
) -> tuple[bool, str]:
    try:
        raw = (
            base64.b64decode(png_bytes_or_b64)
            if isinstance(png_bytes_or_b64, str)
            else bytes(png_bytes_or_b64)
        )
        if not raw:
            return False, "empty wallpaper data"
        out_p = Path(cache_dir) / "phone_wallpaper.png"
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_bytes(raw)
        uri = f"file://{out_p.resolve()}"
        if shutil.which("gsettings"):
            for key in ("picture-uri", "picture-uri-dark"):
                with contextlib.suppress(Exception):
                    subprocess.run(
                        ["gsettings", "set", "org.gnome.desktop.background", key, uri],
                        capture_output=True,
                        timeout=2.5,
                        check=False,
                    )
            return True, str(out_p)
        if shutil.which("feh"):
            with contextlib.suppress(Exception):
                subprocess.run(
                    ["feh", "--bg-scale", str(out_p)],
                    capture_output=True,
                    timeout=2.5,
                    check=False,
                )
                return True, str(out_p)
        return True, str(out_p)
    except Exception as exc:
        return False, repr(exc)

def try_os_lock_screen() -> tuple[bool, str]:
    candidates = [
        (["loginctl", "lock-session"], "loginctl"),
        (["xdg-screensaver", "lock"], "xdg-screensaver"),
        (
            [
                "dbus-send",
                "--type=method_call",
                "--dest=org.gnome.ScreenSaver",
                "/org/gnome/ScreenSaver",
                "org.gnome.ScreenSaver.Lock",
            ],
            "gnome-screensaver-dbus",
        ),
    ]
    for cmd, method in candidates:
        if not shutil.which(cmd[0]):
            continue
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=3.0, check=False)
            if r.returncode == 0:
                return True, method
        except Exception:
            continue
    return False, "no lock tool available"

def query_os_screen_locked() -> bool:
    if shutil.which("gdbus"):
        with contextlib.suppress(Exception):
            r = subprocess.run(
                [
                    "gdbus",
                    "call",
                    "--session",
                    "--dest",
                    "org.gnome.ScreenSaver",
                    "--object-path",
                    "/org/gnome/ScreenSaver",
                    "--method",
                    "org.gnome.ScreenSaver.GetActive",
                ],
                capture_output=True,
                text=True,
                timeout=1.5,
                check=False,
            )
            if r.returncode == 0 and "true" in (r.stdout or "").lower():
                return True
    return False
