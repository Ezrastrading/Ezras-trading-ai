import pytest

from trading_ai.nte.monitoring.live_dashboard import build_live_monitoring_dashboard, strategy_ab_label
from trading_ai.nte.reports.first_twenty_trades_report import build_first_twenty_trades_report
from trading_ai.nte.memory.store import MemoryStore


def test_strategy_ab_label():
    assert strategy_ab_label("mean_reversion") == "A"
    assert strategy_ab_label("continuation_pullback") == "B"


def test_live_dashboard_boots(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    d = build_live_monitoring_dashboard(engine=None, user_ws_stale=None)
    assert "A_system_health" in d
    assert "hard_stop" in d
    assert d["schema_version"] == 1


def test_first_twenty_report_with_sample_trades(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    st = MemoryStore()
    st.ensure_defaults()
    tm = st.load_json("trade_memory.json")
    tm["trades"] = [
        {
            "avenue": "coinbase",
            "setup_type": "mean_reversion",
            "net_pnl_usd": 5.0,
            "fees": 0.2,
            "spread_bps": 3.0,
            "duration_sec": 120,
            "exit_reason": "take_profit",
            "entry_maker_intent": True,
            "expected_edge_bps": 12.0,
        }
    ]
    st.save_json("trade_memory.json", tm)
    r = build_first_twenty_trades_report(store=st)
    assert r["trades_included"] == 1
    assert r["trade_table"][0]["strategy_ab"] == "A"
    assert "summary_metrics" in r
