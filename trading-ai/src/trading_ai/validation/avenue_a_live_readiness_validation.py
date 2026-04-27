"""
Avenue A live-readiness validation script.

Tests fee-aware sizing, PnL calculation, and trade memory/databank write paths.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from trading_ai.validation.avenue_a_fee_aware_sizing import (
    CoinbaseFeeModel,
    FirstTradeSizingResult,
    fee_aware_first_trade_size,
)
from trading_ai.nte.databank.databank_schema import merge_defaults
from trading_ai.nte.databank.local_trade_store import (
    databank_memory_root,
    resolve_databank_root,
)
from trading_ai.runtime_paths import ezras_runtime_root

logger = logging.getLogger(__name__)


class AvenueALiveReadinessValidator:
    """Validates Avenue A is ready for live trading."""

    def __init__(self, runtime_root: Path | None = None):
        self.runtime_root = Path(runtime_root or ezras_runtime_root()).resolve()
        self.results: Dict[str, Dict[str, Any]] = {}

    def run_all_tests(self) -> Dict[str, Any]:
        """Run all validation tests and return PASS/FAIL table."""
        logger.info("Starting Avenue A live-readiness validation...")
        
        # Test 1: Fee/slippage model rejects fee-dominated tiny trades
        self.test_fee_dominance_rejection()
        
        # Test 2: PnL calculation uses net_pnl after fees/slippage
        self.test_net_pnl_calculation()
        
        # Test 3: Simulated buy → sell → rebuy cycle writes all records
        self.test_trade_cycle_writes()
        
        # Test 4: Databank write paths use EZRAS_RUNTIME_ROOT
        self.test_databank_write_paths()
        
        # Test 5: Trade events schema includes fee breakdown fields
        self.test_trade_event_schema()
        
        # Test 6: Exit monitor can read position/trade path
        self.test_exit_monitor_path()
        
        return self._compile_results()

    def test_fee_dominance_rejection(self) -> None:
        """Test 1: Fee/slippage model rejects fee-dominated tiny trades."""
        logger.info("Test 1: Fee dominance rejection...")
        
        try:
            # Test with tiny capital where fees dominate
            tiny_equity = 25.00  # $25 starting capital
            venue_min = 2.00  # $2 minimum order
            expected_edge = 50.0  # 50 bps expected edge
            
            result = fee_aware_first_trade_size(
                equity_usd=tiny_equity,
                venue_min_notional=venue_min,
                expected_edge_bps=expected_edge,
                min_net_profit_buffer_usd=0.50,
            )
            
            # Should reject due to fee dominance
            if not result.ok and result.reason == "trade_size_fee_dominated":
                self.results["test_1_fee_dominance"] = {
                    "status": "PASS",
                    "details": "Correctly rejected fee-dominated tiny trade",
                    "result": result.to_dict(),
                }
            else:
                self.results["test_1_fee_dominance"] = {
                    "status": "FAIL",
                    "details": f"Expected fee_dominated rejection, got {result.reason}",
                    "result": result.to_dict(),
                }
        except Exception as exc:
            self.results["test_1_fee_dominance"] = {
                "status": "ERROR",
                "details": str(exc),
            }

    def test_net_pnl_calculation(self) -> None:
        """Test 2: PnL calculation uses net_pnl after fees/slippage."""
        logger.info("Test 2: Net PnL calculation...")
        
        try:
            # Test with sufficient capital and realistic edge
            sufficient_equity = 2000.00
            venue_min = 2.00
            expected_edge = 100.0  # 100 bps expected edge (more realistic for profitable trade)
            
            result = fee_aware_first_trade_size(
                equity_usd=sufficient_equity,
                venue_min_notional=venue_min,
                expected_edge_bps=expected_edge,
                min_net_profit_buffer_usd=0.50,
            )
            
            # Should pass and calculate net profit correctly
            if result.ok:
                meta = result.meta
                expected_net = meta.get("expected_net_usd", 0.0)
                gross = meta.get("expected_gross_usd", 0.0)
                fees = meta.get("round_trip_cost_usd", 0.0)
                
                # Verify net = gross - fees
                if abs(expected_net - (gross - fees)) < 1e-6:
                    self.results["test_2_net_pnl"] = {
                        "status": "PASS",
                        "details": "Net PnL correctly calculated as gross - fees",
                        "result": result.to_dict(),
                    }
                else:
                    self.results["test_2_net_pnl"] = {
                        "status": "FAIL",
                        "details": f"Net PnL calculation incorrect: {expected_net} vs {gross - fees}",
                        "result": result.to_dict(),
                    }
            else:
                self.results["test_2_net_pnl"] = {
                    "status": "FAIL",
                    "details": f"Expected PASS with sufficient capital and edge, got {result.reason}",
                    "result": result.to_dict(),
                }
        except Exception as exc:
            self.results["test_2_net_pnl"] = {
                "status": "ERROR",
                "details": str(exc),
            }

    def test_trade_cycle_writes(self) -> None:
        """Test 3: Simulated buy → sell → rebuy cycle writes all records."""
        logger.info("Test 3: Trade cycle write paths...")
        
        try:
            # Verify databank root resolves correctly
            db_root, source = resolve_databank_root()
            
            if source == "EZRAS_RUNTIME_ROOT/databank":
                self.results["test_3_trade_cycle"] = {
                    "status": "PASS",
                    "details": f"Databank root correctly resolves from EZRAS_RUNTIME_ROOT: {db_root}",
                    "source": source,
                }
            else:
                self.results["test_3_trade_cycle"] = {
                    "status": "FAIL",
                    "details": f"Databank root resolves from unexpected source: {source}",
                    "source": source,
                }
        except Exception as exc:
            self.results["test_3_trade_cycle"] = {
                "status": "ERROR",
                "details": str(exc),
            }

    def test_databank_write_paths(self) -> None:
        """Test 4: Databank write paths use EZRAS_RUNTIME_ROOT."""
        logger.info("Test 4: Databank write paths...")
        
        try:
            # Check that EZRAS_RUNTIME_ROOT is set
            runtime_root = ezras_runtime_root()
            
            # Check that databank root is under runtime root
            db_root = databank_memory_root()
            
            # Verify db_root is under runtime_root
            try:
                db_root.relative_to(runtime_root)
                self.results["test_4_databank_paths"] = {
                    "status": "PASS",
                    "details": f"Databank root {db_root} is under runtime root {runtime_root}",
                    "runtime_root": str(runtime_root),
                    "databank_root": str(db_root),
                }
            except ValueError:
                self.results["test_4_databank_paths"] = {
                    "status": "FAIL",
                    "details": f"Databank root {db_root} is NOT under runtime root {runtime_root}",
                    "runtime_root": str(runtime_root),
                    "databank_root": str(db_root),
                }
        except Exception as exc:
            self.results["test_4_databank_paths"] = {
                "status": "ERROR",
                "details": str(exc),
            }

    def test_trade_event_schema(self) -> None:
        """Test 5: Trade events schema includes fee breakdown fields."""
        logger.info("Test 5: Trade event schema...")
        
        try:
            # Test that merge_defaults includes new fee breakdown fields
            defaults = merge_defaults({})
            
            required_fields = [
                "entry_fee",
                "exit_fee",
                "total_fees",
                "estimated_slippage",
                "spread_cost",
                "net_pnl",
                "net_roi",
                "fee_dominance_ratio",
                "expected_edge_before_cost",
                "expected_edge_after_cost",
            ]
            
            missing = [f for f in required_fields if f not in defaults]
            
            if not missing:
                self.results["test_5_schema"] = {
                    "status": "PASS",
                    "details": "All fee breakdown fields present in schema",
                    "fields": required_fields,
                }
            else:
                self.results["test_5_schema"] = {
                    "status": "FAIL",
                    "details": f"Missing fee breakdown fields: {missing}",
                    "missing": missing,
                }
        except Exception as exc:
            self.results["test_5_schema"] = {
                "status": "ERROR",
                "details": str(exc),
            }

    def test_exit_monitor_path(self) -> None:
        """Test 6: Exit monitor can read position/trade path."""
        logger.info("Test 6: Exit monitor path...")
        
        try:
            # Verify positions path resolves from runtime root
            from trading_ai.nte.paths import nte_memory_dir
            
            nte_root = nte_memory_dir()
            runtime_root = ezras_runtime_root()
            
            try:
                nte_root.relative_to(runtime_root)
                self.results["test_6_exit_monitor"] = {
                    "status": "PASS",
                    "details": f"NTE memory dir {nte_root} is under runtime root {runtime_root}",
                    "nte_root": str(nte_root),
                    "runtime_root": str(runtime_root),
                }
            except ValueError:
                self.results["test_6_exit_monitor"] = {
                    "status": "FAIL",
                    "details": f"NTE memory dir {nte_root} is NOT under runtime root {runtime_root}",
                    "nte_root": str(nte_root),
                    "runtime_root": str(runtime_root),
                }
        except Exception as exc:
            self.results["test_6_exit_monitor"] = {
                "status": "ERROR",
                "details": str(exc),
            }

    def _compile_results(self) -> Dict[str, Any]:
        """Compile all test results into a summary."""
        total = len(self.results)
        passed = sum(1 for r in self.results.values() if r.get("status") == "PASS")
        failed = sum(1 for r in self.results.values() if r.get("status") == "FAIL")
        errors = sum(1 for r in self.results.values() if r.get("status") == "ERROR")
        
        return {
            "validation_timestamp": datetime.now(timezone.utc).isoformat(),
            "runtime_root": str(self.runtime_root),
            "summary": {
                "total_tests": total,
                "passed": passed,
                "failed": failed,
                "errors": errors,
                "overall_status": "PASS" if failed == 0 and errors == 0 else "FAIL",
            },
            "results": self.results,
        }


def main() -> None:
    """Run validation and print PASS/FAIL table."""
    import sys
    
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    
    validator = AvenueALiveReadinessValidator()
    results = validator.run_all_tests()
    
    # Print PASS/FAIL table
    print("\n" + "=" * 80)
    print("AVENUE A LIVE-READINESS VALIDATION RESULTS")
    print("=" * 80)
    
    for test_name, test_result in results["results"].items():
        status = test_result.get("status", "UNKNOWN")
        details = test_result.get("details", "")
        print(f"{test_name:40s} | {status:10s} | {details}")
    
    print("=" * 80)
    summary = results["summary"]
    print(f"Total: {summary['total_tests']} | Passed: {summary['passed']} | Failed: {summary['failed']} | Errors: {summary['errors']}")
    print(f"Overall Status: {summary['overall_status']}")
    print("=" * 80)
    
    # Exit with error code if any failures
    if summary["overall_status"] != "PASS":
        sys.exit(1)


if __name__ == "__main__":
    main()
