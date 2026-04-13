"""
Instant post-trade layer: Telegram + ezras-runtime manifest + post_trade_log (non-blocking).

Does not replace morning/evening cycles. Uses existing :func:`send_telegram_with_idempotency`.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, TYPE_CHECKING

from trading_ai.automation.telegram_ops import send_telegram_with_idempotency
from trading_ai.automation.telegram_trade_events import (
    format_trade_closed_message,
    format_trade_placed_message,
)

if TYPE_CHECKING:
    from trading_ai.config import Settings

logger = logging.getLogger(__name__)


def _settings(s: Optional["Settings"]):
    if s is not None:
        return s
    from trading_ai.config import get_settings

    return get_settings()

_lock = threading.Lock()

_DEFAULT_RUNTIME = Path.home() / "ezras-runtime"


def runtime_root() -> Path:
    raw = (os.environ.get("EZRAS_RUNTIME_ROOT") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return _DEFAULT_RUNTIME.resolve()


def post_trade_log_path() -> Path:
    return runtime_root() / "logs" / "post_trade_log.md"


def manifest_path() -> Path:
    return runtime_root() / "state" / "post_trade_manifest.json"


def vault_touch_log_path() -> Path:
    return runtime_root() / "state" / "vault_trade_touch.jsonl"


def _ensure_runtime_dirs() -> None:
    root = runtime_root()
    (root / "logs").mkdir(parents=True, exist_ok=True)
    (root / "state").mkdir(parents=True, exist_ok=True)


def _validate_placed(trade: Dict[str, Any]) -> Tuple[bool, str]:
    tid = str(trade.get("trade_id") or "").strip()
    if not tid:
        return False, "missing trade_id"
    return True, ""


def _validate_closed(trade: Dict[str, Any]) -> Tuple[bool, str]:
    tid = str(trade.get("trade_id") or "").strip()
    if not tid:
        return False, "missing trade_id"
    if str(trade.get("result") or "") not in ("win", "loss"):
        return False, "result must be win or loss for closed trigger"
    return True, ""


def _load_manifest() -> Dict[str, Any]:
    p = manifest_path()
    if not p.is_file():
        return {
            "version": 1,
            "stats": {
                "placed_fired": 0,
                "closed_fired": 0,
                "telegram_skipped_duplicate": 0,
            },
        }
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            raw.setdefault("version", 1)
            raw.setdefault("stats", {})
            return raw
    except (OSError, json.JSONDecodeError):
        pass
    return {"version": 1, "stats": {}}


def _save_manifest(data: Dict[str, Any]) -> None:
    _ensure_runtime_dirs()
    p = manifest_path()
    data["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(p)


def _append_post_trade_log(entry: Dict[str, Any]) -> None:
    """Append structured JSON block to post_trade_log.md."""
    try:
        _ensure_runtime_dirs()
        ts = entry.get("timestamp") or datetime.now(timezone.utc).isoformat()
        block = (
            f"\n## {ts}\n\n"
            f"```json\n{json.dumps(entry, indent=2, default=str)}\n```\n\n---\n"
        )
        with open(post_trade_log_path(), "a", encoding="utf-8") as f:
            f.write(block)
    except Exception as exc:
        logger.warning("post_trade_log append failed: %s", exc)


def _vault_touch(trade: Dict[str, Any], event_type: str, *, skip: bool) -> Dict[str, Any]:
    """Single-trade hint for downstream vault/morning merge — never raises."""
    if skip:
        return {"status": "skipped", "reason": "duplicate_or_invalid"}
    try:
        _ensure_runtime_dirs()
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "trade_id": str(trade.get("trade_id") or ""),
            "market": trade.get("market"),
            "result": trade.get("result"),
        }
        p = vault_touch_log_path()
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")
        return {"status": "ok", "path": str(p)}
    except Exception as exc:
        return {"status": "failed", "error": str(exc)}


def _merge_manifest(
    event_type: str,
    trade_id: str,
    telegram: Dict[str, Any],
    vault: Dict[str, Any],
    validation_ok: bool,
) -> None:
    try:
        with _lock:
            m = _load_manifest()
            stats = m.setdefault("stats", {})
            key = "placed_fired" if event_type == "placed" else "closed_fired"
            stats[key] = int(stats.get(key) or 0) + 1
            if telegram.get("skipped_duplicate"):
                stats["telegram_skipped_duplicate"] = int(stats.get("telegram_skipped_duplicate") or 0) + 1

            last = {
                "trade_id": trade_id,
                "at": datetime.now(timezone.utc).isoformat(),
                "event_type": event_type,
                "telegram_sent": bool(telegram.get("sent")),
                "telegram_skipped_duplicate": bool(telegram.get("skipped_duplicate")),
                "validation_ok": validation_ok,
            }
            if event_type == "placed":
                m["last_placed"] = last
            else:
                m["last_closed"] = last
            m["last_event"] = last
            m["last_vault_touch"] = vault
            _save_manifest(m)
    except Exception as exc:
        logger.warning("post_trade manifest update failed: %s", exc)


def execute_post_trade_placed(
    settings: Optional["Settings"],
    trade: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Primary entry: placed. Never raises."""
    ts = datetime.now(timezone.utc).isoformat()
    out: Dict[str, Any] = {
        "timestamp": ts,
        "event_type": "placed",
        "trade_id": None,
        "status": "failed",
        "telegram": {},
        "vault": {},
        "error": None,
    }
    if not trade:
        out["error"] = "empty trade"
        out["status"] = "failed"
        _append_post_trade_log(out)
        return out

    ok, err = _validate_placed(trade)
    tid = str(trade.get("trade_id") or "").strip()
    out["trade_id"] = tid or None
    if not ok:
        out["error"] = err
        out["status"] = "failed"
        _append_post_trade_log(out)
        return out

    s = _settings(settings)
    text = format_trade_placed_message(trade)
    tg: Dict[str, Any] = {}
    try:
        tg = send_telegram_with_idempotency(
            s,
            text,
            dedupe_key=f"placed:{tid}",
            event_label="trade_placed",
        )
    except Exception as exc:
        tg = {"sent": False, "skipped_duplicate": False, "ok": False, "error": str(exc)}

    dup = bool(tg.get("skipped_duplicate"))
    vault = _vault_touch(trade, "placed", skip=dup)

    if tg.get("skipped_duplicate"):
        out["status"] = "skipped_duplicate"
    elif tg.get("sent") or tg.get("ok"):
        out["status"] = "sent"
    elif tg.get("error"):
        out["status"] = "failed"
        out["error"] = tg.get("error")
    else:
        out["status"] = "processed_partial"

    out["telegram"] = {
        "summary": "sent" if tg.get("sent") else ("skipped_duplicate" if dup else ("failed" if not tg.get("ok") else "unknown")),
        **{k: v for k, v in tg.items() if k in ("sent", "skipped_duplicate", "ok", "error")},
    }
    out["vault"] = vault
    _append_post_trade_log(out)
    _merge_manifest("placed", tid, tg, vault, True)
    return out


