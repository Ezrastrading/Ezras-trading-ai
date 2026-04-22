"""
Controlled micro-live enablement — fail-closed artifacts and gates.

No venue orders from this module. Operator must:
1. Export required env (see ``REQUIRED_ENV_NAMES``).
2. Run ``live-micro-*`` deployment subcommands to write proof JSON under ``data/control/``.
3. Set ``EZRA_LIVE_MICRO_ENABLED=true`` only after preflight + readiness pass (typically in ``ops.env``).

Execution paths that touch live Coinbase consult :func:`assert_live_micro_runtime_contract` when
``EZRA_LIVE_MICRO_ENABLED`` is truthy.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

TRUTH_VERSION = "live_micro_enablement_v1"

OPERATOR_CONFIRM_ENV = "EZRA_LIVE_MICRO_OPERATOR_CONFIRM"
OPERATOR_CONFIRM_VALUE = "I_ACCEPT_MICRO_LIVE_CAPITAL_RISK_AND_LIMITS"

MICRO_ENABLED_ENV = "EZRA_LIVE_MICRO_ENABLED"

ARTIFACT_REQUEST = "data/control/live_enablement_request.json"
ARTIFACT_PREFLIGHT = "data/control/live_preflight.json"
ARTIFACT_READINESS = "data/control/live_micro_readiness.json"
ARTIFACT_START_RECEIPT = "data/control/live_start_receipt.json"
ARTIFACT_GUARD_PROOF = "data/control/live_guard_proof.json"
ARTIFACT_SESSION_LIMITS = "data/control/live_session_limits.json"
ARTIFACT_DISABLE_RECEIPT = "data/control/live_disable_receipt.json"
ARTIFACT_SESSION_STATE = "data/control/live_micro_session_state.json"
ARTIFACT_FORCE_HALT = "data/control/live_micro_force_halt.json"
ARTIFACT_VERIFY_CONTRACT = "data/control/live_micro_verify_contract.json"

REQUIRED_LIMIT_ENVS = (
    "EZRA_LIVE_MICRO_MAX_NOTIONAL_USD",
    "EZRA_LIVE_MICRO_MAX_DAILY_LOSS_USD",
    "EZRA_LIVE_MICRO_MAX_TOTAL_EXPOSURE_USD",
    "EZRA_LIVE_MICRO_ALLOWED_PRODUCTS",
    "EZRA_LIVE_MICRO_ALLOWED_AVENUE",
    "EZRA_LIVE_MICRO_ALLOWED_GATE",
    "EZRA_LIVE_MICRO_MAX_TRADES_PER_SESSION",
    "EZRA_LIVE_MICRO_COOLDOWN_SEC",
    "EZRA_LIVE_MICRO_MAX_CONCURRENT_POSITIONS",
)


def _truthy_env(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


def live_micro_runtime_enabled() -> bool:
    return _truthy_env(MICRO_ENABLED_ENV)


def _ctrl(root: Path) -> Path:
    p = root / "data" / "control"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _atomic_write(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _age_sec(path: Path) -> Optional[float]:
    if not path.is_file():
        return None
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return None


def collect_required_env_snapshot() -> Dict[str, Any]:
    snap: Dict[str, Any] = {}
    for name in (OPERATOR_CONFIRM_ENV, *REQUIRED_LIMIT_ENVS, "EZRA_LIVE_MICRO_ALLOW_MULTI_PRODUCT"):
        raw = os.environ.get(name)
        if raw is None or not str(raw).strip():
            snap[name] = None
        else:
            snap[name] = "***" if "SECRET" in name.upper() else str(raw).strip()
    snap[OPERATOR_CONFIRM_ENV] = (
        "SET" if (os.environ.get(OPERATOR_CONFIRM_ENV) or "").strip() == OPERATOR_CONFIRM_VALUE else "INVALID_OR_MISSING"
    )
    return snap


def validate_required_micro_env_or_errors() -> List[str]:
    errs: List[str] = []
    if (os.environ.get(OPERATOR_CONFIRM_ENV) or "").strip() != OPERATOR_CONFIRM_VALUE:
        errs.append(f"missing_or_invalid_{OPERATOR_CONFIRM_ENV}")
    for name in REQUIRED_LIMIT_ENVS:
        v = (os.environ.get(name) or "").strip()
        if not v:
            errs.append(f"missing_env:{name}")
            continue
        if name.endswith("_USD"):
            try:
                x = float(v)
                if x <= 0:
                    errs.append(f"invalid_positive_float:{name}")
            except ValueError:
                errs.append(f"invalid_float:{name}")
        if name == "EZRA_LIVE_MICRO_MAX_TRADES_PER_SESSION" or name.endswith("COOLDOWN_SEC") or name.endswith(
            "CONCURRENT_POSITIONS"
        ):
            try:
                n = int(float(v))
                if n < 0:
                    errs.append(f"invalid_non_negative_int:{name}")
            except ValueError:
                errs.append(f"invalid_int:{name}")
    if not (os.environ.get("EZRA_LIVE_MICRO_ALLOWED_PRODUCTS") or "").strip():
        errs.append("missing_products")
    return errs


def write_live_enablement_request(runtime_root: Path, *, operator: str, note: str) -> Dict[str, Any]:
    root = Path(runtime_root).resolve()
    errs = validate_required_micro_env_or_errors()
    payload = {
        "truth_version": TRUTH_VERSION,
        "artifact": "live_enablement_request",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "runtime_root": str(root),
        "operator": (operator or "").strip(),
        "note": (note or "").strip(),
        "env_contract_ok": len(errs) == 0,
        "env_errors": errs,
        "env_snapshot_non_secret": collect_required_env_snapshot(),
        "honesty": "Request records intent; it does not enable live. Set EZRA_LIVE_MICRO_ENABLED only after preflight+readiness.",
    }
    _atomic_write(root / ARTIFACT_REQUEST, payload)
    return payload


def write_live_session_limits(runtime_root: Path) -> Dict[str, Any]:
    root = Path(runtime_root).resolve()
    errs = validate_required_micro_env_or_errors()
    lim = {
        "truth_version": TRUTH_VERSION,
        "artifact": "live_session_limits",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "runtime_root": str(root),
        "max_notional_usd": float(os.environ.get("EZRA_LIVE_MICRO_MAX_NOTIONAL_USD") or 0),
        "max_daily_loss_usd": float(os.environ.get("EZRA_LIVE_MICRO_MAX_DAILY_LOSS_USD") or 0),
        "max_total_exposure_usd": float(os.environ.get("EZRA_LIVE_MICRO_MAX_TOTAL_EXPOSURE_USD") or 0),
        "allowed_products": [x.strip().upper() for x in (os.environ.get("EZRA_LIVE_MICRO_ALLOWED_PRODUCTS") or "").split(",") if x.strip()],
        "allowed_avenue": (os.environ.get("EZRA_LIVE_MICRO_ALLOWED_AVENUE") or "").strip().upper(),
        "allowed_gate": (os.environ.get("EZRA_LIVE_MICRO_ALLOWED_GATE") or "").strip().lower(),
        "max_trades_per_session": int(float(os.environ.get("EZRA_LIVE_MICRO_MAX_TRADES_PER_SESSION") or 0)),
        "cooldown_sec": int(float(os.environ.get("EZRA_LIVE_MICRO_COOLDOWN_SEC") or 0)),
        "max_concurrent_positions": int(float(os.environ.get("EZRA_LIVE_MICRO_MAX_CONCURRENT_POSITIONS") or 1)),
        "allow_multi_product": _truthy_env("EZRA_LIVE_MICRO_ALLOW_MULTI_PRODUCT"),
        "contract_ok": len(errs) == 0,
        "contract_errors": errs,
    }
    _atomic_write(root / ARTIFACT_SESSION_LIMITS, lim)
    return lim


def run_live_micro_preflight(runtime_root: Path, *, max_artifact_age_sec: float = 172800.0) -> Dict[str, Any]:
    root = Path(runtime_root).resolve()
    os.environ["EZRAS_RUNTIME_ROOT"] = str(root)
    blockers: List[str] = []
    checks: Dict[str, Any] = {}

    errs = validate_required_micro_env_or_errors()
    checks["required_env"] = {"ok": len(errs) == 0, "errors": errs}
    if errs:
        blockers.extend(errs)

    try:
        from trading_ai.control.kill_switch import kill_switch_active

        ks = kill_switch_active()
        checks["kill_switch"] = {"active": bool(ks), "ok": not bool(ks)}
        if ks:
            blockers.append("kill_switch_active")
    except Exception as exc:
        checks["kill_switch"] = {"ok": False, "error": type(exc).__name__}
        blockers.append("kill_switch_check_failed")

    smoke_p = root / "data" / "control" / "deployed_environment_smoke.json"
    smoke = _read_json(smoke_p)
    age = _age_sec(smoke_p)
    lm_probe = smoke.get("live_micro_private_build") if isinstance(smoke, dict) else None
    lm_ok = isinstance(lm_probe, dict) and bool(lm_probe.get("ok"))
    smoke_ok = bool(smoke.get("truth_version")) and bool((smoke.get("live_disabled") or {}).get("ok"))
    stale = age is None or age > max_artifact_age_sec
    checks["deployed_environment_smoke"] = {"ok": smoke_ok and not stale and lm_ok, "age_sec": age, "stale": stale}
    checks["live_micro_private_build"] = lm_probe if isinstance(lm_probe, dict) else {"ok": False, "error": "missing_or_invalid"}
    if not smoke_p.is_file():
        blockers.append("missing_deployed_environment_smoke_json")
    elif stale:
        blockers.append("stale_deployed_environment_smoke")
    elif not smoke_ok:
        blockers.append("deployed_environment_smoke_not_ok")
    elif not lm_ok:
        if not isinstance(smoke, dict) or not isinstance(smoke.get("live_micro_private_build"), dict):
            blockers.append("deployed_environment_smoke_missing_live_micro_private_build")
        else:
            blockers.append("live_micro_private_build_not_ok")

    micro_p = root / "data" / "control" / "micro_trade_readiness.json"
    micro = _read_json(micro_p)
    m_ok = micro.get("ok") is True
    mage = _age_sec(micro_p)
    mstale = mage is None or mage > max_artifact_age_sec
    checks["micro_trade_readiness"] = {"ok": m_ok and not mstale, "age_sec": mage, "stale": mstale}
    if not micro_p.is_file():
        blockers.append("missing_micro_trade_readiness_json")
    elif mstale:
        blockers.append("stale_micro_trade_readiness")
    elif not m_ok:
        blockers.append("micro_trade_readiness_not_ok")

    gov = root / "shark" / "memory" / "global" / "joint_review_latest.json"
    checks["joint_review_present"] = {"ok": gov.is_file()}
    if not gov.is_file():
        blockers.append("missing_joint_review_latest")

    try:
        from trading_ai.deployment.operator_env_contracts import missing_coinbase_credential_env_vars

        miss = missing_coinbase_credential_env_vars()
        checks["coinbase_credentials"] = {"ok": len(miss) == 0, "missing": miss}
        if miss:
            blockers.append("coinbase_credentials_missing")
    except Exception as exc:
        checks["coinbase_credentials"] = {"ok": False, "error": type(exc).__name__}
        blockers.append("coinbase_credentials_check_failed")

    try:
        from trading_ai.safety.failsafe_guard import load_failsafe_state

        st = load_failsafe_state(runtime_root=root)
        checks["failsafe_state_readable"] = {"ok": True, "keys": list((st or {}).keys())[:12]}
    except Exception as exc:
        checks["failsafe_state_readable"] = {"ok": False, "error": type(exc).__name__}
        blockers.append("failsafe_unreadable")

    out = {
        "truth_version": TRUTH_VERSION,
        "artifact": "live_preflight",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "runtime_root": str(root),
        "ok": len(blockers) == 0,
        "blockers": blockers,
        "checks": checks,
    }
    _atomic_write(root / ARTIFACT_PREFLIGHT, out)
    return out


def run_live_micro_readiness(runtime_root: Path) -> Dict[str, Any]:
    """Re-run preflight checks plus execution-surface import proofs."""
    root = Path(runtime_root).resolve()
    pre = run_live_micro_preflight(root)
    blockers = list(pre.get("blockers") or [])
    extra: Dict[str, Any] = {}

    for mod in (
        "trading_ai.nte.hardening.live_order_guard",
        "trading_ai.shark.mission",
        "trading_ai.runtime.trade_snapshots",
        "trading_ai.runtime_proof.first_twenty_judge",
        "trading_ai.automation.post_trade_hub",
    ):
        try:
            __import__(mod)
            extra[mod] = True
        except Exception as exc:
            extra[mod] = f"import_failed:{type(exc).__name__}"
            blockers.append(f"import_failed:{mod}")

    try:
        db = root / "databank"
        db.mkdir(parents=True, exist_ok=True)
        (db / ".live_micro_readiness_probe").write_text("ok\n", encoding="utf-8")
        extra["databank_writable"] = True
    except Exception as exc:
        extra["databank_writable"] = str(exc)
        blockers.append("databank_not_writable")

    for d in ("logs", "state"):
        try:
            (root / d).mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            blockers.append(f"mkdir_failed:{d}:{type(exc).__name__}")

    out = {
        "truth_version": TRUTH_VERSION,
        "artifact": "live_micro_readiness",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "runtime_root": str(root),
        "preflight_ok": bool(pre.get("ok")),
        "ok": len(blockers) == 0,
        "blockers": blockers,
        "import_surface": extra,
    }
    _atomic_write(root / ARTIFACT_READINESS, out)
    return out


def build_live_guard_proof(runtime_root: Path) -> Dict[str, Any]:
    root = Path(runtime_root).resolve()
    os.environ["EZRAS_RUNTIME_ROOT"] = str(root)
    proof: Dict[str, Any] = {
        "truth_version": TRUTH_VERSION,
        "artifact": "live_guard_proof",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "runtime_root": str(root),
        "micro_runtime_enabled_env": live_micro_runtime_enabled(),
        "nte_execution_mode": (os.environ.get("NTE_EXECUTION_MODE") or "").strip(),
        "nte_live_trading_enabled": (os.environ.get("NTE_LIVE_TRADING_ENABLED") or "").strip(),
        "coinbase_execution_enabled": (os.environ.get("COINBASE_EXECUTION_ENABLED") or "").strip(),
    }
    try:
        from trading_ai.control.kill_switch import kill_switch_active

        proof["kill_switch_active"] = bool(kill_switch_active())
    except Exception as exc:
        proof["kill_switch_active"] = f"error:{type(exc).__name__}"
    try:
        from trading_ai.global_layer.governance_order_gate import load_joint_review_snapshot

        proof["joint_review_snapshot"] = load_joint_review_snapshot()
    except Exception as exc:
        proof["joint_review_snapshot"] = {"error": type(exc).__name__}
    _atomic_write(root / ARTIFACT_GUARD_PROOF, proof)
    return proof


def assert_live_micro_runtime_contract(runtime_root: Path, *, phase: str) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Fail-closed gate for live venue paths when ``EZRA_LIVE_MICRO_ENABLED`` is true.

    Returns (ok, error_message, audit).
    """
    if not live_micro_runtime_enabled():
        return True, "", {"live_micro_runtime_enabled": False, "phase": phase}

    root = Path(runtime_root).resolve()
    audit: Dict[str, Any] = {"phase": phase, "runtime_root": str(root), "live_micro_runtime_enabled": True}

    halt_p = root / ARTIFACT_FORCE_HALT
    if halt_p.is_file():
        audit["reason"] = "operator_force_halt_file"
        return (
            False,
            "live_micro:operator_pause — remove data/control/live_micro_force_halt.json (deployment live-micro-resume) before live orders",
            audit,
        )

    lim = _read_json(root / ARTIFACT_SESSION_LIMITS)
    if not lim.get("contract_ok"):
        audit["reason"] = "live_session_limits_missing_or_invalid"
        return False, "live_micro:live_session_limits.json missing or env contract failed when micro runtime enabled", audit

    pf = _read_json(root / ARTIFACT_PREFLIGHT)
    if not pf.get("ok"):
        audit["reason"] = "live_preflight_not_ok"
        return False, "live_micro:live_preflight.json missing or not ok — run deployment live-micro-preflight", audit

    rd = _read_json(root / ARTIFACT_READINESS)
    if not rd.get("ok"):
        audit["reason"] = "live_micro_readiness_not_ok"
        return False, "live_micro:live_micro_readiness.json missing or not ok — run deployment live-micro-readiness", audit

    req = _read_json(root / ARTIFACT_REQUEST)
    if not req.get("env_contract_ok"):
        audit["reason"] = "live_enablement_request_not_ok"
        return False, "live_micro:live_enablement_request.json missing or env_contract_ok false", audit

    errs = validate_required_micro_env_or_errors()
    if errs:
        audit["reason"] = "env_drift"
        return False, "live_micro:required env drift vs enablement request — " + ";".join(errs), audit

    audit["ok"] = True
    return True, "", audit


