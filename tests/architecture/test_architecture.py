from __future__ import annotations

import ast
import unittest
from pathlib import Path

from samyuj.features import FEATURES, resolve_lazy_symbol

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "samyuj"
TESTS_ROOT = REPO_ROOT / "tests"

BANNED_LEGACY_FILES: tuple[str, ...] = ()

BANNED_OVERFLOW_MODULES = (
    "dispatcher_ext.py",
    "dispatcher_comms.py",
    "catalog_services.py",
    "stream_controls.py",
    "stream_input_render.py",
    "gallery_helpers.py",
    "screenshare_display.py",
    "webrtc_signaling.py",
    "ws_framing.py",
    "clipboard_dialogs.py",
)

BANNED_IMPORT_SUBSTRINGS = (
    "PyQt6",
    "PySide6",
    "audio_sink",
    "AudioService",
    "ControlGui",
)

def _parse_file(py: Path) -> ast.AST:
    with py.open(encoding="utf-8") as fh:
        return ast.parse(fh.read(), filename=str(py))

def _iter_imports(tree: ast.AST) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            found.append(mod)
            for alias in node.names:
                found.append(f"{mod}.{alias.name}" if mod else alias.name)
    return found

def _module_parts(py: Path) -> tuple[str, ...]:
    rel = py.relative_to(SRC_ROOT.parent)
    if rel.name == "__init__.py":
        rel = rel.parent
    return rel.with_suffix("").parts

def _resolves_on_disk(dotted: str) -> bool:
    base = SRC_ROOT.parent.joinpath(*dotted.split("."))
    return base.with_suffix(".py").is_file() or (base / "__init__.py").is_file()

def _internal_import_targets(py: Path, tree: ast.AST) -> list[str]:
    parts = _module_parts(py)
    targets: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.extend(a.name for a in node.names if a.name.split(".")[0] == "samyuj")
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                anchor = parts if py.name == "__init__.py" else parts[:-1]
                if node.level - 1 > len(anchor):
                    continue
                base = anchor[: len(anchor) - (node.level - 1)]
                mod = ".".join(base + ((node.module,) if node.module else ()))
            else:
                mod = node.module or ""
            if mod.split(".")[0] != "samyuj":
                continue
            targets.append(mod)
            for alias in node.names:
                if alias.name != "*":
                    targets.append(f"{mod}.{alias.name}")
    return targets

def _module_dotted(py: Path) -> str:
    return ".".join(_module_parts(py))

