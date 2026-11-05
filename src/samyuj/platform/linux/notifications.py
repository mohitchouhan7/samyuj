from __future__ import annotations

import base64
import contextlib
import os
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
        abs_str = str(out_p.resolve())
        uri = f"file://{abs_str}"
        desktop = (
            (os.environ.get("XDG_CURRENT_DESKTOP") or "")
            + ":"
            + (os.environ.get("DESKTOP_SESSION") or "")
        ).lower()

        if ("kde" in desktop or "plasma" in desktop) and shutil.which("plasma-apply-wallpaperimage"):
            with contextlib.suppress(Exception):
                r = subprocess.run(
                    ["plasma-apply-wallpaperimage", abs_str],
                    capture_output=True,
                    timeout=3.0,
                    check=False,
                )
                if r.returncode == 0:
                    return True, str(out_p)

        if "xfce" in desktop and shutil.which("xfconf-query"):
            with contextlib.suppress(Exception):
                r_list = subprocess.run(
                    ["xfconf-query", "-c", "xfce4-desktop", "-l"],
                    capture_output=True,
                    text=True,
                    timeout=2.5,
                    check=False,
                )
                for prop in (r_list.stdout or "").splitlines():
                    prop = prop.strip()
                    if prop.endswith("last-image"):
                        subprocess.run(
                            ["xfconf-query", "-c", "xfce4-desktop", "-p", prop, "-s", abs_str],
                            capture_output=True,
                            timeout=2.5,
                            check=False,
                        )
                return True, str(out_p)

        if shutil.which("gsettings"):
            if "cinnamon" in desktop:
                with contextlib.suppress(Exception):
                    subprocess.run(
                        ["gsettings", "set", "org.cinnamon.desktop.background", "picture-uri", uri],
                        capture_output=True,
                        timeout=2.5,
                        check=False,
                    )
                    return True, str(out_p)
            if "mate" in desktop:
                with contextlib.suppress(Exception):
                    subprocess.run(
                        ["gsettings", "set", "org.mate.background", "picture-filename", abs_str],
                        capture_output=True,
                        timeout=2.5,
                        check=False,
                    )
                    return True, str(out_p)
            for key in ("picture-uri", "picture-uri-dark"):
                with contextlib.suppress(Exception):
                    subprocess.run(
                        ["gsettings", "set", "org.gnome.desktop.background", key, uri],
                        capture_output=True,
                        timeout=2.5,
                        check=False,
                    )
            if any(k in desktop for k in ("gnome", "ubuntu", "budgie", "pantheon", "cosmic")) or not desktop.strip(":"):
                return True, str(out_p)

        if shutil.which("plasma-apply-wallpaperimage"):
            with contextlib.suppress(Exception):
                r = subprocess.run(
                    ["plasma-apply-wallpaperimage", abs_str],
                    capture_output=True,
                    timeout=3.0,
                    check=False,
                )
                if r.returncode == 0:
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
                "--dest=org.freedesktop.ScreenSaver",
                "/ScreenSaver",
                "org.freedesktop.ScreenSaver.Lock",
            ],
            "freedesktop-screensaver-dbus",
        ),
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
    if shutil.which("loginctl"):
        with contextlib.suppress(Exception):
            sid = (os.environ.get("XDG_SESSION_ID") or "").strip()
            cmd = ["loginctl", "show-session", "-p", "LockedHint", "--value"]
            if sid:
                cmd.append(sid)
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=1.5, check=False)
            if r.returncode == 0 and (r.stdout or "").strip().lower() == "yes":
                return True
    if shutil.which("gdbus"):
        for dest, obj_path, iface in (
            ("org.gnome.ScreenSaver", "/org/gnome/ScreenSaver", "org.gnome.ScreenSaver.GetActive"),
            ("org.freedesktop.ScreenSaver", "/ScreenSaver", "org.freedesktop.ScreenSaver.GetActive"),
        ):
            with contextlib.suppress(Exception):
                r = subprocess.run(
                    [
                        "gdbus",
                        "call",
                        "--session",
                        "--dest",
                        dest,
                        "--object-path",
                        obj_path,
                        "--method",
                        iface,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=1.5,
                    check=False,
                )
                if r.returncode == 0 and "true" in (r.stdout or "").lower():
                    return True
    return False
