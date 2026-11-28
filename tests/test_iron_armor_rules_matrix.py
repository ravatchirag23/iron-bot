"""
Institutional 16 Iron Armor Rules Matrix Test Suite (110 Tests)
Covers:
1. Market hours, morning buffers, Euro blackout, and timing boundaries (25 tests)
2. Daily trade counts, loss limits, auto-locks, and dynamic overrides (25 tests)
3. Midday chop defense, candle ranges, and exhaustion filters (25 tests)
4. HTF trend shields, active trade locks, debounce, and wallet caps (35 tests)
"""

import unittest
import os
import sys
import tempfile
from datetime import datetime, time
from zoneinfo import ZoneInfo
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ["IRON_BOT_TEST_MODE"] = "1"
if "IRON_BOT_DB_PATH" not in os.environ:
    os.environ["IRON_BOT_DB_PATH"] = os.path.join(tempfile.gettempdir(), "iron_bot_unit_tests.db")

import config
import risk_manager
import database

TZ = ZoneInfo("Asia/Kolkata")


def make_dt(hour, minute, second=0):
    return datetime(2026, 11, 28, hour, minute, second, tzinfo=TZ)


class TestMarketHoursAndTimingRules(unittest.TestCase):
    """Verifies Rule 3 (Market Hours), Rule 5 (Euro Blackout), and timing gates (25 tests)."""

    def setUp(self):
        self._patcher = patch("builtins.print")
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    def _mock_time(self, hour, minute, second=0):
        dt = make_dt(hour, minute, second)
        return patch.object(config, "get_now_ist", return_value=dt)

    def _base_signal(self, is_test=False):
        return {
            "signal": "BUY_CE",
            "spot": 25200.0,
            "entry_price": 200.0,
            "candle_range": 22.0,
            "is_test": is_test
        }

    def test_091459_premarket_rejected(self):
        with self._mock_time(9, 14, 59):
            ok, msg = risk_manager.validate_signal_armor(self._base_signal())
            self.assertFalse(ok)
            self.assertIn("Too early", msg)

    def test_091500_open_buffer_rejected(self):
        with self._mock_time(9, 15, 0):
            ok, msg = risk_manager.validate_signal_armor(self._base_signal())
            self.assertFalse(ok)
            self.assertIn("Wait till 09:30 AM", msg)

    def test_092000_morning_chop_rejected(self):
        with self._mock_time(9, 20, 0):
            ok, msg = risk_manager.validate_signal_armor(self._base_signal())
            self.assertFalse(ok)
            self.assertIn("Too early", msg)

    def test_092959_preopen_boundary_rejected(self):
        with self._mock_time(9, 29, 59):
            ok, msg = risk_manager.validate_signal_armor(self._base_signal())
            self.assertFalse(ok)

    def test_093000_opening_bell_allowed(self):
        with self._mock_time(9, 30, 0):
            ok, msg = risk_manager.validate_signal_armor(self._base_signal())
            self.assertTrue(ok)
            self.assertEqual(msg, "APPROVED")

    def test_093001_morning_allowed(self):
        with self._mock_time(9, 30, 1):
            ok, msg = risk_manager.validate_signal_armor(self._base_signal())
            self.assertTrue(ok)

    def test_101500_prime_morning_momentum_allowed(self):
        with self._mock_time(10, 15, 0):
            ok, msg = risk_manager.validate_signal_armor(self._base_signal())
            self.assertTrue(ok)

    def test_110000_midmorning_allowed(self):
        with self._mock_time(11, 0, 0):
            ok, msg = risk_manager.validate_signal_armor(self._base_signal())
            self.assertTrue(ok)

    def test_112959_pre_midday_allowed(self):
        with self._mock_time(11, 29, 59):
            ok, msg = risk_manager.validate_signal_armor(self._base_signal())
            self.assertTrue(ok)

    def test_120000_midday_with_momentum_allowed(self):
        with self._mock_time(12, 0, 0):
            ok, msg = risk_manager.validate_signal_armor(self._base_signal())
            self.assertTrue(ok)

    def test_132459_before_euro_blackout_allowed(self):
        with self._mock_time(13, 24, 59):
            ok, msg = risk_manager.validate_signal_armor(self._base_signal())
            self.assertTrue(ok)

    def test_132500_euro_blackout_start_rejected(self):
        with self._mock_time(13, 25, 0):
            ok, msg = risk_manager.validate_signal_armor(self._base_signal())
            self.assertFalse(ok)
            self.assertIn("European Market Open Whiplash Window", msg)

    def test_133000_euro_blackout_mid_rejected(self):
        with self._mock_time(13, 30, 0):
            ok, msg = risk_manager.validate_signal_armor(self._base_signal())
            self.assertFalse(ok)
            self.assertIn("European Market Open", msg)

    def test_134500_euro_blackout_end_rejected(self):
        with self._mock_time(13, 45, 0):
            ok, msg = risk_manager.validate_signal_armor(self._base_signal())
            self.assertFalse(ok)

    def test_134501_after_euro_blackout_allowed(self):
        with self._mock_time(13, 45, 1):
            ok, msg = risk_manager.validate_signal_armor(self._base_signal())
            self.assertTrue(ok)

    def test_140000_afternoon_session_allowed(self):
        with self._mock_time(14, 0, 0):
            ok, msg = risk_manager.validate_signal_armor(self._base_signal())
            self.assertTrue(ok)

    def test_142000_power_hour_prep_allowed(self):
        with self._mock_time(14, 20, 0):
            ok, msg = risk_manager.validate_signal_armor(self._base_signal())
            self.assertTrue(ok)

    def test_144459_pre_cutoff_allowed(self):
        with self._mock_time(14, 44, 59):
            ok, msg = risk_manager.validate_signal_armor(self._base_signal())
            self.assertTrue(ok)

    def test_144500_exact_cutoff_allowed(self):
        with self._mock_time(14, 45, 0):
            ok, msg = risk_manager.validate_signal_armor(self._base_signal())
            self.assertTrue(ok)

    def test_144501_post_cutoff_rejected(self):
        with self._mock_time(14, 45, 1):
            ok, msg = risk_manager.validate_signal_armor(self._base_signal())
            self.assertFalse(ok)
            self.assertIn("Too late", msg)

    def test_150000_market_late_rejected(self):
        with self._mock_time(15, 0, 0):
            ok, msg = risk_manager.validate_signal_armor(self._base_signal())
            self.assertFalse(ok)
            self.assertIn("Too late", msg)

    def test_152000_squareoff_time_rejected(self):
        with self._mock_time(15, 20, 0):
            ok, msg = risk_manager.validate_signal_armor(self._base_signal())
            self.assertFalse(ok)

    def test_153000_market_close_rejected(self):
        with self._mock_time(15, 30, 0):
            ok, msg = risk_manager.validate_signal_armor(self._base_signal())
            self.assertFalse(ok)

    def test_is_test_flag_bypasses_market_hours(self):
        with self._mock_time(15, 25, 0):
            ok, msg = risk_manager.validate_signal_armor(self._base_signal(is_test=True))
            self.assertTrue(ok)

    def test_is_test_flag_bypasses_euro_blackout(self):
        with self._mock_time(13, 30, 0):
            ok, msg = risk_manager.validate_signal_armor(self._base_signal(is_test=True))
            self.assertTrue(ok)


