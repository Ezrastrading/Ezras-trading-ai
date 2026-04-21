"""
Outcome learning + research automation from *real closed trades*.

Non-negotiable honesty:
- Uses net-after-fees fields from the databank merged event.
- Never claims improvements without artifacts.
- Never changes live trading permissions (writes advisory + bounded posture only).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from trading_ai.nte.databank.local_trade_store import append_jsonl_atomic, databank_memory_root
from trading_ai.runtime_paths import ezras_runtime_root


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _num(x: Any, d: float = 0.0) -> float:
    try:
        return float(x) if x is not None else d
    except (TypeError, ValueError):
        return d


def _s(x: Any) -> str:
    return str(x or "").strip()


def _safe_div(a: float, b: float) -> float:
    if b == 0:
        return 0.0
    return a / b


def _learning_paths(runtime_root: Optional[Path]) -> Dict[str, Path]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    return {
        "trade_learning_jsonl": root / "data" / "learning" / "trade_learning_objects.jsonl",
        "research_queue_jsonl": root / "data" / "research" / "outcome_research_queue.jsonl",
        "ranked_opportunities_json": root / "data" / "research" / "ranked_improvement_opportunities.json",
        "loss_patterns_json": root / "data" / "research" / "recurring_loss_patterns.json",
        "bounded_posture_json": root / "data" / "control" / "bounded_risk_posture.json",
    }


def _append_jsonl_dedup(path: Path, record: Dict[str, Any], *, trade_id: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    return append_jsonl_atomic(path, record, trade_id=trade_id)


def _infer_gate(ev: Mapping[str, Any]) -> str:
    g = _s(ev.get("trading_gate") or ev.get("gate_id") or ev.get("gate") or "")
    return g.lower() if g else "unknown"


def _infer_symbol(ev: Mapping[str, Any]) -> str:
    return _s(ev.get("asset") or ev.get("product_id") or ev.get("market_id") or "")


def _classify_should_have_blocked(ev: Mapping[str, Any]) -> Tuple[bool, List[str]]:
    reasons: List[str] = []
    net = _num(ev.get("net_pnl"))
    fees = _num(ev.get("fees_paid"))
    exp_net_edge = _num(ev.get("expected_net_edge_bps"))
    exp_fee = _num(ev.get("expected_fee_bps"))
    spread = _num(ev.get("spread_bps_entry"))
    slip = abs(_num(ev.get("entry_slippage_bps"))) + abs(_num(ev.get("exit_slippage_bps")))

    # Rule: if expected edge after costs is non-positive, it should not have been entered.
    if exp_net_edge <= 0.0 + 1e-9:
        reasons.append("expected_net_edge_bps_non_positive")
    # Rule: fee-dominant patterns (fees high vs edge).
    if exp_fee > 0 and exp_fee >= max(1.0, abs(exp_net_edge) * 1.0):
        reasons.append("expected_fee_bps_dominant_vs_expected_net_edge_bps")
    # Rule: realized fee drag flipped (gross>0 net<0) indicates the floor is too low.
    gross = _num(ev.get("gross_pnl"))
    if gross > 0 and net < 0:
        reasons.append("realized_fee_drag_flip_gross_positive_net_negative")
    # Rule: micro churn (small notional implied) – approximate via fees magnitude.
    if fees > 0 and abs(net) < fees * 0.25 and net < 0:
        reasons.append("fees_dominated_small_loss")
    # Rule: spread+slippage unusually high.
    if spread + slip > 50.0:
        reasons.append("spread_plus_slippage_extreme_bps")
    return (len(reasons) > 0), reasons


def build_trade_learning_object(
    *,
    merged_trade_event: Mapping[str, Any],
    scores: Mapping[str, Any],
    post_trade_intelligence: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    ev = merged_trade_event
    trade_id = _s(ev.get("trade_id"))
    gate = _infer_gate(ev)
    symbol = _infer_symbol(ev)

    gross = _num(ev.get("gross_pnl"))
    fees = _num(ev.get("fees_paid"))
    net = _num(ev.get("net_pnl"))
    hold = _num(ev.get("hold_seconds"))
    entry_reason = _s(ev.get("entry_reason") or "")
    exit_reason = _s(ev.get("exit_reason") or "")

    slip_entry = _num(ev.get("entry_slippage_bps"))
    slip_exit = _num(ev.get("exit_slippage_bps"))
    exp_edge = _num(ev.get("expected_edge_bps"))
    exp_fee = _num(ev.get("expected_fee_bps"))
    exp_net_edge = _num(ev.get("expected_net_edge_bps"))

    thesis_correct = net > 0
    execution_correct = _num(scores.get("execution_score"), 0.0) >= 0.55
    should_block, block_reasons = _classify_should_have_blocked(ev)

    lesson_category = "win" if net > 0 else ("fee_drag" if gross > 0 and net < 0 else "loss")
    recommended_adjustment = ""
    if should_block:
        recommended_adjustment = "Raise fee-aware net edge floor / increase minimum net profit USD floor; add cooldown for symbol/exit_reason cluster."
    elif net < 0 and "timeout" in exit_reason.lower():
        recommended_adjustment = "Reduce timeout exits (tighten entry selectivity or use stateful dead-trade timeout)."
    elif net < 0 and fees > abs(net):
        recommended_adjustment = "Reduce low-notional churn (block trades where expected net @ target < min_net_profit_usd)."
    elif net > 0 and fees > 0 and net / max(fees, 1e-9) < 1.0:
        recommended_adjustment = "Prefer maker entries / reduce spread+slippage regimes; keep size modest."

    return {
        "truth_version": "trade_learning_object_v1",
        "generated_at_utc": _iso_now(),
        "trade_id": trade_id,
        "gate": gate,
        "symbol": symbol,
        "avenue_id": _s(ev.get("avenue_id")),
        "avenue_name": _s(ev.get("avenue_name")),
        "strategy_id": _s(ev.get("strategy_id")),
        "entry_reason": entry_reason,
        "exit_reason": exit_reason,
        "hold_duration_sec": float(hold),
        "gross_pnl_usd": float(gross),
        "fees_usd": float(fees),
        "net_pnl_usd": float(net),
        "slippage_estimate_bps": float(abs(slip_entry) + abs(slip_exit)),
        "slippage_entry_bps": float(slip_entry),
        "slippage_exit_bps": float(slip_exit),
        "expected_edge_bps": float(exp_edge),
        "expected_fee_bps": float(exp_fee),
        "expected_net_edge_bps": float(exp_net_edge),
        "thesis_correct": bool(thesis_correct),
        "execution_correct": bool(execution_correct),
        "should_have_been_blocked": bool(should_block),
        "should_have_been_blocked_reasons": block_reasons,
        "lesson_category": lesson_category,
        "recommended_adjustment": recommended_adjustment,
        "scores": {
            "trade_quality_score": scores.get("trade_quality_score"),
            "execution_score": scores.get("execution_score"),
            "edge_score": scores.get("edge_score"),
            "discipline_score": scores.get("discipline_score"),
        },
        "post_trade_intelligence": dict(post_trade_intelligence or {}),
        "honesty": "Learning object derived from databank merged event; net_pnl_usd is authoritative vs gross.",
    }


def _rank_loss_patterns(objs: List[Mapping[str, Any]]) -> Dict[str, Any]:
    """
    Very small, evidence-first heuristics.
    Produces a ranked list of repeating net-loss causes (by symbol and exit_reason).
    """
    by_symbol: Dict[str, Dict[str, Any]] = {}
    by_exit: Dict[str, Dict[str, Any]] = {}
    fee_dom = 0
    flips = 0
    for o in objs:
        sym = _s(o.get("symbol"))
        ex = _s(o.get("exit_reason"))
        net = _num(o.get("net_pnl_usd"))
        fees = _num(o.get("fees_usd"))
        gross = _num(o.get("gross_pnl_usd"))
        if sym:
            b = by_symbol.setdefault(sym, {"trades": 0, "net_sum": 0.0, "losses": 0, "wins": 0})
            b["trades"] += 1
            b["net_sum"] += net
            b["wins"] += 1 if net > 0 else 0
            b["losses"] += 1 if net <= 0 else 0
        if ex:
            b2 = by_exit.setdefault(ex, {"trades": 0, "net_sum": 0.0, "losses": 0})
            b2["trades"] += 1
            b2["net_sum"] += net
            b2["losses"] += 1 if net <= 0 else 0
        if fees > 0 and net < 0 and abs(net) < fees * 0.5:
            fee_dom += 1
        if gross > 0 and net < 0:
            flips += 1

    ranked_symbols = sorted(
        [{"symbol": k, **v, "avg_net": _safe_div(v["net_sum"], max(1, v["trades"]))} for k, v in by_symbol.items()],
        key=lambda r: (r["net_sum"], -r["trades"]),
    )
    ranked_exits = sorted(
        [{"exit_reason": k, **v, "avg_net": _safe_div(v["net_sum"], max(1, v["trades"]))} for k, v in by_exit.items()],
        key=lambda r: (r["net_sum"], -r["trades"]),
    )
    return {
        "as_of_utc": _iso_now(),
        "window_trades": len(objs),
        "fee_dominant_losses_count": fee_dom,
        "fee_drag_flip_count": flips,
        "worst_symbols": ranked_symbols[:10],
        "worst_exit_reasons": ranked_exits[:10],
        "honesty": "Patterns are descriptive and derived from the learning objects only.",
    }


def _bounded_posture_from_recent(objs: List[Mapping[str, Any]]) -> Dict[str, Any]:
    """
    Bounded adaptation:
    - only scales size multiplier within [0.25, 1.0]
    - only applies symbol cooldowns when repeated net losses occur
    """
    tail = list(objs)[-12:]
    losses = [o for o in tail if _num(o.get("net_pnl_usd")) < 0]
    consec_losses = 0
    for o in reversed(tail):
        if _num(o.get("net_pnl_usd")) < 0:
            consec_losses += 1
        else:
            break

    size_mult = 1.0
    reasons: List[str] = []
    if consec_losses >= 3:
        size_mult = 0.5
        reasons.append("consecutive_net_losses>=3")
    if consec_losses >= 5:
        size_mult = 0.25
        reasons.append("consecutive_net_losses>=5")

    # Symbol cooldown if >=2 losses for same symbol in last 6 trades.
    sym_losses: Dict[str, int] = {}
    for o in tail[-6:]:
        if _num(o.get("net_pnl_usd")) < 0:
            sym = _s(o.get("symbol"))
            if sym:
                sym_losses[sym] = sym_losses.get(sym, 0) + 1
    cooldown_symbols = sorted([k for k, v in sym_losses.items() if v >= 2])
    if cooldown_symbols:
        reasons.append("same_symbol_losses>=2_in_last_6")

    return {
        "truth_version": "bounded_risk_posture_v1",
        "generated_at_utc": _iso_now(),
        "window_trades": len(tail),
        "consecutive_net_losses": consec_losses,
        "size_multiplier": float(max(0.25, min(1.0, size_mult))),
        "cooldown_symbols": cooldown_symbols,
        "reasons": reasons or ["no_adaptation"],
        "bounds": {"size_multiplier_min": 0.25, "size_multiplier_max": 1.0, "cooldown_scope": "symbol_only"},
        "honesty": "Posture is advisory and bounded; it does not change guards/permissions.",
    }


def _load_recent_learning_objects(path: Path, *, limit: int = 200) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()][-limit:]
    except OSError:
        return []
    out: List[Dict[str, Any]] = []
    for ln in lines:
        try:
            o = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if isinstance(o, dict):
            out.append(o)
    return out


def on_closed_trade(
    *,
    merged_trade_event: Mapping[str, Any],
    scores: Mapping[str, Any],
    post_trade_intelligence: Optional[Mapping[str, Any]] = None,
    runtime_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Called after databank has written the merged record.
    Writes:
    - per-trade learning JSONL (dedup)
    - rolling loss pattern snapshot
    - ranked improvement opportunities (heuristic)
    - bounded risk posture snapshot
    - research queue line (dedup by trade_id)
    """
    trade_id = _s(merged_trade_event.get("trade_id"))
    if not trade_id:
        return {"ok": False, "error": "missing_trade_id"}
    paths = _learning_paths(runtime_root)

    learning_obj = build_trade_learning_object(
        merged_trade_event=merged_trade_event,
        scores=scores,
        post_trade_intelligence=post_trade_intelligence,
    )
    appended = _append_jsonl_dedup(paths["trade_learning_jsonl"], learning_obj, trade_id=trade_id)

    recent = _load_recent_learning_objects(paths["trade_learning_jsonl"], limit=240)
    patterns = _rank_loss_patterns(recent[-80:])
    posture = _bounded_posture_from_recent(recent)

    paths["loss_patterns_json"].parent.mkdir(parents=True, exist_ok=True)
    paths["loss_patterns_json"].write_text(json.dumps(patterns, indent=2, default=str) + "\n", encoding="utf-8")
    paths["bounded_posture_json"].parent.mkdir(parents=True, exist_ok=True)
    paths["bounded_posture_json"].write_text(json.dumps(posture, indent=2, default=str) + "\n", encoding="utf-8")

    # Research queue: questions derived from observed bleed causes (no LLM required).
    q: List[str] = []
    if patterns.get("fee_drag_flip_count", 0) >= 1:
        q.append("Are we entering trades where target_move_bps barely clears spread+fees+slippage, causing fee-drag flips?")
    if any("timeout" in str(x.get("exit_reason", "")).lower() for x in (patterns.get("worst_exit_reasons") or [])[:5]):
        q.append("Are timeout exits harming PnL more than stop/target logic (should we tighten entry or change timeout state)?")
    worst_syms = [str(x.get("symbol")) for x in (patterns.get("worst_symbols") or [])[:3] if x.get("symbol")]
    if worst_syms:
        q.append(f"Should we temporarily deprioritize/cooldown symbols: {', '.join(worst_syms)} based on net losses?")

    opp = {
        "generated_at_utc": _iso_now(),
        "top_recurring_loss_patterns": patterns,
        "do_less": [
            "Low-notional trades that cannot clear min_net_profit_usd after estimated costs.",
            "Symbols on cooldown from repeated net losses.",
        ],
        "do_more": [
            "Maker-intent entries when spreads are tight and liquidity is healthy.",
            "Trades with target_move_bps well above required_move_bps (net edge buffer).",
        ],
        "top_research_questions": q[:8],
        "honesty": "Opportunities are heuristic rankings from outcomes; not a promise of profitability.",
    }
    paths["ranked_opportunities_json"].write_text(json.dumps(opp, indent=2, default=str) + "\n", encoding="utf-8")

    rq_row = {
        "truth_version": "outcome_research_queue_item_v1",
        "generated_at_utc": _iso_now(),
        "trade_id": trade_id,
        "symbol": _infer_symbol(merged_trade_event),
        "gate": _infer_gate(merged_trade_event),
        "net_pnl_usd": _num(merged_trade_event.get("net_pnl")),
        "fees_usd": _num(merged_trade_event.get("fees_paid")),
        "exit_reason": _s(merged_trade_event.get("exit_reason")),
        "research_questions": q[:6],
        "honesty": "Queue item generated from real trade outcome; questions are prompts for research automation.",
    }
    rq_app = _append_jsonl_dedup(paths["research_queue_jsonl"], rq_row, trade_id=trade_id)
    return {
        "ok": True,
        "trade_id": trade_id,
        "learning_object_appended": bool(appended),
        "research_queue_appended": bool(rq_app),
        "loss_patterns_path": str(paths["loss_patterns_json"]),
        "ranked_opportunities_path": str(paths["ranked_opportunities_json"]),
        "bounded_posture_path": str(paths["bounded_posture_json"]),
    }

