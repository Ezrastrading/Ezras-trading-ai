"""
Edge extraction from recorded trades only — builds analysis and control artifacts under
``EZRAS_RUNTIME_ROOT/data/{analysis,control,reports}``.

Sources: databank ``trade_events.jsonl``, optional ``first_20_trade_diagnostics.jsonl``,
and ``universal_execution_*`` JSON snapshots (last-trade alignment).
"""

from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from trading_ai.control.adaptive_scope import resolve_gate_id_attribution_for_trade_row
from trading_ai.nte.databank.local_trade_store import DatabankRootUnsetError, load_all_trade_events
from trading_ai.nte.databank.trade_score_engine import compute_scores_for_trade
from trading_ai.nte.utils.atomic_json import atomic_write_json
from trading_ai.runtime_paths import ezras_runtime_root


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _num(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except (TypeError, ValueError):
        return default


def _parse_ts(s: Any) -> Optional[float]:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    t = str(s).strip()
    if not t:
        return None
    try:
        return datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _hold_bucket(sec: float) -> str:
    if sec < 30:
        return "<30s"
    if sec < 120:
        return "30s–2m"
    if sec < 300:
        return "2m–5m"
    return "5m+"


def _tod_bucket(ts: Optional[float]) -> str:
    if ts is None:
        return "unknown"
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    h = dt.hour
    if 0 <= h < 6:
        return "night_utc_0_6"
    if 6 <= h < 12:
        return "morning_utc_6_12"
    if 12 <= h < 18:
        return "afternoon_utc_12_18"
    return "evening_utc_18_24"


def _infer_sides(row: Mapping[str, Any]) -> Tuple[str, str]:
    ms = row.get("market_snapshot_json")
    if isinstance(ms, str) and ms.strip():
        try:
            ms = json.loads(ms)
        except json.JSONDecodeError:
            ms = None
    if isinstance(ms, dict):
        se = ms.get("side_entry") or ms.get("entry_side")
        sx = ms.get("side_exit") or ms.get("exit_side")
        if isinstance(se, str) and isinstance(sx, str):
            return se.lower(), sx.lower()
    return "", ""


def _return_bps(row: Mapping[str, Any], diag: Mapping[str, Any]) -> Optional[float]:
    rb = diag.get("return_bps")
    if rb is not None:
        try:
            return float(rb)
        except (TypeError, ValueError):
            pass
    rb = row.get("return_bps")
    if rb is not None:
        try:
            return float(rb)
        except (TypeError, ValueError):
            pass
    ep = _num(row.get("actual_entry_price") or row.get("avg_entry_price"))
    xp = _num(row.get("actual_exit_price") or row.get("avg_exit_price"))
    if ep > 0 and xp > 0:
        return (xp / ep - 1.0) * 10000.0
    return None


def _slippage_bps_combined(row: Mapping[str, Any]) -> float:
    v = row.get("slippage_estimate")
    if v is not None:
        return abs(_num(v))
    return abs(_num(row.get("entry_slippage_bps"))) + abs(_num(row.get("exit_slippage_bps")))


def _spread_bps(row: Mapping[str, Any]) -> Optional[float]:
    s = row.get("spread_bps_entry")
    if s is None:
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _gate_id_and_attribution(row: Mapping[str, Any], diag: Mapping[str, Any]) -> Tuple[str, str]:
    return resolve_gate_id_attribution_for_trade_row(row, diag)


def _product(row: Mapping[str, Any], diag: Mapping[str, Any]) -> str:
    return str(
        diag.get("product_id")
        or row.get("product_id")
        or row.get("asset")
        or row.get("symbol")
        or "unknown"
    )


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except Exception:
        return None


def _load_jsonl_by_trade_id(path: Path) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    if not path.is_file():
        return out
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            tid = str(rec.get("trade_id") or "").strip()
            if tid:
                out[tid] = rec
    return out


def resolve_trade_events(
    runtime_root: Path,
    trade_events_path: Optional[Path],
) -> Tuple[List[Dict[str, Any]], str]:
    """Return (events, source_note)."""
    if trade_events_path is not None:
        ev = load_all_trade_events(trade_events_path)
        return ev, str(trade_events_path)
    try:
        ev = load_all_trade_events()
        return ev, "databank_env"
    except DatabankRootUnsetError:
        p = runtime_root / "databank" / "trade_events.jsonl"
        ev = load_all_trade_events(p) if p.exists() else []
        return ev, str(p) if p.exists() else "empty_no_databank"


def _execution_success_for_row(
    row: Mapping[str, Any],
    diag: Mapping[str, Any],
    last_proof_tid: Optional[str],
    last_proven: Optional[bool],
) -> Optional[bool]:
    tid = str(row.get("trade_id") or "")
    if last_proof_tid and tid == last_proof_tid and last_proven is not None:
        return bool(last_proven)
    if diag:
        ef = diag.get("exit_fill_confirmed")
        ee = diag.get("entry_fill_confirmed")
        if ef is False or ee is False:
            return False
        if diag.get("truth_level") == "FULL" and ef is True and ee is True:
            return True
    if row.get("stale_cancelled"):
        return False
    if int(_num(row.get("partial_fill_count"))) > 0:
        return False
    if str(row.get("health_state") or "").lower() not in ("", "ok"):
        return False
    return None


def _is_execution_loss(row: Mapping[str, Any], diag: Mapping[str, Any]) -> bool:
    slip = _slippage_bps_combined(row)
    lat = _num(row.get("latency_ms"))
    fees = abs(_num(row.get("fees_paid")))
    gross = abs(_num(row.get("gross_pnl")))
    if slip >= 80.0:
        return True
    if lat >= 3000.0:
        return True
    if diag.get("exit_fill_confirmed") is False:
        return True
    if bool(row.get("stale_cancelled")):
        return True
    if int(_num(row.get("partial_fill_count"))) > 0:
        return True
    if gross > 1e-9 and fees / gross > 0.45:
        return True
    return False


def _max_consecutive_losses(pnls: Sequence[float]) -> int:
    best = cur = 0
    for p in pnls:
        if p < 0:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def _corr(xs: List[float], ys: List[float]) -> Optional[float]:
    n = len(xs)
    if n < 3 or n != len(ys):
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    denx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    deny = math.sqrt(sum((y - my) ** 2 for y in ys))
    if denx < 1e-18 or deny < 1e-18:
        return None
    return num / (denx * deny)


def _group_key(
    row: Dict[str, Any],
    *,
    strategy_id: str,
    product_id: str,
    gate_id: str,
) -> Tuple[str, str, str, str, str]:
    ts = _parse_ts(row.get("timestamp_close") or row.get("timestamp") or row.get("created_at"))
    hs = _num(row.get("hold_seconds"))
    return (
        strategy_id,
        product_id,
        gate_id,
        _hold_bucket(hs),
        _tod_bucket(ts),
    )


def _aggregate_groups(rows: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Key string -> stats."""
    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        sk = r.get("strategy_id") or "unknown"
        pk = r.get("product_id") or "unknown"
        gk = r.get("gate_id") or "unknown"
        key = "::".join(_group_key(r, strategy_id=sk, product_id=pk, gate_id=gk))
        buckets[key].append(r)
    out: Dict[str, Dict[str, Any]] = {}
    for key, grp in buckets.items():
        pnls = [_num(x.get("net_pnl")) for x in grp]
        wins = sum(1 for p in pnls if p > 0)
        n = len(pnls)
        total = sum(pnls)
        exp = total / n if n else 0.0
        slips = [_slippage_bps_combined(x) for x in grp]
        fees = [abs(_num(x.get("fees_paid"))) for x in grp]
        gross_swings = sum(abs(_num(x.get("gross_pnl"))) for x in grp)
        fee_sum = sum(fees)
        parts = key.split("::")
        out[key] = {
            "strategy_id": parts[0],
            "product_id": parts[1],
            "gate_id": parts[2],
            "hold_time_bucket": parts[3],
            "time_of_day_bucket": parts[4],
            "trades": n,
            "win_rate": wins / n if n else 0.0,
            "net_pnl": round(total, 6),
            "avg_pnl": round(exp, 6),
            "expectancy": round(exp, 6),
            "avg_slippage_bps_combined": round(sum(slips) / n, 4) if n else 0.0,
            "avg_fees": round(fee_sum / n, 6) if n else 0.0,
            "fee_to_gross_swing_ratio": round(fee_sum / gross_swings, 4) if gross_swings > 1e-9 else None,
        }
    return out


def _edge_pair_classification(score: float, n: int) -> str:
    """Five-way label; conservative when sample is tiny."""
    if n < 3:
        return "NEUTRAL"
    if score >= 45 and n >= 5:
        return "STRONG_POSITIVE"
    if score >= 15:
        return "WEAK_POSITIVE"
    if score <= -45 and n >= 3:
        return "STRONG_NEGATIVE"
    if score <= -12:
        return "WEAK_NEGATIVE"
    return "NEUTRAL"


def run_edge_extraction(
    *,
    runtime_root: Optional[Path] = None,
    trade_events_path: Optional[Path] = None,
) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    events, ev_src = resolve_trade_events(root, trade_events_path)

    diag_path = root / "data" / "deployment" / "first_20_trade_diagnostics.jsonl"
    alt_diag = root / "databank" / "first_20_trade_diagnostics.jsonl"
    diagnostics = _load_jsonl_by_trade_id(diag_path)
    if not diagnostics and alt_diag.is_file():
        diagnostics = _load_jsonl_by_trade_id(alt_diag)
        diag_note = str(alt_diag)
    else:
        diag_note = str(diag_path) if diag_path.is_file() else "missing"

    loop_proof = _load_json(root / "data" / "control" / "universal_execution_loop_proof.json")
    validation = _load_json(root / "data" / "control" / "universal_execution_validation.json")
    last_tid = None
    last_proven: Optional[bool] = None
    if loop_proof:
        last_tid = str(loop_proof.get("last_trade_id") or "").strip() or None
        last_proven = loop_proof.get("final_execution_proven")
        if last_proven is None:
            last_proven = loop_proof.get("BUY_SELL_LOG_REBUY_RUNTIME_PROVEN")
    if validation and last_tid is None:
        last_tid = str(validation.get("trade_id") or validation.get("last_trade_id") or "").strip() or None
    if validation and last_proven is None:
        last_proven = validation.get("execution_success") or validation.get("final_execution_proven")

    master_rows: List[Dict[str, Any]] = []
    # Per strategy order for trade_number_sequence fallback
    strat_seq: Dict[str, int] = defaultdict(int)

    for raw in events:
        if not isinstance(raw, dict):
            continue
        tid = str(raw.get("trade_id") or "").strip()
        if not tid:
            continue
        diag = diagnostics.get(tid, {})
        merged = dict(raw)
        scores = compute_scores_for_trade(merged)
        strat = str(merged.get("strategy_id") or "unknown")
        strat_seq[strat] += 1
        gate, gate_attr = _gate_id_and_attribution(merged, diag)
        product = _product(merged, diag)
        se, sx = _infer_sides(merged)
        ts_close = merged.get("timestamp_close") or merged.get("created_at")
        rb = _return_bps(merged, diag)
        slip_est = _slippage_bps_combined(merged)
        spread_est = _spread_bps(merged)
        ex_succ = _execution_success_for_row(merged, diag, last_tid, last_proven)
        adaptive = diag.get("adaptive_mode_post_trade") or diag.get("adaptive_mode_at_entry")
        if adaptive is None:
            adaptive = merged.get("degraded_mode")
            if adaptive is not None:
                adaptive = "degraded" if adaptive else "normal"
        lesson = bool(diag.get("lesson_influence_applied", False))
        rebuy_used = diag.get("rebuy_used")
        if rebuy_used is None:
            rebuy_used = merged.get("rebuy_used")
        tseq = diag.get("trade_number_in_phase")
        if tseq is None:
            tseq = strat_seq[strat]

        row_out: Dict[str, Any] = {
            "trade_id": tid,
            "timestamp": ts_close,
            "avenue_id": str(merged.get("avenue_id") or ""),
            "gate_id": gate,
            "gate_id_attribution": gate_attr,
            "strategy_id": strat,
            "product_id": product,
            "symbol": merged.get("symbol") or merged.get("asset"),
            "side_entry": se,
            "side_exit": sx,
            "hold_seconds": _num(merged.get("hold_seconds")),
            "entry_price": _num(merged.get("actual_entry_price") or merged.get("avg_entry_price")),
            "exit_price": _num(merged.get("actual_exit_price") or merged.get("avg_exit_price")),
            "gross_pnl": _num(merged.get("gross_pnl")),
            "net_pnl": _num(merged.get("net_pnl")),
            "return_bps": rb,
            "fees_paid": _num(merged.get("fees_paid")),
            "slippage_estimate": round(slip_est, 4),
            "spread_estimate": spread_est,
            "slippage_estimate_bps_combined": round(slip_est, 4),
            "spread_estimate_bps": spread_est,
            "execution_success": ex_succ,
            "exit_reason": str(merged.get("exit_reason") or diag.get("exit_reason") or ""),
            "adaptive_mode": adaptive,
            "candidate_rank_score": diag.get("candidate_rank_score"),
            "lesson_influence_applied": lesson,
            "rebuy_used": rebuy_used,
            "trade_number_sequence": tseq,
            "execution_score": scores.get("execution_score"),
            "trade_quality_score": scores.get("trade_quality_score"),
        }
        master_rows.append(row_out)

    master_rows.sort(key=lambda r: str(r.get("timestamp") or ""))

    analysis_dir = root / "data" / "analysis"
    control_dir = root / "data" / "control"
    reports_dir = root / "data" / "reports"
    for d in (analysis_dir, control_dir, reports_dir):
        d.mkdir(parents=True, exist_ok=True)

    meta = {
        "generated_at": _utc_now_iso(),
        "runtime_root": str(root),
        "trade_events_source": ev_src,
        "trade_event_count": len(master_rows),
        "first_20_diagnostics": diag_note,
        "diagnostics_matched": sum(1 for r in master_rows if r["trade_id"] in diagnostics),
        "universal_execution_last_trade_id": last_tid,
        "honesty": {
            "small_sample_warnings": len(master_rows) < 10,
            "tick_data_available": False,
            "return_bps_long_only_assumption_if_unlabeled": True,
        },
    }

    atomic_write_json(analysis_dir / "trade_master_dataset.json", {"meta": meta, "rows": master_rows})

    # --- Section 2 execution vs edge ---
    losses = [r for r in master_rows if _num(r.get("net_pnl")) < 0]
    exec_loss = edge_loss = 0
    for r in losses:
        raw_ev = next((x for x in events if str(x.get("trade_id")) == r["trade_id"]), {})
        d = diagnostics.get(r["trade_id"], {})
        if not isinstance(raw_ev, dict):
            raw_ev = {}
        if _is_execution_loss(raw_ev, d):
            exec_loss += 1
        else:
            edge_loss += 1
    total_l = len(losses)
    el_pct = (exec_loss / total_l * 100.0) if total_l else 0.0
    ed_pct = (edge_loss / total_l * 100.0) if total_l else 0.0
    dom = "INSUFFICIENT_DATA"
    if total_l:
        dom = "EXECUTION" if el_pct >= ed_pct else "EDGE"
        if el_pct > 40.0:
            dom = "EXECUTION_SYSTEM_DOMINANT"

    exec_vs = {
        "meta": {**meta, "section": "execution_vs_edge_truth"},
        "execution_loss_count": exec_loss,
        "edge_loss_count": edge_loss,
        "execution_loss_pct": round(el_pct, 4),
        "edge_loss_pct": round(ed_pct, 4),
        "dominant_problem_type": dom,
        "note_if_execution_gt_40_pct": (
            "System/execution problem likely dominates — address costs, fills, and latency before strategy changes."
            if el_pct > 40.0 and total_l >= 3
            else None
        ),
    }
    atomic_write_json(analysis_dir / "execution_vs_edge_truth.json", exec_vs)

    # --- Pattern grouping ---
    agg = _aggregate_groups(master_rows)
    # consecutive loss sequences per strategy
    by_strat: Dict[str, List[float]] = defaultdict(list)
    order: Dict[str, List[str]] = defaultdict(list)
    for r in sorted(master_rows, key=lambda x: str(x.get("timestamp") or "")):
        by_strat[str(r.get("strategy_id") or "unknown")].append(_num(r.get("net_pnl")))
        order[str(r.get("strategy_id") or "unknown")].append(str(r.get("trade_id")))

    max_consec_by_strat = {s: _max_consecutive_losses(pnls) for s, pnls in by_strat.items()}

    losing_patterns: List[Dict[str, Any]] = []
    winning_patterns: List[Dict[str, Any]] = []

    for key, g in agg.items():
        n = int(g["trades"])
        exp = float(g["expectancy"])
        wr = float(g["win_rate"])
        fee_ratio = g.get("fee_to_gross_swing_ratio")
        flags_l: List[str] = []
        flags_w: List[str] = []
        if n >= 5 and exp < 0:
            flags_l.append("NEGATIVE_EXPECTANCY_STRONG")
        if fee_ratio is not None and fee_ratio > 0.40:
            flags_l.append("HIGH_FEE_DRAIN")
            flags_w.append("HIGH_FEE_DRAIN")
        hb = g["hold_time_bucket"]
        if hb == "<30s" and n >= 3 and wr < 0.35 and exp < 0:
            flags_l.append("QUICK_LOSS_PATTERN")
        if hb == "5m+" and n >= 3 and exp < 0:
            flags_l.append("SLOW_BLEED_PATTERN")

        if n >= 5 and exp > 0:
            flags_w.append("POSITIVE_EXPECTANCY_STRONG")
        if n >= 4 and wr > 0.65 and exp > 0:
            flags_w.append("HIGH_CONFIDENCE_PATTERN")
        slip = float(g.get("avg_slippage_bps_combined") or 0)
        avg_fee_g = float(g.get("avg_fees") or 0)
        if n >= 4 and slip < 45 and avg_fee_g > 0 and exp > 0:
            if slip < 35 and avg_fee_g <= 3 * max(1e-9, abs(float(g["net_pnl"])) / n + 1e-9):
                flags_w.append("CLEAN_EXECUTION_PATTERN")

        entry_l = {**g, "flags": flags_l, "max_consecutive_losses_strategy": max_consec_by_strat.get(g["strategy_id"], 0)}
        entry_w = dict(g)
        entry_w["flags"] = flags_w
        if flags_l:
            losing_patterns.append(entry_l)
        if flags_w:
            winning_patterns.append(entry_w)

    atomic_write_json(
        analysis_dir / "losing_patterns.json",
        {
            "meta": meta,
            "consecutive_loss_sequences_by_strategy": [
                {"strategy_id": s, "max_consecutive_losses": c}
                for s, c in sorted(max_consec_by_strat.items(), key=lambda kv: -kv[1])
            ],
            "patterns": sorted(losing_patterns, key=lambda x: x.get("expectancy", 0)),
        },
    )
    atomic_write_json(
        analysis_dir / "winning_patterns.json",
        {"meta": meta, "patterns": sorted(winning_patterns, key=lambda x: -x.get("expectancy", 0))},
    )

    # --- Edge scores per (strategy, product) ---
    pair_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in master_rows:
        sk = str(r.get("strategy_id") or "unknown")
        pk = str(r.get("product_id") or "unknown")
        pair_groups[f"{sk}::{pk}"].append(r)

    edge_scores: Dict[str, Any] = {"meta": meta, "pairs": []}
    kill_actions: List[Dict[str, Any]] = []
    focus_actions: List[Dict[str, Any]] = []
    ts = _utc_now_iso()

    for pk, grp in sorted(pair_groups.items()):
        strategy_id, product_id = pk.split("::", 1)
        pnls = [_num(x.get("net_pnl")) for x in grp]
        n = len(pnls)
        wins = sum(1 for p in pnls if p > 0)
        wr = wins / n if n else 0.0
        exp = sum(pnls) / n if n else 0.0
        mdd = 0.0
        run = 0.0
        for p in pnls:
            run += p
            mdd = min(mdd, run)
            if p > 0:
                run = 0.0
        ex_scores = [_num(x.get("execution_score")) for x in grp if x.get("execution_score") is not None]
        avg_ex = sum(ex_scores) / len(ex_scores) if ex_scores else 50.0
        if n > 1:
            try:
                stdev = statistics.pstdev(pnls)
            except statistics.StatisticsError:
                stdev = 0.0
        else:
            stdev = 0.0
        mean_abs = statistics.mean([abs(x) for x in pnls]) if pnls else 0.0
        consistency = 1.0 - min(1.0, stdev / (mean_abs + 1e-6)) if n >= 3 else 0.0

        # Weighted score -100..100 (conservative scaling)
        score = (
            max(-40.0, min(40.0, exp * 80.0))
            + (wr - 0.5) * 70.0
            + max(-25.0, min(25.0, -mdd * 15.0))
            + consistency * 20.0
            + (avg_ex - 70.0) * 0.35
        )
        score = max(-100.0, min(100.0, score))

        cls = _edge_pair_classification(score, n)

        rec = {
            "strategy_id": strategy_id,
            "product_id": product_id,
            "sample_size": n,
            "edge_score": round(score, 4),
            "classification": cls,
            "expectancy": round(exp, 6),
            "win_rate": round(wr, 4),
            "max_equity_run_loss_approx": round(mdd, 6),
            "consistency_score": round(consistency, 4),
            "avg_execution_score": round(avg_ex, 2),
        }
        edge_scores["pairs"].append(rec)

        if cls == "STRONG_NEGATIVE" and n >= 3:
            kill_actions.append(
                {
                    "ts": ts,
                    "affected_strategy": strategy_id,
                    "affected_product": product_id,
                    "reason": "STRONG_NEGATIVE edge_score",
                    "evidence": {"edge_score": rec["edge_score"], "n": n, "expectancy": rec["expectancy"]},
                }
            )
        elif cls == "WEAK_NEGATIVE" and n >= 3:
            kill_actions.append(
                {
                    "ts": ts,
                    "affected_strategy": strategy_id,
                    "affected_product": product_id,
                    "reason": "WEAK_NEGATIVE reduce size 50%",
                    "evidence": {"edge_score": rec["edge_score"], "n": n},
                }
            )

    # Fee drain / quick loss from pattern lists
    for lp in losing_patterns:
        if "HIGH_FEE_DRAIN" in lp.get("flags", []):
            kill_actions.append(
                {
                    "ts": ts,
                    "affected_strategy": lp["strategy_id"],
                    "affected_product": lp["product_id"],
                    "reason": "HIGH_FEE_DRAIN — reduce frequency or skip",
                    "evidence": {"fee_to_gross_swing_ratio": lp.get("fee_to_gross_swing_ratio"), "bucket": lp.get("hold_time_bucket")},
                }
            )
        if "QUICK_LOSS_PATTERN" in lp.get("flags", []):
            kill_actions.append(
                {
                    "ts": ts,
                    "affected_strategy": lp["strategy_id"],
                    "affected_product": lp["product_id"],
                    "reason": "QUICK_LOSS_PATTERN — block fast re-entry",
                    "evidence": {"hold_time_bucket": lp.get("hold_time_bucket"), "win_rate": lp.get("win_rate")},
                }
            )

    for wp in winning_patterns:
        if "CLEAN_EXECUTION_PATTERN" in wp.get("flags", []):
            focus_actions.append(
                {
                    "ts": ts,
                    "strategy_id": wp["strategy_id"],
                    "product_id": wp["product_id"],
                    "action": "prioritize_in_ranking",
                    "reason": "CLEAN_EXECUTION_PATTERN",
                    "evidence": {"avg_slippage_bps_combined": wp.get("avg_slippage_bps_combined")},
                }
            )

    for rec in edge_scores["pairs"]:
        c = rec["classification"]
        if c == "STRONG_POSITIVE":
            focus_actions.append(
                {
                    "ts": ts,
                    "strategy_id": rec["strategy_id"],
                    "product_id": rec["product_id"],
                    "action": "allow_normal_size_no_increase",
                    "reason": "STRONG_POSITIVE — do not upsize yet",
                    "evidence": {"edge_score": rec["edge_score"], "n": rec["sample_size"]},
                }
            )
        elif c == "WEAK_POSITIVE":
            focus_actions.append(
                {
                    "ts": ts,
                    "strategy_id": rec["strategy_id"],
                    "product_id": rec["product_id"],
                    "action": "keep_monitor",
                    "reason": "WEAK_POSITIVE",
                    "evidence": {"edge_score": rec["edge_score"]},
                }
            )

    atomic_write_json(control_dir / "edge_scores.json", edge_scores)
    atomic_write_json(
        control_dir / "edge_kill_switches.json",
        {"meta": meta, "actions": kill_actions, "honesty": "artifacts_only_no_runtime_enforcement"},
    )
    atomic_write_json(
        control_dir / "edge_focus_adjustments.json",
        {"meta": meta, "adjustments": focus_actions},
    )

    # --- Cost pressure ---
    n_all = len(master_rows)
    fees_all = [abs(_num(r.get("fees_paid"))) for r in master_rows]
    slip_all = [float(r.get("slippage_estimate") or 0) for r in master_rows]
    avg_fee = sum(fees_all) / n_all if n_all else 0.0
    avg_slip = sum(slip_all) / n_all if n_all else 0.0
    pnl_lt_fee = 0
    pnl_lt_cost = 0
    for r in master_rows:
        net = _num(r.get("net_pnl"))
        fee = abs(_num(r.get("fees_paid")))
        slip_bps = float(r.get("slippage_estimate") or 0)
        gross = abs(_num(r.get("gross_pnl")))
        slip_dollar_est = gross * (slip_bps / 10000.0) if gross > 0 else 0.0
        if net < fee:
            pnl_lt_fee += 1
        if net < (fee + slip_dollar_est):
            pnl_lt_cost += 1
    pf_pct = (pnl_lt_fee / n_all * 100.0) if n_all else 0.0
    pc_pct = (pnl_lt_cost / n_all * 100.0) if n_all else 0.0
    cost_dominated = bool(n_all >= 5 and (pf_pct > 50.0 or pc_pct > 50.0))

    atomic_write_json(
        analysis_dir / "cost_pressure.json",
        {
            "meta": meta,
            "avg_fee_per_trade": round(avg_fee, 8),
            "avg_slippage_bps_combined": round(avg_slip, 4),
            "pct_trades_net_pnl_lt_fees": round(pf_pct, 4),
            "pct_trades_net_pnl_lt_fees_plus_slippage_est": round(pc_pct, 4),
            "COST_DOMINATED_SYSTEM": cost_dominated,
        },
    )

    # --- Timing ---
    holds = [_num(r.get("hold_seconds")) for r in master_rows]
    pnls_t = [_num(r.get("net_pnl")) for r in master_rows]
    corr_hp = _corr(holds, pnls_t)
    bucket_med: Dict[str, List[float]] = defaultdict(list)
    for r in master_rows:
        hb = _hold_bucket(_num(r.get("hold_seconds")))
        bucket_med[hb].append(_num(r.get("net_pnl")))
    medians = {}
    for hb, vals in bucket_med.items():
        if vals:
            medians[hb] = statistics.median(vals)
    opt_range = "unknown"
    worst_range = "unknown"
    if medians:
        opt_range = max(medians, key=lambda k: medians[k])
        worst_range = min(medians, key=lambda k: medians[k])

    atomic_write_json(
        analysis_dir / "timing_edge.json",
        {
            "meta": meta,
            "tick_series_available": False,
            "hold_seconds_vs_net_pnl_correlation": round(corr_hp, 4) if corr_hp is not None else None,
            "median_net_pnl_by_hold_bucket": {k: round(v, 6) for k, v in medians.items()},
            "optimal_hold_seconds_range": opt_range,
            "worst_hold_seconds_range": worst_range,
            "note": "optimal/worst ranges are bucket medians from realized trades — not predictive without more data",
        },
    )

    # --- Operator report ---
    net_total = sum(_num(r.get("net_pnl")) for r in master_rows)
    exp_all = net_total / n_all if n_all else 0.0
    worst_pairs = sorted(edge_scores["pairs"], key=lambda x: x.get("edge_score", 0))[:8]
    best_pairs = sorted(edge_scores["pairs"], key=lambda x: -x.get("edge_score", 0))[:8]

    why_lose = "Insufficient trades to conclude."
    if n_all:
        if dom.startswith("EXECUTION") and total_l >= 2:
            why_lose = "Losses skew toward execution/costs (slippage, fees, fills, latency) — see execution_vs_edge_truth."
        elif total_l:
            why_lose = "Losses skew toward edge (direction/timing/strategy) — see losing_patterns and edge_scores."

    op_json = {
        "meta": meta,
        "answers": {
            "1_why_losing_money": why_lose,
            "2_execution_or_edge": dom if total_l else "N/A",
            "3_worst_strategies_products": worst_pairs,
            "4_stop_immediately": [k for k in kill_actions if "STRONG_NEGATIVE" in k.get("reason", "")],
            "5_promising_patterns": [p for p in winning_patterns if "POSITIVE_EXPECTANCY_STRONG" in p.get("flags", [])][:12],
            "6_fees_slippage_killing": cost_dominated,
            "7_safest_next_adjustment": (
                "Reduce trading frequency and fix execution costs (fees/slippage/latency) before changing strategy weights."
                if el_pct > 40 and total_l >= 3
                else "Halve size on WEAK_NEGATIVE pairs; disable STRONG_NEGATIVE pairs; monitor WEAK_POSITIVE without size-up."
            ),
        },
        "totals": {"net_pnl": round(net_total, 6), "expectancy": round(exp_all, 6), "sample_size": n_all},
    }
    atomic_write_json(reports_dir / "edge_operator_report.json", op_json)

    lines = [
        f"Edge Operator Report — generated {meta['generated_at']}",
        f"Sample: {n_all} trades | Net PnL: {net_total:.6f} | Expectancy/trade: {exp_all:.6f}",
        f"Dominant loss driver (among losses): {dom}",
        f"Execution loss % of losses: {el_pct:.2f}% | Edge loss %: {ed_pct:.2f}%",
        f"Cost dominated system: {cost_dominated}",
        "",
        "Worst edge_score pairs:",
    ]
    for p in worst_pairs:
        lines.append(f"  - {p.get('strategy_id')} / {p.get('product_id')}: score={p.get('edge_score')} n={p.get('sample_size')}")
    lines.extend(["", "Best edge_score pairs:", ""])
    for p in best_pairs:
        lines.append(f"  - {p.get('strategy_id')} / {p.get('product_id')}: score={p.get('edge_score')} n={p.get('sample_size')}")
    lines.extend(
        [
            "",
            "Kill-switch actions (artifact log — enforce separately):",
            json.dumps(kill_actions, indent=2),
            "",
            op_json["answers"]["7_safest_next_adjustment"],
        ]
    )
    (reports_dir / "edge_operator_report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # --- Final truth ---
    edge_detected = any(float(p.get("edge_score", 0)) > 20 for p in edge_scores["pairs"] if int(p.get("sample_size") or 0) >= 3)
    strong_pairs = [p for p in edge_scores["pairs"] if float(p.get("edge_score", 0)) >= 45 and int(p.get("sample_size") or 0) >= 5]
    emerging = [
        p
        for p in edge_scores["pairs"]
        if 25 <= float(p.get("edge_score", 0)) < 45 and int(p.get("sample_size") or 0) >= 4
    ]
    if strong_pairs:
        strength = "STRONG"
    elif emerging:
        strength = "EMERGING"
    elif edge_detected:
        strength = "WEAK"
    else:
        strength = "NONE"

    runtime_proven = bool(strong_pairs or (len(emerging) >= 2 and n_all >= 15))

    final_truth = {
        "meta": meta,
        "sample_size": n_all,
        "net_pnl": round(net_total, 6),
        "expectancy": round(exp_all, 6),
        "dominant_loss_type": dom,
        "cost_dominated": cost_dominated,
        "edge_detected": edge_detected,
        "edge_strength": strength,
        "EDGE_RUNTIME_PROVEN": runtime_proven,
    }
    atomic_write_json(control_dir / "edge_final_truth.json", final_truth)

    return {
        "ok": True,
        "runtime_root": str(root),
        "artifacts": {
            "trade_master_dataset": str(analysis_dir / "trade_master_dataset.json"),
            "execution_vs_edge": str(analysis_dir / "execution_vs_edge_truth.json"),
            "losing_patterns": str(analysis_dir / "losing_patterns.json"),
            "winning_patterns": str(analysis_dir / "winning_patterns.json"),
            "edge_scores": str(control_dir / "edge_scores.json"),
            "edge_kill_switches": str(control_dir / "edge_kill_switches.json"),
            "edge_focus": str(control_dir / "edge_focus_adjustments.json"),
            "cost_pressure": str(analysis_dir / "cost_pressure.json"),
            "timing_edge": str(analysis_dir / "timing_edge.json"),
            "operator_json": str(reports_dir / "edge_operator_report.json"),
            "operator_txt": str(reports_dir / "edge_operator_report.txt"),
            "edge_final_truth": str(control_dir / "edge_final_truth.json"),
        },
        "summary": final_truth,
        "execution_vs_edge": exec_vs,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Build edge extraction artifacts from trade_events + diagnostics.")
    ap.add_argument("--runtime-root", type=Path, default=None, help="EZRAS_RUNTIME_ROOT (default: env or ~/ezras-runtime)")
    ap.add_argument("--trade-events", type=Path, default=None, help="Override path to trade_events.jsonl")
    args = ap.parse_args(list(argv) if argv is not None else None)
    out = run_edge_extraction(runtime_root=args.runtime_root, trade_events_path=args.trade_events)
    print(json.dumps({k: out[k] for k in ("ok", "runtime_root", "artifacts", "summary")}, indent=2))
    return 0
