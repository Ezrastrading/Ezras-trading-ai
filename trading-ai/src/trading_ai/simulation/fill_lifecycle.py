"""
Simulated fill lifecycle (non-venue): intents → partial / filled / canceled / rejected.

Durable paths remain compatible with ``simulated_fill_chain`` reconciliation summaries.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.global_layer.learning_distillation import propose_shared_lesson
from trading_ai.multi_avenue.control_logs import append_control_events
from trading_ai.nte.memory.store import MemoryStore
from trading_ai.runtime_paths import ezras_runtime_root
from trading_ai.simulation.latency import apply_slippage_bps, sample_latency_bundle
from trading_ai.simulation.nonlive import assert_nonlive_for_simulation


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _root(runtime_root: Optional[Path]) -> Path:
    return Path(runtime_root or ezras_runtime_root()).resolve()


def _chain_dir(root: Path) -> Path:
    p = root / "data" / "control" / "simulated_fill_chain"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


_STRATEGIES = ("mean_reversion", "continuation_pullback", "micro_momentum")


def advance_simulated_fill_once(
    *,
    runtime_root: Optional[Path] = None,
    avenue: str = "coinbase",
    gate: str = "gate_a",
    bot_id: str = "simulated_fill_engine",
    tick_index: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Advance one lifecycle step. Terminal outcomes append simulated trades (except rejected/canceled).

    Deterministic branches keyed by ``completed_cycles`` for tests and repeatability.
    """
    assert_nonlive_for_simulation()
    root = _root(runtime_root)
    cdir = _chain_dir(root)
    state_p = cdir / "active_intent.json"
    st = _read_json(state_p)
    phase = str(st.get("phase") or "idle")
    completed = int(st.get("completed_cycles") or 0)
    intent_id = str(st.get("intent_id") or "")
    ti = int(tick_index) if tick_index is not None else completed

    if phase in ("filled", "canceled", "rejected"):
        phase = "idle"
        st = {}

    if phase == "idle":
        intent_id = f"sim_{uuid.uuid4().hex[:12]}"
        lat = sample_latency_bundle(tick_index=ti)
        st = {
            "truth_version": "simulated_fill_intent_v2",
            "intent_id": intent_id,
            "phase": "submitted",
            "avenue": avenue,
            "gate": gate,
            "bot_id": bot_id,
            "started_at": _iso(),
            "completed_cycles": completed,
            "venue_order_submitted": False,
            "non_live_simulation": True,
            "latency": lat,
        }
        _write_json(state_p, st)
        append_control_events(
            "simulated_fill_events.json",
            {"intent_id": intent_id, "phase": "submitted", "detail": "intent_created"},
            runtime_root=root,
        )
        return {"ok": True, "intent_id": intent_id, "phase": "submitted", "latency_ms": lat["inbound_ms"]}

    if phase == "submitted":
        if completed % 17 == 3:
            return _terminal_rejected(root, state_p, intent_id, avenue, gate, bot_id, completed)
        st["phase"] = "accepted"
        st["updated_at"] = _iso()
        _write_json(state_p, st)
        append_control_events(
            "simulated_fill_events.json",
            {"intent_id": intent_id, "phase": "accepted", "detail": "ack"},
            runtime_root=root,
        )
        return {"ok": True, "intent_id": intent_id, "phase": "accepted"}

    if phase == "accepted":
        if completed % 5 == 4:
            summary = _write_terminal_summary(
                root,
                intent_id=intent_id,
                avenue=avenue,
                gate=gate,
                bot_id=bot_id,
                terminal="canceled",
                net_pnl_usd=0.0,
                strategy_id="none",
                slippage_bps=0.0,
            )
            _write_json(
                state_p,
                {
                    "truth_version": "simulated_fill_intent_v2",
                    "phase": "idle",
                    "completed_cycles": completed + 1,
                    "last_intent_id": intent_id,
                    "last_terminal": "canceled",
                },
            )
            append_control_events(
                "simulated_fill_events.json",
                {"intent_id": intent_id, "phase": "canceled", "detail": "terminal_simulated_cancel"},
                runtime_root=root,
            )
            return {"ok": True, "intent_id": intent_id, "phase": "canceled", "summary": summary}

        if completed % 4 == 1:
            st["phase"] = "partial_fill"
            st["fill_fraction"] = 0.35
            st["updated_at"] = _iso()
            _write_json(state_p, st)
            append_control_events(
                "simulated_fill_events.json",
                {"intent_id": intent_id, "phase": "partial_fill", "detail": "partial"},
                runtime_root=root,
            )
            return {"ok": True, "intent_id": intent_id, "phase": "partial_fill"}

        return _terminal_filled(root, state_p, st, intent_id, avenue, gate, bot_id, completed, partial=False)

    if phase == "partial_fill":
        if float(st.get("fill_fraction") or 0) < 0.99:
            st["fill_fraction"] = 1.0
            st["updated_at"] = _iso()
            _write_json(state_p, st)
            append_control_events(
                "simulated_fill_events.json",
                {"intent_id": intent_id, "phase": "partial_fill", "detail": "top_off"},
                runtime_root=root,
            )
            return {"ok": True, "intent_id": intent_id, "phase": "partial_fill"}

        return _terminal_filled(root, state_p, st, intent_id, avenue, gate, bot_id, completed, partial=True)

    _write_json(state_p, {})
    return {"ok": False, "error": "unknown_phase_reset", "phase": phase}


