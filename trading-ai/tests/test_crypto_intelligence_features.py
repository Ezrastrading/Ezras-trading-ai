from __future__ import annotations

from trading_ai.intelligence.crypto_intelligence.features import extract_structure_features


def test_extract_structure_features_closes_ok() -> None:
    row = {
        "product_id": "BTC-USD",
        "quote_ts": 1_700_000_000.0,
        "closes": [100.0 + i for i in range(40)],
        "move_pct": 0.06,
        "volume_surge_ratio": 2.0,
        "continuation_candles": 3,
        "exhaustion_risk": 0.1,
    }
    f = extract_structure_features(row, product_id="BTC-USD", venue="coinbase", gate_id="gate_b", timestamp_unix=row["quote_ts"])
    assert f.product_id == "BTC-USD"
    assert f.n_closes >= 10
    assert f.last_close is not None
    assert f.setup_family.startswith("gate_b::btc::")


def test_extract_structure_features_missing_closes_honest() -> None:
    row = {"product_id": "BTC-USD"}
    f = extract_structure_features(row, product_id="BTC-USD", venue="coinbase", gate_id="gate_b")
    assert "missing_or_thin_closes" in (f.missing_notes or [])

