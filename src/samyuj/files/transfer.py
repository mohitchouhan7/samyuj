from __future__ import annotations
import hashlib
import uuid

import dataclasses
import base64
import os
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

from samyuj.protocol.services.files import gen_task_id, guess_mime as _guess_mime

class TransferTask:

    ZIP_STORE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic",
                     ".mp4", ".mkv", ".mov", ".avi", ".mp3", ".m4a", ".aac",
                     ".ogg", ".flac", ".zip", ".7z", ".rar", ".gz", ".xz",
                     ".apk", ".pdf", ".docx", ".xlsx", ".pptx"}

    def __init__(self, paths: list[Path], server_port: int, zip_dir: Path,
                 mode: str = "zip") -> None:
        self.paths = list(paths)
        self.mode = mode if mode in ("zip", "files") else "zip"
        self.entries = []
        used: dict[str, int] = {}
        for i, pp in enumerate(self.paths):
            nm = pp.name
            if nm in used:
                used[nm] += 1
                stem, dot, ext = nm.rpartition(".")
                nm = f"{stem} ({used[nm]}).{ext}" if dot else f"{nm} ({used[nm]})"
            else:
                used[nm] = 0
            self.entries.append({"name": nm, "mimeType": _guess_mime(pp.name),
                                 "size": pp.stat().st_size, "dir": False, "index": i})
        self.name = self.entries[0]["name"]
        self.mime = self.entries[0]["mimeType"] if len(self.entries) == 1 else ""
        self.logical_size = sum(e["size"] for e in self.entries)
        self.file_count = len(self.entries)
        self.task_id = gen_task_id()
        self.zip_dir = Path(zip_dir)
        self.zip_path: Path | None = None
        self.zip_size: int | None = None
        self.size = self.logical_size
        self.token = base64.urlsafe_b64encode(os.urandom(24)).decode()
        self.server_port = int(server_port)
        self.phase = "announced"
        self.bytes_served = 0
        self.served_idx: set[int] = set()
        self.saved_paths: dict[int, str] = {}
        self.error: str | None = None
        self.server_seq = 1024
        self.base_url = ""

    def next_server_seq(self) -> int:
        s = self.server_seq
        self.server_seq += 1
        return s

    def ensure_zip(self) -> Path:
        if self.zip_path is not None and self.zip_path.is_file():
            return self.zip_path
        self.zip_dir.mkdir(parents=True, exist_ok=True)
        zp = self.zip_dir / f"{self.task_id}.zip"
        with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
            for e, pp in zip(self.entries, self.paths):
                kind = (zipfile.ZIP_STORED if pp.suffix.lower() in self.ZIP_STORE_EXT
                        else zipfile.ZIP_DEFLATED)
                z.write(pp, arcname=e["name"], compress_type=kind)
        self.zip_size = zp.stat().st_size
        self.zip_path = zp
        if self.mode == "zip":
            self.size = self.zip_size
        return zp

    def send_request_json(self, sender_id: str, sender_name: str) -> dict:
        req: dict = {
            "id": self.task_id,
            "senderId": sender_id,
            "senderName": sender_name,
            "fileName": self.name,
            "fileCount": self.file_count,
            "mimeType": self.mime,
            "totalSize": self.size,
            "file": self.entries,
        }
        if self.base_url:
            req["fileUrl"] = "##".join(
                f"{self.base_url}/downloadFile?taskId={self.task_id}&token={self.token}&index={i}"
                for i in range(self.file_count)
            )
        return req

from typing import Iterable

@dataclass
class TransferJob:
    job_id: str
    direction: str
    channel: str
    filename: str
    source_path: str
    dest_path: str
    total_bytes: int = 0
    bytes_done: int = 0
    state: str = "queued"
    expected_sha256: str = ""
    actual_sha256: str = ""
    attempts: int = 0
    error: str = ""
    created_at: float = dataclasses.field(default_factory=time.time)
    completed_at: float = 0.0

    @property
    def progress_pct(self) -> float:
        if self.total_bytes <= 0:
            return 100.0 if self.state == "completed" else 0.0
        return round(min(100.0, (self.bytes_done / self.total_bytes) * 100.0), 1)

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "direction": self.direction,
            "channel": self.channel,
            "filename": self.filename,
            "source_path": self.source_path,
            "dest_path": self.dest_path,
            "total_bytes": self.total_bytes,
            "bytes_done": self.bytes_done,
            "progress_pct": self.progress_pct,
            "state": self.state,
            "expected_sha256": self.expected_sha256,
            "actual_sha256": self.actual_sha256,
            "attempts": self.attempts,
            "error": self.error,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }

