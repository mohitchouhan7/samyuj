from __future__ import annotations
from dataclasses import dataclass

import dataclasses
import datetime
import posixpath
import re
from pathlib import Path

FILE_WIDGET_SOURCES: dict[str, dict] = {
    "gallery": {
        "source_id": "gallery",
        "title": "Gallery",
        "default_path": "/DCIM/Camera",
        "fallback_paths": ["/DCIM/Camera", "/Pictures", "/DCIM"],
        "default_view": "grid",
        "default_sort": "date",
        "extensions": [".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".mp4", ".mov", ".mkv"],
        "description": "Recent photos and videos from your phone",
        "protocol_supported": True,
    },
    "downloads": {
        "source_id": "downloads",
        "title": "Downloads",
        "default_path": "/Download",
        "fallback_paths": ["/Download", "/Downloads"],
        "default_view": "list",
        "default_sort": "date",
        "extensions": None,
        "description": "Downloaded files and active transfer status",
        "protocol_supported": True,
    },
    "documents": {
        "source_id": "documents",
        "title": "Documents",
        "default_path": "/Documents",
        "fallback_paths": ["/Documents"],
        "default_view": "details",
        "default_sort": "name",
        "extensions": [".pdf", ".doc", ".docx", ".txt", ".md", ".xls", ".xlsx", ".ppt", ".pptx", ".csv", ".zip"],
        "description": "PDFs, notes, spreadsheets, and archives",
        "protocol_supported": True,
    },
    "dcim": {
        "source_id": "dcim",
        "title": "DCIM / Camera",
        "default_path": "/DCIM",
        "fallback_paths": ["/DCIM", "/DCIM/Camera"],
        "default_view": "grid",
        "default_sort": "date",
        "extensions": [".jpg", ".jpeg", ".png", ".mp4", ".mov", ".dng"],
        "description": "Camera roll folders and raw captures",
        "protocol_supported": True,
    },
    "media": {
        "source_id": "media",
        "title": "Music & Videos",
        "default_path": "/Music",
        "fallback_paths": ["/Music", "/Movies", "/Podcasts"],
        "default_view": "list",
        "default_sort": "name",
        "extensions": [".mp3", ".flac", ".wav", ".ogg", ".m4a", ".mp4", ".mkv", ".webm"],
        "description": "Audio tracks and video clips on your phone",
        "protocol_supported": True,
    },
    "custom": {
        "source_id": "custom",
        "title": "Custom Folder",
        "default_path": "/",
        "fallback_paths": ["/"],
        "default_view": "list",
        "default_sort": "name",
        "extensions": None,
        "description": "User-selected folder on phone storage",
        "protocol_supported": True,
    },
}

_FILENAME_TS_RE = re.compile(
    r"(?:^|[^0-9])(20[123]\d)[-_]?([01]\d)[-_]?([0-3]\d)[-_T ]?([0-2]\d)[-_:]?([0-5]\d)[-_:]?([0-5]\d)"
)

def extract_timestamp_from_filename(name: str) -> float:
    m = _FILENAME_TS_RE.search(str(name or ""))
    if not m:
        return 0.0
    try:
        dt = datetime.datetime(
            int(m.group(1)),
            int(m.group(2)),
            int(m.group(3)),
            int(m.group(4)),
            int(m.group(5)),
            int(m.group(6)),
            tzinfo=datetime.timezone.utc,
        )
        return float(dt.timestamp())
    except ValueError:
        return 0.0

def sanitize_remote_phone_path(raw_path: str) -> str:
    cleaned = (raw_path or "/").strip().replace("\\", "/")
    if cleaned.startswith("/sdcard"):
        cleaned = cleaned[len("/sdcard"):] or "/"
    parts = [p for p in cleaned.split("/") if p and p != "."]
    if any(p == ".." for p in parts):
        raise ValueError(f"Path traversal rejected in remote phone path: {raw_path!r}")
    return "/" + "/".join(parts)

def classify_file_kind(name: str, is_dir: bool) -> str:
    if is_dir:
        return "folder"
    ext = Path(name).suffix.lower()
    if ext in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".dng", ".bmp"}:
        return "image"
    if ext in {".mp4", ".mov", ".mkv", ".webm", ".3gp", ".avi"}:
        return "video"
    if ext in {".mp3", ".flac", ".wav", ".ogg", ".m4a"}:
        return "audio"
    if ext in {".pdf", ".doc", ".docx", ".txt", ".md", ".xls", ".xlsx", ".ppt", ".pptx", ".csv"}:
        return "document"
    if ext in {".zip", ".tar", ".gz", ".7z"}:
        return "archive"
    return "file"

