"""Kalshi separate runtime root contract — isolation paths and warnings."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from trading_ai.nte.memory.store import MemoryStore
from trading_ai.runtime_proof.kalshi_process_contract import (
    build_kalshi_isolation_report,
    build_kalshi_process_readiness,
    write_kalshi_process_artifacts,
)


def test_kalshi_runtime_roots_isolated_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    k = tmp_path / "kalshi_only"
    k.mkdir()
    monkeypatch.setenv("KALSHI_RUNTIME_ROOT", str(k))
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path / "ezras"))
    r = build_kalshi_process_readiness()
    assert r["kalshi_runtime_root"] == str(k.resolve())
    assert r["contamination_checks"]["same_root_as_coinbase_session"] is False
    assert r["isolated_databank_under_kalshi_root"] == str(k / "databank")


def test_shared_root_surfaces_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("KALSHI_RUNTIME_ROOT", str(tmp_path))
    store = MemoryStore()
    store.ensure_defaults()
    rep = build_kalshi_isolation_report(runtime_root=tmp_path, nte_store=store)
    assert rep["warnings"]
    assert any("equals EZRAS_RUNTIME_ROOT" in w for w in rep["warnings"])


def test_write_kalshi_process_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.delenv("KALSHI_RUNTIME_ROOT", raising=False)
    paths = write_kalshi_process_artifacts(tmp_path)
    for p in paths.values():
        json.loads(Path(p).read_text(encoding="utf-8"))
