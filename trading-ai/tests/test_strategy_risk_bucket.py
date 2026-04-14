from trading_ai.automation.strategy_risk_bucket import resolve_effective_risk_for_open, worst_of_buckets


def test_worst_bucket():
    assert worst_of_buckets("NORMAL", "BLOCKED") == "BLOCKED"
    assert worst_of_buckets("REDUCED", "NORMAL") == "REDUCED"


def test_resolve_open_layers():
    t = {"strategy_id": "s1", "capital_allocated": 10}
    r = resolve_effective_risk_for_open(t)
    assert "effective_bucket" in r
    assert "strategy_bucket" in r
