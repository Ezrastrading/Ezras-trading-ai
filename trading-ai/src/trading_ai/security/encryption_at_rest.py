"""
Symmetric encryption-at-rest for selected runtime state (Fernet).

Environment:
  EZRAS_STATE_ENCRYPTION_KEY — optional. URL-safe base64-encoded 32-byte key
  (``Fernet.generate_key()``). When unset, encryption is disabled and status is explicit.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

_MAGIC = b"EZRAS_F1\n"

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:  # pragma: no cover
    Fernet = None  # type: ignore[misc, assignment]
    InvalidToken = Exception  # type: ignore[misc, assignment]


def encryption_key_configured() -> bool:
    raw = (os.environ.get("EZRAS_STATE_ENCRYPTION_KEY") or "").strip()
    return bool(raw)


def encryption_available() -> bool:
    """False if library missing or key invalid."""
    if not encryption_key_configured():
        return False
    if Fernet is None:
        return False
    try:
        _fernet()
        return True
    except ValueError:
        return False


def encryption_status() -> Dict[str, Any]:
    key_set = encryption_key_configured()
    lib_ok = Fernet is not None
    usable = encryption_available()
    return {
        "encryption_enabled": usable,
        "encryption_key_configured": key_set,
        "cryptography_library_present": lib_ok,
        "explicit_state": (
            "encrypted_at_rest"
            if usable
            else (
                "unencrypted_explicit"
                if not key_set
                else "key_invalid_or_crypto_unavailable"
            )
        ),
        "protected_paths_pattern": [
            "operator_registry.json when encryption enabled",
            "optional_encrypted_snapshots_under_state/",
        ],
    }


def _fernet() -> "Fernet":
    if Fernet is None:
        raise RuntimeError("cryptography package required for encryption")
    key = (os.environ.get("EZRAS_STATE_ENCRYPTION_KEY") or "").strip().encode("ascii")
    return Fernet(key)


def encrypt_bytes(plain: bytes) -> bytes:
    f = _fernet()
    return _MAGIC + f.encrypt(plain)


def decrypt_bytes(blob: bytes) -> bytes:
    if not blob.startswith(_MAGIC):
        raise ValueError("not_encrypted_or_wrong_magic")
    f = _fernet()
    return f.decrypt(blob[len(_MAGIC) :])


def verify_encryption_round_trip() -> Dict[str, Any]:
    """
    Operational proof: encrypt/decrypt a tiny payload with the configured key.
    If no key or crypto missing, returns explicit skipped state (not failure).
    """
    if not encryption_key_configured():
        return {
            "verified": False,
            "reason": "no_key_explicit_unencrypted",
            "operational_class": "encryption_explicitly_disabled",
        }
    if Fernet is None:
        return {
            "verified": False,
            "reason": "cryptography_not_installed",
            "operational_class": "encryption_misconfigured",
        }
    try:
        plain = b"ezras_encryption_probe_v1"
        blob = encrypt_bytes(plain)
        out = decrypt_bytes(blob)
        ok = out == plain
        return {
            "verified": ok,
            "round_trip_ok": ok,
            "operational_class": "encryption_available_and_verified" if ok else "encryption_misconfigured",
        }
    except Exception as exc:
        return {
            "verified": False,
            "round_trip_ok": False,
            "error": str(exc),
            "operational_class": "encryption_misconfigured",
        }


def encryption_operational_status() -> Dict[str, Any]:
    """Architectural + operational verification summary."""
    base = encryption_status()
    probe = verify_encryption_round_trip()
    if not base["encryption_key_configured"]:
        oc = "encryption_explicitly_disabled"
    elif probe.get("verified"):
        oc = "encryption_available_and_verified"
    else:
        oc = str(probe.get("operational_class") or "encryption_misconfigured")
    return {
        **base,
        "operational_verification": probe,
        "operational_class": oc,
    }


def write_encrypted_file(path: Path, plain: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(encrypt_bytes(plain))
    tmp.replace(path)


def read_maybe_encrypted_file(path: Path) -> bytes:
    raw = path.read_bytes()
    if raw.startswith(_MAGIC):
        return decrypt_bytes(raw)
    return raw


def encrypt_json_file(path: Path, obj: Any) -> None:
    import json

    write_encrypted_file(path, json.dumps(obj, indent=2, default=str).encode("utf-8"))


def read_json_maybe_encrypted(path: Path) -> Any:
    import json

    rawb = path.read_bytes()
    if rawb.startswith(_MAGIC) and not encryption_available():
        raise ValueError("encrypted_file_requires_EZRAS_STATE_ENCRYPTION_KEY")
    if rawb.startswith(_MAGIC):
        plain = decrypt_bytes(rawb)
    else:
        plain = rawb
    return json.loads(plain.decode("utf-8"))


def default_protected_paths(runtime_root: Path) -> List[str]:
    return [
        str(runtime_root / "state" / "operator_registry.json"),
    ]
