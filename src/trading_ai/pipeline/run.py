from __future__ import annotations

import logging

from trading_ai.ai.brief import generate_trade_brief
from trading_ai.automation.alerts import send_trade_brief_alert
from trading_ai.config import Settings
from trading_ai.intake.bundle import enrich_market
from trading_ai.intake.gpt_researcher_hooks import reset_gpt_researcher_runtime_state
from trading_ai.market.filters import filter_candidates
from trading_ai.clients.polymarket import fetch_markets, to_candidate
from trading_ai.models.schemas import AlertRecord
from trading_ai.storage.store import Store

logger = logging.getLogger(__name__)


def run_pipeline(settings: Settings) -> str:
    store = Store(settings.data_dir / "trading_ai.sqlite")
    run_id = store.new_run_id()
    reset_gpt_researcher_runtime_state()

    raw = fetch_markets(settings)
    candidates = [to_candidate(m) for m in raw]
    filtered = filter_candidates(candidates, settings)
    filtered = sorted(
        filtered,
        key=lambda c: (c.volume_usd or 0.0),
        reverse=True,
    )[: settings.max_candidates_per_run]

    logger.info("Run %s: %s candidates after filters (cap %s)", run_id, len(filtered), settings.max_candidates_per_run)

    for market in filtered:
        store.log_market(run_id, market)
        try:
            bundle = enrich_market(settings, market)
            store.log_enrichment(run_id, bundle)
        except Exception:
            logger.exception("Enrichment failed for %s", market.market_id)
            continue
        try:
            brief = generate_trade_brief(settings, market, bundle)
        except Exception:
            logger.exception("Brief generation failed for %s", market.market_id)
            continue
        store.log_brief(run_id, brief)

        if brief.signal_score >= settings.alert_min_signal_score:
            source_urls = [r.url for r in bundle.tavily_results[:2]]
            ok, sent_at = send_trade_brief_alert(
                settings,
                brief,
                run_id=run_id,
                source_urls=source_urls,
            )
            if ok:
                summary = f"signal={brief.signal_score} implied={brief.implied_probability}"
                store.log_alert(
                    run_id,
                    AlertRecord(
                        market_id=brief.market_id,
                        brief_created_at=brief.created_at,
                        channel="telegram",
                        payload_summary=summary,
                        sent_at=sent_at,
                    ),
                )
                logger.info("Alert sent for %s", brief.market_id)
            else:
                logger.info("Alert skipped or failed for %s", brief.market_id)
        else:
            logger.debug("Below alert threshold: %s score=%s", brief.market_id, brief.signal_score)

    return run_id
