from __future__ import annotations

import json
import time
from pathlib import Path

import pytest


class _FakeOrderResult:
    def __init__(self, *, success: bool, status: str, reason: str, order_id: str):
        self.success = success
        self.status = status
        self.reason = reason
        self.order_id = order_id


class _FakeCoinbaseClient:
    def __init__(self):
        self.placed = []

    def place_market_buy(self, product_id: str, usd_amount: float, *, execution_gate: str = "gate_a"):
        self.placed.append((product_id, float(usd_amount), execution_gate))
        return _FakeOrderResult(success=True, status="placed", reason="", order_id="ord_123")

    def get_fills(self, order_id: str):
        _ = order_id
        return [{"price": "1", "size": "1"}]


def _seed_micro_contract_green(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trading_ai.deployment import live_micro_enablement as lme

    monkeypatch.setenv("EZRA_LIVE_MICRO_ENABLED", "true")
    monkeypatch.setenv("EZRA_LIVE_MICRO_AUTOTRADE_ENABLED", "true")
    monkeypatch.setenv("COINBASE_EXECUTION_ENABLED", "true")

    monkeypatch.setenv("EZRA_LIVE_MICRO_OPERATOR_CONFIRM", lme.OPERATOR_CONFIRM_VALUE)
    monkeypatch.setenv("EZRA_LIVE_MICRO_MAX_NOTIONAL_USD", "10")
    monkeypatch.setenv("EZRA_LIVE_MICRO_MAX_DAILY_LOSS_USD", "15")
    monkeypatch.setenv("EZRA_LIVE_MICRO_MAX_TOTAL_EXPOSURE_USD", "10")
    monkeypatch.setenv("EZRA_LIVE_MICRO_ALLOWED_PRODUCTS", "BTC-USD")
    monkeypatch.setenv("EZRA_LIVE_MICRO_ALLOWED_AVENUE", "A")
    monkeypatch.setenv("EZRA_LIVE_MICRO_ALLOWED_GATE", "gate_b")
    monkeypatch.setenv("EZRA_LIVE_MICRO_MAX_TRADES_PER_SESSION", "3")
    monkeypatch.setenv("EZRA_LIVE_MICRO_COOLDOWN_SEC", "0")
    monkeypatch.setenv("EZRA_LIVE_MICRO_MAX_CONCURRENT_POSITIONS", "1")

    monkeypatch.setattr("trading_ai.deployment.operator_env_contracts.missing_coinbase_credential_env_vars", lambda: [])
    monkeypatch.setattr("trading_ai.control.kill_switch.kill_switch_active", lambda: False)

    ctrl = tmp_path / "data" / "control"
    ctrl.mkdir(parents=True, exist_ok=True)
    (ctrl / "deployed_environment_smoke.json").write_text(
        '{"truth_version":"t","live_disabled":{"ok":true},"live_micro_private_build":{"ok":true}}',
        encoding="utf-8",
    )
    (ctrl / "micro_trade_readiness.json").write_text('{"ok":true}', encoding="utf-8")
    jr = tmp_path / "shark" / "memory" / "global"
    jr.mkdir(parents=True, exist_ok=True)
    (jr / "joint_review_latest.json").write_text("{}", encoding="utf-8")
    risk = tmp_path / "data" / "risk"
    risk.mkdir(parents=True, exist_ok=True)
    (risk / "risk_state.json").write_text('{"daily_pnl_usd":0.0}', encoding="utf-8")

    lme.write_live_session_limits(tmp_path)
    lme.run_live_micro_preflight(tmp_path)
    lme.run_live_micro_readiness(tmp_path)
    lme.write_live_enablement_request(tmp_path, operator="pytest", note="ok")


def test_candidate_queue_progresses_to_submit_and_fill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    _seed_micro_contract_green(tmp_path, monkeypatch)

    from trading_ai.global_layer.review_storage import ReviewStorage

    st = ReviewStorage()
    st.ensure_review_files()
    cq = st.load_json("candidate_queue.json")
    cq["items"] = [
        {
            "id": "c1",
            "ts": time.time(),
            "avenue_id": "B",
            "gate_id": "gate_b",
            "product_id": "BTC-USD",
            "status": "new",
        }
    ]
    st.save_json("candidate_queue.json", cq)

    monkeypatch.setattr("trading_ai.shark.outlets.coinbase.CoinbaseClient", _FakeCoinbaseClient)
    monkeypatch.setattr(
        "trading_ai.shark.outlets.coinbase._brokerage_public_request",
        lambda _p: {"best_bid": "1", "best_ask": "1.01", "price": "1.005", "time": time.time()},
    )
    monkeypatch.setattr(
        "trading_ai.global_layer.gap_engine.evaluate_candidate",
        lambda _c: type("D", (), {"should_trade": True, "rejection_reasons": []})(),
    )
    monkeypatch.setattr("trading_ai.shark.mission.mission_probability_set", lambda _p: object())
    monkeypatch.setattr("trading_ai.shark.mission.mission_probability_reset", lambda _t: None)
    monkeypatch.setattr("trading_ai.automation.post_trade_hub.execute_post_trade_placed", lambda *_a, **_k: {"status": "sent"})

    from trading_ai.live_micro.candidate_execution import run_live_micro_candidate_execution_once

    out = run_live_micro_candidate_execution_once(runtime_root=tmp_path)
    assert out.get("submitted") is True
    assert out.get("filled") is True

    ev = tmp_path / "data" / "control" / "live_micro_execution_events.jsonl"
    assert ev.is_file()
    tail = ev.read_text(encoding="utf-8")
    assert "candidate_selected" in tail
    assert "order_submitted" in tail
    assert "fill_probe" in tail

