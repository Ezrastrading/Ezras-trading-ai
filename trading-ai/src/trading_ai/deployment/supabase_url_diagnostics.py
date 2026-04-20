"""Non-secret Supabase URL / PostgREST path diagnostics for operator reports."""

from __future__ import annotations

import os
from typing import Any, Dict, Optional
from urllib.parse import urlparse


def build_supabase_runtime_diagnostics() -> Dict[str, Any]:
    """
    Describes what the app will call — no network I/O.

    ``SUPABASE_URL`` should be ``https://<project-ref>.supabase.co`` from Dashboard → Settings → API.
    """
    raw = (os.environ.get("SUPABASE_URL") or "").strip()
    if not raw:
        return {
            "supabase_url_configured": False,
            "supabase_url_host": None,
            "supabase_url_scheme": None,
            "looks_like_supabase_project_url": False,
            "expected_postgrest_base": None,
            "example_trade_events_path": "/rest/v1/trade_events",
            "operator_note": "Set SUPABASE_URL to your project URL (Settings → API → Project URL).",
        }
    parsed = urlparse(raw)
    host = (parsed.netloc or "").strip().lower()
    scheme = (parsed.scheme or "").lower()
    # Typical hosted: xyzcompany.supabase.co
    looks = bool(host) and (
        host.endswith(".supabase.co")
        or host.endswith(".supabase.in")  # some regions
    )
    base = f"{scheme}://{host}" if scheme and host else None
    postgrest = f"{base}/rest/v1" if base else None
    return {
        "supabase_url_configured": True,
        "supabase_url_host": host or None,
        "supabase_url_scheme": scheme or None,
        "looks_like_supabase_project_url": looks,
        "expected_postgrest_base": postgrest,
        "example_trade_events_path": "/rest/v1/trade_events",
        "full_example_trade_events_url": f"{postgrest}/trade_events" if postgrest else None,
        "operator_note": (
            "If this host does not match Dashboard → Settings → API → Project URL, "
            "you are pointed at the wrong project (keys must match the same project)."
        )
        if looks
        else (
            "Host does not look like the default *.supabase.co project URL — confirm you are not using "
            "a proxy or custom domain without PostgREST, or a copy-paste error."
        ),
    }


def hypothesis_for_schema_failure(
    *,
    remote_ok: bool,
    category: Optional[str],
    message_excerpt: str,
) -> str:
    """
    Explicit operator-facing hypothesis — not a silent guess; ties to classifier category when present.
    """
    if remote_ok:
        return "Remote schema probe succeeded — no Supabase migration hypothesis needed."
    low = (message_excerpt or "").lower()
    cat = category or ""

    if cat == "http_404_rest_route_or_table" or "404" in low:
        return (
            "LIKELY: (1) `public.trade_events` was never created in the database behind this SUPABASE_URL — "
            "apply repo SQL migrations 1–3 (see supabase/MIGRATION_ORDER.txt or ALL_REQUIRED_LIVE_MIGRATIONS.sql). "
            "OR (2) SUPABASE_URL / API key refer to a different Supabase project than where SQL was run — "
            "compare Dashboard Project URL with env character-for-character."
        )
    if "column" in low and "does not exist" in low:
        return (
            "LIKELY: `trade_events` exists but is missing columns the app sends — apply migrations 2 and 3 "
            "after migration 1 in the SAME project (edge + ACCO columns)."
        )
    if cat == "auth_jwt_or_rls" or "permission" in low or "jwt" in low:
        return (
            "LIKELY: API key cannot read/insert `trade_events` — use service role for server-side sync or fix RLS "
            "for the key you use (anon vs service_role)."
        )
    if cat == "network_or_dns" or "connection" in low:
        return "LIKELY: Network/DNS from this host to Supabase — not a schema issue until connectivity works."

    return (
        "Review `error_classification.category` and `message_excerpt` in supabase_schema_readiness.json — "
        "compare with Supabase Dashboard Table Editor and Settings → API."
    )


def repo_all_migrations_sql_hint() -> str:
    return "supabase/ALL_REQUIRED_LIVE_MIGRATIONS.sql (repo root) — ordered 1+2+3 for convenience."
