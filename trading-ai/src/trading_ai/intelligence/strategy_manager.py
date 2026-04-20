"""Strategy scores from persisted memory — promotion state is advisory, gradual."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


def evaluate_strategy(strategy_id: str, row: Dict[str, Any], *, global_trade_rows: int) -> Dict[str, Any]:
    """Single strategy row from strategy_scores.json avenues block."""
    try:
        sc = float(row.get("score")) if row.get("score") is not None else None
    except (TypeError, ValueError):
        sc = None
    stability = row.get("stability")
    try:
        st = float(stability) if stability is not None else None
    except (TypeError, ValueError):
        st = None

    recent = row.get("recent_performance")
    if not isinstance(recent, dict):
        recent = {}

    status = "neutral"
    if sc is not None and global_trade_rows >= 15:
        if sc >= 0.72 and (st is None or st >= 0.55):
            status = "promoted"
        elif sc <= 0.35 or (st is not None and st < 0.3):
            status = "restricted"

    cap_w = "medium"
    if status == "promoted":
        cap_w = "high"
    elif status == "restricted":
        cap_w = "low"

    return {
        "strategy_id": strategy_id,
        "score": sc,
        "recent_performance": recent,
        "stability": st,
        "status": status,
        "capital_weight": cap_w,
    }


def update_strategy_state(
    prior: Optional[Dict[str, Any]],
    proposed: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Gradual transition: status does not jump promoted↔restricted in one step without passing neutral.
    ``prior`` is previous evaluation dict for same strategy_id or None.
    """
    out = dict(proposed)
    if prior is None:
        return out
    prev_s = str(prior.get("status") or "neutral")
    new_s = str(out.get("status") or "neutral")
    if prev_s == "neutral":
        return out
    if prev_s == "promoted" and new_s == "restricted":
        out["status"] = "neutral"
        out["capital_weight"] = "medium"
        out["gradual_shift_note"] = "demotion_blocked_single_step_use_neutral_first"
    elif prev_s == "restricted" and new_s == "promoted":
        out["status"] = "neutral"
        out["capital_weight"] = "medium"
        out["gradual_shift_note"] = "promotion_blocked_single_step_use_neutral_first"
    return out


def build_strategy_state_summary(
    strategy_scores_doc: Dict[str, Any],
    *,
    global_trade_rows: int,
) -> Dict[str, Any]:
    """Flatten per-avenue strategy rows into a list for packets and storage."""
    av = strategy_scores_doc.get("avenues") if isinstance(strategy_scores_doc.get("avenues"), dict) else {}
    rows: List[Dict[str, Any]] = []
    for aid, block in av.items():
        if not isinstance(block, dict):
            continue
        for sid, srow in block.items():
            if not isinstance(srow, dict):
                continue
            ev = evaluate_strategy(str(sid), srow, global_trade_rows=global_trade_rows)
            ev["avenue"] = str(aid)
            rows.append(ev)

    promoted = [r["strategy_id"] for r in rows if r.get("status") == "promoted"]
    restricted = [r["strategy_id"] for r in rows if r.get("status") == "restricted"]

    return {
        "strategies": rows,
        "promoted_ids": promoted[:20],
        "restricted_ids": restricted[:20],
        "source_updated": strategy_scores_doc.get("updated"),
    }