def write_live_micro_verify_contract(runtime_root: Path) -> Dict[str, Any]:
    """
    Write an on-disk contract verdict for micro-live.

    Always writes under data/control/ so systemd/ops can prove the gate decision that allowed
    a live-capable process env.
    """
    root = Path(runtime_root).resolve()
    ok, err, audit = assert_live_micro_runtime_contract(root, phase="live_micro_verify_contract_artifact")
    out = {
        "truth_version": TRUTH_VERSION,
        "artifact": "live_micro_verify_contract",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "runtime_root": str(root),
        "micro_runtime_enabled_env": live_micro_runtime_enabled(),
        "ok": bool(ok),
        "error": err,
        "audit": audit,
    }
    _atomic_write(root / ARTIFACT_VERIFY_CONTRACT, out)
    return out


_ENTRY_ACTIONS = frozenset({"place_limit_entry", "place_market_entry", "replace_order"})


def _micro_counts_new_buy_entry(action: str, order_side: Optional[str]) -> bool:
    return action in _ENTRY_ACTIONS and str(order_side or "").strip().upper() == "BUY"


def enforce_live_micro_order_guards(
    *,
    runtime_root: Path,
    avenue_id: str,
    product_id: str,
    execution_gate: str,
    quote_notional: Optional[float],
    action: str,
    order_side: Optional[str] = None,
) -> None:
    """Raise RuntimeError if live micro limits violated (called from live_order_guard)."""
    if not live_micro_runtime_enabled():
        return
    root = Path(runtime_root).resolve()
    ok, err, _ = assert_live_micro_runtime_contract(root, phase="live_order_guard")
    if not ok:
        raise RuntimeError(err)

    lim = _read_json(root / ARTIFACT_SESSION_LIMITS)
    max_n = float(lim.get("max_notional_usd") or 0.0)
    qn = float(quote_notional or 0.0)
    if max_n > 0 and qn > max_n + 1e-9:
        raise RuntimeError(f"live_micro:quote_notional_exceeds_cap:{qn}>{max_n}")

    pid = (product_id or "").strip().upper()
    allowed = [str(x).strip().upper() for x in (lim.get("allowed_products") or []) if str(x).strip()]
    if not allowed:
        raise RuntimeError("live_micro:allowed_products_empty")
    if not lim.get("allow_multi_product") and len(allowed) != 1:
        raise RuntimeError("live_micro:exactly_one_product_required_when_multi_product_disabled")

    if allowed and pid not in allowed:
        raise RuntimeError(f"live_micro:product_not_allowed:{pid}")

    av = (lim.get("allowed_avenue") or "").strip().upper()
    if av and (avenue_id or "").strip().upper() != av and not (
        av == "A" and (avenue_id or "").lower() in ("coinbase", "a")
    ):
        raise RuntimeError(f"live_micro:avenue_not_allowed:{avenue_id}")

    g = (lim.get("allowed_gate") or "").strip().lower()
    if g and (execution_gate or "").strip().lower() != g:
        raise RuntimeError(f"live_micro:gate_not_allowed:{execution_gate}")

    max_exp = float(lim.get("max_total_exposure_usd") or 0.0)
    max_daily = float(lim.get("max_daily_loss_usd") or 0.0)
    st_path = root / ARTIFACT_SESSION_STATE
    st = _read_json(st_path)
    spent = float(st.get("session_notional_usd") or 0.0)
    if max_exp > 0 and spent + qn > max_exp + 1e-9:
        raise RuntimeError(f"live_micro:exceeds_max_total_exposure:{spent}+{qn}>{max_exp}")

    risk_p = root / "data" / "risk" / "risk_state.json"
    if max_daily > 0:
        if not risk_p.is_file():
            raise RuntimeError("live_micro:missing_risk_state_json_for_daily_loss_cap")
        try:
            rs = json.loads(risk_p.read_text(encoding="utf-8"))
            dpnl = float(rs.get("daily_pnl_usd") or rs.get("day_pnl_usd") or 0.0)
            if dpnl <= -max_daily - 1e-9:
                raise RuntimeError(f"live_micro:max_daily_loss_exceeded:{dpnl}")
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"live_micro:risk_state_unreadable:{type(exc).__name__}") from exc

    new_buy = _micro_counts_new_buy_entry(action, order_side)
    now = time.time()
    max_tr = int(lim.get("max_trades_per_session") or 0)
    cd = int(lim.get("cooldown_sec") or 0)
    n_done = int(st.get("session_trades_completed") or 0)
    if new_buy:
        if max_tr >= 0 and n_done >= max_tr:
            raise RuntimeError("live_micro:max_trades_per_session_reached")
        last_ts = float(st.get("last_trade_completed_ts") or 0.0)
        if cd > 0 and last_ts > 0 and (now - last_ts) < float(cd):
            raise RuntimeError("live_micro:cooldown_active")

        max_pos = int(lim.get("max_concurrent_positions") or 1)
        open_n = int(st.get("open_live_positions") or 0)
        if max_pos >= 0 and open_n >= max_pos:
            raise RuntimeError("live_micro:max_concurrent_positions_reached")


