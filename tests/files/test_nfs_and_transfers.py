from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from samyuj.core.identity import load_or_create_identity, make_server_ssl_context
from samyuj.files import (
    MultiShareExport,
    NfsRpcServer,
    PhoneFileWidgetConfig,
    PhoneFileWidgetController,
    PhoneNfsClient,
    build_phonefs_client_ctx,
)
from samyuj.protocol.nfs.xdr import PROG_MOUNT, PROG_NFS

class NfsAndFileSharingTest(unittest.IsolatedAsyncioTestCase):
    async def test_multi_share_export_and_nfs_tls_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sc-nfs-test-") as td:
            base = Path(td)
            root_dir = base / "root_export"
            docs_dir = base / "docs_share"
            ro_dir = base / "vault_ro"
            root_dir.mkdir()
            docs_dir.mkdir()
            ro_dir.mkdir()
            (docs_dir / "note.txt").write_text("hello nfs3", encoding="utf-8")
            (ro_dir / "secret.txt").write_text("read-only data", encoding="utf-8")

            export = MultiShareExport(root_dir)
            export.add_share("Docs", docs_dir, access_mode="rw")
            export.add_share("VaultRO", ro_dir, access_mode="ro")

            shares = export.list_shares()
            aliases = {s["alias"] for s in shares}
            self.assertIn("Docs", aliases)
            self.assertIn("VaultRO", aliases)

            ident = load_or_create_identity(base / "state")
            srv_ctx = make_server_ssl_context(ident)
            srv = NfsRpcServer(export, announced_path="/C/nfs_demo")
            pm_port = await srv.start_listener("127.0.0.1", 0, srv_ctx)
            mnt_port = await srv.start_listener("127.0.0.1", 0, srv_ctx)
            nfs_port = await srv.start_listener("127.0.0.1", 0, srv_ctx)
            srv.portmap = {PROG_MOUNT: mnt_port, PROG_NFS: nfs_port}

            try:
                cli_ctx = build_phonefs_client_ctx(ident.cert_pem, ident.key_pem)
                client = PhoneNfsClient(
                    host="127.0.0.1",
                    portmap_port=pm_port,
                    nfs_port=nfs_port,
                    server_fp=ident.fingerprint.upper(),
                    ssl_ctx=cli_ctx,
                    mount_path="/C/nfs_demo",
                )
                await client.connect()
                try:
                    root_entries = await client.listdir("/")
                    names = {e["name"] for e in root_entries}
                    self.assertIn("Docs", names)
                    self.assertIn("VaultRO", names)

                    doc_entries = await client.listdir("/Docs")
                    self.assertEqual([e["name"] for e in doc_entries], ["note.txt"])

                    data = await client.read_file("/Docs/note.txt")
                    self.assertEqual(data, b"hello nfs3")

                    await client.write_file("/Docs/created.txt", b"written over nfs3")
                    self.assertEqual((docs_dir / "created.txt").read_bytes(), b"written over nfs3")
                finally:
                    await client.close()
            finally:
                await srv.stop()

    async def test_write_range_and_truncate_over_tls_nfs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sc-nfs-wr-") as td:
            base = Path(td)
            root_dir = base / "root_export"
            docs_dir = base / "docs_share"
            root_dir.mkdir()
            docs_dir.mkdir()

            export = MultiShareExport(root_dir)
            export.add_share("Docs", docs_dir, access_mode="rw")

            ident = load_or_create_identity(base / "state")
            srv_ctx = make_server_ssl_context(ident)
            srv = NfsRpcServer(export, announced_path="/C/nfs_demo")
            pm_port = await srv.start_listener("127.0.0.1", 0, srv_ctx)
            mnt_port = await srv.start_listener("127.0.0.1", 0, srv_ctx)
            nfs_port = await srv.start_listener("127.0.0.1", 0, srv_ctx)
            srv.portmap = {PROG_MOUNT: mnt_port, PROG_NFS: nfs_port}

            try:
                cli_ctx = build_phonefs_client_ctx(ident.cert_pem, ident.key_pem)
                client = PhoneNfsClient(
                    host="127.0.0.1",
                    portmap_port=pm_port,
                    nfs_port=nfs_port,
                    server_fp=ident.fingerprint.upper(),
                    ssl_ctx=cli_ctx,
                    mount_path="/C/nfs_demo",
                )
                await client.connect()
                try:
                    await client.write_file("/Docs/rw.txt", b"")
                    await client.write_range("/Docs/rw.txt", 0, b"hello ")
                    await client.write_range("/Docs/rw.txt", 6, b"world")
                    self.assertEqual((docs_dir / "rw.txt").read_bytes(), b"hello world")
                    self.assertEqual(await client.read_file("/Docs/rw.txt"), b"hello world")
                    st = await client.stat("/Docs/rw.txt")
                    self.assertEqual(int(st.get("size") or 0), 11)
                    await client.truncate("/Docs/rw.txt", 0)
                    st0 = await client.stat("/Docs/rw.txt")
                    self.assertEqual(int(st0.get("size") or 0), 0)
                finally:
                    await client.close()
            finally:
                await srv.stop()

    async def test_phone_file_widget_controller_mock_tree(self) -> None:
        ctrl = PhoneFileWidgetController(None)
        ctrl.mock_fs_tree = {
            "/DCIM/Camera": [
                {"name": "IMG_20261022_120000.jpg", "kind": "file", "size": 4096, "mtime": 1700000000},
                {"name": "VID_20261022_120500.mp4", "kind": "file", "size": 8192, "mtime": 1700000100},
            ]
        }
        cfg = PhoneFileWidgetConfig(
            widget_id="w1",
            source_id="gallery",
            remote_path="/DCIM/Camera",
        )
        res = await ctrl.fetch_widget_state(cfg)
        self.assertEqual(res.status, "ready")
        self.assertEqual(len(res.entries), 2)
