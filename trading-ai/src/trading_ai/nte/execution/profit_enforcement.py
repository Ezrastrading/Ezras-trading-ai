"""
Strict profit enforcement gate (Avenue A / Gate A / Gate B).

This is a **hard** pre-trade economic viability check. It must run before any live
order intent is allowed, and it must write an evidence-first truth artifact.

Design goals:
- Deterministic decisions from explicit inputs (quote, spread, fee model, slippage buffers).
- Clear reason codes when blocked.
- Never marks green without a positive expected net after costs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.storage.storage_adapter import LocalStorageAdapter
from trading_ai.multi_avenue.scoped_paths import gate_control_dir


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bps_to_usd(bps: float, quote_usd: float) -> float:
    return float(quote_usd) * (float(bps) / 10_000.0)


@dataclass(frozen=True)
class ProfitEnforcementConfig:
    # Minimum expected net edge after all estimated costs (bps).
    min_expected_net_edge_bps: float = 2.0
    # Minimum expected net profit in USD at the target move.
    min_expected_net_pnl_usd: float = 0.05
    # Minimum reward:risk ratio from configured plan assumptions.
    min_reward_to_risk: float = 1.05
    # Slippage buffer (round-trip) in bps.
    slippage_buffer_bps: float = 8.0
    # Extra spread/crossing buffer (round-trip) in bps.
    spread_buffer_bps: float = 0.0


def evaluate_profit_enforcement(
    *,
    runtime_root: Path,
    trade_id: str,
    avenue_id: str,
    gate_id: str,
    product_id: str,
    quote_usd: float,
    # Spread in bps at entry decision time.
    spread_bps: float,
    # Fee model (round-trip) in bps: maker+taker or taker+taker depending on path.
    fee_bps_round_trip: float,
    # Expected gross move in bps (strategy target / minimum zone).
    expected_gross_move_bps: float,
    # Expected adverse move in bps for risk (stop distance / hard stop).
    expected_risk_bps: float,
    config: Optional[ProfitEnforcementConfig] = None,
    extra: Optional[Dict[str, Any]] = None,
    write_artifact: bool = True,
) -> Dict[str, Any]:
    """
    Returns a decision dict with:
    - allowed (bool)
    - reason_codes (list[str])
    - derived cost components (usd + bps)
    - expected_net_edge_* (bps/usd)
    - expected_reward_to_risk
    """
    root = Path(runtime_root).resolve()
    cfg = config or ProfitEnforcementConfig()

    q = float(quote_usd or 0.0)
    sp = max(0.0, float(spread_bps or 0.0))
    fee = max(0.0, float(fee_bps_round_trip or 0.0))
    slip = max(0.0, float(cfg.slippage_buffer_bps))
    sp_buf = max(0.0, float(cfg.spread_buffer_bps))

    expected_move = float(expected_gross_move_bps or 0.0)
    risk_bps = max(1e-9, float(expected_risk_bps or 0.0))

    # Total estimated round-trip cost (bps).
    total_cost_bps = sp + fee + slip + sp_buf
    required_gross_move_bps = total_cost_bps + float(cfg.min_expected_net_edge_bps)

    expected_net_edge_bps = expected_move - total_cost_bps
    expected_net_edge_usd = _bps_to_usd(expected_net_edge_bps, q)
    expected_cost_usd = _bps_to_usd(total_cost_bps, q)

    expected_reward_to_risk = expected_move / risk_bps if risk_bps > 0 else 0.0

    reason_codes: List[str] = []
    if q <= 0:
        reason_codes.append("blocked_expected_net_pnl_nonpositive")
    if expected_net_edge_usd <= 0:
        reason_codes.append("blocked_expected_net_pnl_nonpositive")
    if expected_net_edge_bps <= 0:
        reason_codes.append("blocked_fee_dominates_move")
    if expected_net_edge_bps < float(cfg.min_expected_net_edge_bps) - 1e-12:
        reason_codes.append("blocked_profit_floor")
    if expected_move <= total_cost_bps + 1e-9:
        reason_codes.append("blocked_spread_not_worth_it")
    if expected_net_edge_usd < float(cfg.min_expected_net_pnl_usd) - 1e-12:
        reason_codes.append("blocked_expected_net_pnl_nonpositive")
    if expected_reward_to_risk < float(cfg.min_reward_to_risk) - 1e-12:
        reason_codes.append("blocked_reward_risk_too_low")

    allowed = len(reason_codes) == 0
    if allowed:
        reason_codes = ["ok"]

    payload: Dict[str, Any] = {
        "truth_version": "profit_enforcement_truth_v1",
        "generated_at": _iso(),
        "runtime_root": str(root),
        "trade_id": str(trade_id),
        "avenue_id": str(avenue_id),
        "gate_id": str(gate_id),
        "product_id": str(product_id),
        "quote_usd": q,
        "inputs": {
            "spread_bps": sp,
            "fee_bps_round_trip": fee,
            "slippage_buffer_bps": slip,
            "spread_buffer_bps": sp_buf,
            "expected_gross_move_bps": expected_move,
            "expected_risk_bps": float(expected_risk_bps or 0.0),
        },
        "derived": {
            "total_cost_bps": float(total_cost_bps),
            "required_gross_move_bps": float(required_gross_move_bps),
            "expected_net_edge_bps": float(expected_net_edge_bps),
            "expected_net_edge_usd": float(expected_net_edge_usd),
            "expected_cost_usd": float(expected_cost_usd),
            "expected_reward_to_risk": float(expected_reward_to_risk),
        },
        "thresholds": {
            "min_expected_net_edge_bps": float(cfg.min_expected_net_edge_bps),
            "min_expected_net_pnl_usd": float(cfg.min_expected_net_pnl_usd),
            "min_reward_to_risk": float(cfg.min_reward_to_risk),
        },
        "allowed": bool(allowed),
        "reason_codes": reason_codes,
        "extra": extra or {},
        "honesty": (
            "allowed=true only means expected net is positive under estimated costs at decision time; "
            "it does not guarantee realized profitability."
        ),
    }

    if write_artifact:
        ad = LocalStorageAdapter(runtime_root=root)
        ad.write_json("data/control/profit_enforcement_truth.json", payload)
        ad.write_text("data/control/profit_enforcement_truth.txt", json.dumps(payload, indent=2, default=str) + "\n")

    return payload


def evaluate_universal_profit_enforcement(
    *,
    runtime_root: Path,
    avenue_id: str,
    gate_id: str,
    strategy_id: str,
    symbol: str,
    spread: float,
    fees: float,
    slippage_buffer: float,
    expected_move_bps: float,
    expected_risk_bps: float,
    quote_size: float,
    config: Optional[ProfitEnforcementConfig] = None,
    write_artifact: bool = True,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Universal profit enforcement wrapper.

    Required output keys:
    - allow_trade
    - reason_code
    - expected_net_pnl
    - expected_net_edge_bps
    - fee_dominant
    - spread_dominant
    - reward_risk_ok

    Contract:
    - Fail-closed on invalid inputs / exceptions.
    - Writes scoped truth artifacts under ``data/control/avenues/<avenue_id>/gates/<gate_id>/...``.
    - Also writes legacy flat artifacts for backward compatibility.
    """
    root = Path(runtime_root).resolve()
    aid = str(avenue_id).strip()
    gid = str(gate_id).strip()
    sid = str(strategy_id or "").strip()
    sym = str(symbol or "").strip()
    cfg = config or ProfitEnforcementConfig()

    try:
        dec = evaluate_profit_enforcement(
            runtime_root=root,
            trade_id=f"profit_enforcement_{aid}_{gid}_{sid}_{sym}".strip("_")[:180],
            avenue_id=aid,
            gate_id=gid,
            product_id=sym,
            quote_usd=float(quote_size or 0.0),
            spread_bps=float(spread or 0.0),
            fee_bps_round_trip=float(fees or 0.0),
            expected_gross_move_bps=float(expected_move_bps or 0.0),
            expected_risk_bps=float(expected_risk_bps or 0.0),
            config=ProfitEnforcementConfig(
                min_expected_net_edge_bps=cfg.min_expected_net_edge_bps,
                min_expected_net_pnl_usd=cfg.min_expected_net_pnl_usd,
                min_reward_to_risk=cfg.min_reward_to_risk,
                slippage_buffer_bps=float(slippage_buffer if slippage_buffer is not None else cfg.slippage_buffer_bps),
                spread_buffer_bps=cfg.spread_buffer_bps,
            ),
            extra={**(extra or {}), "strategy_id": sid, "symbol": sym, "universal_wrapper": True},
            write_artifact=write_artifact,
        )
        allowed = bool(dec.get("allowed") is True)
        codes = dec.get("reason_codes") or []
        reason_code = "ok" if allowed else (str(codes[0]) if isinstance(codes, list) and codes else "blocked_profit_floor")
        derived = dec.get("derived") or {}

        spread_bps = float(dec.get("inputs", {}).get("spread_bps") or 0.0)
        fee_bps = float(dec.get("inputs", {}).get("fee_bps_round_trip") or 0.0)
        slip_bps = float(dec.get("inputs", {}).get("slippage_buffer_bps") or 0.0)
        sp_buf_bps = float(dec.get("inputs", {}).get("spread_buffer_bps") or 0.0)
        total_cost_bps = float(derived.get("total_cost_bps") or (spread_bps + fee_bps + slip_bps + sp_buf_bps))
        move_bps = float(dec.get("inputs", {}).get("expected_gross_move_bps") or 0.0)

        fee_dominant = fee_bps >= max(0.0, total_cost_bps) * 0.55 and fee_bps >= max(1e-9, spread_bps)
        spread_dominant = spread_bps >= max(0.0, total_cost_bps) * 0.55 and spread_bps >= max(1e-9, fee_bps)
        reward_risk_ok = float(derived.get("expected_reward_to_risk") or 0.0) >= float(cfg.min_reward_to_risk) - 1e-12

        out: Dict[str, Any] = {
            "truth_version": "universal_profit_enforcement_v1",
            "generated_at": _iso(),
            "runtime_root": str(root),
            "avenue_id": aid,
            "gate_id": gid,
            "strategy_id": sid,
            "symbol": sym,
            "allow_trade": allowed,
            "reason_code": reason_code,
            "expected_net_pnl": float(derived.get("expected_net_edge_usd") or 0.0),
            "expected_net_edge_bps": float(derived.get("expected_net_edge_bps") or 0.0),
            "fee_dominant": bool(fee_dominant),
            "spread_dominant": bool(spread_dominant),
            "reward_risk_ok": bool(reward_risk_ok),
            "inputs": {
                "spread_bps": float(spread_bps),
                "fees_bps_round_trip": float(fee_bps),
                "slippage_buffer_bps": float(slip_bps),
                "spread_buffer_bps": float(sp_buf_bps),
                "expected_move_bps": float(move_bps),
                "expected_risk_bps": float(dec.get("inputs", {}).get("expected_risk_bps") or 0.0),
                "quote_size_usd": float(dec.get("quote_usd") or 0.0),
            },
            "derived": {
                "total_cost_bps": float(total_cost_bps),
                "expected_cost_usd": float(derived.get("expected_cost_usd") or 0.0),
                "expected_reward_to_risk": float(derived.get("expected_reward_to_risk") or 0.0),
            },
            "raw_profit_enforcement_truth": dec,
            "honesty": "Fail-closed universal wrapper over strict profit enforcement truth.",
        }
    except Exception as exc:
        out = {
            "truth_version": "universal_profit_enforcement_v1",
            "generated_at": _iso(),
            "runtime_root": str(root),
            "avenue_id": aid,
            "gate_id": gid,
            "strategy_id": sid,
            "symbol": sym,
            "allow_trade": False,
            "reason_code": "blocked_profit_enforcement_exception",
            "expected_net_pnl": 0.0,
            "expected_net_edge_bps": 0.0,
            "fee_dominant": False,
            "spread_dominant": False,
            "reward_risk_ok": False,
            "exception_type": type(exc).__name__,
            "honesty": "Exception occurred; decision forced to block (fail-closed).",
        }

    if write_artifact:
        scoped = gate_control_dir(aid, gid, runtime_root=root)
        p_json = scoped / "profit_enforcement_truth.json"
        p_txt = scoped / "profit_enforcement_truth.txt"
        p_json.write_text(json.dumps(out, indent=2, default=str) + "\n", encoding="utf-8")
        p_txt.write_text(json.dumps(out, indent=2, default=str) + "\n", encoding="utf-8")

    return out


def profit_enforcement_allows_or_reason(decision: Dict[str, Any]) -> Tuple[bool, str]:
    if not isinstance(decision, dict):
        return False, "blocked_profit_enforcement_invalid_decision"
    if decision.get("allowed") is True:
        return True, "ok"
    codes = decision.get("reason_codes") or []
    if isinstance(codes, list) and codes:
        return False, str(codes[0])
    return False, "blocked_profit_floor"

