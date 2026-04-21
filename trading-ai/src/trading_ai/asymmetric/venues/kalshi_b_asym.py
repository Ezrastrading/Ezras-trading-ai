from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.asymmetric.batching import AsymmetricBatchPlan, build_batch_plan
from trading_ai.asymmetric.config import AsymmetricConfig, load_asymmetric_config
from trading_ai.global_layer.asymmetric_allocator import (
    AsymmetricSizingDecision,
    compute_asymmetric_position_size,
    new_batch_id,
)
from trading_ai.global_layer.asymmetric_ev import AsymmetricEVCosts, compute_asymmetric_ev
from trading_ai.global_layer.asymmetric_models import (
    AsymmetricEVScenario,
    AsymmetricThesisType,
    GateFamily,
    TradeType,
    validate_asymmetric_trade_record,
)
from trading_ai.global_layer.asymmetric_tracker import (
    record_asymmetric_batch,
    record_asymmetric_trade,
    recompute_asymmetric_snapshots,
)
from trading_ai.shark.outlets.kalshi import (
    KalshiClient,
    _kalshi_yes_no_from_market_row,
    _parse_close_timestamp_unix,
    fetch_kalshi_orderbook_best_ask_cents,
)
from trading_ai.storage.storage_adapter import LocalStorageAdapter


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _env_float(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return float(default)
    try:
        return float(raw)
    except ValueError:
        return float(default)


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return int(default)
    try:
        return int(float(raw))
    except ValueError:
        return int(default)


def _env_truthy(name: str, default: str = "false") -> bool:
    return (os.environ.get(name) or default).strip().lower() in ("1", "true", "yes", "y", "on")


@dataclass(frozen=True)
class KalshiAsymCandidate:
    ticker: str
    side: str  # yes|no
    ask_prob: float
    implied_yes: float
    implied_no: float
    estimated_win_prob: float
    close_ts_unix: Optional[float]
    ttr_seconds: Optional[float]
    title: str
    thesis_type: str
    hold_mode: str  # resolution|bounce|hybrid
    evidence: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ticker": self.ticker,
            "side": self.side,
            "ask_prob": self.ask_prob,
            "implied_yes": self.implied_yes,
            "implied_no": self.implied_no,
            "estimated_win_prob": self.estimated_win_prob,
            "close_ts_unix": self.close_ts_unix,
            "ttr_seconds": self.ttr_seconds,
            "title": self.title,
            "thesis_type": self.thesis_type,
            "hold_mode": self.hold_mode,
            "evidence": self.evidence,
        }


def _candidate_price_band() -> Tuple[int, int]:
    lo = max(1, min(99, _env_int("ASYM_B_MIN_CONTRACT_CENTS", 1)))
    hi = max(lo, min(99, _env_int("ASYM_B_MAX_CONTRACT_CENTS", 5)))
    return lo, hi


def _estimated_edge_pctpoints() -> float:
    # Default 0.0 => EV equals implied => negative after fees => fail-closed
    return max(-0.25, min(0.25, _env_float("ASYM_B_ESTIMATED_EDGE_PCTPOINTS", 0.0)))


def _cost_model_per_contract(ask_prob: float) -> AsymmetricEVCosts:
    # Kalshi: 1 contract pays $1 if correct; cost is ask in dollars.
    fee = max(0.0, _env_float("ASYM_B_FEES_USD_PER_CONTRACT", 0.0))
    slip = max(0.0, _env_float("ASYM_B_SLIPPAGE_USD_PER_CONTRACT", 0.0))
    return AsymmetricEVCosts(entry_cost_usd=float(ask_prob), fees_usd=fee, slippage_usd=slip)


