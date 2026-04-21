"""Naive same-base correlation guard for Gate B."""

from __future__ import annotations

from typing import Iterable, List, Sequence


def _base(pid: str) -> str:
    p = (pid or "").upper().split("-")[0]
    return p[:4] if len(p) >= 4 else p


def evaluate_portfolio_correlation(
    open_product_ids: Sequence[str],
    *,
    proposed_product_id: str,
    max_high_corr: int,
) -> dict:
    base = _base(proposed_product_id)
    cluster: List[str] = []
    for oid in open_product_ids:
        if _base(str(oid)) == base:
            cluster.append(str(oid))
    if proposed_product_id not in cluster:
        cluster = list(cluster) + [proposed_product_id]
    allowed = len(cluster) <= max(1, int(max_high_corr))
    return {
        "allowed": allowed,
        "cluster_size": len(cluster),
        "cluster": cluster,
    }
