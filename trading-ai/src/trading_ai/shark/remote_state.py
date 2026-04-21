"""Optional Supabase sync for shark JSON state (ephemeral VPS / Railway)."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from trading_ai.global_layer.supabase_env_keys import resolve_supabase_jwt_key

logger = logging.getLogger(__name__)


def supabase_configured() -> bool:
    u = (os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
    k, _src = resolve_supabase_jwt_key()
    return bool(u and k)


def _auth_headers() -> Dict[str, str]:
    key, _src = resolve_supabase_jwt_key()
    if not key:
        return {"apikey": "", "Authorization": "Bearer ", "Accept": "application/json"}
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
    }


def push_state_to_supabase(key: str, data: Dict[str, Any]) -> bool:
    if not supabase_configured():
        return False
    try:
        import requests
    except ImportError:
        return False

    url = f"{os.environ['SUPABASE_URL'].strip().rstrip('/')}/rest/v1/shark_state"
    body = {
        "key": key,
        "value": data,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    h = {
        **_auth_headers(),
        "Content-Type": "application/json",
        "Prefer": "return=minimal,resolution=merge-duplicates",
    }
    try:
        r = requests.post(url, headers=h, json=body, timeout=30)
        if 200 <= r.status_code < 300:
            return True
        if r.status_code in (409, 400):
            return _patch_state_row(key, data)
        logger.warning("supabase push HTTP %s: %s", r.status_code, r.text[:200])
    except Exception as exc:
        logger.warning("supabase push failed: %s", exc)
    return False


def _patch_state_row(key: str, data: Dict[str, Any]) -> bool:
    try:
        import requests
    except ImportError:
        return False

    import urllib.parse

    base = os.environ["SUPABASE_URL"].strip().rstrip("/")
    q = urllib.parse.urlencode({"key": f"eq.{key}"})
    url = f"{base}/rest/v1/shark_state?{q}"
    body = {
        "value": data,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        r = requests.patch(
            url,
            headers={**_auth_headers(), "Content-Type": "application/json", "Prefer": "return=minimal"},
            json=body,
            timeout=30,
        )
        return 200 <= r.status_code < 300
    except Exception:
        return False


def pull_state_from_supabase(key: str) -> Optional[Dict[str, Any]]:
    if not supabase_configured():
        return None
    try:
        import requests
    except ImportError:
        return None

    import urllib.parse

    base = os.environ["SUPABASE_URL"].strip().rstrip("/")
    q = urllib.parse.urlencode({"key": f"eq.{key}", "select": "value"})
    url = f"{base}/rest/v1/shark_state?{q}"
    h = _auth_headers()
    try:
        r = requests.get(url, headers=h, timeout=30)
        if r.status_code != 200:
            return None
        raw = r.json()
        if isinstance(raw, list) and raw and isinstance(raw[0].get("value"), dict):
            return raw[0]["value"]
    except Exception as exc:
        logger.warning("supabase pull %s: %s", key, exc)
    return None


def _read_json_file(path: str) -> Optional[Dict[str, Any]]:
    from pathlib import Path

    p = Path(path)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def sync_all_state_to_supabase() -> None:
    if not supabase_configured():
        return
    from trading_ai.governance.storage_architecture import shark_state_path
    from trading_ai.shark.treasury import treasury_path

    mapping = {
        "capital": shark_state_path("capital.json"),
        "positions": shark_state_path("positions.json"),
        "gaps": shark_state_path("gaps.json"),
        "bayesian": shark_state_path("bayesian.json"),
        "wallets": shark_state_path("wallets.json"),
        "treasury": treasury_path(),
    }
    for key, path in mapping.items():
        data = _read_json_file(str(path))
        if data is not None:
            push_state_to_supabase(key, data)


def restore_state_from_supabase() -> int:
    """Write local JSON from Supabase when files are missing. Returns count restored."""
    if not supabase_configured():
        return 0
    from trading_ai.governance.storage_architecture import shark_state_path
    from trading_ai.shark.treasury import treasury_path

    out = 0
    mapping = {
        "capital": shark_state_path("capital.json"),
        "positions": shark_state_path("positions.json"),
        "gaps": shark_state_path("gaps.json"),
        "bayesian": shark_state_path("bayesian.json"),
        "wallets": shark_state_path("wallets.json"),
        "treasury": treasury_path(),
    }
    for key, path in mapping.items():
        if path.is_file() and path.stat().st_size > 0:
            continue
        remote = pull_state_from_supabase(key)
        if remote is None:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(remote, indent=2), encoding="utf-8")
        out += 1
        logger.info("restored %s from supabase -> %s", key, path)
    return out
