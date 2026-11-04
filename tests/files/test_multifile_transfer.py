from __future__ import annotations

import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from samyuj.services.files import _fetch_task_files, _store_received_files

def _make_zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members.items():
            z.writestr(name, data)
    return buf.getvalue()

class StoreReceivedFilesTest(unittest.TestCase):
    def test_single_raw_file_saved_under_announced_name(self) -> None:
        with tempfile.TemporaryDirectory(prefix="xfer-store-") as td:
            saved = _store_received_files(
                b"hello world", td, "task1", [{"name": "a.txt"}], index=0)
            self.assertEqual(len(saved), 1)
            p = Path(saved[0])
            self.assertEqual(p.name, "a.txt")
            self.assertEqual(p.read_bytes(), b"hello world")

    def test_single_file_zip_bundle_is_extracted_not_corrupted(self) -> None:
        payload = b"\xff\xd8\xff photo bytes"
        blob = _make_zip({"photo.jpg": payload})
        with tempfile.TemporaryDirectory(prefix="xfer-store-") as td:
            saved = _store_received_files(
                blob, td, "task2", [{"name": "photo.jpg"}], index=0)
            self.assertEqual(len(saved), 1)
            p = Path(saved[0])
            self.assertEqual(p.name, "photo.jpg")
            self.assertEqual(p.read_bytes(), payload)

    def test_multi_file_bundle_extracts_every_member(self) -> None:
        blob = _make_zip({"a.txt": b"AA", "b.bin": b"BB", "c.md": b"CC"})
        files = [{"name": "a.txt"}, {"name": "b.bin"}, {"name": "c.md"}]
        with tempfile.TemporaryDirectory(prefix="xfer-store-") as td:
            saved = _store_received_files(blob, td, "task3", files, index=0)
            self.assertEqual(sorted(Path(s).name for s in saved),
                             ["a.txt", "b.bin", "c.md"])
            self.assertEqual((Path(td) / "b.bin").read_bytes(), b"BB")

    def test_genuine_zip_in_multi_batch_saved_verbatim(self) -> None:
        blob = _make_zip({"payload.bin": b"inner"})
        files = [{"name": "notes.txt"}, {"name": "archive.zip"}]
        with tempfile.TemporaryDirectory(prefix="xfer-store-") as td:
            saved = _store_received_files(blob, td, "task4", files, index=1)
            self.assertEqual(len(saved), 1)
            p = Path(saved[0])
            self.assertEqual(p.name, "archive.zip")
            with zipfile.ZipFile(p) as z:
                self.assertEqual(z.namelist(), ["payload.bin"])

    def test_name_collision_gets_suffix(self) -> None:
        files = [{"name": "dup.txt"}]
        with tempfile.TemporaryDirectory(prefix="xfer-store-") as td:
            first = _store_received_files(b"1", td, "t", files, index=0)
            second = _store_received_files(b"2", td, "t", files, index=0)
            self.assertEqual(Path(first[0]).name, "dup.txt")
            self.assertEqual(Path(second[0]).name, "dup (1).txt")
            self.assertEqual(Path(second[0]).read_bytes(), b"2")

    def test_traversal_name_is_sanitized(self) -> None:
        files = [{"name": "../../evil.txt"}]
        with tempfile.TemporaryDirectory(prefix="xfer-store-") as td:
            saved = _store_received_files(b"x", td, "t", files, index=0)
            self.assertEqual(len(saved), 1)
            p = Path(saved[0]).resolve()
            self.assertTrue(str(p).startswith(str(Path(td).resolve())))
            self.assertEqual(p.name, "evil.txt")

    def test_real_zip_with_matching_announced_size_saved_verbatim(self) -> None:
        inner = _make_zip({"a.txt": b"A"})
        files = [{"name": "a.txt", "size": 5},
                 {"name": "archive.zip", "size": len(inner)}]
        with tempfile.TemporaryDirectory(prefix="xfer-store-") as td:
            saved = _store_received_files(inner, td, "t5", files, index=1)
            self.assertEqual(len(saved), 1)
            p = Path(saved[0])
            self.assertEqual(p.name, "archive.zip")
            with zipfile.ZipFile(p) as z:
                self.assertEqual(z.namelist(), ["a.txt"])

