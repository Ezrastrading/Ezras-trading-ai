"""
Live wiring: build OperatingSnapshot from databank + NTE state, evaluate adaptive OS, persist proof.

Proof file ``adaptive_live_proof.json`` is written on real paths only (Avenue A entry, live validation,
Gate B Kalshi when enabled). ``proof_source`` identifies the Python entrypoint.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.control.adaptive_operating_system import (
    evaluate_adaptive_operating_system,
    load_operating_mode_config_from_env,
    write_operator_operating_mode_txt,
)
from trading_ai.control.adaptive_scope import (
    consecutive_losses_from_pnls,
    default_production_pnl_only,
    expectancy_tail,
    filter_events_for_scope,
    load_trade_events_for_adaptive,
    pnl_series_from_events,
)
from trading_ai.control.operating_mode_types import OperatingMode, OperatingOutcome, OperatingSnapshot
from trading_ai.deployment.deployment_models import iso_now
from trading_ai.runtime_paths import ezras_runtime_root

logger = logging.getLogger(__name__)

_PROOF_SOURCE_EVAL = "trading_ai.control.live_adaptive_integration:run_live_adaptive_evaluation"
_PROOF_SOURCE_COINBASE_GATE = "trading_ai.control.live_adaptive_integration:coinbase_entry_adaptive_gate"


def adaptive_live_proof_path() -> Path:
    p = ezras_runtime_root() / "data" / "control" / "adaptive_live_proof.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _databank_health_flags() -> Tuple[int, int]:
    try:
        from trading_ai.nte.databank.databank_health import load_health

        h = load_health()
        st = str(h.get("status") or "")
        if st == "degraded":
            return 1, 0
    except Exception:
        pass
    return 0, 0


def build_live_operating_snapshot(
    *,
    consecutive_losses_override: Optional[int] = None,
    rolling_equity_high: Optional[float] = None,
    current_equity: Optional[float] = None,
    slippage_health: float = 0.85,
    liquidity_health: float = 0.85,
    execution_health: float = 0.85,
    market_regime: str = "neutral",
    market_chop_score: float = 0.35,
    anomaly_flags: Optional[List[str]] = None,
    evaluation_scope: str = "global",
    production_pnl_only: Optional[bool] = None,
) -> OperatingSnapshot:
    """
    Build :class:`OperatingSnapshot` for emergency brake + mode evaluation.

    ``evaluation_scope`` selects which gate's production PnL series feeds **last_n_trade_pnls**
    (the brake inputs). ``global`` = all gates' production-eligible rows (never mixed with
    validation strategy_ids when ``production_pnl_only`` is True).

    ``gate_a_expectancy_20`` / ``gate_b_expectancy_20`` are always computed from **separate**
    gate-filtered production series for diagnosis honesty.
    """
    prod = default_production_pnl_only() if production_pnl_only is None else bool(production_pnl_only)
    es = evaluation_scope if evaluation_scope in ("global", "gate_a", "gate_b") else "global"
    raw = load_trade_events_for_adaptive()
    pnls = pnl_series_from_events(
        filter_events_for_scope(raw, scope=es, production_only=prod), max_n=80
    )
    ga_pnls = pnl_series_from_events(
        filter_events_for_scope(raw, scope="gate_a", production_only=prod), max_n=80
    )
    gb_pnls = pnl_series_from_events(
        filter_events_for_scope(raw, scope="gate_b", production_only=prod), max_n=80
    )
    cl = (
        consecutive_losses_override
        if consecutive_losses_override is not None
        else consecutive_losses_from_pnls(pnls)
    )
    hi = float(rolling_equity_high or max(current_equity or 0.0, 1.0))
    cur = float(current_equity or hi)
    db_f, gov_extra = _databank_health_flags()
    meta = {
        "adaptive_evaluation_scope": es,
        "production_pnl_only": prod,
        "brake_inputs_scope": es,
        "gate_a_production_pnl_points": len(ga_pnls),
        "gate_b_production_pnl_points": len(gb_pnls),
        "scoped_pnl_points": len(pnls),
        "raw_trade_event_count": len(raw),
    }
    return OperatingSnapshot(
        consecutive_losses=cl,
        last_n_trade_pnls=pnls,
        rolling_equity_high=hi,
        current_equity=cur,
        slippage_health=float(slippage_health),
        liquidity_health=float(liquidity_health),
        execution_health=float(execution_health),
        anomaly_flags=list(anomaly_flags or []),
        reconciliation_failures_24h=0,
        databank_failures_24h=db_f,
        governance_blocks_24h=gov_extra,
        blocked_orders_streak=0,
        gate_a_expectancy_20=expectancy_tail(ga_pnls, 20),
        gate_b_expectancy_20=expectancy_tail(gb_pnls, 20),
        market_regime=str(market_regime),
        market_chop_score=float(market_chop_score),
        adaptive_scope_metadata=meta,
    )


def snapshot_inputs_summary(snap: OperatingSnapshot) -> Dict[str, Any]:
    d = asdict(snap)
    p = d.get("last_n_trade_pnls") or []
    if len(p) > 16:
        d["last_n_trade_pnls"] = p[-16:]
        d["last_n_trade_pnls_truncated"] = True
    return d


def _mode_change_reason_text(out: OperatingOutcome) -> str:
    if out.mode_change_reasons:
        return "; ".join(str(x) for x in out.mode_change_reasons[:12])
    rep = out.report or {}
    rs = rep.get("mode_change_reasons")
    if isinstance(rs, list) and rs:
        return "; ".join(str(x) for x in rs[:12])
    return str(rep.get("mode_change_summary") or "")


def adaptive_os_affected_decision(out: OperatingOutcome) -> bool:
    return bool(
        out.mode != out.prior_mode
        or not out.allow_new_trades
        or abs(float(out.size_multiplier_effective) - 1.0) > 1e-9
        or out.emergency_brake_triggered
    )


def build_adaptive_live_proof_payload(
    snap: OperatingSnapshot,
    out: OperatingOutcome,
    *,
    proof_context: Optional[Dict[str, Any]] = None,
    decision_extras: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Single authoritative payload for ``adaptive_live_proof.json``."""
    ctx = dict(proof_context or {})
    proof_source = str(ctx.pop("proof_source", None) or _PROOF_SOURCE_EVAL)
    extras = dict(decision_extras or {})
    mode_val = out.mode.value
    prior_val = out.prior_mode.value
    affected = adaptive_os_affected_decision(out)
    rep = out.report if isinstance(out.report, dict) else {}
    rga = rep.get("recommended_gate_allocations")
    payload: Dict[str, Any] = {
        "generated_at": iso_now(),
        "ts": time.time(),
        "route": ctx.get("route"),
        "entrypoint": ctx.get("entrypoint"),
        "venue": ctx.get("venue"),
        "gate": ctx.get("gate"),
        "product_id": ctx.get("product_id"),
        "trade_intent": ctx.get("trade_intent"),
        "current_operating_mode": mode_val,
        "prior_mode": prior_val,
        "mode": mode_val,
        "allow_new_trades": out.allow_new_trades,
        "size_multiplier": float(out.size_multiplier_effective),
        "size_multiplier_effective": float(out.size_multiplier_effective),
        "allocation_source": "adaptive_operating_system.report.recommended_gate_allocations",
        "recommended_gate_allocations": rga,
        "mode_change_reason": _mode_change_reason_text(out),
        "snapshot_inputs": snapshot_inputs_summary(snap),
        "adaptive_os_affected_decision": affected,
        "emergency_brake_triggered": out.emergency_brake_triggered,
        "report": out.report,
        "critical_alerts": out.critical_alerts,
        "proof_source": proof_source,
        "proof_kind": "real_runtime_path",
        "adaptive_scope_truth": snap.adaptive_scope_metadata or {},
        "persist_adaptive_state_applied": (out.report or {}).get("persisted_adaptive_state"),
        "adaptive_state_key": (out.report or {}).get("adaptive_state_key"),
    }
    if extras:
        payload.update(extras)
    return payload


