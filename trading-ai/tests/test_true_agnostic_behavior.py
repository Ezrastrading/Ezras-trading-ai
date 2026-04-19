"""
Phase 3 — prove strategy / latency / edge agnosticism at the organism boundary (global layer).

These tests intentionally avoid live trading and do not assert exchange execution semantics.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

from trading_ai.global_layer.ai_review_packet_builder import build_review_packet, build_route_summary_from_trades
from trading_ai.global_layer.governance_order_gate import check_new_order_allowed_full
from trading_ai.global_layer.review_storage import ReviewStorage
from trading_ai.global_layer.trade_truth import load_federated_trades
from trading_ai.nte.memory.store import MemoryStore


def _write_joint_normal(gdir: Path) -> None:
    gdir.mkdir(parents=True, exist_ok=True)
    (gdir / "joint_review_latest.json").write_text(
        json.dumps(
            {
                "joint_review_id": "jr_agnostic",
                "live_mode_recommendation": "normal",
                "review_integrity_state": "full",
                "generated_at": "2099-06-01T12:00:00+00:00",
                "packet_id": "pkt_agnostic",
                "empty": False,
            }
        ),
        encoding="utf-8",
    )


def _strip_packet_volatile(p: Dict[str, Any]) -> Dict[str, Any]:
    x = deepcopy(p)
    x.pop("packet_id", None)
    x.pop("generated_at", None)
    rc = x.get("review_context_rank")
    if isinstance(rc, dict):
        rc.pop("generated_at", None)
    return x


def _gov_triplet(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    route: str,
) -> Tuple[bool, str, Dict[str, Any]]:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("GOVERNANCE_ORDER_ENFORCEMENT", "true")
    gdir = tmp_path / "shark" / "memory" / "global"
    _write_joint_normal(gdir)
    return check_new_order_allowed_full(
        venue="coinbase",
        operation="agnostic_probe",
        route=route,
        intent_id="intent-agnostic",
        log_decision=False,
    )


def test_strategy_independence_governance_packet_federation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Identical trades differing only in strategy_class → same gate outcome and packet shape (minus volatiles)."""
    monkeypatch.setenv("GOVERNANCE_ORDER_ENFORCEMENT", "true")

    classes = ["mean_reversion", "trend_following", "random"]
    packets: List[Dict[str, Any]] = []
    audits: List[Dict[str, Any]] = []
    metas: List[Dict[str, Any]] = []

    for sc in classes:
        root = tmp_path / f"sc_{sc}"
        root.mkdir(parents=True)
        monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(root))
        _write_joint_normal(root / "shark" / "memory" / "global")
        ms = MemoryStore()
        ms.ensure_defaults()
        ms.append_trade(
            {
                "trade_id": f"t_{sc}",
                "avenue": "coinbase",
                "route_bucket": "agnostic_bench",
                "strategy_class": sc,
                "setup_type": "ignored_for_grouping",
                "net_pnl_usd": 1.0,
                "product_id": "BTC-USD",
            }
        )
        ok, reason, audit = check_new_order_allowed_full(
            venue="coinbase",
            operation="agnostic",
            route="n/a",
            intent_id=f"id-{sc}",
            log_decision=False,
        )
        assert ok and reason.startswith("joint_review_")
        audits.append(
            {k: v for k, v in audit.items() if k not in ("route", "intent_id", "ts", "joint_age_hours")}
        )
        st = ReviewStorage()
        st.ensure_review_files()
        p = build_review_packet(review_type="morning", storage=st)
        packets.append(_strip_packet_volatile(p))
        _, meta = load_federated_trades(nte_store=ms)
        metas.append(
            {
                "merged_trade_count": meta.get("merged_trade_count"),
                "model": meta.get("model"),
                "federation_conflict_count": meta.get("federation_conflict_count"),
            }
        )

    for i in range(1, len(packets)):
        assert packets[i]["route_summary"] == packets[0]["route_summary"]
        assert packets[i]["live_trading_summary"]["closed_trades_count"] == packets[0]["live_trading_summary"][
            "closed_trades_count"
        ]
        assert audits[i] == audits[0]
        assert metas[i] == metas[0]


