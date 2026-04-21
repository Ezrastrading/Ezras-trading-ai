from __future__ import annotations

import os
import json
from dataclasses import dataclass


def _env_bool(key: str, default: bool = False) -> bool:
    raw = (os.environ.get(key) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "y", "on")


def _env_float(key: str, default: float) -> float:
    raw = (os.environ.get(key) or "").strip()
    if not raw:
        return float(default)
    try:
        return float(raw)
    except ValueError:
        return float(default)


def _env_int(key: str, default: int) -> int:
    raw = (os.environ.get(key) or "").strip()
    if not raw:
        return int(default)
    try:
        return int(raw)
    except ValueError:
        return int(default)


@dataclass(frozen=True)
class AsymmetricConfig:
    enabled: bool
    # Capital isolation
    core_capital_pct: float
    asym_capital_pct: float
    asym_capital_pct_per_avenue: dict[str, float]  # avenue_id -> fraction of asym bucket
    asym_reserve_cash_pct: float
    # Hard risk rails
    asym_max_position_pct_of_total: float
    asym_max_position_pct_of_asym_bucket: float
    asym_max_open_positions: int
    asym_max_batch_deployment_pct: float
    asym_redeploy_winner_profit_pct: float
    # Selection + batching
    min_ev_usd: float
    min_ev_per_dollar: float
    batch_size: int
    allow_single_probe_without_batch: bool
    # Scope control
    venue_allowlist: tuple[str, ...]
    avenue_allowlist: tuple[str, ...]
    gate_id_map: dict[str, str]  # avenue_id -> gate_id (e.g. B -> B_ASYM)


