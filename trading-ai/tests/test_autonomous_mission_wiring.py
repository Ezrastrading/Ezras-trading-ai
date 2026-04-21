"""Mission fields, edge/convergence snapshots, backbone status, trade cycle intelligence."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from trading_ai.global_layer.autonomous_backbone_status import build_autonomous_backbone_status
from trading_ai.global_layer.canonical_specialist_seed import ensure_canonical_specialists
from trading_ai.global_layer.edge_discovery_engine import build_edge_discovery_snapshot
from trading_ai.global_layer.orchestration_registry_normalize import normalize_bot_record
from trading_ai.global_layer.system_mission import default_bot_mission_fields, system_mission_dict
from trading_ai.global_layer.time_to_convergence_engine import build_time_to_convergence_snapshot
from trading_ai.global_layer.trade_cycle_intelligence import classify_trade_efficiency, refresh_trade_cycle_intelligence_bundle
from trading_ai.orchestration.supervised_avenue_a_truth import append_supervised_trade_log_line


def test_system_mission_dict_has_no_guaranteed_roi_language() -> None:
    blob = json.dumps(system_mission_dict())
    assert "guarantee" not in blob.lower()
    assert "100%" not in blob


def test_normalize_bot_includes_mission_fields() -> None:
    b = normalize_bot_record(
        {
            "bot_id": "t_mission",
            "role": "SCANNER",
            "avenue": "A",
            "gate": "gate_a",
            "version": "v1",
        }
    )
    for k in default_bot_mission_fields():
        assert k in b


def test_trade_efficiency_classification() -> None:
    c = classify_trade_efficiency({"outcome_class": "clean_full_proof", "net_pnl": 1.0, "hold_seconds": 10})
    assert c["efficiency_class"] in ("optimal", "acceptable")


@pytest.fixture
def orch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    g = tmp_path / "gov"
    g.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("EZRAS_BOT_REGISTRY_PATH", str(tmp_path / "reg.json"))
    monkeypatch.setattr("trading_ai.global_layer._bot_paths.global_layer_governance_dir", lambda: g)
    monkeypatch.setattr("trading_ai.global_layer.orchestration_paths.global_layer_governance_dir", lambda: g)
    monkeypatch.setattr("trading_ai.global_layer.budget_governor.budget_state_path", lambda: g / "budget.json")
    monkeypatch.setattr(
        "trading_ai.global_layer.orchestration_kill_switch.orchestration_kill_switch_path",
        lambda: g / "orchestration_kill_switch.json",
    )
    (g / "orchestration_kill_switch.json").write_text(
        json.dumps(
            {
                "truth_version": "orchestration_kill_switch_v1",
                "orchestration_frozen": False,
                "avenue": {},
                "gate": {},
                "bot_class": {},
                "bot_id": {},
            }
        ),
        encoding="utf-8",
    )
    return tmp_path


def test_edge_and_convergence_snapshots(orch: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(orch))
    regp = Path(os.environ["EZRAS_BOT_REGISTRY_PATH"])
    from trading_ai.global_layer.bot_registry import register_bot

    register_bot(
        {
            "bot_id": "edge_bot",
            "role": "SCANNER",
            "avenue": "A",
            "gate": "gate_a",
            "version": "v1",
            "profitability_score": 0.6,
            "truth_score": 0.7,
        },
        path=regp,
    )
    e = build_edge_discovery_snapshot(registry_path=regp, runtime_root=orch)
    assert e.get("truth_version") == "edge_discovery_snapshot_v1"
    t = build_time_to_convergence_snapshot(registry_path=regp)
    assert t.get("truth_version") == "time_to_convergence_snapshot_v1"


def test_trade_cycle_intel_writes(orch: Path) -> None:
    append_supervised_trade_log_line(
        runtime_root=orch,
        record={"source": "supervised_operator_session", "outcome_class": "clean_full_proof", "trade_id": "x1"},
    )
    out = refresh_trade_cycle_intelligence_bundle(orch)
    assert out.get("trade_count") == 1
    p = orch / "data" / "control" / "trade_cycle_intelligence.json"
    assert p.is_file()


def test_smoke_specialists_and_backbone(orch: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(orch))
    regp = Path(os.environ["EZRAS_BOT_REGISTRY_PATH"])
    s = ensure_canonical_specialists(registry_path=regp)
    assert s.get("ok") is True
    bb = build_autonomous_backbone_status(registry_path=regp, runtime_root=orch, write_file=False)
    assert bb.get("system_profitability_mission_active") is True
    assert bb.get("truth_version") == "autonomous_backbone_status_v1"
