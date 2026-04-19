"""
Rolling performance stats per strategy_id; promotion / demotion and JSON snapshot.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.nte.paths import nte_root


def strategy_scores_path() -> Path:
    """Runtime ``strategy_scores.json`` under NTE data root."""
    p = nte_root() / "strategy_scores.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


@dataclass
class StrategyTradeRecord:
    strategy_id: str
    pnl: float
    slippage: float
    latency_ms: float
    success: bool
    ts: float = field(default_factory=time.time)


@dataclass
class StrategyRollingStats:
    strategy_id: str
    n: int = 0
    wins: int = 0
    win_rate: float = 0.0
    avg_pnl: float = 0.0
    sharpe_proxy: float = 0.0
    consistency_score: float = 0.0
    disabled: bool = False
    priority_boost: float = 1.0
    recent_pnls: List[float] = field(default_factory=list)


class StrategyValidationEngine:
    """
    Tracks trades, rolling metrics, and writes ``strategy_scores.json``.

    Demotion: win_rate < 40% with at least ``min_trades_for_disable`` samples.
    Promotion: consistent positive recent PnL → priority_boost > 1.
    """

    window: int = 50
    min_trades_for_disable: int = 8
    win_rate_disable_threshold: float = 0.40
    consistency_window: int = 10
    sharpe_eps: float = 1e-9

    def __init__(self, *, path: Optional[Path] = None) -> None:
        self._path = path or strategy_scores_path()
        self._trades: Dict[str, List[StrategyTradeRecord]] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.is_file():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return
        tr = raw.get("trades") if isinstance(raw, dict) else None
        if not isinstance(tr, dict):
            return
        for sid, rows in tr.items():
            if not isinstance(rows, list):
                continue
            out: List[StrategyTradeRecord] = []
            for row in rows[-200:]:
                if not isinstance(row, dict):
                    continue
                try:
                    out.append(
                        StrategyTradeRecord(
                            strategy_id=str(row.get("strategy_id") or sid),
                            pnl=float(row.get("pnl") or 0.0),
                            slippage=float(row.get("slippage") or 0.0),
                            latency_ms=float(row.get("latency_ms") or 0.0),
                            success=bool(row.get("success")),
                            ts=float(row.get("ts") or time.time()),
                        )
                    )
                except (TypeError, ValueError):
                    continue
            self._trades[str(sid)] = out

    def record_trade(
        self,
        strategy_id: str,
        *,
        pnl: float,
        slippage: float = 0.0,
        latency_ms: float = 0.0,
        success: Optional[bool] = None,
    ) -> None:
        sid = str(strategy_id or "unknown")
        if success is None:
            success = float(pnl) > 0.0
        rec = StrategyTradeRecord(
            strategy_id=sid,
            pnl=float(pnl),
            slippage=float(slippage),
            latency_ms=float(latency_ms),
            success=bool(success),
        )
        self._trades.setdefault(sid, []).append(rec)
        self._trades[sid] = self._trades[sid][-400:]
        self._persist()

    def _rolling_pnls(self, strategy_id: str) -> List[float]:
        rows = self._trades.get(strategy_id, [])
        return [float(r.pnl) for r in rows[-self.window :]]

    def _stats(self, strategy_id: str) -> StrategyRollingStats:
        sid = str(strategy_id)
        pnls = self._rolling_pnls(sid)
        n = len(pnls)
        wins = sum(1 for x in pnls if x > 0)
        wr = (wins / n) if n else 0.0
        avg = sum(pnls) / n if n else 0.0
        if n >= 2:
            m = avg
            var = sum((x - m) ** 2 for x in pnls) / (n - 1)
            std = math.sqrt(max(var, 0.0))
            sharpe = m / (std + self.sharpe_eps)
        else:
            sharpe = 0.0
        recent = pnls[-self.consistency_window :]
        pos_frac = sum(1 for x in recent if x > 0) / len(recent) if recent else 0.0
        consistency = pos_frac * (1.0 if avg >= 0 else 0.35)

        disabled = False
        if n >= self.min_trades_for_disable and wr < self.win_rate_disable_threshold:
            disabled = True

        priority_boost = 1.0
        if n >= 5 and wr >= 0.55 and avg > 0 and consistency >= 0.6:
            priority_boost = 1.15
        if disabled:
            priority_boost = 0.0

        return StrategyRollingStats(
            strategy_id=sid,
            n=n,
            wins=wins,
            win_rate=wr,
            avg_pnl=avg,
            sharpe_proxy=sharpe,
            consistency_score=consistency,
            disabled=disabled,
            priority_boost=priority_boost,
            recent_pnls=list(pnls[-20:]),
        )

    def score_strategy(self, strategy_id: str) -> float:
        """
        Composite score in ``[0, 1]`` — higher is better; zero when disabled.
        """
        s = self._stats(str(strategy_id))
        if s.disabled or s.n == 0:
            return 0.0 if s.disabled else 0.5
        # Blend win rate, normalized sharpe proxy, consistency
        sp = max(-3.0, min(3.0, s.sharpe_proxy))
        sp01 = (sp + 3.0) / 6.0
        score = 0.45 * s.win_rate + 0.35 * sp01 + 0.20 * s.consistency_score
        return max(0.0, min(1.0, score))

    def is_strategy_disabled(self, strategy_id: str) -> bool:
        return self._stats(str(strategy_id)).disabled

    def priority_multiplier(self, strategy_id: str) -> float:
        return float(self._stats(str(strategy_id)).priority_boost)

    def snapshot(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "ts": time.time(),
            "strategies": {},
        }
        for sid in sorted(self._trades.keys()):
            st = self._stats(sid)
            out["strategies"][sid] = {
                **asdict(st),
                "score": self.score_strategy(sid),
            }
        return out

    def _persist(self) -> None:
        payload = {
            "ts": time.time(),
            "trades": {k: [asdict(x) for x in v[-400:]] for k, v in self._trades.items()},
            "strategies": {},
        }
        for sid in self._trades:
            st = self._stats(sid)
            payload["strategies"][sid] = {**asdict(st), "score": self.score_strategy(sid)}
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError:
            pass


def strategy_preflight_ok(
    engine: Optional[StrategyValidationEngine],
    strategy_id: str,
) -> bool:
    """If no engine, allow; otherwise require non-disabled strategy."""
    if engine is None:
        return True
    return not engine.is_strategy_disabled(strategy_id)


_sve_instance: Optional[StrategyValidationEngine] = None


def get_strategy_validation_engine() -> StrategyValidationEngine:
    """Process-wide cached engine so JSON is not re-read on every order."""
    global _sve_instance
    if _sve_instance is None:
        _sve_instance = StrategyValidationEngine()
    return _sve_instance
