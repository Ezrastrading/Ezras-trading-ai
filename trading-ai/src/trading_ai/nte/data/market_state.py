"""In-memory market snapshot per product (bid/ask/mid/spread/volatility helpers)."""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional


@dataclass
class ProductMarketState:
    product_id: str
    best_bid: Optional[float] = None
    best_ask: Optional[float] = None
    last_trade: Optional[float] = None
    spread_bps: Optional[float] = None
    mid_price: Optional[float] = None
    last_update_ts: float = field(default_factory=time.time)
    recent_mids: Deque[float] = field(default_factory=lambda: deque(maxlen=120))

    def update_mid(self) -> None:
        if self.best_bid is None or self.best_ask is None:
            return
        if self.best_bid <= 0 or self.best_ask <= 0:
            return
        self.mid_price = (self.best_bid + self.best_ask) / 2.0
        if self.best_bid > 0:
            self.spread_bps = ((self.best_ask - self.best_bid) / self.best_bid) * 10000.0
        if self.mid_price is not None:
            self.recent_mids.append(float(self.mid_price))
        self.last_update_ts = time.time()

    def short_volatility_bps(self, window: int = 30) -> float:
        """Rolling stdev of mid returns over last ``window`` mids, in bps (approx)."""
        if len(self.recent_mids) < max(5, window // 2):
            return 0.0
        mids = list(self.recent_mids)[-window:]
        if len(mids) < 3:
            return 0.0
        rets: List[float] = []
        for i in range(1, len(mids)):
            if mids[i - 1] > 0:
                rets.append((mids[i] - mids[i - 1]) / mids[i - 1])
        if not rets:
            return 0.0
        m = sum(rets) / len(rets)
        v = sum((x - m) ** 2 for x in rets) / len(rets)
        sigma = math.sqrt(max(v, 0.0))
        return sigma * 10000.0


def empty_states(products: List[str]) -> Dict[str, ProductMarketState]:
    return {p: ProductMarketState(product_id=p) for p in products}
