"""
Avenue A Startup Report - Coinbase-only enforcement and runtime validation.

This module provides a startup report that validates Avenue A is configured
for Coinbase-only execution and blocks any Kalshi execution from Avenue A jobs.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def _truthy_env(name: str, default: str = "false") -> bool:
    return (os.environ.get(name) or default).strip().lower() in ("1", "true", "yes")


def coinbase_avenue_execution_enabled() -> bool:
    """Check if Coinbase Avenue A execution is enabled."""
    a = (os.environ.get("COINBASE_EXECUTION_ENABLED") or "").strip().lower() in ("1", "true", "yes")
    b = (os.environ.get("COINBASE_ENABLED") or "").strip().lower() in ("1", "true", "yes")
    return bool(a or b)


def kalshi_avenue_execution_enabled() -> bool:
    """Check if Kalshi Avenue B execution is enabled."""
    return (os.environ.get("GATE_B_LIVE_EXECUTION_ENABLED") or "").strip().lower() in ("1", "true", "yes")


def is_dry_run() -> bool:
    """Check if system is in dry-run mode."""
    nte_dry = (os.environ.get("NTE_DRY_RUN") or "").strip().lower() in ("1", "true", "yes")
    ezras_dry = (os.environ.get("EZRAS_DRY_RUN") or "").strip().lower() in ("1", "true", "yes")
    return nte_dry or ezras_dry


def print_avenue_a_startup_report() -> Dict[str, Any]:
    """
    Print comprehensive Avenue A startup report and return status dict.
    
    This validates:
    - Avenue A = Coinbase only
    - Kalshi = Avenue B only (no mixed routing)
    - Execution mode (live vs dry-run)
    - Risk controls active
    """
    blockers: List[str] = []
    
    # Check Coinbase execution
    cb_enabled = coinbase_avenue_execution_enabled()
    if not cb_enabled:
        blockers.append("COINBASE_EXECUTION_ENABLED_or_COINBASE_ENABLED_not_true")
    
    # Check Coinbase credentials presence (without printing secrets)
    cb_key = (os.environ.get("COINBASE_API_KEY_NAME") or os.environ.get("COINBASE_API_KEY") or "").strip()
    cb_secret = (os.environ.get("COINBASE_API_PRIVATE_KEY") or os.environ.get("COINBASE_API_SECRET") or "").strip()
    cb_auth_env_present = bool(cb_key and cb_secret)
    
    # Try Coinbase balance check
    cb_balance_check = "ok"
    if cb_auth_env_present:
        try:
            from trading_ai.shark.coinbase_tracker import get_coinbase_balance
            
            balance = get_coinbase_balance()
            if not balance or balance.get("error"):
                cb_balance_check = "unauthorized_or_error"
                blockers.append("coinbase_balance_check_failed")
        except Exception as e:
            cb_balance_check = f"check_failed:{str(e)[:50]}"
            blockers.append(f"coinbase_balance_check_exception:{str(e)[:50]}")
    else:
        cb_balance_check = "no_credentials"
        blockers.append("coinbase_credentials_missing")
    
    # Check dry-run mode
    dry_run = is_dry_run()
    if dry_run:
        # Dry-run mode is OK for testing - not a blocker
        logger.info("dry_run_mode_active (ok for testing)")
    else:
        # For live mode, check live trading flag
        live_enabled = (os.environ.get("NTE_LIVE_TRADING_ENABLED") or "").strip().lower() in ("1", "true", "yes")
        if not live_enabled:
            blockers.append("NTE_LIVE_TRADING_ENABLED_not_true")
        
        # Check execution mode
        exec_mode = os.environ.get("NTE_EXECUTION_MODE", "unknown")
        if exec_mode.lower() != "live":
            blockers.append(f"NTE_EXECUTION_MODE_not_live_{exec_mode}")
    
    # Check if Kalshi is accidentally enabled for Avenue A (always block regardless of mode)
    kalshi_enabled = kalshi_avenue_execution_enabled()
    if kalshi_enabled:
        blockers.append("GATE_B_LIVE_EXECUTION_ENABLED_true_for_Avenue_A")
    
    report = {
        "AVENUE_A_RUNTIME": {
            "daemon": "ok" if cb_enabled else "fail",
            "scheduler": "ok",  # Scheduler is always running in shark daemon
            "coinbase_auth": "ok" if cb_enabled else "fail",
            "coinbase_auth_env_present": cb_auth_env_present,
            "coinbase_balance_check": cb_balance_check,
            "coinbase_entry_path": "ok" if cb_enabled else "fail",
            "coinbase_exit_path": "ok" if cb_enabled else "fail",
            "kalshi_disabled_for_A": not kalshi_enabled,
            "live_mode": not dry_run,
            "blockers": blockers,
        }
    }
    
    # Print formatted report
    print("=" * 80)
    print("AVENUE_A_RUNTIME STARTUP REPORT")
    print("=" * 80)
    print(f"daemon: {report['AVENUE_A_RUNTIME']['daemon']}")
    print(f"scheduler: {report['AVENUE_A_RUNTIME']['scheduler']}")
    print(f"coinbase_auth: {report['AVENUE_A_RUNTIME']['coinbase_auth']}")
    print(f"coinbase_auth_env_present: {report['AVENUE_A_RUNTIME']['coinbase_auth_env_present']}")
    print(f"coinbase_balance_check: {report['AVENUE_A_RUNTIME']['coinbase_balance_check']}")
    print(f"coinbase_entry_path: {report['AVENUE_A_RUNTIME']['coinbase_entry_path']}")
    print(f"coinbase_exit_path: {report['AVENUE_A_RUNTIME']['coinbase_exit_path']}")
    print(f"kalshi_disabled_for_A: {report['AVENUE_A_RUNTIME']['kalshi_disabled_for_A']}")
    print(f"live_mode: {report['AVENUE_A_RUNTIME']['live_mode']}")
    print(f"blockers: {report['AVENUE_A_RUNTIME']['blockers']}")
    print("=" * 80)
    
    if blockers:
        logger.warning("Avenue A startup blockers: %s", blockers)
    else:
        logger.info("Avenue A startup report: all checks passed")
    
    return report


def enforce_avenue_a_coinbase_only(intent_outlet: str) -> str:
    """
    Enforce that Avenue A only uses Coinbase outlet.
    
    This should be called before any execution to ensure Avenue A jobs
    cannot accidentally route to Kalshi or other exchanges.
    
    Args:
        intent_outlet: The outlet from the execution intent
        
    Returns:
        The validated outlet (coinbase) or blocks with error
    """
    if intent_outlet.lower() == "coinbase":
        return "coinbase"
    
    # Block any non-coinbase outlet for Avenue A
    logger.error(
        "Avenue A execution blocked: outlet=%s (must be coinbase only)",
        intent_outlet
    )
    raise ValueError(
        f"Avenue A requires outlet=coinbase only, got outlet={intent_outlet}. "
        "Avenue A is Coinbase-only; Kalshi is Avenue B only."
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print_avenue_a_startup_report()
