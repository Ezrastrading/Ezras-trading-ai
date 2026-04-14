"""
Tamper-evident append-only audit log using a hash chain (in-repo).

Each record: payload + prev_hash + record_hash where
record_hash = SHA-256(prev_hash | canonical_json(payload) | timestamp_iso)
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_ai.automation.risk_bucket import runtime_root

logger = logging.getLogger(__name__)
_lock = threading.Lock()

CHAIN_FILENAME = "governance_audit_chain.jsonl"
GENESIS_PREV = "0" * 64


def chain_path() -> Path:
    p = runtime_root() / "logs" / CHAIN_FILENAME
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _canonical_payload(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _hash_record(prev_hash: str, payload_canonical: str, ts_iso: str) -> str:
    h = hashlib.sha256()
    h.update(prev_hash.encode("ascii"))
    h.update(b"|")
    h.update(payload_canonical.encode("utf-8"))
    h.update(b"|")
    h.update(ts_iso.encode("ascii"))
    return h.hexdigest()


def _last_record_hash(path: Path) -> str:
    if not path.is_file():
        return GENESIS_PREV
    prev = GENESIS_PREV
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            prev = str(row.get("record_hash", prev))
    except (OSError, json.JSONDecodeError):
        return GENESIS_PREV
    return prev


def append_chained_event(payload: Dict[str, Any], *, chain_file: Optional[Path] = None) -> Dict[str, Any]:
    """Append one record; return the full record dict."""
    path = chain_file or chain_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    prev = _last_record_hash(path)
    canon = _canonical_payload(payload)
    rec_hash = _hash_record(prev, canon, ts)
    record = {
        "prev_hash": prev,
        "record_hash": rec_hash,
        "timestamp": ts,
        "payload": payload,
    }
    line = json.dumps(record, sort_keys=True, default=str) + "\n"
    with _lock:
        with path.open("a", encoding="utf-8") as f:
            f.write(line)
    return record


@dataclass
class VerifyResult:
    ok: bool
    records_verified: int
    first_bad_line: Optional[int]
    detail: str


def verify_audit_chain(path: Optional[Path] = None) -> VerifyResult:
    p = path or chain_path()
    if not p.is_file():
        return VerifyResult(True, 0, None, "empty_chain")
    prev_expected = GENESIS_PREV
    n = 0
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return VerifyResult(False, 0, None, f"read_error:{exc}")
    for i, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            return VerifyResult(False, n, i, "json_decode_error")
        ph = str(row.get("prev_hash", ""))
        rh = str(row.get("record_hash", ""))
        ts = str(row.get("timestamp", ""))
        pl = row.get("payload")
        if ph != prev_expected:
            return VerifyResult(False, n, i, f"prev_hash_mismatch expected={prev_expected} got={ph}")
        if not isinstance(pl, dict):
            return VerifyResult(False, n, i, "payload_not_object")
        canon = _canonical_payload(pl)
        calc = _hash_record(ph, canon, ts)
        if calc != rh:
            return VerifyResult(False, n, i, f"record_hash_mismatch calc={calc} stored={rh}")
        prev_expected = rh
        n += 1
    return VerifyResult(True, n, None, "ok")


def chain_status() -> Dict[str, Any]:
    vr = verify_audit_chain()
    return {
        "chain_path": str(chain_path()),
        "verification_ok": vr.ok,
        "records_verified": vr.records_verified,
        "first_bad_line": vr.first_bad_line,
        "detail": vr.detail,
    }