class FileTransferEngine:

    def __init__(self) -> None:
        self.jobs: dict[str, TransferJob] = {}

    @staticmethod
    def sha256_file(path: str | Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()

    def create_job(
        self,
        *,
        direction: str,
        channel: str,
        filename: str,
        source_path: str,
        dest_path: str,
        total_bytes: int = 0,
        expected_sha256: str = "",
    ) -> TransferJob:
        safe_name = Path(filename).name
        if not safe_name or safe_name in (".", ".."):
            raise ValueError(f"Unsafe transfer filename: {filename!r}")
        jid = f"xfer_{uuid.uuid4().hex[:10]}"
        job = TransferJob(
            job_id=jid,
            direction=direction,
            channel=channel,
            filename=safe_name,
            source_path=source_path,
            dest_path=dest_path,
            total_bytes=int(total_bytes),
            expected_sha256=(expected_sha256 or "").lower(),
        )
        self.jobs[jid] = job
        return job

    def cancel_job(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if job is None or job.state == "completed":
            return False
        job.state = "cancelled"
        job.error = "Cancelled by user"
        return True

    def retry_job(self, job_id: str) -> TransferJob | None:
        job = self.jobs.get(job_id)
        if job is None:
            return None
        job.state = "queued"
        job.bytes_done = 0
        job.error = ""
        return job

    def receive_stream_to_dest(
        self,
        chunks: Iterable[bytes],
        *,
        dest_dir: str | Path,
        filename: str,
        expected_size: int = 0,
        expected_sha256: str = "",
        channel: str = "fileex_svc2",
    ) -> TransferJob:
        target_dir = Path(dest_dir).expanduser().resolve()
        target_dir.mkdir(parents=True, exist_ok=True)
        safe_name = Path(filename).name
        if not safe_name or safe_name in (".", ".."):
            raise ValueError(f"Invalid filename: {filename!r}")
        final_path = target_dir / safe_name
        part_path = target_dir / f".{safe_name}.{uuid.uuid4().hex[:8]}.part"

        job = self.create_job(
            direction="phone_to_pc",
            channel=channel,
            filename=safe_name,
            source_path=filename,
            dest_path=str(final_path),
            total_bytes=expected_size,
            expected_sha256=expected_sha256,
        )
        job.state = "running"
        job.attempts += 1
        hasher = hashlib.sha256()

        try:
            with open(part_path, "wb") as f:
                for chunk in chunks:
                    if job.state == "cancelled":
                        raise RuntimeError("Transfer cancelled")
                    f.write(chunk)
                    hasher.update(chunk)
                    job.bytes_done += len(chunk)
                f.flush()
                os.fsync(f.fileno())

            job.actual_sha256 = hasher.hexdigest()
            if expected_size > 0 and job.bytes_done != expected_size:
                raise ValueError(
                    f"Size mismatch: expected {expected_size} bytes, got {job.bytes_done}"
                )
            if expected_sha256 and job.actual_sha256.lower() != expected_sha256.lower():
                raise ValueError(
                    f"SHA-256 mismatch: expected {expected_sha256}, got {job.actual_sha256}"
                )
            os.replace(part_path, final_path)
            job.state = "completed"
            job.completed_at = time.time()
        except Exception as exc:
            job.state = "failed" if job.state != "cancelled" else "cancelled"
            job.error = str(exc)
            try:
                if part_path.exists():
                    part_path.unlink()
            except OSError:
                pass
        return job

    def list_jobs(self) -> list[dict]:
        return [j.to_dict() for j in self.jobs.values()]
