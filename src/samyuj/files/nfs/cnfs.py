from __future__ import annotations
import json
import posixpath

import contextlib
import os
import shutil
import struct
from pathlib import Path

from samyuj.protocol.nfs.xdr import (
    NFS3_OK,
    NFS3ERR_ACCES,
    NFS3ERR_EXIST,
    NFS3ERR_IO,
    NFS3ERR_ISDIR,
    NFS3ERR_NOENT,
    NFS3ERR_ROFS,
    XdrReader as _Xdr,
    XdrWriter as _XdrWriter,
    cnfs_create_file_name,
    xopaque as _xop,
    xstr as _xstr,
)

class CnfsProceduresMixin:

    def _normalize_cnfs_rel(self, raw_path: str) -> str:
        s = (raw_path or "").replace("\\", "/").strip()
        if s.endswith(".suffix_thm"):
            s = s[:-len(".suffix_thm")]
        ann = "/" + (self.announced_path or "").strip("/")
        if ann != "/" and (s == ann or s.startswith(ann + "/")):
            s = s[len(ann):]
        elif s == "/sdcard" or s.startswith("/sdcard/"):
            s = s[len("/sdcard"):]
        return s.strip("/")

    def _resolve_cnfs_path(self, raw_path: str) -> tuple[str, Path] | None:
        rel = self._normalize_cnfs_rel(raw_path)
        p = self.export.path(rel)
        if p is None:
            return None
        return rel, p

    def _cnfs_bump_revision(self, why: str = "") -> None:
        with contextlib.suppress(Exception):
            exp = getattr(self, "export", None)
            if exp is None:
                return
            old = int(getattr(exp, "revision", 1) or 1)
            exp.revision = old + 1
            self.stats["cnfs_mutations"] = int(self.stats.get("cnfs_mutations", 0)) + 1
            self._ev("NFS_EXPORT_REVISION",
                     f"{old} -> {old + 1} ({why or 'cnfs mutation'})")

    def _entries_for_dir(self, drel: str, dp: Path) -> list[tuple[str, str, Path]]:
        out: list[tuple[str, str, Path]] = []
        seen_names: set[str] = set()
        if drel == "":
            for alias, s_entry in sorted(self.export.shares.items(), key=lambda kv: kv[0].lower()):
                if getattr(s_entry, "enabled", True) and s_entry.host_path.is_dir():
                    out.append((alias, alias, s_entry.host_path))
                    seen_names.add(alias)
        if dp.is_dir():
            with contextlib.suppress(OSError):
                for p in sorted(dp.iterdir(), key=lambda q: q.name.lower()):
                    if p.name in seen_names:
                        continue
                    rel = posixpath.join(drel, p.name) if drel else p.name
                    out.append((p.name, rel, p))
        return out

    def _cnfs_proc_read_thumbnail(self, call: dict, x: _Xdr, _peer) -> bytes:
        if len(call["args"]) < 8:
            return self._ok(call, struct.pack(">II", NFS3_OK, 0))
        quality = max(10, min(95, int(x.u32() or 75)))
        raw_path = x.opaque().decode("utf-8", "replace")
        got = self._resolve_cnfs_path(raw_path)
        if not got or not got[1].exists():
            return self._ok(call, struct.pack(">I", 17))
        _rel, fp = got
        if not fp.is_file():
            return self._ok(call, struct.pack(">I", 100))
        thumb_bytes = b""
        with contextlib.suppress(Exception):
            from PIL import Image
            import io as _io
            with Image.open(fp) as im:
                im_rgb = im.convert("RGB")
                im_rgb.thumbnail((256, 256))
                buf = _io.BytesIO()
                im_rgb.save(buf, format="JPEG", quality=quality)
                thumb_bytes = buf.getvalue()
        if not thumb_bytes and fp.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp") and fp.stat().st_size <= 512 * 1024:
            with contextlib.suppress(OSError):
                thumb_bytes = fp.read_bytes()
        if not thumb_bytes:
            return self._ok(call, struct.pack(">I", 100))
        return self._ok(call, struct.pack(">I", NFS3_OK) + _xop(thumb_bytes))

    def _cnfs_proc_read_category_dir(self, call: dict, x: _Xdr, _peer) -> bytes:
        if len(call["args"]) < 16:
            return self._ok(call, struct.pack(">II", NFS3_OK, 0))
        fh = self._fh_of(x)
        offset = max(0, int(x.u32()))
        limit = max(1, min(5000, int(x.u32() or 200)))
        category = int(x.u32())
        got = self._resolve(fh)
        base_rel, base_p = got if got else ("", self.export.root)
        img_exts = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".heic"}
        vid_exts = {".mp4", ".mkv", ".webm", ".avi", ".mov", ".3gp"}
        aud_exts = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac"}
        doc_exts = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".md", ".csv"}
        zip_exts = {".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz"}

        def _smart_share_ftype(ext_s: str, is_dir_flag: bool = False) -> int:
            if is_dir_flag:
                return 1
            for flag, exts in ((2, img_exts), (4, vid_exts), (8, aud_exts), (16, doc_exts), (32, zip_exts)):
                if ext_s in exts:
                    return flag
            return 0

        cat_exts = {
            1: img_exts, 2: vid_exts, 3: aud_exts, 4: doc_exts, 5: zip_exts, 7: img_exts | vid_exts,
        }.get(category)
        matched: list[dict] = []
        if category == 6:
            for name, vrel, child_p in self._entries_for_dir(base_rel, base_p):
                if name.startswith("."):
                    continue
                try:
                    st = child_p.stat()
                    is_d = child_p.is_dir()
                except OSError:
                    continue
                fid = str(self.export.hid_for_rel(vrel))
                matched.append({
                    "fileId": fid, "path": "/" + vrel.lstrip("/"), "name": name,
                    "type": _smart_share_ftype(child_p.suffix.lower(), is_dir_flag=is_d),
                    "fileSize": 0 if is_d else int(st.st_size),
                    "modifyDate": int(st.st_mtime * 1000), "createdDate": int(st.st_ctime * 1000),
                    "isLive": 0, "iCloud": 0, "isSnapshot": 0,
                })
                if len(matched) >= offset + limit:
                    break
        else:
            roots_to_scan: list[tuple[str, Path]] = []
            if base_rel == "":
                roots_to_scan.append(("", self.export.root))
                for alias, s_entry in sorted(self.export.shares.items()):
                    if s_entry.is_available():
                        roots_to_scan.append((alias, s_entry.host_path))
            else:
                roots_to_scan.append((base_rel, base_p))
            for prefix_rel, rpath in roots_to_scan:
                if not rpath.is_dir():
                    continue
                for root_s, dirs, files in os.walk(rpath, followlinks=False):
                    dirs[:] = [d for d in dirs if not d.startswith(".")]
                    rp = Path(root_s)
                    for fn in files:
                        if fn.startswith("."):
                            continue
                        fp = rp / fn
                        if fp.is_symlink():
                            continue
                        ext = fp.suffix.lower()
                        if cat_exts is not None and ext not in cat_exts:
                            continue
                        try:
                            st = fp.stat()
                        except OSError:
                            continue
                        sub = str(fp.relative_to(rpath)).replace(os.sep, "/")
                        vrel = f"{prefix_rel}/{sub}".strip("/") if prefix_rel else sub
                        fid = str(self.export.hid_for_rel(vrel))
                        matched.append({
                            "fileId": fid, "path": "/" + vrel, "name": fn,
                            "type": _smart_share_ftype(ext, is_dir_flag=False),
                            "fileSize": int(st.st_size),
                            "modifyDate": int(st.st_mtime * 1000), "createdDate": int(st.st_ctime * 1000),
                            "isLive": 0, "iCloud": 0, "isSnapshot": 0,
                        })
                        if len(matched) >= offset + limit:
                            break
                    if len(matched) >= offset + limit:
                        break
        sliced = matched[offset:offset + limit]
        w = _XdrWriter()
        w.u32(NFS3_OK)
        use_json = bool(self.nfs_capability & 8)
        for item in sliced:
            w.boolean(True)
            if use_json:
                w.string(json.dumps(item, separators=(",", ":")))
            else:
                w.string(item["fileId"]).string(item["path"]).string(item["name"])
                w.u32(item["type"]).u64(item["fileSize"])
                w.u64(item["modifyDate"]).u64(item["createdDate"])
                if self.nfs_version >= 7:
                    w.u32(0).u32(0)
        w.boolean(False)
        return self._ok(call, w.bytes())

    def _cnfs_proc_getattr_path(self, call: dict, x: _Xdr, _peer) -> bytes:
        if len(call["args"]) < 4:
            return self._ok(call, struct.pack(">I", NFS3_OK))
        raw_path = x.opaque().decode("utf-8", "replace")
        got = self._resolve_cnfs_path(raw_path)
        if not got or not got[1].exists():
            return self._ok(call, struct.pack(">I", NFS3ERR_NOENT))
        rel, fp = got
        return self._ok(call, struct.pack(">I", NFS3_OK) + self.export.fattr3(fp, rel=rel))

    def _cnfs_proc_read_path(self, call: dict, x: _Xdr, _peer) -> bytes:
        if len(call["args"]) < 16:
            root_name = (self.announced_path or "").strip("/").split("/")[-1] or "Samyuj"
            return self._ok(call, struct.pack(">I", NFS3_OK) + _xstr(root_name))
        off = x.u64()
        cnt = x.u32()
        raw_path = x.opaque().decode("utf-8", "replace")
        got = self._resolve_cnfs_path(raw_path)
        if not got or not got[1].exists():
            return self._ok(call, struct.pack(">I", NFS3ERR_NOENT) + self.export.post_op_attr(None))
        rel, fp = got
        if not fp.is_file():
            return self._ok(call, struct.pack(">I", NFS3ERR_ISDIR) + self.export.post_op_attr(fp, rel=rel))
        cnt = max(0, min(cnt or self.read_max, self.read_max))
        try:
            with open(fp, "rb") as f:
                f.seek(off)
                blob = f.read(cnt)
        except OSError:
            return self._ok(call, struct.pack(">I", NFS3ERR_IO) + self.export.post_op_attr(fp, rel=rel))
        eof = (off + len(blob)) >= fp.stat().st_size
        self.stats["reads"] += 1
        w = _XdrWriter()
        w.u32(NFS3_OK).fixed(self.export.post_op_attr(fp, rel=rel))
        w.u32(len(blob)).boolean(eof).opaque(blob)
        return self._ok(call, w.bytes())

    def _cnfs_proc_create_path(self, call: dict, x: _Xdr, peer) -> bytes:
        if len(call["args"]) < 4:
            return self._ok(call, struct.pack(">I", NFS3_OK) + _xstr(""))
        raw_path = x.opaque().decode("utf-8", "replace")
        rel = self._normalize_cnfs_rel(raw_path)
        if not rel or self.export.is_readonly(rel):
            return self._ok(call, struct.pack(">I", NFS3ERR_ROFS))
        target_p = self.export.path(rel)
        if target_p is None:
            return self._ok(call, struct.pack(">I", NFS3ERR_ACCES))
        want_dir = str(raw_path or "").rstrip().endswith(("/", "\\"))
        try:
            target_p.parent.mkdir(parents=True, exist_ok=True)
            if want_dir:
                cand_p = target_p
                if cand_p.exists():
                    cand_p = target_p.parent / cnfs_create_file_name(target_p)
                cand_p.mkdir(parents=True, exist_ok=False)
            else:
                cand_p = target_p.parent / cnfs_create_file_name(target_p)
                with open(cand_p, "xb"):
                    pass
        except FileExistsError:
            return self._ok(call, struct.pack(">I", NFS3ERR_EXIST))
        except OSError as exc:
            self._ev("NFS_CREATEPATH_FAIL", f"peer={peer} {rel} {exc!r}", rel)
            return self._ok(call, struct.pack(">I", NFS3ERR_IO))
        self._cnfs_bump_revision(f"createpath {rel}")
        self._ev("NFS_CREATEPATH", f"peer={peer} {rel} -> {cand_p.name} ({'dir' if want_dir else 'file'})", rel)
        return self._ok(call, struct.pack(">I", NFS3_OK) + _xstr(cand_p.name))

    def _cnfs_proc_remove_batch(self, call: dict, x: _Xdr, peer) -> bytes:
        if len(call["args"]) < 4:
            return self._ok(call, struct.pack(">I", NFS3ERR_IO))
        raw_json = x.opaque().decode("utf-8", "replace")
        try:
            paths_list = json.loads(raw_json)
            if not isinstance(paths_list, list):
                paths_list = []
        except Exception:
            paths_list = []
        if not paths_list:
            return self._ok(call, struct.pack(">I", NFS3ERR_IO))
        results: list[int] = []
        failed: list[str] = []
        changed = False
        for p_item in paths_list:
            raw_item = str(p_item or "")
            rel = self._normalize_cnfs_rel(raw_item)
            if not rel or self.export.is_readonly(rel):
                results.append(0)
                failed.append(f"{raw_item}(readonly/outside-export)")
                continue
            got = self._resolve_cnfs_path(raw_item)
            if not got or not got[1].exists():
                results.append(0)
                failed.append(f"{raw_item}(missing)")
                continue
            _r, fp = got
            try:
                if fp.is_dir() and not fp.is_symlink():
                    shutil.rmtree(fp)
                else:
                    fp.unlink()
                results.append(1)
                changed = True
            except OSError as exc:
                results.append(0)
                failed.append(f"{raw_item}({exc.__class__.__name__})")
        res_str = json.dumps(results, separators=(",", ":"))
        if changed:
            self._cnfs_bump_revision(f"removebatch {sum(results)}/{len(results)}")
        self._ev(
            "NFS_REMOVEBATCH",
            f"peer={peer} count={len(paths_list)} ok={sum(results)}" + (f" failed={failed[:6]}" if failed else ""),
            paths_list[0] if paths_list else None,
        )
        return self._ok(call, struct.pack(">I", NFS3_OK) + _xstr(res_str))

    def _cnfs_proc_noimp(self, call: dict, _x: _Xdr, peer) -> bytes:
        self._ev("NFS_NOIMP", f"peer={peer} proc=28 (procedureNOIMP)")
        return self._proc_unavail(call)

    def _cnfs_proc_file_change_notify(self, call: dict, x: _Xdr, peer) -> bytes:
        if len(call["args"]) >= 8:
            with contextlib.suppress(Exception):
                cpath = x.opaque().decode("utf-8", "replace")
                ctype = x.u32()
                self._ev("NFS_FILE_CHANGE_NOTIFY", f"peer={peer} path={cpath} type={ctype}")
        return self._ok(call, struct.pack(">I", NFS3_OK))

    _CNFS_PROC_TABLE = {
        22: _cnfs_proc_read_thumbnail,
        23: _cnfs_proc_read_category_dir,
        24: _cnfs_proc_getattr_path,
        25: _cnfs_proc_read_path,
        26: _cnfs_proc_create_path,
        27: _cnfs_proc_remove_batch,
        28: _cnfs_proc_noimp,
        29: _cnfs_proc_file_change_notify,
        40014: _cnfs_proc_file_change_notify,
        40015: _cnfs_proc_file_change_notify,
        40016: _cnfs_proc_file_change_notify,
    }

    def _do_cnfs(self, call: dict, proc: int, peer) -> bytes:
        x = _Xdr(call["args"])
        handler = self._CNFS_PROC_TABLE.get(proc)
        try:
            if handler is not None:
                return handler(self, call, x, peer)
        except Exception as exc:
            self._ev("CNFS_ERR", f"peer={peer} proc={proc} {exc!r}")
            return self._ok(call, struct.pack(">I", NFS3ERR_IO))
        return self._proc_unavail(call)
