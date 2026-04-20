"""
Repo vs runtime deployment parity (Procfile, env expectations, writable paths).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Set

from trading_ai.deployment.deployment_models import iso_now
from trading_ai.deployment.env_parity import scan_env_for_placeholders
from trading_ai.deployment.paths import deployment_data_dir
from trading_ai.nte.databank.local_trade_store import resolve_databank_root
from trading_ai.runtime_paths import ezras_runtime_root


def _ensure_ezras_runtime_env() -> None:
    root = ezras_runtime_root()
    if not (os.environ.get("EZRAS_RUNTIME_ROOT") or "").strip():
        os.environ["EZRAS_RUNTIME_ROOT"] = str(root)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _read_procfile() -> Dict[str, Any]:
    p = _repo_root() / "Procfile"
    if not p.is_file():
        return {"present": False, "path": str(p), "lines": []}
    lines = [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return {"present": True, "path": str(p), "lines": lines, "worker_command": lines[0] if lines else None}


def _template_env_keys() -> Set[str]:
    p = _repo_root() / ".env.template"
    if not p.is_file():
        return set()
    keys: Set[str] = set()
    for ln in p.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Z][A-Z0-9_]*)\s*=", ln.strip())
        if m:
            keys.add(m.group(1))
    return keys


REQUIRED_RUNTIME_ENVS = ("EZRAS_RUNTIME_ROOT",)

# Proof / live path: Supabase sync + Coinbase execution + explicit governance mode
PROOF_ENV_KEYS = (
    "SUPABASE_URL",
    "GOVERNANCE_ORDER_ENFORCEMENT",
    "LIVE_SINGLE_EXECUTION_VALIDATION_CONFIRM",
    "COINBASE_EXECUTION_ENABLED",
)


def _supabase_jwt_present() -> bool:
    return bool((os.environ.get("SUPABASE_KEY") or "").strip() or (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip())


def _coinbase_material_present() -> bool:
    key = (os.environ.get("COINBASE_API_KEY_NAME") or os.environ.get("COINBASE_API_KEY") or "").strip()
    pem = (os.environ.get("COINBASE_API_PRIVATE_KEY") or os.environ.get("COINBASE_API_SECRET") or "").strip()
    return bool(key and pem)


def _railway_section() -> Dict[str, Any]:
    return {
        "RAILWAY_ENVIRONMENT": (os.environ.get("RAILWAY_ENVIRONMENT") or "").strip() or None,
        "RAILWAY_PROJECT_ID": (os.environ.get("RAILWAY_PROJECT_ID") or "").strip() or None,
        "RAILWAY_SERVICE_ID": (os.environ.get("RAILWAY_SERVICE_ID") or "").strip() or None,
        "PORT": (os.environ.get("PORT") or "").strip() or None,
        "note": "No railway.toml in repo — set the same env vars on Railway as in .env; worker should match Procfile.",
    }


def _writable_paths() -> List[Dict[str, Any]]:
    _ensure_ezras_runtime_env()
    root = ezras_runtime_root()
    paths = [
        root,
        root / "data",
        root / "data" / "deployment",
        root / "data" / "learning",
        root / "data" / "review",
        root / "data" / "control",
        root / "data" / "trade_logs",
        root / "data" / "reality",
        root / "shark" / "state",
        root / "shark" / "logs",
        root / "shark" / "memory" / "global",
        root / "shark" / "nte" / "memory",
    ]
    out: List[Dict[str, Any]] = []
    for p in paths:
        try:
            p.mkdir(parents=True, exist_ok=True)
            ok = p.exists() and os.access(p, os.W_OK)
        except OSError:
            ok = False
        out.append({"path": str(p), "writable": ok})
    try:
        dr, src = resolve_databank_root()
        dr.mkdir(parents=True, exist_ok=True)
        out.append({"path": str(dr), "writable": os.access(dr, os.W_OK), "source": src})
    except Exception as exc:
        out.append({"path": None, "writable": False, "error": type(exc).__name__})
    return out


def run_deployment_parity_report(*, write_file: bool = True) -> Dict[str, Any]:
    """
    ``deployment_parity_ready`` when Procfile exists, runtime root set, proof envs present,
    no placeholders on critical keys, paths writable.

    Writes ``data/deployment/deployment_parity_report.json``.
    """
    deployment_data_dir().mkdir(parents=True, exist_ok=True)
    proc = _read_procfile()
    template_keys = _template_env_keys()

    missing_required: List[str] = []
    for k in REQUIRED_RUNTIME_ENVS:
        if not (os.environ.get(k) or "").strip():
            missing_required.append(k)

    proof_gaps: List[str] = []
    if not (os.environ.get("SUPABASE_URL") or "").strip():
        proof_gaps.append("SUPABASE_URL_missing")
    if not _supabase_jwt_present():
        proof_gaps.append("SUPABASE_KEY_or_SUPABASE_SERVICE_ROLE_KEY_missing")
    if not _coinbase_material_present():
        proof_gaps.append("coinbase_api_key_material_missing")
    if not (os.environ.get("GOVERNANCE_ORDER_ENFORCEMENT") or "").strip():
        proof_gaps.append("GOVERNANCE_ORDER_ENFORCEMENT_unset")

    ph_keys = (
        "SUPABASE_URL",
        "SUPABASE_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
        "COINBASE_API_KEY",
        "COINBASE_API_KEY_NAME",
        "COINBASE_API_PRIVATE_KEY",
        "COINBASE_API_SECRET",
    )
    placeholders = scan_env_for_placeholders(extra_keys=ph_keys)

    writ = _writable_paths()
    write_ok = all(x.get("writable") for x in writ if x.get("path"))

    parity_ok = (
        bool(proc.get("present"))
        and len(missing_required) == 0
        and len(placeholders) == 0
        and len(proof_gaps) == 0
        and write_ok
    )

    reasons: List[str] = []
    if not proc.get("present"):
        reasons.append("procfile_missing")
    if missing_required:
        reasons.append("missing_env:" + ",".join(missing_required))
    if proof_gaps:
        reasons.append("proof_env_gaps:" + ",".join(proof_gaps))
    if placeholders:
        reasons.append("placeholder_env:" + ",".join(p["key"] for p in placeholders))
    if not write_ok:
        reasons.append("path_not_writable")

    out: Dict[str, Any] = {
        "generated_at": iso_now(),
        "repo_root": str(_repo_root()),
        "procfile": proc,
        "env_template_key_count": len(template_keys),
        "required_runtime_envs_present": {k: bool((os.environ.get(k) or "").strip()) for k in REQUIRED_RUNTIME_ENVS},
        "proof_environment_keys": {k: bool((os.environ.get(k) or "").strip()) for k in PROOF_ENV_KEYS},
        "proof_environment_gaps": proof_gaps,
        "placeholders_found": placeholders,
        "railway_hints": _railway_section(),
        "deployment_parity_ready": parity_ok,
        "blocking_reasons": reasons,
        "writable_paths": writ,
        "expected_worker_start": proc.get("worker_command"),
    }

    p = deployment_data_dir() / "deployment_parity_report.json"
    if write_file:
        p.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    return out
