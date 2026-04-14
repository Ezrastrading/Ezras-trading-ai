"""
Hierarchical alignment and structured cross-agent conflict detection.

Machine-checkable constraints only; does not grant autonomy or bypass operator controls.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from trading_ai.governance.system_doctrine import DoctrineVerdict, doctrine_signature, evaluate_doctrine_alignment


@dataclass(frozen=True)
class AgentSpec:
    """Legacy minimal spec (kept for compatibility)."""

    agent_id: str
    role: str
    objectives: Sequence[str]
    parent_doctrine_ack: bool = True
    provenance_operator_id: Optional[str] = None
    risk_hooks_acknowledged: bool = True


@dataclass
class AgentObjectiveProfile:
    """
    Structured agent definition for scalable conflict checks.

    ``forbidden_objectives`` lists phrases that must not appear in declared objectives.
    """

    agent_id: str
    scope: str
    declared_objectives: List[str]
    forbidden_objectives: List[str] = field(default_factory=list)
    inherits_doctrine: bool = True
    provenance_operator_id: Optional[str] = None
    risk_hooks_acknowledged: bool = True

    @staticmethod
    def from_legacy(spec: AgentSpec) -> "AgentObjectiveProfile":
        return AgentObjectiveProfile(
            agent_id=spec.agent_id,
            scope=spec.role,
            declared_objectives=list(spec.objectives),
            forbidden_objectives=[],
            inherits_doctrine=spec.parent_doctrine_ack,
            provenance_operator_id=spec.provenance_operator_id,
            risk_hooks_acknowledged=spec.risk_hooks_acknowledged,
        )


def default_known_profiles() -> List[AgentObjectiveProfile]:
    """Sample defaults for common Ezras roles (inspectable, not autonomous)."""
    return [
        AgentObjectiveProfile(
            agent_id="execution_router",
            scope="phase8_execution",
            declared_objectives=["route orders within gates and size limits"],
            forbidden_objectives=["bypass kill switch", "ignore risk state"],
            inherits_doctrine=True,
            provenance_operator_id="operator",
        ),
        AgentObjectiveProfile(
            agent_id="risk_governor",
            scope="risk_account",
            declared_objectives=["enforce lockouts and bucket state"],
            forbidden_objectives=["increase risk during lockout", "override operator halt"],
            inherits_doctrine=True,
            provenance_operator_id="operator",
        ),
        AgentObjectiveProfile(
            agent_id="research_assistant",
            scope="research",
            declared_objectives=["produce evidence-bound research outputs"],
            forbidden_objectives=["conceal uncertainty", "fabricate fills"],
            inherits_doctrine=True,
            provenance_operator_id="operator",
        ),
    ]


def _lower_join(objs: Sequence[str]) -> str:
    return " ".join(o.lower() for o in objs)


@dataclass
class ConflictFinding:
    conflict_detected: bool
    conflict_type: str
    agents_involved: List[str]
    severity: str  # LOW|MEDIUM|HIGH|CRITICAL
    recommended_action: str
    evidence: Dict[str, Any] = field(default_factory=dict)


def detect_structured_conflicts(profiles: Sequence[AgentObjectiveProfile]) -> List[ConflictFinding]:
    """Deterministic pairwise and rule-based conflict detection."""
    findings: List[ConflictFinding] = []
    plist = list(profiles)

    # Rule: growth vs lockout bypass
    for i, a in enumerate(plist):
        for b in plist[i + 1 :]:
            ta = _lower_join(a.declared_objectives)
            tb = _lower_join(b.declared_objectives)
            if (
                ("maximize contracts" in ta or "maximize growth" in ta)
                and ("bypass" in tb and "lockout" in tb)
            ):
                findings.append(
                    ConflictFinding(
                        True,
                        "growth_vs_lockout_bypass",
                        [a.agent_id, b.agent_id],
                        "CRITICAL",
                        "Remove bypass language or growth-at-all-costs objective; escalate to operator.",
                        {"pair": [a.agent_id, b.agent_id]},
                    )
                )
            if (
                ("maximize contracts" in tb or "maximize growth" in tb)
                and ("bypass" in ta and "lockout" in ta)
            ):
                findings.append(
                    ConflictFinding(
                        True,
                        "growth_vs_lockout_bypass",
                        [a.agent_id, b.agent_id],
                        "CRITICAL",
                        "Remove bypass language or growth objective; escalate to operator.",
                        {"pair": [a.agent_id, b.agent_id]},
                    )
                )

            # Uncertainty suppression vs doctrine surfacing
            if ("suppress" in ta and "uncertainty" in ta) or ("hide" in ta and "uncertainty" in ta):
                if "surface" in tb or "disclose" in tb:
                    findings.append(
                        ConflictFinding(
                            True,
                            "uncertainty_suppression_vs_transparency",
                            [a.agent_id, b.agent_id],
                            "HIGH",
                            "Align on uncertainty disclosure; doctrine requires non-concealment.",
                            {},
                        )
                    )

            # Local PnL vs capital preservation
            if "local pnl" in ta or "local pnl" in tb:
                if "capital preservation" in ta or "capital preservation" in tb:
                    pass  # ok
                elif "minimize drawdown" in ta or "minimize drawdown" in tb:
                    if "ignore drawdown" in ta or "ignore drawdown" in tb:
                        findings.append(
                            ConflictFinding(
                                True,
                                "local_pnl_vs_drawdown",
                                [a.agent_id, b.agent_id],
                                "HIGH",
                                "Reconcile PnL objective with drawdown controls under operator governance.",
                                {},
                            )
                        )

    # Forbidden objective hits within same agent
    for p in plist:
        blob = _lower_join(p.declared_objectives)
        for f in p.forbidden_objectives:
            if f.lower() in blob:
                findings.append(
                    ConflictFinding(
                        True,
                        "declared_vs_forbidden_objective",
                        [p.agent_id],
                        "CRITICAL",
                        "Declared objectives intersect forbidden list for this agent profile.",
                        {"agent_id": p.agent_id, "forbidden": f},
                    )
                )

    # Pairwise declared text tension (throughput vs exposure) — retained
    for i, a in enumerate(plist):
        for b in plist[i + 1 :]:
            ta = _lower_join(a.declared_objectives)
            tb = _lower_join(b.declared_objectives)
            if ("maximize contracts" in ta and "minimize exposure" in tb) or (
                "maximize contracts" in tb and "minimize exposure" in ta
            ):
                findings.append(
                    ConflictFinding(
                        True,
                        "throughput_vs_exposure",
                        [a.agent_id, b.agent_id],
                        "MEDIUM",
                        "Clarify priority with operator; add governance constraints to both agents.",
                        {},
                    )
                )

    # Dedupe by (type, agents)
    seen: Set[Tuple[str, Tuple[str, ...]]] = set()
    unique: List[ConflictFinding] = []
    for f in findings:
        key = (f.conflict_type, tuple(sorted(f.agents_involved)))
        if key in seen:
            continue
        seen.add(key)
        unique.append(f)
    return unique


def conflict_finding_to_evidence(f: ConflictFinding) -> Dict[str, Any]:
    """Structured, inspectable conflict record (matches governance contract)."""
    ev = dict(f.evidence) if f.evidence else {}
    return {
        "conflict_detected": f.conflict_detected,
        "conflict_type": f.conflict_type,
        "agents_involved": list(f.agents_involved),
        "severity": f.severity,
        "recommended_action": f.recommended_action,
        **ev,
    }


def conflict_findings_to_verdicts(findings: Sequence[ConflictFinding]) -> List[DoctrineVerdict]:
    """Map conflict findings to DoctrineVerdict list (for engine integration)."""
    out: List[DoctrineVerdict] = []
    sig = doctrine_signature()
    ts = datetime.now(timezone.utc)
    sev_map = {"LOW": "INFO", "MEDIUM": "WARNING", "HIGH": "CRITICAL", "CRITICAL": "CRITICAL"}
    for f in findings:
        if not f.conflict_detected:
            continue
        v = (
            "DOCTRINE_VIOLATION"
            if f.severity == "CRITICAL"
            else ("DRIFTING" if f.severity in ("HIGH", "MEDIUM") else "PARTIALLY_ALIGNED")
        )
        out.append(
            DoctrineVerdict(
                verdict=v,  # type: ignore[arg-type]
                rule_triggered=f"structured_conflict:{f.conflict_type}",
                severity=sev_map.get(f.severity, "WARNING"),  # type: ignore[arg-type]
                evidence=conflict_finding_to_evidence(f),
                timestamp=ts,
                signed_by=sig,
                escalation_required=True,
            )
        )
    return out


def evaluate_agent_spec(spec: AgentSpec) -> DoctrineVerdict:
    """Check a single agent configuration against doctrine and alignment rules."""
    text = _lower_join(spec.objectives)
    payload = {
        "agent_id": spec.agent_id,
        "role": spec.role,
        "objectives": list(spec.objectives),
        "parent_doctrine_ack": spec.parent_doctrine_ack,
        "provenance_operator_id": spec.provenance_operator_id,
        "risk_hooks_acknowledged": spec.risk_hooks_acknowledged,
    }
    base = evaluate_doctrine_alignment(change_type="bot_creation", payload=payload)

    strict = os.environ.get("EZRAS_AGENT_PROVENANCE_REQUIRED", "0") in ("1", "true", "True")
    if strict and not (spec.provenance_operator_id or "").strip():
        return DoctrineVerdict(
            verdict="DRIFTING",
            rule_triggered="missing_operator_provenance",
            severity="CRITICAL",
            evidence={"agent_id": spec.agent_id, "strict_env": True},
            timestamp=datetime.now(timezone.utc),
            signed_by=doctrine_signature(),
            escalation_required=True,
        )

    if not spec.parent_doctrine_ack:
        return DoctrineVerdict(
            verdict="DOCTRINE_VIOLATION",
            rule_triggered="doctrine_not_acknowledged",
            severity="CRITICAL",
            evidence={"agent_id": spec.agent_id},
            timestamp=datetime.now(timezone.utc),
            signed_by=doctrine_signature(),
            escalation_required=True,
        )

    if not spec.risk_hooks_acknowledged:
        return DoctrineVerdict(
            verdict="PARTIALLY_ALIGNED",
            rule_triggered="risk_hooks_not_acknowledged",
            severity="WARNING",
            evidence={"agent_id": spec.agent_id},
            timestamp=datetime.now(timezone.utc),
            signed_by=doctrine_signature(),
            escalation_required=True,
        )

    if any(
        x in text
        for x in (
            "redefine system goals",
            "replace operator",
            "ignore kill switch",
            "bypass phase8",
        )
    ):
        return DoctrineVerdict(
            verdict="DOCTRINE_VIOLATION",
            rule_triggered="self_authored_objective_conflict",
            severity="CRITICAL",
            evidence={"agent_id": spec.agent_id},
            timestamp=datetime.now(timezone.utc),
            signed_by=doctrine_signature(),
            escalation_required=True,
        )

    # Structured profile from legacy spec
    prof = AgentObjectiveProfile.from_legacy(spec)
    sf = detect_structured_conflicts([prof])
    if sf:
        vs = conflict_findings_to_verdicts(sf)
        if vs:
            return vs[0]

    return base


def detect_cross_agent_objective_conflict(specs: Sequence[AgentSpec]) -> Optional[DoctrineVerdict]:
    """Pairwise legacy detection + structured profiles."""
    if len(specs) < 2:
        return None
    profiles = [AgentObjectiveProfile.from_legacy(s) for s in specs]
    findings = detect_structured_conflicts(profiles)
    vs = conflict_findings_to_verdicts(findings)
    return vs[0] if vs else None


def evaluate_agent_alignment(
    agent_specs: Sequence[AgentSpec],
    *,
    check_cross_agent: bool = True,
) -> List[DoctrineVerdict]:
    """Evaluate each agent and optional cross-agent conflict."""
    out: List[DoctrineVerdict] = [evaluate_agent_spec(s) for s in agent_specs]
    if check_cross_agent and len(agent_specs) >= 2:
        profiles = [AgentObjectiveProfile.from_legacy(s) for s in agent_specs]
        findings = detect_structured_conflicts(profiles)
        out.extend(conflict_findings_to_verdicts(findings))
    return out


def alignment_rules_summary() -> Dict[str, Any]:
    return {
        "inheritance": "subordinate_agents_inherit_top_level_doctrine_first",
        "no_self_authored_mandate_changes": True,
        "structured_profiles": "AgentObjectiveProfile",
        "conflict_engine": "detect_structured_conflicts",
        "provenance_strict_mode_env": "EZRAS_AGENT_PROVENANCE_REQUIRED",
    }
