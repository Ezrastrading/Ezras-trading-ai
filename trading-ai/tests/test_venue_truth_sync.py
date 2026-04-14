from trading_ai.execution.venue_truth_sync import (
    KalshiVenueTruthAdapter,
    MockLocalVenueAdapter,
    run_truth_sync,
    validate_external_position_row,
)


def test_validate_position_row():
    assert validate_external_position_row({"trade_id": "a", "contracts": 1})[0] is True
    assert validate_external_position_row({})[0] is False


def test_aligned_mock(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    ad = MockLocalVenueAdapter({"open_positions": [{"trade_id": "a", "contracts": 1}], "recent_fills": [], "cash": {}, "fees": {}})
    r = run_truth_sync(internal_open_ids=["a"], internal_cash=100.0, adapter=ad)
    assert r["verdict"] == "ALIGNED"


def test_material_drift_mock(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    ad = MockLocalVenueAdapter({"open_positions": [{"trade_id": "ghost-1", "contracts": 1}], "recent_fills": [], "cash": {}, "fees": {}})
    r = run_truth_sync(internal_open_ids=["real-1"], internal_cash=1000.0, adapter=ad)
    assert r["verdict"] == "MATERIAL_DRIFT"


def test_unsupported_kalshi_no_creds(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.delenv("KALSHI_API_KEY_ID", raising=False)
    monkeypatch.delenv("KALSHI_API_KEY", raising=False)
    r = run_truth_sync(internal_open_ids=[], internal_cash=None, adapter_factory="kalshi")
    assert r["verdict"] == "UNSUPPORTED"
    assert "kalshi" in str(r.get("detail", "")).lower() or r.get("detail")


def test_kalshi_adapter_id():
    class _S:
        kalshi_enabled = True
        kalshi_trade_api_base = "https://demo.elections.kalshi.com/trade-api/v2"

    a = KalshiVenueTruthAdapter(_S())
    assert a.adapter_id() == "kalshi"
