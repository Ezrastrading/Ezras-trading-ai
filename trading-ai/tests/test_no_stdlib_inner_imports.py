"""
Regression: redundant inner stdlib imports (shadowing / duplicate lines) in deployment + hot paths.

Intentional lazy imports of *project* modules (trading_ai.*) are ignored.
Allowlisted tuples: (relative_path_under_src, function_name or "*", "reason").
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SRC = _REPO / "src"

_STDLIB_TOP = frozenset({"os", "json", "time", "sys", "traceback"})
_DATETIME_MODULE = "datetime"

# (path suffix under trading_ai/, function name or "*", note)
INNER_STDLIB_IMPORT_ALLOWLIST: Tuple[Tuple[str, str, str], ...] = ()

_SCAN_GLOBS = (
    "trading_ai/deployment/*.py",
    "trading_ai/orchestration/avenue_a_live_daemon.py",
    "trading_ai/runtime_proof/live_execution_validation.py",
    "trading_ai/orchestration/supervised_avenue_a_truth.py",
    "trading_ai/deployment/check_env.py",
)


def _norm_import(node: ast.Import | ast.ImportFrom) -> List[Tuple[str, Optional[str]]]:
    out: List[Tuple[str, Optional[str]]] = []
    if isinstance(node, ast.Import):
        for a in node.names:
            base = (a.name or "").split(".")[0]
            out.append((base, None))
        return out
    if isinstance(node, ast.ImportFrom):
        mod = (node.module or "").split(".")[0]
        if not mod:
            return out
        for a in node.names:
            if a.name == "*":
                continue
            out.append((mod, a.name))
        return out
    return out


def _module_level_stdlib_keys(mod: ast.Module) -> Set[Tuple[str, Optional[str]]]:
    keys: Set[Tuple[str, Optional[str]]] = set()
    for stmt in mod.body:
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            for pair in _norm_import(stmt):
                top, _ = pair
                if top in _STDLIB_TOP or top == _DATETIME_MODULE:
                    keys.add(pair)
    return keys


def _stdlib_imports_skipping_nested_functions(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> List[Tuple[str, Optional[str], int]]:
    """Imports inside fn body, excluding nested function/class definitions."""
    inner: List[Tuple[str, Optional[str], int]] = []
    stack: List[ast.AST] = list(fn.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for pair in _norm_import(node):
                top, _ = pair
                if top in _STDLIB_TOP or top == _DATETIME_MODULE:
                    inner.append((*pair, node.lineno))
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            stack.append(ch)
    return inner


def _allowed(rel: str, fn_name: str) -> bool:
    for p, f, _ in INNER_STDLIB_IMPORT_ALLOWLIST:
        if p != rel:
            continue
        if f == "*" or f == fn_name:
            return True
    return False


def _scan_file(path: Path) -> List[str]:
    rel = str(path.relative_to(_SRC))
    text = path.read_text(encoding="utf-8")
    try:
        mod = ast.parse(text)
    except SyntaxError as exc:
        return [f"{rel}: syntax error {exc}"]

    mod_keys = _module_level_stdlib_keys(mod)
    issues: List[str] = []

    def _functions(mod_: ast.Module) -> List[ast.FunctionDef | ast.AsyncFunctionDef]:
        out: List[ast.FunctionDef | ast.AsyncFunctionDef] = []
        for stmt in mod_.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.append(stmt)
            elif isinstance(stmt, ast.ClassDef):
                for sub in stmt.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        out.append(sub)
        return out

    for node in _functions(mod):
        inner = _stdlib_imports_skipping_nested_functions(node)
        by_key: Dict[Tuple[str, Optional[str]], List[int]] = {}
        for top, name, ln in inner:
            key = (top, name)
            by_key.setdefault(key, []).append(ln)

        for key, lines in by_key.items():
            if len(lines) > 1:
                issues.append(f"{rel}:{node.name}: duplicate inner import {key} at lines {lines}")

        for top, name, ln in inner:
            key = (top, name)
            if key in mod_keys and not _allowed(rel, node.name):
                issues.append(
                    f"{rel}:{node.name}: inner import {key} at line {ln} shadows module-level stdlib import"
                )

    return issues


def test_no_redundant_inner_stdlib_in_target_files() -> None:
    problems: List[str] = []
    for pattern in _SCAN_GLOBS:
        for path in sorted(_SRC.glob(pattern)):
            if path.name == "__init__.py":
                continue
            problems.extend(_scan_file(path))
    assert not problems, "Inner stdlib import issues:\n" + "\n".join(problems)
