"""
Instant post-trade: Telegram (existing stack) + runtime logging under ``EZRAS_RUNTIME_ROOT`` / default repo parent.

Idempotency: ``placed:{trade_id}``, ``closed:{trade_id}`` via :func:`send_telegram_with_idempotency`.
Never raises from Phase 2 hooks.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

from trading_ai.automation.telegram_ops import send_telegram_with_idempotency
from trading_ai.automation.telegram_trade_events import (
    format_trade_closed_message,
    format_trade_placed_message,
)
from trading_ai.runtime_paths import ezras_runtime_root

if TYPE_CHECKING:
    from trading_ai.config import Settings

logger = logging.getLogger(__name__)
_lock = threading.Lock()


def runtime_root() -> Path:
    """Canonical runtime root — see :mod:`trading_ai.runtime_paths`."""
    return ezras_runtime_root()


def post_trade_log_path() -> Path:
    return runtime_root() / "logs" / "post_trade_log.md"


def manifest_path() -> Path:
    return runtime_root() / "state" / "post_trade_manifest.json"


def vault_touch_path() -> Path:
    return runtime_root() / "state" / "vault_trade_touch.jsonl"


def _ensure_dirs() -> None:
    post_trade_log_path().parent.mkdir(parents=True, exist_ok=True)
    manifest_path().parent.mkdir(parents=True, exist_ok=True)


def _settings(s: Optional["Settings"]):
    if s is not None:
        return s
    from trading_ai.config import get_settings

    return get_settings()


def _validate_placed(trade: Dict[str, Any]) -> Tuple[bool, str]:
    if not str(trade.get("trade_id") or "").strip():
        return False, "missing trade_id"
    return True, ""


def _validate_closed(trade: Dict[str, Any]) -> Tuple[bool, str]:
    if not str(trade.get("trade_id") or "").strip():
        return False, "missing trade_id"
    if str(trade.get("result") or "") not in ("win", "loss"):
        return False, "result must be win or loss"
    return True, ""


def _append_post_trade_log(entry: Dict[str, Any]) -> None:
    try:
        _ensure_dirs()
        ts = entry.get("timestamp") or datetime.now(timezone.utc).isoformat()
        block = (
            f"\n## {ts}\n\n```json\n{json.dumps(entry, indent=2, default=str)}\n```\n\n---\n"
        )
        with open(post_trade_log_path(), "a", encoding="utf-8") as f:
            f.write(block)
    except Exception as exc:
        logger.warning("post_trade_log append failed: %s", exc)


def _vault_touch(trade: Dict[str, Any], event_type: str, *, skip: bool) -> Dict[str, Any]:
    if skip:
        return {"status": "skipped", "reason": "duplicate_or_invalid"}
    try:
        _ensure_dirs()
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "trade_id": str(trade.get("trade_id") or ""),
            "market": trade.get("market"),
            "result": trade.get("result"),
        }
        p = vault_touch_path()
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")
        return {"status": "ok", "path": str(p)}
    except Exception as exc:
        return {"status": "failed", "error": str(exc)}


def _merge_manifest(event_type: str, trade_id: str, tg: Dict[str, Any], vault: Dict[str, Any]) -> None:
    try:
        with _lock:
            _ensure_dirs()
            p = manifest_path()
            data: Dict[str, Any] = {}
            if p.is_file():
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    data = {}
            data.setdefault("version", 1)
            stats = data.setdefault("stats", {})
            k = "placed_total" if event_type == "placed" else "closed_total"
            stats[k] = int(stats.get(k) or 0) + 1
            if tg.get("skipped_duplicate"):
                stats["telegram_duplicate_skips"] = int(stats.get("telegram_duplicate_skips") or 0) + 1
            data["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
            data["last_event"] = {
                "event_type": event_type,
                "trade_id": trade_id,
                "telegram_sent": bool(tg.get("sent")),
                "telegram_skipped_duplicate": bool(tg.get("skipped_duplicate")),
            }
            data["last_vault_touch"] = vault
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(p)
    except Exception as exc:
        logger.warning("post_trade manifest update failed: %s", exc)


def execute_post_trade_placed(
    settings: Optional["Settings"],
    trade: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    ts = datetime.now(timezone.utc).isoformat()
    out: Dict[str, Any] = {
        "timestamp": ts,
        "event_type": "placed",
        "trade_id": None,
        "status": "failed",
        "telegram": {},
        "vault": {},
        "runtime_root": str(runtime_root()),
        "error": None,
    }
    if not trade:
        out["error"] = "empty trade"
        _append_post_trade_log(out)
        return out

    ok, err = _validate_placed(trade)
    tid = str(trade.get("trade_id") or "").strip()
    out["trade_id"] = tid or None
    if not ok:
        out["error"] = err
        _append_post_trade_log(out)
        return out

    try:
        from trading_ai.automation.position_sizing_policy import enrich_open_payload_with_sizing_preview

        enrich_open_payload_with_sizing_preview(trade)
    except Exception as exc:
        logger.warning("enrich_open_payload_with_sizing_preview failed (non-fatal): %s", exc)

    out["position_sizing"] = trade.get("position_sizing_meta")
    out["risk_bucket_at_open"] = trade.get("risk_bucket_at_open")

    s = _settings(settings)
    text = format_trade_placed_message(trade)
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

    if dup:
        out["status"] = "skipped_duplicate"
    elif tg.get("sent"):
        out["status"] = "sent"
    elif tg.get("error"):
        out["status"] = "failed"
        out["error"] = tg.get("error")
    else:
        out["status"] = "processed_partial"

    out["telegram"] = {k: tg.get(k) for k in ("sent", "skipped_duplicate", "ok", "error") if k in tg}
    out["vault"] = vault
    _append_post_trade_log(out)
    _merge_manifest("placed", tid, tg, vault)
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
        "runtime_root": str(runtime_root()),
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
        _append_post_trade_log(out)
        return out

    try:
        from trading_ai.automation.close_trade_reconciliation import reconcile_closed_trade_execution

        out["execution_close_reconciliation"] = reconcile_closed_trade_execution(trade)
    except Exception as exc:
        logger.warning("reconcile_closed_trade_execution failed (non-fatal): %s", exc)

    try:
        from trading_ai.automation.risk_bucket import record_closed_trade

        record_closed_trade(trade)
    except Exception as exc:
        logger.warning("record_closed_trade failed (non-fatal): %s", exc)
    try:
        from trading_ai.automation.strategy_risk_bucket import record_strategy_closed_trade

        record_strategy_closed_trade(trade)
    except Exception as exc:
        logger.warning("record_strategy_closed_trade failed (non-fatal): %s", exc)
    try:
        from trading_ai.risk.hard_lockouts import update_lockout_state_from_closed_trade

        update_lockout_state_from_closed_trade(trade)
    except Exception as exc:
        logger.warning("update_lockout_state_from_closed_trade failed (non-fatal): %s", exc)
    try:
        from trading_ai.execution.execution_reconciliation import get_execution_reconciliation_status
        from trading_ai.analysis.trade_quality_score import score_closed_trade

        tid = str(trade.get("trade_id") or "").strip()
        rec = (get_execution_reconciliation_status(trade_id=tid).get("trade") or {}) if tid else {}
        out["trade_quality"] = score_closed_trade(trade, reconciliation=rec if isinstance(rec, dict) else {})
    except Exception as exc:
        logger.warning("score_closed_trade failed (non-fatal): %s", exc)
    try:
        from trading_ai.automation.risk_bucket import get_account_risk_bucket

        out["risk_mode_after_close"] = get_account_risk_bucket({"phase": "closed", "trade": trade})
        b0 = trade.get("risk_bucket_at_open")
        if b0 and str(b0) != str(out.get("risk_mode_after_close")):
            out["bucket_change"] = f"{b0} → {out['risk_mode_after_close']}"
    except Exception as exc:
        logger.warning("risk_mode_after_close snapshot failed (non-fatal): %s", exc)

    s = _settings(settings)
    text = format_trade_closed_message(trade)
    logger.info(
        "[post_trade_closed] BEFORE telegram send: trade_id=%s result=%s payout_dollars=%s market=%s",
        tid,
        trade.get("result"),
        trade.get("payout_dollars"),
        trade.get("market"),
    )
    try:
        tg = send_telegram_with_idempotency(
            s,
            text,
            dedupe_key=f"closed:{tid}",
            event_label="trade_closed",
        )
    except Exception as exc:
        tg = {"sent": False, "skipped_duplicate": False, "ok": False, "error": str(exc)}
    logger.info(
        "[post_trade_closed] AFTER telegram send: trade_id=%s sent=%s skipped_duplicate=%s error=%s",
        tid,
        tg.get("sent"),
        tg.get("skipped_duplicate"),
        tg.get("error"),
    )

    dup = bool(tg.get("skipped_duplicate"))
    vault = _vault_touch(trade, "closed", skip=dup)

    if dup:
        out["status"] = "skipped_duplicate"
    elif tg.get("sent"):
        out["status"] = "sent"
    elif tg.get("error"):
        out["status"] = "failed"
        out["error"] = tg.get("error")
    else:
        out["status"] = "processed_partial"

    out["telegram"] = {k: tg.get(k) for k in ("sent", "skipped_duplicate", "ok", "error") if k in tg}
    out["vault"] = vault
    _append_post_trade_log(out)
    _merge_manifest("closed", tid, tg, vault)
    return out
