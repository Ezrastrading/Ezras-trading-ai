"""Production safety: failsafe guards and structured execution error codes."""

from trading_ai.safety.error_taxonomy import ExecutionErrorCode, normalize_error_code

__all__ = ["ExecutionErrorCode", "normalize_error_code"]
