from __future__ import annotations

import time
from pathlib import Path


def test_product_cooldown_blocks_then_expires(tmp_path: Path) -> None:
    from trading_ai.live_micro.suppression import check_suppression, set_product_cooldown

    set_product_cooldown(runtime_root=tmp_path, product_id="BTC-USD", seconds=2, reason="x", meta={"a": 1})
    d1 = check_suppression(runtime_root=tmp_path, product_id="BTC-USD", quote_ccy="USD")
    assert d1.suppressed is True
    time.sleep(2.1)
    d2 = check_suppression(runtime_root=tmp_path, product_id="BTC-USD", quote_ccy="USD")
    assert d2.suppressed is False


def test_quote_wallet_cooldown_blocks_quote_only(tmp_path: Path) -> None:
    from trading_ai.live_micro.suppression import check_suppression, set_quote_wallet_cooldown

    set_quote_wallet_cooldown(runtime_root=tmp_path, quote_ccy="USDC", seconds=60, reason="dust", meta={})
    assert check_suppression(runtime_root=tmp_path, product_id="BTC-USDC", quote_ccy="USDC").suppressed is True
    # USD products should remain allowed
    assert check_suppression(runtime_root=tmp_path, product_id="BTC-USD", quote_ccy="USD").suppressed is False

