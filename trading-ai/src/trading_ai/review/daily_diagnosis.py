"""
Daily self-diagnosis from trade logs, summaries, edge truth, execution metrics, governance.

Read-only: aggregates and recommends risk/discipline posture — **never places trades**.
"""

from __future__ import annotations

import json
import logging
import os
import statistics
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from trading_ai.review.paths import external_context_override_path

logger = logging.getLogger(__name__)

MIN_TRADES_FOR_RAISE_CONFIDENCE = 30


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


def _parse_day(val: Any) -> Optional[date]:
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    try:
        if "T" in s or s.endswith("Z") or "+" in s[-6:]:
            raw = s.replace("Z", "+00:00")
            return datetime.fromisoformat(raw).date()
        return date.fromisoformat(s[:10])
    except Exception:
        return None


def load_json_file(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.debug("load_json_file %s: %s", path, exc)
        return None


def _read_jsonl(path: Path, *, max_lines: int = 50_000) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not path.is_file():
        return out
    try:
        with path.open("r", encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i >= max_lines:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if isinstance(rec, dict):
                        out.append(rec)
                except json.JSONDecodeError:
                    continue
    except OSError as exc:
        logger.debug("read_jsonl %s: %s", path, exc)
    return out


def _trade_events_safe() -> List[Dict[str, Any]]:
    try:
        from trading_ai.nte.databank.local_trade_store import load_all_trade_events

        return load_all_trade_events()
    except Exception as exc:
        logger.debug("trade events unavailable: %s", exc)
        return []


def _trade_logs_jsonl(trade_logs_dir: Optional[Path]) -> List[Dict[str, Any]]:
    if trade_logs_dir is None or not trade_logs_dir.is_dir():
        return []
    rows: List[Dict[str, Any]] = []
    try:
        for p in sorted(trade_logs_dir.glob("*.jsonl")):
            rows.extend(_read_jsonl(p, max_lines=20_000))
    except OSError as exc:
        logger.debug("trade_logs scan: %s", exc)
    return rows


def _net_pnl(rec: Mapping[str, Any]) -> float:
    for k in ("net_pnl_usd", "net_pnl", "pnl_usd", "realized_pnl_usd"):
        if k in rec and rec[k] is not None:
            try:
                return float(rec[k])
            except (TypeError, ValueError):
                continue
    return 0.0


def _filter_day(trades: Sequence[Mapping[str, Any]], day: date) -> List[Mapping[str, Any]]:
    out: List[Mapping[str, Any]] = []
    for t in trades:
        d = _parse_day(t.get("timestamp_close") or t.get("closed_at") or t.get("ts") or t.get("date"))
        if d == day:
            out.append(t)
    return out


def _venue_key(t: Mapping[str, Any]) -> str:
    v = t.get("venue") or t.get("exchange") or t.get("platform") or "unknown"
    return str(v).strip().lower() or "unknown"


def _execution_samples(path: Optional[Path]) -> Tuple[List[float], List[float], int]:
    if path is None or not path.is_file():
        return [], [], 0
    slips: List[float] = []
    lats: List[float] = []
    anomalies = 0
    for row in _read_jsonl(path, max_lines=20_000):
        try:
            if "slippage_bps" in row:
                slips.append(abs(float(row["slippage_bps"])))
            elif "slippage" in row:
                slips.append(abs(float(row["slippage"])))
        except (TypeError, ValueError):
            pass
        try:
            if "latency_ms" in row:
                lats.append(float(row["latency_ms"]))
        except (TypeError, ValueError):
            pass
        if bool(row.get("degraded")) or bool(row.get("anomaly")):
            anomalies += 1
    return slips, lats, anomalies


def _discipline_scores_from_log(path: Optional[Path]) -> Tuple[List[float], int]:
    if path is None or not path.is_file():
        return [], 0
    scores: List[float] = []
    violations = 0
    for row in _read_jsonl(path, max_lines=10_000):
        try:
            if "discipline_score" in row:
                scores.append(float(row["discipline_score"]))
        except (TypeError, ValueError):
            pass
        violations += int(row.get("violation") or row.get("violation_event") or 0)
    return scores, violations


def advisory_web_enrichment(
    as_of: date,
    metrics: Mapping[str, Any],
) -> Dict[str, Any]:
    """
    Optional contextual annotations (advisory only). Never affects order logic here.

    Sources:
    - ``EZRAS_REVIEW_EXTERNAL_CONTEXT_JSON`` env (JSON object with external_context list)
    - ``data/review/external_context_override.json`` if present
    """
    ext: List[str] = []
    explain: List[str] = []
    raw_env = (os.environ.get("EZRAS_REVIEW_EXTERNAL_CONTEXT_JSON") or "").strip()
    if raw_env:
        try:
            inj = json.loads(raw_env)
            if isinstance(inj, dict):
                for x in inj.get("external_context") or []:
                    ext.append(str(x))
                for x in inj.get("possible_market_explanation") or []:
                    explain.append(str(x))
        except (json.JSONDecodeError, TypeError):
            pass
    p = external_context_override_path()
    ovr = load_json_file(p)
    if ovr:
        for x in ovr.get("external_context") or []:
            ext.append(str(x))
        for x in ovr.get("possible_market_explanation") or []:
            explain.append(str(x))
    if not ext and not explain:
        ext.append(
            f"No live web fetch for {as_of.isoformat()} (advisory layer; "
            "set EZRAS_REVIEW_EXTERNAL_CONTEXT_JSON or external_context_override.json)."
        )
    return {"external_context": ext[:50], "possible_market_explanation": explain[:50]}


def recommend_risk_mode(metrics: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Returns risk posture recommendation from measured metrics only (advisory).

    ``metrics`` may include:
    rolling_expectancy, consecutive_losses, fee_to_pnl_ratio, slippage_consuming_edge,
    discipline_deteriorating, anomalies_rising, validated_edge_post_fee_positive,
    drawdown_fraction, execution_healthy, trade_count, high_confidence_sample.
    """
    roll_exp = float(metrics.get("rolling_expectancy") or 0.0)
    losses_streak = int(metrics.get("consecutive_losses") or 0)
    fee_ratio = float(metrics.get("fee_to_pnl_ratio") or 0.0)
    slip_eat = bool(metrics.get("slippage_consuming_edge"))
    disc_bad = bool(metrics.get("discipline_deteriorating"))
    anomalies_rising = bool(metrics.get("anomalies_rising"))
    edge_ok = bool(metrics.get("validated_edge_post_fee_positive"))
    dd = float(metrics.get("drawdown_fraction") or 0.0)
    exec_ok = metrics.get("execution_healthy")
    if exec_ok is None:
        exec_ok = True
    exec_ok = bool(exec_ok)
    n = int(metrics.get("trade_count") or 0)
    high_n = bool(metrics.get("high_confidence_sample"))

    lower_reasons: List[str] = []
    if roll_exp < 0:
        lower_reasons.append("rolling_expectancy_negative")
    if losses_streak >= 3:
        lower_reasons.append("loss_streak_3plus")
    if fee_ratio > 0.35 and n >= 5:
        lower_reasons.append("fees_high_vs_pnl")
    if slip_eat:
        lower_reasons.append("slippage_consuming_edge")
    if disc_bad:
        lower_reasons.append("discipline_deteriorating")
    if anomalies_rising:
        lower_reasons.append("anomalies_rising")

    raise_reasons: List[str] = []
    if edge_ok and dd < 0.15 and exec_ok and n >= MIN_TRADES_FOR_RAISE_CONFIDENCE and high_n:
        raise_reasons.append("validated_edge_post_fee_drawdown_ok_execution_clean_sample_adequate")

    if lower_reasons:
        return {
            "risk_mode": "lower_risk",
            "reason": "; ".join(lower_reasons),
            "size_multiplier_recommendation": max(0.25, 0.5 ** min(len(lower_reasons), 3)),
        }
    if raise_reasons and not lower_reasons:
        return {
            "risk_mode": "raise_risk",
            "reason": "; ".join(raise_reasons),
            "size_multiplier_recommendation": min(1.25, 1.0 + 0.05 * min(n // 50, 5)),
        }
    return {
        "risk_mode": "hold_risk",
        "reason": "no_strong_lower_signals;_raise_bar_not_met",
        "size_multiplier_recommendation": 1.0,
    }


def discipline_recommendations(metrics: Mapping[str, Any], *, governance_healthy: bool) -> Dict[str, Any]:
    """
    When to tighten / maintain / allow slightly more aggression (advisory).
    Aggression only if governance healthy, execution clean, edge validated, meaningful sample.
    """
    disc = float(metrics.get("discipline_score_avg") or 100.0)
    exec_ok = bool(metrics.get("execution_healthy", True))
    edge_ok = bool(metrics.get("validated_edge_post_fee_positive"))
    n = int(metrics.get("trade_count") or 0)
    anomalies = int(metrics.get("anomaly_count") or 0)

    if disc < 70 or anomalies >= 5 or not governance_healthy:
        posture = "tighten_discipline"
        detail = "Low discipline score, elevated anomalies, or governance stress — reduce discretion."
    elif disc >= 85 and exec_ok and edge_ok and n >= 20:
        posture = "maintain_discipline"
        detail = "Healthy discipline with clean execution and validated edge — keep process."
    elif (
        governance_healthy
        and exec_ok
        and edge_ok
        and n >= MIN_TRADES_FOR_RAISE_CONFIDENCE
        and disc >= 80
        and anomalies < 3
    ):
        posture = "slightly_more_aggression_allowed"
        detail = "Governance and execution clean; edge validated; sample adequate — small size increases may be justified."
    else:
        posture = "maintain_discipline"
        detail = "Insufficient evidence for loosening — hold course."

    return {"posture": posture, "detail": detail}


def build_diagnosis(
    *,
    as_of: Optional[date] = None,
    trade_logs_dir: Optional[Path] = None,
    databank_events: Optional[Sequence[Mapping[str, Any]]] = None,
    daily_summary: Optional[Dict[str, Any]] = None,
    weekly_summary: Optional[Dict[str, Any]] = None,
    edge_truth_summary: Optional[Dict[str, Any]] = None,
    edge_registry_edges: Optional[Sequence[Mapping[str, Any]]] = None,
    execution_metrics_file: Optional[Path] = None,
    discipline_log_file: Optional[Path] = None,
    halt_present: bool = False,
    halt_reason: str = "",
    system_guard: Optional[Dict[str, Any]] = None,
    portfolio_state: Optional[Dict[str, Any]] = None,
    prior_anomaly_count: int = 0,
) -> Dict[str, Any]:
    """Assemble structured diagnosis dict for ``as_of`` (UTC date)."""
    day = as_of or _utc_today()
    events = list(databank_events) if databank_events is not None else _trade_events_safe()
    log_rows = _trade_logs_jsonl(trade_logs_dir)
    combined: List[Mapping[str, Any]] = list(events) + log_rows
    day_trades = _filter_day(combined, day)

    total_trades = len(day_trades)
    pnls = [_net_pnl(t) for t in day_trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    win_rate = len(wins) / total_trades if total_trades else 0.0
    gross_pnl = sum(pnls)
    fees = sum(float(t.get("fees_usd") or t.get("fees") or 0) for t in day_trades)
    net_pnl = sum(_net_pnl(t) for t in day_trades)
    fee_ratio = abs(fees) / max(abs(net_pnl), 1e-9) if total_trades else 0.0

    slips, lats, anomaly_exec = _execution_samples(execution_metrics_file)
    avg_slip = float(statistics.mean(slips)) if slips else 0.0
    avg_lat = float(statistics.mean(lats)) if lats else 0.0

    disc_scores, _viol = _discipline_scores_from_log(discipline_log_file)
    disc_avg = float(statistics.mean(disc_scores)) if disc_scores else 100.0

    expectancy = (sum(pnls) / total_trades) if total_trades else 0.0

    edge_expectancies: List[Tuple[str, float]] = []
    if edge_truth_summary:
        for k, v in (edge_truth_summary.get("edges") or {}).items():
            if not isinstance(v, dict):
                continue
            ne = None
            wins = v.get("windows") or {}
            if isinstance(wins, dict):
                for pref in ("100", "50", "20"):
                    w = wins.get(pref)
                    if isinstance(w, dict) and w.get("net_expectancy") is not None:
                        try:
                            ne = float(w["net_expectancy"])
                            break
                        except (TypeError, ValueError):
                            ne = None
            if ne is not None:
                edge_expectancies.append((str(k), ne))
    best_edge = max(edge_expectancies, key=lambda x: x[1]) if edge_expectancies else ("", 0.0)
    worst_edge = min(edge_expectancies, key=lambda x: x[1]) if edge_expectancies else ("", 0.0)

    venue_perf: Dict[str, Dict[str, Any]] = {}
    for t in day_trades:
        vk = _venue_key(t)
        venue_perf.setdefault(vk, {"trades": 0, "net_pnl": 0.0, "wins": 0})
        venue_perf[vk]["trades"] += 1
        p = _net_pnl(t)
        venue_perf[vk]["net_pnl"] += p
        if p > 0:
            venue_perf[vk]["wins"] += 1
    for vk, agg in venue_perf.items():
        nt = int(agg["trades"])
        agg["win_rate"] = float(agg["wins"]) / nt if nt else 0.0

    sg = system_guard or {}
    consec_losses = int(sg.get("consecutive_losses") or 0)
    guard_anomalies = int(sg.get("execution_anomaly_count") or 0)
    anomaly_total = anomaly_exec + guard_anomalies
    failsafe_triggers: List[str] = []
    if halt_present:
        failsafe_triggers.append(f"trading_halt:{halt_reason or 'unknown'}")
    if consec_losses >= 3:
        failsafe_triggers.append("consecutive_losses_elevated")

    rolling_expectancy = expectancy
    if daily_summary and daily_summary.get("expectancy") is not None:
        try:
            rolling_expectancy = float(daily_summary["expectancy"])
        except (TypeError, ValueError):
            pass

    slip_eat = avg_slip > 25 and gross_pnl > 0 and abs(fees) + avg_slip * 0.01 * max(abs(gross_pnl), 1.0) > abs(
        gross_pnl * 0.2
    )
    disc_bad = disc_avg < 75 and len(disc_scores) >= 3
    anomalies_rising = anomaly_total > prior_anomaly_count and anomaly_total >= 3

    validated_positive = False
    if edge_registry_edges:
        for e in edge_registry_edges:
            st = str(e.get("status") or "").lower()
            conf = float(e.get("confidence") or 0)
            if st in ("validated", "scaled") and conf >= 0.5:
                validated_positive = True
                break
    if edge_expectancies:
        validated_positive = validated_positive or best_edge[1] > 0

    dd_frac = 0.0
    if portfolio_state:
        try:
            dd_frac = abs(float(portfolio_state.get("drawdown_fraction") or portfolio_state.get("drawdown") or 0.0))
        except (TypeError, ValueError):
            dd_frac = 0.0

    metrics_for_risk = {
        "rolling_expectancy": rolling_expectancy,
        "consecutive_losses": consec_losses,
        "fee_to_pnl_ratio": fee_ratio,
        "slippage_consuming_edge": slip_eat,
        "discipline_deteriorating": disc_bad,
        "anomalies_rising": anomalies_rising,
        "validated_edge_post_fee_positive": validated_positive,
        "drawdown_fraction": dd_frac,
        "execution_healthy": avg_lat < 2000 and not anomalies_rising,
        "trade_count": total_trades,
        "high_confidence_sample": total_trades >= MIN_TRADES_FOR_RAISE_CONFIDENCE,
    }

    risk_rec = recommend_risk_mode(metrics_for_risk)
    gov_ok = not halt_present and consec_losses < 5
    disc_rec = discipline_recommendations(
        {
            **metrics_for_risk,
            "discipline_score_avg": disc_avg,
            "anomaly_count": anomaly_total,
        },
        governance_healthy=gov_ok,
    )

    ext = advisory_web_enrichment(day, metrics_for_risk)

    key_problems: List[str] = []
    key_strengths: List[str] = []
    if rolling_expectancy < 0:
        key_problems.append("Negative rolling expectancy.")
    if fee_ratio > 0.4 and total_trades >= 3:
        key_problems.append("Fees large relative to net PnL.")
    if disc_bad:
        key_problems.append("Discipline score trending down.")
    if anomalies_rising:
        key_problems.append("Execution anomalies increasing.")
    if win_rate >= 0.55 and net_pnl > 0:
        key_strengths.append("Positive day with workable win rate.")
    if best_edge[1] > 0:
        key_strengths.append(f"Strongest measured edge: {best_edge[0]} ({best_edge[1]:.4f}).")

    health = "mixed"
    if net_pnl > 0 and rolling_expectancy >= 0 and not key_problems:
        health = "good"
    elif net_pnl < 0 and (rolling_expectancy < 0 or len(key_problems) >= 2):
        health = "bad"

    recommended_actions: List[str] = []
    recommended_actions.append(f"Risk posture: {risk_rec['risk_mode']} — {risk_rec['reason']}")
    recommended_actions.append(f"Discipline: {disc_rec['posture']}")

    diagnosis = {
        "date": day.isoformat(),
        "metrics": {
            "total_trades": total_trades,
            "win_rate": win_rate,
            "gross_pnl": gross_pnl,
            "net_pnl": net_pnl,
            "fees_usd": fees,
            "fees_to_pnl_ratio": fee_ratio,
            "avg_slippage_bps": avg_slip,
            "avg_latency_ms": avg_lat,
            "best_edge": {"edge_id": best_edge[0], "net_expectancy": best_edge[1]},
            "worst_edge": {"edge_id": worst_edge[0], "net_expectancy": worst_edge[1]},
            "discipline_score_avg": disc_avg,
            "anomaly_count": anomaly_total,
            "fail_safe_triggers": failsafe_triggers,
            "venue_performance": venue_perf,
            "rolling_expectancy": rolling_expectancy,
            "daily_summary_ref": bool(daily_summary),
            "weekly_summary_ref": bool(weekly_summary),
        },
        "health": health,
        "key_problems": key_problems[:20],
        "key_strengths": key_strengths[:20],
        "biggest_risk": key_problems[0] if key_problems else "Unmodeled tail / data gaps",
        "best_opportunity": key_strengths[0] if key_strengths else "Collect more post-fee samples",
        "recommended_actions": recommended_actions,
        "risk_recommendation": risk_rec,
        "discipline_recommendation": disc_rec,
        "external_context": ext.get("external_context", []),
        "possible_market_explanation": ext.get("possible_market_explanation", []),
        "portfolio_snapshot": {
            "halt_present": halt_present,
            "halt_reason": halt_reason,
            "portfolio_keys": list(portfolio_state.keys()) if portfolio_state else [],
        },
    }
    return diagnosis


def run_daily_diagnosis(
    *,
    as_of: Optional[date] = None,
    write_files: bool = True,
) -> Dict[str, Any]:
    """
    Callable daily entrypoint: gather inputs from standard paths, build diagnosis,
    optionally write review artifacts and update learning memory hooks.
    """
    from trading_ai.core.system_guard import system_guard_state_path, trading_halt_path
    from trading_ai.edge.registry import EdgeRegistry
    from trading_ai.nte.databank.local_trade_store import path_daily_summary, path_weekly_summary
    from trading_ai.reality.discipline_engine import discipline_log_path
    from trading_ai.reality.edge_truth import edge_truth_summary_path
    from trading_ai.reality.paths import trade_logs_dir
    from trading_ai.monitoring.execution_monitor import execution_metrics_path
    from trading_ai.review.paths import daily_diagnosis_path
    from trading_ai.review import ceo_review_session

    day = as_of or _utc_today()
    halt_path = trading_halt_path()
    halt_present = halt_path.is_file()
    halt_reason = ""
    if halt_present:
        hj = load_json_file(halt_path)
        if hj:
            halt_reason = str(hj.get("reason") or hj.get("message") or "")

    sg_dict = load_json_file(system_guard_state_path()) or {}
    portfolio: Optional[Dict[str, Any]] = None
    try:
        from trading_ai.core.portfolio_engine import portfolio_state_path

        portfolio = load_json_file(portfolio_state_path())
    except Exception:
        portfolio = None

    daily_s = load_json_file(path_daily_summary())
    weekly_s = load_json_file(path_weekly_summary())
    edge_truth = load_json_file(edge_truth_summary_path())
    reg = EdgeRegistry()
    raw_reg = reg.load_raw()
    edges = raw_reg.get("edges") or []

    prior_anomalies = int((edge_truth or {}).get("last_anomaly_count") or 0) if edge_truth else 0

    diagnosis = build_diagnosis(
        as_of=day,
        trade_logs_dir=trade_logs_dir(),
        daily_summary=daily_s,
        weekly_summary=weekly_s,
        edge_truth_summary=edge_truth,
        edge_registry_edges=edges,
        execution_metrics_file=execution_metrics_path(),
        discipline_log_file=discipline_log_path(),
        halt_present=halt_present,
        halt_reason=halt_reason,
        system_guard=sg_dict,
        portfolio_state=portfolio,
        prior_anomaly_count=prior_anomalies,
    )

    if write_files:
        outp = daily_diagnosis_path()
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(json.dumps(diagnosis, indent=2, default=str), encoding="utf-8")
        ceo_review_session.write_ceo_daily_review(diagnosis)
        try:
            from trading_ai.learning.improvement_loop import ingest_daily_diagnosis

            ingest_daily_diagnosis(diagnosis)
        except Exception as exc:
            logger.debug("improvement_loop ingest: %s", exc)

    return diagnosis
