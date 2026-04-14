"""Filesystem paths for truth / uncertainty artifacts (operator-local runtime)."""

from __future__ import annotations

from pathlib import Path

from trading_ai.automation.risk_bucket import runtime_root


def uncertainty_registry_path() -> Path:
    return runtime_root() / "state" / "uncertainty_registry.json"