@dataclass
class PhoneFileWidgetConfig:
    widget_id: str
    source_id: str = "gallery"
    custom_title: str = ""
    remote_path: str = ""
    view_mode: str = "grid"
    sort_by: str = "date"
    sort_desc: bool = True
    size_mode: str = "compact"

    def __post_init__(self) -> None:
        preset = FILE_WIDGET_SOURCES.get(self.source_id)
        if preset is not None:
            if not self.remote_path:
                self.remote_path = preset["default_path"]
            if not self.custom_title:
                self.custom_title = preset["title"]
        if self.remote_path:
            self.remote_path = sanitize_remote_phone_path(self.remote_path)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

@dataclass
class PhoneFileEntry:
    name: str
    remote_path: str
    is_dir: bool
    size: int = 0
    mtime: float = 0.0
    file_kind: str = "file"
    extension: str = ""
    thumbnail_hint: str = ""

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["path"] = self.remote_path
        return d

@dataclass
class PhoneFileWidgetResult:
    widget_id: str
    source_id: str
    title: str
    remote_path: str
    view_mode: str
    sort_by: str
    sort_desc: bool
    size_mode: str
    status: str
    user_message: str
    next_step_hint: str
    entries: list[dict]
    transfer_summary: dict

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

class PhoneFileWidgetController:

    def __init__(self, harness) -> None:
        self.h = harness
        self.mock_fs_tree: dict[str, list[dict]] | None = None

    def _get_transfer_summary(self) -> dict:
        if hasattr(self.h, "get_filetransfer_summary"):
            return self.h.get_filetransfer_summary()
        return {
            "active_task": None,
            "sent_count": len(getattr(self.h, "xfer_tasks", {}) or {}),
            "recv_count": len(getattr(self.h, "recv_files", []) or []),
            "last_status": getattr(self.h, "xfer_last_status", ""),
        }

    async def fetch_widget_state(self, cfg: PhoneFileWidgetConfig) -> PhoneFileWidgetResult:
        xfer = self._get_transfer_summary()
        preset = FILE_WIDGET_SOURCES.get(cfg.source_id)
        if preset is None or not preset.get("protocol_supported", False):
            return PhoneFileWidgetResult(
                widget_id=cfg.widget_id,
                source_id=cfg.source_id,
                title=cfg.custom_title or cfg.source_id.title(),
                remote_path=cfg.remote_path or "/",
                view_mode=cfg.view_mode,
                sort_by=cfg.sort_by,
                sort_desc=cfg.sort_desc,
                size_mode=cfg.size_mode,
                status="unsupported_source",
                user_message=f"The source '{cfg.source_id}' is not supported by the phone's storage protocol.",
                next_step_hint="Choose Gallery, Downloads, Documents, DCIM, Music & Videos, or a Custom Folder under /sdcard.",
                entries=[],
                transfer_summary=xfer,
            )

        try:
            norm_path = sanitize_remote_phone_path(cfg.remote_path or preset["default_path"])
        except ValueError as exc:
            return PhoneFileWidgetResult(
                widget_id=cfg.widget_id,
                source_id=cfg.source_id,
                title=cfg.custom_title or preset["title"],
                remote_path=cfg.remote_path,
                view_mode=cfg.view_mode,
                sort_by=cfg.sort_by,
                sort_desc=cfg.sort_desc,
                size_mode=cfg.size_mode,
                status="unsupported_source",
                user_message="Invalid folder path.",
                next_step_hint=str(exc),
                entries=[],
                transfer_summary=xfer,
            )

        raw_items: list[dict] | None = None
        if self.mock_fs_tree is not None:
            if norm_path in self.mock_fs_tree:
                raw_items = self.mock_fs_tree[norm_path]
            else:
                return PhoneFileWidgetResult(
                    widget_id=cfg.widget_id,
                    source_id=cfg.source_id,
                    title=cfg.custom_title or preset["title"],
                    remote_path=norm_path,
                    view_mode=cfg.view_mode,
                    sort_by=cfg.sort_by,
                    sort_desc=cfg.sort_desc,
                    size_mode=cfg.size_mode,
                    status="path_not_found",
                    user_message=f"Folder '{norm_path}' was not found on your phone.",
                    next_step_hint="Choose an existing folder on your phone or reset this widget's path.",
                    entries=[],
                    transfer_summary=xfer,
                )
        else:
            live_conn = self.h._live_ws() if hasattr(self.h, "_live_ws") else None
            pfs_state = (getattr(self.h, "phonefs", {}) or {}).get("state", "idle")
            if live_conn is None and pfs_state != "ready":
                return PhoneFileWidgetResult(
                    widget_id=cfg.widget_id,
                    source_id=cfg.source_id,
                    title=cfg.custom_title or preset["title"],
                    remote_path=norm_path,
                    view_mode=cfg.view_mode,
                    sort_by=cfg.sort_by,
                    sort_desc=cfg.sort_desc,
                    size_mode=cfg.size_mode,
                    status="unavailable",
                    user_message="Phone storage is not connected right now.",
                    next_step_hint="Connect your phone over USB or Wi-Fi and grant 'All files access' in Smart Connect on your phone.",
                    entries=[],
                    transfer_summary=xfer,
                )
            if getattr(self.h, "_pfs_client", None) is None or pfs_state != "ready":
                return PhoneFileWidgetResult(
                    widget_id=cfg.widget_id,
                    source_id=cfg.source_id,
                    title=cfg.custom_title or preset["title"],
                    remote_path=norm_path,
                    view_mode=cfg.view_mode,
                    sort_by=cfg.sort_by,
                    sort_desc=cfg.sort_desc,
                    size_mode=cfg.size_mode,
                    status="unavailable",
                    user_message="Phone connected — click 'Connect Phone Storage' to browse files.",
                    next_step_hint="Open Phone Storage or click Refresh to mount your phone's folders.",
                    entries=[],
                    transfer_summary=xfer,
                )
            res = await self.h.phonefs_ls(norm_path)
            if not res.get("ok"):
                return PhoneFileWidgetResult(
                    widget_id=cfg.widget_id,
                    source_id=cfg.source_id,
                    title=cfg.custom_title or preset["title"],
                    remote_path=norm_path,
                    view_mode=cfg.view_mode,
                    sort_by=cfg.sort_by,
                    sort_desc=cfg.sort_desc,
                    size_mode=cfg.size_mode,
                    status="path_not_found",
                    user_message=f"Could not open '{norm_path}' on phone storage.",
                    next_step_hint=res.get("error") or "Verify the folder exists on your phone.",
                    entries=[],
                    transfer_summary=xfer,
                )
            raw_items = res.get("entries") or []

        parsed_entries: list[PhoneFileEntry] = []
        for item in raw_items or []:
            name = str(item.get("name") or "")
            if not name or name in (".", ".."):
                continue
            is_dir = bool(item.get("is_dir") or item.get("type") == "dir")
            ext = Path(name).suffix.lower() if not is_dir else ""
            raw_item_path = item.get("path") or item.get("remote_path")
            if raw_item_path:
                rpath = sanitize_remote_phone_path(str(raw_item_path))
            else:
                rpath = posixpath.join(norm_path, name) if norm_path != "/" else f"/{name}"
            kind = classify_file_kind(name, is_dir)
            raw_mtime = float(item.get("mtime") or 0.0)
            if raw_mtime > 1e12:
                raw_mtime = raw_mtime / 1000.0
            if raw_mtime <= 0.0 and not is_dir:
                raw_mtime = extract_timestamp_from_filename(name)
            parsed_entries.append(
                PhoneFileEntry(
                    name=name,
                    remote_path=rpath,
                    is_dir=is_dir,
                    size=int(item.get("size") or 0),
                    mtime=raw_mtime,
                    file_kind=kind,
                    extension=ext,
                    thumbnail_hint=rpath if kind in ("image", "video") else "",
                )
            )

        sort_key = cfg.sort_by.lower()
        if sort_key == "size":
            parsed_entries.sort(key=lambda e: (not e.is_dir, e.size, e.name.lower()), reverse=cfg.sort_desc)
        elif sort_key == "date":
            if cfg.source_id == "gallery":
                files_only = [e for e in parsed_entries if not e.is_dir]
                dirs_only = [e for e in parsed_entries if e.is_dir]
                files_only.sort(key=lambda e: (e.mtime, e.name.lower()), reverse=cfg.sort_desc)
                dirs_only.sort(key=lambda e: (e.mtime, e.name.lower()), reverse=cfg.sort_desc)
                parsed_entries = files_only + dirs_only
            else:
                parsed_entries.sort(key=lambda e: (not e.is_dir, e.mtime, e.name.lower()), reverse=cfg.sort_desc)
        elif sort_key == "type":
            parsed_entries.sort(key=lambda e: (not e.is_dir, e.file_kind, e.extension, e.name.lower()), reverse=cfg.sort_desc)
        else:
            parsed_entries.sort(key=lambda e: (not e.is_dir, e.name.lower()), reverse=cfg.sort_desc)

        return PhoneFileWidgetResult(
            widget_id=cfg.widget_id,
            source_id=cfg.source_id,
            title=cfg.custom_title or preset["title"],
            remote_path=norm_path,
            view_mode=cfg.view_mode,
            sort_by=cfg.sort_by,
            sort_desc=cfg.sort_desc,
            size_mode=cfg.size_mode,
            status="ready",
            user_message=f"{len(parsed_entries)} item(s) in {norm_path}",
            next_step_hint="",
            entries=[e.to_dict() for e in parsed_entries],
            transfer_summary=xfer,
        )
