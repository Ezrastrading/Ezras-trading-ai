"""
Daily trading summary (TXT + JSON) under ``data/reports/`` — federated trades + control snapshots.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.runtime_paths import ezras_runtime_root


def _parse_ts(t: Dict[str, Any]) -> Optional[datetime]:
    for k in ("closed_at", "exit_time", "exit_ts", "timestamp", "created_at", "opened_at"):
        v = t.get(k)
        if v is None:
            continue
        if isinstance(v, (int, float)):
            try:
                return datetime.fromtimestamp(float(v), tz=timezone.utc)
            except (TypeError, ValueError, OSError):
                continue
        if isinstance(v, str) and v.strip():
            try:
                s = v.strip().replace("Z", "+00:00")
                return datetime.fromisoformat(s)
            except (TypeError, ValueError):
                continue
    return None


def _day_filter(trades: List[Dict[str, Any]], day: date) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for t in trades:
        dt = _parse_ts(t)
        if dt is None:
            continue
        if dt.date() != day:
            continue
        out.append(t)
    return out


def _gate_label(t: Dict[str, Any]) -> str:
    for k in ("gate", "gate_id", "trade_tag"):
        v = t.get(k)
        if v:
            return str(v).lower()
    prov = t.get("truth_provenance") if isinstance(t.get("truth_provenance"), dict) else {}
    g = prov.get("gate") or prov.get("gate_id")
    if g:
        return str(g).lower()
    av = str(t.get("avenue") or t.get("venue") or "").lower()
    if "kalshi" in av:
        return "gate_b"
    if "coinbase" in av or av in ("nte", "coinbase_spot", "avenue_a"):
        return "gate_a"
    return "unknown"


def _pnl(t: Dict[str, Any]) -> Optional[float]:
    for k in ("net_pnl_usd", "net_pnl", "realized_pnl_usd"):
        v = t.get(k)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def _fees(t: Dict[str, Any]) -> Optional[float]:
    for k in ("fees_usd", "fees_paid", "fees"):
        v = t.get(k)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def _read_json(p: Path) -> Optional[Dict[str, Any]]:
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def build_daily_trading_summary(
    *,
    runtime_root: Optional[Path] = None,
    day: Optional[date] = None,
) -> Dict[str, Any]:
    root = Path(runtime_root or os.environ.get("EZRAS_RUNTIME_ROOT") or ezras_runtime_root()).resolve()
    day = day or datetime.now(timezone.utc).date()

    from trading_ai.control.system_execution_lock import load_system_execution_lock
    from trading_ai.global_layer.trade_truth import load_federated_trades

    lock = load_system_execution_lock(runtime_root=root)
    trades_all, meta = load_federated_trades()
    day_trades = _day_filter(trades_all, day)

    gate_a: List[Dict[str, Any]] = []
    gate_b: List[Dict[str, Any]] = []
    other: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []

    for t in day_trades:
        gl = _gate_label(t)
        row = {
            "trade_id": str(t.get("trade_id") or t.get("id") or ""),
            "timestamp": _parse_ts(t),
            "avenue": str(t.get("avenue") or t.get("avenue_name") or "unknown"),
            "gate": gl,
            "product": str(
                t.get("product_id") or t.get("venue_product_id") or t.get("market_id") or ""
            ),
            "entry_price": t.get("entry_price") or t.get("avg_entry"),
            "exit_price": t.get("exit_price") or t.get("avg_exit"),
            "pnl": _pnl(t),
            "fees": _fees(t),
            "ratio_context": t.get("ratio_context") or t.get("deployment_ratio_context"),
            "execution_status": t.get("execution_status") or t.get("status") or "closed",
            "error_code": t.get("error_code") or t.get("failure_reason"),
        }
        if row["error_code"]:
            failures.append({"trade_id": row["trade_id"], "error_code": row["error_code"]})
        if gl == "gate_b":
            gate_b.append(row)
        elif gl == "gate_a":
            gate_a.append(row)
        else:
            other.append(row)

    def _stats(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        pnls = [float(x["pnl"]) for x in rows if x.get("pnl") is not None]
        fees = [float(x["fees"]) for x in rows if x.get("fees") is not None]
        wins = sum(1 for p in pnls if p > 0)
        total = sum(pnls) if pnls else 0.0
        fees_total = sum(fees) if fees else 0.0
        n = len(rows)
        wr = (wins / n) if n else 0.0
        ap = (total / len(pnls)) if pnls else 0.0
        return {
            "trades_today": n,
            "pnl_usd": round(total, 6),
            "fees_usd": round(fees_total, 6),
            "win_rate": round(wr, 4),
            "avg_profit_usd": round(ap, 6),
        }

    gate_b_status = "disabled"
    if lock.get("gate_b_enabled"):
        gate_b_status = "active"
    elif _read_json(root / "data" / "control" / "honest_live_status_matrix.json"):
        gate_b_status = "scaffold"

    qct = _read_json(root / "data" / "control" / "quote_capital_truth.json") or {}
    dcr = _read_json(root / "data" / "control" / "deployable_capital_report.json") or {}
    res = _read_json(root / "data" / "control" / "reserve_capital_report.json") or {}

    all_pnls = [float(x["pnl"]) for x in (gate_a + gate_b + other) if x.get("pnl") is not None]
    all_fees = [float(x["fees"]) for x in (gate_a + gate_b + other) if x.get("fees") is not None]
    total_pnl = sum(all_pnls) if all_pnls else 0.0
    total_fees = sum(all_fees) if all_fees else 0.0
    ntr = len(day_trades)
    succ = sum(1 for x in (gate_a + gate_b + other) if not x.get("error_code"))

    ai_lines: List[str] = []
    learn_log = root / "data" / "learning" / "system_learning_log.jsonl"
    if learn_log.is_file():
        try:
            raw_lines = learn_log.read_text(encoding="utf-8").splitlines()
            tail = raw_lines[-5:] if len(raw_lines) > 5 else raw_lines
            for ln in tail:
                try:
                    o = json.loads(ln)
                except json.JSONDecodeError:
                    continue
                if isinstance(o, dict):
                    ai_lines.append(
                        f"{o.get('event_type')}: {str(o.get('improvement_suggestion') or '')[:160]}"
                    )
        except OSError:
            pass
    ai_learning_summary = (
        " | ".join(ai_lines)
        if ai_lines
        else "No recent self-learning log entries for this runtime root (or log empty)."
    )

    # Outcome research highlights (optional, local truth).
    top_lesson = None
    top_research_q = None
    next_mode_hint = None
    try:
        rpath = root / "data" / "research" / "ranked_improvement_opportunities.json"
        if rpath.is_file():
            rj = json.loads(rpath.read_text(encoding="utf-8"))
            if isinstance(rj, dict):
                qs = rj.get("top_research_questions") or []
                if isinstance(qs, list) and qs:
                    top_research_q = str(qs[0])[:240]
    except Exception:
        pass
    try:
        lpath = root / "data" / "learning" / "trade_learning_objects.jsonl"
        if lpath.is_file():
            lines = [ln for ln in lpath.read_text(encoding="utf-8").splitlines() if ln.strip()]
            if lines:
                last = json.loads(lines[-1])
                if isinstance(last, dict):
                    top_lesson = str(last.get("recommended_adjustment") or "")[:240] or None
    except Exception:
        pass
    try:
        ppath = root / "data" / "control" / "bounded_risk_posture.json"
        if ppath.is_file():
            pj = json.loads(ppath.read_text(encoding="utf-8"))
            if isinstance(pj, dict):
                cm = int(pj.get("consecutive_net_losses") or 0)
                sm = float(pj.get("size_multiplier") or 1.0)
                if cm >= 3 or sm < 1.0:
                    next_mode_hint = f"REDUCED (size_multiplier={sm:.2f}, consecutive_losses={cm})"
                else:
                    next_mode_hint = "NORMAL"
    except Exception:
        pass

    plain_parts: List[str] = [
        f"Daily trading summary for {day.isoformat()} (UTC calendar day from trade timestamps).",
        f"Gate A (Coinbase NTE-style) trades recorded: {len(gate_a)}. "
        f"Aggregate PnL (where known): {_stats(gate_a)['pnl_usd']:.4f} USD.",
        f"Gate B status: {gate_b_status}. Trades tagged gate_b today: {len(gate_b)}.",
        f"Total closed trades in window: {ntr}. Total PnL (known legs): {total_pnl:.4f} USD. Total fees: {total_fees:.4f} USD.",
        f"Success rows (no error_code): {succ}. Failures logged: {len(failures)}.",
        "",
        "AI Learning Summary:",
        ai_learning_summary,
    ]
    if top_lesson:
        plain_parts.extend(["", "Top Lesson:", top_lesson])
    if top_research_q:
        plain_parts.extend(["", "Top Research Question:", top_research_q])
    if next_mode_hint:
        plain_parts.extend(["", f"Recommended Next Operating Mode: {next_mode_hint}"])
    if failures:
        plain_parts.append("Failure reasons: " + "; ".join(f"{f['trade_id']}: {f['error_code']}" for f in failures[:12]))

    out: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary_date": day.isoformat(),
        "runtime_root": str(root),
        "per_avenue": {
            "Avenue A": {
                "Gate A": _stats(gate_a),
                "Gate B": {"status": gate_b_status, "trades_today": len(gate_b), "stats": _stats(gate_b)},
            }
        },
        "trades": gate_a + gate_b + other,
        "system_summary": {
            "total_pnl_today_usd": round(total_pnl, 6),
            "total_fees_today_usd": round(total_fees, 6),
            "total_trades": ntr,
            "success_rate": round((succ / ntr), 4) if ntr else 0.0,
            "failures": failures,
            "capital_snapshot": {
                "quote_capital_truth": qct.get("summary") or qct.get("classification") or qct,
                "deployable_capital": dcr.get("conservative_deployable_capital") or dcr.get("policy_vs_capital_one_liner"),
            },
            "reserve_snapshot": res.get("reserve_breakdown") or res,
            "ai_learning_summary": ai_learning_summary,
            "top_lesson": top_lesson,
            "top_research_question": top_research_q,
            "recommended_next_operating_mode": next_mode_hint,
        },
        "plain_english": "\n".join(plain_parts),
        "federation_meta": {k: meta[k] for k in ("merged_trade_count", "warnings") if k in meta},
    }
    return out


def write_daily_trade_snapshot(*, runtime_root: Optional[Path] = None, day: Optional[date] = None) -> Dict[str, Any]:
    from trading_ai.storage.storage_adapter import LocalStorageAdapter

    root = Path(runtime_root or os.environ.get("EZRAS_RUNTIME_ROOT") or ezras_runtime_root()).resolve()
    adapter = LocalStorageAdapter(runtime_root=root)
    payload = build_daily_trading_summary(runtime_root=root, day=day)
    adapter.write_text("data/reports/daily_trading_summary.txt", payload.get("plain_english") or "")
    adapter.write_json("data/reports/daily_trading_summary.json", payload)
    return payload


def write_system_go_live_confirmation(
    *,
    runtime_root: Optional[Path] = None,
    readiness: Optional[Dict[str, Any]] = None,
) -> Path:
    """Write ``data/control/system_go_live_confirmation.txt`` from readiness payload."""
    root = Path(runtime_root or os.environ.get("EZRAS_RUNTIME_ROOT") or ezras_runtime_root()).resolve()
    if readiness is None:
        from trading_ai.deployment.readiness_decision import compute_final_readiness

        readiness = compute_final_readiness(write_files=False)
    ready = bool(readiness.get("ready_for_first_20"))
    rsd = readiness.get("readiness_scope_disclosure")
    lim_lines: List[str] = []
    if isinstance(rsd, dict):
        for k, v in rsd.items():
            lim_lines.append(f"  - {k}: {v}")
    lines = [
        "SYSTEM GO-LIVE CONFIRMATION",
        "===========================",
        "",
        f"generated_at: {readiness.get('generated_at')}",
        f"runtime_root: {root}",
        "",
        f"validation_passed (readiness artifact): {bool(ready)}",
        f"ready_for_real_trading_first_20_scope: {'YES' if ready else 'NO'}",
        "",
        "critical_blockers:",
        "\n".join(f"  - {x}" for x in (readiness.get("critical_blockers") or [])) or "  (none)",
        "",
        "limitations / scope:",
        "\n".join(lim_lines) if lim_lines else "  (see data/deployment/final_readiness.json)",
        "",
    ]
    p = root / "data" / "control" / "system_go_live_confirmation.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines), encoding="utf-8")
    return p