def scan_kalshi_penny_candidates(
    *,
    client: KalshiClient,
    max_markets: int = 2500,
) -> List[KalshiAsymCandidate]:
    """
    Scan for penny-priced contracts (1–5c by default). This does NOT assume they are positive-EV.

    Default model is fail-closed: estimated probability == implied unless ASYM_B_ESTIMATED_EDGE_PCTPOINTS is set.
    """
    lo_c, hi_c = _candidate_price_band()
    edge = _estimated_edge_pctpoints()
    now = time.time()

    out: List[KalshiAsymCandidate] = []
    rows = client.fetch_all_open_markets(max_rows=max(100, min(20000, int(max_markets))))
    for m in rows:
        if not isinstance(m, dict):
            continue
        tid = str(m.get("ticker") or "").strip()
        if not tid:
            continue
        title = str(m.get("title") or m.get("subtitle") or "").strip()
        close_ts = _parse_close_timestamp_unix(m)
        ttr = (float(close_ts) - now) if close_ts else None

        # pull best asks from orderbook to avoid stale list quotes
        ya_c, na_c = fetch_kalshi_orderbook_best_ask_cents(tid, client)
        # fall back to market-row asks if orderbook missing
        yes_ask = float(ya_c) / 100.0 if ya_c is not None else None
        no_ask = float(na_c) / 100.0 if na_c is not None else None
        if yes_ask is None or no_ask is None:
            try:
                inner = client.enrich_market_with_detail_and_orderbook(m)
                ya, na, _, _ = _kalshi_yes_no_from_market_row(inner)
                # bids used here are not asks; keep them only as implied placeholders
                if yes_ask is None:
                    yes_ask = float(ya)
                if no_ask is None:
                    no_ask = float(na)
            except Exception:
                pass
        if yes_ask is None and no_ask is None:
            continue

        implied_yes = float(yes_ask) if yes_ask is not None else 0.0
        implied_no = float(no_ask) if no_ask is not None else 0.0

        # Penny asym focuses on cheap convexity: choose whichever side is inside the cents band.
        picks: List[Tuple[str, float]] = []
        if yes_ask is not None:
            yc = int(round(float(yes_ask) * 100))
            if lo_c <= yc <= hi_c:
                picks.append(("yes", float(yes_ask)))
        if no_ask is not None:
            nc = int(round(float(no_ask) * 100))
            if lo_c <= nc <= hi_c:
                picks.append(("no", float(no_ask)))
        if not picks:
            continue

        for side, ask_prob in picks:
            est = max(0.01, min(0.99, float(ask_prob) + float(edge)))
            out.append(
                KalshiAsymCandidate(
                    ticker=tid,
                    side=side,
                    ask_prob=float(ask_prob),
                    implied_yes=implied_yes,
                    implied_no=implied_no,
                    estimated_win_prob=float(est),
                    close_ts_unix=float(close_ts) if close_ts else None,
                    ttr_seconds=float(ttr) if ttr is not None else None,
                    title=title,
                    thesis_type=AsymmetricThesisType.PENNY_ASYMMETRY.value,
                    hold_mode="resolution",
                    evidence={
                        "source": "kalshi_orderbook_best_ask",
                        "candidate_price_band_cents": {"lo": lo_c, "hi": hi_c},
                        "estimated_edge_pctpoints": edge,
                    },
                )
            )

    return out


def _ev_for_candidate_per_contract(c: KalshiAsymCandidate) -> Dict[str, Any]:
    # two-scenario settlement model: resolve true pays $1, else $0.
    sc = [
        AsymmetricEVScenario(scenario_id="win", probability=float(c.estimated_win_prob), payout_usd=1.0, label="settle_true"),
        AsymmetricEVScenario(scenario_id="lose", probability=float(1.0 - c.estimated_win_prob), payout_usd=0.0, label="settle_false"),
    ]
    costs = _cost_model_per_contract(c.ask_prob)
    ev = compute_asymmetric_ev(
        scenarios=sc,
        costs=costs,
        quality_inputs={
            "calibration_score": 0.35,  # conservative until calibrated
            "evidence_score": 0.45,
            "liquidity_score": 0.45,
        },
    )
    return ev.to_dict()


