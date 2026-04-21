"""
Promotion / demotion engine for edge scoring outputs.

This engine does NOT fabricate edge quality. It only maps measured EdgeScore truth
into lifecycle statuses with anti-overfit controls (sample size, persistence, hysteresis).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from trading_ai.runtime_paths import ezras_runtime_root
from trading_ai.storage.storage_adapter import LocalStorageAdapter


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _s(x: Any) -> str:
    return str(x or "").strip()


def _num(x: Any, d: float = 0.0) -> float:
    try:
        return float(x) if x is not None else d
    except (TypeError, ValueError):
        return d


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(x)))


STATUSES = ("candidate", "experimental", "production", "reduced", "paused", "archived")


@dataclass(frozen=True)
class PromotionConfig:
    min_trades_candidate: int = 20
    min_trades_production: int = 45
    min_trades_reduce: int = 20
    min_trades_pause: int = 12
    # Edge health thresholds
    promote_to_candidate_health: float = 62.0
    promote_to_production_health: float = 72.0
    reduce_health: float = 48.0
    pause_health: float = 35.0
    # Recent degradation
    demote_recent10_net_pnl: float = -0.25
    pause_recent5_net_pnl: float = -0.35
    # Execution problems
    max_timeout_ratio_reduce: float = 0.25
    max_timeout_ratio_pause: float = 0.40
    fee_dominance_reduce: float = 0.35
    fee_dominance_pause: float = 0.55
    # Hysteresis: require sustained signal before major status changes
    promote_persistence_cycles: int = 2
    demote_persistence_cycles: int = 2


def _load_prev(runtime_root: Path) -> Dict[str, Any]:
    ad = LocalStorageAdapter(runtime_root=Path(runtime_root).resolve())
    prev = ad.read_json("data/control/edge_promotion_truth.json")
    return prev if isinstance(prev, dict) else {}


def _status_for_score(
    score: Mapping[str, Any],
    *,
    prev_status: str,
    cfg: PromotionConfig,
) -> Tuple[str, str, Dict[str, Any]]:
    """
    Map a single EdgeScore record to (new_status, decision_reason, signals_meta).
    Uses only observed score fields.
    """
    n = int(score.get("total_trades") or 0)
    health = _num(score.get("edge_health_score"))
    recent5 = _num(score.get("recent_5_trade_net_pnl"))
    recent10 = _num(score.get("recent_10_trade_net_pnl"))
    timeout_ratio = _num(score.get("timeout_ratio"))
    fee_dom = _num(score.get("fee_dominance_ratio"))
    max_dd = _num(score.get("max_drawdown"))
    expectancy = _num(score.get("expectancy"))

    signals = {
        "n": n,
        "health": health,
        "recent5": recent5,
        "recent10": recent10,
        "timeout_ratio": timeout_ratio,
        "fee_dominance_ratio": fee_dom,
        "max_drawdown": max_dd,
        "expectancy_net_per_trade": expectancy,
    }

    # Hard safety pauses (still evidence-first): require some sample to avoid 1-trade overreaction.
    if n >= cfg.min_trades_pause:
        if health <= cfg.pause_health and recent5 <= cfg.pause_recent5_net_pnl:
            return "paused", "pause_health_and_recent5", signals
        if timeout_ratio >= cfg.max_timeout_ratio_pause:
            return "paused", "pause_timeout_ratio", signals
        if fee_dom >= cfg.fee_dominance_pause and expectancy < 0:
            return "paused", "pause_fee_dominant_negative_expectancy", signals

    # Demotions (reduced) only after enough evidence.
    if n >= cfg.min_trades_reduce:
        if health <= cfg.reduce_health and recent10 <= cfg.demote_recent10_net_pnl:
            return "reduced", "reduce_health_and_recent10", signals
        if timeout_ratio >= cfg.max_timeout_ratio_reduce:
            return "reduced", "reduce_timeout_ratio", signals
        if fee_dom >= cfg.fee_dominance_reduce and expectancy < 0:
            return "reduced", "reduce_fee_dominant_negative_expectancy", signals

    # Promotions: require sample + positive expectancy.
    if expectancy <= 0:
        return prev_status or "experimental", "no_promotion_expectancy_non_positive", signals

    if n >= cfg.min_trades_production and health >= cfg.promote_to_production_health:
        return "production", "promote_production", signals

    if n >= cfg.min_trades_candidate and health >= cfg.promote_to_candidate_health:
        # candidate is an intermediate state; production requires stronger gates.
        if prev_status in ("experimental", "", "candidate"):
            return "candidate", "promote_candidate", signals
        return prev_status, "candidate_signal_but_prev_status_not_lower", signals

    return prev_status or "experimental", "hold", signals


def _apply_hysteresis(
    *,
    prev_row: Mapping[str, Any],
    proposed_status: str,
    reason: str,
    cfg: PromotionConfig,
    kind: str,
) -> Tuple[str, Dict[str, Any]]:
    """
    Require persistence before moving between key rungs.
    Tracks `promote_signal_streak` and `demote_signal_streak`.
    """
    prev_status = _s(prev_row.get("status") or "experimental")
    streak_p = int(prev_row.get("promote_signal_streak") or 0)
    streak_d = int(prev_row.get("demote_signal_streak") or 0)

    meta: Dict[str, Any] = {"prev_status": prev_status, "proposed_status": proposed_status, "reason": reason}

    if proposed_status == prev_status:
        return prev_status, {**meta, "promote_signal_streak": 0, "demote_signal_streak": 0, "applied": "no_change"}

    # classify direction
    promote = proposed_status in ("candidate", "production") and prev_status in ("experimental", "candidate", "")
    demote = proposed_status in ("reduced", "paused") and prev_status in ("production", "candidate", "experimental", "reduced")

    if promote:
        streak_p += 1
        if streak_p < cfg.promote_persistence_cycles:
            return prev_status, {**meta, "promote_signal_streak": streak_p, "demote_signal_streak": 0, "applied": "deferred_promotion"}
        return proposed_status, {**meta, "promote_signal_streak": 0, "demote_signal_streak": 0, "applied": "promotion"}

    if demote:
        streak_d += 1
        if streak_d < cfg.demote_persistence_cycles:
            return prev_status, {**meta, "promote_signal_streak": 0, "demote_signal_streak": streak_d, "applied": "deferred_demotion"}
        return proposed_status, {**meta, "promote_signal_streak": 0, "demote_signal_streak": 0, "applied": "demotion"}

    # Any other transition: apply immediately but reset streaks.
    return proposed_status, {**meta, "promote_signal_streak": 0, "demote_signal_streak": 0, "applied": "direct"}


def evaluate_promotions(
    *,
    runtime_root: Optional[Path] = None,
    scores_truth: Mapping[str, Any],
    cfg: Optional[PromotionConfig] = None,
) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    cfg = cfg or PromotionConfig()
    prev = _load_prev(root)
    prev_by = prev.get("by_key_id") if isinstance(prev, dict) else None
    if not isinstance(prev_by, dict):
        prev_by = {}

    by_key_id = {}
    changes = []

    by_score = scores_truth.get("by_key_id") if isinstance(scores_truth, dict) else None
    if not isinstance(by_score, dict):
        return {"ok": False, "error": "scores_truth_missing_by_key_id"}

    for kid, score in by_score.items():
        if not isinstance(score, dict):
            continue
        prev_row = prev_by.get(kid) if isinstance(prev_by.get(kid), dict) else {}
        prev_status = _s(prev_row.get("status") or "experimental")
        proposed, why, signals = _status_for_score(score, prev_status=prev_status, cfg=cfg)
        final_status, meta = _apply_hysteresis(prev_row=prev_row, proposed_status=proposed, reason=why, cfg=cfg, kind="edge")
        row = {
            "key_id": kid,
            "status": final_status,
            "decision": meta,
            "signals": signals,
            "updated_at": _iso_now(),
        }
        # preserve streaks if deferred
        row["promote_signal_streak"] = int(meta.get("promote_signal_streak") or 0)
        row["demote_signal_streak"] = int(meta.get("demote_signal_streak") or 0)
        by_key_id[kid] = row
        if final_status != prev_status:
            changes.append({"key_id": kid, "from": prev_status, "to": final_status, "why": why, "applied": meta.get("applied")})

    out = {
        "truth_version": "edge_promotion_truth_v1",
        "generated_at": _iso_now(),
        "runtime_root": str(root),
        "changes": changes,
        "by_key_id": by_key_id,
        "config": cfg.__dict__,
        "honesty": "Promotion/demotion statuses are derived from edge_scores_truth only (realized outcomes).",
    }
    return out


def write_edge_promotion_truth(*, runtime_root: Optional[Path], payload: Dict[str, Any]) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    ad = LocalStorageAdapter(runtime_root=root)
    ad.write_json("data/control/edge_promotion_truth.json", payload)
    return {"ok": True, "path": str(root / "data" / "control" / "edge_promotion_truth.json")}


def promotion_status_maps(payload: Mapping[str, Any]) -> Tuple[Dict[str, str], Dict[str, str]]:
    """
    Convert promotion truth to (promotion_status_by_key_id, demotion_status_by_key_id)
    for consumption by scoring engine.
    """
    by = payload.get("by_key_id") if isinstance(payload, dict) else None
    if not isinstance(by, dict):
        return {}, {}
    promo: Dict[str, str] = {}
    demo: Dict[str, str] = {}
    for kid, r in by.items():
        if not isinstance(r, dict):
            continue
        st = _s(r.get("status") or "")
        promo[kid] = st
        if st in ("reduced", "paused", "archived"):
            demo[kid] = st
    return promo, demo

