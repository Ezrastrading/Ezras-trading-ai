"""Synthetic avenue/gate registration and auto-attach metadata."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def rt(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    return tmp_path


def test_register_avenue_and_gate_overlay_merged(rt: Path) -> None:
    from trading_ai.multi_avenue.avenue_factory import register_avenue
    from trading_ai.multi_avenue.avenue_registry import merged_avenue_definitions
    from trading_ai.multi_avenue.gate_factory import register_gate
    from trading_ai.multi_avenue.gate_registry import merged_gate_rows

    register_avenue(
        {
            "avenue_id": "Z",
            "avenue_name": "synthetic",
            "display_name": "Synthetic Z",
            "venue_name": "unknown",
            "market_type": "unknown",
            "wiring_status": "scaffold_only",
            "notes": "test",
            "gates": [],
        },
        runtime_root=rt,
    )
    avs = merged_avenue_definitions(runtime_root=rt)
    assert any(a["avenue_id"] == "Z" for a in avs)
    register_gate("Z", "gate_z", runtime_root=rt)
    rows = merged_gate_rows(runtime_root=rt)
    assert any(g["avenue_id"] == "Z" and g["gate_id"] == "gate_z" for g in rows)
