"""
Process-wide safety guard: halt all trading on repeated losses, infra failures, latency, or API errors.

When triggered, writes ``shark/state/system_trading_halt.json``; operator must call
:func:`clear_trading_halt` (or remove the file) after review.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

from trading_ai.governance.storage_architecture import shark_state_path
from trading_ai.runtime_paths import ezras_runtime_root

logger = logging.getLogger(__name__)

HALT_FILENAME = "system_trading_halt.json"
STATE_FILENAME = "system_guard.json"
DEFAULT_LATENCY_WINDOW = 32
DEFAULT_API_WINDOW = 64


def trading_halt_path(*, runtime_root: Optional[Path] = None) -> Path:
    return shark_state_path(HALT_FILENAME, runtime_root=runtime_root)


def system_guard_state_path(*, runtime_root: Optional[Path] = None) -> Path:
    return shark_state_path(STATE_FILENAME, runtime_root=runtime_root)


@dataclass
class SystemGuardState:
    consecutive_losses: int = 0
    supabase_failure_streak: int = 0
    supabase_success_streak: int = 0
    api_errors_recent: Deque[float] = field(default_factory=lambda: deque(maxlen=DEFAULT_API_WINDOW))
    latency_samples_ms: Deque[float] = field(default_factory=lambda: deque(maxlen=DEFAULT_LATENCY_WINDOW))
    last_shutdown_reason: str = ""
    halted_at_unix: float = 0.0
    recent_trade_pnls: List[float] = field(default_factory=list)
    execution_anomaly_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "consecutive_losses": self.consecutive_losses,
            "supabase_failure_streak": self.supabase_failure_streak,
            "supabase_success_streak": self.supabase_success_streak,
            "api_errors_recent_ts": list(self.api_errors_recent),
            "latency_samples_ms": list(self.latency_samples_ms),
            "last_shutdown_reason": self.last_shutdown_reason,
            "halted_at_unix": self.halted_at_unix,
            "recent_trade_pnls": list(self.recent_trade_pnls)[-100:],
            "execution_anomaly_count": self.execution_anomaly_count,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SystemGuardState":
        st = cls(
            consecutive_losses=int(d.get("consecutive_losses") or 0),
            supabase_failure_streak=int(d.get("supabase_failure_streak") or 0),
            supabase_success_streak=int(d.get("supabase_success_streak") or 0),
            last_shutdown_reason=str(d.get("last_shutdown_reason") or ""),
            halted_at_unix=float(d.get("halted_at_unix") or 0.0),
            recent_trade_pnls=[float(x) for x in (d.get("recent_trade_pnls") or []) if x is not None][-100:],
            execution_anomaly_count=int(d.get("execution_anomaly_count") or 0),
        )
        st.api_errors_recent = deque(maxlen=DEFAULT_API_WINDOW)
        for t in d.get("api_errors_recent_ts") or []:
            try:
                st.api_errors_recent.append(float(t))
            except (TypeError, ValueError):
                continue
        st.latency_samples_ms = deque(maxlen=DEFAULT_LATENCY_WINDOW)
        for x in d.get("latency_samples_ms") or []:
            try:
                st.latency_samples_ms.append(float(x))
            except (TypeError, ValueError):
                continue
        return st


class SystemGuard:
    """Per-runtime-root guard; use :func:`get_system_guard` in the trading loop."""

    def __init__(self, *, runtime_root: Optional[Path] = None) -> None:
        self._runtime_root = Path(runtime_root).resolve() if runtime_root is not None else None
        self._halt_path = trading_halt_path(runtime_root=self._runtime_root)
        self._state_path = system_guard_state_path(runtime_root=self._runtime_root)
        self._state = self._load_state()

    def _load_state(self) -> SystemGuardState:
        p = self._state_path
        if not p.is_file():
            return SystemGuardState()
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            return SystemGuardState.from_dict(raw if isinstance(raw, dict) else {})
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.warning("system_guard state load failed: %s", exc)
            return SystemGuardState()

    def _persist(self) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(json.dumps(self._state.to_dict(), indent=2), encoding="utf-8")

    def _trigger_halt(self, reason: str) -> None:
        self._state.last_shutdown_reason = reason
        self._state.halted_at_unix = time.time()
        self._persist()
        payload = {
            "halted": True,
            "reason": reason,
            "ts_unix": self._state.halted_at_unix,
            "manual_reset_required": True,
            "clear_instructions": "Delete this file or call trading_ai.core.system_guard.clear_trading_halt()",
        }
        self._halt_path.parent.mkdir(parents=True, exist_ok=True)
        self._halt_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        logger.critical("SYSTEM GUARD HALT — %s — manual reset required", reason)
        try:
            from trading_ai.control.alerts import emit_alert

            emit_alert("CRITICAL", f"system_halt: {reason}")
        except Exception:
            pass

    def is_trading_halted(self) -> bool:
        return self._halt_path.is_file()

    def halt_reason_from_file(self) -> str:
        if not self._halt_path.is_file():
            return ""
        try:
            raw = json.loads(self._halt_path.read_text(encoding="utf-8"))
            return str((raw or {}).get("reason") or "unknown")
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return "halt_file_unreadable"

    def halt_now(self, reason: str) -> None:
        """Operator-style immediate halt (e.g. deployment failure-rate breach)."""
        self._trigger_halt(reason)

    def record_execution_anomaly(self, reason: str = "unknown") -> None:
        self._state.execution_anomaly_count += 1
        self._persist()
        halt_after = int((os.environ.get("SYSTEM_GUARD_EXECUTION_ANOMALY_COUNT_HALT") or "3").strip() or "3")
        if self._state.execution_anomaly_count >= halt_after:
            self._trigger_halt(f"execution_anomaly:{reason}")

    def record_closed_trade_pnl(self, pnl_usd: float) -> None:
        if pnl_usd < -1e-9:
            self._state.consecutive_losses += 1
        elif pnl_usd > 1e-9:
            self._state.consecutive_losses = 0
        self._state.recent_trade_pnls.append(float(pnl_usd))
        self._state.recent_trade_pnls = self._state.recent_trade_pnls[-100:]
        self._persist()

    def record_supabase_ok(self) -> None:
        self._state.supabase_success_streak += 1
        self._state.supabase_failure_streak = 0
        self._persist()

    def record_supabase_failure(self) -> None:
        self._state.supabase_failure_streak += 1
        self._state.supabase_success_streak = 0
        self._persist()
        if self._state.supabase_failure_streak in (1, 4, 8):
            try:
                from trading_ai.control.alerts import emit_alert

                emit_alert(
                    "WARNING",
                    f"supabase_failure streak={self._state.supabase_failure_streak}",
                )
            except Exception:
                pass

    def record_scan_latency_ms(self, ms: float) -> None:
        try:
            self._state.latency_samples_ms.append(float(ms))
        except (TypeError, ValueError):
            return
        self._persist()

    def record_api_error(self) -> None:
        self._state.api_errors_recent.append(time.time())
        self._persist()

    def _latency_spike(self) -> bool:
        samples = list(self._state.latency_samples_ms)
        if len(samples) < 5:
            return False
        thr = float((os.environ.get("SYSTEM_GUARD_LATENCY_SPIKE_MS") or "8000").strip() or "8000")
        return max(samples) > thr

    def _api_error_storm(self) -> bool:
        horizon = float((os.environ.get("SYSTEM_GUARD_API_ERROR_WINDOW_SEC") or "3600").strip() or "3600")
        now = time.time()
        cutoff = now - horizon
        recent = [t for t in self._state.api_errors_recent if t >= cutoff]
        limit = int((os.environ.get("SYSTEM_GUARD_API_ERROR_COUNT") or "25").strip() or "25")
        return len(recent) >= limit

    def should_shutdown(self) -> Tuple[bool, str]:
        """
        Returns ``(halt, reason)``. When True, trading must stop; reason is logged and stored.

        Checks (configurable via env): consecutive losses, Supabase streak, latency spike, API errors,
        and existing halt file.
        """
        if self.is_trading_halted():
            r = self.halt_reason_from_file()
            return True, f"trading_already_halted:{r}"

        loss_n = int((os.environ.get("SYSTEM_GUARD_CONSECUTIVE_LOSSES") or "3").strip() or "3")
        if self._state.consecutive_losses >= loss_n:
            msg = f"consecutive_losses>={loss_n}"
            self._trigger_halt(msg)
            return True, msg

        early_unstable = int((os.environ.get("SYSTEM_GUARD_SUPABASE_UNSTABLE_STREAK") or "4").strip() or "4")
        if self._state.supabase_failure_streak >= early_unstable:
            msg = "supabase_unstable"
            self._trigger_halt(msg)
            return True, msg

        sb_n = int((os.environ.get("SYSTEM_GUARD_SUPABASE_FAIL_STREAK") or "8").strip() or "8")
        if self._state.supabase_failure_streak >= sb_n:
            msg = f"supabase_failure_streak>={sb_n}"
            self._trigger_halt(msg)
            return True, msg

        try:
            from trading_ai.nte.databank.supabase_trade_sync import supabase_sync_rate_unhealthy

            if supabase_sync_rate_unhealthy():
                msg = "supabase_sync_rate_below_threshold"
                self._trigger_halt(msg)
                return True, msg
        except Exception:
            logger.debug("supabase sync rate check skipped", exc_info=True)

        pnls = self._state.recent_trade_pnls
        min_n = int((os.environ.get("EDGE_EXPECTANCY_HALT_MIN_TRADES") or "12").strip() or "12")
        if len(pnls) >= min_n:
            ewma = sum(pnls[-min_n:]) / float(min_n)
            if ewma < 0:
                msg = "negative_expectancy"
                self._trigger_halt(msg)
                return True, msg

        if self._latency_spike():
            msg = "latency_spike"
            self._trigger_halt(msg)
            return True, msg

        if self._api_error_storm():
            msg = "api_errors_threshold"
            self._trigger_halt(msg)
            return True, msg

        return False, ""

    def refresh_from_supabase_diagnostics(self, diag: Optional[Dict[str, Any]]) -> None:
        """Pass :func:`report_supabase_trade_sync_diagnostics` output; increments ok/fail streaks."""
        if not diag:
            return
        if not bool(diag.get("supabase_url_present")):
            return
        ok = bool(diag.get("client_init_ok") and diag.get("insert_probe_ok"))
        if ok:
            self.record_supabase_ok()
        else:
            self.record_supabase_failure()


_guard_by_root: Dict[str, SystemGuard] = {}


def get_system_guard(*, runtime_root: Optional[Path] = None) -> SystemGuard:
    """Return the guard for the resolved runtime root (isolated per ``EZRAS_RUNTIME_ROOT``)."""
    key = str(Path(runtime_root).resolve() if runtime_root is not None else ezras_runtime_root().resolve())
    g = _guard_by_root.get(key)
    if g is None:
        g = SystemGuard(runtime_root=Path(key))
        _guard_by_root[key] = g
    return g


def reset_system_guard_singletons_for_tests() -> None:
    """Test-only: clear cached guards (does not delete on-disk halt files)."""
    _guard_by_root.clear()


def clear_trading_halt(*, runtime_root: Optional[Path] = None) -> bool:
    """Operator reset after incident review. Returns True if a halt file was removed."""
    p = trading_halt_path(runtime_root=runtime_root)
    if not p.is_file():
        return False
    try:
        p.unlink()
    except OSError as exc:
        logger.warning("clear_trading_halt failed: %s", exc)
        return False
    g = get_system_guard(runtime_root=runtime_root)
    g._state.consecutive_losses = 0
    g._state.supabase_failure_streak = 0
    g._state.execution_anomaly_count = 0
    g._state.last_shutdown_reason = ""
    g._state.halted_at_unix = 0.0
    g._persist()
    logger.warning("Trading halt cleared by operator")
    return True
