"""Doctrine gate, execution pause, and versioned system doctrine (integrity + alignment)."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Mapping, Optional

logger = logging.getLogger(__name__)

MARGIN_DOCTRINE: Dict[str, Any] = {
    "never_borrow_for_low_confidence": True,
    "never_borrow_during_drawdown": True,
    "max_simultaneous_margin_positions": 1,
    "margin_requires_tier_a_or_b": True,
    "phase_1_max_margin_pct": 0.20,
    "phase_3_plus_max_margin_pct": 0.10,
    "phase_3_plus_high_conf_only": True,
}


@dataclass
class DoctrineContext:
    """Inputs required to evaluate whether a trade may proceed."""

    source: str
    mandate_compounding_paused: bool = False
    mandate_gaps_paused: bool = False
    execution_paused: bool = False
    edge_after_fees: float = 0.0
    min_edge_for_phase: float = 0.0
    anti_forced_trade: bool = True
    cluster_paused: bool = False
    tags: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DoctrineResult:
    ok: bool
    reason: str
    audit: Dict[str, Any] = field(default_factory=dict)


def is_execution_paused() -> bool:
    """
    Pause only when ``execution_control.json`` sets ``manual_pause``, or when
    persisted capital drawdown vs ``peak_capital`` exceeds 40%.

    Default: not paused. Does not consult in-memory ``MANDATE`` alone — always
    re-reads persisted state (``load_execution_control`` / ``load_capital``).
    """
    try:
        from trading_ai.shark.state_store import load_execution_control

        state = load_execution_control()
        if bool(state.get("manual_pause")):
            return True
    except Exception:
        pass
    try:
        from trading_ai.shark.state_store import load_capital

        cap = load_capital()
        if cap.peak_capital > 0:
            drawdown = (cap.peak_capital - cap.current_capital) / cap.peak_capital
            if drawdown > 0.40:
                logger.warning("Execution paused: drawdown=%.1f%%", drawdown * 100.0)
                return True
    except Exception:
        pass
    return False


def check_doctrine_gate(ctx: DoctrineContext) -> DoctrineResult:
    """
    Hard gate. No monthly targets, idle timers, or clocks may force a trade.
    Drawdown >25% is handled via sizing (execution chain), not this gate.
    Drawdown >40% pauses execution (see ``is_execution_paused``) or ``manual_pause`` in execution_control.json.
    """
    audit: Dict[str, Any] = {"source": ctx.source, "tags": dict(ctx.tags)}
    if is_execution_paused():
        return DoctrineResult(False, "doctrine: execution_paused", audit)
    is_compounding = ctx.source in ("shark_compounding",) or "compounding" in ctx.source
    is_gap = ctx.source in ("shark_gap",) or bool(ctx.tags.get("gap_exploit"))
    if is_compounding and ctx.mandate_compounding_paused:
        return DoctrineResult(False, "doctrine: mandate_compounding_paused", audit)
    if is_gap and ctx.mandate_gaps_paused:
        return DoctrineResult(False, "doctrine: mandate_gaps_paused", audit)
    if ctx.cluster_paused:
        return DoctrineResult(False, "doctrine: cluster_paused", audit)
    if ctx.anti_forced_trade and ctx.edge_after_fees < ctx.min_edge_for_phase:
        return DoctrineResult(
            False,
            f"doctrine: anti_forced_trade edge {ctx.edge_after_fees:.4f} < min {ctx.min_edge_for_phase:.4f}",
            audit,
        )
    return DoctrineResult(True, "doctrine: ok", audit)


def merge_audit(base: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    out.update(extra)
    return out


def audit_trail_append(entries: List[Dict[str, Any]], step: str, detail: Dict[str, Any]) -> None:
    entries.append({"step": step, **detail})


# ---------------------------------------------------------------------------
# Versioned canonical doctrine (operator / registry tooling)
# ---------------------------------------------------------------------------

DOCTRINE_VERSION = "2026.04.13"

CANONICAL_DOCTRINE_TEXT = """\
Ezras Trading AI — System Doctrine (non-negotiable)

1. Wholeness: All sub-agents, bots, and modules serve the whole system. No component may
   optimize locally in a way that harms total capital preservation, truth, portability, or
   cross-module consistency.

2. No hidden objectives: There shall be no conflicting or undisclosed goals. Optimization
   must be visible in operator-facing artifacts and auditable logs.

3. No silent drift: The system must not drift from the top-level mandate without explicit
   operator acknowledgment recorded in governance or audit trails.

4. Improvements strengthen: Permitted improvements increase consistency, expected real
   quality, strategy strength, justified market coverage, retention of knowledge, and
   operator clarity — not shortcuts that weaken controls.

