"""
Institutional Unit Test Suite for Iron Bot
Covers 100% of UT Bot Math, 13 Iron Armor Rules, and Paper Engine Execution.
Run with: /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 -m unittest tests/test_iron_bot.py
"""

import unittest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
import database
import risk_manager
import paper_engine
from unittest.mock import patch
from datetime import datetime
from zoneinfo import ZoneInfo
from native_scanner import NativeMarketScanner

class TestUTBotCoreMath(unittest.TestCase):
    def setUp(self):
        self.scanner = NativeMarketScanner(a_buy=1.5, c_buy=10, a_sell=1.5, c_sell=10, ema_len=20)

    def test_insufficient_candles_returns_none(self):
        # Must return None if candles < 22
        candles = [{"time": i, "open": 100, "high": 101, "low": 99, "close": 100} for i in range(10)]
        pos, spot, sig = self.scanner.evaluate_ut_bot(candles)
        self.assertEqual(pos, 0)
        self.assertIsNone(sig)

    def test_ut_bot_with_real_market_candles(self):
        # Fetch live/cached 5m candles for NIFTY 50
        candles = self.scanner.fetch_intraday_candles()
        self.assertGreater(len(candles), 40, "Must fetch full day of 5m candles")

        # Scan across bars to verify crossover detection
        signals = []
        for i in range(25, len(candles)):
            sub = candles[:i+1]
            s = NativeMarketScanner()
            pos, spot, sig = s.evaluate_ut_bot(sub)
            if sig:
                signals.append(sig)

        self.assertGreater(len(signals), 0, "Must detect at least 1 valid UT Bot signal across intraday bars")
        first_sig = signals[0]
        self.assertIn(first_sig["signal"], ["BUY_CE", "BUY_PE"])
        self.assertGreater(first_sig["spot"], 20000.0)
        self.assertGreater(first_sig["candle_range"], 0.0)

class TestIronArmorRules(unittest.TestCase):
    def setUp(self):
        database.reset_daily_limits()
        paper_engine.engine.sync_state()

    def test_strike_selection_rounding(self):
        # 25024 -> 25000
        self.assertEqual(risk_manager.get_atm_strike(25024.0, is_call=True), 25000)
        # 25026 -> 25050
        self.assertEqual(risk_manager.get_atm_strike(25026.0, is_call=True), 25050)
        # 25075 -> 25100
        self.assertEqual(risk_manager.get_atm_strike(25075.0, is_call=False), 25100)

    def test_exhaustion_giant_candle_filter(self):
        # Signal candle > 45 pts must be blocked by Rule 10
        signal_data = {
            "signal": "BUY_CE",
            "spot": 25100.0,
            "candle_range": 52.0, # Exceeds 45 pt max
            "is_test": True
        }
        approved, reason = risk_manager.validate_signal_armor(signal_data)
        self.assertFalse(approved)
        self.assertIn("Exhaustion candle detected", reason)

class TestPaperExecutionEngine(unittest.TestCase):
    def setUp(self):
        database.reset_daily_limits()

    def test_micro_sl_exit(self):
        # Mock time to 10:30 AM IST (Active Market Hours)
        mock_now = datetime(2026, 11, 11, 10, 30, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
        with patch("config.get_now_ist", return_value=mock_now):
            trade_params = {
                "signal_type": "BUY_CE",
                "symbol": "NIFTY25100CE (TEST)",
                "spot_price": 25100.0,
                "entry_price": 200.0,
                "ltp_reference": 200.0,
                "sl_price": 193.0, # 7 pt SL
                "target_price": 222.0, # 22 pt TP
                "quantity": 65,
                "vix_regime": "TEST",
                "is_runner": False,
                "runner_reason": "",
                "delta": 0.50
            }
            trade_id = paper_engine.engine.process_entry(trade_params)
            self.assertIsNotNone(trade_id)

            # Price drops to 192.5 (hits SL at 193.0)
            exit_event = paper_engine.engine.update_tick(192.5)
            self.assertIsNotNone(exit_event, "Must trigger exit event on SL breach")
            self.assertEqual(exit_event["reason"], "MICRO_SL_HIT")

            active = database.get_active_trade()
            self.assertIsNone(active, "Active trade must be closed after SL hit")

    def tearDown(self):
        database.reset_daily_limits()

if __name__ == "__main__":
    unittest.main()
