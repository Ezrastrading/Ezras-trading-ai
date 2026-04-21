"""
Post-trade intelligence (fee-aware, evidence-first).

This module is **descriptive**: it classifies each closed trade using net-after-fees truth, detects
recurring harmful patterns (timeout-loss clusters, fee-drag flips), and emits **bounded** artifacts:

- append-only per-trade classification JSONL (dedup by trade_id)
- rolling cluster snapshot JSON (local truth)
- optional tickets for CEO/review pipelines (no trading side effects)
- optional lesson/progression updates (local truth files only)

It must never claim profit improvements or change live trading permissions.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from trading_ai.intelligence.tickets.detect import detect_from_execution_event
from trading_ai.intelligence.tickets.models import Ticket, TicketSeverity, TicketType
from trading_ai.intelligence.tickets.store import append_ticket
from trading_ai.nte.databank.local_trade_store import (
    append_jsonl_atomic,
    databank_memory_root,
)

logger = logging.getLogger(__name__)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _num(x: Any, d: float = 0.0) -> float:
    try:
        return float(x) if x is not None else d
    except (TypeError, ValueError):
        return d


def _parse_ts(raw: Any) -> Optional[datetime]:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _close_reason(ev: Mapping[str, Any]) -> str:
    # Canonical source in databank is exit_reason; fall back to common close dict keys.
    for k in ("exit_reason", "close_reason", "reason_closed", "exit_reason_code"):
        v = ev.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def _is_timeout_reason(reason: str) -> bool:
    r = (reason or "").strip().lower()
    if not r:
        return False
    return ("timeout" in r) or (r in ("max_hold_timeout", "max_hold", "hold_timeout"))


def _infer_notional_usd(ev: Mapping[str, Any]) -> Optional[float]:
    """
    Best-effort notional for fee drag bps.

    Priority:
    - explicit notional fields when present
    - spot quote legs (quote_qty_buy/sell)
    - capital_allocated / size_dollars style fields (may be present on raw merges)
    """
    for k in ("notional_usd", "trade_size_usd", "size_usd", "capital_allocated", "size_dollars"):
        v = ev.get(k)
        if v is not None:
            try:
                n = abs(float(v))
                if n > 0:
                    return n
            except (TypeError, ValueError):
                pass
    qb = ev.get("quote_qty_buy")
    qs = ev.get("quote_qty_sell")
    try:
        qbv = abs(float(qb)) if qb is not None else 0.0
        qsv = abs(float(qs)) if qs is not None else 0.0
        n = max(qbv, qsv)
        if n > 0:
            return n
    except (TypeError, ValueError):
        pass
    return None


@dataclass(frozen=True)
class PostTradeClassification:
    trade_id: str
    timestamp_close: str
    avenue_id: str
    avenue_name: str
    strategy_id: str
    asset: str
    close_reason: str
    hold_seconds: float
    gross_pnl_usd: float
    net_pnl_usd: float
    fee_cost_usd: float
    fee_drag_bps: Optional[float]
    fee_drag_usd: float
    profitable_pre_fee: bool
    profitable_post_fee: bool
    flipped_negative_by_fees: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trade_id": self.trade_id,
            "timestamp_close": self.timestamp_close,
            "avenue_id": self.avenue_id,
            "avenue_name": self.avenue_name,
            "strategy_id": self.strategy_id,
            "asset": self.asset,
            "close_reason": self.close_reason,
            "hold_seconds": self.hold_seconds,
            "gross_pnl_usd": self.gross_pnl_usd,
            "net_pnl_usd": self.net_pnl_usd,
            "fee_cost_usd": self.fee_cost_usd,
            "fee_drag_bps": self.fee_drag_bps,
            "fee_drag_usd": self.fee_drag_usd,
            "profitable_pre_fee": self.profitable_pre_fee,
            "profitable_post_fee": self.profitable_post_fee,
            "flipped_negative_by_fees": self.flipped_negative_by_fees,
        }


def classify_closed_trade(ev: Mapping[str, Any]) -> PostTradeClassification:
    tid = str(ev.get("trade_id") or "").strip()
    ts = str(ev.get("timestamp_close") or ev.get("timestamp") or "").strip()
    gross = _num(ev.get("gross_pnl"))
    net = _num(ev.get("net_pnl"))
    fees = _num(ev.get("fees_paid"))
    hold = _num(ev.get("hold_seconds"))
    reason = _close_reason(ev)
    notional = _infer_notional_usd(ev)
    fee_bps = (fees / notional * 10000.0) if (notional is not None and notional > 0) else None
    pre_fee = gross > 0
    post_fee = net > 0
    flipped = gross > 0 and net < 0
    return PostTradeClassification(
        trade_id=tid,
        timestamp_close=ts,
        avenue_id=str(ev.get("avenue_id") or ""),
        avenue_name=str(ev.get("avenue_name") or ""),
        strategy_id=str(ev.get("strategy_id") or ""),
        asset=str(ev.get("asset") or ""),
        close_reason=reason,
        hold_seconds=hold,
        gross_pnl_usd=gross,
        net_pnl_usd=net,
        fee_cost_usd=fees,
        fee_drag_bps=fee_bps,
        fee_drag_usd=fees,
        profitable_pre_fee=bool(pre_fee),
        profitable_post_fee=bool(post_fee),
        flipped_negative_by_fees=bool(flipped),
    )


def _classifications_jsonl_path(root: Optional[Path] = None) -> Path:
    r = root or databank_memory_root()
    return r / "post_trade_classifications.jsonl"


def _clusters_snapshot_path(root: Optional[Path] = None) -> Path:
    r = root or databank_memory_root()
    return r / "post_trade_clusters.json"


def _load_recent_classifications(
    *,
    root: Optional[Path],
    since_days: float,
    max_rows: int = 5000,
) -> List[Dict[str, Any]]:
    p = _classifications_jsonl_path(root)
    if not p.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
    out: List[Dict[str, Any]] = []
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            ts = _parse_ts(rec.get("timestamp_close"))
            if ts is None:
                continue
            if ts < cutoff:
                continue
            out.append(rec)
            if len(out) >= max_rows:
                break
    return out


def detect_clusters(
    recent: List[Mapping[str, Any]],
    *,
    window_trades: int = 12,
    min_timeout_losers: int = 3,
    min_fee_flip_losers: int = 3,
) -> Dict[str, Any]:
    """
    Cluster rules are intentionally simple and conservative:
    - timeout_loss_cluster: at least N timeout exits with net<0 in last window_trades
    - fee_drag_cluster: at least N "flipped by fees" in last window_trades
    - degrading_account_hint: recent net sum negative AND (timeout cluster OR fee flip cluster)
    """
    tail = list(recent)[-window_trades:] if window_trades > 0 else list(recent)
    timeout_losers = [
        r
        for r in tail
        if _is_timeout_reason(str(r.get("close_reason") or ""))
        and _num(r.get("net_pnl_usd")) < 0
    ]
    fee_flips = [r for r in tail if bool(r.get("flipped_negative_by_fees")) and _num(r.get("net_pnl_usd")) < 0]
    net_sum = sum(_num(r.get("net_pnl_usd")) for r in tail)
    timeout_cluster = len(timeout_losers) >= min_timeout_losers
    fee_cluster = len(fee_flips) >= min_fee_flip_losers
    degrading = net_sum < 0 and (timeout_cluster or fee_cluster)
    return {
        "as_of_utc": _iso_now(),
        "window_trades": window_trades,
        "net_sum_window_usd": round(net_sum, 6),
        "timeout_loss_cluster": {
            "active": bool(timeout_cluster),
            "count": len(timeout_losers),
            "trade_ids": [str(r.get("trade_id") or "") for r in timeout_losers][-20:],
            "rule": f"count(timeout & net<0) >= {min_timeout_losers} in last {window_trades} trades",
        },
        "fee_drag_flip_cluster": {
            "active": bool(fee_cluster),
            "count": len(fee_flips),
            "trade_ids": [str(r.get("trade_id") or "") for r in fee_flips][-20:],
            "rule": f"count(flipped_negative_by_fees & net<0) >= {min_fee_flip_losers} in last {window_trades} trades",
        },
        "degrading_account_hint": {
            "active": bool(degrading),
            "rule": "net_sum_window_usd < 0 AND (timeout_loss_cluster OR fee_drag_flip_cluster)",
        },
        "honesty": "Clusters are heuristic labels on internal trade classifications; they are not market-proof.",
    }


def _write_clusters_snapshot(snapshot: Dict[str, Any], *, root: Optional[Path]) -> None:
    p = _clusters_snapshot_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _maybe_emit_cluster_tickets(
    *,
    merged_trade_event: Mapping[str, Any],
    classification: PostTradeClassification,
    clusters: Dict[str, Any],
    runtime_root: Optional[Path] = None,
) -> List[str]:
    """
    Emit tickets only when a cluster is active (prevents spam).
    Tickets are appended to the intelligence store and can feed CEO/review automation.
    """
    created: List[str] = []
    sessions = []
    aid = str(merged_trade_event.get("avenue_id") or "")
    gate = str(merged_trade_event.get("gate_id") or merged_trade_event.get("trading_gate") or "")
    venue = str(merged_trade_event.get("avenue_name") or merged_trade_event.get("venue") or "")
    product_id = str(merged_trade_event.get("asset") or merged_trade_event.get("market_id") or "")
    trade_id = classification.trade_id
    evidence = [f"databank_trade_event:{trade_id}", f"post_trade_classification:{trade_id}"]

    # Timeout-loss cluster
    tcl = (clusters.get("timeout_loss_cluster") or {}).get("active")
    if tcl:
        ev = {
            "trigger": "timeout_loss_cluster",
            "avenue_id": aid,
            "gate_id": gate,
            "venue": venue,
            "product_id": product_id,
            "market_id": str(merged_trade_event.get("market_id") or ""),
            "source_component": "post_trade_intelligence",
            "human_summary": f"Recurring timeout exits producing net losses (trade_id={trade_id}).",
            "machine_summary": json.dumps(
                {"clusters": clusters.get("timeout_loss_cluster"), "classification": classification.to_dict()},
                default=str,
            )[:8000],
            "evidence_refs": evidence,
            "confidence": 0.55,
        }
        # classify_signal will treat "timeout" as timeout_incident; detect will set CEO review required at medium+
        tickets = detect_from_execution_event(ev)
        for t in tickets:
            t.ticket_type = TicketType.timeout_incident
            t.severity = TicketSeverity.medium
            t.category = "timeout_loss_cluster"
            t.ceo_review_required = True
            t.learning_update_required = True
            t.extra.setdefault("post_trade_tags", [])
            t.extra["post_trade_tags"] = list(
                {
                    *list(t.extra.get("post_trade_tags") or []),
                    "timeout_loss_cluster",
                    "repeated_small_loss_pattern",
                    "exit_latency_mismatch",
                }
            )
            append_ticket(t, runtime_root=runtime_root)
            created.append(t.ticket_id)
            try:
                from trading_ai.intelligence.tickets.ceo_review import should_emit_ceo_session, write_ceo_session_files

                if should_emit_ceo_session(t):
                    sessions.append(write_ceo_session_files(t, runtime_root=runtime_root))
            except Exception:
                pass

    # Fee-drag flip cluster
    fcl = (clusters.get("fee_drag_flip_cluster") or {}).get("active")
    if fcl:
        ev = {
            "trigger": "fee_drag_flip_cluster",
            "avenue_id": aid,
            "gate_id": gate,
            "venue": venue,
            "product_id": product_id,
            "market_id": str(merged_trade_event.get("market_id") or ""),
            "source_component": "post_trade_intelligence",
            "human_summary": f"Fees repeatedly flip trades negative post-close (trade_id={trade_id}).",
            "machine_summary": json.dumps(
                {"clusters": clusters.get("fee_drag_flip_cluster"), "classification": classification.to_dict()},
                default=str,
            )[:8000],
            "evidence_refs": evidence,
            "confidence": 0.55,
        }
        tickets = detect_from_execution_event(ev)
        for t in tickets:
            t.ticket_type = TicketType.strategy_degradation
            t.severity = TicketSeverity.medium
            t.category = "fee_drag_dominant"
            t.ceo_review_required = True
            t.learning_update_required = True
            t.extra.setdefault("post_trade_tags", [])
            t.extra["post_trade_tags"] = list(
                {
                    *list(t.extra.get("post_trade_tags") or []),
                    "fee_drag_dominant",
                    "entry_not_worth_fee",
                    "low_edge_after_costs",
                }
            )
            append_ticket(t, runtime_root=runtime_root)
            created.append(t.ticket_id)
            try:
                from trading_ai.intelligence.tickets.ceo_review import should_emit_ceo_session, write_ceo_session_files

                if should_emit_ceo_session(t):
                    sessions.append(write_ceo_session_files(t, runtime_root=runtime_root))
            except Exception:
                pass

    if sessions:
        try:
            from trading_ai.intelligence.tickets.ceo_review import append_daily_ceo_rollup

            append_daily_ceo_rollup(sessions, runtime_root=runtime_root)
        except Exception:
            pass

    return created


def _maybe_update_lessons_and_progression(
    *,
    classification: PostTradeClassification,
    clusters: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Update local lesson/progression truth files. Best-effort; never raises.
    """
    out: Dict[str, Any] = {"lessons_updated": False, "progression_updated": False}
    try:
        from trading_ai.shark.lessons import upsert_auto_lesson

        tags: List[str] = []
        if bool((clusters.get("timeout_loss_cluster") or {}).get("active")):
            tags.extend(["timeout_loss_cluster", "repeated_small_loss_pattern", "exit_latency_mismatch"])
        if bool((clusters.get("fee_drag_flip_cluster") or {}).get("active")):
            tags.extend(["fee_drag_dominant", "entry_not_worth_fee", "low_edge_after_costs"])
        if classification.flipped_negative_by_fees:
            tags.extend(["fee_drag_dominant", "entry_not_worth_fee"])
        if _is_timeout_reason(classification.close_reason) and classification.net_pnl_usd < 0:
            tags.extend(["timeout_loss_cluster"])
        tags = sorted(set(tags))
        if tags:
            msg = (
                f"Post-trade pattern detected: tags={tags}. "
                f"Close reason={classification.close_reason or 'unknown'}. "
                f"Net={classification.net_pnl_usd:+.4f} Gross={classification.gross_pnl_usd:+.4f} Fees={classification.fee_cost_usd:.4f}. "
                "Action: tighten fee-aware entry thresholds and review timeout exit policy before scaling."
            )
            upsert_auto_lesson(
                platform=classification.avenue_name or "both",
                lesson=msg,
                cost=float(min(0.0, classification.net_pnl_usd)),
                category="post_trade_intelligence",
                tags=tags,
                dedupe_key="|".join(tags)[:80],
            )
            out["lessons_updated"] = True
    except Exception:
        pass

    try:
        from trading_ai.shark.progression import record_post_trade_intelligence

        record_post_trade_intelligence(
            trade_id=classification.trade_id,
            net_pnl_usd=classification.net_pnl_usd,
            close_reason=classification.close_reason,
            tags=list((clusters.get("timeout_loss_cluster") or {}).get("active") and ["timeout_loss_cluster"] or [])
            + list((clusters.get("fee_drag_flip_cluster") or {}).get("active") and ["fee_drag_dominant"] or []),
            clusters=clusters,
        )
        out["progression_updated"] = True
    except Exception:
        pass

    return out


