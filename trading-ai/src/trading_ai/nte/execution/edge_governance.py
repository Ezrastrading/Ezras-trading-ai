"""
Edge governance layer (production vs experimental vs blocked).

This sits in front of execution for Avenue A Gate A / Gate B and produces an explicit
contract per trade candidate:
- Which edge family (if any) was detected
- Whether the candidate is production / experimental / blocked
- Expected move/risk/confidence and fee-aware net edge estimate
- Block reason (if any)

No code presence is treated as proof: this module writes truth artifacts only from
observed inputs at decision time.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.storage.storage_adapter import LocalStorageAdapter


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class StrategyMode(str, Enum):
    PRODUCTION = "production"
    EXPERIMENTAL = "experimental"


class Lane(str, Enum):
    PRODUCTION = "production"
    EXPERIMENTAL = "experimental"
    BLOCKED = "blocked"


class EdgeType(str, Enum):
    # Gate A production edges
    A_PULLBACK_CONTINUATION = "A_PULLBACK_CONTINUATION"
    A_SPREAD_COMPRESSION = "A_SPREAD_COMPRESSION"
    A_VOL_BREAKOUT = "A_VOL_BREAKOUT"
    # Gate B production edges
    B_MEAN_REVERSION = "B_MEAN_REVERSION"
    B_MOMENTUM_BURST = "B_MOMENTUM_BURST"
    B_LIQUIDITY_SWEEP = "B_LIQUIDITY_SWEEP"


@dataclass(frozen=True)
class EdgeSignal:
    edge_type: EdgeType
    detected: bool
    edge_confidence: float  # 0..1
    expected_move_bps: float
    expected_risk_bps: float
    risk_level: str
    reason: str


@dataclass(frozen=True)
class StrategySpec:
    strategy_id: str
    gate_id: str
    strategy_mode: StrategyMode
    enabled: bool
    max_size_multiplier: float
    required_confidence: float
    required_net_edge_bps: float
    cooldown_sec: float
    daily_loss_cap_usd: float
    max_open_positions: int
    default_expected_move_bps: Optional[float] = None
    default_expected_risk_bps: Optional[float] = None
    notes: str = ""
    promotion_status: str = "production"  # "production" | "experimental" | "disabled" | "archived"


def default_strategy_registry() -> Dict[str, StrategySpec]:
    # Production strategies = the primary edge set.
    base: List[StrategySpec] = [
        StrategySpec(
            strategy_id=EdgeType.A_PULLBACK_CONTINUATION.value,
            gate_id="gate_a",
            strategy_mode=StrategyMode.PRODUCTION,
            enabled=True,
            max_size_multiplier=1.0,
            required_confidence=0.62,
            required_net_edge_bps=float(os.environ.get("EZRAS_PROD_MIN_NET_EDGE_BPS") or 2.0),
            cooldown_sec=0.0,
            daily_loss_cap_usd=0.0,
            max_open_positions=1,
            notes="Gate A production edge: trend continuation after pullback.",
        ),
        StrategySpec(
            strategy_id=EdgeType.A_SPREAD_COMPRESSION.value,
            gate_id="gate_a",
            strategy_mode=StrategyMode.PRODUCTION,
            enabled=True,
            max_size_multiplier=0.9,
            required_confidence=0.65,
            required_net_edge_bps=float(os.environ.get("EZRAS_PROD_MIN_NET_EDGE_BPS") or 2.0),
            cooldown_sec=0.0,
            daily_loss_cap_usd=0.0,
            max_open_positions=1,
            notes="Gate A production edge: tight spread + stable regime.",
        ),
        StrategySpec(
            strategy_id=EdgeType.A_VOL_BREAKOUT.value,
            gate_id="gate_a",
            strategy_mode=StrategyMode.PRODUCTION,
            enabled=True,
            max_size_multiplier=0.85,
            required_confidence=0.68,
            required_net_edge_bps=float(os.environ.get("EZRAS_PROD_MIN_NET_EDGE_BPS") or 3.0),
            cooldown_sec=0.0,
            daily_loss_cap_usd=0.0,
            max_open_positions=1,
            notes="Gate A production edge: volatility breakout.",
        ),
        StrategySpec(
            strategy_id=EdgeType.B_MEAN_REVERSION.value,
            gate_id="gate_b",
            strategy_mode=StrategyMode.PRODUCTION,
            enabled=True,
            max_size_multiplier=1.0,
            required_confidence=0.60,
            required_net_edge_bps=float(os.environ.get("EZRAS_PROD_MIN_NET_EDGE_BPS") or 3.0),
            cooldown_sec=0.0,
            daily_loss_cap_usd=0.0,
            max_open_positions=1,
            notes="Gate B production edge: mean reversion after overshoot.",
        ),
        StrategySpec(
            strategy_id=EdgeType.B_MOMENTUM_BURST.value,
            gate_id="gate_b",
            strategy_mode=StrategyMode.PRODUCTION,
            enabled=True,
            max_size_multiplier=1.0,
            required_confidence=0.66,
            required_net_edge_bps=float(os.environ.get("EZRAS_PROD_MIN_NET_EDGE_BPS") or 4.0),
            cooldown_sec=0.0,
            daily_loss_cap_usd=0.0,
            max_open_positions=1,
            notes="Gate B production edge: momentum burst with volume support.",
        ),
        StrategySpec(
            strategy_id=EdgeType.B_LIQUIDITY_SWEEP.value,
            gate_id="gate_b",
            strategy_mode=StrategyMode.PRODUCTION,
            enabled=True,
            max_size_multiplier=0.9,
            required_confidence=0.70,
            required_net_edge_bps=float(os.environ.get("EZRAS_PROD_MIN_NET_EDGE_BPS") or 4.0),
            cooldown_sec=0.0,
            daily_loss_cap_usd=0.0,
            max_open_positions=1,
            notes="Gate B production edge: sweep / fast rejection in high liquidity names.",
        ),
    ]
    return {s.strategy_id: s for s in base}


def load_strategy_registry(*, runtime_root: Path) -> Dict[str, StrategySpec]:
    """
    Load canonical registry from runtime control plane when present.
    File: data/control/edge_strategy_registry.json
    """
    root = Path(runtime_root).resolve()
    reg = default_strategy_registry()
    p = root / "data" / "control" / "edge_strategy_registry.json"
    if not p.is_file():
        return reg
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return reg
    if not isinstance(raw, dict):
        return reg
    rows = raw.get("strategies")
    if not isinstance(rows, list):
        return reg
    for r in rows:
        if not isinstance(r, dict):
            continue
        sid = str(r.get("strategy_id") or "").strip()
        if not sid:
            continue
        try:
            spec = StrategySpec(
                strategy_id=sid,
                gate_id=str(r.get("gate_id") or "gate_a"),
                strategy_mode=StrategyMode(str(r.get("strategy_mode") or "experimental")),
                enabled=bool(r.get("enabled") is True),
                max_size_multiplier=float(r.get("max_size_multiplier") or 0.5),
                required_confidence=float(r.get("required_confidence") or 0.7),
                required_net_edge_bps=float(r.get("required_net_edge_bps") or 2.0),
                cooldown_sec=float(r.get("cooldown_sec") or 120.0),
                daily_loss_cap_usd=float(r.get("daily_loss_cap_usd") or 5.0),
                max_open_positions=int(r.get("max_open_positions") or 1),
                default_expected_move_bps=float(r.get("default_expected_move_bps"))
                if r.get("default_expected_move_bps") is not None
                else None,
                default_expected_risk_bps=float(r.get("default_expected_risk_bps"))
                if r.get("default_expected_risk_bps") is not None
                else None,
                notes=str(r.get("notes") or ""),
                promotion_status=str(r.get("promotion_status") or "experimental"),
            )
        except Exception:
            continue
        reg[sid] = spec
    return reg


def _clamp01(x: float) -> float:
    if not math.isfinite(x):
        return 0.0
    return max(0.0, min(1.0, float(x)))


def detect_gate_a_edges(*, closes: List[float], feat: Dict[str, Any]) -> List[EdgeSignal]:
    """
    Deterministic Gate A detectors using:
    - recent close path
    - spread_pct
    - z_score
    - regime
    - quote_volume_24h
    """
    mid = float(feat.get("mid") or 0.0)
    spread_pct = float(feat.get("spread_pct") or 1.0)
    spread_bps = float(spread_pct) * 10_000.0
    z = float(feat.get("z_score") or 0.0)
    regime = str(feat.get("regime") or "unknown")
    vol24 = float(feat.get("quote_volume_24h") or 0.0)
    n = len(closes)
    last = float(closes[-1]) if n >= 1 else mid
    prev = float(closes[-2]) if n >= 2 else last
    ret_1 = (last / prev - 1.0) if prev > 0 else 0.0
    ret_1_bps = ret_1 * 10_000.0
    # crude short vol proxy: abs return over last 5 closes average
    abs_rets: List[float] = []
    if n >= 6:
        for i in range(n - 5, n):
            p0 = float(closes[i - 1])
            p1 = float(closes[i])
            if p0 > 0:
                abs_rets.append(abs(p1 / p0 - 1.0))
    short_vol_bps = (sum(abs_rets) / len(abs_rets) * 10_000.0) if abs_rets else abs(ret_1_bps)

    out: List[EdgeSignal] = []

    # 1) Pullback continuation: trend_up + negative z (pullback) + rebound last bar + tight spread
    det_pc = regime == "trend_up" and z < -0.6 and ret_1 > 0 and spread_bps <= 25.0
    conf_pc = _clamp01(0.55 + min(0.35, abs(z) / 3.0) + (0.10 if spread_bps <= 15 else 0.0))
    exp_move_pc = max(18.0, min(80.0, short_vol_bps * 1.8))
    risk_pc = max(12.0, min(60.0, short_vol_bps * 1.2))
    out.append(
        EdgeSignal(
            edge_type=EdgeType.A_PULLBACK_CONTINUATION,
            detected=bool(det_pc),
            edge_confidence=float(conf_pc),
            expected_move_bps=float(exp_move_pc),
            expected_risk_bps=float(risk_pc),
            risk_level="medium",
            reason=f"trend_up={regime=='trend_up'} z={z:.2f} ret1_bps={ret_1_bps:.1f} spread_bps={spread_bps:.1f}",
        )
    )

    # 2) Spread compression: very tight spread + stable short vol + adequate volume
    det_sc = spread_bps <= 12.0 and short_vol_bps <= 20.0 and vol24 >= 250_000
    conf_sc = _clamp01(0.50 + (0.25 if spread_bps <= 8 else 0.0) + (0.15 if short_vol_bps <= 12 else 0.0))
    exp_move_sc = max(14.0, min(55.0, 10.0 + short_vol_bps * 2.2))
    risk_sc = max(10.0, min(45.0, 8.0 + short_vol_bps * 1.6))
    out.append(
        EdgeSignal(
            edge_type=EdgeType.A_SPREAD_COMPRESSION,
            detected=bool(det_sc),
            edge_confidence=float(conf_sc),
            expected_move_bps=float(exp_move_sc),
            expected_risk_bps=float(risk_sc),
            risk_level="low",
            reason=f"spread_bps={spread_bps:.1f} short_vol_bps={short_vol_bps:.1f} vol24={vol24:.0f}",
        )
    )

    # 3) Vol breakout: range regime + big z + expanding short vol + not-too-wide spread
    det_vb = regime in ("range", "unknown") and abs(z) >= 1.6 and short_vol_bps >= 22.0 and spread_bps <= 25.0
    conf_vb = _clamp01(0.50 + min(0.35, abs(z) / 3.0) + (0.10 if short_vol_bps >= 35 else 0.0))
    exp_move_vb = max(25.0, min(140.0, short_vol_bps * 2.6))
    risk_vb = max(18.0, min(110.0, short_vol_bps * 1.8))
    out.append(
        EdgeSignal(
            edge_type=EdgeType.A_VOL_BREAKOUT,
            detected=bool(det_vb),
            edge_confidence=float(conf_vb),
            expected_move_bps=float(exp_move_vb),
            expected_risk_bps=float(risk_vb),
            risk_level="high",
            reason=f"regime={regime} z={z:.2f} short_vol_bps={short_vol_bps:.1f} spread_bps={spread_bps:.1f}",
        )
    )

    return out


def detect_gate_b_edges(*, row: Dict[str, Any]) -> List[EdgeSignal]:
    """
    Deterministic Gate B detectors using row fields from the Gate B engine scan path:
    - move_pct
    - volume_surge_ratio
    - spread_bps
    - book_depth_usd
    - exhaustion_risk
    """
    mv = float(row.get("move_pct") or 0.0)
    mv_bps = mv * 10_000.0
    vol_surge = float(row.get("volume_surge_ratio") or 0.0)
    spread_bps = float(row.get("spread_bps") or 0.0)
    depth = float(row.get("book_depth_usd") or 0.0)
    ex = float(row.get("exhaustion_risk") or 0.0)

    out: List[EdgeSignal] = []

    # 1) Mean reversion: sharp move + high exhaustion risk + acceptable spread
    det_mr = abs(mv) >= 0.018 and ex >= 0.55 and spread_bps <= 35.0
    conf_mr = _clamp01(0.50 + min(0.30, abs(mv) / 0.05) + min(0.20, ex))
    exp_move_mr = max(35.0, min(180.0, abs(mv_bps) * 0.9))
    risk_mr = max(25.0, min(160.0, abs(mv_bps) * 0.9))
    out.append(
        EdgeSignal(
            edge_type=EdgeType.B_MEAN_REVERSION,
            detected=bool(det_mr),
            edge_confidence=float(conf_mr),
            expected_move_bps=float(exp_move_mr),
            expected_risk_bps=float(risk_mr),
            risk_level="high",
            reason=f"abs_move_bps={abs(mv_bps):.0f} exhaustion={ex:.2f} spread_bps={spread_bps:.1f}",
        )
    )

    # 2) Momentum burst: positive move + strong volume surge + tight spread
    det_mb = mv >= 0.020 and vol_surge >= 1.35 and spread_bps <= 30.0
    conf_mb = _clamp01(0.45 + min(0.35, mv / 0.06) + min(0.20, (vol_surge - 1.0) / 1.5))
    exp_move_mb = max(45.0, min(260.0, mv_bps * 1.1))
    risk_mb = max(30.0, min(220.0, mv_bps * 0.9))
    out.append(
        EdgeSignal(
            edge_type=EdgeType.B_MOMENTUM_BURST,
            detected=bool(det_mb),
            edge_confidence=float(conf_mb),
            expected_move_bps=float(exp_move_mb),
            expected_risk_bps=float(risk_mb),
            risk_level="high",
            reason=f"move_bps={mv_bps:.0f} vol_surge={vol_surge:.2f} spread_bps={spread_bps:.1f}",
        )
    )

    # 3) Liquidity sweep: high depth + sharp move + spread acceptable (fast stopability)
    det_ls = depth >= 50_000 and abs(mv) >= 0.015 and spread_bps <= 28.0
    conf_ls = _clamp01(0.48 + min(0.22, depth / 200_000.0) + min(0.30, abs(mv) / 0.06))
    exp_move_ls = max(40.0, min(220.0, abs(mv_bps) * 1.0))
    risk_ls = max(28.0, min(200.0, abs(mv_bps) * 0.9))
    out.append(
        EdgeSignal(
            edge_type=EdgeType.B_LIQUIDITY_SWEEP,
            detected=bool(det_ls),
            edge_confidence=float(conf_ls),
            expected_move_bps=float(exp_move_ls),
            expected_risk_bps=float(risk_ls),
            risk_level="medium",
            reason=f"depth={depth:.0f} abs_move_bps={abs(mv_bps):.0f} spread_bps={spread_bps:.1f}",
        )
    )

    return out


def edge_priority_for_gate(gate_id: str) -> List[EdgeType]:
    g = str(gate_id or "").strip().lower()
    if g == "gate_b":
        return [
            EdgeType.B_MEAN_REVERSION,
            EdgeType.B_MOMENTUM_BURST,
            EdgeType.B_LIQUIDITY_SWEEP,
        ]
    return [
        EdgeType.A_PULLBACK_CONTINUATION,
        EdgeType.A_SPREAD_COMPRESSION,
        EdgeType.A_VOL_BREAKOUT,
    ]


def choose_best_edge(
    *,
    gate_id: str,
    edges: List[EdgeSignal],
    min_confidence: float,
) -> Optional[EdgeSignal]:
    pri = edge_priority_for_gate(gate_id)
    by_type = {e.edge_type: e for e in edges if isinstance(e, EdgeSignal)}
    best: Optional[EdgeSignal] = None
    for et in pri:
        e = by_type.get(et)
        if not e or not e.detected:
            continue
        if float(e.edge_confidence) + 1e-12 < float(min_confidence):
            continue
        best = e
        break
    return best


def write_edge_governance_truth(runtime_root: Path, payload: Dict[str, Any]) -> None:
    root = Path(runtime_root).resolve()
    ad = LocalStorageAdapter(runtime_root=root)
    ad.write_json("data/control/edge_governance_truth.json", payload)
    ad.write_text("data/control/edge_governance_truth.txt", json.dumps(payload, indent=2, default=str) + "\n")


def write_edge_do_nothing_truth(runtime_root: Path, payload: Dict[str, Any]) -> None:
    root = Path(runtime_root).resolve()
    ad = LocalStorageAdapter(runtime_root=root)
    ad.write_json("data/control/edge_do_nothing_truth.json", payload)
    ad.write_text("data/control/edge_do_nothing_truth.txt", json.dumps(payload, indent=2, default=str) + "\n")


def decide_lane_and_strategy(
    *,
    runtime_root: Path,
    gate_id: str,
    candidate_product: str,
    candidate_strategy_id: str,
    # Candidate mode hint: if strategy id is EXP_* we treat as experimental candidate only.
    # Production requires production edge match.
    edges: List[EdgeSignal],
    estimated_fees_bps: float,
    estimated_slippage_bps: float,
    spread_bps: float,
    required_net_edge_bps_override: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Returns a governance contract dict (serializable).
    """
    root = Path(runtime_root).resolve()
    reg = load_strategy_registry(runtime_root=root)
    gate = str(gate_id).strip().lower()
    prod_min_conf = float(os.environ.get("EZRAS_PROD_REQUIRED_CONFIDENCE") or 0.62)

    # Production edge match (highest priority).
    chosen_prod = choose_best_edge(gate_id=gate, edges=edges, min_confidence=prod_min_conf)
    lane: Lane = Lane.BLOCKED
    strategy_mode: StrategyMode = StrategyMode.PRODUCTION
    edge_family = None
    edge_detected = False
    expected_move_bps = 0.0
    confidence = 0.0
    block_reason = ""

    # Derived net edge estimate in bps (expected_move - fees - slippage - spread).
    cost_bps = max(0.0, float(estimated_fees_bps) + float(estimated_slippage_bps) + float(spread_bps))

    if chosen_prod is not None:
        edge_family = chosen_prod.edge_type.value
        edge_detected = True
        expected_move_bps = float(chosen_prod.expected_move_bps)
        confidence = float(chosen_prod.edge_confidence)
        net_expected_edge_bps = expected_move_bps - cost_bps
        spec = reg.get(edge_family) or reg.get(chosen_prod.edge_type.value)
        req_net = float(required_net_edge_bps_override) if required_net_edge_bps_override is not None else float(
            (spec.required_net_edge_bps if spec else (os.environ.get("EZRAS_PROD_MIN_NET_EDGE_BPS") or 2.0))
        )
        if spec is None or (spec.enabled and spec.strategy_mode == StrategyMode.PRODUCTION):
            if net_expected_edge_bps <= req_net + 1e-9:
                lane = Lane.BLOCKED
                block_reason = "blocked_profit_floor"
            else:
                lane = Lane.PRODUCTION
                strategy_mode = StrategyMode.PRODUCTION
        else:
            lane = Lane.BLOCKED
            block_reason = "blocked_production_strategy_disabled"
    else:
        # Experimental lane: requires explicit registry entry enabled and positive net.
        cand = str(candidate_strategy_id or "").strip()
        spec = reg.get(cand)
        if spec and spec.enabled and spec.strategy_mode == StrategyMode.EXPERIMENTAL:
            lane = Lane.EXPERIMENTAL
            strategy_mode = StrategyMode.EXPERIMENTAL
            # Experimental does not require one of the production edges; but we still record
            # whether any edge detectors fired (informational).
            best_any = max(edges, key=lambda e: float(e.edge_confidence), default=None) if edges else None
            if best_any and best_any.detected:
                edge_family = best_any.edge_type.value
                edge_detected = True
                expected_move_bps = float(best_any.expected_move_bps)
                confidence = float(best_any.edge_confidence)
            elif spec.default_expected_move_bps is not None:
                # Experimental strategies must still be explicit: the expected move/risk must be
                # declared (registry) or derived (detector). No vague "maybe it works".
                expected_move_bps = float(spec.default_expected_move_bps)
                if spec.default_expected_risk_bps is not None:
                    # We do not currently return this in the contract, but it is used by callers for profit enforcement.
                    pass
                confidence = float(spec.required_confidence)
                edge_detected = False
                edge_family = "EXPERIMENTAL_REGISTRY_EXPECTANCY"
            net_expected_edge_bps = float(expected_move_bps) - cost_bps
            if net_expected_edge_bps <= float(spec.required_net_edge_bps) + 1e-9:
                lane = Lane.BLOCKED
                block_reason = "blocked_profit_floor"
            if confidence and confidence + 1e-12 < float(spec.required_confidence):
                lane = Lane.BLOCKED
                block_reason = "blocked_confidence_below_threshold"
        else:
            lane = Lane.BLOCKED
            block_reason = "blocked_no_edge_and_no_enabled_experimental_strategy"

    # recompute net expected edge from chosen expected_move (may be 0)
    net_expected_edge_bps = float(expected_move_bps) - cost_bps

    payload = {
        "truth_version": "edge_governance_contract_v1",
        "generated_at": _iso(),
        "runtime_root": str(root),
        "gate_id": gate,
        "candidate_product": str(candidate_product),
        "strategy_id": str(candidate_strategy_id),
        "strategy_mode": str(strategy_mode.value),
        "lane": str(lane.value),
        "edge_family": edge_family,
        "edge_detected_bool": bool(edge_detected),
        "expected_move_bps": float(expected_move_bps),
        "estimated_fees_bps": float(estimated_fees_bps),
        "estimated_slippage_bps": float(estimated_slippage_bps),
        "spread_bps": float(spread_bps),
        "net_expected_edge_bps": float(net_expected_edge_bps),
        "confidence": float(confidence),
        "approval_status": "APPROVED" if lane != Lane.BLOCKED else "BLOCKED",
        "block_reason_if_any": str(block_reason or "") if lane == Lane.BLOCKED else "",
        "edge_candidates": [asdict(e) for e in edges],
        "registry_note": "data/control/edge_strategy_registry.json overrides built-in defaults when present.",
    }
    write_edge_governance_truth(root, payload)
    if lane == Lane.BLOCKED:
        write_edge_do_nothing_truth(
            root,
            {
                "truth_version": "edge_do_nothing_truth_v1",
                "generated_at": _iso(),
                "runtime_root": str(root),
                "gate_id": gate,
                "candidate_product": str(candidate_product),
                "strategy_id": str(candidate_strategy_id),
                "reason": payload["block_reason_if_any"] or "no_valid_edge",
                "lane": payload["lane"],
            },
        )
    return payload

