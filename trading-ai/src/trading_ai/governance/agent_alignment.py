"""Agent alignment — Shark is explicitly subordinate to governance."""

from __future__ import annotations

SHARK_SUBORDINATE = True


def assert_shark_subordinate() -> bool:
    """Returns True when Shark module must defer to doctrine and operator CLI."""
    return SHARK_SUBORDINATE


def alignment_audit_blob() -> dict:
    return {"shark_subordinate": SHARK_SUBORDINATE, "governance_chain_required": True}
