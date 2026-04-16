import json
import logging
import sys

sys.path.insert(0, "src")
logging.basicConfig(level=logging.INFO)
from trading_ai.shark.dotenv_load import load_shark_dotenv  # noqa: E402

load_shark_dotenv()
from trading_ai.shark.outlets.coinbase import CoinbaseClient  # noqa: E402

c = CoinbaseClient()
print("Has creds:", c.has_credentials())
print("Balance:", c.get_usd_balance())
print()
print("=== DEBUG ALL ===")
results = c.debug_all_balances()
print(json.dumps(results, indent=2)[:5000])
