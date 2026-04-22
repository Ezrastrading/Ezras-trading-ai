"""Daily crypto learning distillation (deterministic, evidence-only).

Reads:
- data/learning/trade_learning_objects.jsonl (authoritative net-after-fees objects)
- data/learning/crypto_intelligence/candidate_events.jsonl
- setup_family_stats.json
Writes:
- data/learning/crypto_intelligence/daily_distillation.json
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from trading_ai.intelligence.crypto_intelligence.paths import (
    candidate_events_jsonl_path,
    daily_distillation_json_path,
    setup_family_stats_json_path,
)
from trading_ai.runtime_paths import ezras_runtime_root


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _read_jsonl_tail(path: Path, *, limit: int) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    out: List[Dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            lines = f.readlines()[-max(0, int(limit)) :]
        for ln in lines:
            ln = ln.strip()
            if not ln:
                continue
            try:
                rec = json.loads(ln)
                if isinstance(rec, dict):
                    out.append(rec)
            except json.JSONDecodeError:
                continue
    except Exception:
        return []
    return out


def write_daily_crypto_learning_distillation(
    *,
    runtime_root: Optional[Path] = None,
    as_of: Optional[date] = None,
    lookback_trade_learning: int = 240,
    lookback_candidate_events: int = 1000,
) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    as_of = as_of or _utc_today()

    trade_learning_path = root / "data" / "learning" / "trade_learning_objects.jsonl"
    trades = _read_jsonl_tail(trade_learning_path, limit=lookback_trade_learning)
    candidates = _read_jsonl_tail(candidate_events_jsonl_path(root), limit=lookback_candidate_events)
    stats = _read_json(setup_family_stats_json_path(root))

    by_setup = defaultdict(lambda: {"n": 0, "net_sum": 0.0, "wins": 0, "losses": 0})
    by_symbol = defaultdict(lambda: {"n": 0, "net_sum": 0.0})
    by_exit = Counter()
    for t in trades:
        fam = str(t.get("setup_family") or "").strip() or "unknown"
        sym = str(t.get("symbol") or "").strip().upper() or "unknown"
        net = float(t.get("net_pnl_usd") or 0.0)
        ex = str(t.get("exit_reason") or "").strip().lower()
        by_setup[fam]["n"] += 1
        by_setup[fam]["net_sum"] += net
        by_setup[fam]["wins"] += 1 if net > 0 else 0
        by_setup[fam]["losses"] += 1 if net <= 0 else 0
        by_symbol[sym]["n"] += 1
        by_symbol[sym]["net_sum"] += net
        if ex:
            by_exit[ex] += 1

    # Candidate diagnostics: rejection reasons frequency (stage-level).
    rej_stage = Counter()
    appearance = Counter()
    for c in candidates:
        if c.get("truth_version") not in ("crypto_candidate_event_v1", "crypto_micro_candidate_decision_v1"):
            continue
        if c.get("passed") is False:
            rej_stage[str(c.get("stage") or "unknown")] += 1
        appearance[str(c.get("setup_appearance") or "unknown")] += 1

    ranked_setups = sorted(
        [
            {
                "setup_family": k,
                "sample": v["n"],
                "net_sum_usd": round(float(v["net_sum"]), 6),
                "avg_net_usd": round(float(v["net_sum"]) / max(1, int(v["n"])), 6),
                "win_rate": round(float(v["wins"]) / max(1, int(v["n"])), 4),
            }
            for k, v in by_setup.items()
        ],
        key=lambda r: (r["avg_net_usd"], r["sample"]),
        reverse=True,
    )
    ranked_symbols = sorted(
        [
            {
                "symbol": k,
                "sample": v["n"],
                "net_sum_usd": round(float(v["net_sum"]), 6),
                "avg_net_usd": round(float(v["net_sum"]) / max(1, int(v["n"])), 6),
            }
            for k, v in by_symbol.items()
        ],
        key=lambda r: (r["avg_net_usd"], r["sample"]),
        reverse=True,
    )

    out = {
        "truth_version": "crypto_learning_distillation_v1",
        "generated_at_utc": _iso(),
        "as_of_date_utc": as_of.isoformat(),
        "evidence_windows": {
            "trade_learning_tail_n": len(trades),
            "candidate_events_tail_n": len(candidates),
        },
        "what_worked_best": ranked_setups[:5],
        "what_failed_most": list(reversed(ranked_setups[-5:])),
        "best_symbols": ranked_symbols[:5],
        "worst_symbols": list(reversed(ranked_symbols[-5:])),
        "top_exit_reason_clusters": [{"exit_reason": k, "count": v} for k, v in by_exit.most_common(8)],
        "candidate_rejection_stage_histogram": dict(rej_stage),
        "setup_appearance_histogram": dict(appearance),
        "setup_family_stats_ref": "data/learning/crypto_intelligence/setup_family_stats.json",
        "setup_family_stats_snapshot": {
            "family_count": len((stats.get("families") or {}) if isinstance(stats.get("families"), dict) else {}),
        },
        "honesty": "This distillation is deterministic: it summarizes recorded learning objects and candidate events only. It does not infer candle meaning without captured features.",
    }
    p = daily_distillation_json_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return {"ok": True, "path": str(p), "as_of": as_of.isoformat()}

