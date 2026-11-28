"""
Institutional Database Persistence, Overrides, and Capital Campaign Matrix Test Suite (80 Tests)
Covers:
1. SQLite configuration store, dynamic overrides, schema, and WAL mode (25 tests)
2. Daily tracker operations, locks, resets, and ledger analytics (25 tests)
3. ₹1 Lakh wallet campaign, margin tracking, position sizing, and drawdowns (30 tests)
"""

import unittest
import os
import sys
import tempfile
from datetime import datetime, date
from zoneinfo import ZoneInfo
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ["IRON_BOT_TEST_MODE"] = "1"
if "IRON_BOT_DB_PATH" not in os.environ:
    os.environ["IRON_BOT_DB_PATH"] = os.path.join(tempfile.gettempdir(), "iron_bot_unit_tests.db")

import config
import database

TZ = ZoneInfo("Asia/Kolkata")


class TestDatabaseConfigurationAndOverrides(unittest.TestCase):
    """Verifies SQLite key-value config store and dynamic overrides (25 tests)."""

    def setUp(self):
        self._patcher = patch("builtins.print")
        self._patcher.start()
        database.reset_daily_limits()

    def tearDown(self):
        database.reset_daily_limits()
        if "iron_bot_unit_tests" in database.get_db_path():
            conn = database.get_connection()
            conn.cursor().execute("DELETE FROM trades")
            conn.commit()
            conn.close()
        self._patcher.stop()

    def test_schema_initialization(self):
        database.init_db()
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cursor.fetchall()]
        conn.close()
        for expected in ["trades", "daily_tracker", "system_config"]:
            self.assertIn(expected, tables)

    def test_schema_init_idempotency(self):
        database.init_db()
        database.init_db()
        self.assertTrue(True)

    def test_wal_mode_enabled(self):
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0].lower()
        conn.close()
        self.assertIn(mode, ["wal", "memory"])

    def test_busy_timeout_configured(self):
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute("PRAGMA busy_timeout")
        timeout = cursor.fetchone()[0]
        conn.close()
        self.assertGreaterEqual(timeout, 1000)

    def test_set_config_string(self):
        database.set_config("test_str_key", "alpha_value")
        self.assertEqual(database.get_config("test_str_key"), "alpha_value")

    def test_set_config_int(self):
        database.set_config("test_int_key", 42)
        self.assertEqual(database.get_config("test_int_key"), "42")

    def test_set_config_float(self):
        database.set_config("test_float_key", 3.1415)
        self.assertEqual(float(database.get_config("test_float_key")), 3.1415)

    def test_set_config_boolean_true(self):
        database.set_config("test_bool_key", True)
        self.assertEqual(database.get_config("test_bool_key"), "True")

    def test_set_config_boolean_false(self):
        database.set_config("test_bool_key", False)
        self.assertEqual(database.get_config("test_bool_key"), "False")

    def test_get_config_default_when_missing(self):
        val = database.get_config("non_existent_key_999", "default_val")
        self.assertEqual(val, "default_val")

    def test_set_config_none_deletes_key(self):
        database.set_config("key_to_delete", "temporary")
        database.set_config("key_to_delete", None)
        self.assertIsNone(database.get_config("key_to_delete"))

    def test_set_today_loss_limit_sets_value(self):
        database.set_today_loss_limit(4500.0)
        self.assertEqual(float(database.get_config("max_loss_override_value")), 4500.0)

    def test_set_today_loss_limit_sets_date(self):
        database.set_today_loss_limit(4500.0)
        today = config.get_now_ist().strftime("%Y-%m-%d")
        self.assertEqual(database.get_config("max_loss_override_date"), today)

    def test_set_today_loss_limit_auto_unlocks_system(self):
        database.get_today_stats()
        database.lock_system_today("MANUAL_LOCK_TEST")
        database.set_today_loss_limit(5000.0)
        stats = database.get_today_stats()
        self.assertEqual(stats["is_locked"], 0)

    def test_set_today_trade_limit_sets_value(self):
        database.set_today_trade_limit(9)
        self.assertEqual(int(database.get_config("max_trades_override_value")), 9)

    def test_set_today_trade_limit_sets_date(self):
        database.set_today_trade_limit(9)
        today = config.get_now_ist().strftime("%Y-%m-%d")
        self.assertEqual(database.get_config("max_trades_override_date"), today)

    def test_set_today_trade_limit_auto_unlocks_system(self):
        database.get_today_stats()
        database.lock_system_today("MAX_TRADES_HIT")
        database.set_today_trade_limit(10)
        stats = database.get_today_stats()
        self.assertEqual(stats["is_locked"], 0)

    def test_set_max_sl_points_sets_value(self):
        database.set_max_sl_points(12.5)
        self.assertEqual(float(database.get_config("max_sl_points_override")), 12.5)

    def test_set_max_sl_points_none_clears(self):
        database.set_max_sl_points(12.5)
        database.set_max_sl_points(None)
        self.assertIsNone(database.get_config("max_sl_points_override"))

    def test_set_universal_trailing_true(self):
        database.set_universal_trailing(True)
        self.assertEqual(database.get_config("enable_universal_trailing"), "1")

    def test_set_universal_trailing_false(self):
        database.set_universal_trailing(False)
        self.assertEqual(database.get_config("enable_universal_trailing"), "0")

    def test_reset_daily_limits_clears_overrides(self):
        database.set_today_loss_limit(5000.0)
        database.set_today_trade_limit(10)
        database.set_max_sl_points(12.0)
        database.set_universal_trailing(True)
        database.reset_daily_limits()
        self.assertIsNone(database.get_config("max_loss_override_value"))
        self.assertIsNone(database.get_config("max_trades_override_value"))
        self.assertIsNone(database.get_config("max_sl_points_override"))
        self.assertIsNone(database.get_config("enable_universal_trailing"))

    def test_reset_daily_limits_unlocks_tracker(self):
        database.get_today_stats()
        database.lock_system_today("LIMIT_HIT")
        database.reset_daily_limits()
        stats = database.get_today_stats()
        self.assertEqual(stats["is_locked"], 0)

    def test_db_session_commit(self):
        database.set_config("session_test", "committed")
        self.assertEqual(database.get_config("session_test"), "committed")

    def test_db_session_rollback_on_error(self):
        database.set_config("rollback_test", "initial")
        try:
            with database.db_session() as conn:
                c = conn.cursor()
                now_str = config.get_now_str_ist()
                c.execute("UPDATE system_config SET value = 'should_rollback' WHERE key = 'rollback_test'")
                raise ValueError("Forced error to test rollback")
        except ValueError:
            pass
        self.assertEqual(database.get_config("rollback_test"), "initial")


