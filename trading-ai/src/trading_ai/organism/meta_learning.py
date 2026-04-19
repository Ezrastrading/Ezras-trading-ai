"""Track which research sources and hypotheses correlate with profitable edges."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from trading_ai.nte.utils.atomic_json import atomic_write_json
from trading_ai.organism.paths import meta_learning_path


def _load(path: Path | None = None) -> Dict[str, Any]:
    p = path or meta_learning_path()
    if not p.is_file():
        return {"research_quality": {}, "model_contribution": {"gpt": 0.0, "claude": 0.0}, "hypothesis_hashes": {}}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _save(data: Dict[str, Any], path: Path | None = None) -> None:
    atomic_write_json(path or meta_learning_path(), data)


def score_research_sources(path: Path | None = None) -> Dict[str, float]:
    st = _load(path)
    rq = st.get("research_quality")
    if isinstance(rq, dict):
        return {str(k): float(v) for k, v in rq.items() if isinstance(v, (int, float))}
    return {}


def update_meta_from_closed_trade(
    trade: Mapping[str, Any],
    *,
    research_source: Optional[str] = None,
    path: Path | None = None,
) -> None:
    """EMA-style update for model contribution when edge-tagged trades close."""
    st = _load(path)
    net = float(trade.get("net_pnl") or 0.0)
    src = research_source or str(trade.get("research_source") or "")
    mc = st.get("model_contribution")
    if not isinstance(mc, dict):
        mc = {"gpt": 0.0, "claude": 0.0}
    key = src.lower()
    if key in ("gpt", "openai"):
        k = "gpt"
    elif key in ("claude", "anthropic"):
        k = "claude"
    else:
        return
    alpha = 0.08
    prev = float(mc.get(k) or 0.0)
    mc[k] = prev * (1 - alpha) + alpha * (1.0 if net > 0 else -1.0 if net < 0 else 0.0)
    st["model_contribution"] = mc
    _save(st, path)
