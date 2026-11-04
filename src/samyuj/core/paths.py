from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class XdgPaths:
    config_dir: Path
    data_dir: Path
    cache_dir: Path
    runtime_dir: Path
    downloads_dir: Path

    def ensure_dirs(self) -> None:
        for d in (self.config_dir, self.data_dir, self.cache_dir, self.runtime_dir):
            d.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(d, 0o700)
            except OSError:
                pass

def get_xdg_paths(app_name: str = "samyuj") -> XdgPaths:
    home = Path.home()
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or (home / ".config"))
    data_home = Path(os.environ.get("XDG_DATA_HOME") or (home / ".local" / "share"))
    cache_home = Path(os.environ.get("XDG_CACHE_HOME") or (home / ".cache"))
    uid = os.getuid() if hasattr(os, "getuid") else 1000
    runtime_base = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/runtime-{uid}")
    downloads = Path(os.environ.get("XDG_DOWNLOAD_DIR") or (home / "Downloads"))

    paths = XdgPaths(
        config_dir=config_home / app_name,
        data_dir=data_home / app_name,
        cache_dir=cache_home / app_name,
        runtime_dir=runtime_base / app_name,
        downloads_dir=downloads / "Samyuj",
    )
    paths.ensure_dirs()
    return paths
