"""Trade Intelligence Databank — after-trade pipeline (local + Supabase + verification)."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Mapping, Optional

from trading_ai.nte.databank.databank_health import refresh_health_from_verification, save_health
from trading_ai.nte.databank.databank_schema import row_for_supabase_trade_events
from trading_ai.nte.databank.local_trade_store import (
    append_jsonl_atomic,
    avenue_trade_events_path,
    ensure_seed_files,
    global_trade_events_path,
    load_all_trade_events,
    upsert_score_record,
)
from trading_ai.nte.databank.supabase_trade_sync import upsert_trade_event
from trading_ai.nte.databank.trade_event_writer import validate_and_build_record
from trading_ai.nte.databank.trade_summary_engine import (
    append_learning_hook,
    refresh_all_summaries,
    update_ceo_review_snapshot,
    update_goal_snapshot_hook,
)
from trading_ai.nte.databank.trade_verification_engine import record_trade_write_verification

logger = logging.getLogger(__name__)


class TradeIntelligenceDatabank:
    """Monitoring spine: one closed trade → scores, local truth, Supabase, summaries, verification."""

    def __init__(self, *, sync_summary_tables: Optional[bool] = None) -> None:
        if sync_summary_tables is None:
            sync_summary_tables = (os.environ.get("TRADE_DATABANK_SYNC_SUPABASE_SUMMARIES") or "").lower() in (
                "1",
                "true",
                "yes",
            )
        self._sync_summary_tables = sync_summary_tables

    def process_closed_trade(self, raw: Mapping[str, Any]) -> Dict[str, Any]:
        """
        Run the full after-trade pipeline. Returns result dict with ok, trade_id, stages, errors.
        Does not raise on validation failure — returns ok=False.
        """
        ensure_seed_files()
        stages: Dict[str, bool] = {}
        errors: List[str] = []

        merged, scores, verrs = validate_and_build_record(raw)
        if verrs:
            errors.extend(verrs)
            stages["validated"] = False
            self._finalize_verification(
                str(raw.get("trade_id") or "unknown"),
                stages,
                errors,
                partial_failure=True,
            )
            return {"ok": False, "trade_id": raw.get("trade_id"), "validation_errors": verrs, "stages": stages}

        stages["validated"] = True
        trade_id = str(merged["trade_id"])

        appended = append_jsonl_atomic(global_trade_events_path(), merged, trade_id=trade_id)
        stages["local_raw_event"] = appended
        if not appended:
            self._finalize_verification(trade_id, stages, ["duplicate_trade_id"], partial_failure=True)
            return {"ok": False, "trade_id": trade_id, "reason": "duplicate_trade_id", "stages": stages}

        ave_path = avenue_trade_events_path(str(merged["avenue_name"]))
        append_jsonl_atomic(ave_path, merged, trade_id=trade_id)

        upsert_score_record(trade_id, merged, scores, extra_meta=None)
        stages["local_score_record"] = True

        supabase_ok = upsert_trade_event(row_for_supabase_trade_events(merged, scores))
        stages["supabase_trade_events"] = supabase_ok
        if not supabase_ok:
            errors.append("supabase_upsert_failed")
        upsert_score_record(trade_id, merged, scores, extra_meta={"supabase_sync": bool(supabase_ok)})

        events = load_all_trade_events()
        refresh_all_summaries(events)
        stages["daily_summary_updated"] = True
        stages["weekly_summary_updated"] = True
        stages["monthly_summary_updated"] = True
        stages["strategy_summary_updated"] = True
        stages["avenue_summary_updated"] = True

        if self._sync_summary_tables:
            self._push_summaries_to_supabase()

        update_goal_snapshot_hook(events)
        stages["goal_snapshot_hook"] = True

        update_ceo_review_snapshot(events)
        stages["ceo_snapshot_hook"] = True

        append_learning_hook(merged, scores)
        stages["learning_hook"] = True

        partial = not supabase_ok
        self._finalize_verification(trade_id, stages, errors, partial_failure=partial)

        return {
            "ok": not partial,
            "trade_id": trade_id,
            "record": merged,
            "scores": scores,
            "stages": stages,
            "errors": errors,
        }

    def _push_summaries_to_supabase(self) -> None:
        try:
            from trading_ai.nte.databank.local_trade_store import (
                load_aggregate,
                path_daily_summary,
                path_avenue_performance,
                path_monthly_summary,
                path_strategy_performance,
                path_weekly_summary,
            )
            from trading_ai.nte.databank.supabase_trade_sync import sync_summary_batch

            combined: List[Mapping[str, Any]] = []
            for loader in (
                load_aggregate(path_daily_summary(), {}),
                load_aggregate(path_weekly_summary(), {}),
                load_aggregate(path_monthly_summary(), {}),
            ):
                combined.extend(list(loader.get("rollups") or []))
            sync_summary_batch("daily_trade_summary", combined, "summary_id")
            s = load_aggregate(path_strategy_performance(), {})
            sync_summary_batch("strategy_performance_summary", list(s.get("rows") or []), "strategy_summary_id")
            a = load_aggregate(path_avenue_performance(), {})
            sync_summary_batch("avenue_performance_summary", list(a.get("rows") or []), "avenue_summary_id")
        except Exception as exc:
            logger.warning("push summaries to supabase: %s", exc)

    def _finalize_verification(
        self,
        trade_id: str,
        stages: Mapping[str, bool],
        errors: List[str],
        *,
        partial_failure: bool,
    ) -> None:
        record_trade_write_verification(
            trade_id,
            stages,
            partial_failure=partial_failure,
            error_messages=errors,
            retry_status="none",
        )
        refresh_health_from_verification()
        if partial_failure:
            save_health("degraded", [f"pipeline:{trade_id}"] + errors)
        else:
            save_health("ok", [])


def process_closed_trade(raw: Mapping[str, Any]) -> Dict[str, Any]:
    """Module-level convenience."""
    return TradeIntelligenceDatabank().process_closed_trade(raw)
