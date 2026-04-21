"""Evolution loop: scoring, routing, safest bets, maturity, CEO wiring — no live trading."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_ai.edge.models import EdgeRecord, EdgeStatus, EdgeType
from trading_ai.edge.registry import EdgeRegistry
from trading_ai.evolution.accumulation import accumulation_snapshot, contribution_by_dimension
from trading_ai.evolution.measures import infer_capital_gate
from trading_ai.evolution.routing import compute_adaptive_gate_split
from trading_ai.evolution.scoring import MaturityLevel, rank_edges_by_score
from trading_ai.evolution.safest import rank_safest_edges
from trading_ai.evolution.loop import run_evolution_cycle
from trading_ai.review.ceo_session_engine import build_evolution_ceo_answers, run_ceo_session_bundle


def _synthetic_events() -> list:
    base_a = {
        "trade_id": "t-a-1",
        "avenue_id": "A",
        "avenue_name": "coinbase",
        "asset": "BTC-USD",
        "strategy_id": "nte_core",
        "route_chosen": "a",
        "regime": "neutral",
        "timestamp_open": "2026-01-01T00:00:00+00:00",
        "timestamp_close": "2026-01-01T01:00:00+00:00",
        "edge_id": "edge_win",
        "edge_lane": "validated",
        "net_pnl": 12.0,
        "fees_paid": 1.0,
        "gross_pnl": 13.0,
        "hold_seconds": 300.0,
        "execution_quality_score": 85.0,
        "health_state": "ok",
        "degraded_mode": False,
    }
    base_b = {
        **base_a,
        "trade_id": "t-b-1",
        "strategy_id": "gate_b_momentum_scan",
        "edge_id": "edge_b",
        "net_pnl": -4.0,
        "fees_paid": 0.5,
        "gross_pnl": -3.5,
    }
    return [dict(base_a), dict(base_b)]


def test_infer_capital_gate() -> None:
    ev_a = _synthetic_events()[0]
    assert infer_capital_gate(ev_a) == "gate_a"
    ev_b = _synthetic_events()[1]
    assert infer_capital_gate(ev_b) == "gate_b"


def test_adaptive_routing_nudges() -> None:
    ev = _synthetic_events()
    r = compute_adaptive_gate_split(ev)
    assert r.split.gate_a + r.split.gate_b == pytest.approx(1.0)
    assert 0.0 <= r.defensive_idle_fraction <= 0.26


def test_rank_edges_and_unified_score() -> None:
    ev = _synthetic_events()
    e1 = EdgeRecord(
        edge_id="edge_win",
        avenue="coinbase",
        edge_type=EdgeType.MOMENTUM.value,
        hypothesis_text="h",
        required_conditions={},
        status=EdgeStatus.VALIDATED.value,
        confidence=0.7,
        strategy_lane="gate_a",
    )
    e2 = EdgeRecord(
        edge_id="edge_b",
        avenue="coinbase",
        edge_type=EdgeType.MOMENTUM.value,
        hypothesis_text="h2",
        required_conditions={},
        status=EdgeStatus.TESTING.value,
        confidence=0.3,
        strategy_lane="gate_b_momentum",
    )
    ranked = rank_edges_by_score([e1, e2], ev)
    assert ranked[0]["unified_score"] >= ranked[-1]["unified_score"]


def test_safest_edges_order() -> None:
    ev = _synthetic_events()
    s = rank_safest_edges(ev, registry_edges=[
        EdgeRecord(
            edge_id="edge_win",
            avenue="coinbase",
            edge_type=EdgeType.MOMENTUM.value,
            hypothesis_text="h",
            required_conditions={},
            status=EdgeStatus.VALIDATED.value,
        ),
        EdgeRecord(
            edge_id="edge_b",
            avenue="coinbase",
            edge_type=EdgeType.MOMENTUM.value,
            hypothesis_text="h2",
            required_conditions={},
            status=EdgeStatus.TESTING.value,
        ),
    ])
    assert s[0]["safest_score"] >= s[-1]["safest_score"]


def test_contribution_by_dimension() -> None:
    c = contribution_by_dimension(_synthetic_events())
    assert "coinbase" in str(c.get("by_avenue") or {})


def test_run_evolution_cycle_smoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADE_DATABANK_MEMORY_ROOT", str(tmp_path / "db"))
    (tmp_path / "db").mkdir(parents=True)
    reg_path = tmp_path / "db" / "edge_registry.json"
    reg_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0.0",
                "edges": [
                    {
                        "edge_id": "edge_win",
                        "avenue": "coinbase",
                        "edge_type": "momentum",
                        "hypothesis_text": "x",
                        "required_conditions": {},
                        "status": "validated",
                        "confidence": 0.6,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    ev = _synthetic_events()
    bundle = run_evolution_cycle(
        ev,
        registry=EdgeRegistry(path=reg_path),
        current_capital=1000.0,
        write_artifacts=False,
        apply_adjustments=False,
    )
    assert bundle.get("schema") == "ezras.evolution_cycle.v1"
    assert len(bundle.get("steps") or []) == 10
    assert "top_edges" in (bundle.get("summary") or {})


def test_ceo_bundle_with_evolution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_EVOLUTION_HOOK_ON_CLOSE", "0")
    b = run_ceo_session_bundle(include_evolution=False, metrics_gate_a={"pnl": 1.0})
    assert "CEO_GLOBAL_SESSION" in b
    g = b["CEO_GLOBAL_SESSION"]
    assert "evolution_ceo_answers" not in g


def test_build_evolution_ceo_answers() -> None:
    fake = {
        "summary": {
            "top_edges": [{"edge_id": "a"}],
            "safest_edges": [{"edge_id": "b"}],
            "most_degraded": [{"edge_id": "c"}],
            "gate_split": {"gate_a_share": 0.5},
        },
        "steps": [
            {
                "name": "update_operator_ceo_sessions",
                "acceleration": {"growth_mode_recommendation": "safe_compounding"},
                "accumulation": {"current_capital": 100.0},
            }
        ],
    }
    ans = build_evolution_ceo_answers(fake)
    assert ans["what_is_working"] == ["a"]
    assert ans["goal_acceleration"] == "safe_compounding"


def test_paused_edge_zero_size() -> None:
    from trading_ai.edge.execution_policy import _size_scale_for_edge_status
    from trading_ai.edge.models import EdgeStatus

    assert _size_scale_for_edge_status(EdgeStatus.PAUSED.value) == 0.0


def test_maturity_enum() -> None:
    assert MaturityLevel.VALIDATED.value == "validated"
