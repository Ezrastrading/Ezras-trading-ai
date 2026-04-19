#!/usr/bin/env python3
"""
Emit live-readiness JSON reports (no secrets). Does not place orders.

Usage (from repo root):
  PYTHONPATH=src python3 scripts/generate_live_readiness_reports.py
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "docs" / "runtime_storage" / "live_readiness_last_run"
STRICT_GOV = {
    "GOVERNANCE_ORDER_ENFORCEMENT": "true",
    "GOVERNANCE_CAUTION_BLOCK_ENTRIES": "true",
    "GOVERNANCE_MISSING_JOINT_BLOCKS": "true",
    "GOVERNANCE_STALE_JOINT_BLOCKS": "true",
    "GOVERNANCE_UNKNOWN_MODE_BLOCKS": "true",
    "GOVERNANCE_DEGRADED_INTEGRITY_BLOCKS": "true",
    "GOVERNANCE_JOINT_STALE_HOURS": "48",
}


def _load_dotenv_safe() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    p = REPO / ".env"
    if p.is_file():
        load_dotenv(p)


def _scan_env_file_var_names(path: Path, keys: tuple[str, ...]) -> dict[str, bool]:
    found = {k: False for k in keys}
    if not path.is_file():
        return found
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return found
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        name, val = s.split("=", 1)
        name = name.strip()
        if name.startswith("export "):
            name = name[7:].strip()
        if name in found:
            found[name] = len(val.strip()) > 0
    return found


def _validate_url(url: str) -> tuple[bool, str]:
    if not url.strip():
        return False, "empty"
    u = urlparse(url.strip())
    if u.scheme not in ("http", "https"):
        return False, "bad_scheme"
    if not u.netloc:
        return False, "no_netloc"
    return True, "ok"


def _key_len_ok(k: str) -> bool:
    return 10 < len(k) < 12000


def main() -> int:
    os.chdir(REPO)
    sys.path.insert(0, str(REPO / "src"))
    _load_dotenv_safe()

    OUT.mkdir(parents=True, exist_ok=True)

    from trading_ai.global_layer.supabase_env_keys import resolve_supabase_jwt_key
    from trading_ai.nte.databank import supabase_trade_sync as sts

    # --- supabase_sync_fix_report.json
    fix_rep = {
        "schema": "supabase_sync_fix_report_v1",
        "change": "supabase_trade_sync._client uses resolve_supabase_jwt_key: SUPABASE_KEY first, else SUPABASE_SERVICE_ROLE_KEY",
        "also_updated": [
            "global_layer/supabase_runtime_reader.py",
            "shark/remote_state.py",
            "shark/supabase_logger.py",
        ],
        "logging": "supabase_client_initialized true/false with key_source and url_host (no secrets)",
    }
    (OUT / "supabase_sync_fix_report.json").write_text(json.dumps(fix_rep, indent=2), encoding="utf-8")

    # --- supabase_env_detection_report.json
    sup_vars = ("SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_SERVICE_ROLE_KEY")
    env_status = {v: bool((os.environ.get(v) or "").strip()) for v in sup_vars}
    dotenv_path = REPO / ".env"
    file_presence = _scan_env_file_var_names(dotenv_path, sup_vars)
    url = (os.environ.get("SUPABASE_URL") or "").strip()
    key, ksrc = resolve_supabase_jwt_key()
    url_ok, url_note = _validate_url(url)
    key_ok = bool(key) and _key_len_ok(key)
    hard_blockers = []
    if not env_status["SUPABASE_URL"] and not file_presence.get("SUPABASE_URL"):
        hard_blockers.append("SUPABASE_URL missing in env and .env")
    if not key and not (file_presence.get("SUPABASE_KEY") or file_presence.get("SUPABASE_SERVICE_ROLE_KEY")):
        hard_blockers.append("Neither SUPABASE_KEY nor SUPABASE_SERVICE_ROLE_KEY in env/.env with non-empty value")
    det = {
        "schema": "supabase_env_detection_report_v1",
        "sources_scanned": {
            "process_environment": True,
            "dotenv_file": str(dotenv_path),
            "dotenv_file_exists": dotenv_path.is_file(),
        },
        "found_in_environment": {k: env_status[k] for k in sup_vars},
        "found_nonempty_in_dotenv_file": file_presence,
        "resolved_jwt_source": ksrc,
        "url_validation": {"ok": url_ok, "detail": url_note},
        "jwt_length_reasonable": key_ok if key else False,
        "hard_blockers": hard_blockers,
    }
    (OUT / "supabase_env_detection_report.json").write_text(json.dumps(det, indent=2, default=str), encoding="utf-8")

    # --- supabase_sync_test_report.json
    probe_id = f"lr_probe_{uuid.uuid4().hex[:12]}"
    row = {
        "trade_id": probe_id,
        "avenue_id": "A",
        "avenue_name": "coinbase",
        "asset": "BTC-USD",
        "strategy_id": "live_readiness_probe",
        "route_chosen": "n/a",
        "regime": "probe",
        "timestamp_open": datetime.now(timezone.utc).isoformat(),
        "timestamp_close": datetime.now(timezone.utc).isoformat(),
        "schema_version": "1.0.0",
    }
    sync_exc: str | None = None
    try:
        res = sts.upsert_trade_event(row)
        ok = bool(res.get("success"))
    except Exception as e:
        ok = False
        sync_exc = f"{type(e).__name__}:{e}"
    desc = sts.describe_supabase_sync_client()
    sync_test = {
        "schema": "supabase_sync_test_report_v1",
        "probe_trade_id": probe_id,
        "upsert_trade_event_returned": ok,
        "describe_supabase_sync_client": desc,
        "exception_during_upsert": sync_exc,
        "note": "False return is expected when Supabase unreachable or table/RLS rejects row; check logs.",
    }
    (OUT / "supabase_sync_test_report.json").write_text(json.dumps(sync_test, indent=2, default=str), encoding="utf-8")

    # --- governance_env_verification.json
    before = {k: (os.environ.get(k) or "").strip() for k in STRICT_GOV}
    applied: list[str] = []
    for k, want in STRICT_GOV.items():
        if before[k] != want:
            os.environ[k] = want
            applied.append(k)
    gov = {
        k: {
            "expected": STRICT_GOV[k],
            "before": before[k],
            "after": (os.environ.get(k) or "").strip(),
            "matches": (os.environ.get(k) or "").strip() == STRICT_GOV[k],
        }
        for k in STRICT_GOV
    }
    gov_rep = {
        "schema": "governance_env_verification_v1",
        "strict_profile_vars_applied_this_run": applied,
        "variables": gov,
        "reference": "docs/runtime_storage/governance_live_profile.json",
    }
    (OUT / "governance_env_verification.json").write_text(json.dumps(gov_rep, indent=2), encoding="utf-8")

    # --- coinbase_credential_status.json
    cb_names = (
        "COINBASE_API_KEY_NAME",
        "COINBASE_API_KEY",
        "COINBASE_API_PRIVATE_KEY",
        "COINBASE_API_SECRET",
    )
    cb_env = {n: bool((os.environ.get(n) or "").strip()) for n in cb_names}
    cb_file = _scan_env_file_var_names(dotenv_path, cb_names)
    ec_marker = False
    if dotenv_path.is_file():
        for line in dotenv_path.read_text(encoding="utf-8", errors="replace").splitlines():
            lu = line.upper()
            if "COINBASE_API_PRIVATE_KEY" in lu or "COINBASE_API_SECRET" in lu:
                if "=" in line:
                    _, v = line.split("=", 1)
                    norm = v.strip().strip('"').strip("'").replace("\\n", "\n")
                    if "BEGIN EC PRIVATE KEY" in norm:
                        ec_marker = True
    coin_rep = {
        "schema": "coinbase_credential_status_v1",
        "environment": {
            "key_id_or_legacy_set": cb_env["COINBASE_API_KEY_NAME"] or cb_env["COINBASE_API_KEY"],
            "private_key_or_secret_set": cb_env["COINBASE_API_PRIVATE_KEY"] or cb_env["COINBASE_API_SECRET"],
        },
        "dotenv_nonempty": {k: cb_file.get(k, False) for k in cb_names},
        "dotenv_pem_contains_begin_ec_private_key": ec_marker,
        "ready_for_preflight_pem_check": (
            (cb_env["COINBASE_API_KEY_NAME"] or cb_env["COINBASE_API_KEY"])
            and (cb_env["COINBASE_API_PRIVATE_KEY"] or cb_env["COINBASE_API_SECRET"])
        ),
    }
    (OUT / "coinbase_credential_status.json").write_text(json.dumps(coin_rep, indent=2), encoding="utf-8")

    # --- env_handling_verification.json
    env_hand = {
        "schema": "env_handling_verification_v1",
        "python_dotenv_available": True,
        "load_dotenv_used_in_script": True,
        "shell_source_dotenv_safe": False,
        "reason_not_shell_safe": "Multiline unquoted PEM in .env breaks POSIX source; use python-dotenv or file-based export",
        "coinbase_pem_normalization": "live_first_20_operator replaces \\\\n before PEM parse",
        "reference": "docs/runtime_storage/env_handling_report.json",
    }
    try:
        import dotenv  # noqa: F401
    except ImportError:
        env_hand["python_dotenv_available"] = False
    (OUT / "env_handling_verification.json").write_text(json.dumps(env_hand, indent=2), encoding="utf-8")

    print(json.dumps({"ok": True, "output_dir": str(OUT)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
