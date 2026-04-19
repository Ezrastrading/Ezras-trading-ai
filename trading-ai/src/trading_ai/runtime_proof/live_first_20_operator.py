"""
Controlled live first-20 — operator preflight, manifest, governance log attachment.

Does **not** auto-submit 20 live orders. Use with explicit env + operator supervision.

**Exit 0** requires every *critical* check in :func:`run_live_preflight` to pass — no bypass flags.
See :data:`LIVE_FIRST_20_PREFLIGHT_ENV_REFERENCE` for the operator contract.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_ai.runtime_proof.first_twenty_session import RollbackThresholds

LOG = logging.getLogger(__name__)

# Operator runbook — must match validators in this module (no hidden bypasses).
LIVE_FIRST_20_PREFLIGHT_ENV_REFERENCE = """
=== Coinbase Avenue A — live first-20 preflight (required for exit 0) ===

A) Simulated shadow judge (check 1)
   LIVE_FIRST_20_SIMULATED_JUDGE_JSON=/abs/path/to/first_20_judge_report.json
   File must contain real_capital_go_no_go == GO_FOR_CONTROLLED_FIRST_20_LIVE_CONSIDERATION

B) Coinbase Advanced Trade credentials (check 3)
   COINBASE_API_KEY_NAME=organizations/{org}/apiKeys/{id}   (or legacy COINBASE_API_KEY)
   COINBASE_API_PRIVATE_KEY=<EC P-256 PEM>                  (or COINBASE_API_SECRET)
   PEM may use literal \\n in .env; must load with cryptography as ES256 signing key.
   After PEM loads, REST read-only ping (list accounts) must succeed.

C) Live mode — ALL required simultaneously (check 4)
   COINBASE_ENABLED=true
   NTE_EXECUTION_MODE=live
   NTE_PAPER_MODE must be unset OR false (not 1/true/yes)
   LIVE_FIRST_20_ENABLED=true
   FIRST_TWENTY_ALLOW_LIVE=true

D) Optional notional floor (check 5)
   LIVE_FIRST_20_QUOTE_NOTIONAL_USD=<float>  (if set, must be >= product min, default min 10 USD)

E) Supabase stance — ONE path required (check 9)
   Remote: SUPABASE_URL=https://....supabase.co  AND  SUPABASE_KEY=<secret>
           (SUPABASE_SERVICE_ROLE_KEY is also accepted as the key)
   Local-first for this run only: LIVE_FIRST_20_LOCAL_FIRST_OK=true

F) Runtime / archive (checks 2, 8, 10)
   EZRAS_RUNTIME_ROOT passed as --runtime-root must be writable; archive dir created under
   <runtime_root>/live_first_20_sessions/<session_id>/
   Governance JSON lines: <runtime_root>/governance_gate_decisions.log (sink must be writable)

G) Governance policy (check 7 — informational PASS; values are your choice)
   GOVERNANCE_ORDER_ENFORCEMENT, GOVERNANCE_CAUTION_BLOCK_ENTRIES, GOVERNANCE_*_BLOCKS, etc.