def _terminal_rejected(
    root: Path,
    state_p: Path,
    intent_id: str,
    avenue: str,
    gate: str,
    bot_id: str,
    completed: int,
) -> Dict[str, Any]:
    summary = _write_terminal_summary(
        root,
        intent_id=intent_id,
        avenue=avenue,
        gate=gate,
        bot_id=bot_id,
        terminal="rejected",
        net_pnl_usd=0.0,
        strategy_id="none",
        slippage_bps=0.0,
    )
    _write_json(
        state_p,
        {
            "truth_version": "simulated_fill_intent_v2",
            "phase": "idle",
            "completed_cycles": completed + 1,
            "last_intent_id": intent_id,
            "last_terminal": "rejected",
        },
    )
    append_control_events(
        "simulated_fill_events.json",
        {"intent_id": intent_id, "phase": "rejected", "detail": "risk_reject_sim"},
        runtime_root=root,
    )
    return {"ok": True, "intent_id": intent_id, "phase": "rejected", "summary": summary}


def _terminal_filled(
    root: Path,
    state_p: Path,
    st: Dict[str, Any],
    intent_id: str,
    avenue: str,
    gate: str,
    bot_id: str,
    completed: int,
    *,
    partial: bool,
) -> Dict[str, Any]:
    strat = _STRATEGIES[completed % len(_STRATEGIES)]
    slip = apply_slippage_bps(tick_index=completed)
    base_net = round(1.25 + (completed % 7) * 0.1, 4)
    slip_adj = round(base_net - abs(slip) * 0.0001, 6)
    if completed % 13 == 0:
        net = round(-3.1 - (completed % 5) * 0.22, 4)
    else:
        net = slip_adj if slip_adj > -1e6 else base_net
    ms = MemoryStore()
    ms.append_trade(
        {
            "trade_id": intent_id,
            "status": "closed",
            "avenue": avenue,
            "gate": gate,
            "source_bot_id": bot_id,
            "net_pnl_usd": net,
            "fees_usd": 0.01,
            "setup_type": "simulated_fill_chain",
            "strategy_id": strat,
            "simulated_non_live": True,
            "venue_order_submitted": False,
            "partial_fill_prior": bool(partial),
            "execution_slippage_bps": slip,
            "truth_note": "synthetic_closed_trade_for_autonomy_proof_only",
        }
    )
    propose_shared_lesson(
        bot_id,
        {
            "title": f"Simulated fill lesson {intent_id}",
            "body": f"Closed simulated trade net={net} avenue={avenue} gate={gate} strategy={strat} slip_bps={slip}",
            "tags": ["simulated_fill", avenue, gate, strat],
        },
    )
    summary = _write_terminal_summary(
        root,
        intent_id=intent_id,
        avenue=avenue,
        gate=gate,
        bot_id=bot_id,
        terminal="filled",
        net_pnl_usd=net,
        strategy_id=strat,
        slippage_bps=slip,
    )
    _write_json(
        state_p,
        {
            "truth_version": "simulated_fill_intent_v2",
            "phase": "idle",
            "completed_cycles": completed + 1,
            "last_intent_id": intent_id,
            "last_terminal": "filled",
        },
    )
    append_control_events(
        "simulated_fill_events.json",
        {
            "intent_id": intent_id,
            "phase": "filled",
            "detail": "trade_memory_appended",
            "net_pnl_usd": net,
            "strategy_id": strat,
        },
        runtime_root=root,
    )
    return {"ok": True, "intent_id": intent_id, "phase": "filled", "net_pnl_usd": net, "summary": summary}


def _write_terminal_summary(
    root: Path,
    *,
    intent_id: str,
    avenue: str,
    gate: str,
    bot_id: str,
    terminal: str,
    net_pnl_usd: float,
    strategy_id: str,
    slippage_bps: float,
) -> Dict[str, Any]:
    rec: Dict[str, Any] = {
        "truth_version": "simulated_fill_reconciliation_v1",
        "generated_at": _iso(),
        "intent_id": intent_id,
        "avenue": avenue,
        "gate": gate,
        "bot_id": bot_id,
        "terminal": terminal,
        "net_pnl_usd": net_pnl_usd,
        "strategy_id": strategy_id,
        "execution_slippage_bps": slippage_bps,
        "venue_order_submitted": False,
        "honesty": "Simulated lifecycle only; not a venue execution report.",
    }
    p = _chain_dir(root) / "reconciliation_summary.json"
    hist_p = _chain_dir(root) / "reconciliation_history.json"
    hist = _read_json(hist_p)
    xs: List[Dict[str, Any]] = list(hist.get("items") or [])
    xs.append(rec)
    hist = {"truth_version": "simulated_fill_history_v1", "items": xs[-200:]}
    _write_json(hist_p, hist)
    _write_json(p, rec)
    return rec
