"""Edge research subsystem — records, discovery, honesty, scoping, auto-attach proof."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def rt(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    return tmp_path


def test_research_record_creation_and_json_roundtrip(rt: Path) -> None:
    from trading_ai.intelligence.edge_research.models import EdgeResearchRecord, ResearchStatus, parse_record_dict

    r = EdgeResearchRecord(
        avenue_id="X",
        gate_id="g1",
        edge_name="test_edge",
        current_status=ResearchStatus.hypothesis,
        confidence=0.4,
    )
    d = r.to_json_dict()
    r2 = parse_record_dict(d)
    assert r2.edge_name == "test_edge"
    assert r2.avenue_id == "X"


def test_merge_registry_preserves_ids(rt: Path) -> None:
    from trading_ai.intelligence.edge_research.models import StrategyResearchRecord, ResearchStatus
    from trading_ai.intelligence.edge_research.registry import load_registry, merge_records

    a = StrategyResearchRecord(record_id="same", strategy_name="a", current_status=ResearchStatus.hypothesis).to_json_dict()
    b = StrategyResearchRecord(record_id="same", strategy_name="b", current_status=ResearchStatus.hypothesis).to_json_dict()
    b["updated_at"] = a["updated_at"] + "_z"
    merge_records([a], runtime_root=rt)
    merge_records([b], runtime_root=rt)
    reg = load_registry(runtime_root=rt)
    assert len(reg["records"]) == 1
    assert reg["records"][0]["strategy_name"] == "b"


def test_discovery_from_ticket(rt: Path) -> None:
    from trading_ai.intelligence.edge_research.discovery import discover_from_tickets
    from trading_ai.intelligence.paths import tickets_jsonl_path

    p = tickets_jsonl_path(runtime_root=rt)
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(
        {
            "ticket_id": "tk_test_1",
            "avenue_id": "A",
            "gate_id": "gate_a",
            "ticket_type": "edge_opportunity",
            "human_plain_english_summary": "possible edge",
            "confidence": 0.5,
        }
    )
    p.write_text(line + "\n", encoding="utf-8")
    rows = discover_from_tickets(runtime_root=rt)
    assert any(r.get("record_id") == "er_ticket_tk_test_1" for r in rows)


def test_evidence_level_honesty_live_blocked_without_confirmation(rt: Path) -> None:
    from trading_ai.intelligence.edge_research.lifecycle import can_upgrade_status
    from trading_ai.intelligence.edge_research.models import ResearchStatus

    ok, reason = can_upgrade_status(
        ResearchStatus.mock_supported,
        ResearchStatus.live_supported,
        evidence_paths=["data/control/honest_live_status_matrix.json"],
        explicit_live_confirmation=False,
    )
    assert ok is False
    assert "live_requires" in reason


def test_evidence_mock_upgrade_with_harness_artifact(rt: Path) -> None:
    from trading_ai.intelligence.edge_research.lifecycle import can_upgrade_status
    from trading_ai.intelligence.edge_research.models import ResearchStatus

    ok, _ = can_upgrade_status(
        ResearchStatus.hypothesis,
        ResearchStatus.mock_supported,
        evidence_paths=["data/control/mock_execution_harness_results.json"],
    )
    assert ok is True


def test_scoped_filter_separation(rt: Path) -> None:
    from trading_ai.intelligence.edge_research.scoring import filter_scoped

    rows = [
        {"avenue_id": "A", "gate_id": "g1", "record_id": "1"},
        {"avenue_id": "B", "gate_id": "g1", "record_id": "2"},
    ]
    a = filter_scoped(rows, avenue_id="A", gate_id="g1")
    assert len(a) == 1 and a[0]["record_id"] == "1"


def test_comparison_deterministic_id(rt: Path) -> None:
    from trading_ai.intelligence.edge_research.comparisons import build_pair_comparison

    left = {"record_id": "r1", "confidence": 0.5, "current_status": "hypothesis"}
    right = {"record_id": "r2", "confidence": 0.1, "current_status": "hypothesis"}
    c = build_pair_comparison(left, right, dimension="test")
    assert c.record_id == "ercmp__r1__r2"


def test_auto_attach_proof_passes(rt: Path) -> None:
    from trading_ai.intelligence.edge_research.proof import run

    out = run(runtime_root=rt)
    assert out.get("auto_attach_passed") is True
    assert (rt / "data/control/edge_research_auto_attach_proof.json").is_file()


def test_no_cross_avenue_contamination_in_proof_artifacts(rt: Path) -> None:
    from trading_ai.intelligence.edge_research.proof import run

    run(runtime_root=rt)
    g1 = rt / "data/research/avenues/EDGEPROOF/gates/edgeproof_g1/best_edges.json"
    raw = json.loads(g1.read_text(encoding="utf-8"))
    assert raw.get("avenue_id") == "EDGEPROOF"


def test_daily_review_generates_ceo_files(rt: Path) -> None:
    from trading_ai.intelligence.edge_research.daily_cycle import run_daily_edge_research_cycle

    out = run_daily_edge_research_cycle(runtime_root=rt)
    assert out["status"] == "ok"
    assert (rt / "data/research/daily/daily_edge_research_review.json").is_file()
    assert (rt / "data/review/daily_edge_ceo_session.json").is_file()


def test_proving_catalog_in_registry(rt: Path) -> None:
    from trading_ai.intelligence.edge_research.discovery import run_discovery
    from trading_ai.intelligence.edge_research.registry import load_registry

    run_discovery(runtime_root=rt)
    reg = load_registry(runtime_root=rt)
    ids = [r.get("record_id") for r in reg.get("records") or []]
    assert "er_proving_layer_catalog" in ids


def test_scaffold_creates_edge_research_via_auto_scaffold(rt: Path) -> None:
    from trading_ai.multi_avenue.auto_scaffold import ensure_gate_scaffold

    ensure_gate_scaffold("Q", "gate_q1", runtime_root=rt)
    assert (rt / "data/research/avenues/Q/gates/gate_q1/instrument_intelligence.json").is_file()


def test_status_matrix_includes_edge_research_flag(rt: Path) -> None:
    from trading_ai.intelligence.edge_research.artifacts import ensure_global_research_templates
    from trading_ai.multi_avenue.status_matrix import build_multi_avenue_status_matrix

    ensure_global_research_templates(runtime_root=rt)
    m = build_multi_avenue_status_matrix(runtime_root=rt)
    for row in m["rows"]:
        assert "edge_research_scaffold_ready" in row


def test_attachment_lists_edge_research_layers() -> None:
    from trading_ai.multi_avenue.attachment import compute_auto_attach_layers

    out = compute_auto_attach_layers(avenue_id="Z", gate_id="g")
    layers = out.get("auto_attach_layers") or []
    assert "edge_research_subsystem" in layers
