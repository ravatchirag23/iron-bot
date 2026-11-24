"""
Adversarial Raw Meat Sanitation Test Suite for Iron Bot
Tests for:
1. Reversal PnL Guardrail (Prevents 25,000 pt spot-as-option ledger corruption)
2. Database WAL & Concurrency Stress (Prevents 'database is locked')
3. Thread Safety & Mutex in PaperTradingEngine
4. NoneType & Malformed Candle Payload Resilience
5. Division by Zero Protection in Wallet Analytics
6. Runner Target Extension Notification Formatting
"""

import unittest
import threading
import time
import os
import sqlite3
from unittest.mock import patch

import config
import database
import paper_engine
import risk_manager
import macro_radar
import option_chain_shield


class TestRawMeatSanitation(unittest.TestCase):
    def setUp(self):
        # Enforce strict test isolation on trades and tracker
        conn = database.get_connection()
        conn.execute("UPDATE trades SET status = 'CLOSED' WHERE status = 'ACTIVE'")
        conn.commit()
        conn.close()
        paper_engine.engine.sync_state()

    def tearDown(self):
        conn = database.get_connection()
        conn.execute("UPDATE trades SET status = 'CLOSED' WHERE status = 'ACTIVE'")
        conn.commit()
        conn.close()
        paper_engine.engine.sync_state()

    def test_spot_as_option_exit_price_interception(self):
        """
        BACTERIA 1 AUDIT:
        When an indicator reversal occurs, callers might accidentally pass the Spot index (e.g. 25,200.0)
        instead of an Option Premium (e.g. 195.0).
        Verify that force_exit intercepts spot (> 5000) and converts it to a realistic option price,
        preventing multi-lakh PnL ledger corruption.
        """
        trade_data = {
            "signal_type": "BUY_CE",
            "symbol": "NIFTY25200CE (TEST_GUARD)",
            "spot_price": 25200.0,
            "entry_price": 180.0,
            "sl_price": 166.0,
            "target_price": 194.0,
            "quantity": 65,
            "is_runner": 0
        }
        trade_id = paper_engine.engine.process_entry(trade_data)
        
        # Accidentally pass Nifty index spot price (25,250.0) into force_exit
        res = paper_engine.engine.force_exit(exit_price=25250.0, reason="UT_BOT_REVERSAL_EXIT")
        
        self.assertEqual(res["status"], "CLOSED")
        
        # Verify from database that exit_price was NOT 25,250.0!
        conn = database.get_connection()
        row = conn.execute("SELECT exit_price, pnl_points, pnl_rupees FROM trades WHERE id = ?", (trade_id,)).fetchone()
        conn.close()
        
        recorded_exit = float(row["exit_price"])
        recorded_pnl_pts = float(row["pnl_points"])
        
        # Exit price must be in realistic option territory (< 1000 pts), NOT index territory (> 20000 pts)
        self.assertLess(recorded_exit, 1000.0)
        self.assertLess(abs(recorded_pnl_pts), 500.0)
        print(f"\n✅ [SANITATION TEST 1 PASSED] Spot 25,250 was safely intercepted and converted to Option Exit ₹{recorded_exit:.2f} (PnL: {recorded_pnl_pts:+.1f} pts)")

    def test_database_wal_mode_and_concurrency_stress(self):
        """
        BACTERIA 2 AUDIT:
        Verify SQLite is running in WAL (Write-Ahead Logging) mode with busy_timeout.
        Simulate 10 concurrent threads simultaneously reading and writing to trades.db without locking errors.
        """
        conn = database.get_connection()
        journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        conn.close()
        self.assertEqual(journal_mode.lower(), "wal")

        errors = []
        def concurrent_writer(thread_id: int):
            try:
                for i in range(15):
                    database.log_event("CONCURRENCY_TEST", f"Thread {thread_id} iteration {i}")
                    stats = database.get_today_stats()
                    _ = stats.get("trade_count", 0)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=concurrent_writer, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Concurrent database writes failed with errors: {errors}")
        print("\n✅ [SANITATION TEST 2 PASSED] 10 concurrent threads executed 150 WAL writes with zero database locks!")

    def test_paper_engine_thread_safety(self):
        """
        BACTERIA 3 AUDIT:
        Verify PaperTradingEngine uses thread locking to prevent race conditions during tick updates and state sync.
        """
        self.assertTrue(hasattr(paper_engine.engine, "lock"))
        self.assertIsInstance(paper_engine.engine.lock, type(threading.RLock()))
        print("\n✅ [SANITATION TEST 3 PASSED] PaperTradingEngine mutex confirmed.")

    def test_resilience_to_malformed_and_none_candle_payloads(self):
        """
        BACTERIA 4 AUDIT:
        Verify risk_manager does not crash with TypeError when candle_high, candle_low, or candle_close are None.
        """
        malformed_signal = {
            "signal": "BUY_CE",
            "spot": 25200.0,
            "candle_high": None,
            "candle_low": None,
            "candle_close": None,
            "candle_range": None,
            "is_test": True
        }
        
        # Must not raise TypeError
        is_approved, reason = risk_manager.validate_signal_armor(malformed_signal)
        self.assertIsInstance(is_approved, bool)
        print("\n✅ [SANITATION TEST 4 PASSED] Malformed NoneType candle payload handled safely without exceptions.")

    def test_division_by_zero_guards_in_wallet_analytics(self):
        """
        BACTERIA 5 AUDIT:
        Verify get_wallet_stats() does not crash with ZeroDivisionError if initial_capital or duration_days is 0.
        """
        with patch.object(config, "INITIAL_WALLET_CAPITAL", 0.0), \
             patch.object(config, "CAMPAIGN_DURATION_DAYS", 0):
            stats = database.get_wallet_stats()
            self.assertIn("current_balance", stats)
            self.assertIn("roi_pct", stats)
            self.assertIn("timeline_pct", stats)
        print("\n✅ [SANITATION TEST 5 PASSED] Wallet metrics division-by-zero successfully guarded.")

    def test_runner_target_extension_reporting_precision(self):
        """
        BACTERIA 6 AUDIT:
        Verify that runner target extension notifies with the previous target hit and the new target.
        """
        trade_data = {
            "signal_type": "BUY_CE",
            "symbol": "NIFTY25200CE (RUNNER_TEST)",
            "spot_price": 25200.0,
            "entry_price": 200.0,
            "sl_price": 186.0,
            "target_price": 214.0,
            "quantity": 65,
            "is_runner": 1
        }
        _ = paper_engine.engine.process_entry(trade_data)
        
        # When price reaches 214.0, target extends by +10.0 to 224.0
        with patch("telegram_notifier.notify_trailing_update") as mock_tg:
            res = paper_engine.engine.update_tick(214.0)
            self.assertEqual(res["status"], "ACTIVE")
            self.assertTrue(mock_tg.called)
            args, _ = mock_tg.call_args
            tag_msg = args[1]
            self.assertIn("Hit ₹214.00", tag_msg)
            self.assertIn("extended to ₹224.00", tag_msg)
        print(f"\n✅ [SANITATION TEST 6 PASSED] Runner extension report accurately logs previous hit target and new target: '{tag_msg}'")


if __name__ == "__main__":
    unittest.main()
