from trading_ai.automation.adaptive_sizing import get_effective_sizing_multiplier, explain_multiplier_decision


def test_default_ladder():
    assert get_effective_sizing_multiplier("NORMAL") == 1.0
    assert get_effective_sizing_multiplier("REDUCED") == 0.5
    assert get_effective_sizing_multiplier("BLOCKED") == 0.0


def test_explain():
    d = explain_multiplier_decision("NORMAL")
    assert d["multiplier"] == 1.0
