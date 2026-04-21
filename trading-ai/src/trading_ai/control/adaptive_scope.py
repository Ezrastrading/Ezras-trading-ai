"""
Scope purity for the adaptive operating system — which trade rows feed PnL / loss streak / expectancy.

Emergency brake and mode transitions must not treat Gate A production history as Gate B evidence,
or treat validation / micro-validation / staged harness rows as production performance unless explicit.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence, Tuple

from trading_ai.runtime_paths import ezras_runtime_root

EvaluationScope = Literal["global", "gate_a", "gate_b"]

# Strategy IDs used only for proof / validation — excluded from production adaptive PnL by default.
_PRODUCTION_EXCLUDED_STRATEGY_IDS = frozenset(
    {
        "live_execution_validation",
        "gate_b_live_micro_validation",
    }
)


def strategy_id_excluded_from_production_adaptive(strategy_id: Optional[str]) -> bool:
    sid = str(strategy_id or "").strip()
    return sid in _PRODUCTION_EXCLUDED_STRATEGY_IDS


def row_trading_gate(row: Mapping[str, Any]) -> str:
    """Normalize gate label from a trade_events row."""
    tg = str(row.get("trading_gate") or "").strip().lower()
    if tg in ("gate_a", "gate_b"):
        return tg
    # Some rows may only have gate_id (ledger-aligned)
    gid = str(row.get("gate_id") or "").strip().lower()
    if gid in ("gate_a", "gate_b"):
        return gid
    return ""


def effective_gate_label_for_scope(row: Mapping[str, Any]) -> str:
    """
    Canonical gate_a / gate_b label for scoping production adaptive PnL.

    Prefers explicit ``trading_gate`` / ``gate_id``; uses legacy strategy-id heuristic only when absent.
    """
    base = row_trading_gate(row)
    if base:
        return base
    gid, mode = resolve_gate_id_attribution_for_trade_row(row, None)
    if mode == "legacy_strategy_id_heuristic" and gid in ("gate_a", "gate_b"):
        return gid
    return ""


def resolve_gate_id_attribution_for_trade_row(
    row: Mapping[str, Any],
    diag: Optional[Mapping[str, Any]] = None,
) -> tuple[str, str]:
    """
    Returns (gate_id, attribution_mode).

    Modes: explicit_trading_gate | explicit_gate_id | legacy_diagnostic_gate_id |
    legacy_strategy_id_heuristic | unattributed
    """
    tg = str(row.get("trading_gate") or "").strip().lower()
    if tg in ("gate_a", "gate_b"):
        return tg, "explicit_trading_gate"
    gid_row = str(row.get("gate_id") or "").strip().lower()
    if gid_row in ("gate_a", "gate_b"):
        return gid_row, "explicit_gate_id"
    if diag and str(diag.get("gate_id") or "").strip():
        g = str(diag.get("gate_id")).strip().lower()
        return (g if g else "unknown"), "legacy_diagnostic_gate_id"
    sk = str(row.get("strategy_id") or "")
    low = sk.lower()
    if "gate_b" in low:
        return "gate_b", "legacy_strategy_id_heuristic"
    if "gate_a" in low:
        return "gate_a", "legacy_strategy_id_heuristic"
    return "unknown", "unattributed"


def row_counts_for_production_adaptive_pnl(row: Mapping[str, Any], *, production_only: bool) -> bool:
    """
    Whether this row's net_pnl should feed production emergency-brake / expectancy inputs.

    When ``production_only`` is False, all rows with numeric PnL are included (legacy / diagnostic).
    """
    if not production_only:
        return True
    if row.get("adaptive_pnl_exclude") is True:
        return False
    if strategy_id_excluded_from_production_adaptive(row.get("strategy_id")):
        return False
    vk = str(row.get("validation_kind") or row.get("proof_axis") or "").lower()
    if vk and ("staged" in vk or "mock" in vk or vk == "micro_validation"):
        return False
    return True


def filter_events_for_scope(
    events: Sequence[Mapping[str, Any]],
    *,
    scope: EvaluationScope,
    production_only: bool,
) -> List[Dict[str, Any]]:
    """Return rows in order, filtered by gate scope and production eligibility."""
    out: List[Dict[str, Any]] = []
    for row in events:
        if not isinstance(row, dict):
            continue
        if not row_counts_for_production_adaptive_pnl(row, production_only=production_only):
            continue
        g = effective_gate_label_for_scope(row)
        if scope == "global":
            out.append(dict(row))
        elif scope == "gate_a":
            if g == "gate_a":
                out.append(dict(row))
        elif scope == "gate_b":
            if g == "gate_b":
                out.append(dict(row))
        else:
            out.append(dict(row))
    return out


def pnl_series_from_events(events: Sequence[Mapping[str, Any]], *, max_n: int = 80) -> List[float]:
    out: List[float] = []
    for row in events:
        if not isinstance(row, dict):
            continue
        p = row.get("net_pnl")
        if p is None:
            p = row.get("net_pnl_usd")
        try:
            out.append(float(p or 0.0))
        except (TypeError, ValueError):
            continue
    if max_n > 0 and len(out) > max_n:
        out = out[-max_n:]
    return out


def consecutive_losses_from_pnls(pnls: List[float]) -> int:
    n = 0
    for p in reversed(pnls):
        if p < 0:
            n += 1
        else:
            break
    return n


def expectancy_tail(pnls: List[float], n: int) -> Optional[float]:
    if len(pnls) < 5:
        return None
    w = pnls[-n:] if n > 0 else pnls
    if not w:
        return None
    return sum(w) / len(w)


def load_trade_events_for_adaptive() -> List[Dict[str, Any]]:
    try:
        from trading_ai.nte.databank.local_trade_store import load_all_trade_events, resolve_databank_root

        db_root, _src = resolve_databank_root()
        primary = db_root / "trade_events.jsonl"
        primary_rows: List[Dict[str, Any]] = load_all_trade_events(primary) if primary.is_file() else []
        rt = (os.environ.get("EZRAS_RUNTIME_ROOT") or "").strip()
        alt_rows: List[Dict[str, Any]] = []
        if rt:
            altp = Path(rt).expanduser().resolve() / "databank" / "trade_events.jsonl"
            if altp.is_file():
                alt_rows = load_all_trade_events(altp)
        po = default_production_pnl_only()
        # Prefer explicit databank when it carries scoped production rows; otherwise fall back to the
        # session runtime file (tests often set TRADE_DATABANK_MEMORY_ROOT to an isolated empty store).
        if primary_rows:
            if rt and alt_rows and primary != (Path(rt).expanduser().resolve() / "databank" / "trade_events.jsonl"):
                gb_pri = len(filter_events_for_scope(primary_rows, scope="gate_b", production_only=po))
                gb_alt = len(filter_events_for_scope(alt_rows, scope="gate_b", production_only=po))
                if gb_pri == 0 and gb_alt > 0:
                    return alt_rows
            return primary_rows
        if alt_rows:
            return alt_rows
        return primary_rows
    except Exception:
        return []


def build_scoped_trade_history(
    *,
    scope: EvaluationScope,
    production_only: bool,
    max_n: int = 80,
) -> Tuple[List[float], Dict[str, Any]]:
    """
    Returns (pnl_series, metadata) for :class:`OperatingSnapshot` — **one scope at a time**.

    Metadata is attached to proofs so operators see contamination boundaries.
    """
    raw = load_trade_events_for_adaptive()
    filtered = filter_events_for_scope(raw, scope=scope, production_only=production_only)
    pnls = pnl_series_from_events(filtered, max_n=max_n)
    meta = {
        "adaptive_evaluation_scope": scope,
        "production_pnl_only": production_only,
        "raw_trade_event_count": len(raw),
        "scoped_row_count": len(filtered),
        "pnl_points_used": len(pnls),
        "excluded_validation_strategies": sorted(_PRODUCTION_EXCLUDED_STRATEGY_IDS),
        "scope_purity_note": (
            "Emergency brake inputs (loss streak, expectancy, loss rate) use scoped production rows only; "
            "validation/micro-validation strategy_ids are excluded when production_pnl_only=true."
        ),
    }
    return pnls, meta


def _safe_key_suffix(state_key: str) -> str:
    key = (state_key or "global").strip().lower() or "global"
    if key == "global":
        return "global"
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in key)[:64]


def operating_mode_state_path_for_key(state_key: str = "global") -> Path:
    sk = _safe_key_suffix(state_key)
    root = ezras_runtime_root() / "data" / "control"
    root.mkdir(parents=True, exist_ok=True)
    if sk == "global":
        return root / "operating_mode_state.json"
    return root / f"operating_mode_state_{sk}.json"


def diagnosis_artifact_path_for_key(state_key: str = "global") -> Path:
    sk = _safe_key_suffix(state_key)
    root = ezras_runtime_root() / "data" / "control"
    root.mkdir(parents=True, exist_ok=True)
    if sk == "global":
        return root / "last_mode_diagnosis.json"
    return root / f"last_mode_diagnosis_{sk}.json"


def operating_mode_transitions_path_for_key(state_key: str = "global") -> Path:
    sk = _safe_key_suffix(state_key)
    root = ezras_runtime_root() / "data" / "control"
    root.mkdir(parents=True, exist_ok=True)
    if sk == "global":
        return root / "operating_mode_transitions.jsonl"
    return root / f"operating_mode_transitions_{sk}.jsonl"


def env_truthy(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


def default_production_pnl_only() -> bool:
    """Default: exclude validation strategies; set ADAPTIVE_INCLUDE_VALIDATION_PNLS=true to blend (honest global diagnostic)."""
    return not env_truthy("ADAPTIVE_INCLUDE_VALIDATION_PNLS")


def audit_trade_event_row_stats(*, production_only: bool = True) -> Dict[str, Any]:
    """
    Row counts for operator truth surfaces — raw vs production-eligible vs per-gate.

    ``validation_rows_excluded`` counts rows that fail ``row_counts_for_production_adaptive_pnl``
    when ``production_only`` is True.
    """
    raw = load_trade_events_for_adaptive()
    excluded = 0
    for row in raw:
        if not isinstance(row, dict):
            continue
        if not row_counts_for_production_adaptive_pnl(row, production_only=production_only):
            excluded += 1
    ga = len(filter_events_for_scope(raw, scope="gate_a", production_only=production_only))
    gb = len(filter_events_for_scope(raw, scope="gate_b", production_only=production_only))
    glob = len(filter_events_for_scope(raw, scope="global", production_only=production_only))
    explicit_tags = 0
    legacy_heuristic = 0
    for row in raw:
        if not isinstance(row, dict):
            continue
        if not row_counts_for_production_adaptive_pnl(row, production_only=production_only):
            continue
        _, mode = resolve_gate_id_attribution_for_trade_row(row, None)
        if mode in ("explicit_trading_gate", "explicit_gate_id"):
            explicit_tags += 1
        elif mode == "legacy_strategy_id_heuristic":
            legacy_heuristic += 1
    if explicit_tags and legacy_heuristic:
        gmm = "mixed_explicit_and_legacy"
    elif explicit_tags:
        gmm = "explicit_tags"
    elif legacy_heuristic:
        gmm = "legacy_inference_only"
    else:
        gmm = "unattributed"
    return {
        "raw_trade_event_rows": len(raw),
        "production_pnl_only": production_only,
        "validation_or_nonproduction_rows_excluded": excluded,
        "gate_a_rows_seen_count": ga,
        "gate_b_rows_seen_count": gb,
        "global_production_rows_seen_count": glob,
        "gate_metrics_attribution_mode": gmm,
        "explicit_gate_tag_rows": explicit_tags,
        "legacy_strategy_heuristic_rows": legacy_heuristic,
    }
