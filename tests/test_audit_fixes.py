"""
Unit tests for audit fixes:
1. Realistic gap-through SL fills (using actual tick LTP, not optimistic SL price)
2. Inverted SL / Target guardrails in risk manager
3. Fixed initial SL distance preservation in trailing SL milestones
4. Initial SL price column persistence in SQLite
"""

import unittest
from unittest.mock import patch
from datetime import datetime
from zoneinfo import ZoneInfo
import database
import paper_engine
import risk_manager
import config

MOCK_NOW_1130 = datetime(2026, 11, 18, 11, 30, 0, tzinfo=ZoneInfo("Asia/Kolkata"))

class TestAuditFixes(unittest.TestCase):
    def setUp(self):
        database.reset_daily_limits()
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE trades SET status = 'CLOSED' WHERE status = 'ACTIVE'")
        cursor.execute("DELETE FROM trades WHERE symbol LIKE '%AUDIT_FIX%'")
        conn.commit()
        conn.close()
        paper_engine.engine.sync_state()

    def tearDown(self):
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE trades SET status = 'CLOSED' WHERE status = 'ACTIVE'")
        cursor.execute("DELETE FROM trades WHERE symbol LIKE '%AUDIT_FIX%'")
        conn.commit()
        conn.close()
        database.reset_daily_limits()
        paper_engine.engine.sync_state()

    def test_gap_through_sl_fill(self):
        """When price drops through SL in a fast move, exit must reflect actual market price, not optimistic SL."""
        with patch("config.get_now_ist", return_value=MOCK_NOW_1130):
            trade_params = {
                "signal_type": "BUY_CE",
                "symbol": "NIFTY25250CE_AUDIT_FIX_1",
                "spot_price": 25250.0,
                "entry_price": 185.0,
                "sl_price": 170.0,
                "target_price": 215.0,
                "quantity": 65,
                "is_runner": False
            }
            trade_id = paper_engine.engine.process_entry(trade_params)
            
            # Simulate price suddenly gapping through SL to 165.70
            res = paper_engine.engine.update_tick(165.70)
            self.assertIsNotNone(res)
            self.assertEqual(res["status"], "CLOSED")
            self.assertEqual(res["reason"], "MICRO_SL_HIT")
            
            # Verify exit in DB
            trade = database.get_all_trades(1)[0]
            # Actual exit should be 165.70 - 0.6 = 165.10 (NOT 170.0 - 0.6 = 169.40!)
            self.assertEqual(trade["exit_price"], 165.10)
            self.assertEqual(trade["pnl_points"], round(165.10 - 185.0, 2)) # -19.9 pts
            self.assertEqual(trade["pnl_rupees"], round(-19.9 * 65, 2))

    def test_exact_touch_sl_fill(self):
        """When price touches SL exactly at 170.0, exit is 170.0 - friction."""
        with patch("config.get_now_ist", return_value=MOCK_NOW_1130):
            trade_params = {
                "signal_type": "BUY_CE",
                "symbol": "NIFTY25250CE_AUDIT_FIX_2",
                "spot_price": 25250.0,
                "entry_price": 185.0,
                "sl_price": 170.0,
                "target_price": 215.0,
                "quantity": 65,
                "is_runner": False
            }
            trade_id = paper_engine.engine.process_entry(trade_params)
            
            res = paper_engine.engine.update_tick(170.0)
            self.assertIsNotNone(res)
            self.assertEqual(res["status"], "CLOSED")
            
            trade = database.get_all_trades(1)[0]
            self.assertEqual(trade["exit_price"], 169.40) # 170.0 - 0.6

    def test_initial_sl_price_persisted(self):
        """Verify initial_sl_price is saved in DB and remains uncorrupted by trailing SL."""
        with patch("config.get_now_ist", return_value=MOCK_NOW_1130):
            trade_params = {
                "signal_type": "BUY_CE",
                "symbol": "NIFTY25250CE_AUDIT_FIX_3",
                "spot_price": 25250.0,
                "entry_price": 180.0,
                "sl_price": 165.0,
                "target_price": 210.0,
                "quantity": 65,
                "is_runner": False
            }
            trade_id = paper_engine.engine.process_entry(trade_params)
            
            trade = database.get_all_trades(1)[0]
            self.assertEqual(trade["entry_price"], 180.0)
            self.assertEqual(trade["sl_price"], 165.0)
            self.assertEqual(trade["initial_sl_price"], 165.0)
            
            # Now trigger Milestone 1 (+15 pts gain -> price reaches 195.0 -> cost lock)
            res = paper_engine.engine.update_tick(195.0)
            trade_after_m1 = database.get_all_trades(1)[0]
            self.assertEqual(trade_after_m1["sl_price"], 180.0) # SL moved to cost
            self.assertEqual(trade_after_m1["initial_sl_price"], 165.0) # Initial SL intact!
            
            # Engine should still know initial SL dist is 15.0 pts, not collapsed to 5.0 pts
            self.assertEqual(paper_engine.engine.initial_sl_dist, 15.0)

    def test_inverted_sl_guardrail(self):
        """Test risk manager corrects inverted SL if dynamic formula returns invalid bracket."""
        signal_data = {
            "signal": "BUY_CE",
            "spot": 25200.0,
            "entry_price": 167.55
        }
        params = risk_manager.calculate_trade_parameters(signal_data)
        
        # SL must always be below entry for a BUY position
        self.assertLess(params["sl_price"], params["entry_price"])
        self.assertGreater(params["target_price"], params["entry_price"])

if __name__ == "__main__":
    unittest.main()