5. Subordination: Subordinate agents may not redefine top-level goals. Conflicts must
   escalate upward. Failures, uncertainty, and drift must not be concealed.

6. Operator ownership: The operator retains final authority. The system remains
   deterministic, inspectable, portable, and suitable for third-party audit where
   artifacts and logs are complete.
"""

EXPECTED_DOCTRINE_SHA256 = "f233e2af0cf894b5bdec877c32a3b300a1eff4532a389e6869b76c0f87ce0b9a"


def compute_doctrine_sha256() -> str:
    return hashlib.sha256(CANONICAL_DOCTRINE_TEXT.encode("utf-8")).hexdigest()


VerdictLiteral = Literal["ALIGNED", "PARTIALLY_ALIGNED", "DRIFTING", "DOCTRINE_VIOLATION", "HALT"]
SeverityLiteral = Literal["INFO", "WARNING", "CRITICAL", "HALT"]


@dataclass(frozen=True)
class DoctrineVerdict:
    verdict: VerdictLiteral
    rule_triggered: str
    severity: SeverityLiteral
    evidence: Dict[str, Any]
    timestamp: datetime
    signed_by: str
    escalation_required: bool

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        return d


def doctrine_signature() -> str:
    return f"doctrine_v{DOCTRINE_VERSION}:{compute_doctrine_sha256()[:16]}"


def verify_doctrine_integrity() -> DoctrineVerdict:
    """Return HALT verdict if embedded hash does not match canonical text."""
    actual = compute_doctrine_sha256()
    ok = actual == EXPECTED_DOCTRINE_SHA256
    ts = datetime.now(timezone.utc)
    sig = doctrine_signature()
    if ok:
        return DoctrineVerdict(
            verdict="ALIGNED",
            rule_triggered="doctrine_integrity",
            severity="INFO",
            evidence={"expected_sha256": EXPECTED_DOCTRINE_SHA256, "actual_sha256": actual},
            timestamp=ts,
            signed_by=sig,
            escalation_required=False,
        )
    return DoctrineVerdict(
        verdict="HALT",
        rule_triggered="doctrine_hash_mismatch",
        severity="HALT",
        evidence={
            "expected_sha256": EXPECTED_DOCTRINE_SHA256,
            "actual_sha256": actual,
            "hint": "Update EXPECTED_DOCTRINE_SHA256 or restore CANONICAL_DOCTRINE_TEXT",
        },
        timestamp=ts,
        signed_by=sig,
        escalation_required=True,
    )


def evaluate_doctrine_alignment(
    *,
    change_type: str,
    payload: Optional[Mapping[str, Any]] = None,
    context: Optional[Mapping[str, Any]] = None,
) -> DoctrineVerdict:
    """Evaluate whether a proposed change or artifact is aligned with system doctrine."""
    from trading_ai.governance.doctrine_evaluator import evaluate_doctrine_scorecard

    integrity = verify_doctrine_integrity()
    if integrity.verdict == "HALT":
        return integrity

    ts = datetime.now(timezone.utc)
    sig = doctrine_signature()
    scorecard = evaluate_doctrine_scorecard(
        change_type=change_type,
        payload=payload,
        context=context,
    )
    agg = scorecard["verdict"]
    sev: SeverityLiteral
    if agg == "DOCTRINE_VIOLATION":
        sev = "CRITICAL"
    elif agg == "DRIFTING":
        sev = "WARNING"
    elif agg == "PARTIALLY_ALIGNED":
        sev = "INFO"
    else:
        sev = "INFO"

    verdict_lit: VerdictLiteral
    if agg in ("ALIGNED", "PARTIALLY_ALIGNED", "DRIFTING", "DOCTRINE_VIOLATION"):
        verdict_lit = agg  # type: ignore[assignment]
    else:
        verdict_lit = "PARTIALLY_ALIGNED"

    return DoctrineVerdict(
        verdict=verdict_lit,
        rule_triggered="doctrine_rule_table",
        severity=sev,
        evidence={
            "change_type": change_type,
            "scorecard": scorecard,
        },
        timestamp=ts,
        signed_by=sig,
        escalation_required=agg in ("DOCTRINE_VIOLATION", "DRIFTING"),
    )


def summarize_doctrine_for_export() -> Dict[str, Any]:
    return {
        "doctrine_version": DOCTRINE_VERSION,
        "sha256": compute_doctrine_sha256(),
        "expected_sha256": EXPECTED_DOCTRINE_SHA256,
        "integrity_ok": compute_doctrine_sha256() == EXPECTED_DOCTRINE_SHA256,
        "text": CANONICAL_DOCTRINE_TEXT,
    }
