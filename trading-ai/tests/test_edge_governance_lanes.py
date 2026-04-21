from __future__ import annotations

from pathlib import Path

from trading_ai.nte.execution.edge_governance import (
    EdgeSignal,
    EdgeType,
    decide_lane_and_strategy,
    detect_gate_b_edges,
    detect_gate_a_edges,
)


def test_gate_a_no_edge_blocks(tmp_path: Path) -> None:
    closes = [100.0] * 30
    edges = detect_gate_a_edges(
        closes=closes,
        feat={"mid": 100.0, "spread_pct": 0.005, "z_score": 0.0, "regime": "range", "quote_volume_24h": 1_000_000},
    )
    d = decide_lane_and_strategy(
        runtime_root=tmp_path,
        gate_id="gate_a",
        candidate_product="BTC-USD",
        candidate_strategy_id="",
        edges=edges,
        estimated_fees_bps=15.0,
        estimated_slippage_bps=10.0,
        spread_bps=50.0,
    )
    assert d["lane"] == "blocked"
    assert (d.get("approval_status") or "").upper() == "BLOCKED"


def test_production_edge_allows_when_net_edge_positive(tmp_path: Path) -> None:
    edges = [
        EdgeSignal(
            edge_type=EdgeType.A_PULLBACK_CONTINUATION,
            detected=True,
            edge_confidence=0.75,
            expected_move_bps=80.0,
            expected_risk_bps=50.0,
            risk_level="medium",
            reason="test",
        )
    ]
    d = decide_lane_and_strategy(
        runtime_root=tmp_path,
        gate_id="gate_a",
        candidate_product="BTC-USD",
        candidate_strategy_id="",
        edges=edges,
        estimated_fees_bps=10.0,
        estimated_slippage_bps=10.0,
        spread_bps=10.0,
    )
    assert d["lane"] == "production"
    assert d["edge_detected_bool"] is True


def test_experimental_lane_requires_registry_and_positive_edge(tmp_path: Path) -> None:
    # Register an experimental strategy explicitly.
    p = tmp_path / "data" / "control"
    p.mkdir(parents=True, exist_ok=True)
    (p / "edge_strategy_registry.json").write_text(
        """{
  "truth_version": "edge_strategy_registry_v1",
  "strategies": [
    {
      "strategy_id": "EXP_TEST_STRAT",
      "gate_id": "gate_a",
      "strategy_mode": "experimental",
      "enabled": true,
      "max_size_multiplier": 0.25,
      "required_confidence": 0.5,
      "required_net_edge_bps": 1.0,
      "cooldown_sec": 120,
      "daily_loss_cap_usd": 2.0,
      "max_open_positions": 1,
      "notes": "test"
    }
  ]
}
""",
        encoding="utf-8",
    )
    edges = []  # no production edges
    d = decide_lane_and_strategy(
        runtime_root=tmp_path,
        gate_id="gate_a",
        candidate_product="BTC-USD",
        candidate_strategy_id="EXP_TEST_STRAT",
        edges=edges,
        estimated_fees_bps=0.0,
        estimated_slippage_bps=0.0,
        spread_bps=0.0,
    )
    # With no edges, experimental still won't be allowed unless expected_move is present via an edge.
    assert d["lane"] == "blocked"


def test_gate_b_edge_detector_smoke() -> None:
    edges = detect_gate_b_edges(
        row={"move_pct": 0.03, "volume_surge_ratio": 1.8, "spread_bps": 20, "book_depth_usd": 80_000, "exhaustion_risk": 0.4}
    )
    assert any(e.edge_type == EdgeType.B_MOMENTUM_BURST for e in edges)

