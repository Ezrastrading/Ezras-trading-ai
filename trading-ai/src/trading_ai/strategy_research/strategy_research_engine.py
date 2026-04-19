"""Non-authoritative strategy research — logs hypotheses only; never drives execution."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from trading_ai.market_intelligence.market_intelligence_engine import active_markets_snapshot_path, get_active_markets
from trading_ai.runtime_paths import ezras_runtime_root
from trading_ai.strategy_research.research_execution_guard import assert_strategy_research_read_allowed

logger = logging.getLogger(__name__)

_RESEARCH_PROMPTS = [
    "What strategies perform in high volatility BTC?",
    "What edges exist in prediction markets with skewed odds?",
    "What time-based patterns exist?",
]


def strategy_research_dir() -> Path:
    return ezras_runtime_root() / "strategy_research"


def research_log_path() -> Path:
    return strategy_research_dir() / "research_log.jsonl"


def daily_summary_path() -> Path:
    return strategy_research_dir() / "daily_summary.json"


def _read_json_file(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception as exc:
        logger.warning("read_json %s: %s", path, exc)
        return {}


def _load_market_snapshot() -> Dict[str, Any]:
    p = active_markets_snapshot_path()
    if not p.is_file():
        return {}
    return _read_json_file(p)


def _recent_trade_context(*, max_events: int = 40) -> tuple[str, int]:
    try:
        from trading_ai.nte.databank.local_trade_store import load_all_trade_events
    except Exception as exc:
        logger.debug("databank unavailable: %s", exc)
        return "", 0
    try:
        events = load_all_trade_events()
    except Exception as exc:
        logger.warning("trade history read failed: %s", exc)
        return "", 0
    tail = events[-max_events:] if len(events) > max_events else events
    parts: List[str] = []
    for e in tail:
        if not isinstance(e, dict):
            continue
        parts.append(
            json.dumps(
                {
                    "avenue_id": e.get("avenue_id"),
                    "asset": e.get("asset"),
                    "strategy_id": e.get("strategy_id"),
                    "timestamp_close": e.get("timestamp_close"),
                },
                default=str,
            )
        )
    return "\n".join(parts[-max_events:]), len(tail)


def _parse_confidence(text: str) -> str:
    m = re.search(r"\b(HIGH|MEDIUM|LOW)\b", text.upper())
    if m:
        return m.group(1).upper()
    return "LOW"


def _gpt_research(prompt_block: str, *, stub: bool) -> str:
    if stub:
        return (
            "[stub] Research mode: consider volatility regimes, fee drag, and sample size. "
            "Confidence: LOW (no API)."
        )
    key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not key:
        return "[stub] OPENAI_API_KEY unset — offline hypothesis placeholder. Confidence: LOW."
    from openai import OpenAI

    model = (os.environ.get("STRATEGY_RESEARCH_GPT_MODEL") or "gpt-4o-mini").strip()
    client = OpenAI(api_key=key)
    comp = client.chat.completions.create(
        model=model,
        temperature=0.35,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a research assistant. Output concise hypotheses only. "
                    "Do not issue trade instructions. End with a line: Confidence: LOW|MEDIUM|HIGH."
                ),
            },
            {"role": "user", "content": prompt_block},
        ],
        timeout=120,
    )
    return (comp.choices[0].message.content or "").strip()


def _claude_research(prompt_block: str, *, stub: bool) -> str:
    if stub:
        return (
            "[stub] Qualitative review: skew and liquidity matter in prediction markets. "
            "Confidence: LOW (no API)."
        )
    key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not key:
        return "[stub] ANTHROPIC_API_KEY unset — offline hypothesis placeholder. Confidence: LOW."
    import anthropic

    model = (os.environ.get("STRATEGY_RESEARCH_CLAUDE_MODEL") or "claude-3-5-haiku-20241022").strip()
    client = anthropic.Anthropic(api_key=key)
    kwargs: Dict[str, Any] = {
        "model": model,
        "max_tokens": 1200,
        "system": (
            "You are a research assistant. Output concise hypotheses only. "
            "Do not issue trade instructions. End with a line: Confidence: LOW|MEDIUM|HIGH."
        ),
        "messages": [{"role": "user", "content": prompt_block}],
    }
    try:
        msg = client.messages.create(**kwargs, timeout=120)
    except TypeError:
        msg = client.messages.create(**kwargs)
    text = ""
    for block in msg.content:
        if hasattr(block, "text"):
            text += block.text
    return text.strip()


def _append_jsonl(path: Path, record: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, default=str) + "\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(line)


def _build_prompt_block(
    *,
    market_snapshot: Dict[str, Any],
    trade_snippet: str,
    n_trades: int,
) -> str:
    lines = [
        "Context: non-authoritative strategy research. Do not output executable trades.",
        f"Recent trade rows (truncated, n≈{n_trades}):",
        trade_snippet or "(none)",
        "",
        "Active markets snapshot (JSON excerpt):",
        json.dumps(market_snapshot, default=str)[:12000],
        "",
        "Answer these research prompts with short bullets + synthesis:",
    ]
    for p in _RESEARCH_PROMPTS:
        lines.append(f"- {p}")
    return "\n".join(lines)


def run_strategy_research_cycle(
    *,
    force_stub: bool = False,
    refresh_market_snapshot: bool = False,
    write_daily_summary: bool = True,
) -> Dict[str, Any]:
    """
    Read snapshot + databank, query GPT and Claude, append JSONL lines only.

    Does not touch execution. Research consumers must use guarded readers (dashboards/analysis).
    """
    stub = bool(force_stub or (os.environ.get("STRATEGY_RESEARCH_FORCE_STUB") or "").strip() == "1")
    if not active_markets_snapshot_path().is_file() or refresh_market_snapshot:
        try:
            get_active_markets(force_snapshot=True)
        except Exception as exc:
            logger.warning("market snapshot refresh failed: %s", exc)
    market_snapshot = _load_market_snapshot()
    trade_snippet, n_trades = _recent_trade_context()
    prompt_block = _build_prompt_block(
        market_snapshot=market_snapshot,
        trade_snippet=trade_snippet,
        n_trades=n_trades,
    )
    market_context = json.dumps(
        {
            "snapshot_keys": list(market_snapshot.keys()),
            "trade_rows_used": n_trades,
        },
        default=str,
    )
    ts = datetime.now(timezone.utc).isoformat()
    out: Dict[str, Any] = {"entries_written": 0, "log": str(research_log_path())}

    for source, fn in (("gpt", _gpt_research), ("claude", _claude_research)):
        try:
            text = fn(prompt_block, stub=stub)
        except Exception as exc:
            logger.warning("%s research call failed: %s", source, exc)
            text = f"[error] {exc}"[:2000]
        conf = _parse_confidence(text)
        rec = {
            "timestamp": ts,
            "market_context": market_context,
            "hypothesis": text[:8000],
            "confidence": conf if conf in ("LOW", "MEDIUM", "HIGH") else "LOW",
            "source": source,
            "validated": False,
        }
        _append_jsonl(research_log_path(), rec)
        out["entries_written"] += 1

    if write_daily_summary:
        try:
            _write_daily_summary()
        except Exception as exc:
            logger.warning("daily summary failed: %s", exc)

    try:
        from trading_ai.edge.research_bridge import materialize_from_research_log_path

        out["edge_materialization"] = materialize_from_research_log_path(
            research_log_path(),
            lines_limit=12,
        )
    except Exception as exc:
        logger.warning("edge materialization from research log failed: %s", exc)
        out["edge_materialization"] = {"ok": False, "error": str(exc)[:500]}

    return out


def _top_tokens_from_hypotheses(lines: List[Dict[str, Any]]) -> List[str]:
    """Very small heuristic — not trade signals."""
    bag: Dict[str, int] = {}
    for row in lines:
        h = str(row.get("hypothesis") or "")
        for w in re.findall(r"[A-Za-z]{5,}", h.lower()):
            if w in ("confidence", "research", "market", "hypothesis", "strategies"):
                continue
            bag[w] = bag.get(w, 0) + 1
    ranked = sorted(bag.items(), key=lambda x: x[1], reverse=True)
    return [f"{k} (mentions={v})" for k, v in ranked[:12]]


def _write_daily_summary() -> None:
    path = research_log_path()
    if not path.is_file():
        return
    raw_lines = path.read_text(encoding="utf-8").splitlines()
    parsed: List[Dict[str, Any]] = []
    for line in raw_lines[-500:]:
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
            if isinstance(o, dict):
                parsed.append(o)
        except json.JSONDecodeError:
            continue
    hyps = [str(x.get("hypothesis") or "")[:400] for x in parsed[-20:]]
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "disclaimer": "not_trade_signals",
        "top_hypotheses": hyps,
        "repeated_patterns": _top_tokens_from_hypotheses(parsed[-100:]),
    }
    outp = daily_summary_path()
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")


def iter_research_log_entries(
    *,
    max_lines: Optional[int] = None,
) -> Iterator[Dict[str, Any]]:
    """Iterate research log lines — allowed from dashboards/analysis only."""
    assert_strategy_research_read_allowed()
    path = research_log_path()
    if not path.is_file():
        return iter(())
    lines = path.read_text(encoding="utf-8").splitlines()
    if max_lines is not None and max_lines > 0:
        lines = lines[-max_lines:]
    out: List[Dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
            if isinstance(o, dict):
                out.append(o)
        except json.JSONDecodeError:
            continue
    return iter(out)


def load_daily_summary_for_review() -> Dict[str, Any]:
    """Load daily summary — allowed from dashboards/analysis only."""
    assert_strategy_research_read_allowed()
    return _read_json_file(daily_summary_path())
