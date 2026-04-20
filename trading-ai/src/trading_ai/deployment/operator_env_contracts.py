"""
Canonical operator-facing env names and export strings for Avenue A supervised live validation.

No secrets: lists variable names and example export lines only.
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Supervised Gate A live round-trip (same as runtime_proof.live_execution_validation)
LIVE_SINGLE_EXECUTION_VALIDATION_CONFIRM = "LIVE_SINGLE_EXECUTION_VALIDATION_CONFIRM"
LIVE_SINGLE_EXECUTION_VALIDATION_CONFIRM_VALUE = "YES_I_UNDERSTAND_REAL_CAPITAL"

# Coinbase Advanced Trade (same as shark.outlets.coinbase.CoinbaseClient)
COINBASE_KEY_ID_ENVS = ("COINBASE_API_KEY_NAME", "COINBASE_API_KEY")
COINBASE_PRIVATE_KEY_ENVS = ("COINBASE_API_PRIVATE_KEY", "COINBASE_API_SECRET")
# Optional (portfolio scoping); not required for JWT auth itself.
COINBASE_PORTFOLIO_ID_ENV = "COINBASE_PORTFOLIO_ID"

FAILURE_CODE_COINBASE_CREDENTIALS_NOT_CONFIGURED = "coinbase_credentials_not_configured"

# Venue enablement (live_execution_validation)
COINBASE_VENUE_ENABLE_ENVS = ("COINBASE_EXECUTION_ENABLED", "COINBASE_ENABLED")

# Daemon mode
EZRAS_AVENUE_A_DAEMON_MODE = "EZRAS_AVENUE_A_DAEMON_MODE"
SUPERVISED_LIVE = "supervised_live"


def _has_any_env(names: Tuple[str, ...]) -> bool:
    return any((os.environ.get(n) or "").strip() for n in names)


def missing_coinbase_credential_env_vars() -> List[str]:
    """Which credential env slots are unset (lists both names in a group when that group is unset)."""
    missing: List[str] = []
    if not _has_any_env(COINBASE_KEY_ID_ENVS):
        missing.extend(list(COINBASE_KEY_ID_ENVS))
    if not _has_any_env(COINBASE_PRIVATE_KEY_ENVS):
        missing.extend(list(COINBASE_PRIVATE_KEY_ENVS))
    return missing


def _coinbase_export_next_step_shell(*, missing: List[str]) -> str:
    """
    One copy-pastable shell line (placeholders only — never real secrets).
    Tailors to which of the two groups is still missing.
    """
    need_key = any(n in missing for n in COINBASE_KEY_ID_ENVS)
    need_priv = any(n in missing for n in COINBASE_PRIVATE_KEY_ENVS)
    parts: List[str] = []
    if need_key:
        parts.append("export COINBASE_API_KEY_NAME='<your_coinbase_api_key_name>'")
    if need_priv:
        parts.append("export COINBASE_API_PRIVATE_KEY='<your_ed25519_private_key_pem_or_secret>'")
    if not parts:
        return (
            "export COINBASE_API_KEY_NAME='<your_coinbase_api_key_name>' && "
            "export COINBASE_API_PRIVATE_KEY='<your_ed25519_private_key_pem_or_secret>' "
            "(alternates: COINBASE_API_KEY / COINBASE_API_SECRET)"
        )
    alt = " (alternates: COINBASE_API_KEY for key id; COINBASE_API_SECRET for private key)"
    return " && ".join(parts) + alt


def coinbase_credentials_failure_payload() -> Dict[str, Any]:
    """
    Structured operator payload when Coinbase JWT credentials are missing or unusable.
    Same contract as live validation / daemon JSON surfaces.
    """
    miss = missing_coinbase_credential_env_vars()
    return {
        "failure_code": FAILURE_CODE_COINBASE_CREDENTIALS_NOT_CONFIGURED,
        "exact_missing_coinbase_env_vars": miss,
        "next_step": _coinbase_export_next_step_shell(missing=miss),
        "required_key_id_env_options": list(COINBASE_KEY_ID_ENVS),
        "required_private_key_env_options": list(COINBASE_PRIVATE_KEY_ENVS),
        "optional_env": [COINBASE_PORTFOLIO_ID_ENV],
    }


def coinbase_credentials_operator_hint() -> Dict[str, Any]:
    """Backward-compatible superset: includes failure_payload fields + legacy key."""
    payload = coinbase_credentials_failure_payload()
    miss = payload["exact_missing_coinbase_env_vars"]
    return {
        **payload,
        "missing_env_vars": miss,
    }


def supervised_gate_a_live_validation_confirm_contract() -> Dict[str, Any]:
    return {
        "scope": "supervised_gate_a_live_validation_round_trip_only",
        "not_autonomous_live_enablement": True,
        "required_env": LIVE_SINGLE_EXECUTION_VALIDATION_CONFIRM,
        "required_value_exact": LIVE_SINGLE_EXECUTION_VALIDATION_CONFIRM_VALUE,
        "export_command": (
            f'export {LIVE_SINGLE_EXECUTION_VALIDATION_CONFIRM}='
            f'{shlex.quote(LIVE_SINGLE_EXECUTION_VALIDATION_CONFIRM_VALUE)}'
        ),
        "alternate_ack_file_when_daemon_active": "data/control/avenue_a_autonomous_live_ack.json",
        "alternate_ack_note": (
            "When EZRAS_AVENUE_A_DAEMON_ACTIVE=1, a file ack with confirmed true may satisfy the "
            "same contract — see live_execution_validation._gate_a_operator_confirms_live_round_trip."
        ),
    }


def exact_supervised_env_exports_required(*, runtime_root: Path) -> List[str]:
    """Concrete export lines (no secrets) for supervised daemon + Gate A validation shell."""
    rt = str(Path(runtime_root).resolve())
    out = [
        "export PYTHONPATH=src",
        f'export EZRAS_RUNTIME_ROOT={shlex.quote(rt)}',
        f"export {EZRAS_AVENUE_A_DAEMON_MODE}={SUPERVISED_LIVE}",
        supervised_gate_a_live_validation_confirm_contract()["export_command"],
    ]
    return out


def exact_next_command_after_env_fix(*, runtime_root: Path, product_id: str = "BTC-USD", quote_usd: float = 10.0) -> str:
    rt = str(Path(runtime_root).resolve())
    return (
        f'export PYTHONPATH=src && export EZRAS_RUNTIME_ROOT={shlex.quote(rt)} '
        f"&& export {EZRAS_AVENUE_A_DAEMON_MODE}={SUPERVISED_LIVE} "
        f"&& export {LIVE_SINGLE_EXECUTION_VALIDATION_CONFIRM}={shlex.quote(LIVE_SINGLE_EXECUTION_VALIDATION_CONFIRM_VALUE)} "
        f"&& python3 -m trading_ai.deployment avenue-a-daemon-once --quote-usd {quote_usd} --product-id {product_id}"
    )


def build_env_config_blocker_summary(
    *,
    runtime_root: Optional[Path] = None,
    require_supervised_confirm: bool = True,
    assume_supervised_daemon_shell: bool = False,
) -> Dict[str, Any]:
    """
    Structured env/config blockers for status / verdict (never claims venue keys are valid, only presence).
    """
    miss_cb = missing_coinbase_credential_env_vars()
    confirm_ok = (
        (os.environ.get(LIVE_SINGLE_EXECUTION_VALIDATION_CONFIRM) or "").strip()
        == LIVE_SINGLE_EXECUTION_VALIDATION_CONFIRM_VALUE
    )
    venue_ok = (os.environ.get("COINBASE_EXECUTION_ENABLED") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    ) or (os.environ.get("COINBASE_ENABLED") or "").strip().lower() in ("1", "true", "yes")

    daemon_mode = (os.environ.get(EZRAS_AVENUE_A_DAEMON_MODE) or "").strip().lower()
    want_confirm = bool(
        require_supervised_confirm
        and (assume_supervised_daemon_shell or daemon_mode in ("supervised_live", "autonomous_live"))
    )

    blockers: List[str] = []
    if want_confirm and not confirm_ok:
        blockers.append(
            f"{LIVE_SINGLE_EXECUTION_VALIDATION_CONFIRM}_must_equal_{LIVE_SINGLE_EXECUTION_VALIDATION_CONFIRM_VALUE}"
        )
    if miss_cb:
        blockers.append("coinbase_api_credentials_missing_or_unset")
    if not venue_ok:
        blockers.append("coinbase_live_execution_not_enabled_set_COINBASE_EXECUTION_ENABLED_or_COINBASE_ENABLED")

    out: Dict[str, Any] = {
        "supervised_live_validation_confirm_ok": bool(confirm_ok),
        "supervised_confirm_evaluated_for_daemon_modes": want_confirm,
        "coinbase_credentials_present_per_env_names": len(miss_cb) == 0,
        "exact_missing_coinbase_env_vars": miss_cb,
        "coinbase_venue_enablement_ok": bool(venue_ok),
        "exact_supervised_env_exports_required": exact_supervised_env_exports_required(
            runtime_root=runtime_root or Path(os.environ.get("EZRAS_RUNTIME_ROOT") or ".")
        ),
        "env_config_blockers": blockers,
    }
    if runtime_root is not None:
        out["exact_next_command_after_env_fix"] = exact_next_command_after_env_fix(runtime_root=runtime_root)
    return out
