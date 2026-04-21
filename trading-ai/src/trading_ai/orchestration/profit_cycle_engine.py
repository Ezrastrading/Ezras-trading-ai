"""
Venue-agnostic profit cycle engine.

Entry point required by repo mission:
  profit_cycle_engine(venue_id, gate_id)

Hard rules:
- If blocked -> return explicit NO_TRADE with reason + blocking_layer.
- No stage may report PASS without evidence artifacts.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

from trading_ai.execution_validation import validate_runtime_pretrade
from trading_ai.risk_engine import risk_allows_or_no_trade
from trading_ai.runtime_paths import ezras_runtime_root
from trading_ai.storage.storage_adapter import LocalStorageAdapter
from trading_ai.final_validation import compute_final_system_status
from trading_ai.runtime_checks.ssl_guard import enforce_ssl


def _runtime_root(runtime_root: Optional[Path] = None) -> Path:
    root = Path(runtime_root or os.environ.get("EZRAS_RUNTIME_ROOT") or ezras_runtime_root()).resolve()
    os.environ["EZRAS_RUNTIME_ROOT"] = str(root)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _no_trade(reason: str, blocking_layer: str, *, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {"action": "NO_TRADE", "reason": reason, "blocking_layer": blocking_layer}
    if extra:
        out["meta"] = dict(extra)
    return out


def profit_cycle_engine(
    venue_id: str,
    gate_id: str,
    *,
    runtime_root: Optional[Path] = None,
    quote_usd: Optional[float] = None,
    symbol: str = "",
) -> Dict[str, Any]:
    """
    Executes one profit cycle for a venue+gate.

    This does not assume Avenue A/B naming and does not allow "green without evidence".
    """
    root = _runtime_root(runtime_root)
    ad = LocalStorageAdapter(runtime_root=root)

    # Phase 1: fail hard on LibreSSL / legacy OpenSSL for any network-capable path.
    enforce_ssl()

    # 1) Risk hard stop
    ra = risk_allows_or_no_trade(runtime_root=root)
    if ra.get("action") == "NO_TRADE":
        ad.write_json("data/control/last_decision.json", {**ra, "timestamp": time.time()})
        status = compute_final_system_status(runtime_root=root)
        return {**ra, "FINAL_SYSTEM_STATUS": status}

    venue = str(venue_id or "").strip().lower()
    gate = str(gate_id or "").strip().lower()
    if not venue or not gate:
        out = _no_trade("invalid_venue_or_gate", "execution")
        ad.write_json("data/control/last_decision.json", {**out, "timestamp": time.time()})
        status = compute_final_system_status(runtime_root=root)
        return {**out, "FINAL_SYSTEM_STATUS": status}

    # 2) Dispatch to venue implementation
    if venue == "coinbase":
        from trading_ai.shark.outlets.coinbase import CoinbaseClient

        client = CoinbaseClient()
        # Basic min notional: default 10 (matches existing profit-cycle guard); can be overridden per venue.
        min_notional = float(os.environ.get("EZRAS_MIN_NOTIONAL_USD") or 10.0)
        q = float(quote_usd or os.environ.get("EZRAS_PROFIT_QUOTE_USD") or 20.0)
        sym = (symbol or os.environ.get("EZRAS_PROFIT_SYMBOL") or "BTC-USD").strip().upper()

        v = validate_runtime_pretrade(
            venue="coinbase",
            client=client,
            symbol=sym,
            quote_size=q,
            min_notional=min_notional,
        )
        if not v.ok:
            out = _no_trade(f"execution_validation_failed:{v.reason}", "execution", extra=v.meta)
            ad.write_json("data/control/last_decision.json", {**out, "timestamp": time.time()})
            status = compute_final_system_status(runtime_root=root)
            return {**out, "FINAL_SYSTEM_STATUS": status}

        # Delegate to the existing proven profit loop, but do not allow it to claim readiness
        # unless the new artifact-first truth chain is satisfied.
        from trading_ai.orchestration.avenue_a_profit_cycle import run_avenue_a_profit_cycle

        exec_profile = "gate_b" if gate in ("momentum", "gate_b") else "gate_a"
        res = run_avenue_a_profit_cycle(
            root,
            quote_usd=q,
            product_id=sym,
            include_runtime_stability=True,
            execution_profile=exec_profile,  # type: ignore[arg-type]
            gate_a_anchored_majors_only=True,
            avenue_a_autonomous_lane_decision=None,
        )
        ad.write_json(
            "data/control/last_decision.json",
            {"action": "TRADE_ATTEMPTED", "result": res, "timestamp": time.time()},
        )
        status = compute_final_system_status(runtime_root=root)
        return {"action": "TRADE_ATTEMPTED", "result": res, "FINAL_SYSTEM_STATUS": status}

    if venue == "kalshi":
        out = _no_trade("kalshi_profit_cycle_not_yet_migrated_to_profit_cycle_engine", "execution")
        ad.write_json("data/control/last_decision.json", {**out, "timestamp": time.time()})
        status = compute_final_system_status(runtime_root=root)
        return {**out, "FINAL_SYSTEM_STATUS": status}

    out = _no_trade("unsupported_venue", "execution", extra={"venue_id": venue})
    ad.write_json("data/control/last_decision.json", {**out, "timestamp": time.time()})
    status = compute_final_system_status(runtime_root=root)
    return {**out, "FINAL_SYSTEM_STATUS": status}

