"""Outlet fetcher base — retries, health, no time-based gating."""

from __future__ import annotations

import random
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from typing import Any, Callable, List, TypeVar

from trading_ai.shark.models import MarketSnapshot

T = TypeVar("T")


def retry_backoff(
    fn: Callable[[], T],
    *,
    retries: int = 3,
    base_delay: float = 0.5,
) -> T:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            return fn()
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            last = e
            time.sleep(base_delay * (2**attempt) + random.random() * 0.1)
    if last:
        raise last
    raise RuntimeError("retry_backoff")


class BaseOutletFetcher(ABC):
    outlet_name: str = "base"

    @abstractmethod
    def fetch_binary_markets(self) -> List[MarketSnapshot]:
        raise NotImplementedError

    def http_get_json(self, url: str, timeout: float = 20.0) -> Any:
        import json

        def _req() -> Any:
            req = urllib.request.Request(url, headers={"User-Agent": "EzrasShark/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))

        return retry_backoff(_req)
