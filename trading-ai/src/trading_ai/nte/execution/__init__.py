"""NTE execution — import submodules directly (e.g. ``coinbase_engine``) to avoid import cycles."""

# Do not import coinbase_engine here: ``strategies.ab_router`` ↔ ``execution`` circular refs.
