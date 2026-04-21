import os


def test_asymmetric_gate_registry_visibility(monkeypatch):
    monkeypatch.setenv("ASYMMETRIC_ENABLED", "true")
    from trading_ai.multi_avenue.avenue_registry import merged_avenue_definitions

    avs = merged_avenue_definitions(runtime_root=None)
    by = {a["avenue_id"]: a for a in avs}
    assert "gate_a_asymmetric" in by["A"]["gates"]
    assert "gate_b_asymmetric" in by["B"]["gates"]

