"""Structured, evidence-gated learning under intelligence (separate from trading_ai.learning runtime memory)."""

from trading_ai.intelligence.learning.domain_catalog import DOMAIN_IDS
from trading_ai.intelligence.learning.registry import default_registry, load_or_init_registry, save_registry
from trading_ai.intelligence.learning.synthesis import synthesize_learning_priorities
from trading_ai.intelligence.learning.updater import default_domain_document, ensure_domain_files, maybe_update_domain

__all__ = [
    "DOMAIN_IDS",
    "default_registry",
    "load_or_init_registry",
    "save_registry",
    "ensure_domain_files",
    "default_domain_document",
    "maybe_update_domain",
    "synthesize_learning_priorities",
]
