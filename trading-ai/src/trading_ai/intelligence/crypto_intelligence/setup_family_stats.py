"""Setup-family rolling stats (deterministic, post-fee, evidence-only)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from trading_ai.intelligence.crypto_intelligence.paths import setup_family_stats_json_path
from trading_ai.runtime_paths import ezras_runtime_root


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _f(x: Any, d: float = 0.0) -> float:
    try:
        return float(x) if x is not None else d
    except (TypeError, ValueError):
        return d


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)


def _derive_setup_family_from_learning_obj(obj: Mapping[str, Any]) -> str:
    # Best-effort: prefer explicit field if present; else derive from gate+symbol+entry_reason.
    sf = str(obj.get("setup_family") or "").strip()
    if sf:
        return sf
    gate = str(obj.get("gate") or obj.get("gate_id") or "unknown").strip().lower()
    sym = str(obj.get("symbol") or obj.get("product_id") or "unknown").strip().upper()
    base = "btc" if sym.startswith("BTC") else ("eth" if sym.startswith("ETH") else "alt")
    reason = str(obj.get("entry_reason") or "").lower()
    if "breakout" in reason or "momentum" in reason:
        appearance = "breakout_continuation"
    elif "pullback" in reason:
        appearance = "pullback_continuation"
    else:
        appearance = "unclear_mixed"
    return f"{gate}::{base}::{appearance}"


def update_setup_family_stats_from_trade_learning_object(
    learning_obj: Mapping[str, Any],
    *,
    runtime_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Updates rolling stats using the already-honest trade learning object (net-after-fees).
    """
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    p = setup_family_stats_json_path(root)
    doc = _read_json(p)
    fam = _derive_setup_family_from_learning_obj(learning_obj)
    fams = doc.get("families") if isinstance(doc.get("families"), dict) else {}
    fams = dict(fams) if isinstance(fams, dict) else {}
    row = fams.get(fam) if isinstance(fams.get(fam), dict) else {}
    row = dict(row) if isinstance(row, dict) else {}

    n = int(row.get("sample_count") or 0) + 1
    net = _f(learning_obj.get("net_pnl_usd"))
    fees = _f(learning_obj.get("fees_usd"))
    slip = _f(learning_obj.get("slippage_estimate_bps"))
    hold = _f(learning_obj.get("hold_duration_sec"))
    wins = int(row.get("wins") or 0) + (1 if net > 0 else 0)
    losses = int(row.get("losses") or 0) + (1 if net <= 0 else 0)
    net_sum = _f(row.get("net_sum_usd")) + net
    fees_sum = _f(row.get("fees_sum_usd")) + fees
    slip_sum = _f(row.get("slippage_sum_bps")) + slip
    hold_sum = _f(row.get("hold_sum_sec")) + hold

    row.update(
        {
            "setup_family": fam,
            "sample_count": n,
            "wins": wins,
            "losses": losses,
            "win_rate": (wins / n) if n > 0 else 0.0,
            "net_sum_usd": net_sum,
            "avg_net_usd": (net_sum / n) if n > 0 else 0.0,
            "fees_sum_usd": fees_sum,
            "avg_fees_usd": (fees_sum / n) if n > 0 else 0.0,
            "slippage_sum_bps": slip_sum,
            "avg_slippage_bps": (slip_sum / n) if n > 0 else 0.0,
            "hold_sum_sec": hold_sum,
            "avg_hold_sec": (hold_sum / n) if n > 0 else 0.0,
            "last_trade_id": str(learning_obj.get("trade_id") or ""),
            "updated_at_utc": _iso(),
            "honesty": "Stats derived from trade_learning_object net-after-fees; setup_family may be best-effort derived if not recorded at entry.",
        }
    )
    fams[fam] = row
    out = {
        "truth_version": "crypto_setup_family_stats_v1",
        "generated_at_utc": _iso(),
        "families": fams,
    }
    _write_json(p, out)
    return {"ok": True, "setup_family": fam, "sample_count": n, "path": str(p)}

