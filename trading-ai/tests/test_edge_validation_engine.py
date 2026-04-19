"""Edge Validation and Promotion Engine — registry, scoring, tagging, lifecycle."""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from trading_ai.edge.capital_allocation import allocation_weights_for_validated
from trading_ai.edge.execution_policy import resolve_coinbase_edge
from trading_ai.edge.models import EdgeRecord, EdgeStatus
from trading_ai.edge.registry import EdgeRegistry
from trading_ai.edge.research_bridge import materialize_from_research_log_path
from trading_ai.edge.scoring import compute_edge_metrics
from trading_ai.edge.validation import (
    apply_evaluation,
    evaluate_edge,
    minimum_sample_trades,
    promote_testing_if_candidate,
)


def _trade(
    tid: str,
    edge_id: str,
    net: float,
    fees: float = 0.5,
    **kwargs: object,
) -> dict:
    base = {
        "trade_id": tid,
        "avenue_id": "A",
        "avenue_name": "coinbase",
        "asset": "BTC-USD",
        "strategy_id": "mean_reversion",
        "route_chosen": "A",
        "regime": "calm",
        "timestamp_open": "2026-04-01T12:00:00+00:00",
        "timestamp_close": "2026-04-01T13:00:00+00:00",
        "net_pnl": net,
        "fees_paid": fees,
        "edge_id": edge_id,
        "entry_slippage_bps": 2.0,
        "exit_slippage_bps": 2.0,
        "health_state": "ok",
    }
    base.update(kwargs)
    return base


