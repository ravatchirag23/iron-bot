"""
Unit Tests for ₹50,000 Capital Cap & 30-Day ROI Campaign Engine
Verifies strict wallet limits, margin locking, circuit breaker, and ROI tracking.
"""

import unittest
import sys
import os

# Add root project dir to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import tempfile
os.environ["IRON_BOT_TEST_MODE"] = "1"
if "IRON_BOT_DB_PATH" not in os.environ:
    os.environ["IRON_BOT_DB_PATH"] = os.path.join(tempfile.gettempdir(), "iron_bot_unit_tests.db")

import config
import database
import risk_manager

class TestWalletCampaign(unittest.TestCase):
    TEST_START_DATE = "2099-01-01"

    def setUp(self):
        database.init_db()
        database.reset_daily_limits()
        conn = database.get_connection()
        conn.cursor().execute("DELETE FROM trades WHERE symbol LIKE '%WALLET%' OR symbol = 'NIFTY25200CE' OR trade_date >= '2099-01-01'")
        conn.cursor().execute(f"DELETE FROM daily_tracker WHERE trade_date = '{self.TEST_START_DATE}'")
        conn.commit()
        conn.close()
        # Initialize campaign with 50,000 cap on isolated test date
        database.init_campaign_wallet(initial_capital=50000.0, start_date=self.TEST_START_DATE)

    def tearDown(self):
        conn = database.get_connection()
        conn.cursor().execute("DELETE FROM trades WHERE symbol LIKE '%WALLET%' OR symbol = 'NIFTY25200CE' OR trade_date >= '2099-01-01'")
        conn.cursor().execute(f"DELETE FROM daily_tracker WHERE trade_date = '{self.TEST_START_DATE}'")
        conn.commit()
        conn.close()
        # Restore actual campaign start date
        database.init_campaign_wallet(initial_capital=config.INITIAL_WALLET_CAPITAL, start_date=config.CAMPAIGN_START_DATE)
        database.reset_daily_limits()

    def test_initial_wallet_baseline(self):
        """Verify wallet starts with exact ₹50,000 cap and 0.0% ROI."""
        w = database.get_wallet_stats()
        self.assertEqual(w["initial_capital"], 50000.0)
        self.assertEqual(w["current_balance"], 50000.0)
        self.assertEqual(w["roi_pct"], 0.0)
        self.assertEqual(w["available_margin"], 50000.0)
        self.assertEqual(w["margin_used"], 0.0)
        self.assertEqual(w["preservation_floor"], 35000.0)
        self.assertEqual(w["total_days"], 30)
        self.assertFalse(w["is_preservation_triggered"])

    def test_margin_exceeded_rejection(self):
        """Verify order is rejected if required margin exceeds available wallet balance."""
        # Signal requesting 65 qty @ 800 premium = ₹52,000 required margin (> ₹50,000 wallet)
        signal = {
            "signal": "BUY_CE",
            "spot": 25100.0,
            "entry_price": 850.0, # 850 * 65 = ₹55,250
            "quantity": 65,
            "is_test": True
        }
        approved, reason = risk_manager.validate_signal_armor(signal)
        self.assertFalse(approved)
        self.assertIn("Insufficient Wallet Capital", reason)

    def test_position_sizing_cap(self):
        """Verify order is rejected if required margin exceeds 50% max allocation per trade."""
        # 50% of 50K = 25K max. 400 * 65 = ₹26,000 (> 25K)
        signal = {
            "signal": "BUY_CE",
            "spot": 25100.0,
            "entry_price": 420.0, # 420 * 65 = ₹27,300
            "quantity": 65,
            "is_test": True
        }
        approved, reason = risk_manager.validate_signal_armor(signal)
        self.assertFalse(approved)
        self.assertIn("Position Sizing Cap", reason)

    def test_normal_order_passes_wallet_check(self):
        """Verify standard 1 lot ATM option buy (~₹13,000 margin) passes wallet check."""
        signal = {
            "signal": "BUY_CE",
            "spot": 25100.0,
            "entry_price": 200.0, # 200 * 65 = ₹13,000 (< 25K cap and < 50K balance)
            "quantity": 65,
            "candle_range": 15.0,
            "is_test": True
        }
        approved, reason = risk_manager.validate_signal_armor(signal)
        self.assertTrue(approved, f"Expected APPROVED, got: {reason}")

    def test_roi_calculation_after_trades(self):
        """Verify wallet balance and ROI % update accurately on closed trade."""
        # Record trade entry
        trade_id = database.record_trade_entry({
            "signal_type": "BUY_CE",
            "symbol": "NIFTY25200CE",
            "spot_price": 25150.0,
            "entry_price": 200.0,
            "sl_price": 190.0,
            "target_price": 225.0,
            "quantity": 65,
            "is_runner": 0
        })
        
        # Set trade_date to isolated test campaign date
        conn = database.get_connection()
        conn.cursor().execute("UPDATE trades SET trade_date = ? WHERE id = ?", (self.TEST_START_DATE, trade_id))
        conn.commit()
        conn.close()
        
        # Verify margin is locked while active
        w_active = database.get_wallet_stats()
        self.assertEqual(w_active["margin_used"], 13000.0) # 200 * 65
        self.assertEqual(w_active["available_margin"], 37000.0)

        # Record trade exit with +20 pts profit (+₹1,300.00)
        database.record_trade_exit(
            trade_id=trade_id,
            exit_price=220.0,
            exit_reason="TARGET_HIT",
            pnl_points=20.0,
            pnl_rupees=1300.0
        )

        w_closed = database.get_wallet_stats()
        self.assertEqual(w_closed["margin_used"], 0.0)
        self.assertEqual(w_closed["current_balance"], 51300.0)
        self.assertEqual(w_closed["realized_pnl"], 1300.0)
        # ROI = (1300 / 50000) * 100 = 2.6%
        self.assertEqual(w_closed["roi_pct"], 2.6)

if __name__ == "__main__":
    unittest.main()
