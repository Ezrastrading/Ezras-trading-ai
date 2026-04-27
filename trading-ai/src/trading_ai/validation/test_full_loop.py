"""Test full loop: scan, trade, exit, rebuy, memory validation."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def test_execution_lifecycle():
    """Test execution lifecycle tracking."""
    from trading_ai.shark.execution_lifecycle import (
        ExecutionStage,
        log_execution_stage,
        reset_trade_lifecycle,
        get_current_execution_stage,
    )
    
    trade_id = "test_trade_001"
    
    # Test lifecycle progression
    log_execution_stage(trade_id, ExecutionStage.SCAN, {"market": "KXBTC"})
    assert get_current_execution_stage(trade_id) == ExecutionStage.SCAN
    
    log_execution_stage(trade_id, ExecutionStage.INTENT, {"confidence": 0.9})
    assert get_current_execution_stage(trade_id) == ExecutionStage.INTENT
    
    log_execution_stage(trade_id, ExecutionStage.BUY, {"price": 0.85, "size": 10})
    assert get_current_execution_stage(trade_id) == ExecutionStage.BUY
    
    log_execution_stage(trade_id, ExecutionStage.HOLD, {"hold_time": 60})
    assert get_current_execution_stage(trade_id) == ExecutionStage.HOLD
    
    log_execution_stage(trade_id, ExecutionStage.EXIT, {"pnl": 5.0})
    assert get_current_execution_stage(trade_id) == ExecutionStage.EXIT
    
    # Test rebuy
    reset_trade_lifecycle(trade_id)
    log_execution_stage(trade_id, ExecutionStage.REBUY, {"new_entry": 0.88})
    assert get_current_execution_stage(trade_id) == ExecutionStage.REBUY
    
    logger.info("✓ Execution lifecycle test passed")
    return True


def test_trade_tracker():
    """Test trade tracking and memory writes."""
    from trading_ai.shark.trade_tracker import (
        TradeRecord,
        record_trade,
        load_trades,
        get_win_rate,
        get_total_pnl,
    )
    
    # Create test trade
    trade = TradeRecord(
        market_id="KXBTC-78K-T-25JUN24",
        entry_price=0.85,
        exit_price=0.90,
        size=10.0,
        fees=0.50,
        slippage=0.01,
        pnl_gross=5.0,
        pnl_net=4.49,
        hold_time=300.0,
        outcome="win",
        reason_for_entry="high_probability_edge",
        reason_for_exit="take_profit",
        timestamp=time.time(),
        outlet="kalshi",
        side="yes",
        edge_percent=0.05,
        confidence_score=0.9,
        liquidity_score=0.8,
        fill_rate=0.95,
    )
    
    record_trade(trade)
    
    # Load and verify
    trades = load_trades(limit=1)
    assert len(trades) == 1
    assert trades[0].market_id == trade.market_id
    assert trades[0].outcome == "win"
    
    # Test analytics
    assert get_win_rate([trade]) == 1.0
    assert get_total_pnl([trade]) == 4.49
    
    logger.info("✓ Trade tracker test passed")
    return True


def test_edge_learning():
    """Test edge learning system."""
    from trading_ai.shark.edge_learning import (
        EdgeMetrics,
        update_edge_registry,
        add_trade_pattern,
        get_win_rate_by_edge_range,
        get_best_edge,
    )
    
    market_id = "KXBTC-78K-T-25JUN24"
    edge_metrics = EdgeMetrics(
        edge_percent=0.05,
        confidence_score=0.9,
        liquidity_score=0.8,
        fill_rate=0.95,
    )
    
    update_edge_registry(market_id, edge_metrics)
    add_trade_pattern(edge_metrics, "win", 4.49, "crypto")
    
    # Verify edge registry
    best_edge = get_best_edge()
    assert best_edge is not None
    assert best_edge.edge_percent == 0.05
    
    # Test win rate by edge range
    win_rate = get_win_rate_by_edge_range(0.04, 0.06)
    assert win_rate == 1.0
    
    logger.info("✓ Edge learning test passed")
    return True


def test_safety_layer():
    """Test safety layer."""
    from trading_ai.shark.safety_layer import (
        set_daily_start_capital,
        update_position_count,
        record_trade_pnl,
        check_max_positions,
        check_max_per_trade,
        check_daily_loss_cap,
    )
    
    set_daily_start_capital(100.0)
    update_position_count(2)
    
    # Test max positions
    assert check_max_positions(1) == True  # 2 + 1 = 3, at limit
    assert check_max_positions(2) == False  # 2 + 2 = 4, exceeds limit
    
    # Test max per trade
    assert check_max_per_trade(10.0, 100.0) == True  # 10% of capital
    assert check_max_per_trade(20.0, 100.0) == False  # 20% exceeds 15% cap
    
    # Test daily loss cap
    record_trade_pnl(-5.0)  # 5% loss, under 10% cap
    assert check_daily_loss_cap() == True
    
    record_trade_pnl(-15.0)  # Total 20% loss, exceeds 10% cap
    assert check_daily_loss_cap() == False
    
    logger.info("✓ Safety layer test passed")
    return True


def test_memory_directory():
    """Test memory directory creation and writes."""
    memory_dir = Path(os.environ.get("EZRAS_RUNTIME_ROOT", "/app/ezras-runtime")) / "shark/memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    
    assert memory_dir.exists()
    
    # Test file write
    test_file = memory_dir / "test_write.txt"
    test_file.write_text("test")
    assert test_file.exists()
    assert test_file.read_text() == "test"
    
    # Cleanup
    test_file.unlink()
    
    logger.info("✓ Memory directory test passed")
    return True


def run_all_tests():
    """Run all full loop tests."""
    logger.info("=" * 60)
    logger.info("FULL LOOP VALIDATION TESTS")
    logger.info("=" * 60)
    
    tests = [
        test_execution_lifecycle,
        test_trade_tracker,
        test_edge_learning,
        test_safety_layer,
        test_memory_directory,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            if test():
                passed += 1
        except Exception as exc:
            logger.error(f"✗ {test.__name__} failed: {exc}")
            failed += 1
    
    logger.info("=" * 60)
    logger.info(f"TEST RESULTS: {passed} passed, {failed} failed")
    logger.info("=" * 60)
    
    return failed == 0


if __name__ == "__main__":
    import sys
    sys.exit(0 if run_all_tests() else 1)
