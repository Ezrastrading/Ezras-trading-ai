"""Tests for system doctrine and consistency engine."""

from __future__ import annotations

from pathlib import Path

import pytest

from trading_ai.governance.agent_alignment import (
    AgentObjectiveProfile,
    AgentSpec,
    detect_structured_conflicts,
    evaluate_agent_alignment,
)
from trading_ai.governance.consistency_engine import (
    evaluate_change_consistency,
    evaluate_doctrine_alignment,
    get_consistency_status,
    get_full_integrity_report,
)
from trading_ai.governance.doctrine_evaluator import evaluate_doctrine_scorecard
from trading_ai.governance.system_doctrine import compute_doctrine_sha256


@pytest.fixture
def runtime_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    return tmp_path


def test_doctrine_alignment_aligned_healthy_sample(runtime_tmp: Path) -> None:
    v = evaluate_doctrine_alignment(
        change_type="audit",
        payload={"scope": "phase8_readiness", "result": "pass"},
        context={"operator": "test"},
    )
    assert v.verdict == "ALIGNED"
    assert v.escalation_required is False
    assert "scorecard" in v.evidence
    assert v.evidence["scorecard"]["dimensions"]["truthfulness"] == "PASS"


def test_doctrine_violation_conflicting_sample(runtime_tmp: Path) -> None:
    v = evaluate_doctrine_alignment(
        change_type="strategy_change",
        payload={"notes": "bypass governance and hide losses from operator"},
    )
    assert v.verdict == "DOCTRINE_VIOLATION"


def test_agent_alignment_conflicting_objectives(runtime_tmp: Path) -> None:
    a = AgentSpec(
        agent_id="a1",
        role="execution",
        objectives=["maximize contracts traded"],
        provenance_operator_id="op-1",
    )
    b = AgentSpec(
        agent_id="b1",
        role="risk",
        objectives=["minimize exposure at all costs"],
        provenance_operator_id="op-1",
    )
    verdicts = evaluate_agent_alignment([a, b], check_cross_agent=True)
    rules = " ".join(x.rule_triggered for x in verdicts)
    assert "throughput_vs_exposure" in rules or "structured_conflict" in rules


def test_consistency_status_includes_doctrine_sha(runtime_tmp: Path) -> None:
    st = get_consistency_status()
    assert st["doctrine"]["sha256"] == compute_doctrine_sha256()
    assert st["runtime_root"] == str(runtime_tmp)
    assert "audit_chain" in st
    assert "encryption_at_rest" in st
    assert "full_integrity" in st
    assert "temporal" in st
    assert "automation_heartbeat" in st
    fi = get_full_integrity_report()
    assert "overall_ok" in fi
    assert "audit_chain" in fi
    assert "tamper_evident_failure" in fi["audit_chain"]


def test_evaluate_change_consistency_has_delta_score(runtime_tmp: Path) -> None:
    out = evaluate_change_consistency(
        change_type="parameter_change",
        payload={"param": "edge_threshold", "old": 5, "new": 6},
    )
    assert "consistency_delta_score" in out
    assert 0.0 <= out["consistency_delta_score"] <= 1.0


def test_structured_conflict_evidence_shape() -> None:
    from trading_ai.governance.agent_alignment import conflict_finding_to_evidence

    a = AgentObjectiveProfile(
        agent_id="g1",
        scope="growth",
        declared_objectives=["maximize growth at all costs"],
    )
    b = AgentObjectiveProfile(
        agent_id="r1",
        scope="risk",
        declared_objectives=["bypass risk lockouts when convenient"],
    )
    fs = detect_structured_conflicts([a, b])
    assert fs
    ev = conflict_finding_to_evidence(fs[0])
    assert ev["conflict_detected"] is True
    assert ev["recommended_action"]
    assert "agents_involved" in ev


def test_structured_conflict_growth_vs_lockout() -> None:
    a = AgentObjectiveProfile(
        agent_id="g1",
        scope="growth",
        declared_objectives=["maximize growth at all costs"],
    )
    b = AgentObjectiveProfile(
        agent_id="r1",
        scope="risk",
        declared_objectives=["bypass risk lockouts when convenient"],
    )
    findings = detect_structured_conflicts([a, b])
    assert any(f.conflict_type == "growth_vs_lockout_bypass" for f in findings)


def test_doctrine_scorecard_rule_table() -> None:
    sc = evaluate_doctrine_scorecard(
        change_type="strategy_change",
        payload={"notes": "bypass governance for profit"},
    )
    assert sc["dimensions"]["whole_system_alignment"] == "FAIL"
    assert sc["verdict"] == "DOCTRINE_VIOLATION"
    assert any(r["rule_id"] == "R_SYS_01" for r in sc["triggered_rules"])


def test_doctrine_hash_tamper_detected(monkeypatch: pytest.MonkeyPatch, runtime_tmp: Path) -> None:
    monkeypatch.setattr(
        "trading_ai.governance.system_doctrine.EXPECTED_DOCTRINE_SHA256",
        "0" * 64,
        raising=True,
    )
    from trading_ai.governance.system_doctrine import verify_doctrine_integrity

    v = verify_doctrine_integrity()
    assert v.verdict == "HALT"