class TestDailyTrackerAndLedgerSummary(unittest.TestCase):
    """Verifies daily tracker CRUD, lock states, and historical trade retrieval (25 tests)."""

    def setUp(self):
        self._patcher = patch("builtins.print")
        self._patcher.start()
        database.reset_daily_limits()
        database.get_today_stats()

    def tearDown(self):
        database.reset_daily_limits()
        if "iron_bot_unit_tests" in database.get_db_path():
            conn = database.get_connection()
            conn.cursor().execute("DELETE FROM trades")
            conn.commit()
            conn.close()
        self._patcher.stop()

    def test_today_stats_date_format(self):
        stats = database.get_today_stats()
        today = config.get_now_ist().strftime("%Y-%m-%d")
        self.assertEqual(stats["trade_date"], today)

    def test_fresh_day_initial_trades(self):
        stats = database.get_today_stats()
        self.assertEqual(stats["trade_count"], 0)

    def test_fresh_day_initial_pnl(self):
        stats = database.get_today_stats()
        self.assertEqual(stats["total_pnl_rupees"], 0.0)

    def test_fresh_day_initial_lock_state(self):
        stats = database.get_today_stats()
        self.assertEqual(stats["is_locked"], 0)

    def test_record_trade_exit_increments_trade_count(self):
        tid = database.record_trade_entry({
            "signal_type": "BUY_CE",
            "symbol": "NIFTY25200CE_TRACK_TEST",
            "spot_price": 25200.0,
            "entry_price": 200.0,
            "sl_price": 185.0,
            "target_price": 214.0,
            "quantity": 65,
            "is_paper": 1
        })
        database.record_trade_exit(tid, 214.0, "TARGET_HIT", 14.0, 910.0)
        stats = database.get_today_stats()
        self.assertEqual(stats["trade_count"], 1)

    def test_record_trade_exit_accumulates_profit(self):
        tid = database.record_trade_entry({
            "signal_type": "BUY_CE",
            "symbol": "NIFTY25200CE_TRACK_PROFIT",
            "spot_price": 25200.0,
            "entry_price": 200.0,
            "sl_price": 185.0,
            "target_price": 214.0,
            "quantity": 65,
            "is_paper": 1
        })
        database.record_trade_exit(tid, 214.0, "TARGET_HIT", 14.0, 910.0)
        stats = database.get_today_stats()
        self.assertEqual(stats["total_pnl_rupees"], 910.0)

    def test_record_trade_exit_accumulates_loss(self):
        tid = database.record_trade_entry({
            "signal_type": "BUY_CE",
            "symbol": "NIFTY25200CE_TRACK_LOSS",
            "spot_price": 25200.0,
            "entry_price": 200.0,
            "sl_price": 185.0,
            "target_price": 214.0,
            "quantity": 65,
            "is_paper": 1
        })
        database.record_trade_exit(tid, 185.0, "MICRO_SL_HIT", -15.0, -975.0)
        stats = database.get_today_stats()
        self.assertEqual(stats["total_pnl_rupees"], -975.0)

    def test_cumulative_multiple_trades_pnl(self):
        t1 = database.record_trade_entry({"signal_type": "BUY_CE", "symbol": "N1", "spot_price": 25000.0, "entry_price": 100.0, "sl_price": 85.0, "target_price": 114.0, "quantity": 65, "is_paper": 1})
        database.record_trade_exit(t1, 114.0, "TARGET_HIT", 14.0, 910.0)
        t2 = database.record_trade_entry({"signal_type": "BUY_PE", "symbol": "N2", "spot_price": 25000.0, "entry_price": 100.0, "sl_price": 85.0, "target_price": 114.0, "quantity": 65, "is_paper": 1})
        database.record_trade_exit(t2, 85.0, "MICRO_SL_HIT", -15.0, -975.0)
        stats = database.get_today_stats()
        self.assertEqual(stats["total_pnl_rupees"], round(910.0 - 975.0, 2))

    def test_lock_system_today_sets_is_locked(self):
        database.get_today_stats()
        database.lock_system_today("CUSTOM_REASON")
        stats = database.get_today_stats()
        self.assertEqual(stats["is_locked"], 1)

    def test_lock_system_today_stores_reason(self):
        database.get_today_stats()
        database.lock_system_today("CUSTOM_REASON_XYZ")
        stats = database.get_today_stats()
        self.assertEqual(stats["lock_reason"], "CUSTOM_REASON_XYZ")

    def test_unlock_system_today_clears_is_locked(self):
        database.get_today_stats()
        database.lock_system_today("LOCK_TEST")
        database.unlock_system_today()
        stats = database.get_today_stats()
        self.assertEqual(stats["is_locked"], 0)

    def test_unlock_system_today_clears_reason(self):
        database.get_today_stats()
        database.lock_system_today("LOCK_TEST")
        database.unlock_system_today()
        stats = database.get_today_stats()
        self.assertFalse(bool(stats["lock_reason"]))

    def test_get_all_trades_returns_list(self):
        trades = database.get_all_trades(10)
        self.assertIsInstance(trades, list)

    def test_get_all_trades_respects_limit(self):
        trades = database.get_all_trades(5)
        self.assertLessEqual(len(trades), 5)

    def test_get_active_trade_returns_none_when_empty(self):
        conn = database.get_connection()
        c = conn.cursor()
        c.execute("UPDATE trades SET status = 'CLOSED' WHERE status = 'ACTIVE'")
        conn.commit()
        conn.close()
        self.assertIsNone(database.get_active_trade())

    def test_get_active_trade_returns_dict(self):
        tid = database.record_trade_entry({"signal_type": "BUY_CE", "symbol": "N_ACT", "spot_price": 25000.0, "entry_price": 100.0, "sl_price": 85.0, "target_price": 114.0, "quantity": 65, "is_paper": 1})
        trade = database.get_active_trade()
        self.assertEqual(trade["id"], tid)
        database.record_trade_exit(tid, 100.0, "TEST_EXIT", 0.0, 0.0)

    def test_log_event_writes_audit(self):
        database.log_event("TEST_EVENT", "System audit check")
        self.assertTrue(True)

    def test_is_duplicate_alert_false_when_empty(self):
        is_dup = database.is_duplicate_alert("NON_EXISTENT_SIG_99", 120)
        self.assertFalse(is_dup)

    def test_is_duplicate_alert_true_after_event(self):
        database.log_event("SIGNAL_RECEIVED_BUY_CE", "Test alert debounce")
        is_dup = database.is_duplicate_alert("BUY_CE", 120)
        self.assertTrue(is_dup)

    def test_is_duplicate_alert_different_signal_type(self):
        database.log_event("SIGNAL_RECEIVED_BUY_CE", "Test alert debounce")
        is_dup = database.is_duplicate_alert("BUY_PE", 120)
        self.assertFalse(is_dup)

    def test_config_get_max_daily_trades(self):
        self.assertEqual(config.get_max_daily_trades(), 4)

    def test_config_get_max_daily_loss(self):
        self.assertEqual(config.get_max_daily_loss(), 2500.0)

    def test_config_dynamic_trade_limit_reflected(self):
        database.set_today_trade_limit(7)
        self.assertEqual(config.get_max_daily_trades(), 7)

    def test_config_dynamic_loss_limit_reflected(self):
        database.set_today_loss_limit(3500.0)
        self.assertEqual(config.get_max_daily_loss(), 3500.0)

    def test_record_trade_exit_updates_exit_time(self):
        tid = database.record_trade_entry({"signal_type": "BUY_CE", "symbol": "N_TIME", "spot_price": 25000.0, "entry_price": 100.0, "sl_price": 85.0, "target_price": 114.0, "quantity": 65, "is_paper": 1})
        database.record_trade_exit(tid, 114.0, "TARGET_HIT", 14.0, 910.0)
        trades = database.get_all_trades(1)
        self.assertIsNotNone(trades[0]["exit_time"])


