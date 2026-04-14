"""
Canonical Telegram send + markdown audit log + idempotency for trade/cycle events.

All sends go through :func:`send_telegram_alert` in ``alerts.py`` (single HTTP path).
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.config import Settings

from trading_ai.automation.alerts import send_telegram_alert

_lock = threading.Lock()


def monorepo_root() -> Path:
    """Ezras monorepo root (parent of ``trading-ai/``)."""
    return Path(__file__).resolve().parents[4]


def telegram_log_path() -> Path:
    return monorepo_root() / "logs" / "telegram_log.md"


def telegram_idempotency_path() -> Path:
    return monorepo_root() / "logs" / "telegram_idempotency.json"


def _ensure_logs_dir() -> None:
    telegram_log_path().parent.mkdir(parents=True, exist_ok=True)


def _load_idempotency() -> Dict[str, Any]:
    p = telegram_idempotency_path()
    if not p.is_file():
        return {"keys": {}}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and isinstance(raw.get("keys"), dict):
            return raw
    except (OSError, json.JSONDecodeError):
        pass
    return {"keys": {}}


def _save_idempotency(data: Dict[str, Any]) -> None:
    _ensure_logs_dir()
    p = telegram_idempotency_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(p)


def append_telegram_log(
    *,
    event: str,
    detail: str,
    ok: bool,
    skipped_duplicate: bool = False,
) -> None:
    """Append one line block to ``/logs/telegram_log.md`` (monorepo root)."""
    _ensure_logs_dir()
    ts = datetime.now(timezone.utc).isoformat()
    flag = "SKIP_DUP" if skipped_duplicate else ("OK" if ok else "FAIL")
    block = (
        f"\n## {ts} [{flag}] {event}\n\n"
        f"{detail.strip()}\n\n"
        f"---\n"
    )
    with open(telegram_log_path(), "a", encoding="utf-8") as f:
        f.write(block)


def send_telegram_with_idempotency(
    settings: Settings,
    text: str,
    *,
    dedupe_key: Optional[str],
    event_label: str,
) -> Dict[str, Any]:
    """
    Send Telegram message; optional dedupe by ``dedupe_key`` (persists forever in JSON).

    Never raises. Returns ``{sent, skipped_duplicate, ok, error?}``.
    """
    if dedupe_key:
        with _lock:
            store = _load_idempotency()
            keys: Dict[str, Any] = store.setdefault("keys", {})
            if dedupe_key in keys:
                append_telegram_log(
                    event=event_label,
                    detail=f"dedupe_key={dedupe_key!r} (already sent at {keys[dedupe_key]})",
                    ok=True,
                    skipped_duplicate=True,
                )
                return {"sent": False, "skipped_duplicate": True, "ok": True}

    ok = False
    err: Optional[str] = None
    try:
        ok = bool(send_telegram_alert(settings, text))
    except Exception as exc:
        err = str(exc)
        ok = False

    detail = (text[:1500] + ("…" if len(text) > 1500 else "")) + (f"\n\nerror: {err}" if err else "")
    append_telegram_log(event=event_label, detail=detail, ok=ok, skipped_duplicate=False)

    if ok and dedupe_key:
        with _lock:
            store = _load_idempotency()
            store.setdefault("keys", {})[dedupe_key] = datetime.now(timezone.utc).isoformat()
            _save_idempotency(store)

    return {"sent": ok, "skipped_duplicate": False, "ok": ok, "error": err}
