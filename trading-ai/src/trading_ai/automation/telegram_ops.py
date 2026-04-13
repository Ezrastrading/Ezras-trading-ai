"""
Telegram send + idempotency — minimal stub for workspace / tests.

Full implementation lives in the complete Ezras monorepo; this never raises.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.config import Settings

_lock = threading.Lock()


def _runtime_root() -> Path:
    raw = (os.environ.get("EZRAS_RUNTIME_ROOT") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (Path.home() / "ezras-runtime").resolve()


def _idempotency_path() -> Path:
    return _runtime_root() / "logs" / "telegram_idempotency.json"


def _load_keys() -> Dict[str, Any]:
    p = _idempotency_path()
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and isinstance(raw.get("keys"), dict):
            return dict(raw["keys"])
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _save_keys(keys: Dict[str, Any]) -> None:
    p = _idempotency_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"keys": keys}, indent=2), encoding="utf-8")
    tmp.replace(p)


def send_telegram_with_idempotency(
    settings: Settings,
    text: str,
    *,
    dedupe_key: Optional[str],
    event_label: str,
) -> Dict[str, Any]:
    """
    Stub: no outbound HTTP. Honors dedupe under ``EZRAS_RUNTIME_ROOT`` / logs.
    Never raises. Returns ``{sent, skipped_duplicate, ok, error?}``.
    """
    _ = settings, text, event_label
    if dedupe_key:
        with _lock:
            keys = _load_keys()
            if dedupe_key in keys:
                return {"sent": False, "skipped_duplicate": True, "ok": True}
            keys[dedupe_key] = datetime.now(timezone.utc).isoformat()
            _save_keys(keys)
    return {"sent": True, "skipped_duplicate": False, "ok": True, "error": None}
