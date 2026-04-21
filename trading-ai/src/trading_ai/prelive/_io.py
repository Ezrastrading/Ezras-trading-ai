"""Shared artifact writers under data/control."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from trading_ai.storage.storage_adapter import LocalStorageAdapter


def write_control_json(name: str, payload: Dict[str, Any], *, runtime_root: Path | None = None) -> None:
    ad = LocalStorageAdapter(runtime_root=runtime_root)
    ad.write_json(f"data/control/{name}", payload)


def write_control_txt(name: str, text: str, *, runtime_root: Path | None = None) -> None:
    ad = LocalStorageAdapter(runtime_root=runtime_root)
    ad.write_text(f"data/control/{name}", text)
