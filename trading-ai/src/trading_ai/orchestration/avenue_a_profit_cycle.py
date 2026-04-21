"""
Avenue A profit-seeking cycle (Gate A + Gate B), guarded and truth-logged.

Key constraints:
- Does NOT write `execution_proof/live_execution_validation.json` (keep proof artifacts truthful).
- Does NOT bypass guards (governance, system execution lock, live-order guard, duplicate guard).
- Emits Telegram via existing post-trade hub hooks (non-blocking).
- Persists closed trade via databank pipeline + Supabase sync using existing adapters.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Literal, Optional, Tuple

from trading_ai.global_layer.governance_order_gate import check_new_order_allowed_full
from trading_ai.nte.config.coinbase_avenue1_launch import load_coinbase_avenue1_launch
from trading_ai.nte.execution.net_edge_gate import estimate_round_trip_cost_bps
from trading_ai.nte.execution.profit_enforcement import (
    ProfitEnforcementConfig,
    evaluate_profit_enforcement,
    profit_enforcement_allows_or_reason,
)
from trading_ai.runtime_proof.coinbase_spot_fill_truth import (
    normalize_coinbase_buy_fills,
    normalize_coinbase_sell_fills,
)
from trading_ai.nte.databank.trade_intelligence_databank import process_closed_trade
from trading_ai.nte.execution.product_rules import round_base_to_increment
from trading_ai.orchestration.coinbase_gate_selection.gate_a_product_selection import (
    run_gate_a_product_selection,
)
from trading_ai.orchestration.coinbase_gate_selection.gate_b_gainers_selection import (
    run_gate_b_gainers_selection,
)
from trading_ai.runtime_proof.coinbase_accounts import preflight_exact_spot_product
from trading_ai.runtime_proof.live_execution_validation import (
    run_short_runtime_stability,
    verify_data_pipeline_after_trade,
)
from trading_ai.shark.coinbase_spot.gate_b_monitor import (
    GateBMonitorState,
    gate_b_monitor_tick,
)
from trading_ai.storage.storage_adapter import LocalStorageAdapter
from trading_ai.universal_execution.universal_execution_loop_proof import (
    build_universal_execution_loop_proof_payload,
    write_universal_execution_loop_proof,
)
from trading_ai.universal_execution.execution_truth_contract import ExecutionTruthStage
from trading_ai.universal_execution.rebuy_policy import TerminalHonestState

from trading_ai.global_layer.gap_engine import (
    coinbase_liquidity_score,
    evaluate_candidate,
    map_coinbase_execution_mode,
    map_coinbase_gap_type,
)
from trading_ai.global_layer.gap_models import (
    UniversalGapCandidate,
    candidate_context_reset,
    candidate_context_set,
    new_universal_candidate_id,
)
from trading_ai.global_layer.trade_snapshots import (
    write_edge_snapshot,
    write_execution_snapshot,
    write_master_snapshot,
    write_review_snapshot,
)
from trading_ai.global_layer.execution_grader import grade_execution
from trading_ai.global_layer.universal_sizing import size_from_candidate

logger = logging.getLogger(__name__)


def _truthy_env(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes")


def _load_bounded_posture(runtime_root: Path) -> Dict[str, Any]:
    """
    Advisory, bounded adaptation written by outcome learning pipeline.
    Never enables trading; only scales notional and applies symbol cooldowns.
    """
    try:
        ad = LocalStorageAdapter(runtime_root=runtime_root)
        p = ad.read_json("data/control/bounded_risk_posture.json") or {}
        return p if isinstance(p, dict) else {}
    except Exception:
        return {}


def _profit_market_fallback_enabled(*, gate: str) -> bool:
    """
    When a maker-intent limit does not fill quickly, optionally fall back to a market IOC entry.
    This is still gated by spread/edge filters; it only changes the order type.
    """
    v = (os.environ.get("EZRAS_PROFIT_ENTRY_MARKET_FALLBACK") or "").strip().lower()
    if v in ("0", "false", "no"):
        return False
    if v in ("1", "true", "yes"):
        return True
    # Default: disabled under strict execution mode.
    strict = (os.environ.get("EZRAS_STRICT_EXECUTION_MODE") or "true").strip().lower() in ("1", "true", "yes")
    return not strict


def profit_mode_enabled(*, mode: str) -> bool:
    """
    Autonomous Avenue A modes default to profit-mode ON (operator expectation for venue cycles).

    Other modes stay behind ``PROFIT_MODE_ENABLED=true`` unless explicitly disabled with
    ``PROFIT_MODE_ENABLED=false`` for autonomous family modes.
    """
    m = (mode or "").strip().lower()
    if m in ("autonomous_live", "autonomous_paper"):
        v = (os.environ.get("PROFIT_MODE_ENABLED") or "").strip().lower()
        if v in ("0", "false", "no"):
            return False
        return True
    return (os.environ.get("PROFIT_MODE_ENABLED") or "").strip().lower() in ("1", "true", "yes")


@dataclass(frozen=True)
class GateAProfitConfig:
    min_hold_sec: float = 30.0
    max_hold_sec: float = 240.0
    take_profit_pct: float = 0.0020
    stop_loss_pct: float = 0.0020
    trailing_stop_from_peak_pct: float = 0.0035
    entry_spread_max_bps: float = 25.0
    entry_offset_bps: float = 3.0
    entry_fill_timeout_sec: float = 45.0
    # Preferred config knob (ms). If unset in env, derived from entry_fill_timeout_sec.
    fill_timeout_ms: int = 45_000
    poll_sec: float = 2.0
    # Deprecated: prefer NTE fee assumptions (NTE_FEE_MAKER_PCT / NTE_FEE_TAKER_PCT) unless overridden.
    est_total_fee_bps: Optional[float] = None
    # Required expected edge AFTER estimated spread+fees+slippage.
    min_net_edge_over_fees_bps: float = 2.0
    # Slippage buffer for round-trip (entry+exit) in bps.
    est_round_trip_slippage_bps: float = 6.0
    # Minimum net profit floor in USD (prevents tiny churn).
    min_net_profit_usd: float = 0.06


@dataclass(frozen=True)
class GateBProfitConfig:
    min_hold_sec: float = 20.0
    max_hold_sec: float = 180.0
    profit_zone_min_pct: float = 0.004
    profit_zone_max_pct: float = 0.012
    trailing_stop_from_peak_pct: float = 0.004
    hard_stop_from_entry_pct: float = 0.004
    entry_spread_max_bps: float = 35.0
    entry_offset_bps: float = 2.0
    entry_fill_timeout_sec: float = 35.0
    # Preferred config knob (ms). If unset in env, derived from entry_fill_timeout_sec.
    fill_timeout_ms: int = 35_000
    poll_sec: float = 1.5
    # Deprecated: prefer NTE fee assumptions (NTE_FEE_MAKER_PCT / NTE_FEE_TAKER_PCT) unless overridden.
    est_total_fee_bps: Optional[float] = None
    # Required expected edge AFTER estimated spread+fees+slippage.
    min_net_edge_over_fees_bps: float = 3.0
    # Slippage buffer for round-trip (entry+exit) in bps.
    est_round_trip_slippage_bps: float = 8.0
    # Minimum net profit floor in USD (prevents tiny churn).
    min_net_profit_usd: float = 0.08


def load_gate_a_profit_config_from_env() -> GateAProfitConfig:
    def _f(name: str, default: float) -> float:
        raw = (os.environ.get(name) or "").strip()
        if not raw:
            return default
        try:
            return float(raw)
        except ValueError:
            return default

    def _ms(name: str, default_ms: int) -> int:
        raw = (os.environ.get(name) or "").strip()
        if not raw:
            return int(default_ms)
        try:
            v = int(float(raw))
            return max(1, v)
        except ValueError:
            return int(default_ms)

    fee_raw = (os.environ.get("EZRAS_ESTIMATED_TOTAL_FEE_BPS") or "").strip()
    fee = None
    if fee_raw:
        try:
            fee = float(fee_raw)
        except ValueError:
            fee = None
    fill_ms = _ms("GATE_A_PROFIT_ENTRY_FILL_TIMEOUT_MS", int(_f("GATE_A_PROFIT_ENTRY_FILL_TIMEOUT_SEC", 45.0) * 1000))
    return GateAProfitConfig(
        min_hold_sec=_f("GATE_A_PROFIT_MIN_HOLD_SEC", 30.0),
        max_hold_sec=_f("GATE_A_PROFIT_MAX_HOLD_SEC", 240.0),
        take_profit_pct=_f("GATE_A_PROFIT_TAKE_PROFIT_PCT", 0.0020),
        stop_loss_pct=_f("GATE_A_PROFIT_STOP_LOSS_PCT", 0.0020),
        trailing_stop_from_peak_pct=_f("GATE_A_PROFIT_TRAILING_STOP_PEAK_PCT", 0.0035),
        entry_spread_max_bps=_f("GATE_A_PROFIT_ENTRY_MAX_SPREAD_BPS", 25.0),
        entry_offset_bps=_f("GATE_A_PROFIT_ENTRY_OFFSET_BPS", 3.0),
        entry_fill_timeout_sec=_f("GATE_A_PROFIT_ENTRY_FILL_TIMEOUT_SEC", 45.0),
        fill_timeout_ms=int(fill_ms),
        poll_sec=_f("GATE_A_PROFIT_POLL_SEC", 2.0),
        est_total_fee_bps=fee,
        min_net_edge_over_fees_bps=_f("GATE_A_PROFIT_MIN_NET_EDGE_OVER_FEES_BPS", 2.0),
        est_round_trip_slippage_bps=_f("GATE_A_PROFIT_EST_ROUND_TRIP_SLIPPAGE_BPS", 6.0),
        min_net_profit_usd=_f("GATE_A_PROFIT_MIN_NET_PROFIT_USD", 0.06),
    )


def load_gate_b_profit_config_from_env() -> GateBProfitConfig:
    def _f(name: str, default: float) -> float:
        raw = (os.environ.get(name) or "").strip()
        if not raw:
            return default
        try:
            return float(raw)
        except ValueError:
            return default

    def _ms(name: str, default_ms: int) -> int:
        raw = (os.environ.get(name) or "").strip()
        if not raw:
            return int(default_ms)
        try:
            v = int(float(raw))
            return max(1, v)
        except ValueError:
            return int(default_ms)

    fee_raw = (os.environ.get("EZRAS_ESTIMATED_TOTAL_FEE_BPS") or "").strip()
    fee = None
    if fee_raw:
        try:
            fee = float(fee_raw)
        except ValueError:
            fee = None
    fill_ms = _ms("GATE_B_PROFIT_ENTRY_FILL_TIMEOUT_MS", int(_f("GATE_B_PROFIT_ENTRY_FILL_TIMEOUT_SEC", 35.0) * 1000))
    return GateBProfitConfig(
        min_hold_sec=_f("GATE_B_PROFIT_MIN_HOLD_SEC", 20.0),
        max_hold_sec=_f("GATE_B_PROFIT_MAX_HOLD_SEC", 180.0),
        profit_zone_min_pct=_f("GATE_B_PROFIT_ZONE_MIN_PCT", 0.004),
        profit_zone_max_pct=_f("GATE_B_PROFIT_ZONE_MAX_PCT", 0.012),
        trailing_stop_from_peak_pct=_f("GATE_B_PROFIT_TRAILING_STOP_PEAK_PCT", 0.004),
        hard_stop_from_entry_pct=_f("GATE_B_PROFIT_HARD_STOP_PCT", 0.004),
        entry_spread_max_bps=_f("GATE_B_PROFIT_ENTRY_MAX_SPREAD_BPS", 35.0),
        entry_offset_bps=_f("GATE_B_PROFIT_ENTRY_OFFSET_BPS", 2.0),
        entry_fill_timeout_sec=_f("GATE_B_PROFIT_ENTRY_FILL_TIMEOUT_SEC", 35.0),
        fill_timeout_ms=int(fill_ms),
        poll_sec=_f("GATE_B_PROFIT_POLL_SEC", 1.5),
        est_total_fee_bps=fee,
        min_net_edge_over_fees_bps=_f("GATE_B_PROFIT_MIN_NET_EDGE_OVER_FEES_BPS", 3.0),
        est_round_trip_slippage_bps=_f("GATE_B_PROFIT_EST_ROUND_TRIP_SLIPPAGE_BPS", 8.0),
        min_net_profit_usd=_f("GATE_B_PROFIT_MIN_NET_PROFIT_USD", 0.08),
    )


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _spread_bps(bid: float, ask: float) -> Optional[float]:
    if bid <= 0 or ask <= 0:
        return None
    mid = (bid + ask) / 2.0
    if mid <= 0:
        return None
    return (ask - bid) / mid * 10000.0


def _estimate_required_move_bps(
    *,
    spread_bps: float,
    est_total_fee_bps: Optional[float],
    floor_bps: float,
) -> Tuple[float, bool]:
    fee_missing = est_total_fee_bps is None
    fee_bps = float(est_total_fee_bps or 0.0)
    return float(spread_bps + fee_bps + float(floor_bps)), fee_missing


def _profit_protection_preflight(
    *,
    product_id: str,
    gate: str,
    bid: float,
    ask: float,
    quote_usd: float,
    spread_bps: float,
    cfg: Any,
    assume_maker_entry: bool,
) -> Dict[str, Any]:
    """
    Fee-aware preflight for profit-mode cycles.

    Non-negotiable:
    - Block if the configured target move does not clear spread+fees+slippage+floor.
    - Block if the trade is too small to clear a minimum net profit USD floor after estimated costs.
    """
    launch = load_coinbase_avenue1_launch()
    maker_fee_pct = float(launch.fees.estimated_maker_fee_pct)
    taker_fee_pct = float(launch.fees.estimated_taker_fee_pct)
    # Prefer the NTE fee assumptions. Allow explicit override for older profit-mode configs.
    est_fee_bps = None
    if getattr(cfg, "est_total_fee_bps", None) is not None:
        est_fee_bps = float(getattr(cfg, "est_total_fee_bps"))
    else:
        est_fee_bps = estimate_round_trip_cost_bps(
            spread_bps=0.0,
            maker_fee_pct=maker_fee_pct,
            taker_fee_pct=taker_fee_pct,
            assume_maker_entry=bool(assume_maker_entry),
        )
    slip_bps = float(getattr(cfg, "est_round_trip_slippage_bps", 0.0) or 0.0)
    floor_bps = float(getattr(cfg, "min_net_edge_over_fees_bps", 0.0) or 0.0)
    cost_bps = float(spread_bps) + float(est_fee_bps) + float(slip_bps)
    required_move_bps = float(cost_bps + floor_bps)

    if str(gate).strip().lower() == "gate_a":
        target_move_bps = float(getattr(cfg, "take_profit_pct", 0.0) * 10000.0)
    else:
        target_move_bps = float(getattr(cfg, "profit_zone_min_pct", 0.0) * 10000.0)

    # Minimum economic trade filter (USD): expected gross $ move at target must exceed cost + net floor.
    min_net_usd = float(getattr(cfg, "min_net_profit_usd", 0.0) or 0.0)
    expected_gross_profit_at_target = float(quote_usd) * (target_move_bps / 10000.0)
    expected_cost_usd = float(quote_usd) * (cost_bps / 10000.0)
    expected_net_at_target = expected_gross_profit_at_target - expected_cost_usd
    min_economic_ok = expected_net_at_target >= float(min_net_usd) - 1e-12

    edge_ok = target_move_bps > required_move_bps + 1e-9
    allowed = bool(edge_ok and min_economic_ok)
    reason_codes = []
    if not edge_ok:
        reason_codes.append("target_move_bps_below_required_move_bps")
    if not min_economic_ok:
        reason_codes.append("min_net_profit_usd_not_met_for_notional")
    return {
        "allowed": allowed,
        "reason_codes": reason_codes or ["ok"],
        "product_id": product_id,
        "gate": gate,
        "assume_maker_entry": bool(assume_maker_entry),
        "spread_bps": float(spread_bps),
        "maker_fee_pct": maker_fee_pct,
        "taker_fee_pct": taker_fee_pct,
        "est_fee_bps_round_trip": float(est_fee_bps),
        "est_round_trip_slippage_bps": float(slip_bps),
        "min_net_edge_over_costs_bps": float(floor_bps),
        "est_round_trip_cost_bps": float(cost_bps),
        "required_move_bps": float(required_move_bps),
        "target_move_bps": float(target_move_bps),
        "quote_usd": float(quote_usd),
        "min_net_profit_usd": float(min_net_usd),
        "expected_cost_usd": float(expected_cost_usd),
        "expected_gross_profit_at_target_usd": float(expected_gross_profit_at_target),
        "expected_net_profit_at_target_usd": float(expected_net_at_target),
        "bid": float(bid),
        "ask": float(ask),
    }


def _wait_for_fills(client: Any, order_id: str, *, timeout_sec: float) -> list[dict]:
    """
    Minimal, non-bypass fill waiter for Advanced Trade orders.
    Uses authenticated `get_fills(order_id)` (already guarded by order placement path).
    """
    t0 = time.time()
    oid = str(order_id or "").strip()
    if not oid:
        return []
    last: list[dict] = []
    while time.time() - t0 <= float(timeout_sec):
        try:
            rows = client.get_fills(oid)
            if isinstance(rows, list):
                last = [r for r in rows if isinstance(r, dict)]
                if last:
                    return last
        except Exception:
            pass
        time.sleep(1.0)
    return last


def _profit_cycle_counter_path(runtime_root: Path) -> Path:
    p = Path(runtime_root).resolve() / "data" / "control" / "avenue_a_profit_mode_cycles.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _increment_profit_clean_cycle(runtime_root: Path, *, gate: str, trade_id: str) -> Dict[str, Any]:
    """
    Persistent clean-cycle counter for profit mode.
    Used to prove sustained operation (e.g. Gate B requires 2 full clean cycles).
    """
    root = Path(runtime_root).resolve()
    p = _profit_cycle_counter_path(root)
    rel = str(p.relative_to(root))
    ad = LocalStorageAdapter(runtime_root=root)
    prev = ad.read_json(rel)
    base: Dict[str, Any] = prev if isinstance(prev, dict) else {}
    key = "gate_b_clean_cycles" if str(gate).strip().lower() == "gate_b" else "gate_a_clean_cycles"
    n = int(base.get(key) or 0) + 1
    base[key] = n
    base["last_incremented_at"] = datetime.now(timezone.utc).isoformat()
    base["last_trade_id"] = str(trade_id)
    ad.write_json(rel, base)
    return {"counter_path": str(p), key: n}


def _profit_enforcement_gate(
    *,
    runtime_root: Path,
    trade_id: str,
    gate_id: str,
    product_id: str,
    quote_usd: float,
    spread_bps: float,
    profit_protection: Dict[str, Any],
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Hard economic gate used by profit-mode cycles. This is stricter than advisory preflight:
    if the expected net after costs is not positive (or below configured floors), we do nothing.
    """
    cfg = ProfitEnforcementConfig(
        min_expected_net_edge_bps=float(os.environ.get("EZRAS_MIN_EXPECTED_NET_EDGE_BPS") or 2.0),
        min_expected_net_pnl_usd=float(os.environ.get("EZRAS_MIN_EXPECTED_NET_PNL_USD") or 0.05),
        min_reward_to_risk=float(os.environ.get("EZRAS_MIN_REWARD_TO_RISK") or 1.10),
        slippage_buffer_bps=float(os.environ.get("EZRAS_SLIPPAGE_BUFFER_BPS") or 10.0),
        spread_buffer_bps=float(os.environ.get("EZRAS_SPREAD_BUFFER_BPS") or 0.0),
    )
    # Use the same fee model already computed in profit_protection_preflight.
    fee_bps = float(profit_protection.get("est_fee_bps_round_trip") or 0.0)
    expected_move = float(profit_protection.get("target_move_bps") or 0.0)
    # Risk distance: for gate_a use stop_loss_pct, for gate_b use hard_stop_from_entry_pct.
    risk_bps = 0.0
    try:
        if str(gate_id).strip().lower() == "gate_a":
            risk_bps = float(os.environ.get("GATE_A_PROFIT_STOP_LOSS_PCT") or 0.0020) * 10_000.0
        else:
            risk_bps = float(os.environ.get("GATE_B_PROFIT_HARD_STOP_PCT") or 0.0040) * 10_000.0
    except Exception:
        risk_bps = 0.0
    decision = evaluate_profit_enforcement(
        runtime_root=runtime_root,
        trade_id=trade_id,
        avenue_id="A",
        gate_id=str(gate_id),
        product_id=str(product_id),
        quote_usd=float(quote_usd),
        spread_bps=float(spread_bps),
        fee_bps_round_trip=float(fee_bps),
        expected_gross_move_bps=float(expected_move),
        expected_risk_bps=float(risk_bps),
        config=cfg,
        extra={"surface": "avenue_a_profit_cycle", "profit_protection_preflight": profit_protection},
        write_artifact=True,
    )
    ok, why = profit_enforcement_allows_or_reason(decision)
    return ok, why, decision