class TestDailyLimitsAndLocks(unittest.TestCase):
    """Verifies Rule 1 (Trade Limit), Rule 2 (Loss Limit), and dynamic overrides (25 tests)."""

    def setUp(self):
        self._patcher = patch("builtins.print")
        self._patcher.start()
        database.reset_daily_limits()
        conn = database.get_connection()
        c = conn.cursor()
        c.execute("UPDATE trades SET status = 'CLOSED' WHERE status = 'ACTIVE'")
        if "iron_bot_unit_tests" in database.get_db_path():
            c.execute("DELETE FROM trades")
        c.execute("DELETE FROM audit_logs")
        conn.commit()
        conn.close()
        database.init_campaign_wallet(100000.0)

    def tearDown(self):
        database.reset_daily_limits()
        conn = database.get_connection()
        c = conn.cursor()
        c.execute("DELETE FROM audit_logs")
        conn.commit()
        conn.close()
        self._patcher.stop()

    def _sig(self):
        return {
            "signal": "BUY_CE",
            "spot": 25200.0,
            "entry_price": 200.0,
            "candle_range": 22.0,
            "is_test": True
        }

    def test_locked_flag_rejects_signal(self):
        with patch("database.get_today_stats", return_value={"is_locked": 1, "lock_reason": "MANUAL_LOCK"}):
            ok, msg = risk_manager.validate_signal_armor(self._sig())
            self.assertFalse(ok)
            self.assertIn("System is LOCKED", msg)

    def test_unlocked_flag_allows_signal(self):
        with patch("database.get_today_stats", return_value={"is_locked": 0, "trade_count": 0, "total_pnl_rupees": 0.0}):
            ok, msg = risk_manager.validate_signal_armor(self._sig())
            self.assertTrue(ok)

    def test_trade_count_0_allowed(self):
        with patch("database.get_today_stats", return_value={"is_locked": 0, "trade_count": 0, "total_pnl_rupees": 0.0}):
            ok, _ = risk_manager.validate_signal_armor(self._sig())
            self.assertTrue(ok)

    def test_trade_count_1_allowed(self):
        with patch("database.get_today_stats", return_value={"is_locked": 0, "trade_count": 1, "total_pnl_rupees": 500.0}):
            ok, _ = risk_manager.validate_signal_armor(self._sig())
            self.assertTrue(ok)

    def test_trade_count_2_allowed(self):
        with patch("database.get_today_stats", return_value={"is_locked": 0, "trade_count": 2, "total_pnl_rupees": 1000.0}):
            ok, _ = risk_manager.validate_signal_armor(self._sig())
            self.assertTrue(ok)

    def test_trade_count_3_allowed(self):
        with patch("database.get_today_stats", return_value={"is_locked": 0, "trade_count": 3, "total_pnl_rupees": 1500.0}):
            ok, _ = risk_manager.validate_signal_armor(self._sig())
            self.assertTrue(ok)

    def test_trade_count_4_default_rejected(self):
        with patch("database.get_today_stats", return_value={"is_locked": 0, "trade_count": 4, "total_pnl_rupees": 2000.0}):
            ok, msg = risk_manager.validate_signal_armor(self._sig())
            self.assertFalse(ok)
            self.assertIn("Max daily trade limit (4) reached", msg)

    def test_trade_count_5_rejected(self):
        with patch("database.get_today_stats", return_value={"is_locked": 0, "trade_count": 5, "total_pnl_rupees": 2000.0}):
            ok, msg = risk_manager.validate_signal_armor(self._sig())
            self.assertFalse(ok)

    def test_dynamic_trade_limit_override_6_at_4(self):
        database.set_today_trade_limit(6)
        with patch("database.get_today_stats", return_value={"is_locked": 0, "trade_count": 4, "total_pnl_rupees": 0.0}):
            ok, _ = risk_manager.validate_signal_armor(self._sig())
            self.assertTrue(ok)

    def test_dynamic_trade_limit_override_6_at_5(self):
        database.set_today_trade_limit(6)
        with patch("database.get_today_stats", return_value={"is_locked": 0, "trade_count": 5, "total_pnl_rupees": 0.0}):
            ok, _ = risk_manager.validate_signal_armor(self._sig())
            self.assertTrue(ok)

    def test_dynamic_trade_limit_override_6_at_6_rejected(self):
        database.set_today_trade_limit(6)
        with patch("database.get_today_stats", return_value={"is_locked": 0, "trade_count": 6, "total_pnl_rupees": 0.0}):
            ok, msg = risk_manager.validate_signal_armor(self._sig())
            self.assertFalse(ok)
            self.assertIn("Max daily trade limit (6) reached", msg)

    def test_loss_at_1500_allowed(self):
        with patch("database.get_today_stats", return_value={"is_locked": 0, "trade_count": 2, "total_pnl_rupees": -1500.0}):
            ok, _ = risk_manager.validate_signal_armor(self._sig())
            self.assertTrue(ok)

    def test_loss_at_2490_allowed(self):
        with patch("database.get_today_stats", return_value={"is_locked": 0, "trade_count": 2, "total_pnl_rupees": -2490.0}):
            ok, _ = risk_manager.validate_signal_armor(self._sig())
            self.assertTrue(ok)

    def test_loss_at_2500_default_rejected(self):
        with patch("database.get_today_stats", return_value={"is_locked": 0, "trade_count": 2, "total_pnl_rupees": -2500.0}):
            ok, msg = risk_manager.validate_signal_armor(self._sig())
            self.assertFalse(ok)
            self.assertIn("Daily max loss (-₹2500", msg)

    def test_loss_at_2501_rejected(self):
        with patch("database.get_today_stats", return_value={"is_locked": 0, "trade_count": 2, "total_pnl_rupees": -2501.0}):
            ok, msg = risk_manager.validate_signal_armor(self._sig())
            self.assertFalse(ok)

    def test_dynamic_loss_limit_5000_at_3000_allowed(self):
        database.set_today_loss_limit(5000.0)
        with patch("database.get_today_stats", return_value={"is_locked": 0, "trade_count": 2, "total_pnl_rupees": -3000.0}):
            ok, _ = risk_manager.validate_signal_armor(self._sig())
            self.assertTrue(ok)

    def test_dynamic_loss_limit_5000_at_4500_allowed(self):
        database.set_today_loss_limit(5000.0)
        with patch("database.get_today_stats", return_value={"is_locked": 0, "trade_count": 2, "total_pnl_rupees": -4500.0}):
            ok, _ = risk_manager.validate_signal_armor(self._sig())
            self.assertTrue(ok)

    def test_dynamic_loss_limit_5000_at_5000_rejected(self):
        database.set_today_loss_limit(5000.0)
        with patch("database.get_today_stats", return_value={"is_locked": 0, "trade_count": 2, "total_pnl_rupees": -5000.0}):
            ok, msg = risk_manager.validate_signal_armor(self._sig())
            self.assertFalse(ok)
            self.assertIn("Daily max loss (-₹5000.0) hit", msg)

    def test_positive_pnl_allowed(self):
        with patch("database.get_today_stats", return_value={"is_locked": 0, "trade_count": 2, "total_pnl_rupees": 3500.0}):
            ok, _ = risk_manager.validate_signal_armor(self._sig())
            self.assertTrue(ok)

    def test_unlock_restores_trading(self):
        database.lock_system_today("TEST_LOCK")
        database.unlock_system_today()
        stats = database.get_today_stats()
        self.assertEqual(stats["is_locked"], 0)

    def test_unlock_retains_trade_count(self):
        tid = database.record_trade_entry({
            "signal_type": "BUY_CE",
            "symbol": "NIFTY25200CE_UNLOCK_TEST",
            "spot_price": 25200.0,
            "entry_price": 200.0,
            "sl_price": 185.0,
            "target_price": 214.0,
            "quantity": 65,
            "is_paper": 1
        })
        database.record_trade_exit(tid, 185.0, "MICRO_SL_HIT", -15.0, -975.0)
        database.unlock_system_today()
        stats = database.get_today_stats()
        self.assertGreaterEqual(stats["trade_count"], 1)

    def test_rejection_format_trade_limit(self):
        with patch("database.get_today_stats", return_value={"is_locked": 0, "trade_count": 4, "total_pnl_rupees": 0.0}):
            _, msg = risk_manager.validate_signal_armor(self._sig())
            self.assertIn("Zero overtrading", msg)

    def test_rejection_format_loss_limit(self):
        with patch("database.get_today_stats", return_value={"is_locked": 0, "trade_count": 1, "total_pnl_rupees": -2600.0}):
            _, msg = risk_manager.validate_signal_armor(self._sig())
            self.assertIn("Capital protection active", msg)

    def test_dynamic_trade_limit_persisted(self):
        database.set_today_trade_limit(8)
        self.assertEqual(config.get_max_daily_trades(), 8)

    def test_dynamic_loss_limit_persisted(self):
        database.set_today_loss_limit(4000.0)
        self.assertEqual(config.get_max_daily_loss(), 4000.0)