def _module_source_path(dotted: str) -> Path | None:
    base = SRC_ROOT.parent.joinpath(*dotted.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None

def _defines_lazily(dotted: str) -> bool:
    path = _module_source_path(dotted)
    if path is None:
        return False
    tree = _parse_file(path)
    return any(
        isinstance(node, ast.FunctionDef) and node.name == "__getattr__"
        for node in tree.body
    )

def _names_in_target(target: ast.expr) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        found: set[str] = set()
        for elt in target.elts:
            found |= _names_in_target(elt)
        return found
    if isinstance(target, ast.Starred):
        return _names_in_target(target.value)
    return set()

def _bound_names(dotted: str) -> set[str]:
    path = _module_source_path(dotted)
    if path is None:
        return set()
    tree = _parse_file(path)
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                bound |= _names_in_target(target)
        elif isinstance(node, ast.AnnAssign):
            bound |= _names_in_target(node.target)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound.add((alias.asname or alias.name).split(".")[0])
    return bound

def _import_is_satisfied(target: str) -> bool:
    if _resolves_on_disk(target):
        return True
    parent, _, name = target.rpartition(".")
    if not parent or not name:
        return False
    return _defines_lazily(parent) or name in _bound_names(parent)

class ArchitectureRulesTest(unittest.TestCase):
    def test_no_legacy_monolith_or_dead_files_in_repo(self) -> None:
        for rel in BANNED_LEGACY_FILES:
            self.assertFalse((REPO_ROOT / rel).exists(), f"Banned legacy file still exists: {rel}")
        self.assertFalse((REPO_ROOT / "src" / "main.cpp").exists())
        self.assertFalse((REPO_ROOT / "src" / "sc_native_client.cpp").exists())
        for py in SRC_ROOT.rglob("*.py"):
            self.assertNotIn(
                py.name,
                BANNED_OVERFLOW_MODULES,
                f"Artificial ceiling-split overflow module should be unified into its parent class: {py}",
            )

    def test_no_banned_imports_across_codebase(self) -> None:
        for py in sorted(SRC_ROOT.rglob("*.py")):
            tree = _parse_file(py)
            for imp in _iter_imports(tree):
                for banned in BANNED_IMPORT_SUBSTRINGS:
                    self.assertNotIn(banned, imp, f"Banned import {banned!r} in {py}: {imp}")

    def test_every_internal_import_resolves_to_a_real_module_or_symbol(self) -> None:
        unresolved: list[str] = []
        for py in sorted(SRC_ROOT.rglob("*.py")):
            tree = _parse_file(py)
            for target in _internal_import_targets(py, tree):
                if not _import_is_satisfied(target):
                    unresolved.append(f"{py.relative_to(REPO_ROOT)} imports {target!r}")
        self.assertEqual(
            unresolved,
            [],
            "Unresolvable internal imports (module/symbol does not exist):\n  "
            + "\n  ".join(unresolved),
        )

    def test_layering_features_protocol_and_ui_boundaries(self) -> None:
        feat_tree = _parse_file(SRC_ROOT / "features.py")
        for imp in _iter_imports(feat_tree):
            for forbidden in ("samyuj.ui", "samyuj.services", "samyuj.session"):
                self.assertFalse(
                    imp.startswith(forbidden),
                    f"features.py must not import {forbidden}: found {imp}",
                )

        for py in sorted((SRC_ROOT / "protocol").rglob("*.py")):
            tree = _parse_file(py)
            for imp in _iter_imports(tree):
                for forbidden in ("samyuj.ui", "samyuj.services", "samyuj.session"):
                    self.assertFalse(
                        imp.startswith(forbidden),
                        f"protocol module {py} must not import {forbidden}: found {imp}",
                    )

    def test_no_production_monkey_patching_and_no_source_text_assertions_in_tests(self) -> None:
        for py in sorted(SRC_ROOT.rglob("*.py")):
            tree = _parse_file(py)
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr == "__dict__":
                    self.fail(f"Production monkey-patching (__dict__ access) found in {py}:{node.lineno}")
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "setdefault"
                    and isinstance(node.func.value, ast.Attribute)
                    and node.func.value.attr == "modules"
                ):
                    self.fail(f"sys.modules.setdefault monkey-patch found in {py}:{node.lineno}")

        for py in sorted(TESTS_ROOT.rglob("*.py")):
            tree = _parse_file(py)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    self.assertNotIn(
                        node.func.attr,
                        ("read_text", "getsource"),
                        f"Source-text inspection ({node.func.attr}) banned in tests: {py}:{node.lineno}",
                    )

    def test_feature_spec_lazy_imports_and_cohesive_domain_ownership(self) -> None:
        self.assertEqual(len(FEATURES), 12)
        for spec in FEATURES:
            page_cls = resolve_lazy_symbol(spec.gtk_page)
            svc_cls = resolve_lazy_symbol(spec.service_factory)
            self.assertIsNotNone(page_cls, f"Unresolved gtk_page for {spec.key}")
            self.assertIsNotNone(svc_cls, f"Unresolved service_factory for {spec.key}")
            expected_svc_mod = spec.service_factory.split(":")[0]
            self.assertEqual(
                svc_cls.__module__,
                expected_svc_mod,
                f"Service class {svc_cls.__name__} for {spec.key} must be defined directly in {expected_svc_mod}, not re-exported from {svc_cls.__module__}",
            )

    def test_solid_principles_composition_table_dispatch_and_gtk_facade_dip(self) -> None:
        lc_tree = _parse_file(SRC_ROOT / "session" / "lifecycle.py")
        rt_cls = [n for n in lc_tree.body if isinstance(n, ast.ClassDef) and n.name == "SamyujRuntime"][0]
        self.assertLessEqual(
            len(rt_cls.bases),
            4,
            f"SamyujRuntime should compose domain services rather than inheriting {len(rt_cls.bases)} mixins",
        )

        rt_tree = _parse_file(SRC_ROOT / "session" / "router.py")
        router_cls = [n for n in rt_tree.body if isinstance(n, ast.ClassDef) and n.name == "SessionRouterMixin"][0]
        post_hello = [m for m in router_cls.body if isinstance(m, ast.AsyncFunctionDef) and m.name == "_on_post_hello"][0]
        post_hello_lines = (post_hello.end_lineno or 0) - post_hello.lineno
        self.assertLessEqual(
            post_hello_lines,
            40,
            f"_on_post_hello must delegate to table-driven ServiceBus.dispatch_inbound_frame (got {post_hello_lines} lines)",
        )

        dp_tree = _parse_file(SRC_ROOT / "services" / "dispatcher.py")
        dp_cls = [n for n in dp_tree.body if isinstance(n, ast.ClassDef) and n.name == "RequestDispatcherMixin"][0]
        req_fn = [m for m in dp_cls.body if isinstance(m, ast.AsyncFunctionDef) and m.name == "request"][0]
        req_lines = (req_fn.end_lineno or 0) - req_fn.lineno
        self.assertLessEqual(
            req_lines,
            60,
            f"RequestDispatcherMixin.request() must use table-driven command routing (got {req_lines} lines)",
        )

        for py in sorted((SRC_ROOT / "ui" / "gtk").rglob("*.py")):
            tree = _parse_file(py)
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Attribute)
                    and node.attr == "h"
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "self"
                ):
                    self.fail(f"DIP violation: direct `self.h` access in GTK UI module {py}:{node.lineno} (use self.facade)")

    def test_zero_undefined_globals_via_compiler_symtable(self) -> None:
        import builtins
        import symtable

        builtin_names = set(dir(builtins)) | {
            "__file__", "__name__", "__doc__", "__package__", "TYPE_CHECKING", "__annotations__", "__class__"
        }

        def check_table(st: symtable.SymbolTable, mod_globals: set[str], enclosing_locals: set[str], missing: set[str]) -> None:
            cur_locals = set(enclosing_locals)
            for sym in st.get_symbols():
                if sym.is_local() or sym.is_parameter() or sym.is_imported() or sym.is_assigned() or sym.is_namespace():
                    cur_locals.add(sym.get_name())
            for sym in st.get_symbols():
                name = sym.get_name()
                if sym.is_referenced() and not (sym.is_local() or sym.is_parameter() or sym.is_imported() or sym.is_assigned()):
                    if name not in cur_locals and name not in mod_globals and name not in builtin_names:
                        missing.add(name)
            for child in st.get_children():
                next_enclosing = cur_locals if st.get_type() == "function" else enclosing_locals
                check_table(child, mod_globals, next_enclosing, missing)

        for py in sorted(SRC_ROOT.rglob("*.py")):
            with py.open(encoding="utf-8") as fh:
                src = fh.read()
            top = symtable.symtable(src, str(py), "exec")
            mod_globals = {
                s.get_name()
                for s in top.get_symbols()
                if s.is_local() or s.is_imported() or s.is_assigned() or s.is_namespace()
            }
            missing: set[str] = set()
            check_table(top, mod_globals, set(), missing)
            self.assertEqual(missing, set(), f"Undefined global symbols in {py}: {sorted(missing)}")

    def test_call_site_signatures_match_definitions(self) -> None:
        import inspect
        from samyuj.session.lifecycle import DOMAIN_CONTROLLER_CLASSES, SamyujRuntime
        from samyuj.ui.facade import ApplicationFacade

        rt_sigs: dict[str, tuple[inspect.Signature, bool]] = {}
        for cls in (SamyujRuntime, ApplicationFacade) + DOMAIN_CONTROLLER_CLASSES:
            for mro_cls in cls.__mro__:
                if mro_cls is object:
                    continue
                for name, val in vars(mro_cls).items():
                    if callable(val) and not name.startswith("__"):
                        try:
                            rt_sigs.setdefault(name, (inspect.signature(val), isinstance(val, staticmethod)))
                        except Exception:
                            pass

        for py in sorted(SRC_ROOT.rglob("*.py")):
            tree = _parse_file(py)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    recv = ast.unparse(node.func.value)
                    if recv not in ("self.h", "self.facade", "self.runtime", "rt"):
                        continue
                    mname = node.func.attr
                    if mname not in rt_sigs:
                        continue
                    sig, is_static = rt_sigs[mname]
                    params = list(sig.parameters.values())
                    if not is_static and params and params[0].name == "self":
                        params = params[1:]
                    has_varargs = any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in params)
                    has_varkw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params)
                    valid_kw = {
                        p.name
                        for p in params
                        if p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
                    }
                    for kw in node.keywords:
                        if kw.arg is not None and not has_varkw:
                            self.assertIn(
                                kw.arg,
                                valid_kw,
                                f"Invalid keyword arg {kw.arg!r} for {recv}.{mname} at {py}:{node.lineno} (sig: {sig})",
                            )
                    max_pos = sum(
                        1
                        for p in params
                        if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
                    )
                    has_star = any(isinstance(a, ast.Starred) for a in node.args)
                    if not has_star and not has_varargs:
                        self.assertLessEqual(
                            len(node.args),
                            max_pos,
                            f"Too many positional args ({len(node.args)} > {max_pos}) for {recv}.{mname} at {py}:{node.lineno}",
                        )

if __name__ == "__main__":
    unittest.main()