def write_adaptive_live_proof_file(payload: Dict[str, Any]) -> Path:
    p = adaptive_live_proof_path()
    p.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    try:
        rep = payload.get("report")
        if isinstance(rep, dict):
            write_operator_operating_mode_txt(rep)
    except Exception as exc:
        logger.debug("operator mode txt: %s", exc)
    return p


def resolve_adaptive_state_key(
    proof_context: Optional[Dict[str, Any]],
    *,
    explicit: Optional[str],
    evaluation_scope: str,
) -> str:
    if explicit:
        return explicit
    g = str((proof_context or {}).get("gate") or "").strip().lower()
    if g == "gate_b":
        return "gate_b"
    if g == "gate_a":
        return "gate_a"
    if evaluation_scope in ("gate_a", "gate_b"):
        return evaluation_scope
    return "global"


def run_live_adaptive_evaluation(
    snap: OperatingSnapshot,
    *,
    write_proof: bool = True,
    proof_context: Optional[Dict[str, Any]] = None,
    persist_adaptive_state: bool = True,
    adaptive_state_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Evaluate adaptive OS; persist rich proof when ``write_proof``."""
    cfg = load_operating_mode_config_from_env()
    ctx = dict(proof_context or {})
    sk = resolve_adaptive_state_key(
        ctx,
        explicit=adaptive_state_key,
        evaluation_scope=str((snap.adaptive_scope_metadata or {}).get("adaptive_evaluation_scope") or "global"),
    )
    out = evaluate_adaptive_operating_system(
        snap, cfg=cfg, persist_state=persist_adaptive_state, state_key=sk
    )
    if "proof_source" not in ctx:
        ctx["proof_source"] = _PROOF_SOURCE_EVAL
    payload = build_adaptive_live_proof_payload(snap, out, proof_context=ctx)
    if write_proof:
        write_adaptive_live_proof_file(payload)
    return payload


def coinbase_entry_adaptive_gate(
    *,
    equity: float,
    rolling_equity_high: float,
    market_regime: str,
    market_chop_score: float,
    slippage_health: float,
    liquidity_health: float,
    proof_context: Optional[Dict[str, Any]] = None,
    product_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Avenue A entry-path check. Returns:
      - block_new_entries: bool
      - size_multiplier: float (apply to USD notionals)
      - mode: str
      - proof: dict (full proof payload last written)
    """
    snap = build_live_operating_snapshot(
        rolling_equity_high=rolling_equity_high,
        current_equity=equity,
        slippage_health=slippage_health,
        liquidity_health=liquidity_health,
        execution_health=min(slippage_health, liquidity_health),
        market_regime=market_regime,
        market_chop_score=market_chop_score,
        evaluation_scope="gate_a",
        production_pnl_only=default_production_pnl_only(),
    )
    ctx = {
        "entrypoint": "coinbase_entry_adaptive_gate",
        "route": "coinbase_nte_slow_tick",
        "venue": "coinbase",
        "gate": "gate_a",
        "trade_intent": "evaluate_new_entries",
        "proof_source": _PROOF_SOURCE_COINBASE_GATE,
        "product_id": product_id,
    }
    ctx.update(proof_context or {})
    out = evaluate_adaptive_operating_system(
        snap,
        cfg=load_operating_mode_config_from_env(),
        persist_state=True,
        state_key="gate_a",
    )
    mode = str(out.mode.value)
    mult = float(out.size_multiplier_effective)
    allow = bool(out.allow_new_trades)
    block = (not allow) or mode == OperatingMode.HALTED.value
    payload = build_adaptive_live_proof_payload(
        snap,
        out,
        proof_context=ctx,
        decision_extras={
            "decision_block_new_entries": block,
            "decision_would_allow_entries": allow and mode != OperatingMode.HALTED.value,
            "adaptive_os_affected_decision": adaptive_os_affected_decision(out) or block,
        },
    )
    write_adaptive_live_proof_file(payload)
    return {
        "block_new_entries": block,
        "size_multiplier": max(0.0, min(mult, 1.5)),
        "mode": mode,
        "proof": payload,
        "adaptive_os_evaluated": True,
    }