class TestMiddayAndCandleExhaustion(unittest.TestCase):
    """Verifies Rule 5B (Midday Chop) and Rule 10 (Candle Exhaustion) (25 tests)."""

    def setUp(self):
        self._patcher = patch("builtins.print")
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    def _sig(self, candle_range=22.0, high=None, low=None, close=None, is_ce=True):
        return {
            "signal": "BUY_CE" if is_ce else "BUY_PE",
            "spot": 25200.0,
            "entry_price": 200.0,
            "candle_range": candle_range,
            "candle_high": high,
            "candle_low": low,
            "candle_close": close,
            "is_test": False
        }

    def _mock_time(self, hour, minute):
        return patch.object(config, "get_now_ist", return_value=make_dt(hour, minute))

    def test_midday_10pt_range_rejected(self):
        with self._mock_time(12, 0):
            ok, msg = risk_manager.validate_signal_armor(self._sig(candle_range=10.0))
            self.assertFalse(ok)
            self.assertIn("MIDDAY_CHOP_DEFENSE", msg)

    def test_midday_15pt_range_rejected(self):
        with self._mock_time(12, 15):
            ok, msg = risk_manager.validate_signal_armor(self._sig(candle_range=15.0))
            self.assertFalse(ok)
            self.assertIn("MIDDAY_CHOP_DEFENSE", msg)

    def test_midday_179pt_range_rejected(self):
        with self._mock_time(12, 30):
            ok, msg = risk_manager.validate_signal_armor(self._sig(candle_range=17.9))
            self.assertFalse(ok)

    def test_midday_180pt_range_allowed(self):
        with self._mock_time(12, 0):
            ok, msg = risk_manager.validate_signal_armor(self._sig(candle_range=18.0))
            self.assertTrue(ok)

    def test_midday_220pt_range_allowed(self):
        with self._mock_time(12, 0):
            ok, msg = risk_manager.validate_signal_armor(self._sig(candle_range=22.0))
            self.assertTrue(ok)

    def test_midday_computed_range_from_high_low(self):
        with self._mock_time(12, 0):
            sig = self._sig(candle_range=0.0, high=25220.0, low=25200.0)
            ok, _ = risk_manager.validate_signal_armor(sig)
            self.assertTrue(ok)

    def test_midday_computed_range_too_small(self):
        with self._mock_time(12, 0):
            sig = self._sig(candle_range=0.0, high=25210.0, low=25200.0)
            ok, msg = risk_manager.validate_signal_armor(sig)
            self.assertFalse(ok)
            self.assertIn("MIDDAY_CHOP_DEFENSE", msg)

    def test_non_midday_12pt_range_allowed(self):
        with self._mock_time(10, 15):
            ok, msg = risk_manager.validate_signal_armor(self._sig(candle_range=12.0))
            self.assertTrue(ok)

    def test_exhaustion_50pt_candle_allowed(self):
        with self._mock_time(10, 15):
            ok, msg = risk_manager.validate_signal_armor(self._sig(candle_range=50.0))
            self.assertTrue(ok)

    def test_exhaustion_649pt_candle_allowed(self):
        with self._mock_time(10, 15):
            ok, msg = risk_manager.validate_signal_armor(self._sig(candle_range=64.9))
            self.assertTrue(ok)

    def test_exhaustion_heavy_upper_wick_ce_rejected(self):
        with self._mock_time(10, 15):
            # Candle range 70, High 25270, Close 25230 -> upper wick = 40 (57% of range > 35%)
            sig = self._sig(candle_range=70.0, high=25270.0, low=25200.0, close=25230.0, is_ce=True)
            ok, msg = risk_manager.validate_signal_armor(sig)
            self.assertFalse(ok)
            self.assertIn("Heavy upper wick rejection", msg)

    def test_exhaustion_heavy_lower_wick_pe_rejected(self):
        with self._mock_time(10, 15):
            # Candle range 70, Low 25200, Close 25240 -> lower wick = 40 (57% of range > 35%)
            sig = self._sig(candle_range=70.0, high=25270.0, low=25200.0, close=25240.0, is_ce=False)
            ok, msg = risk_manager.validate_signal_armor(sig)
            self.assertFalse(ok)
            self.assertIn("Heavy lower wick bounce", msg)

    def test_exhaustion_marubozu_bullish_allowed(self):
        with self._mock_time(10, 15):
            # Candle range 70, High 25270, Close 25265 -> upper wick = 5 (7% of range < 35%)
            sig = self._sig(candle_range=70.0, high=25270.0, low=25200.0, close=25265.0, is_ce=True)
            ok, msg = risk_manager.validate_signal_armor(sig)
            self.assertTrue(ok)

    def test_exhaustion_marubozu_bearish_allowed(self):
        with self._mock_time(10, 15):
            # Candle range 70, Low 25200, Close 25205 -> lower wick = 5 (7% of range < 35%)
            sig = self._sig(candle_range=70.0, high=25270.0, low=25200.0, close=25205.0, is_ce=False)
            ok, msg = risk_manager.validate_signal_armor(sig)
            self.assertTrue(ok)

    def test_mega_bar_86pt_rejected(self):
        with self._mock_time(10, 15):
            sig = self._sig(candle_range=86.0)
            ok, msg = risk_manager.validate_signal_armor(sig)
            self.assertFalse(ok)
            self.assertIn("Mega-bar volatility spike", msg)

    def test_mega_bar_120pt_rejected(self):
        with self._mock_time(10, 15):
            sig = self._sig(candle_range=120.0)
            ok, msg = risk_manager.validate_signal_armor(sig)
            self.assertFalse(ok)
            self.assertIn("Mega-bar volatility spike", msg)

    def test_missing_high_close_above_65_rejected(self):
        with self._mock_time(10, 15):
            sig = self._sig(candle_range=68.0)
            ok, msg = risk_manager.validate_signal_armor(sig)
            self.assertFalse(ok)
            self.assertIn("Exhaustion candle detected", msg)

    def test_zero_candle_range_normal_hours_allowed(self):
        with self._mock_time(10, 15):
            ok, msg = risk_manager.validate_signal_armor(self._sig(candle_range=0.0))
            self.assertTrue(ok)

    def test_negative_candle_range_normalization(self):
        with self._mock_time(10, 15):
            ok, msg = risk_manager.validate_signal_armor(self._sig(candle_range=-10.0))
            self.assertTrue(ok)

    def test_micro_candle_range_2pt_normal_hours(self):
        with self._mock_time(10, 15):
            ok, msg = risk_manager.validate_signal_armor(self._sig(candle_range=2.0))
            self.assertTrue(ok)

    def test_normal_candle_25pt_allowed(self):
        with self._mock_time(10, 15):
            ok, msg = risk_manager.validate_signal_armor(self._sig(candle_range=25.0))
            self.assertTrue(ok)

    def test_midday_threshold_custom_config(self):
        with self._mock_time(12, 0), patch.object(config, "MIDDAY_MIN_CANDLE_RANGE", 25.0):
            ok, msg = risk_manager.validate_signal_armor(self._sig(candle_range=22.0))
            self.assertFalse(ok)

    def test_upper_wick_exact_ratio_boundary(self):
        with self._mock_time(10, 15):
            # 35% exact wick: range 100, high 25300, close 25265 -> wick = 35 (35% -> <= 35% allowed)
            sig = self._sig(candle_range=70.0, high=25270.0, low=25200.0, close=25245.5, is_ce=True)
            ok, _ = risk_manager.validate_signal_armor(sig)
            self.assertTrue(ok)

    def test_lower_wick_exact_ratio_boundary(self):
        with self._mock_time(10, 15):
            sig = self._sig(candle_range=70.0, high=25270.0, low=25200.0, close=25224.5, is_ce=False)
            ok, _ = risk_manager.validate_signal_armor(sig)
            self.assertTrue(ok)

    def test_exhaustion_do_not_buy_top_msg(self):
        with self._mock_time(10, 15):
            sig = self._sig(candle_range=75.0, high=25275.0, low=25200.0, close=25220.0, is_ce=True)
            _, msg = risk_manager.validate_signal_armor(sig)
            self.assertIn("Do not buy top", msg)