def load_asymmetric_config() -> AsymmetricConfig:
    """
    Asymmetric Gate is treated as a separate system with strict capital isolation.

    Default is DISABLED. Turning it on does not automatically wire execution for a venue; it only
    enables registry visibility + allocator/tracker primitives.
    """
    enabled = _env_bool("ASYMMETRIC_ENABLED", False)

    # Split total capital into core vs asym (non-negotiable isolation)
    core_capital_pct = max(0.50, min(0.99, _env_float("CORE_CAPITAL_PCT", 0.90)))
    asym_capital_pct = max(0.01, min(0.50, _env_float("ASYM_CAPITAL_PCT", 0.10)))
    # If both are set inconsistently, normalize (core wins by default).
    if core_capital_pct + asym_capital_pct > 1.0:
        asym_capital_pct = max(0.0, 1.0 - core_capital_pct)

    # Within asym bucket, per-avenue allocation fractions (must sum <= 1.0; remainder stays unallocated cash)
    # Supports either:
    # - ASYM_CAPITAL_PCT_PER_AVENUE='{"A":0.2,"B":0.5,"C":0.3}'
    # - or per-avenue env vars (defaults below)
    asym_capital_pct_per_avenue: dict[str, float] = {}
    raw_map = (os.environ.get("ASYM_CAPITAL_PCT_PER_AVENUE") or "").strip()
    if raw_map:
        try:
            parsed = json.loads(raw_map)
            if isinstance(parsed, dict):
                for k, v in parsed.items():
                    kk = str(k).strip().upper()
                    if kk in ("A", "B", "C"):
                        try:
                            asym_capital_pct_per_avenue[kk] = float(v)
                        except (TypeError, ValueError):
                            continue
        except json.JSONDecodeError:
            asym_capital_pct_per_avenue = {}
    if not asym_capital_pct_per_avenue:
        a_pct = max(0.0, min(1.0, _env_float("ASYM_CAPITAL_PCT_AVENUE_A", 0.20)))
        b_pct = max(0.0, min(1.0, _env_float("ASYM_CAPITAL_PCT_AVENUE_B", 0.50)))
        c_pct = max(0.0, min(1.0, _env_float("ASYM_CAPITAL_PCT_AVENUE_C", 0.30)))
        asym_capital_pct_per_avenue = {"A": a_pct, "B": b_pct, "C": c_pct}
    if sum(asym_capital_pct_per_avenue.values()) > 1.0 + 1e-9:
        # Normalize proportionally to preserve intent while avoiding over-allocation.
        s = sum(asym_capital_pct_per_avenue.values())
        if s > 0:
            asym_capital_pct_per_avenue = {k: v / s for k, v in asym_capital_pct_per_avenue.items()}

    asym_reserve_cash_pct = max(0.0, min(0.95, _env_float("ASYM_RESERVE_CASH_PCT", 0.20)))

    # Risk rails
    asym_max_position_pct_of_total = max(0.0, min(0.05, _env_float("ASYM_MAX_POSITION_PCT_OF_TOTAL", 0.005)))
    asym_max_position_pct_of_asym_bucket = max(0.0, min(0.50, _env_float("ASYM_MAX_POSITION_PCT_OF_ASYM_BUCKET", 0.08)))
    asym_max_open_positions = max(1, min(5000, _env_int("ASYM_MAX_OPEN_POSITIONS", 50)))
    asym_max_batch_deployment_pct = max(0.01, min(1.0, _env_float("ASYM_MAX_BATCH_DEPLOYMENT_PCT", 0.35)))
    asym_redeploy_winner_profit_pct = max(0.0, min(1.0, _env_float("ASYM_REDEPLOY_WINNER_PROFIT_PCT", 0.50)))

    # EV thresholds (net of costs handled in EV engine)
    min_ev_usd = _env_float("ASYM_MIN_EV_USD", 0.02)
    min_ev_per_dollar = max(0.0, _env_float("ASYM_MIN_EV_PER_DOLLAR", 0.02))

    # Batch behavior
    batch_size = max(1, min(500, _env_int("ASYM_BATCH_SIZE", 50)))
    allow_single_probe_without_batch = _env_bool("ASYM_ALLOW_SINGLE_PROBE_WITHOUT_BATCH", False)

    # Scope allowlists
    allow_venues = (os.environ.get("ASYM_VENUE_ALLOWLIST") or os.environ.get("ASYMMETRIC_VENUE_ALLOWLIST") or "kalshi").strip()
    venues = tuple(x.strip().lower() for x in allow_venues.split(",") if x.strip())
    allow_avenues = (os.environ.get("ASYM_AVENUE_ALLOWLIST") or "B").strip()
    avenues = tuple(x.strip().upper() for x in allow_avenues.split(",") if x.strip())

    # Canonical asym gate ids per avenue (used for tagging + review packets)
    gate_id_map = {
        "A": (os.environ.get("ASYM_GATE_ID_A") or "A_ASYM").strip(),
        "B": (os.environ.get("ASYM_GATE_ID_B") or "B_ASYM").strip(),
        "C": (os.environ.get("ASYM_GATE_ID_C") or "C_ASYM").strip(),
    }
    return AsymmetricConfig(
        enabled=enabled,
        core_capital_pct=core_capital_pct,
        asym_capital_pct=asym_capital_pct,
        asym_capital_pct_per_avenue=asym_capital_pct_per_avenue,
        asym_reserve_cash_pct=asym_reserve_cash_pct,
        asym_max_position_pct_of_total=asym_max_position_pct_of_total,
        asym_max_position_pct_of_asym_bucket=asym_max_position_pct_of_asym_bucket,
        asym_max_open_positions=asym_max_open_positions,
        asym_max_batch_deployment_pct=asym_max_batch_deployment_pct,
        asym_redeploy_winner_profit_pct=asym_redeploy_winner_profit_pct,
        min_ev_usd=min_ev_usd,
        min_ev_per_dollar=min_ev_per_dollar,
        batch_size=batch_size,
        allow_single_probe_without_batch=allow_single_probe_without_batch,
        venue_allowlist=venues or ("kalshi",),
        avenue_allowlist=avenues or ("B",),
        gate_id_map=gate_id_map,
    )

