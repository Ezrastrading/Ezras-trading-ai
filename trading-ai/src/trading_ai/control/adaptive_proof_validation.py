"""
Validate adaptive proof JSON artifacts (existence, schema, freshness, proof_source).
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Reject stale proofs older than this unless explicitly overridden (seconds).
_DEFAULT_MAX_AGE_SEC = 14 * 24 * 3600

_EXPECTED_PROOF_SOURCE_PREFIX = "trading_ai."


def _parse_generated_at(payload: Dict[str, Any]) -> Optional[float]:
    raw = payload.get("generated_at")
    if raw is None:
        ts = payload.get("ts")
        if isinstance(ts, (int, float)):
            return float(ts)
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        try:
            from datetime import datetime

            if raw.endswith("Z"):
                raw = raw[:-1] + "+00:00"
            return datetime.fromisoformat(raw).timestamp()
        except (TypeError, ValueError):
            return None
    return None


def validate_adaptive_live_proof_file(
    path: Path,
    *,
    max_age_sec: float = _DEFAULT_MAX_AGE_SEC,
) -> Dict[str, Any]:
    """
    Returns dict with keys: ok, errors, warnings, proof_source, generated_at_epoch, size_bytes, mtime_epoch.
    """
    path = path.resolve()
    err: list[str] = []
    warn: list[str] = []
    out: Dict[str, Any] = {
        "ok": False,
        "errors": err,
        "warnings": warn,
        "path": str(path),
        "proof_source": None,
        "generated_at_epoch": None,
        "size_bytes": 0,
        "mtime_epoch": None,
    }
    if not path.is_file():
        err.append("file_missing")
        return out
    try:
        st = path.stat()
        out["size_bytes"] = int(st.st_size)
        out["mtime_epoch"] = float(st.st_mtime)
    except OSError as exc:
        err.append(f"stat_error:{exc}")
        return out
    if st.st_size < 32:
        err.append("file_too_small_or_empty")
        return out
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        err.append(f"json_error:{exc}")
        return out
    if not isinstance(raw, dict):
        err.append("root_not_object")
        return out
    ps = raw.get("proof_source")
    out["proof_source"] = ps
    if not ps or not isinstance(ps, str):
        err.append("missing_proof_source")
    elif not str(ps).startswith(_EXPECTED_PROOF_SOURCE_PREFIX):
        err.append("proof_source_not_trading_ai_module_path")

    gen = _parse_generated_at(raw)
    out["generated_at_epoch"] = gen
    if gen is None:
        err.append("missing_generated_at")
    else:
        age = time.time() - gen
        if age > max_age_sec:
            warn.append(f"proof_may_be_stale_age_sec={int(age)}")

    if raw.get("current_operating_mode") is None and raw.get("mode") is None:
        err.append("missing_operating_mode")
    if raw.get("allow_new_trades") is None:
        err.append("missing_allow_new_trades")

    if not err:
        out["ok"] = True
    return out


def validate_adaptive_routing_proof_file(
    path: Path,
    *,
    max_age_sec: float = _DEFAULT_MAX_AGE_SEC,
) -> Dict[str, Any]:
    path = path.resolve()
    err: list[str] = []
    warn: list[str] = []
    out: Dict[str, Any] = {
        "ok": False,
        "errors": err,
        "warnings": warn,
        "path": str(path),
        "proof_source": None,
        "allocation_source": None,
        "generated_at_epoch": None,
        "size_bytes": 0,
        "mtime_epoch": None,
    }
    if not path.is_file():
        err.append("file_missing")
        return out
    try:
        st = path.stat()
        out["size_bytes"] = int(st.st_size)
        out["mtime_epoch"] = float(st.st_mtime)
    except OSError as exc:
        err.append(f"stat_error:{exc}")
        return out
    if st.st_size < 16:
        err.append("file_too_small_or_empty")
        return out
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        err.append(f"json_error:{exc}")
        return out
    if not isinstance(raw, dict):
        err.append("root_not_object")
        return out
    out["proof_source"] = raw.get("proof_source")
    src = raw.get("allocation_source") or raw.get("route_source")
    out["allocation_source"] = src
    if not src:
        err.append("missing_allocation_source")
    elif str(src) not in ("adaptive_route", "fallback_static_route"):
        err.append("allocation_source_unexpected")

    ps = raw.get("proof_source")
    if not ps or not isinstance(ps, str):
        err.append("missing_proof_source")
    elif not str(ps).startswith(_EXPECTED_PROOF_SOURCE_PREFIX):
        err.append("proof_source_not_trading_ai_module_path")

    gen = _parse_generated_at(raw)
    out["generated_at_epoch"] = gen
    if gen is None:
        err.append("missing_generated_at")
    else:
        age = time.time() - gen
        if age > max_age_sec:
            warn.append(f"proof_may_be_stale_age_sec={int(age)}")

    rga = raw.get("recommended_gate_allocations")
    if not isinstance(rga, dict) or rga.get("gate_a") is None:
        err.append("missing_recommended_gate_allocations")

    if not err:
        out["ok"] = True
    return out


def readiness_adaptive_proofs_ok(
    live_path: Path,
    routing_path: Path,
    *,
    max_age_sec: float = _DEFAULT_MAX_AGE_SEC,
) -> Tuple[bool, Dict[str, Any]]:
    """Both proofs valid → checklist items 3 and 4 can be true."""
    a = validate_adaptive_live_proof_file(live_path, max_age_sec=max_age_sec)
    r = validate_adaptive_routing_proof_file(routing_path, max_age_sec=max_age_sec)
    return (
        bool(a.get("ok")) and bool(r.get("ok")),
        {"adaptive_live": a, "adaptive_routing": r},
    )