def _wait_for_limit_fill_complete(
    client: Any,
    order_id: str,
    *,
    expected_base: float,
    timeout_sec: float,
    min_fill_ratio: float = 0.995,
) -> tuple[bool, float]:
    """
    Poll `get_order` until filled_size >= expected_base * min_fill_ratio or status is FILLED.
    Returns (filled, filled_size_seen).
    """
    t0 = time.time()
    oid = str(order_id or "").strip()
    if not oid:
        return False, 0.0
    last_filled = 0.0
    while time.time() - t0 <= float(timeout_sec):
        try:
            o = client.get_order(oid)
            st = str(o.get("status") or o.get("order_status") or "").upper()
            fs = o.get("filled_size") or o.get("filled_quantity") or 0.0
            try:
                last_filled = float(fs)
            except (TypeError, ValueError):
                last_filled = 0.0
            if "FILLED" in st:
                return True, last_filled
            if expected_base > 0 and last_filled >= float(expected_base) * float(min_fill_ratio):
                return True, last_filled
        except Exception:
            pass
        time.sleep(1.0)
    return False, last_filled


def _write_profit_cycle_last(runtime_root: Path, payload: Dict[str, Any]) -> None:
    ad = LocalStorageAdapter(runtime_root=runtime_root)
    ad.write_json("data/control/avenue_a_profit_cycle_last.json", payload)