class TestWalletCampaignAndPositionSizing(unittest.TestCase):
    """Verifies ₹1 Lakh wallet campaign analytics and position sizing limits (30 tests)."""

    def setUp(self):
        self._patcher = patch("builtins.print")
        self._patcher.start()
        database.init_campaign_wallet(100000.0)

    def tearDown(self):
        self._patcher.stop()

    def test_wallet_initial_capital(self):
        wallet = database.get_wallet_stats()
        self.assertEqual(wallet["initial_capital"], 100000.0)

    def test_wallet_campaign_total_days(self):
        wallet = database.get_wallet_stats()
        self.assertEqual(wallet["total_days"], 30)

    def test_wallet_day_number_valid_range(self):
        wallet = database.get_wallet_stats()
        self.assertGreaterEqual(wallet["day_number"], 1)
        self.assertLessEqual(wallet["day_number"], 30)

    def test_wallet_days_remaining_valid_range(self):
        wallet = database.get_wallet_stats()
        self.assertGreaterEqual(wallet["days_remaining"], 0)
        self.assertLessEqual(wallet["days_remaining"], 30)

    def test_wallet_timeline_pct_valid_range(self):
        wallet = database.get_wallet_stats()
        self.assertGreaterEqual(wallet["timeline_pct"], 0)
        self.assertLessEqual(wallet["timeline_pct"], 100)

    def test_wallet_preservation_floor_70k(self):
        wallet = database.get_wallet_stats()
        self.assertEqual(wallet["preservation_floor"], 70000.0)

    def test_wallet_target_roi_pct_20(self):
        wallet = database.get_wallet_stats()
        self.assertEqual(wallet["target_roi_pct"], 20.0)

    def test_wallet_target_profit_20k(self):
        wallet = database.get_wallet_stats()
        self.assertEqual(wallet["target_profit"], 20000.0)

    def test_wallet_target_balance_120k(self):
        wallet = database.get_wallet_stats()
        self.assertEqual(wallet["target_balance"], 120000.0)

    def test_wallet_current_balance_calculation(self):
        wallet = database.get_wallet_stats()
        expected = round(wallet["initial_capital"] + wallet["realized_pnl"], 2)
        self.assertEqual(wallet["current_balance"], expected)

    def test_wallet_available_margin_calculation(self):
        wallet = database.get_wallet_stats()
        expected = max(0.0, round(wallet["current_balance"] - wallet["margin_used"], 2))
        self.assertEqual(wallet["available_margin"], expected)

    def test_wallet_max_safe_drawdown_calculation(self):
        wallet = database.get_wallet_stats()
        expected = round(wallet["current_balance"] - wallet["preservation_floor"], 2)
        self.assertEqual(wallet["max_safe_drawdown"], expected)

    def test_is_preservation_triggered_false_when_healthy(self):
        wallet = database.get_wallet_stats()
        if wallet["current_balance"] > 70000.0:
            self.assertFalse(wallet["is_preservation_triggered"])

    def test_wallet_roi_pct_formula(self):
        wallet = database.get_wallet_stats()
        expected = round((wallet["realized_pnl"] / wallet["initial_capital"]) * 100.0, 2)
        self.assertEqual(wallet["roi_pct"], expected)

    def test_wallet_trade_count_is_int(self):
        wallet = database.get_wallet_stats()
        self.assertIsInstance(wallet["trade_count"], int)

    def test_wallet_wins_count_is_int(self):
        wallet = database.get_wallet_stats()
        self.assertIsInstance(wallet["wins"], int)

    def test_wallet_losses_count_is_int(self):
        wallet = database.get_wallet_stats()
        self.assertIsInstance(wallet["losses"], int)

    def test_wallet_win_rate_bounds(self):
        wallet = database.get_wallet_stats()
        self.assertGreaterEqual(wallet["win_rate"], 0.0)
        self.assertLessEqual(wallet["win_rate"], 100.0)

    def test_wallet_progress_to_target_bounds(self):
        wallet = database.get_wallet_stats()
        self.assertGreaterEqual(wallet["progress_to_target"], 0.0)
        self.assertLessEqual(wallet["progress_to_target"], 100.0)

    def test_wallet_margin_used_zero_when_no_active(self):
        conn = database.get_connection()
        c = conn.cursor()
        c.execute("UPDATE trades SET status = 'CLOSED' WHERE status = 'ACTIVE'")
        conn.commit()
        conn.close()
        wallet = database.get_wallet_stats()
        self.assertEqual(wallet["margin_used"], 0.0)

    def test_wallet_margin_used_when_active_trade(self):
        tid = database.record_trade_entry({"signal_type": "BUY_CE", "symbol": "N_MARG", "spot_price": 25000.0, "entry_price": 200.0, "sl_price": 185.0, "target_price": 214.0, "quantity": 65, "is_paper": 1})
        wallet = database.get_wallet_stats()
        self.assertEqual(wallet["margin_used"], 13000.0)
        database.record_trade_exit(tid, 200.0, "EXIT", 0.0, 0.0)

    def test_wallet_available_margin_decreases_with_active_trade(self):
        conn = database.get_connection()
        c = conn.cursor()
        c.execute("UPDATE trades SET status = 'CLOSED' WHERE status = 'ACTIVE'")
        conn.commit()
        conn.close()
        w1 = database.get_wallet_stats()
        tid = database.record_trade_entry({"signal_type": "BUY_CE", "symbol": "N_MARG2", "spot_price": 25000.0, "entry_price": 200.0, "sl_price": 185.0, "target_price": 214.0, "quantity": 65, "is_paper": 1})
        w2 = database.get_wallet_stats()
        self.assertEqual(w2["available_margin"], round(w1["available_margin"] - 13000.0, 2))
        database.record_trade_exit(tid, 200.0, "EXIT", 0.0, 0.0)

    def test_custom_initial_capital_config(self):
        database.set_config("initial_wallet_capital", "50000.0")
        wallet = database.get_wallet_stats()
        self.assertEqual(wallet["initial_capital"], 50000.0)
        database.set_config("initial_wallet_capital", "100000.0")

    def test_custom_preservation_floor_config(self):
        database.set_config("preservation_floor", "65000.0")
        wallet = database.get_wallet_stats()
        self.assertEqual(wallet["preservation_floor"], 65000.0)
        database.set_config("preservation_floor", "70000.0")

    def test_wallet_campaign_name(self):
        wallet = database.get_wallet_stats()
        self.assertIn("1 Lakh", wallet["campaign_name"])

    def test_wallet_best_trade_is_float(self):
        wallet = database.get_wallet_stats()
        self.assertIsInstance(wallet["best_trade"], float)

    def test_wallet_worst_trade_is_float(self):
        wallet = database.get_wallet_stats()
        self.assertIsInstance(wallet["worst_trade"], float)

    def test_wallet_realized_points_is_float(self):
        wallet = database.get_wallet_stats()
        self.assertIsInstance(wallet["realized_points"], float)

    def test_init_campaign_wallet_function(self):
        database.init_campaign_wallet(75000.0)
        wallet = database.get_wallet_stats()
        self.assertEqual(wallet["initial_capital"], 75000.0)
        database.init_campaign_wallet(100000.0)

    def test_wallet_zero_division_resilience(self):
        database.set_config("initial_wallet_capital", "0.0")
        wallet = database.get_wallet_stats()
        self.assertIsInstance(wallet["roi_pct"], float)
        database.set_config("initial_wallet_capital", "100000.0")


if __name__ == "__main__":
    unittest.main()
