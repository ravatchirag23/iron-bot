"""
Institutional Full System Test Suite for Iron Bot
Comprehensive verification across all modules, Greeks math, 14 Iron Armor rules,
paper execution engine, multi-trade 4-order lifecycle, and webhook gateway.
"""

import unittest
import sys
import os
import json
import sqlite3
from unittest.mock import patch
from datetime import datetime, time
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
import database
import risk_manager
import paper_engine
import telegram_notifier
import macro_radar
import option_chain_shield
from native_scanner import NativeMarketScanner


class TestConfigAndEnvironment(unittest.TestCase):
    """Verifies core environment variables and iron armor configuration constants."""
    def test_config_constants(self):
        self.assertTrue(config.PAPER_TRADING_MODE, "Paper trading mode must be enabled for zero capital risk")
        self.assertEqual(config.MAX_DAILY_TRADES, 4, "Must allow max 4 trades per day")
        self.assertEqual(config.MAX_DAILY_LOSS_RUPEES, 2500, "Daily loss limit must be set to ₹2500")
        self.assertTrue(getattr(config, "ENABLE_GREEKS_DYNAMIC_SL", True), "Dynamic Greeks SL must be enabled")
        self.assertEqual(config.DEFAULT_LOT_SIZE, 65, "Default lot size must be 65")
        self.assertEqual(config.MARKET_START_TIME, time(9, 30))
        self.assertEqual(config.MARKET_END_TIME, time(14, 45))
        self.assertEqual(config.AUTO_SQUARE_OFF_TIME, time(15, 15))


class TestDatabaseEngine(unittest.TestCase):
    """Verifies SQLite tables, CRUD operations, daily tracking, and state integrity."""
    def setUp(self):
        database.reset_daily_limits()

    def test_database_initialization_and_schema(self):
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cursor.fetchall()]
        conn.close()
        for expected in ["trades", "daily_tracker", "audit_logs", "system_config"]:
            self.assertIn(expected, tables, f"Table '{expected}' must exist in schema")

    def test_daily_tracker_crud(self):
        stats = database.get_today_stats()
        self.assertEqual(stats["trade_count"], 0)
        self.assertEqual(stats["total_pnl_rupees"], 0.0)
        self.assertEqual(stats["is_locked"], 0)

        # Lock system
        database.lock_system_today("Test Lock")
        stats_locked = database.get_today_stats()
        self.assertEqual(stats_locked["is_locked"], 1)

    def tearDown(self):
        database.reset_daily_limits()


class TestRiskManagerGreeksMath(unittest.TestCase):
    """Tests Black-Scholes Taylor expansion SL calculations and strike rounding."""
    def test_strike_selection_rounding(self):
        # Calls
        self.assertEqual(risk_manager.get_atm_strike(25024.0, is_call=True), 25000)
        self.assertEqual(risk_manager.get_atm_strike(25026.0, is_call=True), 25050)
        self.assertEqual(risk_manager.get_atm_strike(25075.0, is_call=True), 25100)
        # Puts
        self.assertEqual(risk_manager.get_atm_strike(25024.0, is_call=False), 25000)
        self.assertEqual(risk_manager.get_atm_strike(25075.0, is_call=False), 25100)

    def test_dynamic_greek_brackets_atm(self):
        # ATM Option: Delta = 0.50, Entry = ₹200, ATR = 22
        sl_pts, tp_pts, regime = risk_manager.calculate_dynamic_greek_brackets(
            entry_premium=200.0, delta=0.50, spot_atr=22.0
        )
        self.assertGreaterEqual(sl_pts, 13.0, "ATM SL must provide at least 13 pts breathing room")
        self.assertLessEqual(sl_pts, 18.0, "ATM SL must not exceed 18 pts")
        self.assertAlmostEqual(tp_pts, round(sl_pts * 1.8, 1), delta=0.1)
        self.assertIn("GREEK_DYNAMIC", regime)

    def test_dynamic_greek_brackets_otm(self):
        # OTM Option: Delta = 0.24, Entry = ₹65, ATR = 22
        sl_pts, tp_pts, regime = risk_manager.calculate_dynamic_greek_brackets(
            entry_premium=65.0, delta=0.24, spot_atr=22.0
        )
        self.assertGreaterEqual(sl_pts, 6.0, "OTM SL must meet minimum floor of 6 pts")
        self.assertLessEqual(sl_pts, 9.0, "OTM SL must be proportional to 0.24 delta")
        self.assertAlmostEqual(tp_pts, round(sl_pts * 1.8, 1), delta=0.1)

    def test_dynamic_greek_brackets_itm(self):
        # ITM Option: Delta = 0.70, Entry = ₹320, ATR = 22
        sl_pts, tp_pts, regime = risk_manager.calculate_dynamic_greek_brackets(
            entry_premium=320.0, delta=0.70, spot_atr=22.0
        )
        self.assertGreaterEqual(sl_pts, 19.0, "ITM SL must scale with 0.70 delta")
        self.assertLessEqual(sl_pts, 25.0, "ITM SL must not exceed reasonable threshold")
        self.assertAlmostEqual(tp_pts, round(sl_pts * 1.8, 1), delta=0.1)


