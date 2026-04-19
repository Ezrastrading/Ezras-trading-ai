"""Global data/knowledge intelligence — internal first, external to research queue."""

from __future__ import annotations

from typing import Any, Dict, Optional

from trading_ai.global_layer.external_source_reader import read_external_candidates
from trading_ai.global_layer.global_memory_store import GlobalMemoryStore
from trading_ai.global_layer.internal_data_reader import read_normalized_internal
from trading_ai.global_layer.knowledge_synthesizer import synthesize
from trading_ai.global_layer.market_microstructure_learner import learn_from_trades, merge_into_market_knowledge
from trading_ai.global_layer.pnl_aggregator import refresh_global_pnl_files


class DataKnowledgeEngine:
    def __init__(self, store: Optional[GlobalMemoryStore] = None) -> None:
        self.store = store or GlobalMemoryStore()

    def run_once(self) -> Dict[str, Any]:
        internal = read_normalized_internal()
        trades = internal["trades"]
        refresh_global_pnl_files(self.store, trades)

        learned = learn_from_trades(trades, avenue="coinbase")
        mk = self.store.load_json("market_knowledge.json")
        self.store.save_json("market_knowledge.json", merge_into_market_knowledge(mk, learned))

        ck = self.store.load_json("avenue_knowledge/coinbase_knowledge.json")
        ck.setdefault("internal_findings", []).append(
            {"microstructure": learned, "source": "trade_memory"}
        )
        ck["internal_findings"] = ck["internal_findings"][-40:]
        self.store.save_json("avenue_knowledge/coinbase_knowledge.json", ck)

        ext = read_external_candidates()
        syn = synthesize(internal=internal, external_candidates=ext["candidates"], store=self.store)

        return {"learned": learned, "synthesis": syn}