class TestTrendShieldAndWalletCaps(unittest.TestCase):
    """Verifies Rule 16 (HTF Trend Shield), Active Trade Locks, Debounce, and Wallet Rules (35 tests)."""

    def setUp(self):
        self._patcher = patch("builtins.print")
        self._patcher.start()
        database.reset_daily_limits()
        conn = database.get_connection()
        c = conn.cursor()
        c.execute("UPDATE trades SET status = 'CLOSED' WHERE status = 'ACTIVE'")
        if "iron_bot_unit_tests" in database.get_db_path():
            c.execute("DELETE FROM trades")
        c.execute("DELETE FROM audit_logs")
        conn.commit()
        conn.close()
        database.init_campaign_wallet(100000.0)

    def tearDown(self):
        database.reset_daily_limits()
        conn = database.get_connection()
        c = conn.cursor()
        c.execute("DELETE FROM audit_logs")
        conn.commit()
        conn.close()
        self._patcher.stop()

    def _sig(self, signal="BUY_CE", spot=25200.0, day_open=25200.0, is_test=False, entry_price=200.0):
        return {
            "signal": signal,
            "spot": spot,
            "day_open": day_open,
            "entry_price": entry_price,
            "candle_range": 22.0,
            "is_test": is_test
        }

    def _mock_time(self, hour=10, minute=15):
        return patch.object(config, "get_now_ist", return_value=make_dt(hour, minute))

    def test_htf_ce_blocked_when_down_61pts(self):
        with self._mock_time():
            sig = self._sig(signal="BUY_CE", spot=25139.0, day_open=25200.0)
            ok, msg = risk_manager.validate_signal_armor(sig)
            self.assertFalse(ok)
            self.assertIn("Counter-trend CE buy strictly blocked in heavy downtrend", msg)

    def test_htf_ce_blocked_when_down_100pts(self):
        with self._mock_time():
            sig = self._sig(signal="BUY_CE", spot=25100.0, day_open=25200.0)
            ok, msg = risk_manager.validate_signal_armor(sig)
            self.assertFalse(ok)

    def test_htf_pe_allowed_when_down_61pts(self):
        with self._mock_time():
            sig = self._sig(signal="BUY_PE", spot=25139.0, day_open=25200.0)
            ok, _ = risk_manager.validate_signal_armor(sig)
            self.assertTrue(ok)

    def test_htf_pe_blocked_when_up_61pts(self):
        with self._mock_time():
            sig = self._sig(signal="BUY_PE", spot=25261.0, day_open=25200.0)
            ok, msg = risk_manager.validate_signal_armor(sig)
            self.assertFalse(ok)
            self.assertIn("Counter-trend PE buy strictly blocked in heavy uptrend", msg)

    def test_htf_pe_blocked_when_up_150pts(self):
        with self._mock_time():
            sig = self._sig(signal="BUY_PE", spot=25350.0, day_open=25200.0)
            ok, msg = risk_manager.validate_signal_armor(sig)
            self.assertFalse(ok)

    def test_htf_ce_allowed_when_up_61pts(self):
        with self._mock_time():
            sig = self._sig(signal="BUY_CE", spot=25261.0, day_open=25200.0)
            ok, _ = risk_manager.validate_signal_armor(sig)
            self.assertTrue(ok)

    def test_htf_ce_allowed_when_down_59pts(self):
        with self._mock_time():
            sig = self._sig(signal="BUY_CE", spot=25141.0, day_open=25200.0)
            ok, _ = risk_manager.validate_signal_armor(sig)
            self.assertTrue(ok)

    def test_htf_pe_allowed_when_up_59pts(self):
        with self._mock_time():
            sig = self._sig(signal="BUY_PE", spot=25259.0, day_open=25200.0)
            ok, _ = risk_manager.validate_signal_armor(sig)
            self.assertTrue(ok)

    def test_htf_ce_allowed_at_flat_open(self):
        with self._mock_time():
            sig = self._sig(signal="BUY_CE", spot=25200.0, day_open=25200.0)
            ok, _ = risk_manager.validate_signal_armor(sig)
            self.assertTrue(ok)

    def test_htf_pe_allowed_at_flat_open(self):
        with self._mock_time():
            sig = self._sig(signal="BUY_PE", spot=25200.0, day_open=25200.0)
            ok, _ = risk_manager.validate_signal_armor(sig)
            self.assertTrue(ok)

    def test_htf_macro_pct_positive_blocks_pe(self):
        with self._mock_time(), patch("macro_radar.get_macro_sentiment", return_value={"nifty": {"change": 0.35}}):
            sig = self._sig(signal="BUY_PE", spot=25200.0, day_open=0.0)
            ok, msg = risk_manager.validate_signal_armor(sig)
            self.assertFalse(ok)
            self.assertIn("Raging Bull Day", msg)

    def test_htf_macro_pct_negative_blocks_ce(self):
        with self._mock_time(), patch("macro_radar.get_macro_sentiment", return_value={"nifty": {"change": -0.35}}):
            sig = self._sig(signal="BUY_CE", spot=25200.0, day_open=0.0)
            ok, msg = risk_manager.validate_signal_armor(sig)
            self.assertFalse(ok)
            self.assertIn("Bloodbath Day", msg)

    def test_htf_macro_pct_mild_allows_pe(self):
        with self._mock_time(), patch("macro_radar.get_macro_sentiment", return_value={"nifty": {"change": 0.20}}):
            sig = self._sig(signal="BUY_PE", spot=25200.0, day_open=0.0)
            ok, _ = risk_manager.validate_signal_armor(sig)
            self.assertTrue(ok)

    def test_htf_macro_pct_mild_allows_ce(self):
        with self._mock_time(), patch("macro_radar.get_macro_sentiment", return_value={"nifty": {"change": -0.20}}):
            sig = self._sig(signal="BUY_CE", spot=25200.0, day_open=0.0)
            ok, _ = risk_manager.validate_signal_armor(sig)
            self.assertTrue(ok)

    def test_htf_shield_bypassed_by_is_test(self):
        with self._mock_time():
            sig = self._sig(signal="BUY_CE", spot=25000.0, day_open=25200.0, is_test=True)
            ok, _ = risk_manager.validate_signal_armor(sig)
            self.assertTrue(ok)

    def test_active_trade_blocks_new_entry(self):
        with patch("database.get_active_trade", return_value={"id": 99, "symbol": "NIFTY25200CE"}):
            ok, msg = risk_manager.validate_signal_armor(self._sig(is_test=True))
            self.assertFalse(ok)
            self.assertIn("Already in active trade #99", msg)

    def test_no_active_trade_allows_entry(self):
        with patch("database.get_active_trade", return_value=None):
            ok, _ = risk_manager.validate_signal_armor(self._sig(is_test=True))
            self.assertTrue(ok)

    def test_debounce_duplicate_signal_blocked(self):
        with self._mock_time(), patch("database.is_duplicate_alert", return_value=True):
            ok, msg = risk_manager.validate_signal_armor(self._sig(is_test=False))
            self.assertFalse(ok)
            self.assertIn("Duplicate signal detected within", msg)

    def test_debounce_bypassed_by_is_test(self):
        with self._mock_time(), patch("database.is_duplicate_alert", return_value=True):
            ok, _ = risk_manager.validate_signal_armor(self._sig(is_test=True))
            self.assertTrue(ok)

    def test_debounce_not_duplicate_allowed(self):
        with self._mock_time(), patch("database.is_duplicate_alert", return_value=False):
            ok, _ = risk_manager.validate_signal_armor(self._sig(is_test=False))
            self.assertTrue(ok)

    def test_capital_preservation_triggered_blocks(self):
        with patch("database.get_wallet_stats", return_value={"is_preservation_triggered": True, "current_balance": 68000.0, "preservation_floor": 70000.0, "available_margin": 68000.0}):
            ok, msg = risk_manager.validate_signal_armor(self._sig(is_test=True))
            self.assertFalse(ok)
            self.assertIn("Capital Preservation Circuit Breaker", msg)

    def test_capital_preservation_not_triggered_allows(self):
        with patch("database.get_wallet_stats", return_value={"is_preservation_triggered": False, "current_balance": 98000.0, "preservation_floor": 70000.0, "available_margin": 98000.0}):
            ok, _ = risk_manager.validate_signal_armor(self._sig(is_test=True))
            self.assertTrue(ok)

    def test_insufficient_margin_blocks(self):
        with patch("database.get_wallet_stats", return_value={"is_preservation_triggered": False, "current_balance": 10000.0, "preservation_floor": 5000.0, "available_margin": 5000.0}):
            # Entry 200 * 65 = 13,000 > available 5,000
            ok, msg = risk_manager.validate_signal_armor(self._sig(entry_price=200.0, is_test=True))
            self.assertFalse(ok)
            self.assertIn("Insufficient Wallet Capital", msg)

    def test_sufficient_margin_allows(self):
        with patch("database.get_wallet_stats", return_value={"is_preservation_triggered": False, "current_balance": 98000.0, "preservation_floor": 70000.0, "available_margin": 98000.0}):
            ok, _ = risk_manager.validate_signal_armor(self._sig(entry_price=200.0, is_test=True))
            self.assertTrue(ok)

    def test_position_sizing_50pct_cap_blocks(self):
        with patch("database.get_wallet_stats", return_value={"is_preservation_triggered": False, "current_balance": 20000.0, "preservation_floor": 10000.0, "available_margin": 20000.0}):
            # Required margin 200 * 65 = 13,000 > 50% of 20,000 (10,000)
            ok, msg = risk_manager.validate_signal_armor(self._sig(entry_price=200.0, is_test=True))
            self.assertFalse(ok)
            self.assertIn("Position Sizing Cap", msg)

    def test_position_sizing_exact_50pct_allows(self):
        with patch("database.get_wallet_stats", return_value={"is_preservation_triggered": False, "current_balance": 26000.0, "preservation_floor": 10000.0, "available_margin": 26000.0}):
            # Required margin 200 * 65 = 13,000 == 50% of 26,000 (13,000)
            ok, _ = risk_manager.validate_signal_armor(self._sig(entry_price=200.0, is_test=True))
            self.assertTrue(ok)

    def test_position_sizing_large_premium_blocks(self):
        with patch("database.get_wallet_stats", return_value={"is_preservation_triggered": False, "current_balance": 100000.0, "preservation_floor": 70000.0, "available_margin": 100000.0}):
            # Required margin 800 * 65 = 52,000 > 50,000
            ok, msg = risk_manager.validate_signal_armor(self._sig(entry_price=800.0, is_test=True))
            self.assertFalse(ok)
            self.assertIn("Position Sizing Cap", msg)

    def test_wallet_balance_100k_baseline_allows(self):
        with patch("database.get_wallet_stats", return_value={"is_preservation_triggered": False, "current_balance": 100000.0, "preservation_floor": 70000.0, "available_margin": 100000.0}):
            ok, _ = risk_manager.validate_signal_armor(self._sig(entry_price=180.0, is_test=True))
            self.assertTrue(ok)

    def test_htf_shield_exact_60pt_down_ce_blocked(self):
        with self._mock_time():
            sig = self._sig(signal="BUY_CE", spot=25140.0, day_open=25200.0)
            ok, msg = risk_manager.validate_signal_armor(sig)
            self.assertFalse(ok)

    def test_htf_shield_exact_60pt_up_pe_blocked(self):
        with self._mock_time():
            sig = self._sig(signal="BUY_PE", spot=25260.0, day_open=25200.0)
            ok, msg = risk_manager.validate_signal_armor(sig)
            self.assertFalse(ok)

    def test_htf_shield_day_open_zero_handled_safely(self):
        with self._mock_time():
            sig = self._sig(signal="BUY_CE", spot=25200.0, day_open=0.0)
            ok, _ = risk_manager.validate_signal_armor(sig)
            self.assertTrue(ok)

    def test_htf_shield_spot_zero_handled_safely(self):
        with self._mock_time():
            sig = self._sig(signal="BUY_CE", spot=0.0, day_open=25200.0)
            ok, _ = risk_manager.validate_signal_armor(sig)
            self.assertTrue(ok)

    def test_htf_custom_points_threshold(self):
        with self._mock_time(), patch.object(config, "HTF_TREND_SHIELD_POINTS", 40.0):
            sig = self._sig(signal="BUY_CE", spot=25155.0, day_open=25200.0)
            ok, _ = risk_manager.validate_signal_armor(sig)
            self.assertFalse(ok)

    def test_htf_custom_pct_threshold(self):
        with self._mock_time(), patch.object(config, "HTF_TREND_SHIELD_PCT", 0.50), \
             patch("macro_radar.get_macro_sentiment", return_value={"nifty": {"change": -0.40}}):
            sig = self._sig(signal="BUY_CE", spot=25200.0, day_open=0.0)
            ok, _ = risk_manager.validate_signal_armor(sig)
            self.assertTrue(ok)

    def test_all_16_rules_green_passes(self):
        with self._mock_time(10, 15):
            sig = self._sig(signal="BUY_CE", spot=25210.0, day_open=25200.0, entry_price=180.0)
            ok, msg = risk_manager.validate_signal_armor(sig)
            self.assertTrue(ok)
            self.assertEqual(msg, "APPROVED")


if __name__ == "__main__":
    unittest.main()
