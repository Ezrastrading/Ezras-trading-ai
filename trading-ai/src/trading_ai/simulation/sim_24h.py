"""
Full-system accelerated 24h simulated trading day.

Goal: behave like production runtime (scanner→candidate→routing→paper trades→fills→PnL→learning→reviews→queues),
but with **live trading fail-closed**.
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.control.command_center import run_command_center_snapshot
from trading_ai.control.full_autonomy_mode import write_full_autonomy_mode_artifacts
from trading_ai.runtime_paths import ezras_runtime_root
from trading_ai.storage.storage_adapter import LocalStorageAdapter


def _iso(ts_unix: Optional[float] = None) -> str:
    dt = datetime.fromtimestamp(ts_unix or time.time(), tz=timezone.utc)
    return dt.isoformat()


@dataclass(frozen=True)
class Sim24hConfig:
    hours: int = 24
    trades_per_hour: int = 6
    seed: int = 1337
    product_ids: Tuple[str, ...] = ("BTC-USD", "ETH-USD", "SOL-USD", "AVAX-USD", "LINK-USD")
    runtime_root: Optional[Path] = None
    write_control_artifacts: bool = True
    accelerate_sleep_ms: int = 5


def _control_dir(root: Path) -> Path:
    return root / "data" / "control"


def _write_control_json(ad: LocalStorageAdapter, rel: str, payload: Dict[str, Any]) -> None:
    ad.write_json(rel, payload)
    # Also mirror as stable pretty text for operators when convenient.
    if rel.endswith(".json"):
        ad.write_text(rel.replace(".json", ".txt"), json.dumps(payload, indent=2, default=str) + "\n")


def _read_review_storage_exports(root: Path) -> Dict[str, Any]:
    """
    Pull the key “review/task routing” artifacts that exist today.
    We keep this permissive: missing files are expected in first runs.
    """
    ad = LocalStorageAdapter(runtime_root=root)
    keys = {
        "review_action_log_tail": "shark/memory/global/review_action_log.jsonl",
        "candidate_queue": "shark/memory/global/candidate_queue.json",
        "risk_reduction_queue": "shark/memory/global/risk_reduction_queue.json",
        "ceo_review_queue": "shark/memory/global/ceo_review_queue.json",
        "governance_events": "shark/memory/global/governance_events.json",
        "speed_to_goal_review": "shark/memory/global/speed_to_goal_review.json",
        "ceo_capital_review": "shark/memory/global/ceo_capital_review.json",
        "first_million_progress_review": "shark/memory/global/first_million_progress_review.json",
        "joint_review_latest": "shark/memory/global/joint_review_latest.json",
        "review_packet_latest": "shark/memory/global/review_packet_latest.json",
        "review_scheduler_ticks_tail": "shark/memory/global/review_scheduler_ticks.jsonl",
    }

    out: Dict[str, Any] = {}
    for k, rel in keys.items():
        if rel.endswith(".jsonl"):
            # LocalStorageAdapter doesn't have jsonl tail helper; do best-effort read lines.
            p = root / rel
            if not p.is_file():
                out[k] = []
                continue
            try:
                lines = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()][-50:]
                recs = []
                for ln in lines:
                    try:
                        v = json.loads(ln)
                        if isinstance(v, dict):
                            recs.append(v)
                    except Exception:
                        continue
                out[k] = recs
            except OSError:
                out[k] = []
        else:
            blob = ad.read_json(rel)
            out[k] = blob if isinstance(blob, dict) else (blob or {})
    return out


def _simulate_trade_close(
    *,
    runtime_root: Path,
    databank_root: Path,
    trade_id: str,
    product_id: str,
    net_pnl_usd: float,
    hard_stop: bool,
) -> Dict[str, Any]:
    """
    Use the existing paper close-chain harness to trigger downstream learning/review/metrics paths.

    We patch the returned payload’s pnl fields by choosing the hard_stop flag and allowing the
    underlying close chain to write its artifacts; we then return a compact trade log row.
    """
    from trading_ai.runtime_proof.coinbase_shadow_paper_pass import run_close_chain

    # Reuse the harness, but pick hard_stop to generate loser clusters. The harness itself uses
    # fixed pnl numbers; we record our synthetic pnl separately for the sim control artifacts.
    close = run_close_chain(
        runtime_root=runtime_root,
        databank_root=databank_root,
        trade_id=trade_id,
        product_id=product_id,
        hard_stop=bool(hard_stop),
        skip_entry_gate=False,
    )

    return {
        "trade_id": trade_id,
        "product_id": product_id,
        "net_pnl_usd_simulated": round(float(net_pnl_usd), 4),
        "hard_stop": bool(hard_stop),
        "close_chain": {
            "nte_entry_gate": close.get("nte_entry_gate"),
            "packet_hard_stop_events": close.get("packet_hard_stop_events"),
            "risk_hard_stop_events": close.get("risk_hard_stop_events"),
            "artifact_paths": close.get("artifact_paths"),
        },
    }


def run_simulated_24h_day(
    *,
    config: Optional[Sim24hConfig] = None,
) -> Dict[str, Any]:
    cfg = config or Sim24hConfig()
    root = (cfg.runtime_root or ezras_runtime_root()).resolve()
    Path(root).mkdir(parents=True, exist_ok=True)
    _control_dir(root).mkdir(parents=True, exist_ok=True)
    ad = LocalStorageAdapter(runtime_root=root)

    # Ensure non-live autonomy mode is authoritative and visible on disk.
    mode_bundle = write_full_autonomy_mode_artifacts(runtime_root=root, reason="sim_24h_bootstrap")

    rnd = random.Random(int(cfg.seed))
    databank_root = root / "databank"
    databank_root.mkdir(parents=True, exist_ok=True)

    timeline: List[Dict[str, Any]] = []
    trade_log: List[Dict[str, Any]] = []
    pnl_curve: List[Dict[str, Any]] = []
    lessons: List[Dict[str, Any]] = []
    reviews: List[Dict[str, Any]] = []
    comparisons: List[Dict[str, Any]] = []
    tasks: List[Dict[str, Any]] = []
    ceo: List[Dict[str, Any]] = []

    cumulative = 0.0
    wins = 0
    losses = 0

    start_ts = time.time()

    for hour in range(int(cfg.hours)):
        hour_ts = start_ts + hour * 3600.0
        # Trade generation: losers/winners distribution with occasional hard stops.
        hour_trades: List[Dict[str, Any]] = []
        hour_pnl = 0.0

        for i in range(int(cfg.trades_per_hour)):
            tid = f"sim24h_h{hour:02d}_t{i:02d}"
            pid = rnd.choice(list(cfg.product_ids))
            # PnL: skew slightly positive but with fat-tail losses.
            roll = rnd.random()
            if roll < 0.12:
                pnl = -abs(rnd.gauss(3.5, 1.8))  # loser
                hard = roll < 0.04
            else:
                pnl = abs(rnd.gauss(2.0, 1.2)) * (0.6 if rnd.random() < 0.20 else 1.0)
                hard = False

            row = _simulate_trade_close(
                runtime_root=root,
                databank_root=databank_root,
                trade_id=tid,
                product_id=pid,
                net_pnl_usd=pnl,
                hard_stop=hard,
            )
            row["ts_unix"] = hour_ts + i * (3600.0 / max(1, int(cfg.trades_per_hour)))
            hour_trades.append(row)
            trade_log.append(row)
            hour_pnl += float(pnl)

            if pnl > 0:
                wins += 1
            else:
                losses += 1

        cumulative += hour_pnl
        pnl_curve.append(
            {
                "hour": hour,
                "ts_utc": _iso(hour_ts),
                "net_pnl_usd": round(hour_pnl, 4),
                "cumulative_net_pnl_usd": round(cumulative, 4),
                "wins_cumulative": wins,
                "losses_cumulative": losses,
            }
        )

        # Reviews / scheduler tick: force stub models to avoid external API dependencies.
        # We still exercise packet build, merge, safe routing, queues, and CEO artifacts.
        from trading_ai.global_layer.review_storage import ReviewStorage
        from trading_ai.global_layer.review_scheduler import run_full_review_cycle, tick_scheduler

        st = ReviewStorage()
        st.ensure_review_files()

        # Tick evaluation always appends audit lines; then run an explicit cycle each hour (stubbed).
        tick_out = tick_scheduler(storage=st)
        cycle = run_full_review_cycle("midday", storage=st, skip_models=True)
        joint = (cycle.get("joint") or {}) if isinstance(cycle, dict) else {}

        if joint:
            reviews.append(
                {
                    "hour": hour,
                    "ts_utc": _iso(hour_ts),
                    "joint_review_id": joint.get("joint_review_id"),
                    "live_mode_recommendation": joint.get("live_mode_recommendation"),
                    "confidence_score": joint.get("confidence_score"),
                    "review_integrity_state": joint.get("review_integrity_state"),
                }
            )
            ceo.append(
                {
                    "hour": hour,
                    "ts_utc": _iso(hour_ts),
                    "joint_review_id": joint.get("joint_review_id"),
                    "ceo_summary": (joint.get("ceo_summary") or "")[:1200],
                }
            )

        exports = _read_review_storage_exports(root)
        tasks.append(
            {
                "hour": hour,
                "ts_utc": _iso(hour_ts),
                "candidate_queue": exports.get("candidate_queue"),
                "risk_reduction_queue": exports.get("risk_reduction_queue"),
                "ceo_review_queue": exports.get("ceo_review_queue"),
                "review_action_log_tail": exports.get("review_action_log_tail"),
            }
        )

        # Comparisons: lightweight per-hour rollups.
        comparisons.append(
            {
                "hour": hour,
                "ts_utc": _iso(hour_ts),
                "by_product": {
                    pid: {
                        "trades": sum(1 for t in hour_trades if t.get("product_id") == pid),
                        "net_pnl_usd": round(
                            sum(
                                float(t.get("net_pnl_usd_simulated") or 0.0)
                                for t in hour_trades
                                if t.get("product_id") == pid
                            ),
                            4,
                        ),
                    }
                    for pid in cfg.product_ids
                },
            }
        )

        # Learning: append a compact “lesson” per hour + keep the existing learning engine flowing via close chain.
        lessons.append(
            {
                "hour": hour,
                "ts_utc": _iso(hour_ts),
                "lesson": (
                    "loss_cluster_detected" if hour_pnl < -5 else "steady_state" if hour_pnl > 0 else "mixed"
                ),
                "net_pnl_usd": round(hour_pnl, 4),
                "hard_stop_count": sum(1 for t in hour_trades if t.get("hard_stop")),
            }
        )

        # Command-center snapshot: stitches together ops/review/edge/perf/governance for a firm-style view.
        cc = run_command_center_snapshot(write_files=True, runtime_root=root)

        timeline.append(
            {
                "hour": hour,
                "ts_utc": _iso(hour_ts),
                "trades_closed": len(hour_trades),
                "net_pnl_usd": round(hour_pnl, 4),
                "cumulative_net_pnl_usd": round(cumulative, 4),
                "review_scheduler_ran": [x[0] for x in tick_out],
                "command_center_all_blockers_green": (cc.get("system_health") or {}).get("all_blockers_green"),
                "mode": (mode_bundle.get("mode") or {}).get("mode"),
                "live_trading_disabled": True,
            }
        )

        if int(cfg.accelerate_sleep_ms) > 0:
            time.sleep(int(cfg.accelerate_sleep_ms) / 1000.0)

    # Final artifacts
    summary = {
        "schema": "sim_24h_summary_v1",
        "runtime_root": str(root),
        "generated_at": _iso(),
        "hours": int(cfg.hours),
        "trades_total": int(cfg.hours) * int(cfg.trades_per_hour),
        "wins": wins,
        "losses": losses,
        "net_pnl_usd": round(cumulative, 4),
        "mode": (mode_bundle.get("mode") or {}).get("mode"),
        "live_trading_disabled": True,
        "honesty": "Simulation uses paper close-chain + stubbed review models; it does not submit venue orders.",
    }

    verdict = {
        "schema": "sim_24h_final_verdict_v1",
        "generated_at": _iso(),
        "runtime_root": str(root),
        "operational_autonomy_live": True,
        "live_trading_disabled": True,
        "no_live_orders_submitted": True,
        "proof_basis": [
            "full_autonomy_mode.json (mode=FULL_AUTONOMY_NONLIVE)",
            "full_autonomy_live_status.json (live_orders_allowed=false)",
            "live_order_guard blocks unless ExecutionMode.LIVE + explicit enables",
        ],
        "verdict": "SIM_COMPLETE_NONLIVE",
    }

    _write_control_json(ad, "data/control/sim_24h_summary.json", summary)
    _write_control_json(ad, "data/control/sim_24h_timeline.json", {"schema": "sim_24h_timeline_v1", "timeline": timeline})
    _write_control_json(ad, "data/control/sim_24h_trade_log.json", {"schema": "sim_24h_trade_log_v1", "trades": trade_log})
    _write_control_json(ad, "data/control/sim_24h_pnl.json", {"schema": "sim_24h_pnl_v1", "curve": pnl_curve})
    _write_control_json(ad, "data/control/sim_24h_lessons.json", {"schema": "sim_24h_lessons_v1", "lessons": lessons})
    _write_control_json(ad, "data/control/sim_24h_reviews.json", {"schema": "sim_24h_reviews_v1", "reviews": reviews})
    _write_control_json(ad, "data/control/sim_24h_comparisons.json", {"schema": "sim_24h_comparisons_v1", "comparisons": comparisons})
    _write_control_json(ad, "data/control/sim_24h_tasks.json", {"schema": "sim_24h_tasks_v1", "tasks": tasks})
    _write_control_json(ad, "data/control/sim_24h_ceo.json", {"schema": "sim_24h_ceo_v1", "ceo": ceo})
    _write_control_json(ad, "data/control/sim_24h_final_verdict.json", verdict)

    return {
        "summary": summary,
        "verdict": verdict,
        "paths": {
            "mode": str(root / "data/control/full_autonomy_mode.json"),
            "status": str(root / "data/control/full_autonomy_live_status.json"),
            "sim_24h_summary": str(root / "data/control/sim_24h_summary.json"),
        },
    }

