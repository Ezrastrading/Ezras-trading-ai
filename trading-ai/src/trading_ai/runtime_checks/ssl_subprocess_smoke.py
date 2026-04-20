"""Helpers for subprocess vs in-process deployment smoke when SSL stack is incompatible."""

from __future__ import annotations

from typing import Any, Dict

from trading_ai.runtime_checks.ssl_guard import ssl_runtime_diagnostic


def deployment_subprocess_smoke_decision() -> Dict[str, Any]:
    """
    Returns whether ``python -m trading_ai.deployment`` subprocess smoke is reliable on this interpreter.

    Does not disable SSL enforcement — only documents skip/fallback for tests and operators.
    """
    d = ssl_runtime_diagnostic()
    ok = bool((d or {}).get("ssl_guard_would_pass"))
    return {
        "truth_version": "deployment_subprocess_smoke_decision_v1",
        "ssl_guard_would_pass": ok,
        "recommend_subprocess_smoke": ok,
        "subprocess_skip_reason": None if ok else "ssl_runtime_incompatible_for_isolated_python_minus_m_smoke",
        "fallback_recommendation": (
            "run_in_process_import_smoke: import trading_ai.deployment; import trading_ai.runtime_checks.ssl_guard"
            if not ok
            else None
        ),
        "honesty": "When OpenSSL is missing/broken, subprocess CLI smoke may fail opaquely — prefer ssl_runtime_diagnostic first.",
    }
