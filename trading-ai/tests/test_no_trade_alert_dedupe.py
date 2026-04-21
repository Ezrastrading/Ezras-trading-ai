"""no_trade 10m Telegram dedupe: one alert per trade-ref streak, persisted, concurrent-safe."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from trading_ai.shark.no_trade_alert_dedupe import (
    try_send_no_trade_idle_alert,
)


def test_single_quiet_window_one_alert_only(tmp_path: Path) -> None:
    state = tmp_path / "no_trade_10m_alert.json"
    sends: list[str] = []

    ref = 1_000_000.0
    now = ref + 700.0

    assert try_send_no_trade_idle_alert(
        now=now,
        trade_ref_epoch=ref,
        send=lambda m: sends.append(m) or True,
        state_path=state,
    )
    assert len(sends) == 1

    for _ in range(15):
        try_send_no_trade_idle_alert(
            now=now + 60.0 * _,
            trade_ref_epoch=ref,
            send=lambda m: sends.append(m) or True,
            state_path=state,
        )
    assert len(sends) == 1


def test_concurrent_ticks_single_send(tmp_path: Path) -> None:
    state = tmp_path / "no_trade_10m_alert.json"
    sends: list[str] = []
    ref = 2_000_000.0
    now = ref + 800.0
    lock = threading.Lock()

    def send(m: str) -> bool:
        with lock:
            sends.append(m)
        return True

    def run_once() -> None:
        try_send_no_trade_idle_alert(
            now=now,
            trade_ref_epoch=ref,
            send=send,
            state_path=state,
        )

    threads = [threading.Thread(target=run_once) for _ in range(24)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(sends) == 1


def test_new_trade_resets_eligibility_second_alert(tmp_path: Path) -> None:
    state = tmp_path / "no_trade_10m_alert.json"
    sends: list[str] = []

    ref1 = 3_000_000.0
    assert try_send_no_trade_idle_alert(
        now=ref1 + 700.0,
        trade_ref_epoch=ref1,
        send=lambda m: sends.append(m) or True,
        state_path=state,
    )
    assert len(sends) == 1

    ref2 = ref1 + 10_000.0
    assert try_send_no_trade_idle_alert(
        now=ref2 + 700.0,
        trade_ref_epoch=ref2,
        send=lambda m: sends.append(m) or True,
        state_path=state,
    )
    assert len(sends) == 2


def test_persisted_state_prevents_resend_after_restart(tmp_path: Path) -> None:
    state = tmp_path / "no_trade_10m_alert.json"
    ref = 4_000_000.0
    data = {
        "schema_version": 1,
        "announced_for_trade_ref_epoch": ref,
        "last_alert_sent_unix": ref + 650.0,
        "last_alert_dedupe_key": f"no_trade_10m:global:{round(ref, 6)}",
    }
    state.write_text(json.dumps(data), encoding="utf-8")

    sends: list[str] = []
    assert not try_send_no_trade_idle_alert(
        now=ref + 100_000.0,
        trade_ref_epoch=ref,
        send=lambda m: sends.append(m) or True,
        state_path=state,
    )
    assert sends == []


def test_not_eligible_under_600s(tmp_path: Path) -> None:
    state = tmp_path / "no_trade_10m_alert.json"
    sends: list[str] = []
    ref = 5_000_000.0
    assert not try_send_no_trade_idle_alert(
        now=ref + 100.0,
        trade_ref_epoch=ref,
        send=lambda m: sends.append(m) or True,
        state_path=state,
    )
    assert sends == []
