from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _write_json_atomic(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)


def first20_review_path(runtime_root: Path) -> Path:
    root = Path(runtime_root).resolve()
    return root / "data" / "control" / "live_micro_first20_review.json"


def update_first20_review(
    *,
    runtime_root: Path,
    closed_trade_summary: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Append one closed trade summary into the first-20 cohort artifact.
    Honest nulls are allowed.
    """
    root = Path(runtime_root).resolve()
    p = first20_review_path(root)
    doc = _read_json(p)
    items = doc.get("items")
    if not isinstance(items, list):
        items = []
    # Dedup by trade_id if present
    tid = str(closed_trade_summary.get("trade_id") or closed_trade_summary.get("position_id") or "").strip()
    if tid:
        for it in items:
            if isinstance(it, dict) and str(it.get("trade_id") or it.get("position_id") or "").strip() == tid:
                # already recorded
                break
        else:
            items.append(dict(closed_trade_summary))
    else:
        items.append(dict(closed_trade_summary))

    # Keep only first 20 closes (in order)
    items = items[:20]

    pnls = []
    holds = []
    wins = 0
    losses = 0
    by_product: Dict[str, int] = {}
    exit_reasons: Dict[str, int] = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        pid = str(it.get("product_id") or "").strip().upper() or "unknown"
        by_product[pid] = int(by_product.get(pid, 0)) + 1
        er = str(it.get("exit_reason") or "").strip() or "unknown"
        exit_reasons[er] = int(exit_reasons.get(er, 0)) + 1
        pnl = it.get("realized_pnl_usd")
        if pnl is not None:
            try:
                pv = float(pnl)
                pnls.append(pv)
                if pv > 0:
                    wins += 1
                elif pv < 0:
                    losses += 1
            except Exception:
                pass
        hs = it.get("hold_seconds")
        if hs is not None:
            try:
                holds.append(float(hs))
            except Exception:
                pass

    summary = {
        "truth_version": "live_micro_first20_review_v1",
        "generated_at_unix": time.time(),
        "completed_trades_count": len(items),
        "wins": wins,
        "losses": losses,
        "avg_pnl_usd": (sum(pnls) / len(pnls)) if pnls else None,
        "avg_hold_seconds": (sum(holds) / len(holds)) if holds else None,
        "top_products": sorted(by_product.items(), key=lambda x: x[1], reverse=True)[:5],
        "top_exit_reasons": sorted(exit_reasons.items(), key=lambda x: x[1], reverse=True)[:5],
        "honesty": "Metrics are computed only from recorded closes; missing fields remain null.",
        "items": items,
        "cohort_complete": len(items) >= 20,
    }
    _write_json_atomic(p, summary)
    return summary

