#!/usr/bin/env python3
"""
Smoke test: Kalshi + Polymarket execution preflight + hunt engine + balances.
NEVER submits real orders — sets EZRAS_DRY_RUN and uses execute_live=False.

Run from trading-ai repo root:
  PYTHONPATH=src python3.11 scripts/smoke_test_execution.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Repo root = parent of scripts/ when run as scripts/smoke_test_execution.py
_REPO = Path(__file__).resolve().parent.parent
if str(_REPO / "src") not in os.environ.get("PYTHONPATH", ""):
    sys.path.insert(0, str(_REPO / "src"))

os.environ.setdefault("EZRAS_DRY_RUN", "true")
os.environ.setdefault("EZRAS_RUNTIME_ROOT", str(Path.home() / "ezras-runtime"))


def _load_env() -> None:
    from trading_ai.shark.dotenv_load import load_shark_dotenv

    load_shark_dotenv()


def _ensure_scored_for_chain(
    m: Any,
    hunts: List[Any],
) -> Any:
    """Ensure a ScoredOpportunity that can build an ExecutionIntent (not BELOW_THRESHOLD)."""
    from trading_ai.shark.models import HuntSignal, HuntType, OpportunityTier
    from trading_ai.shark.scorer import score_opportunity

    h = list(hunts)
    scored = score_opportunity(m, h)
    if scored.tier != OpportunityTier.BELOW_THRESHOLD and scored.edge_size > 0:
        return scored
    # Synthetic tier-A pair for pipeline smoke only (does not change venue prices)
    stub = [
        HuntSignal(HuntType.STRUCTURAL_ARBITRAGE, 0.12, 0.88, {"sum": 0.92}),
        HuntSignal(
            HuntType.DEAD_MARKET_CONVERGENCE,
            0.11,
            0.82,
            {"side": "yes", "p_true": 0.62},
        ),
    ]
    return score_opportunity(m, stub)


def _audit_preflight_steps(audit: List[Dict[str, Any]]) -> List[str]:
    """Step labels recorded before step 9 (submit)."""
    out: List[str] = []
    for row in audit:
        step = str(row.get("step", ""))
        if step.startswith("9_"):
            break
        if step:
            out.append(step)
    return out


def _run_outlet_chain(outlet: str, scored: Any, capital: float) -> Tuple[Any, List[str]]:
    from trading_ai.shark.execution import run_execution_chain

    res = run_execution_chain(
        scored,
        capital=capital,
        outlet=outlet,
        execute_live=False,
        estimated_execution_delay_seconds=0.0,
        fee_to_edge_ratio=0.0,
    )
    return res, _audit_preflight_steps(res.audit)


def _smoke_outlet(
    name: str,
    fetch_markets: Any,
    outlet: str,
    capital: float,
) -> Dict[str, Any]:
    from trading_ai.shark.hunt_engine import group_markets_by_event, run_hunts_on_market

    markets: List[Any] = []
    try:
        markets = fetch_markets() or []
    except Exception as e:
        return {
            "markets_found": 0,
            "sample_market": None,
            "execution_chain": "FAIL",
            "gates_passed": [],
            "error": str(e)[:300],
            "balance": None,
            "ready_to_trade": False,
        }

    binary = [m for m in markets if getattr(m, "yes_price", 0) and getattr(m, "no_price", 0)]
    sample_id = binary[0].market_id if binary else (markets[0].market_id if markets else None)
    cross = group_markets_by_event(markets)
    now = time.time()
    scored = None
    for m in binary[:15]:
        hunts = run_hunts_on_market(m, cross_context=cross, now=now)
        scored = _ensure_scored_for_chain(m, hunts)
        if scored.tier.value != "BELOW_THRESHOLD":
            break
    if scored is None and binary:
        scored = _ensure_scored_for_chain(binary[0], [])
    if scored is None:
        return {
            "markets_found": len(markets),
            "sample_market": sample_id,
            "execution_chain": "FAIL",
            "gates_passed": [],
            "error": "no_scored_opportunity",
            "balance": None,
            "ready_to_trade": False,
        }

    res, gates_passed = _run_outlet_chain(outlet, scored, capital)
    chain_ok = res.ok and res.halted_at == "complete"

    return {
        "markets_found": len(markets),
        "sample_market": sample_id,
        "execution_chain": "PASS" if chain_ok else "FAIL",
        "gates_passed": gates_passed,
        "halted_at": res.halted_at,
        "balance": None,
        "ready_to_trade": chain_ok,
    }


def _hunt_scan() -> Dict[str, Any]:
    from trading_ai.shark.hunt_engine import group_markets_by_event, run_hunts_on_market
    from trading_ai.shark.models import OpportunityTier
    from trading_ai.shark.outlets import KalshiFetcher, PolymarketFetcher
    from trading_ai.shark.scorer import score_opportunity

    hunt_out: Dict[str, Any] = {"by_outlet": {}, "qualifying_opportunities": 0, "best_opportunity": {}}
    best_score = -1.0
    best_detail: Optional[Dict[str, Any]] = None
    fetchers = [("kalshi", KalshiFetcher()), ("polymarket", PolymarketFetcher())]
    now = time.time()

    for oname, f in fetchers:
        try:
            mkts = f.fetch_binary_markets()[:5]
        except Exception as e:
            hunt_out["by_outlet"][oname] = {"error": str(e)[:200], "markets": 0}
            continue
        cross = group_markets_by_event(mkts)
        rows = []
        qual = 0
        for m in mkts:
            hunts = run_hunts_on_market(m, cross_context=cross, now=now)
            scored = score_opportunity(m, hunts) if hunts else None
            tier = scored.tier.value if scored else "NO_HUNTS"
            sc = round(scored.score, 4) if scored else None
            rows.append(
                {
                    "market_id": m.market_id,
                    "tier": tier,
                    "score": sc,
                    "hunt_types": [h.hunt_type.value for h in hunts],
                }
            )
            if scored and scored.tier != OpportunityTier.BELOW_THRESHOLD:
                qual += 1
                if scored.score > best_score:
                    best_score = scored.score
                    best_detail = {
                        "outlet": oname,
                        "market_id": m.market_id,
                        "tier": tier,
                        "score": round(scored.score, 4),
                        "edge_size": round(scored.edge_size, 4),
                    }
        hunt_out["by_outlet"][oname] = {"markets_sampled": len(mkts), "rows": rows, "qualifying": qual}
        hunt_out["qualifying_opportunities"] += qual

    hunt_out["best_opportunity"] = best_detail or {}
    hunt_out["note"] = (
        "Six hunt runners per market (dead, structural, statistical, near-zero, liquidity) "
        "+ cross-platform when canonical_event_key matches across outlets"
    )
    return hunt_out


def _balances_vs_capital() -> Dict[str, Any]:
    from trading_ai.shark.balance_sync import fetch_kalshi_balance_usd
    from trading_ai.shark.outlets.polymarket import fetch_polymarket_balance
    from trading_ai.shark.state_store import load_capital

    rec = load_capital()
    book = float(rec.current_capital)
    k = fetch_kalshi_balance_usd()
    p = fetch_polymarket_balance()
    disc: List[str] = []
    if k is not None and abs(k - book) > 0.02:
        disc.append(f"kalshi_usd={k} vs capital.json current_capital={book}")
    if p is not None:
        disc.append(f"polymarket_balance_usd={p} (treasury book; may differ from capital.json)")
    return {
        "capital_json_usd": book,
        "kalshi_live_usd": k,
        "polymarket_live_usd": p,
        "discrepancies": disc,
    }


def main() -> int:
    _load_env()
    try:
        from trading_ai.shark.reporting import clear_test_alerts

        clear_test_alerts()
    except Exception:
        pass

    from trading_ai.shark.outlets.kalshi import KalshiFetcher
    from trading_ai.shark.outlets.polymarket import PolymarketFetcher
    from trading_ai.shark.state_store import load_capital

    capital = float(load_capital().current_capital)

    k_fetch = KalshiFetcher()
    p_fetch = PolymarketFetcher()

    kalshi_block = _smoke_outlet("kalshi", k_fetch.fetch_binary_markets, "kalshi", capital)
    poly_block = _smoke_outlet("polymarket", p_fetch.fetch_binary_markets, "polymarket", capital)

    bal = _balances_vs_capital()
    kalshi_block["balance"] = bal["kalshi_live_usd"]
    poly_block["balance"] = bal["polymarket_live_usd"]

    hunt_full = _hunt_scan()

    summary = {
        "kalshi": kalshi_block,
        "polymarket": poly_block,
        "hunt_results": {
            "qualifying_opportunities": hunt_full.get("qualifying_opportunities", 0),
            "best_opportunity": hunt_full.get("best_opportunity") or {},
            "by_outlet": hunt_full.get("by_outlet"),
            "note": hunt_full.get("note"),
        },
        "balance_audit": bal,
    }

    print("=" * 60)
    print("EXECUTION SMOKE TEST (dry-run only, EZRAS_DRY_RUN enforced)")
    print("=" * 60)
    for label, block in (("KALSHI", kalshi_block), ("POLYMARKET", poly_block)):
        status = "PASS" if block.get("execution_chain") == "PASS" else "FAIL"
        print(f"\n[{label}] execution_chain: {status}")
        print(f"  markets_found={block.get('markets_found')} sample={block.get('sample_market')}")
        print(f"  gates_passed: {block.get('gates_passed')}")
        if block.get("halted_at"):
            print(f"  halted_at: {block.get('halted_at')}")
        print(f"  balance: {block.get('balance')}  ready_to_trade: {block.get('ready_to_trade')}")

    print("\n[HUNT ENGINE]")
    hr = summary["hunt_results"]
    print(f"  qualifying_opportunities: {hr['qualifying_opportunities']}")
    print(f"  best_opportunity: {json.dumps(hr['best_opportunity'], indent=2)}")

    print("\n[BALANCE vs capital.json]")
    print(f"  {json.dumps(bal, indent=2)}")

    print("\n[JSON SUMMARY]")
    print(json.dumps(summary, indent=2, default=str))

    k_ok = kalshi_block.get("execution_chain") == "PASS"
    p_ok = poly_block.get("execution_chain") == "PASS"
    return 0 if (k_ok and p_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
