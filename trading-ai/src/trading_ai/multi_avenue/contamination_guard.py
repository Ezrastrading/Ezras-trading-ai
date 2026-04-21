"""Helpers to prevent cross-avenue / cross-gate data contamination in scoped artifacts."""

from __future__ import annotations

from typing import Any, Mapping, Optional, Tuple


class ScopeContaminationError(ValueError):
    """Raised when a payload's scope keys conflict with the expected avenue/gate."""


def assert_matching_scope(
    payload: Mapping[str, Any],
    *,
    expected_avenue_id: Optional[str] = None,
    expected_gate_id: Optional[str] = None,
    strict: bool = True,
) -> None:
    """
    Validate that explicit scope fields in payload match the target path/operation.

    If ``strict`` and payload omits avenue_id/gate_id, no error (caller may be system scope).
    If present, they must match expected when expected is set.
    """
    av = payload.get("avenue_id")
    g = payload.get("gate_id")
    if expected_avenue_id is not None and av is not None and str(av) != str(expected_avenue_id):
        raise ScopeContaminationError(
            f"avenue_id mismatch: payload={av!r} expected={expected_avenue_id!r}"
        )
    if expected_gate_id is not None and g is not None and str(g) != str(expected_gate_id):
        raise ScopeContaminationError(
            f"gate_id mismatch: payload={g!r} expected={expected_gate_id!r}"
        )
    if strict and expected_avenue_id and av is None and expected_gate_id and g is None:
        # optional: do not require — many legacy payloads lack keys
        return


def paths_must_not_share_parent(
    path_a: str,
    path_b: str,
    *,
    avenue_a: str,
    avenue_b: str,
) -> Tuple[bool, str]:
    """
    Sanity check for writers: two artifact paths should not collide across avenues.

    Returns (ok, reason).
    """
    if avenue_a == avenue_b:
        return True, "same_avenue"
    if path_a == path_b:
        return False, "identical_path_different_avenues_forbidden"
    return True, "distinct_paths"


def validate_namespace_block(scope: Mapping[str, Any]) -> Tuple[bool, str]:
    """Light validation: at least one scope marker for non-system artifacts."""
    if scope.get("scope_level") == "system":
        return True, "ok"
    if scope.get("avenue_id") or scope.get("gate_id"):
        return True, "ok"
    return False, "missing_avenue_or_gate_for_non_system_scope"


def contamination_assert_paths_distinct_across_avenues(
    path_a: str,
    path_b: str,
    *,
    avenue_id_a: str,
    avenue_id_b: str,
) -> None:
    """Raise :class:`ScopeContaminationError` if two artifact paths would collide across avenues."""
    ok, reason = paths_must_not_share_parent(path_a, path_b, avenue_a=avenue_id_a, avenue_b=avenue_id_b)
    if not ok:
        raise ScopeContaminationError(reason)


def contamination_assert_payload_scope(
    payload: Mapping[str, Any],
    *,
    expected_avenue_id: str,
    expected_gate_id: Optional[str] = None,
) -> None:
    """Fail if explicit avenue_id / gate_id in payload disagree with expected scope."""
    assert_matching_scope(
        payload,
        expected_avenue_id=expected_avenue_id,
        expected_gate_id=expected_gate_id,
        strict=True,
    )