class FetchTaskFilesTest(unittest.IsolatedAsyncioTestCase):
    def _stub_dl(self, responses, calls):
        async def http_dl(ip, port, ctx, task_id, token, total, rawdir,
                          file_urls=None, index=None):
            calls.append({"index": index, "file_urls": file_urls})
            if index is None:
                return responses.get(None, b"")
            return responses.get(index, b"")
        return http_dl

    async def test_multi_fetch_per_index_saves_every_file(self) -> None:
        files = [{"name": "one.txt"}, {"name": "two.txt"}, {"name": "three.txt"}]
        responses = {0: b"111", 1: b"222", 2: b"333"}
        calls: list = []
        with tempfile.TemporaryDirectory(prefix="xfer-fetch-") as td:
            saved = await _fetch_task_files(
                self._stub_dl(responses, calls), _store_received_files,
                files=files, file_urls=[], recv_dir=td, task_id="tk",
                token="tok", total=9, ip="203.0.113.7", port=1, ctx=None,
                raw_dir=Path(td))
            self.assertEqual(len(saved), 3)
            self.assertEqual((Path(td) / "two.txt").read_bytes(), b"222")
        self.assertEqual([c["index"] for c in calls], [0, 1, 2])

    async def test_server_ignoring_index_does_not_duplicate(self) -> None:
        files = [{"name": "x.txt"}, {"name": "y.txt"}]
        responses = {0: b"SAME", 1: b"SAME", None: b"SAME"}
        calls: list = []
        with tempfile.TemporaryDirectory(prefix="xfer-fetch-") as td:
            saved = await _fetch_task_files(
                self._stub_dl(responses, calls), _store_received_files,
                files=files, file_urls=[], recv_dir=td, task_id="tk",
                token="tok", total=8, ip="203.0.113.7", port=1, ctx=None,
                raw_dir=Path(td))
            self.assertEqual(len(saved), 1)
            self.assertEqual(Path(saved[0]).name, "x.txt")

    async def test_bundle_at_first_index_stops_the_loop(self) -> None:
        files = [{"name": "a.txt"}, {"name": "b.txt"}, {"name": "c.txt"}]
        bundle = _make_zip({"a.txt": b"A", "b.txt": b"B", "c.txt": b"C"})
        responses = {0: bundle, 1: bundle, 2: bundle, None: bundle}
        calls: list = []
        with tempfile.TemporaryDirectory(prefix="xfer-fetch-") as td:
            saved = await _fetch_task_files(
                self._stub_dl(responses, calls), _store_received_files,
                files=files, file_urls=[], recv_dir=td, task_id="tk",
                token="tok", total=3, ip="203.0.113.7", port=1, ctx=None,
                raw_dir=Path(td))
            self.assertEqual(len(saved), 3)
        self.assertEqual(len(calls), 1, "must not re-download a satisfied bundle")

    async def test_single_file_task_uses_indexless_download(self) -> None:
        files = [{"name": "only.txt"}]
        responses = {None: b"data"}
        calls: list = []
        with tempfile.TemporaryDirectory(prefix="xfer-fetch-") as td:
            saved = await _fetch_task_files(
                self._stub_dl(responses, calls), _store_received_files,
                files=files, file_urls=["https://203.0.113.7/dl?taskId=tk"],
                recv_dir=td, task_id="tk", token="tok", total=4,
                ip="203.0.113.7", port=1, ctx=None, raw_dir=Path(td))
            self.assertEqual(len(saved), 1)
            self.assertEqual((Path(td) / "only.txt").read_bytes(), b"data")
        self.assertEqual(len(calls), 1)
        self.assertIsNone(calls[0]["index"])

    async def test_failed_index_continues_to_the_next(self) -> None:
        files = [{"name": "a.txt"}, {"name": "b.txt"}, {"name": "c.txt"}]
        responses = {0: b"A", 1: b"", 2: b"C", None: b""}
        calls: list = []
        with tempfile.TemporaryDirectory(prefix="xfer-fetch-") as td:
            saved = await _fetch_task_files(
                self._stub_dl(responses, calls), _store_received_files,
                files=files, file_urls=[], recv_dir=td, task_id="tk",
                token="tok", total=2, ip="203.0.113.7", port=1, ctx=None,
                raw_dir=Path(td))
            self.assertEqual(sorted(Path(s).name for s in saved),
                             ["a.txt", "c.txt"])

    async def test_identical_content_kept_when_file_urls_present(self) -> None:
        files = [{"name": "x.txt"}, {"name": "y.txt"}]
        responses = {0: b"same", 1: b"same"}
        calls: list = []
        urls = ["https://203.0.113.7/dl?taskId=tk&index=0",
                "https://203.0.113.7/dl?taskId=tk&index=1"]
        with tempfile.TemporaryDirectory(prefix="xfer-fetch-") as td:
            saved = await _fetch_task_files(
                self._stub_dl(responses, calls), _store_received_files,
                files=files, file_urls=urls, recv_dir=td, task_id="tk",
                token="tok", total=8, ip="203.0.113.7", port=1, ctx=None,
                raw_dir=Path(td))
            self.assertEqual(sorted(Path(s).name for s in saved),
                             ["x.txt", "y.txt"])

    async def test_dir_entries_skipped_without_download(self) -> None:
        files = [{"name": "folder", "dir": True}, {"name": "a.txt"}]
        responses = {1: b"AA"}
        calls: list = []
        with tempfile.TemporaryDirectory(prefix="xfer-fetch-") as td:
            saved = await _fetch_task_files(
                self._stub_dl(responses, calls), _store_received_files,
                files=files, file_urls=[], recv_dir=td, task_id="tk",
                token="tok", total=2, ip="203.0.113.7", port=1, ctx=None,
                raw_dir=Path(td))
            self.assertEqual(len(saved), 1)
            self.assertEqual(Path(saved[0]).name, "a.txt")
            self.assertTrue((Path(td) / "folder").is_dir())
        self.assertEqual([c["index"] for c in calls], [1],
                         "directory entries must not trigger downloads")

    async def test_progress_cb_receives_cumulative_bytes(self) -> None:
        files = [{"name": "a.txt"}, {"name": "b.txt"}]
        responses = {0: b"111", 1: b"22"}
        calls: list = []
        seen: list = []

        async def cb(cum_bytes: int, saved_count: int) -> None:
            seen.append((cum_bytes, saved_count))

        with tempfile.TemporaryDirectory(prefix="xfer-fetch-") as td:
            saved = await _fetch_task_files(
                self._stub_dl(responses, calls), _store_received_files,
                files=files, file_urls=[], recv_dir=td, task_id="tk",
                token="tok", total=5, ip="203.0.113.7", port=1, ctx=None,
                raw_dir=Path(td), progress_cb=cb)
            self.assertEqual(len(saved), 2)
        self.assertEqual(seen, [(3, 1), (5, 2)])

if __name__ == "__main__":
    unittest.main()
