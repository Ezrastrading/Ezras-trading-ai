"""System execution lock + hard guard wiring."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from trading_ai.control.system_execution_lock import (
    assert_hard_execution_guard,
    ensure_system_execution_lock_file,
    require_live_execution_allowed,
)


def test_ensure_lock_creates_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    p = ensure_system_execution_lock_file(runtime_root=tmp_path)
    assert p.is_file()
    raw = json.loads(p.read_text(encoding="utf-8"))
    assert raw.get("system_locked") is True
    assert raw.get("gate_a_enabled") is True


def test_require_gate_b_disabled_by_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    ensure_system_execution_lock_file(runtime_root=tmp_path)
    ok, reason = require_live_execution_allowed("gate_b", runtime_root=tmp_path)
    assert ok is False
    assert "gate_b_disabled" in reason


def test_assert_hard_execution_guard_blocks() -> None:
    assert (
        assert_hard_execution_guard(
            chosen_product_id=None,
            error_code=None,
            quote_sufficient=True,
            runtime_allow=True,
        )
        == "chosen_product_id_required"
    )
    assert (
        assert_hard_execution_guard(
            chosen_product_id="BTC-USD",
            error_code="x",
            quote_sufficient=True,
            runtime_allow=True,
        )
        == "error_code:x"
    )


def test_validate_nt_entry_hard_guard_mocked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.control import system_execution_lock as sel

    sel._HARD_GUARD_CACHE.clear()

    class VR:
        resolution_status = "success"
        chosen_product_id = "BTC-USD"
        error_code = None
        diagnostics = {"candidate_attempts": []}

    monkeypatch.setattr(
        "trading_ai.nte.execution.routing.integration.validation_resolve.resolve_validation_product_coherent",
        lambda *a, **k: VR(),
    )
    from trading_ai.control.system_execution_lock import validate_nt_entry_hard_guard

    r = validate_nt_entry_hard_guard(MagicMock(), product_id="BTC-USD", quote_notional_usd=25.0, runtime_root=tmp_path)
    assert r.ok is True


def test_storage_adapter_writes_under_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.storage.storage_adapter import LocalStorageAdapter

    a = LocalStorageAdapter(runtime_root=tmp_path)
    a.write_text("data/reports/x.txt", "hi")
    assert (tmp_path / "data/reports/x.txt").read_text() == "hi"


def test_refresh_data_index(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    (tmp_path / "data" / "control").mkdir(parents=True)
    (tmp_path / "data" / "control" / "a.json").write_text("{}", encoding="utf-8")
    from trading_ai.control.data_index import refresh_data_index

    out = refresh_data_index(runtime_root=tmp_path)
    assert out.get("artifact_count", 0) >= 1
