"""
Resolve Supabase JWT for API clients (PostgREST / ``create_client``).

**Precedence (deterministic):**

1. ``SUPABASE_KEY`` — anon or service key you export as the primary secret.
2. ``SUPABASE_SERVICE_ROLE_KEY`` — used only if ``SUPABASE_KEY`` is unset or empty.

If both are set, ``SUPABASE_KEY`` wins. Never log key material.
"""

from __future__ import annotations

import os
from typing import Optional, Tuple


def resolve_supabase_jwt_key() -> Tuple[Optional[str], str]:
    """
    Returns (jwt_or_none, source_label).

    ``source_label`` is one of ``SUPABASE_KEY``, ``SUPABASE_SERVICE_ROLE_KEY``, or ``none``.
    """
    k = (os.environ.get("SUPABASE_KEY") or "").strip()
    if k:
        return k, "SUPABASE_KEY"
    k2 = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if k2:
        return k2, "SUPABASE_SERVICE_ROLE_KEY"
    return None, "none"
