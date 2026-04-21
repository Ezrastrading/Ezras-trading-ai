"""
Adaptive operating system — modes, emergency brake, recovery ladder, confidence scaling.

No martingale; no revenge sizing; governance/reconciliation hooks are inputs, not bypassed.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.control.emergency_brake import BrakeEvaluation, evaluate_emergency_brake, mode_size_multiplier
from trading_ai.control.market_awareness import combine_pnl_and_market_for_scale, evaluate_market_quality_for_scaling
from trading_ai.control.mode_diagnosis import build_diagnosis_artifact
from trading_ai.control.operating_mode_types import (
    MODE_ORDER,
    OperatingMode,
    OperatingModeConfig,
    OperatingOutcome,
    OperatingSnapshot,
    mode_index,
)
from trading_ai.control.adaptive_scope import (
    diagnosis_artifact_path_for_key,
    operating_mode_state_path_for_key,
    operating_mode_transitions_path_for_key,
)
from trading_ai.runtime_paths import ezras_runtime_root


def load_operating_mode_config_from_env() -> OperatingModeConfig:
    import os

    c = OperatingModeConfig()
    for name in dir(c):
        if name.startswith("_"):
            continue
        env = f"AOS_{name.upper()}"
        raw = (os.environ.get(env) or "").strip()
        if not raw:
            continue
        v = getattr(c, name)
        try:
            if isinstance(v, bool):
                setattr(c, name, raw.lower() in ("1", "true", "yes"))
            elif isinstance(v, int):
                setattr(c, name, int(raw))
            else:
                setattr(c, name, float(raw))
        except ValueError:
            pass
    return c


def operating_mode_state_path() -> Path:
    """Global (legacy) path — prefer :func:`operating_mode_state_path_for_key`."""
    return operating_mode_state_path_for_key("global")


def operating_mode_transitions_path() -> Path:
    return operating_mode_transitions_path_for_key("global")


def diagnosis_artifact_path() -> Path:
    return diagnosis_artifact_path_for_key("global")


@dataclass
class PersistedOperatingState:
    mode: str = "normal"
    prior_mode: str = "normal"
    last_change_ts: float = 0.0
    last_change_reasons: List[str] = field(default_factory=list)
    cycles_since_mode_change: int = 0
    positive_expectancy_streak: int = 0
    halt_entry_ts: float = 0.0


def load_persisted_state(state_key: str = "global") -> PersistedOperatingState:
    p = operating_mode_state_path_for_key(state_key)
    if not p.is_file():
        return PersistedOperatingState()
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return PersistedOperatingState(
            mode=str(d.get("mode") or "normal"),
            prior_mode=str(d.get("prior_mode") or "normal"),
            last_change_ts=float(d.get("last_change_ts") or 0.0),
            last_change_reasons=list(d.get("last_change_reasons") or []),
            cycles_since_mode_change=int(d.get("cycles_since_mode_change") or d.get("trades_since_mode_change") or 0),
            positive_expectancy_streak=int(d.get("positive_expectancy_streak") or 0),
            halt_entry_ts=float(d.get("halt_entry_ts") or 0.0),
        )
    except Exception:
        return PersistedOperatingState()


def save_persisted_state(st: PersistedOperatingState, *, state_key: str = "global") -> None:
    p = operating_mode_state_path_for_key(state_key)
    p.write_text(json.dumps(asdict(st), indent=2), encoding="utf-8")


def _append_transition(
    prior: OperatingMode,
    new: OperatingMode,
    reasons: List[str],
    *,
    brake: bool,
    state_key: str = "global",
) -> None:
    line = json.dumps(
        {
            "ts": time.time(),
            "prior_mode": prior.value,
            "new_mode": new.value,
            "reasons": reasons,
            "emergency_brake": brake,
            "adaptive_state_key": state_key,
        }
    )
    with operating_mode_transitions_path_for_key(state_key).open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _expectancy_last_n(pnls: List[float], n: int) -> float:
    if not pnls or n <= 0:
        return 0.0
    w = pnls[-n:]
    return sum(w) / len(w)


def _can_step_up(
    current: OperatingMode,
    snap: OperatingSnapshot,
    cfg: OperatingModeConfig,
    persisted: PersistedOperatingState,
    mq: Dict[str, Any],
) -> Tuple[bool, str]:
    """Recovery / confidence — at most one step per evaluation."""
    if mode_index(current) >= mode_index(OperatingMode.AGGRESSIVE_CONFIRMED):
        return False, "already_at_ceiling"
    nxt = MODE_ORDER[mode_index(current) + 1]

    if current == OperatingMode.HALTED:
        if time.time() - persisted.halt_entry_ts < cfg.recovery_cooldown_sec_after_halt:
            return False, "halt_cooldown_active"
        if snap.reconciliation_failures_24h > 0 or snap.databank_failures_24h > 0:
            return False, "structural_issues_unresolved"
        return True, "halt_to_defensive_ladder"

    if nxt in (OperatingMode.CONFIDENT, OperatingMode.AGGRESSIVE_CONFIRMED):
        if len(snap.last_n_trade_pnls) < cfg.min_sample_for_confident_mode and nxt == OperatingMode.CONFIDENT:
            return False, "insufficient_sample_for_confident"
        if len(snap.last_n_trade_pnls) < cfg.min_sample_for_aggressive_confirmed_mode and nxt == OperatingMode.AGGRESSIVE_CONFIRMED:
            return False, "insufficient_sample_for_aggressive_confirmed"
        ev = _expectancy_last_n(snap.last_n_trade_pnls, min(30, len(snap.last_n_trade_pnls)))
        if ev <= cfg.min_positive_expectancy_edge:
            return False, "expectancy_not_positive_enough"
        pnl_ok, why = combine_pnl_and_market_for_scale(
            pnl_evidence_strong=True,
            market_allows=bool(mq.get("market_quality_allows_aggressive_scale")),
        )
        if not pnl_ok and nxt == OperatingMode.AGGRESSIVE_CONFIRMED:
            return False, why

    if mode_index(nxt) > mode_index(OperatingMode.NORMAL) and not mq.get("market_quality_allows_aggressive_scale"):
        return False, "market_quality_blocks_step_up"

    if persisted.cycles_since_mode_change < 3 and current in (
        OperatingMode.DEFENSIVE,
        OperatingMode.CAUTIOUS,
    ):
        return False, "min_cycles_at_mode_before_step_up"

    return True, "recovery_ladder_ok"


def _more_conservative(a: OperatingMode, b: OperatingMode) -> OperatingMode:
    return a if mode_index(a) <= mode_index(b) else b


def evaluate_adaptive_operating_system(
    snap: OperatingSnapshot,
    *,
    cfg: Optional[OperatingModeConfig] = None,
    persisted: Optional[PersistedOperatingState] = None,
    persist_state: bool = True,
    state_key: str = "global",
) -> OperatingOutcome:
    """
    Single evaluation cycle: emergency brake → floor mode → optional recovery step-up
    (bounded) → confidence scaling only with market + sample gates.

    ``state_key`` isolates persisted mode / transitions (e.g. ``gate_b`` vs global).
    When ``persist_state`` is False, transitions and persisted mode are not written
    (informational eval — e.g. post micro-validation proof without poisoning global state).
    """
    cfg = cfg or load_operating_mode_config_from_env()
    persisted = persisted or load_persisted_state(state_key)
    try:
        current = OperatingMode(persisted.mode)
    except ValueError:
        current = OperatingMode.NORMAL

    brake = evaluate_emergency_brake(snap, cfg)
    mq = evaluate_market_quality_for_scaling(
        liquidity_health=snap.liquidity_health,
        slippage_health=snap.slippage_health,
        market_regime=snap.market_regime,
        market_chop_score=snap.market_chop_score,
    )

    new_mode = current
    reasons: List[str] = []

    if brake.triggered:
        new_mode = _more_conservative(brake.recommended_floor, current)
        reasons.extend(brake.reasons)
        if new_mode == OperatingMode.HALTED and current != OperatingMode.HALTED:
            persisted.halt_entry_ts = time.time()
    else:
        step_ok, step_why = _can_step_up(current, snap, cfg, persisted, mq)
        if step_ok and mode_index(current) < len(MODE_ORDER) - 1:
            candidate = MODE_ORDER[mode_index(current) + 1]
            if candidate == OperatingMode.AGGRESSIVE_CONFIRMED:
                ev = _expectancy_last_n(snap.last_n_trade_pnls, 40)
                if ev <= 0 or not mq.get("market_quality_allows_aggressive_scale"):
                    candidate = OperatingMode.CONFIDENT
                    reasons.append("aggressive_blocked_market_or_expectancy_using_confident_cap")
            if mode_index(candidate) > mode_index(current):
                new_mode = candidate
                reasons.append(f"recovery_or_scale:{step_why}")

    prior = current
    if new_mode != current:
        if persist_state:
            _append_transition(prior, new_mode, reasons, brake=brake.triggered, state_key=state_key)
        persisted.prior_mode = prior.value
        persisted.mode = new_mode.value
        persisted.last_change_ts = time.time()
        persisted.last_change_reasons = reasons[:24]
        persisted.cycles_since_mode_change = 0
    else:
        persisted.cycles_since_mode_change = persisted.cycles_since_mode_change + 1

    mult, allow = mode_size_multiplier(new_mode, cfg)
    if not mq.get("market_quality_allows_aggressive_scale") and new_mode in (
        OperatingMode.CONFIDENT,
        OperatingMode.AGGRESSIVE_CONFIRMED,
    ):
        mult = min(mult, 1.05)
        reasons.append("market_quality_capped_multiplier")

    diag = build_diagnosis_artifact(
        brake=brake,
        snap=snap,
        prior_mode=prior,
        new_mode=new_mode,
        gate_a_failing=snap.gate_a_expectancy_20 is not None and snap.gate_a_expectancy_20 < 0,
        gate_b_failing=snap.gate_b_expectancy_20 is not None and snap.gate_b_expectancy_20 < 0,
    )
    diagnosis_artifact_path_for_key(state_key).write_text(
        json.dumps(diag, indent=2, default=str), encoding="utf-8"
    )
    if persist_state:
        save_persisted_state(persisted, state_key=state_key)

    lr = 0.0
    if snap.last_n_trade_pnls:
        losses = sum(1 for p in snap.last_n_trade_pnls if p < 0)
        lr = losses / len(snap.last_n_trade_pnls)

    dd = 0.0
    if snap.rolling_equity_high > 0:
        dd = max(0.0, (snap.rolling_equity_high - snap.current_equity) / snap.rolling_equity_high)

    report = {
        "operating_mode": new_mode.value,
        "prior_mode": prior.value,
        "adaptive_state_key": state_key,
        "persisted_adaptive_state": persist_state,
        "mode_change_reasons": reasons,
        "loss_streak": snap.consecutive_losses,
        "rolling_loss_rate": lr,
        "rolling_expectancy_sample": _expectancy_last_n(snap.last_n_trade_pnls, 20),
        "drawdown_pct": dd,
        "slippage_health": snap.slippage_health,
        "liquidity_health": snap.liquidity_health,
        "anomaly_health": 1.0 if not snap.anomaly_flags else 0.0,
        "restart_ready": new_mode != OperatingMode.HALTED and snap.reconciliation_failures_24h == 0,
        "confidence_scaling_ready": mq.get("market_quality_allows_aggressive_scale")
        and _expectancy_last_n(snap.last_n_trade_pnls, 25) > 0,
        "recommended_gate_allocations": _alloc_hint(new_mode),
        "market_quality": mq,
        "emergency_brake_triggered": brake.triggered,
        "size_multiplier_effective": mult,
        "allow_new_trades": allow,
    }

    alerts: List[str] = []
    if brake.triggered and brake.severity >= 85:
        alerts.append("CRITICAL: emergency_brake — " + "; ".join(brake.reasons[:4]))

    return OperatingOutcome(
        mode=new_mode,
        prior_mode=prior,
        mode_change_reasons=reasons,
        emergency_brake_triggered=brake.triggered,
        size_multiplier_effective=mult,
        allow_new_trades=allow and new_mode != OperatingMode.HALTED,
        diagnosis=diag,
        report=report,
        critical_alerts=alerts,
    )


def _alloc_hint(mode: OperatingMode) -> Dict[str, float]:
    ga, gb = 0.5, 0.5
    if mode in (OperatingMode.HALTED, OperatingMode.DEFENSIVE):
        return {"gate_a": 0.65, "gate_b": 0.35}
    if mode in (OperatingMode.CAUTIOUS,):
        return {"gate_a": 0.55, "gate_b": 0.45}
    if mode in (OperatingMode.CONFIDENT, OperatingMode.AGGRESSIVE_CONFIRMED):
        return {"gate_a": 0.45, "gate_b": 0.55}
    return {"gate_a": ga, "gate_b": gb}


def write_operator_operating_mode_txt(report: Dict[str, Any], *, path: Optional[Path] = None) -> Path:
    p = path or (ezras_runtime_root() / "data" / "control" / "operating_mode_operator.txt")
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"operating_mode: {report.get('operating_mode')}",
        f"prior_mode: {report.get('prior_mode')}",
        f"mode_change_reasons: {report.get('mode_change_reasons')}",
        f"allow_new_trades: {report.get('allow_new_trades')}",
        f"size_multiplier_effective: {report.get('size_multiplier_effective')}",
        f"emergency_brake_triggered: {report.get('emergency_brake_triggered')}",
        f"restart_ready: {report.get('restart_ready')}",
        f"confidence_scaling_ready: {report.get('confidence_scaling_ready')}",
    ]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p