def test_edge_creation_from_research_jsonl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    logp = tmp_path / "strategy_research" / "research_log.jsonl"
    logp.parent.mkdir(parents=True, exist_ok=True)
    logp.write_text(
        json.dumps(
            {
                "timestamp": "2026-04-10T00:00:00+00:00",
                "hypothesis": "momentum continuation on BTC works when volatility is elevated",
                "confidence": "LOW",
                "source": "gpt",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    out = materialize_from_research_log_path(logp, lines_limit=5)
    assert out.get("ok") is True
    assert out.get("created", 0) >= 1
    reg = EdgeRegistry()
    edges = reg.list_edges()
    assert len(edges) >= 1
    assert edges[0].status == EdgeStatus.CANDIDATE.value
    assert edges[0].edge_type in ("momentum", "volatility", "unknown")


def test_expectancy_and_post_fee_metrics() -> None:
    eid = "edge_unit"
    events = [
        _trade("t1", eid, 10.0, 1.0),
        _trade("t2", eid, -4.0, 1.0),
        _trade("t3", eid, 8.0, 1.0),
    ]
    m = compute_edge_metrics(events, eid)
    assert m.total_trades == 3
    assert m.post_fee_expectancy == pytest.approx((10 - 4 + 8) / 3)
    assert m.net_pnl == pytest.approx(14.0)


def test_promotion_to_validated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("EDGE_MIN_SAMPLE_TRADES", "5")
    monkeypatch.setenv("EDGE_MAX_DRAWDOWN_USD", "100000")

    eid = "edge_promo"
    reg = EdgeRegistry()
    reg.upsert(
        EdgeRecord(
            edge_id=eid,
            avenue="coinbase",
            edge_type="momentum",
            hypothesis_text="x",
            required_conditions={},
            status=EdgeStatus.TESTING.value,
        )
    )
    events = [_trade(f"t{i}", eid, 2.0, 0.1) for i in range(6)]
    changed, rep = apply_evaluation(reg, events, eid)
    assert changed is True
    assert reg.get(eid).status == EdgeStatus.VALIDATED.value
    assert rep.get("metrics_dict", {}).get("post_fee_expectancy", 0) > 0


def test_rejection_negative_expectancy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("EDGE_MIN_SAMPLE_TRADES", "5")

    eid = "edge_bad"
    reg = EdgeRegistry()
    reg.upsert(
        EdgeRecord(
            edge_id=eid,
            avenue="coinbase",
            edge_type="momentum",
            hypothesis_text="x",
            required_conditions={},
            status=EdgeStatus.TESTING.value,
        )
    )
    events = [_trade(f"t{i}", eid, -3.0, 0.5) for i in range(6)]
    changed, rep = apply_evaluation(reg, events, eid)
    assert changed is True
    assert reg.get(eid).status == EdgeStatus.REJECTED.value


def test_trade_tagging_in_databank_payload(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    from trading_ai.nte.databank.coinbase_close_adapter import coinbase_nt_close_to_databank_raw

    pos = {
        "id": "pos_1",
        "product_id": "BTC-USD",
        "opened_ts": 1_700_000_000.0,
        "strategy": "mean_reversion",
        "entry_regime": "calm",
        "edge_id": "edge_abc",
        "edge_lane": "testing",
        "market_snapshot": {"mid": 100.0},
    }
    record = {
        "trade_id": "pos_1",
        "duration_sec": 120.0,
        "fees_usd": 1.0,
        "gross_pnl_usd": 5.0,
        "net_pnl_usd": 4.0,
        "expected_edge_bps": 10.0,
    }
    raw = coinbase_nt_close_to_databank_raw(pos, record, exit_reason="take_profit")
    assert raw.get("edge_id") == "edge_abc"
    assert raw.get("edge_lane") == "testing"
    assert raw.get("market_snapshot_json")


def test_resolve_coinbase_edge_no_registry_allows_legacy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    a = resolve_coinbase_edge("mean_reversion", "BTC-USD")
    assert a.edge_lane == "none"
    assert a.size_scale == 1.0


def test_capital_allocation_weights_shift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    reg = EdgeRegistry()
    reg.upsert(
        EdgeRecord(
            edge_id="e1",
            avenue="coinbase",
            edge_type="momentum",
            hypothesis_text="a",
            required_conditions={},
            status=EdgeStatus.VALIDATED.value,
        )
    )
    reg.upsert(
        EdgeRecord(
            edge_id="e2",
            avenue="coinbase",
            edge_type="spread",
            hypothesis_text="b",
            required_conditions={},
            status=EdgeStatus.VALIDATED.value,
        )
    )
    events = [
        _trade("a1", "e1", 50.0, 1.0),
        _trade("a2", "e1", 40.0, 1.0),
        _trade("b1", "e2", 5.0, 1.0),
        _trade("b2", "e2", 4.0, 1.0),
    ]
    aw = allocation_weights_for_validated(reg, events)
    assert aw["weights"]["e1"] > aw["weights"]["e2"]


def test_candidate_to_testing_to_validated_flow(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("EDGE_MIN_SAMPLE_TRADES", "4")
    monkeypatch.setenv("EDGE_MAX_DRAWDOWN_USD", "100000")

    eid = "edge_flow"
    reg = EdgeRegistry()
    reg.upsert(
        EdgeRecord(
            edge_id=eid,
            avenue="coinbase",
            edge_type="momentum",
            hypothesis_text="flow",
            required_conditions={},
            status=EdgeStatus.CANDIDATE.value,
        )
    )
    assert promote_testing_if_candidate(reg, eid) is True
    assert reg.get(eid).status == EdgeStatus.TESTING.value

    events = [_trade(f"f{i}", eid, 3.0, 0.2) for i in range(5)]
    changed, _ = apply_evaluation(reg, events, eid)
    assert changed is True
    assert reg.get(eid).status == EdgeStatus.VALIDATED.value


def test_evaluate_edge_report_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    eid = "edge_rep"
    events = [_trade(f"x{i}", eid, 1.0) for i in range(40)]
    rep = evaluate_edge(EdgeRegistry(), events, eid)
    assert "metrics_dict" in rep
    assert "post_fee_expectancy" in rep["metrics_dict"]
    assert minimum_sample_trades() >= 30
