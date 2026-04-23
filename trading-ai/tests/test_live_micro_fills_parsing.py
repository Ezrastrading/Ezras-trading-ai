from __future__ import annotations


def test_parse_coinbase_fills_size_in_quote_derives_base_qty() -> None:
    from trading_ai.live_micro.fills import parse_coinbase_fills

    fills = [
        {
            "price": "77711.82",
            "size": "8.9197626996",
            "size_in_quote": True,
            "commission": "0.1070371523952",
        }
    ]
    avg, base, quote, comm, diag = parse_coinbase_fills(fills)
    assert diag["saw_quote_sizes"] == 1
    assert avg and abs(avg - 77711.82) < 1e-6
    assert quote and abs(quote - 8.9197626996) < 1e-9
    assert comm and comm > 0
    # base should be tiny: ~8.9197 / 77711.82
    assert base and 0 < base < 0.001


def test_parse_coinbase_fills_base_size_uses_px_times_size_for_quote() -> None:
    from trading_ai.live_micro.fills import parse_coinbase_fills

    fills = [{"price": "2000", "size": "0.0025", "size_in_quote": False}]
    avg, base, quote, comm, diag = parse_coinbase_fills(fills)
    assert diag["saw_base_sizes"] == 1
    assert avg == 2000.0
    assert base == 0.0025
    assert quote == 5.0
    assert comm == 0.0

