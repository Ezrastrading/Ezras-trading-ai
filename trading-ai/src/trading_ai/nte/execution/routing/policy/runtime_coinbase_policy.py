"""
Single canonical **runtime** Coinbase product policy — one resolver for all layers.

Distinguishes:
- code defaults
- env overrides (``NTE_PRODUCTS`` / ``NTE_COINBASE_PRODUCTS``)
- ``runtime_active_products`` (``load_nte_settings().products`` — live guard + execution)
- ``validation_allowed_products`` (priority order ∩ runtime active)
- ``execution_allowed_products`` (same as runtime active for spot NTE)
- venue-supported ids (optional catalog fetch)
- ``effective_allowed_products`` (runtime ∩ venue when catalog is available)
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from trading_ai.nte.config import settings as nte_settings_mod
from trading_ai.nte.config.settings import load_nte_settings
from trading_ai.nte.execution.routing.integration.spot_quote_utils import is_spot_like_product_id
from trading_ai.nte.hardening.coinbase_product_policy import ordered_validation_candidates
from trading_ai.runtime_paths import ezras_runtime_root

logger = logging.getLogger(__name__)


@dataclass
class CoinbaseRuntimeProductPolicy:
    """Canonical runtime product policy — use this everywhere (Gate A, Gate B hooks, diagnostics)."""

    configured_default_products: List[str]
    env_override_products: Optional[List[str]]
    effective_products_source: str
    products_removed_by_env: Optional[List[str]]
    products_added_by_env: Optional[List[str]]
    runtime_active_products: List[str]
    validation_active_products: List[str]
    execution_active_products: List[str]
    validation_allowed_products: List[str]
    execution_allowed_products: List[str]
    venue_supported_count: int
    venue_supported_products_sample: List[str]
    effective_allowed_products: List[str]
    effective_disallowed_products_with_reasons: Dict[str, str]
    effective_products: List[str]
    merged_validation_candidates: List[str]
    runtime_allowlist_valid: bool
    runtime_allowlist_error_code: Optional[str] = None
    runtime_allowlist_operator_message: Optional[str] = None
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def explain_product(self, product_id: str) -> Dict[str, Any]:
        """Operator-friendly single-product truth (e.g. BTC-USDC active vs excluded)."""
        pid = (product_id or "").strip().upper()
        env = self.env_override_products
        in_default = pid in set(p.upper() for p in self.configured_default_products)
        in_runtime = pid in set(p.upper() for p in self.runtime_active_products)
        in_val = pid in set(p.upper() for p in self.validation_active_products)
        in_eff = pid in set(p.upper() for p in self.effective_allowed_products)
        excluded = self.effective_disallowed_products_with_reasons.get(pid)
        reason = None
        if env is not None and pid not in set(p.upper() for p in env):
            reason = "not_listed_in_NTE_PRODUCTS_env_override"
        elif env is None and not in_default and not in_runtime:
            reason = "not_in_code_defaults_and_not_in_runtime"
        elif not in_runtime:
            reason = "not_in_runtime_active_products"
        out: Dict[str, Any] = {
            "product_id": pid,
            "in_code_defaults": in_default,
            "env_override_active": env is not None,
            "env_override_list": list(env) if env else None,
            "in_runtime_active_products": in_runtime,
            "in_validation_allowed_products": in_val,
            "in_execution_allowed_products": in_runtime,
            "in_effective_allowed_products": in_eff,
            "effective_exclusion_reason": excluded,
            "interpretation": (
                "active_for_live_and_validation"
                if in_runtime and in_val and (in_eff or not self.effective_disallowed_products_with_reasons)
                else (
                    "runtime_disallowed_or_not_configured"
                    if not in_runtime
                    else ("blocked_by_venue_catalog" if excluded else "ok")
                )
            ),
        }
        return out


def _merge_spot_candidates_for_runtime(active_unique: List[str]) -> List[str]:
    """Same ordering semantics as :func:`merge_validation_candidates_for_runtime` (no circular import)."""
    cand = ordered_validation_candidates()
    act = {p.upper() for p in active_unique}
    seen: set[str] = set()
    out: List[str] = []
    for p in cand:
        u = p.strip().upper()
        if u in act and u not in seen:
            out.append(u)
            seen.add(u)
    for u in sorted(act):
        if u in seen:
            continue
        if is_spot_like_product_id(u):
            out.append(u)
            seen.add(u)
    return out


@dataclass
class RuntimeCoinbasePolicySnapshot:
    """Backward-compatible snapshot shape for older readers / JSON logs."""

    configured_default_products: List[str]
    env_override_products: Optional[List[str]]
    effective_products_source: str
    products_removed_by_env: Optional[List[str]]
    products_added_by_env: Optional[List[str]]
    nte_runtime_active_products: List[str]
    validation_active_products: List[str]
    execution_active_products: List[str]
    validation_allowed_products: List[str]
    execution_allowed_products: List[str]
    venue_supported_product_ids_sample: List[str]
    venue_supported_count: int
    effective_allowed_products: List[str]
    effective_disallowed_reason_by_product: Dict[str, str]
    effective_products: List[str]
    merged_validation_candidates: List[str]
    runtime_allowlist_valid: bool
    runtime_allowlist_error_code: Optional[str] = None
    runtime_allowlist_operator_message: Optional[str] = None
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _venue_supported_ids() -> tuple[Set[str], int, List[str]]:
    try:
        from trading_ai.nte.execution.routing.venues.coinbase.catalog import (
            fetch_coinbase_spot_products_online,
        )

        rows = fetch_coinbase_spot_products_online()
        ids = {str(r.get("product_id") or "").strip().upper() for r in rows if r.get("product_id")}
        ids.discard("")
        sample = sorted(ids)[:40]
        return ids, len(ids), sample
    except Exception as exc:
        return set(), 0, [f"catalog_error:{exc!s}"]


def product_supported_by_runtime_venue_catalog(
    product_id: str,
    pol: CoinbaseRuntimeProductPolicy,
) -> bool:
    """False when catalog was loaded and this runtime-active product is missing from the venue catalog."""
    pid = (product_id or "").strip().upper()
    return pol.effective_disallowed_products_with_reasons.get(pid) != "venue_catalog_missing_or_offline"


def resolve_coinbase_runtime_product_policy(
    *,
    include_venue_catalog: bool = True,
) -> CoinbaseRuntimeProductPolicy:
    """
    Single canonical resolver — defaults, env, runtime active, validation vs execution sets,
    venue intersection, and rejection reasons.
    """
    default_t = tuple(nte_settings_mod._default_nte_coinbase_products())
    env_t = nte_settings_mod._nte_products_from_environ()
    nte = load_nte_settings()
    active = [str(p).strip().upper() for p in nte.products]
    active_unique = list(dict.fromkeys(active))

    cand = ordered_validation_candidates()
    active_set = set(active_unique)
    validation_allowed = [p for p in cand if p.upper() in active_set]

    venue_ids, venue_n, venue_sample = _venue_supported_ids() if include_venue_catalog else (set(), 0, [])
    reasons: Dict[str, str] = {}
    effective: List[str] = []
    notes: List[str] = []

    for pid in active_unique:
        p = pid.upper()
        if venue_ids and p not in venue_ids:
            reasons[p] = "venue_catalog_missing_or_offline"
            continue
        effective.append(p)

    if not venue_ids and include_venue_catalog:
        notes.append("venue_catalog_empty_or_unavailable_effective_equals_runtime_only")

    default_set = {x.upper() for x in default_t}
    active_set = {x.upper() for x in active_unique}
    removed_by_env: Optional[List[str]] = None
    added_by_env: Optional[List[str]] = None
    if env_t is not None:
        removed_by_env = sorted(default_set - active_set)
        added_by_env = sorted(active_set - default_set)
        for x in removed_by_env:
            logger.warning(
                "WARNING: runtime policy removed default product %s via env override",
                x,
            )
    eff_src = "env_override" if env_t is not None else "code_defaults"

    merged_ids = _merge_spot_candidates_for_runtime(active_unique)
    allowlist_valid = len(active_unique) > 0 and len(merged_ids) > 0
    fatal_code: Optional[str] = None
    fatal_msg: Optional[str] = None
    if not allowlist_valid:
        fatal_code = "runtime_policy_empty_or_invalid"
        fatal_msg = "No runtime-allowed products remain after env override"

    exec_active = sorted(set(active_unique))
    return CoinbaseRuntimeProductPolicy(
        configured_default_products=[x.upper() for x in default_t],
        env_override_products=[x.upper() for x in env_t] if env_t else None,
        effective_products_source=eff_src,
        products_removed_by_env=removed_by_env,
        products_added_by_env=added_by_env,
        runtime_active_products=active_unique,
        validation_active_products=list(validation_allowed),
        execution_active_products=exec_active,
        validation_allowed_products=validation_allowed,
        execution_allowed_products=exec_active,
        venue_supported_count=venue_n,
        venue_supported_products_sample=venue_sample,
        effective_allowed_products=sorted(set(effective)),
        effective_disallowed_products_with_reasons=reasons,
        effective_products=list(active_unique),
        merged_validation_candidates=merged_ids,
        runtime_allowlist_valid=allowlist_valid,
        runtime_allowlist_error_code=fatal_code,
        runtime_allowlist_operator_message=fatal_msg,
        notes=notes,
    )


def build_runtime_coinbase_policy_snapshot(
    *,
    include_venue_catalog: bool = True,
) -> RuntimeCoinbasePolicySnapshot:
    pol = resolve_coinbase_runtime_product_policy(include_venue_catalog=include_venue_catalog)
    return RuntimeCoinbasePolicySnapshot(
        configured_default_products=list(pol.configured_default_products),
        env_override_products=list(pol.env_override_products) if pol.env_override_products else None,
        effective_products_source=pol.effective_products_source,
        products_removed_by_env=list(pol.products_removed_by_env) if pol.products_removed_by_env else None,
        products_added_by_env=list(pol.products_added_by_env) if pol.products_added_by_env else None,
        nte_runtime_active_products=list(pol.runtime_active_products),
        validation_active_products=list(pol.validation_active_products),
        execution_active_products=list(pol.execution_active_products),
        validation_allowed_products=list(pol.validation_allowed_products),
        execution_allowed_products=list(pol.execution_allowed_products),
        venue_supported_product_ids_sample=list(pol.venue_supported_products_sample),
        venue_supported_count=pol.venue_supported_count,
        effective_allowed_products=list(pol.effective_allowed_products),
        effective_disallowed_reason_by_product=dict(pol.effective_disallowed_products_with_reasons),
        effective_products=list(pol.effective_products),
        merged_validation_candidates=list(pol.merged_validation_candidates),
        runtime_allowlist_valid=pol.runtime_allowlist_valid,
        runtime_allowlist_error_code=pol.runtime_allowlist_error_code,
        runtime_allowlist_operator_message=pol.runtime_allowlist_operator_message,
        notes=list(pol.notes),
    )


def write_runtime_policy_artifacts(
    runtime_root: Optional[Path] = None,
    *,
    include_venue_catalog: bool = True,
) -> Dict[str, str]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    pol = resolve_coinbase_runtime_product_policy(include_venue_catalog=include_venue_catalog)
    snap = build_runtime_coinbase_policy_snapshot(include_venue_catalog=include_venue_catalog)

    policy_payload: Dict[str, Any] = {
        "policy_version": "coinbase_runtime_v5",
        "canonical_policy": pol.to_dict(),
        "effective_products": list(pol.effective_products),
        "products_removed_by_env": pol.products_removed_by_env,
        "products_added_by_env": pol.products_added_by_env,
        "proof_fields": {
            "runtime_active_products": pol.runtime_active_products,
            "validation_active_products": pol.validation_active_products,
            "execution_active_products": pol.execution_active_products,
            "env_override_products": pol.env_override_products,
            "effective_products_source": pol.effective_products_source,
            "products_removed_by_env": pol.products_removed_by_env,
            "products_added_by_env": pol.products_added_by_env,
            "effective_products": list(pol.effective_products),
            "merged_validation_candidates": list(pol.merged_validation_candidates),
            "runtime_allowlist_valid": pol.runtime_allowlist_valid,
        },
        "snapshot_compat": snap.to_dict(),
        "btc_usdc": pol.explain_product("BTC-USDC"),
    }
    try:
        from trading_ai.nte.execution.routing.policy.universal_runtime_policy import (
            build_universal_runtime_policy,
        )

        policy_payload["universal_crypto_runtime_policy"] = build_universal_runtime_policy(pol).to_dict()
    except Exception as exc:
        policy_payload["universal_crypto_runtime_policy"] = {"error": str(exc)}

    out_paths: Dict[str, str] = {}

    for subdir in ("control", "routing"):
        out_dir = root / "data" / subdir
        out_dir.mkdir(parents=True, exist_ok=True)
        jp = out_dir / "runtime_policy_snapshot.json"
        tp = out_dir / "runtime_policy_snapshot.txt"
        jp.write_text(json.dumps(policy_payload, indent=2, default=str), encoding="utf-8")
        lines = [
            "RUNTIME COINBASE PRODUCT POLICY (canonical)",
            "============================================",
            f"effective_products: {pol.effective_products}",
            f"effective_products_source: {pol.effective_products_source}",
            f"products_removed_by_env: {pol.products_removed_by_env}",
            f"products_added_by_env: {pol.products_added_by_env}",
            f"merged_validation_candidates: {pol.merged_validation_candidates}",
            f"runtime_allowlist_valid: {pol.runtime_allowlist_valid}",
            f"configured_default_products: {pol.configured_default_products}",
            f"env_override_products: {pol.env_override_products}",
            f"runtime_active_products: {pol.runtime_active_products}",
            f"validation_active_products: {pol.validation_active_products}",
            f"execution_active_products: {pol.execution_active_products}",
            f"validation_allowed_products: {pol.validation_allowed_products}",
            f"execution_allowed_products: {pol.execution_allowed_products}",
            f"venue_supported_count: {pol.venue_supported_count}",
            f"effective_allowed_products: {pol.effective_allowed_products}",
            "",
            "effective_disallowed_products_with_reasons:",
        ]
        for k, v in sorted(pol.effective_disallowed_products_with_reasons.items()):
            lines.append(f"  {k}: {v}")
        for n in pol.notes:
            lines.append(f"note: {n}")
        lines.extend(
            [
                "",
                "BTC-USDC:",
                json.dumps(pol.explain_product("BTC-USDC"), indent=2),
            ]
        )
        tp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        out_paths[f"runtime_policy_snapshot_json_{subdir}"] = str(jp)
        out_paths[f"runtime_policy_snapshot_txt_{subdir}"] = str(tp)

    out_paths["runtime_policy_snapshot_json"] = str(root / "data" / "control" / "runtime_policy_snapshot.json")
    out_paths["runtime_policy_snapshot_txt"] = str(root / "data" / "control" / "runtime_policy_snapshot.txt")
    return out_paths