def run_post_trade_intelligence(
    *,
    merged_trade_event: Mapping[str, Any],
    all_events: List[Mapping[str, Any]],
    runtime_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Main entrypoint called by TradeIntelligenceDatabank after a closed trade is validated/stored.
    """
    tid = str(merged_trade_event.get("trade_id") or "").strip()
    if not tid:
        return {"ok": False, "error": "missing_trade_id"}

    cls = classify_closed_trade(merged_trade_event)
    # Append classification as an append-only record (dedup by trade_id).
    appended = False
    try:
        appended = append_jsonl_atomic(
            _classifications_jsonl_path(None),
            cls.to_dict(),
            trade_id=tid,
        )
    except Exception as exc:
        logger.debug("post_trade classification append failed: %s", exc)

    recent_cls = _load_recent_classifications(root=None, since_days=14.0)
    clusters = detect_clusters(recent_cls)
    try:
        _write_clusters_snapshot(clusters, root=None)
    except Exception as exc:
        logger.debug("post_trade clusters snapshot write failed: %s", exc)

    ticket_ids: List[str] = []
    try:
        if bool((clusters.get("timeout_loss_cluster") or {}).get("active")) or bool(
            (clusters.get("fee_drag_flip_cluster") or {}).get("active")
        ):
            ticket_ids = _maybe_emit_cluster_tickets(
                merged_trade_event=merged_trade_event,
                classification=cls,
                clusters=clusters,
                runtime_root=runtime_root,
            )
    except Exception as exc:
        logger.debug("post_trade cluster ticket emission skipped: %s", exc)

    lessons_prog = _maybe_update_lessons_and_progression(classification=cls, clusters=clusters)

    # Minimal “review readiness” touch file: signals to review/CEO layers that new evidence exists.
    review_touch: Dict[str, Any] = {}
    try:
        root = databank_memory_root()
        p = root / "post_trade_review_inputs.json"
        review_touch = {
            "updated_at_utc": _iso_now(),
            "latest_trade_id": tid,
            "latest_net_pnl_usd": cls.net_pnl_usd,
            "clusters": {
                "timeout_loss_cluster_active": bool((clusters.get("timeout_loss_cluster") or {}).get("active")),
                "fee_drag_flip_cluster_active": bool((clusters.get("fee_drag_flip_cluster") or {}).get("active")),
                "degrading_account_hint_active": bool((clusters.get("degrading_account_hint") or {}).get("active")),
            },
            "ticket_ids_emitted": ticket_ids,
            "honesty": "Inputs are generated from internal post-trade classification + clusters; no profit claims.",
        }
        p.write_text(json.dumps(review_touch, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except Exception:
        review_touch = {}

    return {
        "ok": True,
        "trade_id": tid,
        "classification_appended": appended,
        "clusters_active": {
            "timeout_loss_cluster": bool((clusters.get("timeout_loss_cluster") or {}).get("active")),
            "fee_drag_flip_cluster": bool((clusters.get("fee_drag_flip_cluster") or {}).get("active")),
            "degrading_account_hint": bool((clusters.get("degrading_account_hint") or {}).get("active")),
        },
        "ticket_ids_emitted": ticket_ids,
        "lessons_progression": lessons_prog,
        "review_inputs_written": bool(review_touch),
    }

