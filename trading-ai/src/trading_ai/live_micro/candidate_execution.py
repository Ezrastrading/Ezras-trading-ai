from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


def _truthy_env(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


def _read_json(p: Path) -> Dict[str, Any]:
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _append_jsonl(p: Path, row: Dict[str, Any]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")


def _load_micro_max_notional(runtime_root: Path) -> float:
    lim = _read_json(runtime_root / "data" / "control" / "live_session_limits.json")
    file_v = 0.0
    try:
        file_v = float(lim.get("max_notional_usd") or 0.0)
    except Exception:
        file_v = 0.0
    env_v = 0.0
    try:
        env_v = float((os.environ.get("EZRA_LIVE_MICRO_MAX_NOTIONAL_USD") or "").strip() or 0.0)
    except Exception:
        env_v = 0.0
    if file_v > 0 and env_v > 0:
        return min(file_v, env_v)
    return file_v if file_v > 0 else env_v


def _pick_candidate_item(cq: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], int]:
    items = list(cq.get("items") or [])
    for i in range(len(items) - 1, -1, -1):
        it = items[i]
        if not isinstance(it, dict):
            continue
        if str(it.get("status") or "new").strip().lower() not in ("new", "queued"):
            continue
        if str(it.get("gate_id") or "").strip().lower() != "gate_b":
            continue
        pid = str(it.get("product_id") or "").strip().upper()
        if not pid:
            continue
        return dict(it), i
    return None, -1


def run_live_micro_candidate_execution_once(*, runtime_root: Path) -> Dict[str, Any]:
    """
    Execute one micro-live candidate (Coinbase market BUY) from candidate_queue.

    Safety:
    - Only runs when EZRA_LIVE_MICRO_ENABLED + COINBASE_EXECUTION_ENABLED + EZRA_LIVE_MICRO_AUTOTRADE_ENABLED.
    - live_order_guard and live_micro contract are enforced inside CoinbaseClient (guarded order POST).
    """
    root = Path(runtime_root).resolve()
    os.environ["EZRAS_RUNTIME_ROOT"] = str(root)

    if not _truthy_env("EZRA_LIVE_MICRO_ENABLED"):
        return {"ok": True, "skipped": True, "reason": "micro_disabled"}
    if not _truthy_env("COINBASE_EXECUTION_ENABLED"):
        return {"ok": True, "skipped": True, "reason": "coinbase_execution_disabled"}
    if not _truthy_env("EZRA_LIVE_MICRO_AUTOTRADE_ENABLED"):
        return {"ok": True, "skipped": True, "reason": "autotrade_disabled"}

    # Contract gate (fail-closed).
    from trading_ai.deployment.live_micro_enablement import assert_live_micro_runtime_contract

    okc, err, audit = assert_live_micro_runtime_contract(root, phase="live_micro_candidate_execution")
    if not okc:
        return {"ok": False, "blocked": True, "reason": err, "audit": audit}

    from trading_ai.global_layer.review_storage import ReviewStorage

    st = ReviewStorage()
    cq = st.load_json("candidate_queue.json")
    it, idx = _pick_candidate_item(cq)
    if not it:
        return {"ok": True, "skipped": True, "reason": "no_candidates"}

    pid = str(it.get("product_id") or "").strip().upper()
    max_notional = max(0.0, float(_load_micro_max_notional(root) or 0.0))
    if max_notional <= 0:
        return {"ok": False, "blocked": True, "reason": "missing_or_invalid_max_notional_usd"}

    events_p = root / "data" / "control" / "live_micro_execution_events.jsonl"
    _append_jsonl(
        events_p,
        {
            "ts": time.time(),
            "event": "candidate_selected",
            "product_id": pid,
            "gate_id": "gate_b",
            "candidate_item": it,
        },
    )
    out: Dict[str, Any] = {"ok": True, "product_id": pid, "gate_id": "gate_b"}

    # Hard execution lock (fail-closed): Gate B must be enabled.
    try:
        from trading_ai.control.system_execution_lock import require_live_execution_allowed

        ok_lock, why = require_live_execution_allowed(gate="gate_b", runtime_root=root)
        if not ok_lock:
            _append_jsonl(
                events_p,
                {
                    "ts": time.time(),
                    "event": "blocked",
                    "product_id": pid,
                    "gate_id": "gate_b",
                    "reason": f"execution_lock:{why}",
                },
            )
            return {**out, "skipped": True, "reason": f"execution_lock:{why}"}
    except Exception as exc:
        _append_jsonl(
            events_p,
            {
                "ts": time.time(),
                "event": "blocked",
                "product_id": pid,
                "gate_id": "gate_b",
                "reason": f"execution_lock_error:{type(exc).__name__}",
            },
        )
        return {**out, "skipped": True, "reason": f"execution_lock_error:{type(exc).__name__}"}

    # System health (fail-closed): do not submit while system is marked unhealthy/blocked.
    try:
        from trading_ai.nte.paths import nte_system_health_path

        hp = nte_system_health_path()
        health = _read_json(hp)
        if health and (health.get("healthy") is False or health.get("live_order_guard_blocked") is True):
            _append_jsonl(
                events_p,
                {
                    "ts": time.time(),
                    "event": "blocked",
                    "product_id": pid,
                    "gate_id": "gate_b",
                    "reason": "system_health_blocks_execution",
                    "health_snapshot": {
                        k: health.get(k)
                        for k in (
                            "healthy",
                            "live_order_guard_blocked",
                            "last_block_reason",
                            "degraded_components",
                        )
                    },
                    "health_path": str(hp),
                },
            )
            return {**out, "skipped": True, "reason": "system_health_blocks_execution"}
    except Exception:
        pass

    # Ensure daily-loss guard has a baseline risk state file (safe default: 0 PnL).
    try:
        risk_p = root / "data" / "risk" / "risk_state.json"
        if not risk_p.is_file():
            risk_p.parent.mkdir(parents=True, exist_ok=True)
            risk_p.write_text(json.dumps({"daily_pnl_usd": 0.0}, indent=2) + "\n", encoding="utf-8")
    except Exception:
        pass
    items = list(cq.get("items") or [])
    if 0 <= idx < len(items) and isinstance(items[idx], dict):
        items[idx] = {**items[idx], "status": "selected", "selected_ts": time.time()}
    cq["items"] = items[-300:]
    st.save_json("candidate_queue.json", cq)

    from trading_ai.shark.outlets.coinbase import CoinbaseClient

    client = CoinbaseClient()

    # Execution sizing (halt-safe): never attempt orders that cannot satisfy Coinbase min notional
    # under mission tier caps. This prevents repeated execution failure loops caused by sizing conflicts.
    exchange_min_notional = 10.0
    try:
        from trading_ai.nte.execution.product_rules import venue_min_notional_usd

        exchange_min_notional = float(venue_min_notional_usd(pid))
    except Exception:
        exchange_min_notional = 10.0
    quote_ccy = (pid.split("-")[1] if "-" in pid else "USD").strip().upper()
    quote_balances = None
    try:
        from trading_ai.runtime_proof.coinbase_accounts import get_available_quote_balances

        quote_balances = get_available_quote_balances(client)
    except Exception:
        quote_balances = None
    if not isinstance(quote_balances, dict) or not quote_balances:
        # Fallback: query balances directly (best-effort).
        try:
            usd = float(client.get_usd_balance())
        except Exception:
            usd = 0.0
        try:
            usdc = float(client.get_available_balance("USDC"))
        except Exception:
            usdc = 0.0
        quote_balances = {"USD": usd, "USDC": usdc}
    avail_quote = 0.0
    try:
        avail_quote = float((quote_balances or {}).get(quote_ccy) or 0.0)
    except Exception:
        avail_quote = 0.0
    total_quote = 0.0
    try:
        total_quote = sum(float(v) for v in (quote_balances or {}).values())
    except Exception:
        total_quote = max(avail_quote, 0.0)
    if total_quote <= 0:
        _append_jsonl(
            events_p,
            {
                "ts": time.time(),
                "event": "blocked",
                "product_id": pid,
                "gate_id": "gate_b",
                "reason": "missing_quote_balance_truth",
            },
        )
        return {**out, "skipped": True, "reason": "missing_quote_balance_truth"}

    mission_prob = 0.55
    try:
        mission_prob = float((os.environ.get("EZRA_LIVE_MICRO_MISSION_PROB") or "0.55").strip() or "0.55")
    except Exception:
        mission_prob = 0.55
    # Mission tier cap (matches trading_ai.shark.mission + live_order_guard expectations):
    # - p < 0.63 => blocked
    # - 0.63–0.77 => tier1: 5%
    # - 0.77–0.90 => tier2: 10%
    # - >=0.90 => tier3: 20%
    # Also enforce D1 20% hard cap (same as tier3).
    if mission_prob < 0.63:
        _append_jsonl(
            events_p,
            {
                "ts": time.time(),
                "event": "blocked",
                "product_id": pid,
                "gate_id": "gate_b",
                "reason": "mission_probability_below_min",
                "mission_prob": mission_prob,
            },
        )
        return {**out, "skipped": True, "reason": "mission_probability_below_min"}

    # Mission max tier percent (execution sizing cap).
    # Default derives from mission_prob tiers (existing contract), but allows env override to tighten.
    # - 0.63–0.77 => 5%
    # - 0.77–0.90 => 10%
    # - >=0.90 => 20%
    if mission_prob < 0.77:
        mission_max_tier_pct = 0.05
    elif mission_prob < 0.90:
        mission_max_tier_pct = 0.10
    else:
        mission_max_tier_pct = 0.20
    try:
        raw = (os.environ.get("EZRA_LIVE_MICRO_MISSION_MAX_TIER_PERCENT") or "").strip()
        if raw:
            mission_max_tier_pct = float(raw)
    except Exception:
        pass
    mission_max_tier_pct = max(0.0, min(0.20, float(mission_max_tier_pct)))

    balance_usd = max(0.0, float(avail_quote))
    if balance_usd < 50.0:
        _append_jsonl(
            events_p,
            {
                "ts": time.time(),
                "event": "balance_too_low_warning",
                "product_id": pid,
                "gate_id": "gate_b",
                "balance": float(balance_usd),
                "quote_currency": quote_ccy,
                "message": "Balance too low for Coinbase min notional trading",
            },
        )

    tier_cap = float(balance_usd) * float(mission_max_tier_pct)
    proposed_size = min(float(max_notional), float(tier_cap))

    if float(proposed_size) + 1e-9 < float(exchange_min_notional):
        _append_jsonl(
            events_p,
            {
                "ts": time.time(),
                "event": "execution_skipped",
                "reason": "min_notional_vs_tier_conflict",
                "product_id": pid,
                "gate_id": "gate_b",
                "balance": float(balance_usd),
                "tier_cap": float(tier_cap),
                "required_min": float(exchange_min_notional),
                "mission_max_tier_percent": float(mission_max_tier_pct),
                "max_notional": float(max_notional),
                "mission_prob": float(mission_prob),
            },
        )
        return {**out, "skipped": True, "reason": "min_notional_vs_tier_conflict"}

    quote_usd = float(proposed_size)
    _append_jsonl(
        events_p,
        {
            "ts": time.time(),
            "event": "execution_allowed_size",
            "product_id": pid,
            "gate_id": "gate_b",
            "balance": float(balance_usd),
            "tier_cap": float(tier_cap),
            "final_size": float(quote_usd),
            "required_min": float(exchange_min_notional),
            "mission_max_tier_percent": float(mission_max_tier_pct),
        },
    )
    # Build the required universal candidate context (fail-closed).
    from trading_ai.global_layer.gap_models import (
        UniversalGapCandidate,
        authoritative_live_buy_path_reset,
        authoritative_live_buy_path_set,
        candidate_context_reset,
        candidate_context_set,
        new_universal_candidate_id,
    )

    from trading_ai.global_layer.gap_engine import evaluate_candidate
    from trading_ai.shark.outlets.coinbase import _brokerage_public_request

    t = _brokerage_public_request(f"/market/products/{pid}/ticker")
    t = t if isinstance(t, dict) else {}
    bid = float(t.get("best_bid") or t.get("bid") or 0.0)
    ask = float(t.get("best_ask") or t.get("ask") or 0.0)
    mid = float(t.get("price") or 0.0)
    if mid <= 0 and bid > 0 and ask > 0:
        mid = (bid + ask) / 2.0
    if mid <= 0:
        return {"ok": False, "blocked": True, "reason": "ticker_mid_unavailable", "product_id": pid}

    # Explicit, conservative economics. No hidden defaults.
    edge_pct = float((os.environ.get("EZRA_LIVE_MICRO_EDGE_PCT") or "0.01").strip() or "0.01")
    edge_pct = max(0.0, min(0.05, edge_pct))
    fees_est = float((os.environ.get("EZRA_LIVE_MICRO_FEES_EST_PCT") or "0.006").strip() or "0.006") * quote_usd
    spread_bps = 0.0
    if bid > 0 and ask > 0:
        spread_bps = (ask - bid) / mid * 10000.0
    slippage_est = float((os.environ.get("EZRA_LIVE_MICRO_SLIPPAGE_SPREAD_MULT") or "1.0").strip() or "1.0") * (
        quote_usd * max(0.0, spread_bps) / 10000.0
    )
    liquidity_score = float((os.environ.get("EZRA_LIVE_MICRO_LIQUIDITY_SCORE") or "0.85").strip() or "0.85")
    liquidity_score = max(0.0, min(1.0, liquidity_score))
    confidence = float((os.environ.get("EZRA_LIVE_MICRO_CONFIDENCE_SCORE") or "0.55").strip() or "0.55")
    confidence = max(0.0, min(1.0, confidence))

    cand = UniversalGapCandidate(
        candidate_id=new_universal_candidate_id(prefix="lm"),
        edge_percent=edge_pct,
        edge_score=edge_pct * 100.0,
        confidence_score=confidence,
        execution_mode="taker",
        gap_type="behavioral_gap",
        estimated_true_value=mid,
        liquidity_score=liquidity_score,
        fees_estimate=fees_est,
        slippage_estimate=slippage_est,
        must_trade=False,  # set after gap-engine decision
        risk_flags=["micro_live_candidate_queue"],
    )
    dec = evaluate_candidate(cand)
    must_trade = bool(dec.should_trade)
    cand = UniversalGapCandidate(
        candidate_id=cand.candidate_id,
        edge_percent=cand.edge_percent,
        edge_score=cand.edge_score,
        confidence_score=cand.confidence_score,
        execution_mode=cand.execution_mode,
        gap_type=cand.gap_type,
        estimated_true_value=cand.estimated_true_value,
        liquidity_score=cand.liquidity_score,
        fees_estimate=cand.fees_estimate,
        slippage_estimate=cand.slippage_estimate,
        must_trade=must_trade,
        risk_flags=list(cand.risk_flags or []),
    )

    tok_c = candidate_context_set(cand)
    tok_a = authoritative_live_buy_path_set("nte_only")
    tok_m = None
    try:
        # Mission probability gate requires an explicit context value.
        try:
            from trading_ai.shark.mission import mission_probability_set

            tok_m = mission_probability_set(float(mission_prob))
        except Exception:
            tok_m = None
        _append_jsonl(
            events_p,
            {
                "ts": time.time(),
                "event": "entry_decision",
                "product_id": pid,
                "gate_id": "gate_b",
                "quote_usd": quote_usd,
                "quote_currency": quote_ccy,
                "avail_quote": avail_quote,
                "tier_cap": tier_cap,
                "mission_max_tier_percent": float(mission_max_tier_pct),
                "mission_prob": mission_prob,
                "should_trade": bool(dec.should_trade),
                "rejection_reasons": list(dec.rejection_reasons or []),
                "candidate": cand.to_dict(),
            },
        )
        try:
            from trading_ai.intelligence.crypto_intelligence.recorder import record_micro_candidate_decision

            record_micro_candidate_decision(
                runtime_root=root,
                product_id=pid,
                gate_id="gate_b",
                venue="coinbase",
                quote_usd=float(quote_usd),
                should_trade=bool(dec.should_trade),
                rejection_reasons=list(dec.rejection_reasons or []),
                candidate=cand.to_dict(),
                extra={"source": "live_micro_candidate_queue", "mission_prob": mission_prob},
            )
        except Exception:
            pass
        if not must_trade:
            return {
                **out,
                "ok": True,
                "skipped": True,
                "reason": "gap_engine_rejected",
                "rejection_reasons": list(dec.rejection_reasons or []),
            }
        res = client.place_market_buy(pid, quote_usd, execution_gate="gate_b")
    finally:
        try:
            if tok_m is not None:
                from trading_ai.shark.mission import mission_probability_reset

                mission_probability_reset(tok_m)
        except Exception:
            pass
        try:
            authoritative_live_buy_path_reset(tok_a)
        except Exception:
            pass
        try:
            candidate_context_reset(tok_c)
        except Exception:
            pass
    _append_jsonl(
        events_p,
        {
            "ts": time.time(),
            "event": "order_submitted",
            "product_id": pid,
            "gate_id": "gate_b",
            "order_result": {
                "success": bool(res.success),
                "status": res.status,
                "reason": res.reason,
                "order_id": res.order_id,
            },
        },
    )
    try:
        from trading_ai.intelligence.crypto_intelligence.recorder import link_trade_to_candidate

        link_trade_to_candidate(
            runtime_root=root,
            trade_id=str(res.order_id or ""),
            candidate_id=str(cand.candidate_id),
            setup_family="gate_b::micro::behavioral_gap",
            gate_id="gate_b",
            product_id=pid,
            venue="coinbase",
        )
    except Exception:
        pass

    out = {**out, "order_id": res.order_id, "submitted": bool(res.success)}
    if not res.success or not str(res.order_id or "").strip():
        # Mark queue item failed.
        cq2 = st.load_json("candidate_queue.json")
        its2 = list(cq2.get("items") or [])
        if 0 <= idx < len(its2) and isinstance(its2[idx], dict):
            its2[idx] = {**its2[idx], "status": "submit_failed", "submit_failed_ts": time.time(), "reason": res.reason}
        cq2["items"] = its2[-300:]
        st.save_json("candidate_queue.json", cq2)
        return {**out, "filled": False}

    # Best-effort fill probe (do not block the daemon loop).
    fills = []
    try:
        fills = client.get_fills(str(res.order_id))
    except Exception:
        fills = []
    filled = bool(fills)
    out["filled"] = filled
    _append_jsonl(
        events_p,
        {
            "ts": time.time(),
            "event": "fill_probe",
            "product_id": pid,
            "gate_id": "gate_b",
            "filled": filled,
            "fills_n": len(fills),
        },
    )

    if filled:
        trade = {
            "trade_id": str(res.order_id),
            "avenue_id": "A",
            "gate_id": "gate_b",
            "outlet": "coinbase",
            "market": pid,
            "product_id": pid,
            "live_or_paper": "live",
            "quote_usd": quote_usd,
            "status": "open",
        }
        try:
            from trading_ai.automation.post_trade_hub import execute_post_trade_placed

            tg = execute_post_trade_placed(None, trade)
            out["telegram_placed"] = tg.get("status")
        except Exception as exc:
            out["telegram_placed"] = f"error:{type(exc).__name__}"

        cq3 = st.load_json("candidate_queue.json")
        its3 = list(cq3.get("items") or [])
        if 0 <= idx < len(its3) and isinstance(its3[idx], dict):
            its3[idx] = {**its3[idx], "status": "filled", "filled_ts": time.time(), "order_id": str(res.order_id)}
        cq3["items"] = its3[-300:]
        st.save_json("candidate_queue.json", cq3)

    return out