def touch_live_micro_session_trade_completed(runtime_root: Path, *, quote_usd: float = 0.0) -> None:
    """Call after a completed micro-live round-trip to enforce session caps and cooldown."""
    if not live_micro_runtime_enabled():
        return
    root = Path(runtime_root).resolve()
    st_path = root / ARTIFACT_SESSION_STATE
    st = _read_json(st_path)
    st["session_trades_completed"] = int(st.get("session_trades_completed") or 0) + 1
    st["last_trade_completed_ts"] = time.time()
    q = float(quote_usd or 0.0)
    cur_exp = float(st.get("session_notional_usd") or 0.0)
    st["session_notional_usd"] = max(0.0, cur_exp - q)
    st["open_live_positions"] = max(0, int(st.get("open_live_positions") or 0) - 1)
    st["truth_version"] = TRUTH_VERSION
    _atomic_write(st_path, st)


def touch_live_micro_session_open_increment(runtime_root: Path, *, quote_usd: float = 0.0) -> None:
    """Call after a successful live entry fills (micro session bookkeeping)."""
    if not live_micro_runtime_enabled():
        return
    root = Path(runtime_root).resolve()
    st_path = root / ARTIFACT_SESSION_STATE
    st = _read_json(st_path)
    st["open_live_positions"] = int(st.get("open_live_positions") or 0) + 1
    st["session_notional_usd"] = float(st.get("session_notional_usd") or 0.0) + float(quote_usd or 0.0)
    st["truth_version"] = TRUTH_VERSION
    _atomic_write(st_path, st)


