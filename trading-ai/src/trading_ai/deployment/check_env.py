"""
Operator-facing Coinbase credential env check (no secrets printed).

Safe for zsh, bash, and CI — no heredocs; stdout lines only.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Tuple

from trading_ai.deployment.operator_env_contracts import (
    COINBASE_KEY_ID_ENVS,
    COINBASE_PORTFOLIO_ID_ENV,
    COINBASE_PRIVATE_KEY_ENVS,
    coinbase_credentials_failure_payload,
    missing_coinbase_credential_env_vars,
)


def _status_one(name: str, *, optional: bool = False) -> Tuple[str, Dict[str, Any]]:
    raw = os.environ.get(name)
    val = (raw or "").strip()
    if not val:
        line = f"{name} = MISSING" + (" (optional)" if optional else "")
        return line, {"name": name, "present": False, "optional": optional}
    # Never print secret material — length only.
    line = f"{name} = SET (len={len(val)})"
    return line, {"name": name, "present": True, "len": len(val), "optional": optional}


def run_check_env() -> Dict[str, Any]:
    """
    Build stdout lines and a structured summary (no secret values).
    """
    lines: List[str] = []
    structured: Dict[str, Any] = {"vars": [], "coinbase_credentials_ok": False}

    lines.append("# Coinbase Advanced Trade JWT — required groups (one from each pair)")
    for n in COINBASE_KEY_ID_ENVS:
        s, meta = _status_one(n)
        lines.append(s)
        structured["vars"].append(meta)
    for n in COINBASE_PRIVATE_KEY_ENVS:
        s, meta = _status_one(n)
        lines.append(s)
        structured["vars"].append(meta)

    s, meta = _status_one(COINBASE_PORTFOLIO_ID_ENV, optional=True)
    lines.append(s)
    structured["vars"].append(meta)

    miss = missing_coinbase_credential_env_vars()
    structured["exact_missing_coinbase_env_vars"] = miss
    structured["coinbase_credentials_ok"] = len(miss) == 0
    if miss:
        fb = coinbase_credentials_failure_payload()
        structured["failure_code"] = fb["failure_code"]
        structured["next_step"] = fb["next_step"]
        lines.append("")
        lines.append(f"# {fb['failure_code']}")
        lines.append(f"# next_step: {fb['next_step']}")
    else:
        lines.append("")
        lines.append("# Coinbase credential groups satisfied (JWT key id + private present).")

    try:
        from trading_ai.runtime_checks.ssl_guard import ssl_runtime_diagnostic

        ssl_info = ssl_runtime_diagnostic()
        lines.append("")
        lines.append("# Python / SSL (urllib3 v2 requires OpenSSL 1.1.1+, not LibreSSL)")
        lines.append(f"# python_executable = {ssl_info['python_executable']}")
        lines.append(f"# ssl.OPENSSL_VERSION = {ssl_info['ssl_openssl_version']}")
        lines.append(f"# ssl_guard_would_pass = {ssl_info['ssl_guard_would_pass']}")
        structured["ssl_runtime"] = ssl_info
    except Exception as exc:
        structured["ssl_runtime"] = {"error": str(exc)}

    structured["lines"] = lines
    return structured


def format_check_env_lines() -> str:
    data = run_check_env()
    return "\n".join(data["lines"]) + "\n"


if __name__ == "__main__":
    import sys

    d = run_check_env()
    print("\n".join(d["lines"]) + "\n", end="")
    sys.exit(0 if d.get("coinbase_credentials_ok") else 12)
