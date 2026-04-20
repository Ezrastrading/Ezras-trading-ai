"""Tests for Ezra bot hierarchy (intelligence layer; no live authority)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_ai.global_layer.bot_hierarchy.guards import manager_report_is_not_runtime_proof
from trading_ai.global_layer.bot_hierarchy.gate_discovery import advance_gate_candidate_stage, discover_gate_candidate
from trading_ai.global_layer.bot_hierarchy.integration import build_review_packet_hierarchy_section
from trading_ai.global_layer.bot_hierarchy.models import (
    GateCandidateStage,
    HierarchyAuthorityLevel,
    HierarchyBotType,
    LivePermissions,
    new_hierarchy_bot,
)
from trading_ai.global_layer.bot_hierarchy.registry import (
    EZRA_GOVERNOR_BOT_ID,
    ensure_avenue_master,
    ensure_ezra_governor,
    list_bots,
    load_hierarchy_state,
)
from trading_ai.global_layer.lock_layer.promotion_rung import assert_no_rung_skip


@pytest.fixture()
def hier_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("EZRAS_BOT_HIERARCHY_ROOT", str(tmp_path))
    return tmp_path


def test_registry_creation_and_ezra(hier_root: Path) -> None:
    ez = ensure_ezra_governor(path=hier_root)
    assert ez.bot_id == EZRA_GOVERNOR_BOT_ID
    assert ez.bot_type == HierarchyBotType.EZRA_GOVERNOR
    st = load_hierarchy_state(hier_root)
    assert len(st.get("bots") or []) >= 1
    reg_path = hier_root / "bot_registry.json"
    assert reg_path.is_file()


def test_parent_child_hierarchy(hier_root: Path) -> None:
    ensure_ezra_governor(path=hier_root)
    am = ensure_avenue_master("kalshi_alt", path=hier_root)
    assert am.parent_bot_id == EZRA_GOVERNOR_BOT_ID
    bots = list_bots(path=hier_root)
    assert any(b.bot_id == am.bot_id for b in bots)


def test_avenue_master_cannot_grant_live(hier_root: Path) -> None:
    ensure_ezra_governor(path=hier_root)
    am = ensure_avenue_master("B", path=hier_root)
    assert am.live_permissions.venue_orders is False
    with pytest.raises(ValueError, match="hierarchy_bot_forbids_non_false_live_permissions"):
        new_hierarchy_bot(
            bot_id="bad",
            bot_name="Bad",
            bot_type=HierarchyBotType.AVENUE_MASTER,
            avenue_id="B",
            authority_level=HierarchyAuthorityLevel.AVENUE_INTELLIGENCE,
            parent_bot_id=EZRA_GOVERNOR_BOT_ID,
            gate_id=None,
            live_permissions=LivePermissions(venue_orders=True),
        )


def test_gate_discovery_research_only(hier_root: Path) -> None:
    out = discover_gate_candidate(
        avenue_id="C",
        gate_id="gate_future_x",
        strategy_thesis="test",
        edge_hypothesis="hyp",
        execution_path="paper_only",
        path=hier_root,
    )
    assert out.get("ok") is True
    st = load_hierarchy_state(hier_root)
    cands = st.get("gate_candidates") or []
    assert cands
    assert cands[0].get("stage") == GateCandidateStage.DISCOVERED.value


def test_promotion_ladder_compatibility_rung(hier_root: Path) -> None:
    discover_gate_candidate(
        avenue_id="A",
        gate_id="gate_promo",
        strategy_thesis="t",
        edge_hypothesis="e",
        execution_path="x",
        path=hier_root,
    )
    cid = (load_hierarchy_state(hier_root).get("gate_candidates") or [{}])[0].get("candidate_id")
    assert cid
    ok, why = assert_no_rung_skip("T0", "T1")
    assert ok is True
    ok2, why2 = assert_no_rung_skip("T0", "T2")
    assert ok2 is False


def test_reporting_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_BOT_HIERARCHY_ROOT", str(tmp_path))
    from trading_ai.global_layer.bot_hierarchy.reporting import append_bot_report

    p = append_bot_report(
        {
            "report_type": "test",
            "reporter_bot_id": "b1",
            "parent_bot_id": "p1",
            "avenue_id": "A",
            "gate_id": "gate_a",
            "observation": "o",
            "recommendation": "r",
            "confidence": 0.2,
            "evidence_pointers": [],
            "is_runtime_proof": False,
        },
        root=tmp_path,
    )
    assert p.is_file()
    lines = p.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert manager_report_is_not_runtime_proof(row) is True


def test_manager_summary_not_proof() -> None:
    assert manager_report_is_not_runtime_proof({"is_runtime_proof": False}) is True
    assert manager_report_is_not_runtime_proof({"is_runtime_proof": True}) is False


def test_review_integration_section(hier_root: Path) -> None:
    ensure_ezra_governor(path=hier_root)
    ensure_avenue_master("A", path=hier_root)
    sec = build_review_packet_hierarchy_section(root=hier_root)
    assert "avenue_masters" in sec or sec.get("honesty")


def test_multi_avenue_generic(hier_root: Path) -> None:
    ensure_ezra_governor(path=hier_root)
    ensure_avenue_master("coinbase", path=hier_root)
    ensure_avenue_master("kalshi", path=hier_root)
    assert len([b for b in list_bots(path=hier_root) if b.bot_type == HierarchyBotType.AVENUE_MASTER]) == 2


def test_future_gate_generic(hier_root: Path) -> None:
    discover_gate_candidate(
        avenue_id="avenue_z",
        gate_id="future_gate_99",
        strategy_thesis="s",
        edge_hypothesis="e",
        execution_path="unspecified",
        path=hier_root,
    )
    st = load_hierarchy_state(hier_root)
    assert any((c or {}).get("gate_id") == "future_gate_99" for c in (st.get("gate_candidates") or []))


def test_gate_candidate_advance_no_skip(hier_root: Path) -> None:
    discover_gate_candidate(
        avenue_id="A",
        gate_id="gate_adv",
        strategy_thesis="t",
        edge_hypothesis="e",
        execution_path="x",
        path=hier_root,
    )
    cid = (load_hierarchy_state(hier_root).get("gate_candidates") or [{}])[0].get("candidate_id")
    with pytest.raises(ValueError, match="stage_skip_forbidden"):
        advance_gate_candidate_stage(str(cid), to_stage=GateCandidateStage.SIM_PASSED.value, path=hier_root)
