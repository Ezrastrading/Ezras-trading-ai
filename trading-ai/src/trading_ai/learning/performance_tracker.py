"""Append-only friendly performance snapshot for autonomy promotion *requests* (operator decides)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _safe_float(x: Any) -> Optional[float]:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _pnl(t: Dict[str, Any]) -> Optional[float]:
    for k in ("net_pnl_usd", "net_pnl", "realized_pnl_usd"):
        v = _safe_float(t.get(k))
        if v is not None:
            return v
    return None


def refresh_ai_performance_tracker(*, runtime_root: Path) -> Dict[str, Any]:
    trades: List[Dict[str, Any]] = []
    try:
        from trading_ai.global_layer.trade_truth import load_federated_trades

        trades, _ = load_federated_trades()
    except Exception:
        trades = []

    pnls = [_pnl(t) for t in trades if isinstance(t, dict)]
    pnls_n = [p for p in pnls if p is not None]
    wins = sum(1 for p in pnls_n if p > 0)
    total = sum(pnls_n) if pnls_n else 0.0
    n = len(pnls_n)
    wr = (wins / n) if n else 0.0

    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls_n:
        equity += p
        peak = max(peak, equity)
        dd = peak - equity
        max_dd = max(max_dd, dd)

    promotion_eligible = (
        n >= 20
        and wr >= 0.45
        and total > 0
        and max_dd < max(abs(total) * 0.5, 1.0)
    )

    out: Dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "total_trades_measured": n,
        "total_pnl_usd": round(total, 6),
        "win_rate": round(wr, 4),
        "max_drawdown_proxy_usd": round(max_dd, 6),
        "consistency_note": "Rough proxy from sequential cumulative PnL — not a formal risk engine.",
        "promotion_request_allowed": promotion_eligible,
        "promotion_requires_operator_approval": True,
        "honest_classification": "advisory_metrics_not_order_gating",
    }
    p = runtime_root / "data" / "control" / "ai_performance_tracker.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    return out
