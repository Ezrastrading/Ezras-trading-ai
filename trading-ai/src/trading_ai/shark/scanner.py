"""24/7 scan engine — no clock-based execution blocking. Outlet registry + intervals."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Sequence

from trading_ai.shark.models import MarketSnapshot
from trading_ai.shark.state import HOT


class OutletFetcher(Protocol):
    outlet_name: str

    def fetch_binary_markets(self) -> List[MarketSnapshot]: ...


@dataclass
class ScanConfig:
    standard_interval_seconds: float = 5 * 60
    hot_interval_seconds: float = 90
    gap_active_interval_seconds: float = 30
    hot_opportunity_threshold: int = 3
    opportunity_burst_window_seconds: float = 15 * 60


def _demo_markets() -> List[MarketSnapshot]:
    now = time.time()
    return [
        MarketSnapshot(
            market_id="demo-poly-1",
            outlet="polymarket",
            yes_price=0.42,
            no_price=0.58,
            volume_24h=1200.0,
            time_to_resolution_seconds=7200.0,
            resolution_criteria="Resolves YES if event occurs",
            last_price_update_timestamp=now,
            canonical_event_key="evt-demo-1",
        ),
    ]


def merge_outlet_snapshots(fetchers: Sequence[OutletFetcher]) -> List[MarketSnapshot]:
    rows: List[MarketSnapshot] = []
    for f in fetchers:
        try:
            rows.extend(f.fetch_binary_markets())
        except Exception:
            continue
    return rows


def scan_markets(
    fetchers: Optional[Sequence[OutletFetcher]] = None,
    *,
    fallback_demo: bool = True,
) -> List[MarketSnapshot]:
    """Merge all markets from each fetcher — no category whitelist (each outlet returns its full listing)."""
    if fetchers:
        return merge_outlet_snapshots(fetchers)
    if fallback_demo:
        return _demo_markets()
    return []


def resolve_scan_interval_seconds(
    *,
    now: Optional[float] = None,
    gap_exploitation_active: bool = False,
    cfg: Optional[ScanConfig] = None,
) -> float:
    """
    Priority: gap active (30s) > opportunity burst / hot (90s) > standard (5 min).
    Same logic at 3am or 3pm — no time-of-day gate.
    """
    cfg = cfg or ScanConfig()
    now = now or time.time()
    if gap_exploitation_active:
        return cfg.gap_active_interval_seconds
    if HOT.is_hot(now):
        return cfg.hot_interval_seconds
    return cfg.standard_interval_seconds


def record_opportunity_for_burst(now: Optional[float] = None) -> None:
    """Legacy name: record_scan_opportunity_for_hot_window."""
    HOT.record_opportunity(now or time.time())


def record_scan_opportunity_for_hot_window(now: Optional[float] = None) -> None:
    record_opportunity_for_burst(now)


def current_scan_interval_seconds(now: Optional[float] = None, cfg: Optional[ScanConfig] = None) -> float:
    return resolve_scan_interval_seconds(now=now, gap_exploitation_active=False, cfg=cfg)


def extract_snapshot_fields(m: MarketSnapshot) -> Dict[str, Any]:
    return {
        "market_id": m.market_id,
        "outlet": m.outlet,
        "yes_price": m.yes_price,
        "no_price": m.no_price,
        "volume_24h": m.volume_24h,
        "time_to_resolution_seconds": m.time_to_resolution_seconds,
        "resolution_criteria": m.resolution_criteria,
        "last_price_update_timestamp": m.last_price_update_timestamp,
        "underlying_data_if_available": m.underlying_data_if_available,
    }


@dataclass
class OutletRegistry:
    """All registered fetchers; unhealthy outlets skipped per cycle."""

    fetchers: List[OutletFetcher] = field(default_factory=list)
    last_health: Dict[str, str] = field(default_factory=dict)

    def register(self, f: OutletFetcher) -> None:
        self.fetchers.append(f)

    def scan_all(self) -> List[MarketSnapshot]:
        out: List[MarketSnapshot] = []
        for f in self.fetchers:
            name = getattr(f, "outlet_name", type(f).__name__)
            try:
                out.extend(f.fetch_binary_markets())
                self.last_health[name] = "ok"
            except Exception as exc:
                self.last_health[name] = f"error:{exc!s}"
        return out