def run_b_asym_cycle(
    *,
    total_capital_usd: float,
    runtime_root: Optional[Path] = None,
    cfg: Optional[AsymmetricConfig] = None,
) -> Dict[str, Any]:
    """
    B_ASYM cycle (Kalshi) — builds a batch plan, scans penny candidates, computes EV, and either:
    - returns a plan-only batch (default), or
    - places micro orders if ASYM_B_EXECUTION_ENABLED=true.
    """
    c = cfg or load_asymmetric_config()
    if not c.enabled:
        return {"ok": False, "action": "NO_TRADE", "reason": "asym_disabled"}
    if "B" not in c.avenue_allowlist:
        return {"ok": False, "action": "NO_TRADE", "reason": "avenue_B_not_allowlisted"}

    gate_id = str(c.gate_id_map.get("B") or "B_ASYM").strip()
    plan = build_batch_plan(venue_id="kalshi", gate_id=gate_id, total_capital_usd=float(total_capital_usd), cfg=c)
    if plan.asym_sub_bucket_usd <= 0:
        return {"ok": False, "action": "NO_TRADE", "reason": "asym_sub_bucket_zero", "batch_plan": plan.to_dict()}

    client = KalshiClient()
    if not client.has_kalshi_credentials():
        return {"ok": False, "action": "NO_TRADE", "reason": "kalshi_no_credentials", "batch_plan": plan.to_dict()}

    max_markets = max(100, min(20000, _env_int("ASYM_B_SCAN_MAX_MARKETS", 2500)))
    cands = scan_kalshi_penny_candidates(client=client, max_markets=max_markets)
    if not cands:
        return {"ok": True, "action": "NO_TRADE", "reason": "no_penny_candidates_found", "batch_plan": plan.to_dict()}

    # Compute EV per contract and filter
    rows: List[Dict[str, Any]] = []
    for cand in cands:
        ev = _ev_for_candidate_per_contract(cand)
        ev_net = float(ev.get("expected_value_net_usd") or 0.0)
        ev_per_dollar = float(ev.get("ev_per_dollar") or 0.0)
        if ev_net <= float(c.min_ev_usd) + 1e-12:
            continue
        if ev_per_dollar <= float(c.min_ev_per_dollar) + 1e-12:
            continue
        rows.append({"candidate": cand, "ev": ev, "ev_net": ev_net, "ev_per_dollar": ev_per_dollar})

    if not rows:
        return {
            "ok": True,
            "action": "NO_TRADE",
            "reason": "no_candidates_passed_ev_thresholds",
            "batch_plan": plan.to_dict(),
            "scan_counts": {"candidates_total": len(cands), "candidates_ev_pass": 0},
        }

    rows.sort(key=lambda r: (float(r["ev_per_dollar"]), float(r["ev_net"])), reverse=True)
    batch_n = min(int(plan.batch_size), max(1, _env_int("ASYM_B_MAX_BATCH_SIZE_HARD", 100)))
    # Basic diversification: cap number of picks per series root (prefix before '-').
    max_per_series = max(1, _env_int("ASYM_B_MAX_PER_SERIES_IN_BATCH", 5))
    chosen: List[Dict[str, Any]] = []
    per_series: Dict[str, int] = {}
    for r in rows:
        cand: KalshiAsymCandidate = r["candidate"]
        ser = cand.ticker.split("-", 1)[0].upper() if "-" in cand.ticker else cand.ticker[:12].upper()
        if per_series.get(ser, 0) >= max_per_series:
            continue
        chosen.append(r)
        per_series[ser] = per_series.get(ser, 0) + 1
        if len(chosen) >= batch_n:
            break

    batch_id = new_batch_id("b_asym")
    exec_enabled = _env_truthy("ASYM_B_EXECUTION_ENABLED", "false")
    paper_only = not exec_enabled

    # size per position (micro)
    sizing: AsymmetricSizingDecision = compute_asymmetric_position_size(
        plan=plan,
        requested_notional_usd=float(plan.max_position_usd),
        cfg=c,
        open_positions_count=0,
    )
    per_pos_usd = float(sizing.recommended_notional_usd)
    if per_pos_usd <= 0:
        return {"ok": True, "action": "NO_TRADE", "reason": "sizing_skip", "batch_plan": plan.to_dict(), "sizing": sizing.to_dict()}

    ad = LocalStorageAdapter(runtime_root=Path(runtime_root) if runtime_root else None)
    placed: List[Dict[str, Any]] = []
    planned: List[Dict[str, Any]] = []

    for idx, r in enumerate(chosen):
        cand: KalshiAsymCandidate = r["candidate"]
        ask = max(0.01, float(cand.ask_prob))
        contracts = max(1, int(per_pos_usd / ask))
        est_cost = contracts * ask
        if est_cost > float(plan.max_position_usd) + 1e-9:
            contracts = max(1, int(float(plan.max_position_usd) / ask))
            est_cost = contracts * ask

        ev = dict(r["ev"])
        ev_net_per_contract = float(ev.get("expected_value_net_usd") or 0.0)
        ev_net_total = ev_net_per_contract * float(contracts)

        trade_id = f"asym_{uuid.uuid4().hex[:14]}"
        record = {
            "trade_id": trade_id,
            "gate_family": GateFamily.ASYMMETRIC.value,
            "gate_id": str(gate_id),
            "trade_type": TradeType.ASYMMETRIC.value,
            "avenue": "B",
            "instrument": cand.ticker,
            "market_type": "kalshi_event_contract",
            "asym_style": "PENNY_CONTRACT_BASKET",
            "asym_thesis_type": str(cand.thesis_type),
            "entry_timestamp_utc": _iso_now(),
            "entry_price": float(ask),
            "quantity": float(contracts),
            "max_loss_usd": float(est_cost),
            "expected_value_net_usd": float(ev_net_total),
            "expected_multiple": float(ev.get("expected_multiple") or 0.0),
            "long_tail_rank": float(r["ev_per_dollar"]),
            "batch_id": batch_id,
            "batch_position_index": int(idx),
            "portfolio_role": "lottery_ev",
            "payout_profile_json": {
                "per_contract_payout_if_win": 1.0,
                "per_contract_payout_if_lose": 0.0,
                "per_contract_cost": float(ask),
            },
            "estimated_probabilities": {
                "resolve_true": float(cand.estimated_win_prob),
                "resolve_false": float(1.0 - float(cand.estimated_win_prob)),
            },
            "estimated_payouts": {"resolve_true": 1.0, "resolve_false": 0.0},
            "resolution_or_event_window": {
                "close_ts_unix": cand.close_ts_unix,
                "ttr_seconds": cand.ttr_seconds,
                "hold_mode": cand.hold_mode,
                "title": cand.title,
            },
            "should_trade": True,
            "ev_result": ev,
            "candidate_evidence": cand.evidence,
        }

        # Hard gate: must validate asymmetric schema before any execution/logging.
        verrs = validate_asymmetric_trade_record(record, allow_probe_without_batch=bool(c.allow_single_probe_without_batch))
        if verrs:
            planned.append({"ok": False, "trade_id": trade_id, "errors": verrs})
            continue

        planned.append({"ok": True, "trade_id": trade_id, "record": record, "estimated_cost_usd": est_cost})

        if paper_only:
            record_asymmetric_trade(record, runtime_root=runtime_root, cfg=c)
            continue

        # Live execution (explicitly opt-in)
        # For penny contracts, allow low probability orders by passing min_order_prob very low.
        min_prob = max(0.01, min(0.99, _env_float("ASYM_B_MIN_ORDER_PROB", 0.01)))
        side_price_cents = int(round(ask * 100.0))
        res = client.place_order(
            ticker=cand.ticker,
            side=str(cand.side).lower(),
            count=int(contracts),
            action="buy",
            order_type="market",
            side_price_cents=side_price_cents,
            min_order_prob=min_prob,
            skip_pretrade_buy_gates=False,
            client_order_id=f"{batch_id}:{idx}:{trade_id}",
        )
        placed.append(
            {
                "trade_id": trade_id,
                "ticker": cand.ticker,
                "side": cand.side,
                "contracts": int(contracts),
                "ask_prob": ask,
                "filled_size": float(res.filled_size or 0.0),
                "filled_price": float(res.filled_price or 0.0),
                "success": res.success is not False and float(res.filled_size or 0.0) > 0,
                "status": res.status,
                "reason": getattr(res, "reason", None),
            }
        )
        record_asymmetric_trade({**record, "execution": placed[-1]}, runtime_root=runtime_root, cfg=c)

    # Persist decision artifact + recompute snapshot shells
    try:
        record_asymmetric_batch(
            {
                "truth_version": "asym_batch_v1",
                "batch_id": batch_id,
                "avenue": "B",
                "gate_id": gate_id,
                "gate_family": GateFamily.ASYMMETRIC.value,
                "start_time_utc": _iso_now(),
                "planned_positions": len(chosen),
                "paper_only": bool(paper_only),
                "scan_counts": {"candidates_total": len(cands), "candidates_ev_pass": len(rows)},
                "diversification": {"max_per_series": max_per_series, "series_counts": per_series},
                "sizing": sizing.to_dict(),
                "batch_plan": plan.to_dict(),
            },
            runtime_root=runtime_root,
        )
    except Exception:
        pass
    decision = {
        "ok": True,
        "action": "BATCH_PLANNED" if planned else "NO_TRADE",
        "paper_only": bool(paper_only),
        "venue_id": "kalshi",
        "gate_id": gate_id,
        "gate_family": GateFamily.ASYMMETRIC.value,
        "batch_id": batch_id,
        "batch_size_target": int(plan.batch_size),
        "batch_size_selected": len(chosen),
        "sizing": sizing.to_dict(),
        "batch_plan": plan.to_dict(),
        "scan_counts": {"candidates_total": len(cands), "candidates_ev_pass": len(rows)},
        "planned_trades": planned,
        "placed_trades": placed,
        "honesty": "Default is paper-only. Live execution requires ASYM_B_EXECUTION_ENABLED=true and still enforces EV+batch tagging gates.",
    }
    ad.write_json("data/asymmetric/b_asym_last_decision.json", decision)
    try:
        recompute_asymmetric_snapshots(runtime_root=runtime_root)
    except Exception:
        pass
    return decision

