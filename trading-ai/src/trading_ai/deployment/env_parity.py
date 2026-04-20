"""
Environment / deployment parity: placeholders, runtime root, required keys, optional Railway hints.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.deployment.paths import deployment_data_dir, env_parity_report_path
from trading_ai.deployment.deployment_models import iso_now
from trading_ai.nte.paths import nte_memory_dir
from trading_ai.runtime_paths import ezras_runtime_root


_PLACEHOLDER_PATTERNS = (
    re.compile(r"YOUR_[A-Z0-9_]+", re.I),
    re.compile(r"PASTE[_\s]", re.I),
    re.compile(r"REPLACE[_\s]", re.I),
    re.compile(r"changeme", re.I),
    re.compile(r"example\.com", re.I),
    re.compile(r"<.*>", re.I),
)


def _looks_placeholder(val: str) -> bool:
    s = (val or "").strip()
    if not s:
        return False
    for pat in _PLACEHOLDER_PATTERNS:
        if pat.search(s):
            return True
    if s.startswith("sk-test-placeholder") or s == "test":
        return True
    return False


def scan_env_for_placeholders(
    *,
    extra_keys: Optional[Tuple[str, ...]] = None,
) -> List[Dict[str, Any]]:
    keys = {
        "SUPABASE_URL",
        "SUPABASE_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
        "COINBASE_API_KEY",
        "COINBASE_API_KEY_NAME",
        "COINBASE_API_PRIVATE_KEY",
        "COINBASE_API_SECRET",
        "GOVERNANCE_ORDER_ENFORCEMENT",
        "EZRAS_RUNTIME_ROOT",
    }
    if extra_keys:
        keys |= set(extra_keys)
    hits: List[Dict[str, Any]] = []
    for k in sorted(keys):
        v = os.environ.get(k)
        if v is None:
            continue
        if _looks_placeholder(str(v)):
            hits.append({"key": k, "snippet": str(v)[:80]})
    return hits


def _railway_hint() -> Dict[str, Any]:
    out: Dict[str, Any] = {"railway_project_hint": False, "note": None}
    cwd = Path.cwd()
    for name in ("railway.toml", "railway.json", ".railway"):
        if (cwd / name).exists():
            out["railway_project_hint"] = True
            break
    if os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_PROJECT_ID"):
        out["railway_project_hint"] = True
    if out["railway_project_hint"]:
        out["note"] = "Railway-style env detected — compare required keys with production variables."
    return out


def run_env_parity_report(*, write_file: bool = True) -> Dict[str, Any]:
    """
    Detect placeholder-like env values, missing persistent root, databank path reachability.
    Writes ``data/deployment/env_parity_report.json``.
    """
    root = ezras_runtime_root()
    deployment_data_dir().mkdir(parents=True, exist_ok=True)

    placeholders = scan_env_for_placeholders()
    root_ok = root.exists() and os.access(root, os.W_OK)
    nte_ok = False
    try:
        p = nte_memory_dir()
        nte_ok = p.exists() or True
        p.mkdir(parents=True, exist_ok=True)
        nte_ok = os.access(p, os.W_OK)
    except OSError:
        nte_ok = False

    drift: List[str] = []
    er = (os.environ.get("EZRAS_RUNTIME_ROOT") or "").strip()
    if er and Path(er).resolve() != root.resolve():
        drift.append("EZRAS_RUNTIME_ROOT differs from resolved ezras_runtime_root()")

    payload: Dict[str, Any] = {
        "generated_at": iso_now(),
        "runtime_root": str(root),
        "runtime_root_persistent": root_ok,
        "nte_memory_writable": nte_ok,
        "placeholders_found": placeholders,
        "shell_env_drift_notes": drift,
        "railway": _railway_hint(),
        "env_parity_ok": len(placeholders) == 0 and root_ok and nte_ok,
    }
    if not payload["env_parity_ok"]:
        reasons = []
        if placeholders:
            reasons.append("placeholder_like_env_values")
        if not root_ok:
            reasons.append("runtime_root_not_writable")
        if not nte_ok:
            reasons.append("nte_memory_not_writable")
        payload["blocking_reasons"] = reasons
    else:
        payload["blocking_reasons"] = []

    if write_file:
        env_parity_report_path().write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return payload