def write_live_micro_force_halt(runtime_root: Path, *, operator: str = "", reason: str = "") -> Dict[str, Any]:
    """Operator pause: while this file exists, :func:`assert_live_micro_runtime_contract` fails (disable wins)."""
    root = Path(runtime_root).resolve()
    payload = {
        "truth_version": TRUTH_VERSION,
        "artifact": "live_micro_force_halt",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "runtime_root": str(root),
        "halt": True,
        "operator": (operator or "").strip(),
        "reason": (reason or "").strip(),
    }
    _atomic_write(root / ARTIFACT_FORCE_HALT, payload)
    return payload


def clear_live_micro_force_halt(runtime_root: Path) -> bool:
    root = Path(runtime_root).resolve()
    p = root / ARTIFACT_FORCE_HALT
    if p.is_file():
        p.unlink()
        return True
    return False


def record_live_start_receipt(runtime_root: Path, *, component: str, detail: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    root = Path(runtime_root).resolve()
    payload = {
        "truth_version": TRUTH_VERSION,
        "artifact": "live_start_receipt",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "runtime_root": str(root),
        "component": component,
        "detail": detail or {},
    }
    _atomic_write(root / ARTIFACT_START_RECEIPT, payload)
    return payload


def record_live_disable_receipt(runtime_root: Path, *, reason: str, operator: str = "") -> Dict[str, Any]:
    root = Path(runtime_root).resolve()
    payload = {
        "truth_version": TRUTH_VERSION,
        "artifact": "live_disable_receipt",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "runtime_root": str(root),
        "reason": (reason or "").strip(),
        "operator": (operator or "").strip(),
        "honesty": "Unset EZRA_LIVE_MICRO_ENABLED and return NTE_EXECUTION_MODE to paper in service env; restart units.",
    }
    _atomic_write(root / ARTIFACT_DISABLE_RECEIPT, payload)
    return payload