def test_latency_independence_metadata_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GOVERNANCE_ORDER_ENFORCEMENT", "true")

    packets: List[Dict[str, Any]] = []
    gov: List[Tuple[bool, str]] = []
    for lat_ms in (5, 500, 5000):
        root = tmp_path / f"lat_{lat_ms}"
        root.mkdir(parents=True)
        monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(root))
        _write_joint_normal(root / "shark" / "memory" / "global")
        ms = MemoryStore()
        ms.ensure_defaults()
        ms.append_trade(
            {
                "trade_id": f"lat_{lat_ms}",
                "avenue": "coinbase",
                "route_bucket": "latency_bench",
                "net_pnl_usd": 2.0,
                "execution_latency_ms": lat_ms,
            }
        )
        ok, reason, _ = check_new_order_allowed_full(
            venue="coinbase",
            operation="agnostic",
            route="n/a",
            log_decision=False,
        )
        gov.append((ok, reason))
        st = ReviewStorage()
        st.ensure_review_files()
        packets.append(_strip_packet_volatile(build_review_packet(review_type="eod", storage=st)))

    assert len({g[1] for g in gov}) == 1
    assert all(g[0] for g in gov)
    for i in range(1, len(packets)):
        assert packets[i]["route_summary"] == packets[0]["route_summary"]


def test_edge_independence_stored_not_routed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Varying expected_edge metadata does not change grouping when route_bucket is fixed."""
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    _write_joint_normal(tmp_path / "shark" / "memory" / "global")

    summaries: List[Dict[str, Any]] = []
    for edge in (500.0, 1.0, None):
        t = {
            "trade_id": f"e_{edge}",
            "avenue": "coinbase",
            "route_bucket": "edge_bench",
            "net_pnl_usd": 1.0,
            "expected_edge_bps": edge,
        }
        summaries.append(build_route_summary_from_trades([t]))

    for i in range(1, len(summaries)):
        assert summaries[i]["bucket_order"] == summaries[0]["bucket_order"]
        assert summaries[i]["buckets"]["edge_bench"]["count"] == summaries[0]["buckets"]["edge_bench"]["count"]


def test_missing_optional_fields_no_silent_zero_truth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing fees/slippage/strategy must not force fake 'clean' zeros in truth_provenance."""
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    ms = MemoryStore()
    ms.ensure_defaults()
    ms.append_trade(
        {
            "trade_id": "thin_1",
            "avenue": "coinbase",
            "route_bucket": "thin",
            "net_pnl_usd": 3.0,
        }
    )
    trades, meta = load_federated_trades(nte_store=ms)
    assert meta.get("merged_trade_count", 0) >= 1
    row = next(t for t in trades if t.get("trade_id") == "thin_1")
    prov = row.get("truth_provenance") or {}
    assert prov.get("fees_unknown") is True or prov.get("slippage_unknown") is True

    st = ReviewStorage()
    st.ensure_review_files()
    pkt = build_review_packet(review_type="exception", storage=st)
    assert pkt.get("packet_id")
    assert pkt["packet_truth"].get("field_quality_summary")


def test_cross_avenue_packet_parity_normalized_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same economics + explicit route_bucket; only avenue label changes — route_summary matches."""
    avenues = ("coinbase", "kalshi", "synthetic")
    route_summaries: List[Dict[str, Any]] = []
    for av in avenues:
        root = tmp_path / f"av_{av}"
        root.mkdir(parents=True)
        monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(root))
        _write_joint_normal(root / "shark" / "memory" / "global")
        ms = MemoryStore()
        ms.ensure_defaults()
        ms.append_trade(
            {
                "trade_id": f"av_{av}",
                "avenue": av,
                "route_bucket": "parity",
                "net_pnl_usd": 4.0,
                "entry_slippage_bps": 1.0,
                "exit_slippage_bps": 1.0,
            }
        )
        st = ReviewStorage()
        st.ensure_review_files()
        p = build_review_packet(storage=st)
        route_summaries.append(p["route_summary"])

    for i in range(1, len(route_summaries)):
        assert route_summaries[i]["buckets"] == route_summaries[0]["buckets"]


def test_governance_route_string_is_audit_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Governance decision does not depend on the free-form route audit string."""
    a = _gov_triplet(tmp_path, monkeypatch, route="mean_reversion")
    b = _gov_triplet(tmp_path, monkeypatch, route="trend_following")
    c = _gov_triplet(tmp_path, monkeypatch, route="random")
    skip = ("route", "intent_id", "ts", "joint_age_hours")
    core_a = {k: v for k, v in a[2].items() if k not in skip}
    core_b = {k: v for k, v in b[2].items() if k not in skip}
    core_c = {k: v for k, v in c[2].items() if k not in skip}
    assert a[0] == b[0] == c[0]
    assert a[1] == b[1] == c[1]
    assert core_a == core_b == core_c


def test_strategy_like_strings_do_not_split_route_buckets() -> None:
    """strategy_class / setup_type must not create separate packet buckets (explicit route_bucket only)."""
    t = {
        "trade_id": "x1",
        "avenue": "coinbase",
        "strategy_class": "route_a",
        "setup_type": "route_b",
        "net_pnl_usd": 1.0,
    }
    out = build_route_summary_from_trades([t])
    assert list(out["buckets"].keys()) == ["_ungrouped"]

