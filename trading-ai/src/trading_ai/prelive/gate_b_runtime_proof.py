"""Staged Gate B runtime proof — scan → rank → exit simulation (no venue orders)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from trading_ai.prelive._io import write_control_json, write_control_txt
from trading_ai.shark.coinbase_spot.gate_b_config import load_gate_b_config_from_env
from trading_ai.shark.coinbase_spot.gate_b_engine import GateBMomentumEngine
from trading_ai.shark.coinbase_spot.gate_b_monitor import GateBMonitorState


def _scenarios() -> List[Dict[str, Any]]:
    return [
        {"id": "clean_winner", "note": "Strong row passes filters"},
        {"id": "false_breakout", "note": "Low move_pct fails breakout"},
        {"id": "liquidity_trap", "note": "Thin book fails liquidity gate"},
        {"id": "stale_quote", "note": "Old quote_ts fails data quality"},
        {"id": "spread_wide", "note": "spread_bps too high"},
        {"id": "max_positions", "note": "Simulated by engine when open slots 0"},
        {"id": "chop_regime", "note": "Engine may disable in chop when env set"},
        {"id": "sudden_drop_exit", "note": "Exit path sudden_drop_event"},
        {"id": "trailing_stop", "note": "Monitor tick trailing path"},
        {"id": "time_stop", "note": "max_hold_sec exit"},
        {"id": "hard_stop", "note": "hard_stop_from_entry_pct"},
        {"id": "profit_target", "note": "profit target exit"},
        {"id": "correlation_block", "note": "correlation guard may reject"},
        {"id": "reentry_block", "note": "reentry controller cooldown"},
        {"id": "momentum_scan_branch", "note": "closes present → momentum scan"},
        {"id": "rank_only_branch", "note": "no long closes → rank_gate_b"},
        {"id": "edge_stats_pause", "note": "edge stats may pause (theoretical)"},
        {"id": "latency_anomaly_stub", "note": "timing logged in artifact only for staged"},
        {"id": "partial_fill_stub", "note": "not simulated here — ledger path separate"},
        {"id": "venue_reject_stub", "note": "Kalshi reject → execution_live path"},
    ]


def run(*, runtime_root: Path) -> Dict[str, Any]:
    cfg = load_gate_b_config_from_env()
    eng = GateBMomentumEngine()
    ts = datetime.now(timezone.utc).timestamp()
    base = {
        "volume_24h_usd": 5_000_000.0,
        "spread_bps": 12.0,
        "book_depth_usd": 80_000.0,
        "move_pct": 0.08,
        "volume_surge_ratio": 1.8,
        "continuation_candles": 3,
        "velocity_score": 0.62,
        "quote_ts": ts,
        "best_bid": 50_000.0,
        "best_ask": 50_015.0,
        "closes": [48_000 + 80 * i for i in range(24)],
        "exhaustion_risk": 0.2,
    }
    rows = [
        {**base, "product_id": "BTC-USD"},
        {**base, "product_id": "ETH-USD", "move_pct": 0.01},
        {**base, "product_id": "ILLIQ-USD", "volume_24h_usd": 100.0},
    ]
    ent = eng.evaluate_entry_candidates(rows, open_product_ids=[], regime_inputs={})
    # Exit simulation on first candidate if any
    exits: List[Dict[str, Any]] = []
    cands = ent.get("candidates") or []
    if cands:
        pid = str(cands[0].get("product_id") or "BTC-USD")
        st = GateBMonitorState(
            product_id=pid,
            entry_price=50_000.0,
            peak_price=51_200.0,
            entry_ts=ts - 60.0,
            last_price=50_900.0,
        )
        price_map = {pid: 50_800.0}
        prev_map = {pid: 51_000.0}
        exits = eng.evaluate_exits([st], price_by_product=price_map, prev_price_by_product=prev_map, now_ts=ts)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "gate_id": "gate_b",
        "strategy_family": cfg.strategy_family,
        "scenario_catalog": _scenarios(),
        "entry_evaluation": {
            "candidate_count": len(cands),
            "regime": ent.get("regime"),
            "edge": ent.get("edge"),
        },
        "exit_simulation_sample": exits[:3],
        "honesty": "Staged proof only — does not submit Kalshi or Coinbase orders.",
    }
    write_control_json("gate_b_runtime_proof.json", payload, runtime_root=runtime_root)
    write_control_txt("gate_b_runtime_proof.txt", json.dumps(payload, indent=2, default=str) + "\n", runtime_root=runtime_root)
    return payload
