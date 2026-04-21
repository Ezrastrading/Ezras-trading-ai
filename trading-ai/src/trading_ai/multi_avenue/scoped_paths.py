"""Filesystem layout for scoped artifacts — mirrors legacy flat paths, never replaces them."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from trading_ai.runtime_paths import ezras_runtime_root


def system_control_dir(runtime_root: Optional[Path] = None) -> Path:
    p = Path(runtime_root or ezras_runtime_root()) / "data" / "control" / "system"
    p.mkdir(parents=True, exist_ok=True)
    return p


def avenue_control_dir(avenue_id: str, runtime_root: Optional[Path] = None) -> Path:
    safe = "".join(c for c in str(avenue_id) if c.isalnum() or c in ("_", "-"))[:32]
    p = Path(runtime_root or ezras_runtime_root()) / "data" / "control" / "avenues" / safe
    p.mkdir(parents=True, exist_ok=True)
    return p


def gate_control_dir(avenue_id: str, gate_id: str, runtime_root: Optional[Path] = None) -> Path:
    gsafe = "".join(c for c in str(gate_id) if c.isalnum() or c in ("_", "-"))[:48]
    p = avenue_control_dir(avenue_id, runtime_root=runtime_root) / "gates" / gsafe
    p.mkdir(parents=True, exist_ok=True)
    return p


def avenue_review_dir(avenue_id: str, runtime_root: Optional[Path] = None) -> Path:
    safe = "".join(c for c in str(avenue_id) if c.isalnum() or c in ("_", "-"))[:32]
    p = Path(runtime_root or ezras_runtime_root()) / "data" / "review" / "avenues" / safe
    p.mkdir(parents=True, exist_ok=True)
    return p


def gate_review_dir(avenue_id: str, gate_id: str, runtime_root: Optional[Path] = None) -> Path:
    gsafe = "".join(c for c in str(gate_id) if c.isalnum() or c in ("_", "-"))[:48]
    p = avenue_review_dir(avenue_id, runtime_root=runtime_root) / "gates" / gsafe
    p.mkdir(parents=True, exist_ok=True)
    return p


def avenue_learning_dir(avenue_id: str, runtime_root: Optional[Path] = None) -> Path:
    safe = "".join(c for c in str(avenue_id) if c.isalnum() or c in ("_", "-"))[:32]
    p = Path(runtime_root or ezras_runtime_root()) / "data" / "learning" / "avenues" / safe
    p.mkdir(parents=True, exist_ok=True)
    return p


def legacy_flat_control(runtime_root: Optional[Path] = None) -> Path:
    """Existing runtime layout — unchanged."""
    p = Path(runtime_root or ezras_runtime_root()) / "data" / "control"
    p.mkdir(parents=True, exist_ok=True)
    return p
