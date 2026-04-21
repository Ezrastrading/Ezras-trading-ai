"""Post-fee edge truth — rolling expectancy per edge_id (measurement only)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.nte.utils.atomic_json import atomic_write_json
from trading_ai.reality.paths import reality_data_dir

WINDOWS = (20, 50, 100)


def _expectancy_gross_net(
    gross: List[float],
    net: List[float],
) -> Tuple[float, float, float, float, float]:
    """Returns win_rate, avg_win, avg_loss, gross_expectancy, net_expectancy."""
    if not net:
        return 0.0, 0.0, 0.0, 0.0, 0.0
    if len(gross) != len(net):
        gross = net
    wins_idx = [i for i, x in enumerate(net) if x > 0]
    loss_idx = [i for i, x in enumerate(net) if x <= 0]
    n = len(net)
    win_rate = len(wins_idx) / n
    avg_win = sum(net[i] for i in wins_idx) / len(wins_idx) if wins_idx else 0.0
    avg_loss = sum(abs(net[i]) for i in loss_idx) / len(loss_idx) if loss_idx else 0.0
    gw = sum(gross[i] for i in wins_idx) / len(wins_idx) if wins_idx else 0.0
    gl = sum(abs(gross[i]) for i in loss_idx) / len(loss_idx) if loss_idx else 0.0
    gross_exp = (win_rate * gw) - ((1.0 - win_rate) * gl)
    net_exp = (win_rate * avg_win) - ((1.0 - win_rate) * avg_loss)
    return win_rate, avg_win, avg_loss, gross_exp, net_exp


@dataclass
class EdgeWindowStats:
    win_rate: float
    avg_win: float
    avg_loss: float
    gross_expectancy: float
    net_expectancy: float
    edge_status: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "win_rate": self.win_rate,
            "avg_win": self.avg_win,
            "avg_loss": self.avg_loss,
            "gross_expectancy": self.gross_expectancy,
            "net_expectancy": self.net_expectancy,
            "edge_status": self.edge_status,
        }


def _window_stats(gross: List[float], net: List[float]) -> EdgeWindowStats:
    wr, aw, al, gexp, nexp = _expectancy_gross_net(gross, net)
    status = "FALSE_EDGE" if nexp <= 0 else "REAL_EDGE"
    return EdgeWindowStats(
        win_rate=wr,
        avg_win=aw,
        avg_loss=al,
        gross_expectancy=gexp,
        net_expectancy=nexp,
        edge_status=status,
    )


def edge_truth_summary_path(path: Optional[Path] = None) -> Path:
    return (path or reality_data_dir()) / "edge_truth_summary.json"


class EdgeTruthEngine:
    """
    Tracks per-edge chronological gross/net trade outcomes and persists rolling-window summaries.
    """

    def __init__(self, *, data_dir: Optional[Path] = None) -> None:
        self._dir = data_dir or reality_data_dir()
        self._dir.mkdir(parents=True, exist_ok=True)
        self._edges: Dict[str, Dict[str, List[float]]] = {}
        self._load()

    def _load(self) -> None:
        p = edge_truth_summary_path(self._dir)
        if not p.is_file():
            return
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        edges = raw.get("edges") or {}
        for eid, blob in edges.items():
            hist = blob.get("history") or {}
            self._edges[eid] = {
                "gross": [float(x) for x in hist.get("gross", [])],
                "net": [float(x) for x in hist.get("net", [])],
            }

    def record_trade(
        self,
        edge_id: str,
        *,
        gross_pnl: float,
        net_pnl: float,
    ) -> Dict[str, Any]:
        eid = str(edge_id).strip() or "unknown"
        if eid not in self._edges:
            self._edges[eid] = {"gross": [], "net": []}
        self._edges[eid]["gross"].append(float(gross_pnl))
        self._edges[eid]["net"].append(float(net_pnl))
        summary = self._build_edge_entry(eid)
        self._persist()
        return summary

    def _windows_for(self, eid: str) -> Dict[str, EdgeWindowStats]:
        gross = self._edges.get(eid, {}).get("gross", [])
        net = self._edges.get(eid, {}).get("net", [])
        out: Dict[str, EdgeWindowStats] = {}
        for w in WINDOWS:
            g = gross[-w:] if gross else []
            n = net[-w:] if net else []
            key = str(w)
            out[key] = _window_stats(g, n) if n else _window_stats([], [])
        return out

    def _build_edge_entry(self, eid: str) -> Dict[str, Any]:
        windows: Dict[str, Any] = {}
        for k, st in self._windows_for(eid).items():
            windows[k] = st.to_dict()
        net_series = self._edges.get(eid, {}).get("net", [])
        gross_series = self._edges.get(eid, {}).get("gross", [])
        overall_status = "FALSE_EDGE"
        if net_series:
            chosen = False
            for w in reversed(WINDOWS):
                if len(net_series) >= w:
                    st = _window_stats(gross_series[-w:], net_series[-w:])
                    overall_status = st.edge_status
                    chosen = True
                    break
            if not chosen:
                st = _window_stats(gross_series, net_series)
                overall_status = st.edge_status
        return {
            "windows": windows,
            "trade_count": len(net_series),
            "edge_status": overall_status,
        }

    def _persist(self) -> None:
        edges_out: Dict[str, Any] = {}
        for eid in self._edges:
            edges_out[eid] = {
                "history": {
                    "gross": self._edges[eid]["gross"],
                    "net": self._edges[eid]["net"],
                },
                **self._build_edge_entry(eid),
            }
        payload = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "edges": edges_out,
        }
        atomic_write_json(edge_truth_summary_path(self._dir), payload)

    def summary_for_edge(self, edge_id: str) -> Dict[str, Any]:
        eid = str(edge_id).strip() or "unknown"
        if eid not in self._edges:
            return {"windows": {}, "trade_count": 0, "edge_status": "FALSE_EDGE"}
        return self._build_edge_entry(eid)

    def net_pnls(self, edge_id: str) -> List[float]:
        eid = str(edge_id).strip() or "unknown"
        return list(self._edges.get(eid, {}).get("net", []))
