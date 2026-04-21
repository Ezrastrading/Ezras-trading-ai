"""Storage backends: local filesystem (default) and future S3-style remote."""

from __future__ import annotations

from trading_ai.storage.storage_adapter import LocalStorageAdapter, StorageAdapter, get_storage_adapter

__all__ = ["StorageAdapter", "LocalStorageAdapter", "get_storage_adapter"]
