"""Edge stability — composite score + confidence (advisory; thin-sample honest)."""

from __future__ import annotations

import statistics
from typing import Any, Dict, List, Optional

from trading_ai.intelligence.execution_intelligence.metrics_common import (
    collect_strategy_scores,
    max_drawdown_cumulative_pnls,
)


def _hit_rate_stability(win_flags: List[bool], window: int = 5) -> Optional[float]:
    if len(win_flags) < window * 2:
        return None
    chunks: List[float] = []
    for i in range(0, len(win_flags) - window + 1, window):
        sub = win_flags[i : i + window]
        chunks.append(sum(1 for x in sub if x) / float(len(sub)))
    if len(chunks) < 2:
        return None
    try:
        return 1.0 - min(1.0, statistics.pstdev(chunks) * 2.0)
    except statistics.StatisticsError:
        return None


def compute_edge_stability_bundle(
    *,
    raw_trades: List[Dict[str, Any]],
    strategy_scores_doc: Dict[str, Any],
    ordered_pnls_chronological: List[float],
) -> Dict[str, Any]:
    """
    Returns score 0..1, confidence 0..1, components, honesty note.

    Does not claim statistical significance on thin samples.
    """
    n = len(raw_trades)
    sc_vals = collect_strategy_scores(strategy_scores_doc)
    strat_disp = None
    if len(sc_vals) >= 2:
        try:
            sd = statistics.pstdev(sc_vals)
            strat_disp = max(0.0, min(1.0, 1.0 - min(1.0, sd * 2.0)))
        except statistics.StatisticsError:
            strat_disp = None
    elif len(sc_vals) == 1:
        strat_disp = 0.5

    pnls = [float(p) for p in ordered_pnls_chronological]
    recent_dd = max_drawdown_cumulative_pnls(pnls[-30:]) if len(pnls) >= 5 else max_drawdown_cumulative_pnls(pnls)
    dd_component = max(0.0, min(1.0, 1.0 - min(1.0, recent_dd / max(500.0, 1e-6))))

    wins = [p > 0 for p in pnls] if pnls else []
    hit_stab = _hit_rate_stability(wins) if wins else None

    pnl_consistency = None
    if len(pnls) >= 8:
        try:
            sd_p = statistics.pstdev(pnls[-20:])
            mean_a = abs(statistics.mean(pnls[-20:])) if pnls else 1.0
            cv = sd_p / max(mean_a, 1e-6) if mean_a > 1e-9 else 1.0
            pnl_consistency = max(0.0, min(1.0, 1.0 - min(1.0, cv / 5.0)))
        except statistics.StatisticsError:
            pnl_consistency = None

    parts: Dict[str, Optional[float]] = {
        "strategy_score_dispersion": strat_disp,
        "recent_drawdown_behavior": dd_component if pnls else None,
        "pnl_consistency_recent": pnl_consistency,
        "hit_rate_stability": hit_stab,
    }
    vals = [v for v in parts.values() if v is not None]
    score = sum(vals) / len(vals) if vals else None

    # Confidence scales with sample size and component availability
    comp_n = len(vals)
    conf = None
    honesty = "insufficient_sample"
    if n < 5:
        honesty = "thin_sample: edge stability is indicative only below 5 closed trades"
        conf = 0.15 if score is not None else None
    elif n < 20:
        honesty = "moderate_sample: stability metrics gain reliability toward 20+ closes"
        conf = 0.35 + 0.02 * min(n, 15)
    else:
        honesty = "adequate_sample_for_heuristic_stability"
        conf = 0.55 + 0.02 * min(comp_n, 4)
        conf = max(0.0, min(0.92, conf))

    if score is None:
        return {
            "edge_stability_score": None,
            "edge_stability_confidence": conf,
            "edge_stability_components": parts,
            "honesty_note": honesty,
        }

    return {
        "edge_stability_score": round(float(score), 4),
        "edge_stability_confidence": round(float(conf), 4) if conf is not None else None,
        "edge_stability_components": parts,
        "honesty_note": honesty,
    }
