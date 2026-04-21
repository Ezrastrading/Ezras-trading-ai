from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from trading_ai.storage.storage_adapter import LocalStorageAdapter


class ValidatedMetricsError(RuntimeError):
    pass


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        raise ValidatedMetricsError(f"missing_required_file:{path}")
    rows: List[Dict[str, Any]] = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        try:
            j = json.loads(ln)
        except json.JSONDecodeError as exc:
            raise ValidatedMetricsError(f"invalid_jsonl:{path}:{exc}") from exc
        if isinstance(j, dict):
            rows.append(j)
    return rows


def _mean(xs: Iterable[float]) -> float:
    arr = list(xs)
    if not arr:
        return 0.0
    return sum(arr) / len(arr)


def _win_rate(pnls: List[float]) -> float:
    if not pnls:
        return 0.0
    return sum(1 for x in pnls if x > 0) / len(pnls)


def _expectancy(pnls: List[float]) -> float:
    return _mean(pnls)


@dataclass(frozen=True)
class _TradeJoin:
    trade_id: str
    gap_type: str
    execution_grade: str
    net_pnl: float


def build_validated_metrics(*, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    """
    Strict truth-join:
      trade_id -> master(gap_type) -> execution(execution_grade) -> review(net_pnl)

    FAIL-CLOSED: if any component missing for any trade_id present in master/execution/review snapshots.
    """
    ad = LocalStorageAdapter(runtime_root=runtime_root)
    root = ad.root()

    # Canonical truth streams: runtime snapshot tables (fail-closed writers).
    p_master = root / "data" / "snapshots" / "trades_master.jsonl"
    p_exec = root / "data" / "snapshots" / "trades_execution_snapshot.jsonl"
    p_review = root / "data" / "snapshots" / "trades_review_snapshot.jsonl"

    master = _read_jsonl(p_master)
    exe = _read_jsonl(p_exec)
    rev = _read_jsonl(p_review)

    by_master: Dict[str, Dict[str, Any]] = {}
    for r in master:
        tid = str(r.get("trade_id") or "").strip()
        if tid:
            by_master[tid] = r

    by_exec: Dict[str, Dict[str, Any]] = {}
    for r in exe:
        tid = str(r.get("trade_id") or "").strip()
        if tid:
            by_exec[tid] = r

    by_review: Dict[str, Dict[str, Any]] = {}
    for r in rev:
        tid = str(r.get("trade_id") or "").strip()
        if tid:
            by_review[tid] = r

    tids = sorted(set(by_master.keys()) | set(by_exec.keys()) | set(by_review.keys()))
    if not tids:
        raise ValidatedMetricsError("no_snapshot_trades_to_join")

    joined: List[_TradeJoin] = []
    missing: Dict[str, List[str]] = defaultdict(list)
    for tid in tids:
        m = by_master.get(tid)
        x = by_exec.get(tid)
        r = by_review.get(tid)
        if m is None:
            missing[tid].append("master_snapshot_missing")
            continue
        if x is None:
            missing[tid].append("execution_snapshot_missing")
            continue
        if r is None:
            missing[tid].append("review_snapshot_missing")
            continue

        m0 = m.get("master") if isinstance(m.get("master"), dict) else m
        x0 = x.get("execution") if isinstance(x.get("execution"), dict) else x
        r0 = r.get("review") if isinstance(r.get("review"), dict) else r

        gap_type = str(m0.get("gap_type") or m0.get("gap_family") or "").strip()
        grade = str(x0.get("execution_grade") or r.get("execution_grade") or "").strip()
        pnl = r0.get("net_pnl") or r.get("net_pnl")
        try:
            pnl_f = float(pnl)
        except (TypeError, ValueError):
            pnl_f = None  # type: ignore[assignment]

        if not gap_type:
            missing[tid].append("gap_type_missing")
        if not grade:
            missing[tid].append("execution_grade_missing")
        if pnl_f is None:
            missing[tid].append("net_pnl_missing")
        if missing.get(tid):
            continue
        joined.append(_TradeJoin(trade_id=tid, gap_type=gap_type, execution_grade=grade, net_pnl=float(pnl_f)))

    if missing:
        # Fail-closed: report generation is not allowed with missing join inputs.
        raise ValidatedMetricsError("join_missing:" + json.dumps(missing, sort_keys=True))

    pnl_by_grade: Dict[str, float] = {}
    win_rate_by_grade: Dict[str, float] = {}
    by_g: Dict[str, List[float]] = defaultdict(list)
    for j in joined:
        by_g[j.execution_grade].append(j.net_pnl)
    for g, pnls in by_g.items():
        pnl_by_grade[g] = float(sum(pnls))
        win_rate_by_grade[g] = float(_win_rate(pnls))

    pnl_by_gap_type: Dict[str, float] = {}
    expectancy_by_gap_type: Dict[str, float] = {}
    by_t: Dict[str, List[float]] = defaultdict(list)
    for j in joined:
        by_t[j.gap_type].append(j.net_pnl)
    for t, pnls in by_t.items():
        pnl_by_gap_type[t] = float(sum(pnls))
        expectancy_by_gap_type[t] = float(_expectancy(pnls))

    out = {
        "truth_version": "validated_metrics_v1",
        "joined_trade_count": len(joined),
        "pnl_by_grade": dict(sorted(pnl_by_grade.items())),
        "pnl_by_gap_type": dict(sorted(pnl_by_gap_type.items())),
        "win_rate_by_grade": dict(sorted(win_rate_by_grade.items())),
        "expectancy_by_gap_type": dict(sorted(expectancy_by_gap_type.items())),
        "honesty": "FAIL-CLOSED: this report is written only when every trade has master+execution+review snapshots with gap_type, execution_grade, and net_pnl.",
    }
    ad.write_json("data/reports/validated_metrics.json", out)
    return out

