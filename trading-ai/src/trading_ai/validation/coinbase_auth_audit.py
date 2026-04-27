"""
Coinbase auth audit - checks environment variables without printing secrets.

Prints only exists=true/false and format checks.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def audit_coinbase_auth() -> dict:
    """
    Audit Coinbase auth environment variables.
    
    Returns dict with exists=true/false and format checks (no secrets).
    """
    key_vars = ["COINBASE_API_KEY_NAME", "COINBASE_API_KEY"]
    pem_vars = ["COINBASE_API_PRIVATE_KEY", "COINBASE_API_SECRET"]
    
    results = {
        "key_vars": {},
        "pem_vars": {},
        "summary": {},
    }
    
    # Check key ID variables
    for var in key_vars:
        value = (os.environ.get(var) or "").strip()
        exists = bool(value)
        
        # Format checks (no secrets)
        format_ok = False
        format_reason = ""
        if exists:
            # Key ID should look like "organizations/{org}/apiKeys/{key}"
            if value.startswith("organizations/"):
                parts = value.split("/")
                if len(parts) >= 4 and "apiKeys" in parts:
                    format_ok = True
                    format_reason = "valid_cdp_format"
                else:
                    format_reason = "malformed_cdp_path"
            else:
                format_reason = "not_cdp_format_may_be_legacy"
        
        results["key_vars"][var] = {
            "exists": exists,
            "format_ok": format_ok,
            "format_reason": format_reason,
        }
    
    # Check PEM variables
    for var in pem_vars:
        value = (os.environ.get(var) or "").strip()
        exists = bool(value)
        
        # Format checks (no secrets)
        format_ok = False
        format_reason = ""
        has_begin = False
        has_end = False
        has_escaped_newlines = False
        
        if exists:
            has_begin = "-----BEGIN EC PRIVATE KEY-----" in value
            has_end = "-----END EC PRIVATE KEY-----" in value
            has_escaped_newlines = "\\n" in value
            
            if has_begin and has_end:
                format_ok = True
                format_reason = "valid_pem_format"
            elif has_begin or has_end:
                format_reason = "incomplete_pem_headers"
            else:
                format_reason = "missing_pem_headers"
        
        results["pem_vars"][var] = {
            "exists": exists,
            "format_ok": format_ok,
            "format_reason": format_reason,
            "has_begin": has_begin,
            "has_end": has_end,
            "has_escaped_newlines": has_escaped_newlines,
        }
    
    # Summary
    key_exists = any(results["key_vars"][v]["exists"] for v in key_vars)
    pem_exists = any(results["pem_vars"][v]["exists"] for v in pem_vars)
    key_format_ok = any(results["key_vars"][v]["format_ok"] for v in key_vars)
    pem_format_ok = any(results["pem_vars"][v]["format_ok"] for v in pem_vars)
    
    results["summary"] = {
        "any_key_exists": key_exists,
        "any_pem_exists": pem_exists,
        "any_key_format_ok": key_format_ok,
        "any_pem_format_ok": pem_format_ok,
        "auth_material_present": key_exists and pem_exists,
        "auth_format_valid": key_format_ok and pem_format_ok,
    }
    
    return results


def print_auth_audit() -> None:
    """Print auth audit results."""
    audit = audit_coinbase_auth()
    
    print("=" * 80)
    print("COINBASE AUTH AUDIT")
    print("=" * 80)
    
    print("\nKEY VARIABLES:")
    for var, info in audit["key_vars"].items():
        print(f"  {var}:")
        print(f"    exists: {info['exists']}")
        print(f"    format_ok: {info['format_ok']}")
        print(f"    format_reason: {info['format_reason']}")
    
    print("\nPEM VARIABLES:")
    for var, info in audit["pem_vars"].items():
        print(f"  {var}:")
        print(f"    exists: {info['exists']}")
        print(f"    format_ok: {info['format_ok']}")
        print(f"    format_reason: {info['format_reason']}")
        print(f"    has_begin: {info['has_begin']}")
        print(f"    has_end: {info['has_end']}")
        print(f"    has_escaped_newlines: {info['has_escaped_newlines']}")
    
    print("\nSUMMARY:")
    for key, value in audit["summary"].items():
        print(f"  {key}: {value}")
    
    print("=" * 80)


if __name__ == "__main__":
    print_auth_audit()
