"""Isolate Phase 2 JSON paths under tmp for pytest."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest


@pytest.fixture
def phase2_pkg_root(tmp_path, monkeypatch):
    """Redirect trading_ai.phase2.paths._pkg_root to a writable tree."""
    root = tmp_path / "pkg"
    (root / "data").mkdir(parents=True)
    (root / "audit" / "daily_reports").mkdir(parents=True)
    (root / "strategy").mkdir(parents=True)
    (tmp_path / "tracking").mkdir(parents=True)
    monkeypatch.setattr("trading_ai.phase2.paths._pkg_root", lambda: root)
    monkeypatch.setattr("trading_ai.phase2.paths.ezras_tracking_dir", lambda: tmp_path / "tracking")

    # Minimal kill-switch recovery template
    ks = {
        "minimum_manual_reviews": 5,
        "calibration_recovery_threshold": 0.55,
        "system_confidence_recovery_threshold": 0.45,
        "operator_confidence_min": 7,
        "reactivation_reason_required": True,
        "minimum_trades_for_recovery_thresholds": 20,
        "recovery_threshold_override_allowed": True,
    }
    (root / "audit" / "kill_switch_recovery.json").write_text(json.dumps(ks), encoding="utf-8")

    yield root


@pytest.fixture
def bootstrap_phase2(phase2_pkg_root, monkeypatch):
    """Empty stores + default version row."""
    from trading_ai.phase2 import json_store, paths

    ez_rt = phase2_pkg_root.parent / "ezras_runtime"
    ez_rt.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(ez_rt))

    json_store.save_list(paths.trades_log_path(), [])
    json_store.save_list(
        paths.session_log_path(),
        [
            {
                "session_id": "s1",
                "date": "2026-04-12",
                "time_of_day": "09:00",
                "market_outlook": "neutral",
                "operator_confidence": 7,
                "operator_focus": 7,
                "recent_streak": "mixed",
            }
        ],
    )
    json_store.save_list(paths.learning_events_log_path(), [])
    json_store.save_list(paths.claude_advice_log_path(), [])
    json_store.save_list(paths.hypotheses_path(), [])
    json_store.save_list(paths.counterfactuals_log_path(), [])
    json_store.save_list(paths.benchmark_log_path(), [])
    json_store.save_list(
        paths.system_versions_path(),
        [
            {
                "version_id": "v1.0.0",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "changes_from_previous": ["test"],
                "trigger": "test",
                "performance_at_version_start": {},
                "performance_at_version_end": None,
            }
        ],
    )
    from trading_ai.phase2.schemas import ResultsLog

    json_store.save_dict(paths.results_log_path(), ResultsLog().model_dump())
    try:
        from trading_ai.phase3.bootstrap import ensure_phase3_files

        ensure_phase3_files()
    except Exception:
        pass
    return phase2_pkg_root
