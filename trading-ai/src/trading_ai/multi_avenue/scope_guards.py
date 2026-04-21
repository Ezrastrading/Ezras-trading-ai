"""Hard scope validation for writes — fail loudly on mismatch; log to ``scope_violation_log.json``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Optional

from trading_ai.multi_avenue.contamination_guard import ScopeContaminationError, assert_matching_scope
from trading_ai.multi_avenue.control_logs import append_control_events
from trading_ai.runtime_paths import ezras_runtime_root


class ScopeViolationError(ValueError):
    """Raised when a write target does not match declared scope."""


def _log_violation(kind: str, detail: str, *, runtime_root: Optional[Path] = None) -> None:
    append_control_events(
        "scope_violation_log.json",
        {"kind": kind, "detail": detail},
        runtime_root=runtime_root,
    )


def validate_artifact_scope(
    payload: Mapping[str, Any],
    *,
    expected_scope_level: str,
    expected_avenue_id: Optional[str] = None,
    expected_gate_id: Optional[str] = None,
    runtime_root: Optional[Path] = None,
) -> None:
    sl = str(payload.get("scope_level") or payload.get("artifact_scope") or "")
    if sl and sl != expected_scope_level:
        _log_violation("artifact_scope", f"scope_level={sl!r} expected={expected_scope_level!r}", runtime_root=runtime_root)
        raise ScopeViolationError(f"artifact scope_level mismatch: {sl!r} vs {expected_scope_level!r}")
    assert_matching_scope(payload, expected_avenue_id=expected_avenue_id, expected_gate_id=expected_gate_id)


def validate_session_scope(
    payload: Mapping[str, Any],
    *,
    expected_session_scope: str,
    expected_avenue_id: Optional[str] = None,
    expected_gate_id: Optional[str] = None,
    runtime_root: Optional[Path] = None,
) -> None:
    ss = str(payload.get("session_scope") or "")
    if ss and ss != expected_session_scope:
        _log_violation(
            "session_scope",
            f"session_scope={ss!r} expected={expected_session_scope!r}",
            runtime_root=runtime_root,
        )
        raise ScopeViolationError(f"session_scope mismatch: {ss!r} vs {expected_session_scope!r}")
    assert_matching_scope(payload, expected_avenue_id=expected_avenue_id, expected_gate_id=expected_gate_id)


def validate_trade_scope(
    trade: Mapping[str, Any],
    *,
    expected_avenue_id: Optional[str] = None,
    expected_gate_id: Optional[str] = None,
    runtime_root: Optional[Path] = None,
) -> None:
    try:
        assert_matching_scope(trade, expected_avenue_id=expected_avenue_id, expected_gate_id=expected_gate_id)
    except ScopeContaminationError as e:
        _log_violation("trade_scope", str(e), runtime_root=runtime_root)
        raise ScopeViolationError(str(e)) from e


def validate_edge_scope(
    payload: Mapping[str, Any],
    *,
    expected_avenue_id: str,
    expected_gate_id: str,
    runtime_root: Optional[Path] = None,
) -> None:
    try:
        assert_matching_scope(payload, expected_avenue_id=expected_avenue_id, expected_gate_id=expected_gate_id)
    except ScopeContaminationError as e:
        _log_violation("edge_scope", str(e), runtime_root=runtime_root)
        raise ScopeViolationError(str(e)) from e


def validate_scanner_scope(
    payload: Mapping[str, Any],
    *,
    expected_avenue_id: str,
    expected_gate_id: str,
    runtime_root: Optional[Path] = None,
) -> None:
    try:
        assert_matching_scope(payload, expected_avenue_id=expected_avenue_id, expected_gate_id=expected_gate_id)
    except ScopeContaminationError as e:
        _log_violation("scanner_scope", str(e), runtime_root=runtime_root)
        raise ScopeViolationError(str(e)) from e


def validate_summary_scope(
    payload: Mapping[str, Any],
    *,
    expected_avenue_id: Optional[str] = None,
    expected_gate_id: Optional[str] = None,
    allow_system_wide: bool = False,
    runtime_root: Optional[Path] = None,
) -> None:
    if allow_system_wide and str(payload.get("scope_level") or "") == "system":
        return
    try:
        assert_matching_scope(payload, expected_avenue_id=expected_avenue_id, expected_gate_id=expected_gate_id)
    except ScopeContaminationError as e:
        _log_violation("summary_scope", str(e), runtime_root=runtime_root)
        raise ScopeViolationError(str(e)) from e


def scoped_write_json(
    path: Path,
    payload: Mapping[str, Any],
    *,
    expected_avenue_id: Optional[str] = None,
    expected_gate_id: Optional[str] = None,
    kind: str = "artifact",
    runtime_root: Optional[Path] = None,
) -> None:
    """Validate trade-like scope then write JSON (raises on mismatch)."""
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    if kind == "trade":
        validate_trade_scope(
            payload,
            expected_avenue_id=expected_avenue_id,
            expected_gate_id=expected_gate_id,
            runtime_root=root,
        )
    elif kind == "edge":
        if not expected_avenue_id or not expected_gate_id:
            raise ScopeViolationError("edge writes require avenue_id and gate_id")
        validate_edge_scope(
            payload,
            expected_avenue_id=expected_avenue_id,
            expected_gate_id=expected_gate_id,
            runtime_root=root,
        )
    elif kind == "scanner":
        if not expected_avenue_id or not expected_gate_id:
            raise ScopeViolationError("scanner writes require avenue_id and gate_id")
        validate_scanner_scope(
            payload,
            expected_avenue_id=expected_avenue_id,
            expected_gate_id=expected_gate_id,
            runtime_root=root,
        )
    else:
        assert_matching_scope(payload, expected_avenue_id=expected_avenue_id, expected_gate_id=expected_gate_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), indent=2, default=str), encoding="utf-8")
