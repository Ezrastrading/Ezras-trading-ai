"""Position sizing policy vs account risk bucket."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from trading_ai.automation import position_sizing_policy as psp
from trading_ai.automation.position_sizing_policy import (
    CANONICAL_META_REQUIRED_KEYS,
    TradePlacementBlocked,
    approve_new_trade_for_execution,
    apply_position_sizing_policy,
    compute_sizing_decision_for_trade,
    enrich_open_payload_with_sizing_preview,
    maybe_notify_trade_blocked_by_sizing,
    meta_is_complete,
    normalize_position_sizing_meta,
    resolve_raw_and_effective_bucket,
    simulate_sizing_cli,
    validate_trade_open_invariants,
)
from trading_ai.automation.sizing_cli import main_sizing


def test_normal_full_size() -> None:
    d = apply_position_sizing_policy(100.0, "NORMAL", raw_bucket="NORMAL", bucket_fallback_applied=False)
    assert d["approved_size"] == 100.0
    assert d["approval_status"] == "APPROVED"
    assert d["sizing_multiplier"] == 1.0
    assert d.get("bucket_fallback_applied") is False


def test_reduced_half() -> None:
    d = apply_position_sizing_policy(100.0, "REDUCED", raw_bucket="REDUCED", bucket_fallback_applied=False)
    assert d["approved_size"] == 50.0
    assert d["approval_status"] == "REDUCED"
    assert d["reason"] == "risk_bucket_reduction"


def test_blocked_zero() -> None:
    d = apply_position_sizing_policy(100.0, "BLOCKED", raw_bucket="BLOCKED", bucket_fallback_applied=False)
    assert d["approved_size"] == 0.0
    assert d["approval_status"] == "BLOCKED"


def test_unknown_bucket_failsafe() -> None:
    d = apply_position_sizing_policy(
        100.0,
        "REDUCED",
        raw_bucket="UNKNOWN",
        bucket_fallback_applied=True,
    )
    assert d["approved_size"] == 50.0
    assert d["approval_status"] == "REDUCED"
    assert d["reason"] == "unknown_bucket_failsafe"
    assert d["bucket_fallback_applied"] is True


def test_invalid_requested_size_blocked() -> None:
    d = psp.apply_position_sizing_policy_safe(-5.0, "NORMAL")
    assert d["approval_status"] == "BLOCKED"
    assert d["reason"] == "invalid_requested_size"


def test_missing_risk_state_defaults_normal_bucket_logic(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    rb = resolve_raw_and_effective_bucket(None)
    assert rb["effective_bucket"] == "NORMAL"
    assert rb["bucket_fallback_applied"] is False


def test_corrupt_state_file_recovery(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    p = tmp_path / "state" / "position_sizing_state.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not json", encoding="utf-8")
    st = psp.read_position_sizing_state()
    assert "version" in st


def test_approve_blocked_raises(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    rs = tmp_path / "state" / "risk_state.json"
    rs.parent.mkdir(parents=True, exist_ok=True)
    rs.write_text(
        json.dumps(
            {
                "equity_index": 80.0,
                "peak_equity_index": 100.0,
                "recent_results": ["loss", "loss", "loss", "loss", "win"],
                "processed_close_ids": [],
            }
        ),
        encoding="utf-8",
    )
    t = {
        "trade_id": "x1",
        "capital_allocated": 100.0,
    }
    with pytest.raises(TradePlacementBlocked):
        approve_new_trade_for_execution(t)


def test_approve_reduced_halves_capital(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    rs = tmp_path / "state" / "risk_state.json"
    rs.parent.mkdir(parents=True, exist_ok=True)
    rs.write_text(
        json.dumps(
            {
                "equity_index": 98.0,
                "peak_equity_index": 100.0,
                "recent_results": ["win", "loss", "loss"],
                "processed_close_ids": [],
            }
        ),
        encoding="utf-8",
    )
    t = {"trade_id": "r1", "capital_allocated": 200.0}
    approve_new_trade_for_execution(t)
    assert t["capital_allocated"] == 100.0
    assert t["position_sizing_meta"]["approval_status"] == "REDUCED"
    assert t["position_sizing_meta"]["effective_bucket"] == "REDUCED"


def test_simulate_unknown_shows_fallback() -> None:
    out = simulate_sizing_cli(100.0, "UNKNOWN")
    assert out["raw_bucket"] == "UNKNOWN"
    assert out["effective_bucket"] == "REDUCED"
    assert out["bucket_fallback_applied"] is True
    assert out["approved_size"] == 50.0
    assert out["reason"] == "unknown_bucket_failsafe"


def test_cli_simulate_json(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    assert main_sizing(["simulate", "--size", "100", "--bucket", "NORMAL"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["approved_size"] == 100.0

    assert main_sizing(["simulate", "--size", "100", "--bucket", "REDUCED"]) == 0
    out2 = json.loads(capsys.readouterr().out)
    assert out2["approved_size"] == 50.0

    assert main_sizing(["simulate", "--size", "100", "--bucket", "BLOCKED"]) == 0
    out3 = json.loads(capsys.readouterr().out)
    assert out3["approved_size"] == 0.0

    assert main_sizing(["simulate", "--size", "100", "--bucket", "UNKNOWN"]) == 0
    out4 = json.loads(capsys.readouterr().out)
    assert out4["effective_bucket"] == "REDUCED"
    assert out4["bucket_fallback_applied"] is True


def test_cli_status_has_raw_and_effective(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    assert main_sizing(["status"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert "raw_bucket" in out
    assert "effective_bucket" in out
    assert "bucket_fallback_applied" in out


def test_validate_requested_size() -> None:
    assert psp.validate_requested_size(None)["valid"] is False
    assert psp.validate_requested_size(0)["valid"] is False
    assert psp.validate_requested_size(1.23)["valid"] is True


def test_enrich_preview_does_not_mutate_capital(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    t = {"trade_id": "p1", "capital_allocated": 200.0}
    enrich_open_payload_with_sizing_preview(t)
    assert t["capital_allocated"] == 200.0
    assert "position_sizing_meta" in t
    assert t["position_sizing_meta"].get("requested_size") == 200.0


def test_blocked_alert_uses_trade_blocked_formatter(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    sent = {}

    def fake_send(settings, text, *, dedupe_key, event_label):
        sent["text"] = text
        sent["event_label"] = event_label
        return {"sent": True, "skipped_duplicate": False, "ok": True}

    monkeypatch.setattr(
        "trading_ai.automation.telegram_ops.send_telegram_with_idempotency",
        fake_send,
    )
    exc = TradePlacementBlocked(
        "x",
        decision={
            "requested_size": 100.0,
            "approved_size": 0.0,
            "reason": "risk_bucket_blocked",
            "effective_bucket": "BLOCKED",
        },
        trade_snapshot={"trade_id": "t1", "market": "M", "position": "YES"},
    )
    maybe_notify_trade_blocked_by_sizing(exc)
    assert "TRADE BLOCKED" in sent["text"]
    assert "TRADE OPEN" not in sent["text"]
    assert "Block Reason:" in sent["text"]
    assert sent["event_label"] == "trade_blocked_sizing"


def test_compute_preview_invalid_size() -> None:
    d = compute_sizing_decision_for_trade({"capital_allocated": -1})
    assert d["approval_status"] == "BLOCKED"
    assert d["reason"] == "invalid_requested_size"


def test_normalize_repairs_partial_meta(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    t = {
        "trade_id": "p-meta",
        "capital_allocated": 100.0,
        "position_sizing_meta": {"approved_size": 50.0, "effective_bucket": "REDUCED"},
    }
    normalize_position_sizing_meta(t, source_path="test", mutate_capital=False, record_audit=False)
    assert meta_is_complete(t["position_sizing_meta"])
    for k in CANONICAL_META_REQUIRED_KEYS:
        assert k in t["position_sizing_meta"]
    assert t["risk_bucket_at_open"] == t["position_sizing_meta"]["effective_bucket"]


def test_normalize_unknown_bucket_fallback_explicit(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))

    def _raw_unknown(_evt=None):
        return "WAT"

    monkeypatch.setattr(
        "trading_ai.automation.risk_bucket.get_account_risk_bucket",
        _raw_unknown,
    )
    t = {"trade_id": "unk", "capital_allocated": 80.0}
    normalize_position_sizing_meta(t, source_path="test", mutate_capital=False, record_audit=False)
    m = t["position_sizing_meta"]
    assert m["raw_bucket"] == "WAT"
    assert m["bucket_fallback_applied"] is True
    assert m["reason"] == "unknown_bucket_failsafe"
    assert m["effective_bucket"] == "REDUCED"


def test_invariant_fails_malformed_blocked() -> None:
    bad = {
        "trade_id": "b1",
        "risk_bucket_at_open": "BLOCKED",
        "position_sizing_meta": {
            "requested_size": 100.0,
            "approved_size": 10.0,
            "raw_bucket": "BLOCKED",
            "effective_bucket": "BLOCKED",
            "bucket_fallback_applied": False,
            "sizing_multiplier": 0.0,
            "approval_status": "BLOCKED",
            "reason": "risk_bucket_blocked",
            "trading_allowed": False,
            "normalized_at": "2026-01-01T00:00:00+00:00",
            "source": "x",
            "repair_applied": False,
            "repair_reason": None,
        },
    }
    inv = validate_trade_open_invariants(bad, live=False)
    assert inv["ok"] is False
    assert any("blocked_requires_zero_approved" in e for e in inv["errors"])


def test_invariant_passes_canonical_trade(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    t = {"trade_id": "ok1", "capital_allocated": 50.0}
    normalize_position_sizing_meta(t, source_path="test", mutate_capital=False, record_audit=False)
    inv = validate_trade_open_invariants(t, live=False)
    assert inv["ok"] is True


def test_contradictory_meta_repairs(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    t = {
        "trade_id": "c1",
        "capital_allocated": 100.0,
        "risk_bucket_at_open": "NORMAL",
        "position_sizing_meta": {
            "requested_size": 100.0,
            "approved_size": 25.0,
            "raw_bucket": "NORMAL",
            "effective_bucket": "NORMAL",
            "bucket_fallback_applied": False,
            "sizing_multiplier": 1.0,
            "approval_status": "APPROVED",
            "reason": "risk_bucket_ok",
        },
    }
    normalize_position_sizing_meta(t, source_path="test", mutate_capital=False, record_audit=False)
    assert float(t["position_sizing_meta"]["approved_size"]) == 100.0


def test_cli_validate_sample(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    assert main_sizing(["validate-sample"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["summary"] == "invariants_pass"
    assert out["errors"] == []
    assert "normalized_meta" in out
    assert out.get("risk_bucket_at_open")


def test_pre_submit_log_not_written_when_blocked(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    rs = tmp_path / "state" / "risk_state.json"
    rs.parent.mkdir(parents=True, exist_ok=True)
    rs.write_text(
        json.dumps(
            {
                "equity_index": 80.0,
                "peak_equity_index": 100.0,
                "recent_results": ["loss", "loss", "loss", "loss", "win"],
                "processed_close_ids": [],
            }
        ),
        encoding="utf-8",
    )
    t = {"trade_id": "blocked-x", "capital_allocated": 100.0}
    with pytest.raises(TradePlacementBlocked):
        approve_new_trade_for_execution(t)
    log = tmp_path / "logs" / "pre_submit_sizing_log.md"
    assert not log.is_file()


def test_pre_submit_sizing_log_appended_on_live_approve(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    rs = tmp_path / "state" / "risk_state.json"
    rs.parent.mkdir(parents=True, exist_ok=True)
    rs.write_text(
        json.dumps(
            {
                "equity_index": 98.0,
                "peak_equity_index": 100.0,
                "recent_results": ["win", "loss", "loss"],
                "processed_close_ids": [],
            }
        ),
        encoding="utf-8",
    )
    t = {"trade_id": "pre-submit-1", "capital_allocated": 200.0}
    approve_new_trade_for_execution(t)
    log = tmp_path / "logs" / "pre_submit_sizing_log.md"
    assert log.is_file()
    text = log.read_text(encoding="utf-8")
    assert "pre_submit_sizing" in text and "pre-submit-1" in text
    assert '"trade_id": "pre-submit-1"' in text
    assert '"requested_size": 200.0' in text
    assert '"approved_size": 100.0' in text
    assert '"raw_bucket"' in text
    assert '"effective_bucket": "REDUCED"' in text
    assert '"bucket_fallback_applied"' in text
    assert '"sizing_multiplier": 0.5' in text
    assert '"approval_status": "REDUCED"' in text
    assert '"trading_allowed": true' in text
    assert '"reason": "risk_bucket_reduction"' in text


def test_enrich_preview_normalizes_incomplete_before_display(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    t = {"trade_id": "pv", "capital_allocated": 75.0, "position_sizing_meta": {"approved_size": 75.0}}
    enrich_open_payload_with_sizing_preview(t)
    assert meta_is_complete(t["position_sizing_meta"])
    inv = validate_trade_open_invariants(t, live=False)
    assert inv["ok"] is True


def test_sizing_append_log_includes_repair_fields(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    captured: list = []

    def _cap(entry: Dict[str, Any]) -> None:
        captured.append(entry)

    monkeypatch.setattr("trading_ai.automation.position_sizing_policy.append_position_sizing_log", _cap)
    psp.record_position_sizing_decision(
        trade_id="tlog",
        decision={
            "requested_size": 10.0,
            "approved_size": 10.0,
            "raw_bucket": "NORMAL",
            "effective_bucket": "NORMAL",
            "bucket_fallback_applied": False,
            "sizing_multiplier": 1.0,
            "approval_status": "APPROVED",
            "reason": "risk_bucket_ok",
            "repair_applied": True,
            "repair_reason": "partial_meta",
        },
        event_type="preview_open",
        source_path="preview_open",
    )
    assert captured and captured[0].get("repair_applied") is True
    assert captured[0].get("repair_reason") == "partial_meta"
