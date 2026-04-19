"""
Cross-venue portfolio view: PnL and capital allocation per avenue, with periodic rebalance.

State is persisted to ``shark/state/portfolio_state.json`` under ``EZRAS_RUNTIME_ROOT``.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.governance.storage_architecture import shark_state_path

logger = logging.getLogger(__name__)

DEFAULT_AVENUES = ("kalshi", "coinbase", "polymarket", "manifold")


def portfolio_state_path() -> Path:
    return shark_state_path("portfolio_state.json")


@dataclass
class PortfolioState:
    """Fractions sum to 1.0; PnL is cumulative realized USD per avenue."""

    total_capital_usd: float = 0.0
    cumulative_pnl_by_avenue: Dict[str, float] = field(default_factory=dict)
    capital_fraction_by_avenue: Dict[str, float] = field(default_factory=dict)
    last_rebalance_unix: float = 0.0
    updated_unix: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_capital_usd": self.total_capital_usd,
            "cumulative_pnl_by_avenue": dict(self.cumulative_pnl_by_avenue),
            "capital_fraction_by_avenue": dict(self.capital_fraction_by_avenue),
            "last_rebalance_unix": self.last_rebalance_unix,
            "updated_unix": self.updated_unix,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PortfolioState":
        return cls(
            total_capital_usd=float(d.get("total_capital_usd") or 0.0),
            cumulative_pnl_by_avenue={str(k): float(v) for k, v in (d.get("cumulative_pnl_by_avenue") or {}).items()},
            capital_fraction_by_avenue={
                str(k): float(v) for k, v in (d.get("capital_fraction_by_avenue") or {}).items()
            },
            last_rebalance_unix=float(d.get("last_rebalance_unix") or 0.0),
            updated_unix=float(d.get("updated_unix") or 0.0),
        )


def _ensure_equal_weights(avenues: Tuple[str, ...]) -> Dict[str, float]:
    n = max(len(avenues), 1)
    w = 1.0 / float(n)
    return {a: w for a in avenues}


class PortfolioEngine:
    """
    Tracks per-avenue cumulative PnL and capital weights; :meth:`rebalance` shifts weight
    toward the best-performing venue (highest cumulative PnL).
    """

    def __init__(self, *, state_path: Optional[Path] = None, avenues: Tuple[str, ...] = DEFAULT_AVENUES) -> None:
        self._path = state_path or portfolio_state_path()
        self._avenues = tuple(a.strip().lower() for a in avenues if a.strip())
        self._state = self._load()

    def _load(self) -> PortfolioState:
        p = self._path
        if not p.is_file():
            st = PortfolioState(
                capital_fraction_by_avenue=_ensure_equal_weights(self._avenues),
                updated_unix=time.time(),
            )
            self._save(st)
            return st
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            st = PortfolioState.from_dict(raw if isinstance(raw, dict) else {})
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.warning("portfolio_state load failed (%s), using defaults", exc)
            st = PortfolioState(capital_fraction_by_avenue=_ensure_equal_weights(self._avenues))
        # Ensure every configured avenue exists
        for a in self._avenues:
            st.cumulative_pnl_by_avenue.setdefault(a, 0.0)
            st.capital_fraction_by_avenue.setdefault(
                a, 1.0 / max(len(self._avenues), 1)
            )
        self._normalize_fractions(st)
        return st

    def _normalize_fractions(self, st: PortfolioState) -> None:
        keys = [a for a in self._avenues if a in st.capital_fraction_by_avenue]
        if not keys:
            st.capital_fraction_by_avenue = _ensure_equal_weights(self._avenues)
            return
        s = sum(max(0.0, float(st.capital_fraction_by_avenue[k])) for k in keys)
        if s <= 0:
            st.capital_fraction_by_avenue = _ensure_equal_weights(self._avenues)
            return
        for k in keys:
            st.capital_fraction_by_avenue[k] = max(0.0, float(st.capital_fraction_by_avenue[k])) / s

    def _save(self, st: Optional[PortfolioState] = None) -> None:
        st = st or self._state
        st.updated_unix = time.time()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(st.to_dict(), indent=2), encoding="utf-8")

    @property
    def state(self) -> PortfolioState:
        return self._state

    def capital_usd_for_avenue(self, avenue: str) -> float:
        """Nominal USD allocated to one avenue from total capital and fraction."""
        a = str(avenue).strip().lower()
        frac = float(self._state.capital_fraction_by_avenue.get(a, 0.0))
        return max(0.0, float(self._state.total_capital_usd) * frac)

    def pnl_for_avenue(self, avenue: str) -> float:
        return float(self._state.cumulative_pnl_by_avenue.get(str(avenue).strip().lower(), 0.0))

    def record_realized_pnl(self, avenue: str, pnl_delta_usd: float) -> None:
        a = str(avenue).strip().lower()
        self._state.cumulative_pnl_by_avenue[a] = float(self._state.cumulative_pnl_by_avenue.get(a, 0.0)) + float(
            pnl_delta_usd
        )
        self._save()

    def set_total_capital(self, total_usd: float) -> None:
        self._state.total_capital_usd = max(0.0, float(total_usd))
        self._save()

    def rebalance(self, *, shift: float = 0.08, min_fraction: float = 0.05) -> Dict[str, Any]:
        """
        Move ``shift`` of total weight from weaker venues toward the single best cumulative PnL avenue.

        ``min_fraction`` prevents any venue from going to zero (operational diversification floor).
        """
        try:
            from trading_ai.core.system_guard import get_system_guard

            halt, why = get_system_guard().should_shutdown()
            if halt:
                logger.critical("rebalance blocked by system guard: %s", why)
                return {"ok": False, "reason": "system_guard", "detail": why}
        except Exception:
            logger.debug("system guard pre-rebalance skipped", exc_info=True)

        st = self._state
        if len(self._avenues) < 2:
            self._save()
            return {"ok": True, "reason": "single_avenue_noop", "fractions": dict(st.capital_fraction_by_avenue)}

        scores = {a: float(st.cumulative_pnl_by_avenue.get(a, 0.0)) for a in self._avenues}
        best = max(scores, key=lambda k: scores[k])
        others = [a for a in self._avenues if a != best]
        if not others:
            return {"ok": True, "reason": "no_peers", "fractions": dict(st.capital_fraction_by_avenue)}

        fr = {a: float(st.capital_fraction_by_avenue.get(a, 0.0)) for a in self._avenues}
        total_shift = 0.0
        for o in others:
            take = min(shift / max(len(others), 1), max(0.0, fr[o] - min_fraction))
            take = max(0.0, take)
            fr[o] -= take
            total_shift += take
        fr[best] = fr.get(best, 0.0) + total_shift
        st.capital_fraction_by_avenue = fr
        self._normalize_fractions(st)
        st.last_rebalance_unix = time.time()
        self._save()
        logger.info(
            "portfolio rebalance: best=%s scores=%s fractions=%s",
            best,
            scores,
            st.capital_fraction_by_avenue,
        )
        return {
            "ok": True,
            "best_avenue": best,
            "scores": scores,
            "fractions": dict(st.capital_fraction_by_avenue),
        }


def maybe_rebalance_if_due(
    *,
    interval_sec: float = 3600.0,
    engine: Optional[PortfolioEngine] = None,
) -> Optional[Dict[str, Any]]:
    """Run :meth:`PortfolioEngine.rebalance` at most once per ``interval_sec``."""
    eng = engine or PortfolioEngine()
    now = time.time()
    if now - float(eng.state.last_rebalance_unix or 0.0) < float(interval_sec):
        return None
    return eng.rebalance()
