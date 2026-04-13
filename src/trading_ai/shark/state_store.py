"""Persistent Shark state — survives restarts. ~/ezras-runtime/shark/state/*.json"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.governance.storage_architecture import shark_state_backups_dir, shark_state_path
from trading_ai.shark.dotenv_load import load_shark_dotenv

load_shark_dotenv()
from trading_ai.shark.capital_phase import YEAR_END_TARGET_DEFAULT, detect_phase
from trading_ai.shark.state import BAYES, MANDATE


@dataclass
class CapitalRecord:
    current_capital: float = 50.0
    starting_capital: float = 50.0
    peak_capital: float = 50.0
    phase: str = "phase_1"
    last_updated: str = ""
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    monthly_start_capital: float = 50.0
    monthly_target: float = 375.0
    year_end_target: float = YEAR_END_TARGET_DEFAULT
    acceleration_mode: bool = True
    last_trade_unix: Optional[float] = None


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def require_ezras_runtime_root_configured() -> None:
    """Raise if `EZRAS_RUNTIME_ROOT` is unset (daemon / live entry)."""
    from trading_ai.shark.required_env import require_ezras_runtime_root

    require_ezras_runtime_root()


def capital_path() -> Path:
    """Uses ``EZRAS_RUNTIME_ROOT`` from the environment when set (see ``governance.storage_architecture``)."""
    return shark_state_path("capital.json")


def positions_path() -> Path:
    return shark_state_path("positions.json")


def gaps_path() -> Path:
    return shark_state_path("gaps.json")


def bayesian_path() -> Path:
    return shark_state_path("bayesian.json")


def wallets_path() -> Path:
    return shark_state_path("wallets.json")


def load_wallets_registry() -> Dict[str, Any]:
    p = wallets_path()
    if not p.is_file():
        return {"tracked_wallets": [], "last_full_scan": None}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            raw.setdefault("tracked_wallets", [])
            return raw
    except (OSError, json.JSONDecodeError):
        pass
    return {"tracked_wallets": [], "last_full_scan": None}


def save_wallets_registry(data: Dict[str, Any]) -> None:
    wallets_path().write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_capital() -> CapitalRecord:
    p = capital_path()
    if not p.is_file():
        return CapitalRecord(last_updated=_iso())
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return CapitalRecord(last_updated=_iso())
        # tolerate legacy "capital" key
        if "current_capital" not in raw and "capital" in raw:
            raw["current_capital"] = raw.pop("capital")
        rec = CapitalRecord(
            current_capital=float(raw.get("current_capital", 50)),
            starting_capital=float(raw.get("starting_capital", 50)),
            peak_capital=float(raw.get("peak_capital", raw.get("current_capital", 50))),
            phase=str(raw.get("phase", "phase_1")),
            last_updated=str(raw.get("last_updated", _iso())),
            total_trades=int(raw.get("total_trades", 0)),
            winning_trades=int(raw.get("winning_trades", 0)),
            losing_trades=int(raw.get("losing_trades", 0)),
            monthly_start_capital=float(raw.get("monthly_start_capital", 50)),
            monthly_target=float(raw.get("monthly_target", 375)),
            year_end_target=float(raw.get("year_end_target", YEAR_END_TARGET_DEFAULT)),
            acceleration_mode=bool(raw.get("acceleration_mode", True)),
            last_trade_unix=float(raw["last_trade_unix"]) if raw.get("last_trade_unix") is not None else None,
        )
        _maybe_halt_drawdown(rec)
        return rec
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return CapitalRecord(last_updated=_iso())


def save_capital(rec: CapitalRecord) -> None:
    rec.phase = detect_phase(rec.current_capital).value
    rec.last_updated = _iso()
    p = capital_path()
    p.write_text(json.dumps(asdict(rec), indent=2), encoding="utf-8")
    _maybe_halt_drawdown(rec)


def _maybe_halt_drawdown(rec: CapitalRecord) -> None:
    if rec.peak_capital <= 0:
        return
    dd = (rec.peak_capital - rec.current_capital) / rec.peak_capital
    if dd > 0.40:
        MANDATE.execution_paused = True


def load_positions() -> Dict[str, Any]:
    p = positions_path()
    if not p.is_file():
        return {"open_positions": [], "pending_resolution": [], "history": []}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            raw.setdefault("history", [])
            return raw
        return {"open_positions": [], "pending_resolution": [], "history": []}
    except (OSError, json.JSONDecodeError):
        return {"open_positions": [], "pending_resolution": [], "history": []}


def save_positions(data: Dict[str, Any]) -> None:
    positions_path().write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_gaps() -> Dict[str, Any]:
    p = gaps_path()
    if not p.is_file():
        return {"gaps_under_observation": [], "confirmed_gaps": [], "closed_gaps": []}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {"gaps_under_observation": [], "confirmed_gaps": [], "closed_gaps": []}
    except (OSError, json.JSONDecodeError):
        return {"gaps_under_observation": [], "confirmed_gaps": [], "closed_gaps": []}


def save_gaps(data: Dict[str, Any]) -> None:
    gaps_path().write_text(json.dumps(data, indent=2), encoding="utf-8")


def save_bayesian_snapshot() -> None:
    payload = {
        "strategy_weights": dict(BAYES.strategy_weights),
        "hunt_weights": dict(BAYES.hunt_weights),
        "outlet_weights": dict(BAYES.outlet_weights),
        "hour_edge_quality": {str(k): v for k, v in BAYES.hour_edge_quality.items()},
        "trade_count": BAYES.trade_count,
        "updated_at": _iso(),
    }
    bayesian_path().write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_bayesian_into_memory() -> None:
    p = bayesian_path()
    if not p.is_file():
        return
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(raw.get("strategy_weights"), dict):
            BAYES.strategy_weights.update({k: float(v) for k, v in raw["strategy_weights"].items()})
        if isinstance(raw.get("hunt_weights"), dict):
            BAYES.hunt_weights.update({k: float(v) for k, v in raw["hunt_weights"].items()})
        if isinstance(raw.get("outlet_weights"), dict):
            BAYES.outlet_weights.update({k: float(v) for k, v in raw["outlet_weights"].items()})
        if isinstance(raw.get("hour_edge_quality"), dict):
            for k, v in raw["hour_edge_quality"].items():
                BAYES.hour_edge_quality[int(k)] = float(v)
        BAYES.trade_count = int(raw.get("trade_count", 0))
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        pass


def backup_all_state_files() -> Path:
    """Daily backup to state/backups/."""
    dest_dir = shark_state_backups_dir()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    sub = dest_dir / stamp
    sub.mkdir(parents=True, exist_ok=True)
    for name in ("capital.json", "positions.json", "gaps.json", "bayesian.json", "wallets.json"):
        p = shark_state_path(name)
        if p.is_file():
            shutil.copy2(p, sub / name)
    return sub


def integrity_check_or_restore() -> bool:
    """If capital.json corrupted, try latest backup."""
    p = capital_path()
    if p.is_file():
        try:
            json.loads(p.read_text(encoding="utf-8"))
            return True
        except json.JSONDecodeError:
            pass
    backups = sorted(shark_state_backups_dir().glob("*"), reverse=True)
    for b in backups:
        cand = b / "capital.json"
        if cand.is_file():
            try:
                json.loads(cand.read_text(encoding="utf-8"))
                shutil.copy2(cand, p)
                return True
            except (OSError, json.JSONDecodeError):
                continue
    return False


def apply_win_loss_to_capital(pnl_dollars: float) -> CapitalRecord:
    rec = load_capital()
    rec.current_capital = max(0.0, rec.current_capital + pnl_dollars)
    rec.peak_capital = max(rec.peak_capital, rec.current_capital)
    rec.total_trades += 1
    if pnl_dollars >= 0:
        rec.winning_trades += 1
    else:
        rec.losing_trades += 1
    rec.last_trade_unix = time.time()
    save_capital(rec)
    return rec