def execute_post_trade_closed(
    settings: Optional["Settings"],
    trade: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    ts = datetime.now(timezone.utc).isoformat()
    out: Dict[str, Any] = {
        "timestamp": ts,
        "event_type": "closed",
        "trade_id": None,
        "status": "failed",
        "telegram": {},
        "vault": {},
        "error": None,
    }
    if not trade:
        out["error"] = "empty trade"
        _append_post_trade_log(out)
        return out

    ok, err = _validate_closed(trade)
    tid = str(trade.get("trade_id") or "").strip()
    out["trade_id"] = tid or None
    if not ok:
        out["error"] = err
        out["status"] = "failed"
        _append_post_trade_log(out)
        return out

    try:
        from trading_ai.automation.risk_bucket import record_closed_trade

        record_closed_trade(trade)
    except Exception as exc:
        logger.warning("record_closed_trade failed (non-fatal): %s", exc)

    s = _settings(settings)
    text = format_trade_closed_message(trade)
    tg: Dict[str, Any] = {}
    try:
        tg = send_telegram_with_idempotency(
            s,
            text,
            dedupe_key=f"closed:{tid}",
            event_label="trade_closed",
        )
    except Exception as exc:
        tg = {"sent": False, "skipped_duplicate": False, "ok": False, "error": str(exc)}

    dup = bool(tg.get("skipped_duplicate"))
    vault = _vault_touch(trade, "closed", skip=dup)

    if tg.get("skipped_duplicate"):
        out["status"] = "skipped_duplicate"
    elif tg.get("sent") or tg.get("ok"):
        out["status"] = "sent"
    elif tg.get("error"):
        out["status"] = "failed"
        out["error"] = tg.get("error")
    else:
        out["status"] = "processed_partial"

    out["telegram"] = {
        "summary": "sent" if tg.get("sent") else ("skipped_duplicate" if dup else ("failed" if not tg.get("ok") else "unknown")),
        **{k: v for k, v in tg.items() if k in ("sent", "skipped_duplicate", "ok", "error")},
    }
    out["vault"] = vault
    _append_post_trade_log(out)
    _merge_manifest("closed", tid, tg, vault, True)
    return out


def execute_from_file(event: str, path: Path) -> Dict[str, Any]:
    """File-based trigger: JSON object in file."""
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("JSON root must be an object")
    if event == "placed":
        return execute_post_trade_placed(None, data)
    if event == "closed":
        return execute_post_trade_closed(None, data)
    raise ValueError("event must be placed or closed")
