"""Canonical paths under ``EZRAS_RUNTIME_ROOT/shark/nte``."""

from __future__ import annotations

from pathlib import Path

from trading_ai.governance.storage_architecture import shark_data_dir


def nte_root() -> Path:
    p = shark_data_dir() / "nte"
    p.mkdir(parents=True, exist_ok=True)
    return p


def nte_memory_dir() -> Path:
    p = nte_root() / "memory"
    p.mkdir(parents=True, exist_ok=True)
    return p


def nte_data_dir() -> Path:
    p = nte_root() / "data"
    p.mkdir(parents=True, exist_ok=True)
    return p


def nte_coinbase_positions_path() -> Path:
    from trading_ai.governance.storage_architecture import shark_state_path

    return shark_state_path("nte_coinbase_positions.json")


def nte_failure_log_path() -> Path:
    p = nte_memory_dir() / "failure_log.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def nte_system_health_path() -> Path:
    p = nte_memory_dir() / "system_health.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def nte_capital_ledger_path() -> Path:
    p = nte_memory_dir() / "capital_ledger.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def nte_promotion_log_path() -> Path:
    p = nte_memory_dir() / "promotion_log.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def nte_ceo_action_log_path() -> Path:
    p = nte_memory_dir() / "ceo_action_log.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def nte_unresolved_issues_path() -> Path:
    p = nte_memory_dir() / "unresolved_issues.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p
