"""
Classify Supabase / PostgREST errors for deployment readiness (no secrets).

Used by trade sync diagnostics and schema readiness probes so operators can tell:
- repo/config vs manual migration vs wrong project/credentials.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional


def classify_postgrest_exception(exc: BaseException) -> Dict[str, Any]:
    """
    Return stable categories + operator hints (no secrets).

    PostgREST often surfaces as ``APIError`` with ``code`` / message; HTTP status may appear in text.
    """
    name = type(exc).__name__
    raw = str(exc).strip()
    low = raw.lower()

    code = getattr(exc, "code", None)
    if code is not None:
        code = str(code)

    status: Optional[int] = None
    for attr in ("status", "status_code"):
        v = getattr(exc, attr, None)
        if isinstance(v, int):
            status = v
            break
    if status is None:
        m = re.search(r"\b(40[0-9]|50[0-9])\b", raw)
        if m:
            try:
                status = int(m.group(1))
            except ValueError:
                pass

    category = "unknown_postgrest_error"
    fix_scope = "investigate_message"
    hint = (
        "Compare exception text with Supabase dashboard: SQL Editor → confirm `public.trade_events` exists; "
        "Settings → API → project URL matches SUPABASE_URL; key matches project."
    )

    if not raw and not code:
        category = "empty_error_message"
        fix_scope = "client_or_network"
        hint = "Exception had no message — check network, supabase-py version, and client construction."

    if "could not find the table" in low or ("does not exist" in low and "schema" in low):
        category = "missing_table_or_schema_mismatch"
        fix_scope = "manual_sql_migration"
        hint = (
            "MANUAL: Apply repo files 1–3 from ``supabase/MIGRATION_ORDER.txt`` in the Supabase project "
            "that matches SUPABASE_URL (creates ``trade_events`` and related columns)."
        )

    if status == 404 or code == "404" or "404" in raw or ("not found" in low and "json" in low):
        category = "http_404_rest_route_or_table"
        fix_scope = "manual_migration_or_wrong_project_url"
        hint = (
            "HTTP 404 on ``/rest/v1/...`` usually means: (1) ``trade_events`` is missing in this database, "
            "(2) SUPABASE_URL points at the wrong Supabase project, or (3) table exists in another schema "
            "not exposed to PostgREST. MANUAL: Run migrations 1–3 in the correct project; confirm URL/key pair."
        )

    if status in (401, 403) or code in ("401", "403", "PGRST301") or "jwt" in low or "permission denied" in low:
        category = "auth_jwt_or_rls"
        fix_scope = "credentials_or_rls_manual"
        hint = (
            "MANUAL: Use service role or a key allowed to insert into ``trade_events``; check Row Level Security "
            "policies if using anon key."
        )

    if "pgrst" in low and "column" in low:
        category = "missing_column_remote_schema_drift"
        fix_scope = "manual_sql_migration"
        hint = (
            "MANUAL: Remote ``trade_events`` is missing a column the app sends — apply later migration files "
            "from the repo in order (see MIGRATION_ORDER.txt)."
        )

    if "connection" in low or "timeout" in low or "resolve" in low:
        category = "network_or_dns"
        fix_scope = "runtime_env"
        hint = "MANUAL: Verify outbound network, DNS, and that SUPABASE_URL host is reachable from this runtime."

    return {
        "exception_type": name,
        "category": category,
        "fix_scope": fix_scope,
        "operator_hint": hint,
        "http_status_guess": status,
        "postgrest_code": code,
        "message_excerpt": raw[:800],
    }
