"""
Runtime validation for databank / EZRAS isolation — produces ``databank_isolation_report.json``.

Does not enable live trading. Fails closed on missing roots or unusable paths.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from trading_ai.nte.databank.local_trade_store import DatabankRootUnsetError, resolve_databank_root


def _writable_dir(p: Path) -> Tuple[bool, str]:
    try:
        p.mkdir(parents=True, exist_ok=True)
        probe = p / ".isolation_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True, "ok"
    except OSError as e:
        return False, str(e)


def _load_trade_ids_from_jsonl(path: Path) -> Set[str]:
    ids: Set[str] = set()
    if not path.is_file():
        return ids
    from trading_ai.nte.databank.local_trade_store import load_jsonl_trade_ids

    return load_jsonl_trade_ids(path)


def run_databank_isolation_validation(
    *,
    runtime_root: Optional[Path] = None,
    expect_empty_databank: bool = False,
    session_trade_id_prefix: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Validate EZRAS + databank resolution, writability, optional empty JSONL, foreign id hints.

    ``session_trade_id_prefix`` — if set, any trade_id in databank not starting with this prefix
    yields a contamination_warning (foreign session bleed heuristic).
    """
    ez = (os.environ.get("EZRAS_RUNTIME_ROOT") or "").strip()
    issues: List[str] = []
    warnings: List[str] = []

    if not ez and runtime_root is None:
        issues.append("EZRAS_RUNTIME_ROOT_unset")
    if runtime_root is not None:
        os.environ["EZRAS_RUNTIME_ROOT"] = str(Path(runtime_root).resolve())

    try:
        db_path, db_src = resolve_databank_root()
    except DatabankRootUnsetError as e:
        return {
            "schema": "databank_isolation_report_v1",
            "ok": False,
            "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "error": str(e),
            "issues": issues + ["databank_root_unresolved"],
        }

    rt = Path(os.environ.get("EZRAS_RUNTIME_ROOT") or runtime_root or "").resolve()
    wr_ok, wr_detail = _writable_dir(db_path)
    if not wr_ok:
        issues.append(f"databank_not_writable:{wr_detail}")

    te = db_path / "trade_events.jsonl"
    n_lines = 0
    if te.is_file():
        n_lines = len([ln for ln in te.read_text(encoding="utf-8").splitlines() if ln.strip()])
    if expect_empty_databank and n_lines > 0:
        warnings.append(f"expect_empty_databank_but_trade_events_has_{n_lines}_lines")

    ids = _load_trade_ids_from_jsonl(te)
    if session_trade_id_prefix:
        foreign = sorted(tid for tid in ids if not str(tid).startswith(session_trade_id_prefix))
        if foreign:
            warnings.append(f"possible_foreign_trade_ids:{foreign[:20]}")

    roots_consistent = str(db_path.resolve()).startswith(str(rt.resolve())) if rt and db_src == "EZRAS_RUNTIME_ROOT/databank" else True
    if db_src == "TRADE_DATABANK_MEMORY_ROOT" and rt:
        # Explicit databank may live outside runtime tree — not an error; note only.
        if not str(db_path.resolve()).startswith(str(rt.resolve())):
            warnings.append("databank_root_outside_runtime_tree_explicit_TRADE_DATABANK_MEMORY_ROOT")

    out: Dict[str, Any] = {
        "schema": "databank_isolation_report_v1",
        "ok": len(issues) == 0 and wr_ok,
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ezras_runtime_root": str(rt) if rt else None,
        "databank_root": str(db_path),
        "databank_root_source": db_src,
        "databank_writable": wr_ok,
        "databank_writable_detail": wr_detail,
        "trade_events_jsonl_line_count": n_lines,
        "trade_events_distinct_ids": len(ids),
        "roots_consistent_with_ezras": roots_consistent,
        "issues": issues,
        "warnings": warnings,
        "contamination_hints": {
            "foreign_id_warnings": [w for w in warnings if w.startswith("possible_foreign")],
            "expect_empty_violation": [w for w in warnings if w.startswith("expect_empty")],
        },
    }
    return out


def write_databank_isolation_report(
    runtime_root: Path,
    *,
    expect_empty_databank: bool = False,
    session_trade_id_prefix: Optional[str] = None,
) -> Path:
    """Write ``<runtime_root>/isolation_proof/databank_isolation_report.json``."""
    runtime_root = runtime_root.resolve()
    payload = run_databank_isolation_validation(
        runtime_root=runtime_root,
        expect_empty_databank=expect_empty_databank,
        session_trade_id_prefix=session_trade_id_prefix,
    )
    out_dir = runtime_root / "isolation_proof"
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / "databank_isolation_report.json"
    p.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return p
