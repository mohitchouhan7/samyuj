from __future__ import annotations

import contextlib
import os
import shutil
import sys
from pathlib import Path

from samyuj.core.paths import XdgPaths, get_xdg_paths

def ensure_user_desktop_integration(project_root: Path | None = None) -> Path | None:
    try:
        root = Path(project_root) if project_root else Path(__file__).resolve().parents[4]
        xdg_data = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
        apps_dir = xdg_data / "applications"
        icons_dir = xdg_data / "icons" / "hicolor" / "scalable" / "apps"
        apps_dir.mkdir(parents=True, exist_ok=True)
        icons_dir.mkdir(parents=True, exist_ok=True)
        desktop_dst = apps_dir / "samyuj.desktop"
        sys_desktop = Path("/usr/share/applications/samyuj.desktop")

        if project_root is None and not (root / "src" / "samyuj").is_dir() and sys_desktop.is_file():
            if desktop_dst.is_file():
                with contextlib.suppress(OSError):
                    cur_txt = desktop_dst.read_text(encoding="utf-8", errors="ignore")
                    if "Path=/usr\n" in cur_txt:
                        desktop_dst.unlink()
            return sys_desktop

        svg_src = root / "packaging" / "linux" / "samyuj.svg"
        svg_dst = icons_dir / "samyuj.svg"
        if svg_src.is_file() and not svg_dst.is_file():
            shutil.copyfile(svg_src, svg_dst)

        launcher = root / "samyuj"
        sys_bin = shutil.which("samyuj")
        if launcher.is_file():
            exec_cmd = str(launcher)
            work_path = str(root)
        elif sys_bin:
            exec_cmd = sys_bin
            work_path = str(Path.home())
        else:
            exec_cmd = f"{sys.executable} -m samyuj"
            work_path = str(root if (root / "src" / "samyuj").is_dir() else Path.home())
        icon_val = str(svg_dst) if svg_dst.is_file() else ("samyuj" if Path("/usr/share/icons/hicolor/scalable/apps/samyuj.svg").is_file() else "phone")
        desktop_content = (
            "[Desktop Entry]\n"
            "Version=1.0\n"
            "Type=Application\n"
            "Name=Samyuj\n"
            "GenericName=Phone & PC Companion\n"
            "Comment=Unofficial Linux desktop companion for Motorola & Lenovo Smart Connect phones (USB, Wi-Fi & BLE)\n"
            f"Exec={exec_cmd}\n"
            f"Path={work_path}\n"
            f"Icon={icon_val}\n"
            "Terminal=false\n"
            "Categories=Utility;Network;FileTransfer;AudioVideo;\n"
            "Keywords=Samyuj;Phone;Companion;Webcam;Desktop;AppStream;NFS;FileTransfer;\n"
            "StartupNotify=true\n"
            "StartupWMClass=samyuj\n"
        )
        if not desktop_dst.is_file() or desktop_dst.read_text(encoding="utf-8", errors="ignore") != desktop_content:
            desktop_dst.write_text(desktop_content, encoding="utf-8")
            desktop_dst.chmod(0o755)
        for stale in ("smartconnect-linux.desktop", "dockaroo.desktop",
                      "smartconnect.desktop"):
            sp = apps_dir / stale
            if sp.is_file():
                with contextlib.suppress(OSError):
                    sp.unlink()
        stale_icon = icons_dir / "smartconnect-linux.svg"
        if stale_icon.is_file():
            with contextlib.suppress(OSError):
                stale_icon.unlink()
        return desktop_dst
    except Exception:
        return None

__all__ = ["XdgPaths", "ensure_user_desktop_integration", "get_xdg_paths"]
