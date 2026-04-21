from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from trading_ai.asymmetric.config import AsymmetricConfig, load_asymmetric_config
from trading_ai.global_layer.asymmetric_models import GateFamily, validate_asymmetric_trade_record
from trading_ai.runtime_paths import ezras_runtime_root


def _root(runtime_root: Optional[Path]) -> Path:
    return Path(runtime_root or ezras_runtime_root()).resolve()


def _p(runtime_root: Optional[Path], rel: str) -> Path:
    p = _root(runtime_root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


P_TRADES = "data/asymmetric/asym_trades.jsonl"
P_BATCHES = "data/asymmetric/asym_batches.jsonl"
P_SNAP = "data/asymmetric/asym_portfolio_snapshot.json"
P_FIRST100 = "data/asymmetric/asym_first100_review.json"
P_PAYOUT_DIST = "data/asymmetric/asym_payout_distribution.json"
P_CALIB = "data/asymmetric/asym_model_calibration_report.json"
P_B_ASYM_DECISION = "data/asymmetric/b_asym_last_decision.json"
P_READINESS_MD = "data/asymmetric/asym_gate_readiness_report.md"


def _append_jsonl(path: Path, row: Dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")


def record_asymmetric_trade(
    trade: Mapping[str, Any],
    *,
    runtime_root: Optional[Path] = None,
    cfg: Optional[AsymmetricConfig] = None,
) -> Dict[str, Any]:
    c = cfg or load_asymmetric_config()
    t = dict(trade)
    t.setdefault("gate_family", GateFamily.ASYMMETRIC.value)
    t.setdefault("trade_type", "asymmetric")
    t.setdefault("recorded_at_unix", time.time())
    errs = validate_asymmetric_trade_record(t, allow_probe_without_batch=bool(c.allow_single_probe_without_batch))
    if errs:
        return {"ok": False, "errors": errs}
    _append_jsonl(_p(runtime_root, P_TRADES), t)
    return {"ok": True}


def record_asymmetric_batch(
    batch: Mapping[str, Any],
    *,
    runtime_root: Optional[Path] = None,
) -> Dict[str, Any]:
    b = dict(batch)
    b.setdefault("recorded_at_unix", time.time())
    if not str(b.get("batch_id") or "").strip():
        return {"ok": False, "errors": ["missing_batch_id"]}
    if not str(b.get("gate_id") or "").strip():
        return {"ok": False, "errors": ["missing_gate_id"]}
    _append_jsonl(_p(runtime_root, P_BATCHES), b)
    return {"ok": True}


@dataclass(frozen=True)
class PayoutDistribution:
    zero_x: int = 0
    two_x_plus: int = 0
    five_x_plus: int = 0
    ten_x_plus: int = 0
    twentyfive_x_plus: int = 0
    max_multiple: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "zero_x": self.zero_x,
            "two_x_plus": self.two_x_plus,
            "five_x_plus": self.five_x_plus,
            "ten_x_plus": self.ten_x_plus,
            "twentyfive_x_plus": self.twentyfive_x_plus,
            "max_multiple": self.max_multiple,
        }


def _multiple_from_trade(t: Mapping[str, Any]) -> Optional[float]:
    # Prefer explicit multiple, else infer from realized pnl/max_loss.
    m = t.get("realized_multiple")
    if m is not None:
        try:
            return float(m)
        except (TypeError, ValueError):
            pass
    pnl = t.get("realized_pnl_usd")
    ml = t.get("max_loss_usd") or t.get("capital_deployed_usd")
    try:
        if pnl is None or ml is None:
            return None
        mlv = float(ml)
        if mlv <= 0:
            return None
        return 1.0 + float(pnl) / mlv
    except (TypeError, ValueError):
        return None


def recompute_asymmetric_snapshots(
    *,
    runtime_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Builds portfolio snapshot + first100 review shells from the append-only trades log.
    """
    trades_path = _p(runtime_root, P_TRADES)
    if not trades_path.is_file():
        snap = {"ok": True, "truth_version": "asym_portfolio_snapshot_v1", "total_trades": 0}
        _p(runtime_root, P_SNAP).write_text(json.dumps(snap, indent=2) + "\n", encoding="utf-8")
        return snap

    rows: List[Dict[str, Any]] = []
    for ln in trades_path.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            j = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if isinstance(j, dict):
            rows.append(j)

    total = len(rows)
    realized = [r for r in rows if r.get("resolved") is True or r.get("realized_pnl_usd") is not None]
    unresolved = total - len(realized)
    pnl = 0.0
    for r in realized:
        try:
            pnl += float(r.get("realized_pnl_usd") or 0.0)
        except (TypeError, ValueError):
            pass

    # payout distribution
    dist = PayoutDistribution()
    maxm = 0.0
    for r in realized:
        m = _multiple_from_trade(r)
        if m is None:
            continue
        maxm = max(maxm, m)
        if m <= 0.01:
            dist = PayoutDistribution(**{**dist.to_dict(), "zero_x": dist.zero_x + 1})  # type: ignore[arg-type]
        if m >= 2:
            dist = PayoutDistribution(**{**dist.to_dict(), "two_x_plus": dist.two_x_plus + 1})  # type: ignore[arg-type]
        if m >= 5:
            dist = PayoutDistribution(**{**dist.to_dict(), "five_x_plus": dist.five_x_plus + 1})  # type: ignore[arg-type]
        if m >= 10:
            dist = PayoutDistribution(**{**dist.to_dict(), "ten_x_plus": dist.ten_x_plus + 1})  # type: ignore[arg-type]
        if m >= 25:
            dist = PayoutDistribution(**{**dist.to_dict(), "twentyfive_x_plus": dist.twentyfive_x_plus + 1})  # type: ignore[arg-type]

    dist = PayoutDistribution(
        zero_x=dist.zero_x,
        two_x_plus=dist.two_x_plus,
        five_x_plus=dist.five_x_plus,
        ten_x_plus=dist.ten_x_plus,
        twentyfive_x_plus=dist.twentyfive_x_plus,
        max_multiple=maxm,
    )
    _p(runtime_root, P_PAYOUT_DIST).write_text(json.dumps(dist.to_dict(), indent=2) + "\n", encoding="utf-8")

    snap = {
        "ok": True,
        "truth_version": "asym_portfolio_snapshot_v1",
        "generated_at_unix": time.time(),
        "total_trades": total,
        "resolved_trades": len(realized),
        "unresolved_trades": unresolved,
        "realized_pnl_total_usd": float(pnl),
        "payout_distribution": dist.to_dict(),
        "honesty": "Asymmetric snapshot is separate from core; unresolved positions are not treated as failures.",
    }
    _p(runtime_root, P_SNAP).write_text(json.dumps(snap, indent=2) + "\n", encoding="utf-8")

    # Batch audit shell
    try:
        batches_path = _p(runtime_root, P_BATCHES)
        b_rows: List[Dict[str, Any]] = []
        if batches_path.is_file():
            for ln in batches_path.read_text(encoding="utf-8").splitlines():
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    j = json.loads(ln)
                except json.JSONDecodeError:
                    continue
                if isinstance(j, dict):
                    b_rows.append(j)
        audit = {
            "truth_version": "asym_batch_audit_v1",
            "generated_at_unix": time.time(),
            "batch_count": len(b_rows),
            "recent_batches": b_rows[-20:],
            "honesty": "Batch audit is a raw append-only snapshot; analysis layer can compute correlations/overlap later.",
        }
        _p(runtime_root, "data/asymmetric/asym_batch_audit.json").write_text(
            json.dumps(audit, indent=2) + "\n", encoding="utf-8"
        )
    except Exception:
        pass

    first100 = {
        "truth_version": "asym_first100_review_v1",
        "generated_at_unix": time.time(),
        "target_trade_count": 100,
        "current_trade_count": total,
        "ready_for_judgment": total >= 100,
        "portfolio_snapshot_ref": P_SNAP,
        "payout_distribution_ref": P_PAYOUT_DIST,
        "required_batch_behavior": True,
        "honesty": "Do not judge asym off win-rate; judge EV calibration + basket behavior + payout distribution.",
    }
    _p(runtime_root, P_FIRST100).write_text(json.dumps(first100, indent=2) + "\n", encoding="utf-8")

    # Readiness report (MD): intentionally blunt; low win-rate is expected.
    try:
        lines = [
            "# Asymmetric Gate Readiness Report",
            "",
            f"- Generated: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}",
            f"- Total asym trades recorded: {total}",
            f"- Resolved: {len(realized)}",
            f"- Unresolved: {unresolved}",
            "",
            "## Verdict (conservative)",
            "- If `total_trades < 100`: **DO NOT JUDGE**. Continue paper/shadow or micro-live only.",
            "- If EV is not explicitly modeled (or edge assumptions are hand-wavy): **NOT READY**.",
            "",
            "## Required evidence",
            "- Batch artifacts exist (`asym_batches.jsonl`, `asym_batch_audit.json`).",
            "- Payout distribution exists (`asym_payout_distribution.json`).",
            "- First-100 protocol exists (`asym_first100_review.json`).",
            "",
            "## Non-negotiables checked",
            "- Core/asym reporting separated (asym is not written to `trades_clean.csv`).",
            "- Unresolved positions are not treated as failures in the snapshot.",
            "",
            "## Notes",
            "- Low win rate is expected; focus on basket EV, calibration, and whether winners are cut early.",
            "",
        ]
        _p(runtime_root, P_READINESS_MD).write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        pass

    # calibration report is a placeholder until per-venue EV adapters are wired
    b_asym = {}
    try:
        pdec = _root(runtime_root) / P_B_ASYM_DECISION
        if pdec.is_file():
            b_asym = json.loads(pdec.read_text(encoding="utf-8"))
            if not isinstance(b_asym, dict):
                b_asym = {}
    except Exception:
        b_asym = {}
    calib = {
        "truth_version": "asym_model_calibration_report_v1",
        "generated_at_unix": time.time(),
        "note": "Calibration requires comparing forecast probabilities vs realized outcomes; partial until enough resolved trades exist.",
        "b_asym_last_decision_ref": P_B_ASYM_DECISION,
        "b_asym_last_scan_counts": (b_asym.get("scan_counts") if isinstance(b_asym, dict) else None),
    }
    _p(runtime_root, P_CALIB).write_text(json.dumps(calib, indent=2) + "\n", encoding="utf-8")
    return snap

