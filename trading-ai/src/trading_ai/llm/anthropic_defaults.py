"""Central Anthropic model identifiers — avoid deprecated defaults that emit SDK DeprecationWarning.

See https://docs.claude.com/en/docs/resources/model-deprecations — replace as Anthropic updates EOL dates.
"""

from __future__ import annotations

# Active defaults (aliases per Anthropic docs; not in SDK DEPRECATED_MODELS as of SDK patterns used in CI).
DEFAULT_ANTHROPIC_MESSAGES_MODEL = "claude-sonnet-4-6"
DEFAULT_STRATEGY_RESEARCH_HAIKU_MODEL = "claude-haiku-4-5-20251001"
