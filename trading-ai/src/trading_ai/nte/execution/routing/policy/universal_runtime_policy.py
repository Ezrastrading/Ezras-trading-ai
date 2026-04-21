"""
Venue-agnostic **crypto execution capital** runtime policy — layered on top of the Coinbase NTE
product allowlist without replacing it.

Gates and adapters consume the same resolver output; this object adds explicit route/capital
semantics (quotes, leg limits, honest multi-leg status).
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from trading_ai.nte.execution.routing.integration.spot_quote_utils import parse_spot_base_quote
from trading_ai.nte.execution.routing.policy.runtime_coinbase_policy import CoinbaseRuntimeProductPolicy


def _parse_csv_upper(raw: str) -> List[str]:
    return [x.strip().upper() for x in re.split(r"[,\s]+", raw) if x.strip()]


@dataclass
class UniversalCryptoRuntimePolicy:
    """
    Canonical cross-venue policy shape for execution capital (spot-style).

    ``allowed_products`` mirrors runtime-active spot ids; quotes/assets are derived unless env overrides.
    """

    policy_version: str
    allowed_products: List[str]
    allowed_assets: List[str]
    allowed_quotes: List[str]
    blocked_assets: List[str]
    blocked_products: List[str]
    validation_active_products: List[str]
    execution_active_products: List[str]
    max_route_legs_search: int
    max_route_legs_validation_execution: int
    multi_leg_route_execution_enabled: bool
    multi_leg_execution_blocked_reason: str
    multi_leg_routing_honest_status: str
    route_search_enabled: bool
    products_removed_by_env: Optional[List[str]]
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _assets_from_products(products: List[str]) -> Set[str]:
    out: Set[str] = set()
    for p in products:
        b, q = parse_spot_base_quote(str(p))
        out.add(b)
        out.add(q)
    return out


def build_universal_runtime_policy(
    pol: CoinbaseRuntimeProductPolicy,
    *,
    include_env_route_overrides: bool = True,
) -> UniversalCryptoRuntimePolicy:
    """
    Build from the resolved Coinbase runtime policy + optional env knobs.

    Env:
    - ``EZRAS_ALLOWED_QUOTES`` — comma list (default USD,USDC)
    - ``EZRAS_BLOCKED_ASSETS`` — optional comma list
    - ``EZRAS_BLOCKED_PRODUCTS`` — optional comma list
    - ``EZRAS_ROUTE_SEARCH_MAX_LEGS`` — graph search depth for diagnostics (default 3)
    - ``EZRAS_MULTI_LEG_VALIDATION_ENABLED`` — if true, validation *may* consider multi-leg
      routes in diagnostics only; **execution** stays single-leg until explicitly enabled elsewhere.
    """
    allowed_products = [p.upper() for p in pol.runtime_active_products]
    asset_set = _assets_from_products(allowed_products)

    quotes_raw = (os.environ.get("EZRAS_ALLOWED_QUOTES") or "USD,USDC").strip()
    allowed_quotes = _parse_csv_upper(quotes_raw) if quotes_raw else ["USD", "USDC"]
    for q in allowed_quotes:
        asset_set.add(q)

    blocked_assets: List[str] = []
    blocked_products: List[str] = []
    if include_env_route_overrides:
        ba = (os.environ.get("EZRAS_BLOCKED_ASSETS") or "").strip()
        if ba:
            blocked_assets = sorted(set(_parse_csv_upper(ba)))
        bp = (os.environ.get("EZRAS_BLOCKED_PRODUCTS") or "").strip()
        if bp:
            blocked_products = sorted({x.upper() for x in _parse_csv_upper(bp)})

    max_search = 3
    raw_legs = (os.environ.get("EZRAS_ROUTE_SEARCH_MAX_LEGS") or "").strip()
    if raw_legs:
        try:
            max_search = max(1, min(6, int(raw_legs)))
        except ValueError:
            pass

    multi_leg_val = (os.environ.get("EZRAS_MULTI_LEG_VALIDATION_ENABLED") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    notes: List[str] = [
        "multi_leg_exchange_execution_is_not_enabled_in_deployment_paths",
        "route_graph_search_is_for_operator_visibility_only_unless_execution_flag_changes",
    ]
    if multi_leg_val:
        notes.append("EZRAS_MULTI_LEG_VALIDATION_ENABLED_true_diagnostics_only_execution_remains_single_leg")

    # Honest status string for artifacts (never imply live multi-hop fills).
    honest = "search_only_not_execution_enabled"

    return UniversalCryptoRuntimePolicy(
        policy_version="universal_crypto_runtime_v1",
        allowed_products=sorted(set(allowed_products)),
        allowed_assets=sorted(asset_set),
        allowed_quotes=sorted(set(allowed_quotes)),
        blocked_assets=blocked_assets,
        blocked_products=blocked_products,
        validation_active_products=[p.upper() for p in pol.validation_active_products],
        execution_active_products=[p.upper() for p in pol.execution_active_products],
        max_route_legs_search=max_search,
        max_route_legs_validation_execution=1,
        multi_leg_route_execution_enabled=False,
        multi_leg_execution_blocked_reason="not_enabled_in_production",
        multi_leg_routing_honest_status=honest,
        route_search_enabled=True,
        products_removed_by_env=list(pol.products_removed_by_env) if pol.products_removed_by_env else None,
        notes=notes,
    )


def policy_vs_capital_one_liner(
    *,
    error_code: Optional[str],
    fundable_disallowed: List[str],
    allowed_unfundable: List[str],
) -> str:
    """Short operator sentence for readiness / reports."""
    if error_code == "runtime_policy_disallows_fundable_product" and fundable_disallowed:
        return (
            f"Quote balances could fund {', '.join(fundable_disallowed)}, but runtime policy blocks "
            f"those product ids — adjust NTE_PRODUCTS or env override."
        )
    if error_code == "insufficient_allowed_quote_balance" and allowed_unfundable:
        return (
            "Runtime-allowed products exist but none have enough quote currency for the requested notional "
            f"(underfunded: {', '.join(allowed_unfundable)})."
        )
    if error_code == "insufficient_allowed_quote_balance":
        return "No allowed single-leg spot pair has sufficient quote balance for the requested notional."
    return ""
