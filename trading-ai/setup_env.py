#!/usr/bin/env python3
"""One-shot environment setup before $50 deployment. Run: python setup_env.py"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

_ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT_DIR / "src"))
try:
    from dotenv import load_dotenv

    load_dotenv(_ROOT_DIR / ".env")
    load_dotenv(Path.cwd() / ".env")
except ImportError:
    pass


# Polymarket / Kalshi keys optional (US policy, or credentials pending — system can compound elsewhere)
REQUIRED = [
    "EZRAS_RUNTIME_ROOT",
    "MANIFOLD_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "FRED_API_KEY",
    "STARTING_CAPITAL",
]


def _print_polymarket_optional_warnings() -> None:
    if not (os.environ.get("POLY_WALLET_KEY") or "").strip():
        print(
            "⚠️  Polymarket: scan-only mode\n"
            "        (no wallet key — US operators\n"
            "        cannot execute on Polymarket)"
        )
    if not (os.environ.get("POLY_API_KEY") or "").strip():
        print(
            "⚠️  Polymarket API: market scanning\n"
            "        will use public endpoints only"
        )


def _print_kalshi_optional_warnings() -> None:
    if not (os.environ.get("KALSHI_API_KEY") or "").strip():
        print(
            "⚠️ Kalshi: no API key — scan-only mode\n"
            "        (optional — add key when ready)"
        )


def _print_treasury_optional_warnings() -> None:
    if not (os.environ.get("MASTER_WALLET_ADDRESS") or "").strip():
        print(
            "⚠️ Treasury: MASTER_WALLET_ADDRESS not set\n"
            "        (optional — add to .env to enable withdrawal alerts)"
        )


def _root() -> Path:
    raw = os.environ.get("EZRAS_RUNTIME_ROOT", "").strip() or str(Path.home() / "ezras-runtime")
    return Path(raw).expanduser().resolve()


def ensure_dirs(rt: Path) -> None:
    for sub in ("shark/state", "shark/state/backups", "shark/logs"):
        (rt / sub).mkdir(parents=True, exist_ok=True)


def init_capital(rt: Path) -> None:
    p = rt / "shark" / "state" / "capital.json"
    if p.is_file():
        return
    from datetime import datetime, timezone

    cap = float(os.environ.get("STARTING_CAPITAL", "25"))
    data = {
        "current_capital": cap,
        "starting_capital": cap,
        "peak_capital": cap,
        "phase": "phase_1",
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "total_trades": 0,
        "winning_trades": 0,
        "losing_trades": 0,
        "monthly_start_capital": cap,
        "monthly_target": 375.0,
        "year_end_target": 500000.0,
        "acceleration_mode": True,
    }
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _http_get(url: str, headers: Dict[str, str] | None = None) -> Tuple[int, str]:
    try:
        import urllib.error
        import urllib.request
    except ImportError:
        return 0, "no_urllib"
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "EzrasSetup/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.getcode(), r.read(500).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        try:
            body = e.read(500).decode("utf-8", errors="replace")
        except Exception:
            body = str(e)
        return int(e.code), body
    except Exception as e:
        return -1, str(e)


def test_polymarket() -> Tuple[bool, str]:
    code, body = _http_get("https://clob.polymarket.com/markets?limit=1")
    if code == 200 and ("markets" in body or "[" in body):
        return True, "ok"
    return False, f"HTTP {code} {body[:80]}"


def test_kalshi() -> Tuple[bool, str]:
    base = os.environ.get("KALSHI_API_BASE", "https://api.elections.kalshi.com/trade-api/v2").rstrip("/")
    url = f"{base}/exchange/status"
    key = (os.environ.get("KALSHI_API_KEY") or "").strip()
    if key and "-----BEGIN" in key.replace("\\n", "\n") and "PRIVATE KEY-----" in key.replace("\\n", "\n"):
        aid = (os.environ.get("KALSHI_ACCESS_KEY_ID") or "").strip()
        if not aid:
            return False, "KALSHI_ACCESS_KEY_ID required with RSA private key in KALSHI_API_KEY"
        try:
            from trading_ai.shark.outlets.kalshi import build_kalshi_request_headers

            headers = dict(build_kalshi_request_headers("GET", url))
            headers["User-Agent"] = "EzrasSetup/1.0"
        except Exception as e:
            return False, f"kalshi sign: {e!s}"[:120]
    elif key:
        headers = {"Authorization": f"Bearer {key}", "User-Agent": "EzrasSetup/1.0"}
    else:
        headers = {"User-Agent": "EzrasSetup/1.0"}
    code, body = _http_get(url, headers=headers)
    if code == 401:
        # Non-blocking: compound on other outlets while Kalshi auth is fixed
        return True, "scan_only_401"
    if code == 200:
        return True, "ok"
    return False, f"HTTP {code} {body[:120]}"


def test_manifold() -> Tuple[bool, str]:
    code, body = _http_get("https://api.manifold.markets/v0/markets?limit=1")
    if code == 200:
        return True, "ok"
    return False, f"HTTP {code}"


def test_telegram() -> Tuple[bool, str]:
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat:
        return False, "missing token or chat"
    try:
        import urllib.parse
        import urllib.request

        text = "🦈 Ezras setup test — system initializing"
        payload = urllib.parse.urlencode({"chat_id": chat, "text": text, "parse_mode": "HTML"}).encode()
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        req = urllib.request.Request(url, data=payload, method="POST", headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read().decode()
            if r.getcode() == 200 and '"ok":true' in raw.replace(" ", ""):
                return True, "ok"
            return False, raw[:200]
    except Exception as e:
        return False, str(e)


def main() -> int:
    _print_polymarket_optional_warnings()
    _print_kalshi_optional_warnings()
    _print_treasury_optional_warnings()
    missing = [k for k in REQUIRED if not (os.environ.get(k) or "").strip()]
    if missing:
        for k in missing:
            print(
                f"Missing required env var: {k} — "
                "add it to your .env file (see .env.template)"
            )
        return 1

    rt = _root()
    os.environ["EZRAS_RUNTIME_ROOT"] = str(rt)
    ensure_dirs(rt)
    init_capital(rt)

    results: List[Tuple[str, bool, str]] = []
    for name, fn in (
        ("Polymarket", test_polymarket),
        ("Kalshi", test_kalshi),
        ("Manifold", test_manifold),
    ):
        ok, reason = fn()
        results.append((name, ok, reason))
        if name == "Kalshi" and reason == "scan_only_401":
            print(
                "⚠️ Kalshi: auth failed — scan-only mode or check API key permissions"
            )
        elif ok:
            print(f"✅ {name}: connected")
        else:
            print(f"❌ {name}: FAILED — {reason}")

    tok_ok, tok_reason = test_telegram()
    sym = "✅" if tok_ok else "❌"
    print(f"{sym} Telegram: {'connected' if tok_ok else 'FAILED — ' + tok_reason}")

    all_ok = all(r[1] for r in results) and tok_ok
    rec_path = rt / "shark" / "state" / "capital.json"
    cap = 50.0
    if rec_path.is_file():
        try:
            cap = float(json.loads(rec_path.read_text()).get("current_capital", 50))
        except Exception:
            pass

    if all_ok:
        print()
        print("✅ ALL SYSTEMS GO")
        print(f"   Capital: ${cap:.2f}")
        print("   Phase: phase_1")
        print("   Run: python -m trading_ai.shark.run_shark")
        return 0

    print()
    print("⚠️ FIX ABOVE BEFORE DEPLOYING")
    return 2


if __name__ == "__main__":
    sys.exit(main())