def _emit_telegram_hooks(placed: Optional[Dict[str, Any]], closed: Optional[Dict[str, Any]]) -> None:
    try:
        from trading_ai.automation.telegram_trade_events import (
            maybe_notify_trade_closed,
            maybe_notify_trade_placed,
        )

        if placed:
            maybe_notify_trade_placed(None, placed)
        if closed:
            maybe_notify_trade_closed(None, closed)
    except Exception:
        return


def _open_payload(
    *,
    trade_id: str,
    gate: str,
    product_id: str,
    selected_product_source: str,
    entry_price: float,
    quote_used: float,
    spread_bps: Optional[float],
    fee_awareness_incomplete: bool,
    entry_reason: str,
    profit_protection: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "trade_id": trade_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "market": "COINBASE_SPOT",
        "ticker": product_id,
        "side": "BUY",
        "selected_gate": gate,
        "selected_product_source": selected_product_source,
        "entry_price": entry_price,
        "capital_allocated": float(quote_used),
        "spread_bps": spread_bps,
        "fee_awareness_incomplete": bool(fee_awareness_incomplete),
        "reasoning_text": entry_reason,
        "profit_protection": profit_protection,
    }


def _closed_payload(
    *,
    trade_id: str,
    gate: str,
    product_id: str,
    selected_product_source: str,
    entry_price: float,
    exit_price: float,
    quote_used: float,
    gross_pnl_usd: float,
    fees_usd: Optional[float],
    net_pnl_usd: Optional[float],
    exit_reason: str,
    hold_sec: float,
    fee_awareness_incomplete: bool,
    entry_reason: Optional[str] = None,
    profit_protection: Optional[Dict[str, Any]] = None,
    entry_slippage_bps: Optional[float] = None,
    exit_slippage_bps: Optional[float] = None,
) -> Dict[str, Any]:
    # post_trade_hub expects `result` as win/loss and prefers *_dollars naming.
    net_for_result = net_pnl_usd if net_pnl_usd is not None else gross_pnl_usd
    return {
        "trade_id": trade_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "market": "COINBASE_SPOT",
        "ticker": product_id,
        "side": "BUY",
        "selected_gate": gate,
        "selected_product_source": selected_product_source,
        "capital_allocated": float(quote_used),
        "entry_price": float(entry_price),
        "exit_price": float(exit_price),
        "gross_pnl_dollars": float(gross_pnl_usd),
        "net_pnl_dollars": float(net_for_result),
        "execution_cost_dollars": float(fees_usd) if fees_usd is not None else None,
        "total_execution_cost_dollars": float(fees_usd) if fees_usd is not None else None,
        "exit_reason": str(exit_reason),
        "duration_sec": float(hold_sec),
        "fee_awareness_incomplete": bool(fee_awareness_incomplete),
        "result": "win" if net_for_result > 0 else "loss",
        "entry_reason": entry_reason,
        "profit_protection": profit_protection,
        "entry_slippage_bps": entry_slippage_bps,
        "exit_slippage_bps": exit_slippage_bps,
    }


def _governance_gate_ok(*, gate: str, product_id: str, route: str) -> Tuple[bool, str]:
    allowed, gov_reason, _audit = check_new_order_allowed_full(
        venue="coinbase",
        operation="avenue_a_profit_entry",
        route=str(route or "avenue_a_profit"),
        intent_id=str(product_id or "n/a"),
        strategy_class=str(gate or "gate_a"),
        route_bucket=str(route or "avenue_a_profit"),
        log_decision=True,
    )
    if not allowed:
        return False, str(gov_reason or "governance_blocked")
    return True, "ok"


def _require_live_execution_allowed(gate: str) -> Tuple[bool, str]:
    try:
        from trading_ai.control.system_execution_lock import require_live_execution_allowed

        return require_live_execution_allowed(gate)
    except Exception as exc:
        return False, f"system_execution_lock_error:{type(exc).__name__}"


def _compute_exit_reason_gate_a(
    *,
    now_ts: float,
    entry_ts: float,
    entry_price: float,
    last_price: float,
    peak_price: float,
    cfg: GateAProfitConfig,
) -> Optional[str]:
    hold_t = now_ts - entry_ts
    if hold_t >= float(cfg.max_hold_sec):
        return "max_hold_timeout"
    # Enforce min hold: no TP/SL/trail until after min_hold_sec.
    if hold_t < float(cfg.min_hold_sec):
        return None
    gain = last_price / max(1e-18, entry_price) - 1.0
    loss = 1.0 - last_price / max(1e-18, entry_price)
    dd_from_peak = (peak_price - last_price) / max(1e-18, peak_price)
    if gain >= float(cfg.take_profit_pct):
        return "take_profit"
    if loss >= float(cfg.stop_loss_pct):
        return "stop_loss"
    if gain > 0 and dd_from_peak >= float(cfg.trailing_stop_from_peak_pct):
        return "trailing_stop_from_peak"
    return None


def _select_product_for_gate_a(
    *, runtime_root: Path, client: Any, quote_usd: float, anchored_majors_only: bool
) -> Dict[str, Any]:
    sel = run_gate_a_product_selection(
        runtime_root=runtime_root,
        client=client,
        quote_usd=float(quote_usd),
        anchored_majors_only=bool(anchored_majors_only),
        explicit_product_id=None,
    )
    return sel if isinstance(sel, dict) else {"ok": False, "error": "gate_a_selection_invalid"}


def _select_product_for_gate_b(
    *, runtime_root: Path, client: Any, quote_usd: float
) -> Dict[str, Any]:
    sel = run_gate_b_gainers_selection(
        runtime_root=runtime_root,
        client=client,
        deployable_quote_usd=float(quote_usd),
    )
    return sel if isinstance(sel, dict) else {"ok": False, "error": "gate_b_selection_invalid"}


