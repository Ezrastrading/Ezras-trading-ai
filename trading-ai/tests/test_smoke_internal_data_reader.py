import pytest

from trading_ai.global_layer.internal_data_reader import read_normalized_internal


def test_internal_reader_has_normalized_schema(tmp_path, monkeypatch):
    monkeypatch.setenv("EZRAS_RUNTIME_ROOT", str(tmp_path))
    n = read_normalized_internal()
    assert "capital_ledger" in n
    assert "deposits_usd" in n["capital_ledger"]
    assert "trades" in n
    assert isinstance(n["trades"], list)
