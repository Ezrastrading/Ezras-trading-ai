"""Run one NTE Coinbase cycle (requires COINBASE_ENABLED + API keys)."""

from __future__ import annotations

import logging

from trading_ai.shark.dotenv_load import load_shark_dotenv

load_shark_dotenv()

logging.basicConfig(level=logging.INFO)


def main() -> None:
    from trading_ai.shark.coinbase_accumulator import CoinbaseAccumulator, coinbase_enabled

    if not coinbase_enabled():
        logging.getLogger(__name__).info("Set COINBASE_ENABLED=true to run.")
        return
    CoinbaseAccumulator().run_full_cycle()


if __name__ == "__main__":
    main()
