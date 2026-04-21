"""Canonical on-disk locations for bot governance artifacts (additive; does not touch live execution)."""

from __future__ import annotations

import os
from pathlib import Path

from trading_ai.runtime_paths import ezras_runtime_root


def default_bot_registry_path() -> Path:
    raw = (os.environ.get("EZRAS_BOT_REGISTRY_PATH") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (Path(__file__).resolve().parent / "bot_registry.json").resolve()


def global_layer_governance_dir() -> Path:
    """Budget counters, experiments index, etc."""
    raw = (os.environ.get("EZRAS_GOVERNANCE_DIR") or "").strip()
    if raw:
        p = Path(raw).expanduser().resolve()
    else:
        # Deployed/runtime-safe default: keep governance artifacts under runtime root.
        # This prevents code deploys from overwriting append-only audit streams like tasks.jsonl.
        try:
            root = Path(os.environ.get("EZRAS_RUNTIME_ROOT") or ezras_runtime_root()).resolve()
            p = root / "data" / "governance" / "global_layer"
        except Exception:
            # Last-resort dev fallback: repo-local directory.
            p = Path(__file__).resolve().parent / "_governance_data"
    p.mkdir(parents=True, exist_ok=True)
    return p
