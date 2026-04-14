"""
Operator and doctrine approval registry (local JSON + markdown log, optional encryption).

Signing uses HMAC-SHA256 when ``EZRAS_REGISTRY_HMAC_KEY`` is set; otherwise record
fingerprint is SHA-256 of canonical JSON (integrity id, not secret authentication).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.automation.risk_bucket import runtime_root
from trading_ai.governance.system_doctrine import (
    DoctrineVerdict,
    compute_doctrine_sha256,
    doctrine_signature,
    verify_doctrine_integrity,
)
from trading_ai.security.encryption_at_rest import (
    encryption_available,
    encrypt_json_file,
    read_json_maybe_encrypted,
)

logger = logging.getLogger(__name__)
_lock = threading.Lock()

_STATE_VERSION = 1
STATE_NAME = "operator_registry.json"
LOG_NAME = "operator_registry_log.md"


def _state_path() -> Path:
    return runtime_root() / "state" / STATE_NAME


def _log_path() -> Path:
    return runtime_root() / "logs" / LOG_NAME


def _canonical(obj: Dict[str, Any]) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _sign_record(obj: Dict[str, Any]) -> str:
    raw = _canonical(obj)
    key = (__import__("os").environ.get("EZRAS_REGISTRY_HMAC_KEY") or "").strip().encode()
    if key:
        return hmac.new(key, raw.encode("utf-8"), hashlib.sha256).hexdigest()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _default_state() -> Dict[str, Any]:
    return {
        "version": _STATE_VERSION,
        "operators": [],
        "doctrine_approvals": [],
    }


def load_registry() -> Dict[str, Any]:
    p = _state_path()
    if not p.is_file():
        return _default_state()
    try:
        raw = read_json_maybe_encrypted(p)
        if not isinstance(raw, dict):
            return _default_state()
        out = _default_state()
        out.update(raw)
        out.setdefault("operators", [])
        out.setdefault("doctrine_approvals", [])
        return out
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.warning("operator_registry load failed: %s", exc)
        return _default_state()


def _save_state(data: Dict[str, Any]) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    if encryption_available():
        encrypt_json_file(p, data)
    else:
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        tmp.replace(p)


def _append_md(block: str) -> None:
    try:
        lp = _log_path()
        lp.parent.mkdir(parents=True, exist_ok=True)
        with open(lp, "a", encoding="utf-8") as f:
            f.write(block)
    except OSError as exc:
        logger.warning("operator_registry md log failed: %s", exc)


def register_operator(
    *,
    operator_id: str,
    role: str,
    signing_key_id: str = "",
) -> Dict[str, Any]:
    """Register or reactivate an operator record."""
    with _lock:
        st = load_registry()
        fp = hashlib.sha256(f"{operator_id}:{role}".encode()).hexdigest()[:32]
        rec = {
            "operator_id": operator_id.strip(),
            "role": role.strip(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "active": True,
            "signing_fingerprint": signing_key_id or fp,
        }
        rec["record_signature"] = _sign_record({k: v for k, v in rec.items() if k != "record_signature"})
        found = False
        for i, op in enumerate(st["operators"]):
            if op.get("operator_id") == rec["operator_id"]:
                st["operators"][i] = rec
                found = True
                break
        if not found:
            st["operators"].append(rec)
        _save_state(st)
        _append_md(f"\n## register_operator {rec['created_at']}\n\n```json\n{json.dumps(rec, indent=2)}\n```\n")
        return {"ok": True, "operator": rec}


def approve_doctrine(
    *,
    operator_id: str,
    doctrine_version: str,
    notes: str = "",
) -> Dict[str, Any]:
    """Record operator approval of current canonical doctrine hash (from running code)."""
    sha = compute_doctrine_sha256()
    with _lock:
        st = load_registry()
        ids = {o.get("operator_id") for o in st["operators"] if o.get("active")}
        if operator_id not in ids:
            return {"ok": False, "error": "unknown_or_inactive_operator", "operators_known": list(ids)}
        for d in st["doctrine_approvals"]:
            if d.get("status") == "active":
                d["status"] = "superseded"
                d["superseded_at"] = datetime.now(timezone.utc).isoformat()
        appr = {
            "id": secrets.token_hex(8),
            "doctrine_version": doctrine_version,
            "doctrine_sha256": sha,
            "approved_by_operator_id": operator_id,
            "approved_at": datetime.now(timezone.utc).isoformat(),
            "status": "active",
            "notes": notes,
        }
        appr["record_signature"] = _sign_record({k: v for k, v in appr.items() if k != "record_signature"})
        st["doctrine_approvals"].append(appr)
        _save_state(st)
        _append_md(f"\n## approve_doctrine {appr['approved_at']}\n\n```json\n{json.dumps(appr, indent=2)}\n```\n")
        return {"ok": True, "approval": appr}


def registry_status() -> Dict[str, Any]:
    st = load_registry()
    active_doctrine = next(
        (d for d in st["doctrine_approvals"] if d.get("status") == "active"),
        None,
    )
    return {
        "path": str(_state_path()),
        "log_path": str(_log_path()),
        "operator_count": len(st["operators"]),
        "active_operators": [o["operator_id"] for o in st["operators"] if o.get("active")],
        "active_doctrine_approval": active_doctrine,
        "encryption_at_rest": encryption_available(),
    }


def verify_doctrine_with_registry() -> Dict[str, Any]:
    """
    Combine module hash check with optional operator-approved doctrine record.

    Bootstrap: no registry / no approval → passes if ``verify_doctrine_integrity`` passes.
    If ``EZRAS_DOCTRINE_REGISTRY_REQUIRED=1``, an active approval matching current sha is required.
    """
    integ = verify_doctrine_integrity()
    if integ.verdict == "HALT":
        return {
            "ok": False,
            "mode": "module_hash",
            "integrity_verdict": integ.to_dict(),
            "registry_required": False,
        }

    st = load_registry()
    active = next((d for d in st["doctrine_approvals"] if d.get("status") == "active"), None)
    current_sha = compute_doctrine_sha256()
    required = __import__("os").environ.get("EZRAS_DOCTRINE_REGISTRY_REQUIRED", "0") in ("1", "true", "True")

    if not active:
        if required:
            return {
                "ok": False,
                "mode": "registry_required_missing_approval",
                "integrity_verdict": integ.to_dict(),
                "registry_required": True,
                "hint": "Run: python -m trading_ai consistency register-operator ... && approve-doctrine ...",
            }
        return {
            "ok": True,
            "mode": "bootstrap_no_registry",
            "integrity_verdict": integ.to_dict(),
            "current_sha256": current_sha,
            "registry_required": False,
        }

    if active.get("doctrine_sha256") != current_sha:
        return {
            "ok": False,
            "mode": "registry_sha_mismatch",
            "integrity_verdict": integ.to_dict(),
            "approval": active,
            "current_sha256": current_sha,
            "registry_required": required,
        }

    oid = active.get("approved_by_operator_id")
    op_ok = any(o.get("operator_id") == oid and o.get("active") for o in st["operators"])
    if not op_ok:
        return {
            "ok": False,
            "mode": "approver_not_active_operator",
            "approval": active,
        }

    return {
        "ok": True,
        "mode": "registry_approved",
        "integrity_verdict": integ.to_dict(),
        "active_approval": active,
        "current_sha256": current_sha,
        "registry_required": required,
    }


def verify_doctrine_registry_verdict() -> DoctrineVerdict:
    """Map registry verification to DoctrineVerdict (HALT when strict checks fail)."""
    r = verify_doctrine_with_registry()
    ts = datetime.now(timezone.utc)
    sig = doctrine_signature()
    if r.get("ok"):
        return DoctrineVerdict(
            verdict="ALIGNED",
            rule_triggered="doctrine_registry_ok",
            severity="INFO",
            evidence=r,
            timestamp=ts,
            signed_by=sig,
            escalation_required=False,
        )
    mode = r.get("mode", "unknown")
    return DoctrineVerdict(
        verdict="HALT",
        rule_triggered=f"doctrine_registry_{mode}",
        severity="HALT",
        evidence=r,
        timestamp=ts,
        signed_by=sig,
        escalation_required=True,
    )