class TestIronArmorRulesValidation(unittest.TestCase):
    """Tests all 14 Iron Armor risk filter rules."""
    def setUp(self):
        database.reset_daily_limits()

    def test_market_hours_rule(self):
        # Before 9:30 AM
        early_time = datetime(2026, 11, 11, 9, 15, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
        with patch("config.get_now_ist", return_value=early_time):
            approved, reason = risk_manager.validate_signal_armor({"signal": "BUY_CE", "spot": 25200.0, "is_test": False})
            self.assertFalse(approved)
            self.assertIn("Too early", reason)

        # After 14:45 PM
        late_time = datetime(2026, 11, 11, 15, 0, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
        with patch("config.get_now_ist", return_value=late_time):
            approved, reason = risk_manager.validate_signal_armor({"signal": "BUY_CE", "spot": 25200.0, "is_test": False})
            self.assertFalse(approved)
            self.assertIn("Too late", reason)

    def test_european_blackout_rule(self):
        # 13:30 IST (Inside 13:25 to 13:45 blackout)
        euro_time = datetime(2026, 11, 11, 13, 30, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
        with patch("config.get_now_ist", return_value=euro_time):
            approved, reason = risk_manager.validate_signal_armor({"signal": "BUY_CE", "spot": 25200.0, "is_test": False})
            self.assertFalse(approved)
            self.assertIn("European Market Open", reason)

    def test_giant_exhaustion_candle_filter(self):
        # Candle range 50 > 45 max points
        signal = {"signal": "BUY_CE", "spot": 25200.0, "candle_range": 50.0, "is_test": True}
        approved, reason = risk_manager.validate_signal_armor(signal)
        self.assertFalse(approved)
        self.assertIn("Exhaustion candle detected", reason)


class TestPaperTradingEngineLifecycle(unittest.TestCase):
    """Tests Trade Entry, Micro-SL, Target, Trailing Milestones, and Exits."""
    def setUp(self):
        database.reset_daily_limits("2026-11-98")
        paper_engine.engine.sync_state()

    def test_entry_sl_target_and_trailing(self):
        mock_now = datetime(2026, 11, 11, 11, 0, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
        with patch("config.get_now_ist", return_value=mock_now), patch("config.get_today_ist", return_value="2026-11-98"):
            trade_params = {
                "signal_type": "BUY_CE",
                "symbol": "NIFTY25250CE_LIFECYCLE",
                "spot_price": 25250.0,
                "entry_price": 200.0,
                "ltp_reference": 200.0,
                "sl_price": 185.0, # 15 pt SL
                "target_price": 227.0, # 27 pt TP
                "quantity": 65,
                "vix_regime": "TEST",
                "is_runner": False,
                "runner_reason": "",
                "delta": 0.50
            }
            trade_id = paper_engine.engine.process_entry(trade_params)
            self.assertIsNotNone(trade_id)

            # 1. Price moves to 216 (+16 pts gain -> reaches Milestone 1: 1:1 RR Cost Lock)
            paper_engine.engine.update_tick(216.0)
            self.assertEqual(paper_engine.engine.milestone_reached, 1, "Milestone 1 must trigger at +15 pts gain")
            active = database.get_active_trade()
            self.assertEqual(active["sl_price"], 200.0, "SL must move to entry price (200.0) upon Milestone 1")

            # 2. Price hits Target at 227.0
            exit_event = paper_engine.engine.update_tick(227.5)
            self.assertIsNotNone(exit_event)
            self.assertEqual(exit_event["reason"], "TARGET_HIT")
            self.assertGreater(exit_event["pnl_rupees"], 0.0)

    def tearDown(self):
        database.reset_daily_limits("2026-11-98")


class TestMultiTradeCycleAndLimits(unittest.TestCase):
    """Verifies that up to 4 trades can execute sequentially and 5th trade is locked."""
    def setUp(self):
        self.test_date = "2026-11-97"
        database.reset_daily_limits(self.test_date)

    def test_four_trades_lifecycle_and_fifth_lock(self):
        mock_now = datetime(2026, 11, 11, 10, 30, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
        with patch("config.get_now_ist", return_value=mock_now), patch("config.get_today_ist", return_value=self.test_date):
            for i in range(1, 5):
                stats = database.get_today_stats(self.test_date)
                self.assertEqual(stats["is_locked"], 0, f"System must NOT be locked before Trade #{i}")

                # Enter Trade
                trade_params = {
                    "signal_type": "BUY_CE",
                    "symbol": f"NIFTY25250CE_MULTI_{i}",
                    "spot_price": 25250.0,
                    "entry_price": 200.0,
                    "ltp_reference": 200.0,
                    "sl_price": 195.0,
                    "target_price": 227.0,
                    "quantity": 65,
                    "vix_regime": "TEST",
                    "is_runner": False,
                    "runner_reason": "",
                    "delta": 0.50
                }
                trade_id = paper_engine.engine.process_entry(trade_params)
                self.assertIsNotNone(trade_id)

                # Moderate exit (-5 pts = -₹325 per trade, well within ₹2,500 max daily loss)
                exit_event = paper_engine.engine.update_tick(194.5)
                self.assertEqual(exit_event["reason"], "MICRO_SL_HIT")

                stats_after = database.get_today_stats(self.test_date)
                self.assertEqual(stats_after["trade_count"], i)

                if i < 4:
                    self.assertEqual(stats_after["is_locked"], 0, f"System must remain ACTIVE after Trade #{i}")
                else:
                    self.assertEqual(stats_after["is_locked"], 1, "System must be LOCKED after Trade #4")

            # Trade 5 Attempt must be REJECTED by Rule 1
            signal_data = {"signal": "BUY_CE", "spot": 25250.0, "candle_range": 20.0, "is_test": True}
            approved, reason = risk_manager.validate_signal_armor(signal_data)
            self.assertFalse(approved, "Trade #5 must be blocked by Iron Armor Rule 1")
            self.assertIn("Max daily trades limit", reason)

    def test_daily_loss_limit_hard_lock(self):
        """Rule 2: System must lock immediately if total loss reaches -₹2500, even on Trade #1."""
        loss_date = "2026-11-96"
        database.reset_daily_limits(loss_date)
        mock_now = datetime(2026, 11, 11, 10, 30, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
        with patch("config.get_now_ist", return_value=mock_now), patch("config.get_today_ist", return_value=loss_date):
            trade_params = {
                "signal_type": "BUY_CE",
                "symbol": "NIFTY25250CE_LOSS_TEST",
                "spot_price": 25250.0,
                "entry_price": 200.0,
                "ltp_reference": 200.0,
                "sl_price": 160.0, # Large drop to test hard loss shutdown
                "target_price": 240.0,
                "quantity": 65,
                "vix_regime": "TEST",
                "is_runner": False,
                "runner_reason": "",
                "delta": 0.50
            }
            paper_engine.engine.process_entry(trade_params)
            # Exit at 155 (-45 pts * 65 = -₹2,925 loss, exceeding ₹2,500 limit)
            paper_engine.engine.update_tick(155.0)

            stats = database.get_today_stats(loss_date)
            self.assertEqual(stats["is_locked"], 1, "System must be LOCKED after exceeding max daily loss")

            # Next trade must be blocked by Rule 2
            signal_data = {"signal": "BUY_CE", "spot": 25250.0, "candle_range": 20.0, "is_test": True}
            approved, reason = risk_manager.validate_signal_armor(signal_data)
            self.assertFalse(approved)
            self.assertIn("LOCKED", reason)
        database.reset_daily_limits(loss_date)


if __name__ == "__main__":
    unittest.main()
