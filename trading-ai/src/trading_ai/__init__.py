"""Trading AI — prediction market monitoring and trade briefs (Phase 1)."""

# Allow a dual-repo deployment where private code overlays public scaffolding.
# Server sets PYTHONPATH with private first, then public.
from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)  # type: ignore[name-defined]

__version__ = "0.1.0"