""".strip()


def _truthy(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes")


def _find_default_simulated_judge(trading_ai_repo_root: Path) -> Optional[Path]:
    """Latest first_20_judge_report.json under runtime_proof_runs if present."""
    base = trading_ai_repo_root / "runtime_proof_runs"
    if not base.is_dir():
        return None
    candidates = sorted(base.rglob("first_20_judge_report.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def _validate_judge_go(path: Path) -> Tuple[bool, str]:
    if not path.is_file():
        return False, f"missing_file:{path}"
    try:
        j = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return False, f"invalid_json:{e}"
    go = str(j.get("real_capital_go_no_go") or "").strip()
    if go != "GO_FOR_CONTROLLED_FIRST_20_LIVE_CONSIDERATION":
        return False, f"expected_GO_got:{go!r}"
    return True, "ok"


def _validate_runtime_writable(root: Path) -> Tuple[bool, str]:
    try:
        root.mkdir(parents=True, exist_ok=True)
        p = root / ".live_first_20_write_probe"
        p.write_text("ok", encoding="utf-8")
        p.unlink(missing_ok=True)
        return True, str(root.resolve())
    except OSError as e:
        return False, str(e)


def _coinbase_key_material_status() -> Dict[str, Any]:
    key = (
        (os.environ.get("COINBASE_API_KEY_NAME") or os.environ.get("COINBASE_API_KEY") or "").strip()
    )
    secret = (
        os.environ.get("COINBASE_API_PRIVATE_KEY") or os.environ.get("COINBASE_API_SECRET") or ""
    ).strip()
    return {
        "api_key_id_present": bool(key),
        "private_key_pem_present": bool(secret),
        "source_key_name": "COINBASE_API_KEY_NAME" if os.environ.get("COINBASE_API_KEY_NAME") else (
            "COINBASE_API_KEY" if os.environ.get("COINBASE_API_KEY") else None
        ),
        "source_private": "COINBASE_API_PRIVATE_KEY" if os.environ.get("COINBASE_API_PRIVATE_KEY") else (
            "COINBASE_API_SECRET" if os.environ.get("COINBASE_API_SECRET") else None
        ),
    }


def _coinbase_pem_ok() -> Tuple[bool, str]:
    key = (
        (os.environ.get("COINBASE_API_KEY_NAME") or os.environ.get("COINBASE_API_KEY") or "").strip()
    )
    secret = (
        os.environ.get("COINBASE_API_PRIVATE_KEY") or os.environ.get("COINBASE_API_SECRET") or ""
    ).strip()
    if not key:
        return False, "missing_api_key_id:set COINBASE_API_KEY_NAME (CDP) or COINBASE_API_KEY"
    if not secret:
        return (
            False,
            "missing_private_key:set COINBASE_API_PRIVATE_KEY (EC PEM) or COINBASE_API_SECRET",
        )
    try:
        from cryptography.hazmat.primitives.serialization import load_pem_private_key

        pem = secret.replace("\\n", "\n").strip()
        load_pem_private_key(pem.encode("utf-8"), password=None)
        return True, "pem_load_ok"
    except Exception as e:
        return False, f"pem_parse_failed:{e!s} (expect EC P-256 PEM, BEGIN EC PRIVATE KEY)"


def _coinbase_rest_ping() -> Tuple[bool, str]:
    """Read-only: list accounts (validates JWT + network)."""
    try:
        from trading_ai.shark.outlets.coinbase import CoinbaseClient

        c = CoinbaseClient()
        acct = c.get_accounts()
        n = len(acct) if isinstance(acct, list) else 0
        return True, f"accounts_ok_count={n}"
    except Exception as e:
        return False, f"rest_error:{e!s}"


def _smallest_notional_usd() -> float:
    from trading_ai.nte.execution.product_rules import _DEFAULTS

    # Conservative: min across configured defaults
    mins = [float(m["min_notional_usd"]) for m in _DEFAULTS.values()]
    return min(mins) if mins else 10.0


def _governance_flags_snapshot() -> Dict[str, Any]:
    keys = [
        "GOVERNANCE_ORDER_ENFORCEMENT",
        "GOVERNANCE_CAUTION_BLOCK_ENTRIES",
        "GOVERNANCE_MISSING_JOINT_BLOCKS",
        "GOVERNANCE_STALE_JOINT_BLOCKS",
        "GOVERNANCE_UNKNOWN_MODE_BLOCKS",
        "GOVERNANCE_DEGRADED_INTEGRITY_BLOCKS",
        "GOVERNANCE_JOINT_STALE_HOURS",
    ]
    return {k: os.environ.get(k) for k in keys}


def attach_governance_decision_log(runtime_root: Path) -> Path:
    """
    Ensure ``governance_order_gate`` INFO lines land in ``governance_gate_decisions.log``.
    Safe to call once per process.
    """
    log_path = runtime_root / "governance_gate_decisions.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    gh = logging.getLogger("trading_ai.global_layer.governance_order_gate")
    gh.setLevel(logging.INFO)
    for h in gh.handlers:
        if isinstance(h, logging.FileHandler) and getattr(h, "baseFilename", None) == str(log_path):
            return log_path
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(logging.Formatter("%(message)s"))
    gh.addHandler(fh)
    return log_path


def run_live_preflight(
    *,
    runtime_root: Path,
    trading_ai_repo_root: Path,
    simulated_judge_path: Optional[Path] = None,
) -> Tuple[bool, Dict[str, Any], List[Dict[str, Any]]]:
    """
    Phase 0 checklist. Returns (all_pass, manifest_partial, checks).

    Each check: {"id": int, "name": str, "pass": bool, "detail": str}
    """
    checks: List[Dict[str, Any]] = []
    root = runtime_root.resolve()
    judge_path = simulated_judge_path
    if judge_path is None:
        env_p = (os.environ.get("LIVE_FIRST_20_SIMULATED_JUDGE_JSON") or "").strip()
        judge_path = Path(env_p).resolve() if env_p else _find_default_simulated_judge(trading_ai_repo_root)

    # 1 Judge GO
    if judge_path and judge_path.is_file():
        ok, det = _validate_judge_go(judge_path)
    else:
        ok, det = False, f"simulated_judge_not_found:{judge_path}"
    c1: Dict[str, Any] = {"id": 1, "name": "simulated_judge_GO", "pass": ok, "detail": det}
    if not ok:
        c1["remediation"] = (
            "Set LIVE_FIRST_20_SIMULATED_JUDGE_JSON to an existing first_20_judge_report.json, "
            "or pass --simulated-judge. JSON must have "
            "real_capital_go_no_go == GO_FOR_CONTROLLED_FIRST_20_LIVE_CONSIDERATION."
        )
    checks.append(c1)

    # 2 Runtime root
    ok, det = _validate_runtime_writable(root)
    c2: Dict[str, Any] = {"id": 2, "name": "runtime_root_writable", "pass": ok, "detail": det}
    if not ok:
        c2["remediation"] = "Choose a --runtime-root directory the operator user can create/write."
    checks.append(c2)

    # 3 Credentials format + REST
    pem_ok, pem_det = _coinbase_pem_ok()
    rest_ok, rest_det = (False, "skipped_until_pem_ok")
    if pem_ok:
        rest_ok, rest_det = _coinbase_rest_ping()
    ok = pem_ok and rest_ok
    detail3 = f"pem:{pem_det} | rest:{rest_det}"
    c3: Dict[str, Any] = {"id": 3, "name": "coinbase_credentials_validated", "pass": ok, "detail": detail3}
    if not ok:
        c3["key_material"] = _coinbase_key_material_status()
        parts = [
            "CDP Advanced Trade: COINBASE_API_KEY_NAME=organizations/{org}/apiKeys/{key_id} "
            "(or COINBASE_API_KEY for legacy naming).",
            "Private key: COINBASE_API_PRIVATE_KEY=<EC P-256 PEM> with -----BEGIN EC PRIVATE KEY----- "
            "(or COINBASE_API_SECRET). Use \\n escapes in .env for multiline PEM.",
        ]
        if pem_ok and not rest_ok:
            parts.append(
                "PEM parsed but REST list_accounts failed — check network, key permissions, and CDP key active."
            )
        c3["remediation"] = " ".join(parts)
    checks.append(c3)

    # 4 Live mode flags explicit
    cb = _truthy("COINBASE_ENABLED")
    paper = _truthy("NTE_PAPER_MODE")
    nte_mode = (os.environ.get("NTE_EXECUTION_MODE") or "").strip().lower()
    live_intent = _truthy("LIVE_FIRST_20_ENABLED")
    ft_allow = (os.environ.get("FIRST_TWENTY_ALLOW_LIVE") or "").strip().lower() in ("1", "true", "yes")
    explicit = cb and not paper and nte_mode == "live" and live_intent and ft_allow
    miss: List[str] = []
    if not cb:
        miss.append("COINBASE_ENABLED must be true (1/true/yes)")
    if paper:
        miss.append("NTE_PAPER_MODE must be unset or false (paper blocks live)")
    if nte_mode != "live":
        miss.append(f"NTE_EXECUTION_MODE must be exactly 'live' (got {nte_mode!r})")
    if not live_intent:
        miss.append("LIVE_FIRST_20_ENABLED must be true")
    if not ft_allow:
        miss.append("FIRST_TWENTY_ALLOW_LIVE must be true (explicit operator acknowledgment)")
    detail4 = (
        f"COINBASE_ENABLED={cb} NTE_PAPER_MODE={paper} NTE_EXECUTION_MODE={nte_mode!r} "
        f"LIVE_FIRST_20_ENABLED={live_intent} FIRST_TWENTY_ALLOW_LIVE={os.environ.get('FIRST_TWENTY_ALLOW_LIVE')!r}"
    )
    if miss:
        detail4 += " | failures: " + "; ".join(miss)
    c4: Dict[str, Any] = {"id": 4, "name": "live_mode_flags_intentional", "pass": explicit, "detail": detail4}
    if not explicit:
        c4["remediation"] = (
            "Export all of: COINBASE_ENABLED=true NTE_EXECUTION_MODE=live NTE_PAPER_MODE=false "
            "LIVE_FIRST_20_ENABLED=true FIRST_TWENTY_ALLOW_LIVE=true (see LIVE_FIRST_20_PREFLIGHT_ENV_REFERENCE)."
        )
    checks.append(c4)

    # 5 Smallest notional configured (env cap must be >= product min)
    min_n = _smallest_notional_usd()
    cap = os.environ.get("LIVE_FIRST_20_QUOTE_NOTIONAL_USD")
    cap_ok = True
    cap_detail = f"defaults_min_notional_usd={min_n}"
    if cap:
        try:
            cf = float(cap)
            cap_ok = cf >= min_n
            cap_detail += f" LIVE_FIRST_20_QUOTE_NOTIONAL_USD={cf} (must_be>={min_n})"
        except ValueError:
            cap_ok = False
            cap_detail += " LIVE_FIRST_20_QUOTE_NOTIONAL_USD_invalid"
    c5: Dict[str, Any] = {"id": 5, "name": "smallest_notional_configured", "pass": cap_ok, "detail": cap_detail}
    if not cap_ok:
        c5["remediation"] = (
            f"Unset LIVE_FIRST_20_QUOTE_NOTIONAL_USD to use engine minimums, or set a float >= {min_n} (USD)."
        )
    checks.append(c5)

    # 6 Rollback thresholds
    rb = RollbackThresholds()
    checks.append(
        {
            "id": 6,
            "name": "rollback_thresholds_loaded",
            "pass": True,
            "detail": json.dumps(asdict(rb), default=str),
        }
    )

    # 7 Governance flags explicit (always pass if we can read env; detail carries values)
    gov = _governance_flags_snapshot()
    gov_ok = True  # informational; operator reviews detail
    checks.append({"id": 7, "name": "governance_flags_documented", "pass": gov_ok, "detail": json.dumps(gov)})

    # 8 Governance log sink
    try:
        lp = attach_governance_decision_log(root)
        sink_ok = lp.parent.is_dir() and os.access(lp.parent, os.W_OK)
        checks.append({"id": 8, "name": "governance_log_sink_writable", "pass": sink_ok, "detail": str(lp)})
    except Exception as e:
        checks.append({"id": 8, "name": "governance_log_sink_writable", "pass": False, "detail": str(e)})

    # 9 Supabase stance
    sup_url = (os.environ.get("SUPABASE_URL") or "").strip()
    sup_key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_KEY") or "").strip()
    local_ok = _truthy("LIVE_FIRST_20_LOCAL_FIRST_OK")
    sup_ok = (bool(sup_url) and bool(sup_key)) or local_ok
    sup_detail = (
        f"SUPABASE_URL_set={bool(sup_url)} SUPABASE_KEY_set={bool(sup_key)} "
        f"LIVE_FIRST_20_LOCAL_FIRST_OK={local_ok}"
    )
    if sup_url and sup_key:
        sup_detail += " | path=remote_sync"
    elif local_ok:
        sup_detail += " | path=local_first_explicit"
    else:
        sup_detail += " | path=unset"
    c9: Dict[str, Any] = {"id": 9, "name": "supabase_stance_explicit", "pass": sup_ok, "detail": sup_detail}
    if not sup_ok:
        c9["remediation"] = (
            "Either set SUPABASE_URL and (SUPABASE_KEY or SUPABASE_SERVICE_ROLE_KEY) for remote sync "
            "(same vars used by nte/databank/supabase_trade_sync.py for SUPABASE_KEY), "
            "OR set LIVE_FIRST_20_LOCAL_FIRST_OK=true to acknowledge local-first truth for this run only."
        )
    checks.append(c9)

    # 10 Archive path
    session_id = f"live20_{uuid.uuid4().hex[:12]}"
    arch = root / "live_first_20_sessions" / session_id
    try:
        arch.mkdir(parents=True, exist_ok=True)
        arch_ok = True
        arch_det = str(arch)
    except OSError as e:
        arch_ok = False
        arch_det = str(e)
    checks.append({"id": 10, "name": "archive_path_ready", "pass": arch_ok, "detail": arch_det})

    critical_ids = {1, 2, 3, 4, 5, 8, 9, 10}
    all_pass = all(c["pass"] for c in checks if c["id"] in critical_ids)

    manifest: Dict[str, Any] = {
        "session_id": session_id,
        "start_time": time.time(),
        "start_time_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "runtime_root": str(root),
        "live_mode": True,
        "simulated_judge_report": str(judge_path) if judge_path else None,
        "smallest_notional_usd_default": min_n,
        "governance": _governance_flags_snapshot(),
        "rollback_thresholds": asdict(rb),
        "nte": {
            "NTE_EXECUTION_MODE": os.environ.get("NTE_EXECUTION_MODE"),
            "NTE_PAPER_MODE": os.environ.get("NTE_PAPER_MODE"),
            "COINBASE_ENABLED": os.environ.get("COINBASE_ENABLED"),
            "LIVE_FIRST_20_ENABLED": os.environ.get("LIVE_FIRST_20_ENABLED"),
            "FIRST_TWENTY_ALLOW_LIVE": os.environ.get("FIRST_TWENTY_ALLOW_LIVE"),
        },
        "supabase": {"remote_configured": bool(sup_url and sup_key), "local_first_ok": local_ok},
        "artifact_archive": str(arch) if arch_ok else None,
        "preflight_all_pass": all_pass,
        "preflight_checks": checks,
    }
    return all_pass, manifest, checks


def write_live_manifest(archive_dir: Path, manifest: Dict[str, Any]) -> Path:
    archive_dir.mkdir(parents=True, exist_ok=True)
    p = archive_dir / "live_first_20_session_manifest.json"
    p.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    return p


def write_live_session_report(
    archive_dir: Path,
    payload: Dict[str, Any],
    *,
    name: str = "live_first_20_session_report.json",
) -> Path:
    archive_dir.mkdir(parents=True, exist_ok=True)
    p = archive_dir / name
    p.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return p


def write_live_judge_report(archive_dir: Path, *, out_name: str = "live_first_20_judge_report.json") -> Path:
    """Run the same artifact judge as shadow first-20; writes ``live_first_20_judge_report.json``."""
    from trading_ai.runtime_proof.first_twenty_judge import judge_first_twenty_session

    archive_dir = archive_dir.resolve()
    j = judge_first_twenty_session(archive_dir)
    p = archive_dir / out_name
    p.write_text(json.dumps(j, indent=2, default=str), encoding="utf-8")
    return p


def write_live_session_md(archive_dir: Path, payload: Dict[str, Any]) -> Path:
    lines = [
        "# Live first-20 session report",
        "",
        f"**status:** {payload.get('status')}",
        f"**session_id:** `{payload.get('session_id')}`",
        f"**completed_trades:** {payload.get('completed_trades', 0)}",
        f"**rollback:** {payload.get('rollback_reason')}",
        f"**result:** {payload.get('final_result')}",
        "",
        "```json",
        json.dumps(payload.get("detail") or payload, indent=2, default=str)[:12000],
        "```",
    ]
    p = archive_dir / "live_first_20_session_report.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p