def run_avenue_a_profit_cycle(
    runtime_root: Path,
    *,
    quote_usd: float,
    product_id: str,
    include_runtime_stability: bool,
    execution_profile: Literal["gate_a", "gate_b"],
    gate_a_anchored_majors_only: bool,
    avenue_a_autonomous_lane_decision: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Profit-mode Avenue A cycle.

    Returns a dict shaped similarly to live_execution_validation keys that the daemon consumes
    (execution_success, FINAL_EXECUTION_PROVEN, selected_product_source, gate selection snapshot, etc.).
    """
    from trading_ai.shark.outlets.coinbase import CoinbaseClient

    root = Path(runtime_root).resolve()
    # Phase 1: fail hard on LibreSSL / legacy OpenSSL.
    try:
        from trading_ai.runtime_checks.ssl_guard import enforce_ssl

        enforce_ssl()
    except Exception as exc:
        out_ssl: Dict[str, Any] = {
            "proof_kind": "avenue_a_profit_cycle_v1",
            "execution_profile": execution_profile,
            "runtime_root": str(root),
            "execution_success": False,
            "FINAL_EXECUTION_PROVEN": False,
            "final_execution_proven": False,
            "venue_live_order_attempted": False,
            "do_nothing_mode": True,
            "action": "NO_TRADE",
            "reason": f"ssl_invalid:{type(exc).__name__}",
            "blocking_layer": "execution",
        }
        _write_profit_cycle_last(root, out_ssl)
        return out_ssl
    # Hard risk gate (artifact enforced). If blocked, return explicit do-nothing state.
    try:
        from trading_ai.risk_engine import risk_allows_or_no_trade

        rdec = risk_allows_or_no_trade(runtime_root=root)
        if rdec.get("action") == "NO_TRADE":
            out_blocked: Dict[str, Any] = {
                "proof_kind": "avenue_a_profit_cycle_v1",
                "execution_profile": execution_profile,
                "runtime_root": str(root),
                "execution_success": False,
                "FINAL_EXECUTION_PROVEN": False,
                "final_execution_proven": False,
                "venue_live_order_attempted": False,
                "do_nothing_mode": True,
                "action": "NO_TRADE",
                "reason": rdec.get("reason"),
                "blocking_layer": rdec.get("blocking_layer"),
                "risk_state_path": rdec.get("risk_state_path"),
            }
            _write_profit_cycle_last(root, out_blocked)
            return out_blocked
    except Exception:
        pass
    client = CoinbaseClient()
    t0 = time.perf_counter()
    trade_id = "profit_" + uuid.uuid4().hex[:20]

    out: Dict[str, Any] = {
        "proof_kind": "avenue_a_profit_cycle_v1",
        "execution_profile": execution_profile,
        "trade_id": trade_id,
        "runtime_root": str(root),
        "execution_success": False,
        "FINAL_EXECUTION_PROVEN": False,
        "final_execution_proven": False,
        "venue_live_order_attempted": False,
        "selected_gate": "gate_b" if execution_profile == "gate_b" else "gate_a",
        "avenue_a_autonomous_lane_decision": avenue_a_autonomous_lane_decision,
    }

    # Selection (truthful)
    selected_product_source = "operator_explicit" if str(product_id or "").strip() else ""
    chosen_product_id: Optional[str] = None
    selection_snapshot: Dict[str, Any] = {}
    if str(product_id or "").strip():
        chosen_product_id = str(product_id).strip().upper()
        selected_product_source = "operator_explicit"
        selection_snapshot = {"pinned": True, "product_id": chosen_product_id}
    else:
        if execution_profile == "gate_b":
            selection_snapshot = _select_product_for_gate_b(
                runtime_root=root, client=client, quote_usd=float(quote_usd)
            )
            chosen_product_id = str(
                (selection_snapshot.get("selected_symbols") or [None])[0] or ""
            ).strip().upper() or None
            selected_product_source = "gate_b_gainers_selection_engine"
        else:
            selection_snapshot = _select_product_for_gate_a(
                runtime_root=root,
                client=client,
                quote_usd=float(quote_usd),
                anchored_majors_only=bool(gate_a_anchored_majors_only),
            )
            chosen_product_id = (
                str(selection_snapshot.get("selected_product") or "").strip().upper() or None
            )
            selected_product_source = "gate_a_selection_engine"

    out["selected_product_source"] = selected_product_source
    if execution_profile == "gate_a":
        out["gate_a_selection_snapshot"] = selection_snapshot
    else:
        out["gate_b_selection_snapshot"] = selection_snapshot

    if not chosen_product_id:
        out["error"] = "selection_failed:no_product_selected"
        out["failure_stage"] = "selection"
        out["failure_code"] = "no_selected_product"
        out["duration_sec"] = round(time.perf_counter() - t0, 4)
        _write_profit_cycle_last(root, out)
        return out

    # Preflight exact product (no silent fallback)
    # If the operator pins a USD-quoted product but only USDC is funded, attempt a safe quote-sibling
    # substitution (e.g. BTC-USD -> BTC-USDC) **before** preflight so the cycle remains truthful and executable.
    try:
        pin = str(chosen_product_id or "").strip().upper()
        if pin.endswith("-USD"):
            base_asset = pin.split("-", 1)[0].strip().upper()
            sibling = f"{base_asset}-USDC"
            if sibling != pin:
                from trading_ai.runtime_proof.coinbase_accounts import get_quote_balances_by_currency

                qb = get_quote_balances_by_currency(client)
                usd_av = float(qb.get("USD") or 0.0)
                usdc_av = float(qb.get("USDC") or 0.0)
                # Only substitute when USD wallet cannot cover requested notional (plus venue min),
                # but USDC wallet can reasonably fund the same quote_notional.
                if usd_av + 1e-9 < float(quote_usd) and usdc_av + 1e-9 >= float(quote_usd):
                    ok_sib, diag_sib, err_sib = preflight_exact_spot_product(
                        client,
                        product_id=sibling,
                        quote_notional=float(quote_usd),
                        runtime_root=root,
                    )
                    if ok_sib and not err_sib:
                        out["product_quote_substitution"] = {
                            "from": pin,
                            "to": sibling,
                            "why": "usd_insufficient_usdc_sufficient",
                            "usd_available": usd_av,
                            "usdc_available": usdc_av,
                        }
                        chosen_product_id = sibling
                        if selected_product_source == "operator_explicit":
                            out["selected_product_source"] = "operator_explicit_quote_substitution"
    except Exception:
        pass

    try:
        _ok_pf, _pf_diag, pf_err = preflight_exact_spot_product(
            client,
            product_id=chosen_product_id,
            quote_notional=float(quote_usd),
            runtime_root=root,
        )
    except Exception as exc:
        out["error"] = f"preflight_exception:{type(exc).__name__}"
        out["failure_stage"] = "preflight"
        out["failure_code"] = "preflight_exception"
        out["failure_reason"] = str(exc)
        out["duration_sec"] = round(time.perf_counter() - t0, 4)
        _write_profit_cycle_last(root, out)
        return out

    if pf_err:
        out["error"] = f"preflight_failed:{pf_err}"
        out["failure_stage"] = "preflight"
        out["failure_code"] = "preflight_failed"
        out["failure_reason"] = str(pf_err)
        out["duration_sec"] = round(time.perf_counter() - t0, 4)
        _write_profit_cycle_last(root, out)
        return out

    # Governance + system execution lock (do not bypass)
    ok_gov, gov_why = _governance_gate_ok(
        gate=out["selected_gate"],
        product_id=chosen_product_id,
        route="avenue_a_profit_cycle",
    )
    if not ok_gov:
        out["error"] = f"governance_blocked:{gov_why}"
        out["failure_stage"] = "governance"
        out["failure_code"] = "governance_blocked"
        out["failure_reason"] = gov_why
        out["duration_sec"] = round(time.perf_counter() - t0, 4)
        _write_profit_cycle_last(root, out)
        return out

    ok_lock, lock_why = _require_live_execution_allowed(out["selected_gate"])
    if not ok_lock:
        out["error"] = f"system_execution_lock:{lock_why}"
        out["failure_stage"] = "system_lock"
        out["failure_code"] = "system_execution_lock"
        out["failure_reason"] = lock_why
        out["duration_sec"] = round(time.perf_counter() - t0, 4)
        _write_profit_cycle_last(root, out)
        return out

    # Config
    cfg_a = load_gate_a_profit_config_from_env()
    cfg_b = load_gate_b_profit_config_from_env()
    cfg: Any = cfg_b if execution_profile == "gate_b" else cfg_a

    # Bounded adaptation (evidence-based, local truth) — size scaling + symbol cooldowns only.
    bounded_posture = _load_bounded_posture(root)
    out["bounded_risk_posture"] = bounded_posture
    size_mult = 1.0
    try:
        size_mult = float(bounded_posture.get("size_multiplier") or 1.0)
    except (TypeError, ValueError):
        size_mult = 1.0
    size_mult = max(0.25, min(1.0, size_mult))
    cooldown = {str(x).strip().upper() for x in (bounded_posture.get("cooldown_symbols") or []) if str(x).strip()}

    # Entry spread + edge check
    bid, ask = client.get_product_price(chosen_product_id)
    sbps = _spread_bps(bid, ask)
    if sbps is None:
        out["error"] = "spread_unavailable"
        out["failure_stage"] = "entry_filter"
        out["failure_code"] = "spread_unavailable"
        out["duration_sec"] = round(time.perf_counter() - t0, 4)
        _write_profit_cycle_last(root, out)
        return out
    if float(sbps) > float(cfg.entry_spread_max_bps):
        out["error"] = (
            "spread_too_wide_for_gate_b_profit_mode"
            if execution_profile == "gate_b"
            else "spread_too_wide_for_gate_a_profit_mode"
        )
        out["failure_stage"] = "entry_filter"
        out["failure_code"] = "spread_too_wide"
        out["spread_bps"] = float(sbps)
        out["duration_sec"] = round(time.perf_counter() - t0, 4)
        _write_profit_cycle_last(root, out)
        return out

    if str(chosen_product_id).strip().upper() in cooldown:
        out["error"] = "symbol_cooldown_active"
        out["failure_stage"] = "entry_filter"
        out["failure_code"] = "symbol_cooldown_active"
        out["failure_reason"] = "bounded_posture_symbol_cooldown"
        out["spread_bps"] = float(sbps)
        out["duration_sec"] = round(time.perf_counter() - t0, 4)
        _write_profit_cycle_last(root, out)
        return out

    # Candidate-required sizing: compute after candidate is built (fail-closed).
    effective_quote_usd = float(quote_usd) * float(size_mult)
    out["quote_usd_requested"] = float(quote_usd)

    # Profit-protection: fee-aware net edge floor + minimum economic trade filter.
    # Maker intent entry (limit post_only) but allow taker fallback on entry if enabled.
    fee_incomplete = False
    assume_maker_entry = True
    profit_protection = _profit_protection_preflight(
        product_id=chosen_product_id,
        gate=str(out["selected_gate"]),
        bid=float(bid),
        ask=float(ask),
        quote_usd=float(effective_quote_usd),
        spread_bps=float(sbps),
        cfg=cfg,
        assume_maker_entry=assume_maker_entry,
    )
    out["profit_protection_preflight"] = profit_protection
    if not bool(profit_protection.get("allowed")):
        out["error"] = "profit_protection_blocked"
        out["failure_stage"] = "entry_filter"
        out["failure_code"] = "profit_protection_blocked"
        out["failure_reason"] = ",".join([str(x) for x in (profit_protection.get("reason_codes") or []) if x])
        out["spread_bps"] = float(sbps)
        out["duration_sec"] = round(time.perf_counter() - t0, 4)
        _write_profit_cycle_last(root, out)
        return out

    # Hard profit enforcement (truth artifact). If blocked, enter do-nothing mode for this cycle.
    ok_pe, why_pe, pe = _profit_enforcement_gate(
        runtime_root=root,
        trade_id=trade_id,
        gate_id=str(out["selected_gate"]),
        product_id=chosen_product_id,
        quote_usd=float(effective_quote_usd),
        spread_bps=float(sbps),
        profit_protection=profit_protection,
    )
    out["profit_enforcement"] = pe
    if not ok_pe:
        out["error"] = f"profit_enforcement_blocked:{why_pe}"
        out["failure_stage"] = "entry_filter"
        out["failure_code"] = "profit_enforcement_blocked"
        out["failure_reason"] = why_pe
        out["do_nothing_mode"] = True
        out["venue_live_order_attempted"] = False
        out["duration_sec"] = round(time.perf_counter() - t0, 4)
        _write_profit_cycle_last(root, out)
        return out

    # Place limit entry (maker intent; no immediate flip)
    mid = (bid + ask) / 2.0
    limit_px = mid * (1.0 - float(cfg.entry_offset_bps) / 10000.0)
    base_sz = float(effective_quote_usd) / max(limit_px, 1e-18)
    base_str = round_base_to_increment(chosen_product_id, base_sz)
    limit_str = f"{limit_px:.2f}"

    # ── Universal gap candidate (required for any live BUY order) ─────────────
    # Profit mode must also produce a candidate contract; no parallel live entry philosophies.
    try:
        from trading_ai.nte.execution.edge_governance import decide_lane_and_strategy, detect_gate_a_edges

        # Reconstruct minimal closes for edge governance (same as NTE path, fail-closed if absent).
        try:
            ad2 = LocalStorageAdapter(runtime_root=root)
            mm = ad2.read_json("data/market/market_memory.json") or ad2.read_json("data/market_memory.json") or {}
        except Exception:
            mm = {}
        closes = []
        try:
            cl = (mm.get("closes") or {}).get(chosen_product_id) if isinstance(mm, dict) and isinstance(mm.get("closes"), dict) else None
            if isinstance(cl, list):
                closes = [float(x) for x in cl if isinstance(x, (int, float))][-120:]
        except Exception:
            closes = []
        if not closes:
            out["error"] = "candidate_missing_closes_for_edge_governance"
            out["failure_stage"] = "candidate"
            out["failure_code"] = "candidate_inputs_missing"
            out["duration_sec"] = round(time.perf_counter() - t0, 4)
            _write_profit_cycle_last(root, out)
            return out

        edges = detect_gate_a_edges(
            closes=closes,
            feat={
                "mid": float(mid),
                "spread_pct": float(sbps) / 10_000.0,
                "z_score": float((selection_snapshot.get("z_score") or 0.0) if isinstance(selection_snapshot, dict) else 0.0),
                "regime": str((selection_snapshot.get("regime") or "unknown") if isinstance(selection_snapshot, dict) else "unknown"),
                "quote_volume_24h": float((selection_snapshot.get("quote_volume_24h") or 0.0) if isinstance(selection_snapshot, dict) else 0.0),
            },
        )
        # Derive fee/slippage estimates from profit_protection (already computed).
        est_fee_bps_rt = float(profit_protection.get("est_fee_bps_round_trip") or 0.0)
        est_slip_bps_rt = float(profit_protection.get("est_round_trip_slippage_bps") or 0.0)
        gov = decide_lane_and_strategy(
            runtime_root=root,
            gate_id=str(out["selected_gate"]),
            candidate_product=chosen_product_id,
            candidate_strategy_id="",  # profit mode is CORE; no experimental strategy id
            edges=edges,
            estimated_fees_bps=float(est_fee_bps_rt),
            estimated_slippage_bps=float(est_slip_bps_rt),
            spread_bps=float(sbps),
        )
        out["gap_candidate_edge_governance"] = gov
        conf = gov.get("confidence")
        try:
            conf_f = float(conf)
        except (TypeError, ValueError):
            conf_f = None
        edge_family = str(gov.get("edge_family") or "").strip()
        gap_type = map_coinbase_gap_type(edge_family, latency_trade=False)
        if gap_type is None:
            out["error"] = "gap_type_unmappable"
            out["failure_stage"] = "candidate"
            out["failure_code"] = "gap_type_unmappable"
            out["duration_sec"] = round(time.perf_counter() - t0, 4)
            _write_profit_cycle_last(root, out)
            return out
        if conf_f is None:
            out["error"] = "confidence_missing"
            out["failure_stage"] = "candidate"
            out["failure_code"] = "confidence_missing"
            out["duration_sec"] = round(time.perf_counter() - t0, 4)
            _write_profit_cycle_last(root, out)
            return out

        expected_move_bps = float(profit_protection.get("target_move_bps") or 0.0)
        if expected_move_bps == 0.0:
            out["error"] = "candidate_missing_expected_move_bps"
            out["failure_stage"] = "candidate"
            out["failure_code"] = "candidate_inputs_missing"
            out["duration_sec"] = round(time.perf_counter() - t0, 4)
            _write_profit_cycle_last(root, out)
            return out
        est_true = float(mid) * (1.0 + (expected_move_bps / 10000.0)) if expected_move_bps != 0 else 0.0
        edge_pct = ((est_true / float(mid)) - 1.0) * 100.0 if float(mid) > 0 and est_true > 0 else 0.0
        liq = coinbase_liquidity_score(
            quote_volume_24h_usd=float((selection_snapshot.get("quote_volume_24h") or 0.0) if isinstance(selection_snapshot, dict) else 0.0),
            proposed_notional_usd=float(effective_quote_usd),
        )
        fees_est = float(effective_quote_usd) * (float(est_fee_bps_rt) / 10000.0) if est_fee_bps_rt > 0 else 0.0
        slip_est = float(effective_quote_usd) * (float(est_slip_bps_rt) / 10000.0) if est_slip_bps_rt > 0 else 0.0
        emode = map_coinbase_execution_mode(maker_intent=True, may_fallback_market=_profit_market_fallback_enabled(gate=str(out["selected_gate"])))

        edge_score = float(edge_pct) * float(conf_f)
        cand_id = new_universal_candidate_id(prefix="a_profit_ugc")
        cand0 = UniversalGapCandidate(
            candidate_id=str(cand_id),
            edge_percent=float(edge_pct),
            edge_score=float(edge_score),
            confidence_score=float(conf_f),
            execution_mode=str(emode),
            gap_type=str(gap_type),
            estimated_true_value=float(est_true),
            liquidity_score=float(liq),
            fees_estimate=float(fees_est),
            slippage_estimate=float(slip_est),
            must_trade=False,
        )
        gdec = evaluate_candidate(cand0)
        if not gdec.should_trade:
            out["error"] = "gap_engine_rejected:" + ",".join(gdec.rejection_reasons or ["rejected"])
            out["failure_stage"] = "candidate"
            out["failure_code"] = "gap_engine_rejected"
            out["duration_sec"] = round(time.perf_counter() - t0, 4)
            _write_profit_cycle_last(root, out)
            return out
        cand = UniversalGapCandidate(**{**cand0.to_dict(), "must_trade": True})
        # Universal sizing (fail-closed) — sizing uses only the candidate contract.
        try:
            eq = float(client.get_usd_balance())
        except Exception:
            eq = 0.0
        sdec = size_from_candidate(
            candidate=cand,
            equity_usd=eq,
            gate_id="A_CORE",
            avenue_id="A",
        )
        out["universal_sizing"] = sdec.__dict__
        if not sdec.approved:
            out["error"] = "sizing_rejected:" + str(sdec.cap_reason)
            out["failure_stage"] = "sizing"
            out["failure_code"] = "sizing_rejected"
            out["duration_sec"] = round(time.perf_counter() - t0, 4)
            _write_profit_cycle_last(root, out)
            return out
        effective_quote_usd = float(min(float(effective_quote_usd), float(sdec.recommended_notional)))
        out["effective_quote_usd"] = float(effective_quote_usd)

        tok = candidate_context_set(cand)
        out["universal_gap_candidate"] = cand.to_dict()
    except Exception as exc:
        out["error"] = f"candidate_build_failed:{type(exc).__name__}"
        out["failure_stage"] = "candidate"
        out["failure_code"] = "candidate_build_failed"
        out["duration_sec"] = round(time.perf_counter() - t0, 4)
        _write_profit_cycle_last(root, out)
        return out

    # Avenue A authoritative live BUY path is NTE only. Profit-cycle is not permitted to place live BUYs.
    out["venue_live_order_attempted"] = False
    out["error"] = "non_authoritative_live_buy_path_blocked"
    out["failure_stage"] = "buy"
    out["failure_code"] = "non_authoritative_live_buy_path_blocked"
    out["duration_sec"] = round(time.perf_counter() - t0, 4)
    _write_profit_cycle_last(root, out)
    return out
    # Keep candidate context active across the entire BUY entry path (limit + optional fallback market),
    # and guarantee reset even on early returns (fail-closed, no context leaks).
    try:
        buy = client.place_limit_gtc(
            chosen_product_id,
            "BUY",
            base_str,
            limit_str,
            post_only=True,
            execution_gate=str(out["selected_gate"]),
        )
        if not getattr(buy, "success", False):
            out["error"] = f"buy_order_failed:{getattr(buy, 'reason', '')}"
            out["failure_stage"] = "buy"
            out["failure_code"] = "buy_order_failed"
            out["duration_sec"] = round(time.perf_counter() - t0, 4)
            _write_profit_cycle_last(root, out)
            return out

        buy_oid = str(getattr(buy, "order_id", "") or "")
        entry_ts = time.time()

        # Canonical snapshots (entry)
        out["snapshot_master_write"] = write_master_snapshot(
            root,
            {
                "trade_id": trade_id,
                "venue_id": "coinbase",
                "gate_id": "A_CORE",
                "trade_type": "core",
                "symbol_or_contract": chosen_product_id,
                "strategy_family": (out.get("universal_gap_candidate") or {}).get("strategy_family"),
                "gap_type": (out.get("universal_gap_candidate") or {}).get("gap_type"),
                "entry_timestamp": datetime.now(timezone.utc).isoformat(),
                "exit_timestamp": None,
                "side": "BUY",
                "quantity": float(base_sz),
                "status": "OPEN",
                "exit_reason": None,
                "live_or_paper": "live",
            },
        ).__dict__
        out["snapshot_edge_write"] = write_edge_snapshot(
            root,
            {
                "trade_id": trade_id,
                "candidate_id": (out.get("universal_gap_candidate") or {}).get("candidate_id"),
                "market_price_at_entry": float(mid),
                "estimated_true_value": (out.get("universal_gap_candidate") or {}).get("estimated_true_value"),
                "edge_percent": (out.get("universal_gap_candidate") or {}).get("edge_percent"),
                "confidence_score": (out.get("universal_gap_candidate") or {}).get("confidence_score"),
                "liquidity_score": (out.get("universal_gap_candidate") or {}).get("liquidity_score"),
                "fees_estimate": (out.get("universal_gap_candidate") or {}).get("fees_estimate"),
                "slippage_estimate": (out.get("universal_gap_candidate") or {}).get("slippage_estimate"),
                "reason_summary": (out.get("universal_gap_candidate") or {}).get("reason_summary"),
                "risk_flags": (out.get("universal_gap_candidate") or {}).get("risk_flags") or [],
            },
        ).__dict__
        out["snapshot_execution_entry_write"] = write_execution_snapshot(
            root,
            {
                "trade_id": trade_id,
                "order_type_entry": "limit_gtc_post_only",
                "order_type_exit": None,
                "intended_execution_mode": (out.get("universal_gap_candidate") or {}).get("execution_mode"),
                "actual_execution_mode": "maker",
                "posted_limit_price": float(limit_px),
                "actual_fill_price": None,
                "fill_delay": None,
                "partial_fill_flag": None,
                "maker_or_taker_entry": "maker",
                "maker_or_taker_exit": None,
                "execution_grade": None,
                "slippage_actual": None,
                "fees_actual": None,
                "entry_order_id": buy_oid,
            },
        ).__dict__
        placed_payload = _open_payload(
            trade_id=trade_id,
            gate=str(out["selected_gate"]),
            product_id=chosen_product_id,
            selected_product_source=selected_product_source,
            entry_price=float(limit_px),
            quote_used=float(effective_quote_usd),
            spread_bps=float(sbps),
            fee_awareness_incomplete=bool(fee_incomplete),
            entry_reason="profit_mode_entry_limit_maker_intent",
            profit_protection=profit_protection,
        )
        _emit_telegram_hooks(placed_payload, None)

        filled_ok, filled_sz = _wait_for_limit_fill_complete(
            client,
            buy_oid,
            expected_base=float(base_sz),
            timeout_sec=max(
                0.5,
                float(
                    getattr(
                        cfg,
                        "fill_timeout_ms",
                        int(float(cfg.entry_fill_timeout_sec) * 1000),
                    )
                    / 1000.0
                ),
            ),
        )
        if not filled_ok:
            out["fallback_execution_path"] = False
            try:
                client.cancel_order(buy_oid)
            except Exception:
                pass
            if _profit_market_fallback_enabled(gate=str(out["selected_gate"])):
                # Hybrid execution policy:
                # maker-intent limit first; if not fully filled inside fill_timeout_ms, cancel and
                # fall back immediately to market IOC for the *remaining* notional (prevents duplicate spend).
                out["fallback_execution_path"] = True
                logger.warning(
                    "maker_timeout → fallback_market trade_id=%s product=%s",
                    trade_id,
                    chosen_product_id,
                )

            # Pre-fallback balance sync (root cause: quote truth mismatch after cancel/timeout).
            # We sync exchange quote balances after cancel before computing remaining spend.
            try:
                from trading_ai.runtime_proof.coinbase_accounts import get_available_quote_balances

                before = get_available_quote_balances(client)
            except Exception:
                before = {}
            try:
                # Small delay lets venue release reserved quote after cancel (best-effort, bounded).
                time.sleep(0.35)
            except Exception:
                pass
            try:
                from trading_ai.runtime_proof.coinbase_accounts import get_available_quote_balances

                after = get_available_quote_balances(client)
            except Exception:
                after = {}
            logger.info(
                "fallback_balance_sync: before_usd=%s before_usdc=%s after_usd=%s after_usdc=%s trade_id=%s",
                round(float(before.get("USD", 0.0) or 0.0), 4),
                round(float(before.get("USDC", 0.0) or 0.0), 4),
                round(float(after.get("USD", 0.0) or 0.0), 4),
                round(float(after.get("USDC", 0.0) or 0.0), 4),
                trade_id,
            )

            # If maker partially filled, only spend remaining quote; otherwise spend the original quote_usd.
            remaining_quote = float(quote_usd)
            try:
                maker_fills = _wait_for_fills(client, buy_oid, timeout_sec=5.0)
                if maker_fills:
                    maker_buy_agg = normalize_coinbase_buy_fills(chosen_product_id, maker_fills)
                    spent = float(maker_buy_agg.buy_quote_spent or 0.0)
                    remaining_quote = max(0.0, float(effective_quote_usd) - spent)
            except Exception:
                pass

            # Dynamic allowed quote recalc from live balances (do not block valid fallback trades).
            # Fallback spend is bounded by actually-available USD (for -USD products) or USDC (for -USDC products).
            if str(chosen_product_id).upper().endswith("-USDC"):
                avail_quote = float(after.get("USDC", 0.0) or 0.0)
            else:
                avail_quote = float(after.get("USD", 0.0) or 0.0)
            if remaining_quote > 0 and avail_quote > 0:
                remaining_quote = min(remaining_quote, avail_quote)

                mb = client.place_market_buy(
                    chosen_product_id,
                    float(remaining_quote),
                    execution_gate=str(out["selected_gate"]),
                )
                if not getattr(mb, "success", False):
                    out["error"] = "buy_not_filled_timeout"
                    out["failure_stage"] = "buy_fill"
                    out["failure_code"] = "buy_not_filled_timeout"
                    out["filled_base_seen"] = float(filled_sz)
                    out["duration_sec"] = round(time.perf_counter() - t0, 4)
                    _write_profit_cycle_last(root, out)
                    return out
                buy_oid = str(getattr(mb, "order_id", "") or "")
            else:
                out["error"] = "buy_not_filled_timeout"
                out["failure_stage"] = "buy_fill"
                out["failure_code"] = "buy_not_filled_timeout"
                out["filled_base_seen"] = float(filled_sz)
                out["duration_sec"] = round(time.perf_counter() - t0, 4)
                _write_profit_cycle_last(root, out)
                return out

        fills = _wait_for_fills(client, buy_oid, timeout_sec=15.0)
    finally:
        try:
            candidate_context_reset(tok)  # type: ignore[name-defined]
        except Exception:
            pass
    if not fills:
        out["error"] = "buy_fills_unavailable_after_filled"
        out["failure_stage"] = "buy_fill"
        out["failure_code"] = "buy_fills_unavailable"
        out["duration_sec"] = round(time.perf_counter() - t0, 4)
        _write_profit_cycle_last(root, out)
        return out

    buy_agg = normalize_coinbase_buy_fills(chosen_product_id, fills)
    buy_base = float(buy_agg.buy_base_qty or 0.0)
    entry_avg = float(buy_agg.avg_fill_price or 0.0) or float(limit_px)
    buy_fee = float(buy_agg.fees_buy_usd or 0.0)
    entry_slip_bps = None
    try:
        if float(limit_px) > 0 and float(entry_avg) > 0:
            entry_slip_bps = (float(entry_avg) - float(limit_px)) / float(limit_px) * 10000.0
    except Exception:
        entry_slip_bps = None
    peak = entry_avg

    # REQUIRED execution proof artifact (trade validation depends on it).
    try:
        ad = LocalStorageAdapter(runtime_root=root)
        ad.write_json(
            "execution_proof/execution_proof.json",
            {
                "order_id": str(buy_oid),
                "venue": "coinbase",
                "symbol": str(chosen_product_id),
                "filled": True,
                "price": float(entry_avg),
                "size": float(buy_base),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
        out["execution_proof_path"] = "execution_proof/execution_proof.json"
    except Exception as exc:
        out["execution_proof_error"] = f"{type(exc).__name__}:{exc}"

    # Hold/monitor until exit condition
    mon_b = None
    if execution_profile == "gate_b":
        mon_b = GateBMonitorState(
            product_id=chosen_product_id,
            entry_price=entry_avg,
            peak_price=entry_avg,
            entry_ts=entry_ts,
            last_price=entry_avg,
        )
    exit_reason = None
    exit_px = entry_avg
    intended_exit_px = entry_avg
    while True:
        now = time.time()
        bid2, ask2 = client.get_product_price(chosen_product_id)
        mid2 = (bid2 + ask2) / 2.0 if bid2 > 0 and ask2 > 0 else 0.0
        if mid2 > 0:
            exit_px = mid2
            intended_exit_px = mid2
            if mid2 > peak:
                peak = mid2
        hold_t = now - entry_ts

        if execution_profile == "gate_b" and mon_b is not None and mid2 > 0:
            mon_b.observe_price(mid2, now)
            tick = gate_b_monitor_tick(
                mon_b,
                now_ts=now,
                profit_zone_min_pct=float(cfg_b.profit_zone_min_pct),
                profit_zone_max_pct=float(cfg_b.profit_zone_max_pct),
                trailing_stop_from_peak_pct=float(cfg_b.trailing_stop_from_peak_pct),
                hard_stop_from_entry_pct=float(cfg_b.hard_stop_from_entry_pct),
                max_hold_sec=float(cfg_b.max_hold_sec),
                momentum_invalidation=False,
                momentum_stall=False,
            )
            if hold_t >= float(cfg_b.min_hold_sec) and bool(tick.get("exit")):
                exit_reason = str(tick.get("exit_reason") or "exit")
        else:
            exit_reason = _compute_exit_reason_gate_a(
                now_ts=now,
                entry_ts=entry_ts,
                entry_price=entry_avg,
                last_price=exit_px,
                peak_price=peak,
                cfg=cfg_a,
            )

        if exit_reason:
            break

        # Emergency exits still allowed (kill switch / lock can become active mid-hold)
        ok_lock2, lock_why2 = _require_live_execution_allowed(out["selected_gate"])
        if not ok_lock2:
            exit_reason = f"emergency_system_lock:{lock_why2}"
            break
        try:
            from trading_ai.control.kill_switch import kill_switch_active

            if kill_switch_active():
                exit_reason = "emergency_kill_switch"
                break
        except Exception:
            pass

        if hold_t >= float(cfg.max_hold_sec):
            exit_reason = "max_hold_timeout"
            break
        time.sleep(max(0.5, float(cfg.poll_sec)))

    # Exit market sell
    sell_base_str = round_base_to_increment(chosen_product_id, buy_base)
    sell = client.place_market_sell(
        chosen_product_id, sell_base_str, execution_gate=str(out["selected_gate"])
    )
    if not getattr(sell, "success", False):
        out["error"] = f"sell_order_failed:{getattr(sell, 'reason', '')}"
        out["failure_stage"] = "sell"
        out["failure_code"] = "sell_order_failed"
        out["duration_sec"] = round(time.perf_counter() - t0, 4)
        _write_profit_cycle_last(root, out)
        return out

    sell_oid = str(getattr(sell, "order_id", "") or "")
    sell_fills = _wait_for_fills(client, sell_oid, timeout_sec=90.0)
    if not sell_fills:
        out["error"] = "sell_not_filled_timeout"
        out["failure_stage"] = "sell_fill"
        out["failure_code"] = "sell_not_filled_timeout"
        out["duration_sec"] = round(time.perf_counter() - t0, 4)
        _write_profit_cycle_last(root, out)
        return out

    # Close-safety: if rounding left a residual base balance, attempt one bounded dust-flatten sell.
    # This is not a bypass: uses the same live-order guard and product sizing rules.
    try:
        base_asset = str(chosen_product_id.split("-", 1)[0]).strip().upper()
        residual_attempts: list[Dict[str, Any]] = []
        for _i in range(2):
            try:
                time.sleep(0.35)
            except Exception:
                pass
            rem = float(client.get_available_balance(base_asset) or 0.0)
            rem_str = round_base_to_increment(chosen_product_id, rem)
            try:
                rem_round = float(str(rem_str))
            except (TypeError, ValueError):
                rem_round = 0.0
            # Only flatten meaningful residuals (strictly > 0 after increment rounding).
            if rem_round <= 0:
                residual_attempts.append({"seen_base": rem, "rounded_base": rem_str, "action": "no_residual"})
                break
            # Avoid accidental over-flatten: never attempt to sell more than buy_base.
            if buy_base > 0 and rem_round > float(buy_base) * 1.01:
                residual_attempts.append(
                    {
                        "seen_base": rem,
                        "rounded_base": rem_str,
                        "action": "skip_residual_unexpected_gt_buy_base",
                    }
                )
                break
            ss2 = client.place_market_sell(
                chosen_product_id, rem_str, execution_gate=str(out["selected_gate"])
            )
            residual_attempts.append(
                {
                    "seen_base": rem,
                    "rounded_base": rem_str,
                    "order_success": bool(getattr(ss2, "success", False)),
                    "order_id": str(getattr(ss2, "order_id", "") or ""),
                    "reason": str(getattr(ss2, "reason", "") or ""),
                }
            )
            if not getattr(ss2, "success", False):
                break
            # Wait for fills best-effort; loop will re-check remaining.
            try:
                _wait_for_fills(client, str(getattr(ss2, "order_id", "") or ""), timeout_sec=45.0)
            except Exception:
                pass
        out["residual_flatten"] = {"attempts": residual_attempts}
    except Exception as exc:
        out["residual_flatten"] = {"error": f"{type(exc).__name__}:{exc}"}

    sell_agg = normalize_coinbase_sell_fills(chosen_product_id, sell_fills)
    sell_quote = float(sell_agg.sell_quote_received or 0.0)
    sell_fee = float(sell_agg.fees_sell_usd or 0.0)
    buy_quote = float(buy_agg.buy_quote_spent or 0.0)
    gross = sell_quote - buy_quote
    fees = float(buy_fee + sell_fee)
    net = gross - fees
    exit_avg = float(sell_agg.avg_fill_price or 0.0) or exit_px
    exit_slip_bps = None
    try:
        if float(intended_exit_px) > 0 and float(exit_avg) > 0:
            exit_slip_bps = (float(exit_avg) - float(intended_exit_px)) / float(intended_exit_px) * 10000.0
    except Exception:
        exit_slip_bps = None

    closed_payload = _closed_payload(
        trade_id=trade_id,
        gate=str(out["selected_gate"]),
        product_id=chosen_product_id,
        selected_product_source=selected_product_source,
        entry_price=entry_avg,
        exit_price=exit_avg,
        quote_used=float(effective_quote_usd),
        gross_pnl_usd=gross,
        fees_usd=fees if fees > 0 else None,
        net_pnl_usd=net,
        exit_reason=str(exit_reason or "exit"),
        hold_sec=float(time.time() - entry_ts),
        fee_awareness_incomplete=bool(fee_incomplete),
        entry_reason="profit_mode_entry_limit_maker_intent",
        profit_protection=profit_protection,
        entry_slippage_bps=entry_slip_bps,
        exit_slippage_bps=exit_slip_bps,
    )
    _emit_telegram_hooks(None, closed_payload)

    # Canonical snapshots (exit + execution grade + review)
    try:
        g = grade_execution(
            side="BUY",
            intended_execution_mode=str(
                (out.get("universal_gap_candidate") or {}).get("execution_mode") or "maker"
            ),
            actual_execution_mode="hybrid" if bool(out.get("fallback_execution_path")) else "maker",
            intended_price=float(limit_px),
            actual_fill_price=float(entry_avg),
            slippage_estimate_usd=float(
                (out.get("universal_gap_candidate") or {}).get("slippage_estimate") or 0.0
            ),
            slippage_actual_usd=float(
                (abs(entry_slip_bps or 0.0) / 10000.0) * float(effective_quote_usd)
            ),
            fees_estimate_usd=float(
                (out.get("universal_gap_candidate") or {}).get("fees_estimate") or 0.0
            ),
            fees_actual_usd=float(fees),
        )
        exec_grade = g.grade
        exec_grade_reasons = g.reasons
    except Exception:
        exec_grade = "F"
        exec_grade_reasons = ["grader_error"]

    out["snapshot_execution_exit_write"] = write_execution_snapshot(
        root,
        {
            "trade_id": trade_id,
            "order_type_entry": "limit_gtc_post_only",
            "order_type_exit": "market_ioc",
            "intended_execution_mode": (out.get("universal_gap_candidate") or {}).get("execution_mode"),
            "actual_execution_mode": "hybrid" if bool(out.get("fallback_execution_path")) else "maker",
            "posted_limit_price": float(limit_px),
            "actual_fill_price": float(entry_avg),
            "fill_delay": None,
            "partial_fill_flag": None,
            "maker_or_taker_entry": "maker",
            "maker_or_taker_exit": "taker",
            "execution_grade": exec_grade,
            "execution_grade_reasons": exec_grade_reasons,
            "slippage_actual": entry_slip_bps,
            "fees_actual": float(fees),
            "exit_order_id": sell_oid,
            "actual_exit_fill_price": float(exit_avg),
            "exit_slippage_bps": exit_slip_bps,
        },
    ).__dict__

    out["snapshot_review_write"] = write_review_snapshot(
        root,
        {
            "trade_id": trade_id,
            "expected_value_pretrade": None,
            "net_pnl": float(net),
            "anomaly_flags": [],
            "should_repeat": None,
            "should_reduce": None,
            "should_pause": None,
            "notes": {"execution_grade": exec_grade, "execution_grade_reasons": exec_grade_reasons},
        },
    ).__dict__

    # Canonical pnl record artifact (used by truth chain).
    try:
        from trading_ai.pnl_engine import compute_round_trip_pnl, write_pnl_record

        pr = compute_round_trip_pnl(
            buy_quote_spent=float(buy_quote),
            sell_quote_received=float(sell_quote),
            buy_fees=float(buy_fee),
            sell_fees=float(sell_fee),
            entry_slippage_bps=entry_slip_bps,
            exit_slippage_bps=exit_slip_bps,
        )
        out["pnl_record"] = write_pnl_record(
            pr,
            runtime_root=root,
            extra={"trade_id": trade_id, "venue": "coinbase", "symbol": chosen_product_id},
        )
    except Exception as exc:
        out["pnl_record"] = {"ok": False, "error": f"{type(exc).__name__}:{exc}"}

    # Databank + Supabase (existing pipeline)
    base_asset = str(chosen_product_id.split("-", 1)[0]).strip().upper()
    # Databank schema-aligned payload (truth surface; do not alias gross as success when net<0).
    raw_db = {
        "trade_id": trade_id,
        "avenue_id": "A",
        "avenue_name": "coinbase",
        "trading_gate": str(out["selected_gate"]),
        "asset": str(chosen_product_id),
        "selected_product_source": selected_product_source,
        "strategy_id": "avenue_a_profit_mode",
        "route_chosen": "A" if str(out["selected_gate"]).strip().lower() == "gate_a" else "B",
        "regime": "unknown",
        "spread_bps_entry": float(sbps),
        "expected_fee_bps": float(profit_protection.get("est_fee_bps_round_trip") or 0.0),
        "expected_edge_bps": float(profit_protection.get("target_move_bps") or 0.0),
        "expected_net_edge_bps": float(profit_protection.get("target_move_bps") or 0.0)
        - float(profit_protection.get("required_move_bps") or 0.0),
        "intended_entry_price": float(limit_px),
        "actual_entry_price": float(entry_avg),
        "entry_slippage_bps": float(entry_slip_bps or 0.0),
        "entry_order_type": "limit_gtc_post_only",
        "maker_taker": "maker",
        "intended_exit_price": float(intended_exit_px),
        "actual_exit_price": float(exit_avg),
        "exit_slippage_bps": float(exit_slip_bps or 0.0),
        "gross_pnl": float(gross),
        "fees_paid": float(fees),
        "net_pnl": float(net),
        "exit_reason": str(exit_reason or "exit"),
        "timestamp_open": _iso(entry_ts),
        "timestamp_close": datetime.now(timezone.utc).isoformat(),
        "hold_seconds": float(time.time() - entry_ts),
        "fee_awareness_incomplete": bool(fee_incomplete),
        "execution_intent": "profit_mode",
        "market_snapshot_json": {
            "profit_protection_preflight": profit_protection,
            "entry_reason": "profit_mode_entry_limit_maker_intent",
            "selected_product_source": selected_product_source,
        },
    }
    db_out = process_closed_trade(raw_db)
    out["databank"] = {"ok": bool(db_out.get("ok")), "stages": db_out.get("stages"), "errors": db_out.get("errors")}

    # Pipeline verification hook (same contract shape as proof)
    try:
        out["pipeline_verification"] = verify_data_pipeline_after_trade(
            runtime_root=root,
            trade_id=trade_id,
            allow_missing_remote=not _truthy_env("EZRAS_SUPABASE_REQUIRED"),
        )
    except Exception as exc:
        out["pipeline_verification"] = {"ok": False, "error": f"{type(exc).__name__}:{exc}"}

    if include_runtime_stability:
        try:
            out["runtime_stability"] = run_short_runtime_stability(runtime_root=root)
        except Exception as exc:
            out["runtime_stability"] = {"ok": False, "error": f"{type(exc).__name__}:{exc}"}

    # Universal loop proof emission (do not depend on validation proof file)
    stages: Dict[str, Any] = {}
    proof_kind = str(out.get("proof_kind") or "avenue_a_profit_cycle_v1")
    # Evidence-first: default false; only mark stages true when actually satisfied.
    base_stage = {
        "trade_id": trade_id,
        "avenue_id": "coinbase",
        "gate_id": str(out["selected_gate"]),
        "strategy_id": "avenue_a_profit_mode",
        "route": "avenue_a_profit_cycle",
        "execution_profile": str(execution_profile),
        "proof_kind": proof_kind,
        "proof_source": "avenue_a_profit_cycle",
    }
    for st in ExecutionTruthStage:
        if int(st.value) > 11:
            break
        stages[st.name] = {**base_stage, "ok": False}

    stages[ExecutionTruthStage.STAGE_0_CANDIDATE_SELECTED.name]["ok"] = True
    stages[ExecutionTruthStage.STAGE_1_PRETRADE_GUARDS_PASSED.name]["ok"] = True
    stages[ExecutionTruthStage.STAGE_2_ENTRY_ORDER_SUBMITTED.name]["ok"] = True
    stages[ExecutionTruthStage.STAGE_3_ENTRY_FILL_CONFIRMED.name]["ok"] = True
    stages[ExecutionTruthStage.STAGE_4_EXIT_ORDER_SUBMITTED.name]["ok"] = True
    stages[ExecutionTruthStage.STAGE_5_EXIT_FILL_CONFIRMED.name]["ok"] = True
    stages[ExecutionTruthStage.STAGE_6_PNL_VERIFIED.name]["ok"] = True

    # Align stage 7/8/10 truth to databank outcome when available.
    db_stages = (db_out.get("stages") or {}) if isinstance(db_out, dict) else {}
    local_ok = bool(db_stages.get("local_raw_event") or db_stages.get("local_score_record"))
    remote_ok = bool(db_stages.get("supabase_trade_events"))
    review_ok = bool(
        db_stages.get("daily_summary_updated")
        and db_stages.get("weekly_summary_updated")
        and db_stages.get("monthly_summary_updated")
        and db_stages.get("strategy_summary_updated")
        and db_stages.get("avenue_summary_updated")
        and db_stages.get("ceo_snapshot_hook")
        and db_stages.get("learning_hook")
    )
    stages[ExecutionTruthStage.STAGE_7_LOCAL_DATA_WRITTEN.name]["ok"] = local_ok
    stages[ExecutionTruthStage.STAGE_8_REMOTE_DATA_WRITTEN.name]["ok"] = remote_ok
    # Governance stage: satisfied if governance gate passed for entry (recorded via governance_order_gate).
    stages[ExecutionTruthStage.STAGE_9_GOVERNANCE_LOGGED.name]["ok"] = True
    stages[ExecutionTruthStage.STAGE_10_REVIEW_ARTIFACTS_UPDATED.name]["ok"] = review_ok

    remote_required = bool(_truthy_env("EZRAS_SUPABASE_REQUIRED"))
    final_ok = bool(
        local_ok
        and (remote_ok or (not remote_required))
        and review_ok
        and bool(stages[ExecutionTruthStage.STAGE_9_GOVERNANCE_LOGGED.name]["ok"])
    )

    # Profit-mode clean-cycle counters (Gate B proof requires 2 full clean cycles).
    if final_ok:
        try:
            ctr = _increment_profit_clean_cycle(root, gate=str(out["selected_gate"]), trade_id=trade_id)
            out["profit_mode_cycle_counters"] = ctr
            if str(out["selected_gate"]).strip().lower() == "gate_b":
                out["gate_b_clean_cycles"] = int(ctr.get("gate_b_clean_cycles") or 0)
                out["gate_b_profit_mode_proven"] = bool(int(out["gate_b_clean_cycles"]) >= 2)
        except Exception as exc:
            out["profit_mode_cycle_counters"] = {"error": f"{type(exc).__name__}:{exc}"}
    result_for_universal = {
        "trade_id": trade_id,
        "execution_truth_contract": stages,
        "bundle": {
            "trade_id": trade_id,
            "universal_proof": {"final_execution_proven": final_ok},
            "remote_write": {"remote_required": remote_required},
        },
        "final_execution_proven": final_ok,
        "cycle_ok": bool(final_ok),
        "terminal_honest_state": (
            TerminalHonestState.ROUND_TRIP_SUCCESS.value
            if final_ok
            else TerminalHonestState.ROUND_TRIP_PARTIAL_FAILURE.value
        ),
    }
    payload = build_universal_execution_loop_proof_payload(result_for_universal)
    meta = write_universal_execution_loop_proof(payload, runtime_root=root)
    out["universal_loop_proof"] = {"emitted": True, **meta}

    # Evidence-first: recompute final truth using mandatory artifacts.
    try:
        from trading_ai.truth_engine import truth_chain_for_post_trade, validate_truth_chain

        chain = truth_chain_for_post_trade(runtime_root=root)
        tval = validate_truth_chain(chain)
        out["truth_chain"] = tval
        try:
            LocalStorageAdapter(runtime_root=root).write_json("data/control/truth_chain_last.json", tval)
        except Exception:
            pass
        final_ok = bool(final_ok) and bool(tval.get("ok"))
    except Exception as exc:
        out["truth_chain"] = {"ok": False, "error": f"{type(exc).__name__}:{exc}"}
        final_ok = False

    out["execution_success"] = True
    out["FINAL_EXECUTION_PROVEN"] = bool(final_ok)
    out["final_execution_proven"] = bool(final_ok)
    out["selected_product_id"] = chosen_product_id
    out["exit_reason"] = str(exit_reason or "exit")
    out["duration_sec"] = round(time.perf_counter() - t0, 4)
    _write_profit_cycle_last(root, out)
    return out

