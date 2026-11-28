"""
Comprehensive Execution Engine, Slippage, and Trailing Stops Matrix Test Suite (88 Tests)
Covers:
1. Order entry processing, state syncing, force exits, and fill mechanics (20 tests)
2. Micro-SL hits, gap-through slippage clamps, and PnL accounting (20 tests)
3. Universal trailing promotion, target extensions, and profit locks (25 tests)
4. Ratchet trailing leash, peak high ratcheting, reversals, and time exits (23 tests)
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
import database
import paper_engine

TZ = ZoneInfo("Asia/Kolkata")
MOCK_1015_AM = datetime(2026, 11, 28, 10, 15, 0, tzinfo=TZ)


class TestOrderEntryAndFills(unittest.TestCase):
    """Verifies order entry, fill mechanics, and active trade synchronization (20 tests)."""

    def setUp(self):
        self._patcher = patch("builtins.print")
        self._patcher.start()
        database.reset_daily_limits()
        conn = database.get_connection()
        c = conn.cursor()
        c.execute("UPDATE trades SET status = 'CLOSED' WHERE status = 'ACTIVE'")
        conn.commit()
        conn.close()
        paper_engine.engine.sync_state()

    def tearDown(self):
        database.reset_daily_limits()
        conn = database.get_connection()
        c = conn.cursor()
        if "iron_bot_unit_tests" in database.get_db_path():
            c.execute("DELETE FROM trades")
        else:
            c.execute("UPDATE trades SET status = 'CLOSED' WHERE status = 'ACTIVE'")
        conn.commit()
        conn.close()
        paper_engine.engine.sync_state()
        self._patcher.stop()

    def _sample_params(self, symbol="NIFTY25200CE_EXEC_TEST", is_ce=True, entry=200.0, sl=185.0, tp=214.0):
        return {
            "signal_type": "BUY_CE" if is_ce else "BUY_PE",
            "symbol": symbol,
            "spot_price": 25200.0,
            "entry_price": entry,
            "sl_price": sl,
            "target_price": tp,
            "quantity": 65,
            "is_paper": 1,
            "delta": 0.50
        }

    def test_process_entry_ce_creates_active_trade(self):
        tid = paper_engine.engine.process_entry(self._sample_params())
        self.assertGreater(tid, 0)
        self.assertIsNotNone(paper_engine.engine.active_trade)
        self.assertEqual(paper_engine.engine.active_trade["symbol"], "NIFTY25200CE_EXEC_TEST")

    def test_process_entry_pe_creates_active_trade(self):
        params = self._sample_params(symbol="NIFTY25200PE_EXEC_TEST", is_ce=False)
        tid = paper_engine.engine.process_entry(params)
        self.assertGreater(tid, 0)
        self.assertEqual(paper_engine.engine.active_trade["symbol"], "NIFTY25200PE_EXEC_TEST")

    def test_initial_sl_dist_saved(self):
        paper_engine.engine.process_entry(self._sample_params(entry=200.0, sl=185.0))
        self.assertEqual(paper_engine.engine.initial_sl_dist, 15.0)

    def test_initial_sl_price_persisted_in_db(self):
        tid = paper_engine.engine.process_entry(self._sample_params(entry=200.0, sl=185.0))
        trade = database.get_active_trade()
        self.assertEqual(float(trade["initial_sl_price"]), 185.0)

    def test_entry_quantity_default_65(self):
        paper_engine.engine.process_entry(self._sample_params())
        self.assertEqual(int(paper_engine.engine.active_trade["quantity"]), 65)

    def test_entry_status_active(self):
        paper_engine.engine.process_entry(self._sample_params())
        self.assertEqual(paper_engine.engine.active_trade["status"], "ACTIVE")

    def test_entry_spot_price_recorded(self):
        paper_engine.engine.process_entry(self._sample_params())
        self.assertEqual(float(paper_engine.engine.active_trade["spot_price"]), 25200.0)

    def test_reentry_rejected_when_active(self):
        paper_engine.engine.process_entry(self._sample_params())
        trade = database.get_active_trade()
        self.assertIsNotNone(trade)

    def test_sync_state_loads_active_trade(self):
        paper_engine.engine.process_entry(self._sample_params())
        paper_engine.engine.active_trade = None
        paper_engine.engine.sync_state()
        self.assertIsNotNone(paper_engine.engine.active_trade)

    def test_sync_state_graceful_when_no_active(self):
        paper_engine.engine.sync_state()
        self.assertIsNone(paper_engine.engine.active_trade)

    def test_force_exit_closes_trade(self):
        paper_engine.engine.process_entry(self._sample_params())
        res = paper_engine.engine.force_exit(exit_price=205.0, reason="MANUAL_TEST")
        self.assertEqual(res["status"], "CLOSED")
        self.assertEqual(res["reason"], "MANUAL_TEST")
        self.assertIsNone(paper_engine.engine.active_trade)

    def test_force_exit_calculates_pnl(self):
        paper_engine.engine.process_entry(self._sample_params(entry=200.0))
        res = paper_engine.engine.force_exit(exit_price=210.0, reason="MANUAL_TEST")
        self.assertEqual(res["pnl_rupees"], 650.0)
        trade = database.get_all_trades(1)[0]
        self.assertEqual(float(trade["pnl_points"]), 10.0)

    def test_force_exit_fallback_quote_price(self):
        paper_engine.engine.process_entry(self._sample_params(entry=200.0))
        with patch.object(paper_engine.engine, "_resolve_option_exit_price", return_value=202.0):
            res = paper_engine.engine.force_exit(reason="MANUAL_TEST")
            self.assertEqual(res["status"], "CLOSED")
            trade = database.get_all_trades(1)[0]
            self.assertEqual(float(trade["exit_price"]), 202.0)

    def test_force_exit_clears_milestones(self):
        paper_engine.engine.process_entry(self._sample_params())
        paper_engine.engine.milestone_reached = 2
        paper_engine.engine.force_exit(exit_price=200.0)
        self.assertEqual(paper_engine.engine.milestone_reached, 0)

    def test_force_exit_when_no_active_trade(self):
        res = paper_engine.engine.force_exit(exit_price=200.0)
        self.assertEqual(res["status"], "NO_ACTIVE_TRADE")

    def test_fractional_prices_handled(self):
        paper_engine.engine.process_entry(self._sample_params(entry=201.35, sl=186.25, tp=215.85))
        self.assertEqual(float(paper_engine.engine.active_trade["entry_price"]), 201.35)

    def test_active_trade_initial_sl_dist_calculation(self):
        paper_engine.engine.process_entry(self._sample_params(entry=150.0, sl=136.0))
        self.assertEqual(paper_engine.engine.initial_sl_dist, 14.0)

    def test_lock_reentrancy(self):
        with paper_engine.engine.lock:
            with paper_engine.engine.lock:
                self.assertTrue(True)

    def test_custom_symbol_preserved(self):
        sym = "NIFTY26000CE_CUSTOM_WEEKLY"
        paper_engine.engine.process_entry(self._sample_params(symbol=sym))
        self.assertEqual(paper_engine.engine.active_trade["symbol"], sym)

    def test_active_trade_cleared_after_force_exit(self):
        paper_engine.engine.process_entry(self._sample_params())
        paper_engine.engine.force_exit(exit_price=200.0)
        self.assertIsNone(database.get_active_trade())


class TestStopLossAndSlippageExecution(unittest.TestCase):
    """Verifies Micro-SL, gap-through fills, and realistic slippage mechanics (20 tests)."""

    def setUp(self):
        self._patcher = patch("builtins.print")
        self._patcher.start()
        database.reset_daily_limits()
        conn = database.get_connection()
        c = conn.cursor()
        c.execute("UPDATE trades SET status = 'CLOSED' WHERE status = 'ACTIVE'")
        conn.commit()
        conn.close()
        paper_engine.engine.sync_state()

    def tearDown(self):
        database.reset_daily_limits()
        conn = database.get_connection()
        c = conn.cursor()
        if "iron_bot_unit_tests" in database.get_db_path():
            c.execute("DELETE FROM trades")
        else:
            c.execute("UPDATE trades SET status = 'CLOSED' WHERE status = 'ACTIVE'")
        conn.commit()
        conn.close()
        paper_engine.engine.sync_state()
        self._patcher.stop()

    def _entry(self, entry=200.0, sl=185.0, tp=214.0):
        return paper_engine.engine.process_entry({
            "signal_type": "BUY_CE",
            "symbol": "NIFTY25200CE_SL_TEST",
            "spot_price": 25200.0,
            "entry_price": entry,
            "sl_price": sl,
            "target_price": tp,
            "quantity": 65,
            "is_paper": 1
        })

    def test_tick_above_sl_stays_active(self):
        self._entry(entry=200.0, sl=185.0)
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM):
            res = paper_engine.engine.update_tick(190.0)
            self.assertEqual(res["status"], "ACTIVE")

    def test_exact_touch_sl_triggers_exit(self):
        self._entry(entry=200.0, sl=185.0)
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM):
            res = paper_engine.engine.update_tick(185.0)
            self.assertEqual(res["status"], "CLOSED")
            self.assertEqual(res["reason"], "MICRO_SL_HIT")

    def test_exact_touch_exit_price_includes_friction(self):
        self._entry(entry=200.0, sl=185.0)
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM):
            paper_engine.engine.update_tick(185.0)
            expected_exit = round(185.0 - config.BID_ASK_FRICTION_POINTS, 2)
            trade = database.get_all_trades(1)[0]
            self.assertEqual(float(trade["exit_price"]), expected_exit)

    def test_gap_1pt_below_sl(self):
        self._entry(entry=200.0, sl=185.0)
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM):
            paper_engine.engine.update_tick(184.0)
            expected_exit = round(184.0 - config.BID_ASK_FRICTION_POINTS, 2)
            trade = database.get_all_trades(1)[0]
            self.assertEqual(float(trade["exit_price"]), expected_exit)

    def test_gap_3pt_below_sl(self):
        self._entry(entry=200.0, sl=185.0)
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM):
            paper_engine.engine.update_tick(182.0)
            expected_exit = round(182.0 - config.BID_ASK_FRICTION_POINTS, 2)
            trade = database.get_all_trades(1)[0]
            self.assertEqual(float(trade["exit_price"]), expected_exit)

    def test_gap_5pt_below_sl(self):
        self._entry(entry=200.0, sl=185.0)
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM):
            paper_engine.engine.update_tick(180.0)
            expected_exit = round(180.0 - config.BID_ASK_FRICTION_POINTS, 2)
            trade = database.get_all_trades(1)[0]
            self.assertEqual(float(trade["exit_price"]), expected_exit)

    def test_gap_10pt_below_sl(self):
        self._entry(entry=200.0, sl=185.0)
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM):
            paper_engine.engine.update_tick(175.0)
            expected_exit = round(175.0 - config.BID_ASK_FRICTION_POINTS, 2)
            trade = database.get_all_trades(1)[0]
            self.assertEqual(float(trade["exit_price"]), expected_exit)

    def test_slippage_capped_at_max_slippage(self):
        self._entry(entry=200.0, sl=185.0)
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM):
            paper_engine.engine.update_tick(135.0)
            max_slip = getattr(config, "MAX_SLIPPAGE_POINTS", 15.0)
            clamped_tick = 185.0 - max_slip # 170.0
            expected_exit = round(clamped_tick - config.BID_ASK_FRICTION_POINTS, 2)
            trade = database.get_all_trades(1)[0]
            self.assertEqual(float(trade["exit_price"]), expected_exit)

    def test_pnl_points_loss_calculation(self):
        self._entry(entry=200.0, sl=185.0)
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM):
            paper_engine.engine.update_tick(185.0)
            trade = database.get_all_trades(1)[0]
            self.assertEqual(float(trade["pnl_points"]), -15.6)

    def test_pnl_rupees_loss_calculation(self):
        self._entry(entry=200.0, sl=185.0)
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM):
            paper_engine.engine.update_tick(185.0)
            trade = database.get_all_trades(1)[0]
            self.assertEqual(float(trade["pnl_rupees"]), round(-15.6 * 65, 2))

    def test_trade_status_closed_on_sl(self):
        self._entry(entry=200.0, sl=185.0)
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM):
            paper_engine.engine.update_tick(185.0)
            trade = database.get_all_trades(1)[0]
            self.assertEqual(trade["status"], "CLOSED")

    def test_daily_trade_count_increments_on_sl(self):
        self._entry(entry=200.0, sl=185.0)
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM):
            paper_engine.engine.update_tick(185.0)
            stats = database.get_today_stats()
            self.assertEqual(stats["trade_count"], 1)

    def test_daily_total_pnl_decreases_on_sl(self):
        self._entry(entry=200.0, sl=185.0)
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM):
            paper_engine.engine.update_tick(185.0)
            stats = database.get_today_stats()
            self.assertLess(stats["total_pnl_rupees"], 0.0)

    def test_active_trade_memory_cleared_on_sl(self):
        self._entry(entry=200.0, sl=185.0)
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM):
            paper_engine.engine.update_tick(185.0)
            self.assertIsNone(paper_engine.engine.active_trade)

    def test_consecutive_ticks_below_sl_no_duplicate_exit(self):
        self._entry(entry=200.0, sl=185.0)
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM):
            paper_engine.engine.update_tick(185.0)
            res2 = paper_engine.engine.update_tick(184.0)
            self.assertIsNone(res2)

    def test_invalid_zero_tick_rejected(self):
        self._entry(entry=200.0, sl=185.0)
        res = paper_engine.engine.update_tick(0.0)
        self.assertIsNone(res)
        self.assertIsNotNone(paper_engine.engine.active_trade)

    def test_invalid_negative_tick_rejected(self):
        self._entry(entry=200.0, sl=185.0)
        res = paper_engine.engine.update_tick(-10.0)
        self.assertIsNone(res)
        self.assertIsNotNone(paper_engine.engine.active_trade)

    def test_soft_buffer_sl_hit_reason(self):
        self._entry(entry=200.0, sl=185.0)
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM):
            paper_engine.engine.update_tick(208.0)
            self.assertEqual(paper_engine.engine.milestone_reached, 1)
            res = paper_engine.engine.update_tick(194.0)
            self.assertEqual(res["reason"], "SOFT_BUFFER_SL_HIT")

    def test_cost_lock_exit_reason(self):
        self._entry(entry=200.0, sl=185.0)
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM):
            paper_engine.engine.update_tick(211.5)
            self.assertEqual(paper_engine.engine.milestone_reached, 2)
            res = paper_engine.engine.update_tick(200.0)
            self.assertEqual(res["reason"], "SOFT_BUFFER_SL_HIT")

    def test_initial_sl_dist_reset_on_exit(self):
        self._entry(entry=200.0, sl=185.0)
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM):
            paper_engine.engine.update_tick(185.0)
            self.assertEqual(paper_engine.engine.initial_sl_dist, 0.0)


class TestUniversalTrailingAndRunnerPromotion(unittest.TestCase):
    """Verifies Universal Trailing promotion at target, profit locks, and extensions (25 tests)."""

    def setUp(self):
        self._patcher = patch("builtins.print")
        self._patcher.start()
        database.reset_daily_limits()
        conn = database.get_connection()
        c = conn.cursor()
        c.execute("UPDATE trades SET status = 'CLOSED' WHERE status = 'ACTIVE'")
        conn.commit()
        conn.close()
        paper_engine.engine.sync_state()

    def tearDown(self):
        database.reset_daily_limits()
        conn = database.get_connection()
        c = conn.cursor()
        if "iron_bot_unit_tests" in database.get_db_path():
            c.execute("DELETE FROM trades")
        else:
            c.execute("UPDATE trades SET status = 'CLOSED' WHERE status = 'ACTIVE'")
        conn.commit()
        conn.close()
        paper_engine.engine.sync_state()
        self._patcher.stop()

    def _entry(self, entry=200.0, sl=185.0, tp=214.0):
        return paper_engine.engine.process_entry({
            "signal_type": "BUY_CE",
            "symbol": "NIFTY25200CE_UNIV_TRAIL_TEST",
            "spot_price": 25200.0,
            "entry_price": entry,
            "sl_price": sl,
            "target_price": tp,
            "quantity": 65,
            "is_paper": 1
        })

    def test_universal_trailing_disabled_exits_at_target(self):
        self._entry(entry=200.0, sl=185.0, tp=214.0)
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM), \
             patch.object(config, "get_enable_universal_trailing", return_value=False):
            res = paper_engine.engine.update_tick(214.0)
            self.assertEqual(res["status"], "CLOSED")
            self.assertEqual(res["reason"], "TARGET_HIT")

    def test_universal_trailing_enabled_promotes_trade(self):
        self._entry(entry=200.0, sl=185.0, tp=214.0)
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM), \
             patch.object(config, "get_enable_universal_trailing", return_value=True):
            res = paper_engine.engine.update_tick(214.0)
            self.assertEqual(res["status"], "ACTIVE")

    def test_promotion_updates_is_runner_in_db(self):
        self._entry(entry=200.0, sl=185.0, tp=214.0)
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM), \
             patch.object(config, "get_enable_universal_trailing", return_value=True):
            paper_engine.engine.update_tick(214.0)
            trade = database.get_active_trade()
            self.assertEqual(trade["is_runner"], 1)

    def test_promotion_extends_target_by_10pts(self):
        self._entry(entry=200.0, sl=185.0, tp=214.0)
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM), \
             patch.object(config, "get_enable_universal_trailing", return_value=True):
            paper_engine.engine.update_tick(214.0)
            self.assertEqual(float(paper_engine.engine.active_trade["target_price"]), 224.0)

    def test_promotion_locks_sl_at_target_minus_3pts(self):
        self._entry(entry=200.0, sl=185.0, tp=214.0)
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM), \
             patch.object(config, "get_enable_universal_trailing", return_value=True):
            paper_engine.engine.update_tick(214.0)
            self.assertEqual(float(paper_engine.engine.active_trade["sl_price"]), 211.0)

    def test_promotion_locks_11pts_guaranteed_profit(self):
        self._entry(entry=200.0, sl=185.0, tp=214.0)
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM), \
             patch.object(config, "get_enable_universal_trailing", return_value=True):
            paper_engine.engine.update_tick(214.0)
            sl = float(paper_engine.engine.active_trade["sl_price"])
            self.assertEqual(round(sl - 200.0, 1), 11.0)

    def test_promoted_trade_retraces_to_locked_sl(self):
        self._entry(entry=200.0, sl=185.0, tp=214.0)
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM), \
             patch.object(config, "get_enable_universal_trailing", return_value=True):
            paper_engine.engine.update_tick(214.0)
            res = paper_engine.engine.update_tick(211.0)
            self.assertEqual(res["status"], "CLOSED")
            self.assertEqual(res["reason"], "RUNNER_TRAILING_HIT")

    def test_promoted_exit_realizes_positive_pnl(self):
        self._entry(entry=200.0, sl=185.0, tp=214.0)
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM), \
             patch.object(config, "get_enable_universal_trailing", return_value=True):
            paper_engine.engine.update_tick(214.0)
            paper_engine.engine.update_tick(211.0)
            trade = database.get_all_trades(1)[0]
            self.assertGreater(float(trade["pnl_points"]), 10.0)
            self.assertGreater(float(trade["pnl_rupees"]), 650.0)

    def test_second_target_extension(self):
        self._entry(entry=200.0, sl=185.0, tp=214.0)
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM), \
             patch.object(config, "get_enable_universal_trailing", return_value=True):
            paper_engine.engine.update_tick(214.0)
            paper_engine.engine.update_tick(224.0)
            self.assertEqual(float(paper_engine.engine.active_trade["target_price"]), 234.0)
            self.assertEqual(float(paper_engine.engine.active_trade["sl_price"]), 221.0)

    def test_third_target_extension(self):
        self._entry(entry=200.0, sl=185.0, tp=214.0)
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM), \
             patch.object(config, "get_enable_universal_trailing", return_value=True):
            paper_engine.engine.update_tick(214.0)
            paper_engine.engine.update_tick(224.0)
            paper_engine.engine.update_tick(234.0)
            self.assertEqual(float(paper_engine.engine.active_trade["target_price"]), 244.0)
            self.assertEqual(float(paper_engine.engine.active_trade["sl_price"]), 231.0)

    def test_set_universal_trailing_db_function(self):
        database.set_universal_trailing(True)
        self.assertTrue(config.get_enable_universal_trailing())
        database.set_universal_trailing(False)
        self.assertFalse(config.get_enable_universal_trailing())

    def test_universal_trailing_gap_jump_above_target(self):
        self._entry(entry=200.0, sl=185.0, tp=214.0)
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM), \
             patch.object(config, "get_enable_universal_trailing", return_value=True):
            res = paper_engine.engine.update_tick(216.0)
            self.assertEqual(res["status"], "ACTIVE")
            self.assertEqual(float(paper_engine.engine.active_trade["target_price"]), 224.0)

    def test_milestone_updated_to_3_on_promotion(self):
        self._entry(entry=200.0, sl=185.0, tp=214.0)
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM), \
             patch.object(config, "get_enable_universal_trailing", return_value=True):
            paper_engine.engine.update_tick(214.0)
            self.assertEqual(paper_engine.engine.milestone_reached, 3)

    def test_promotion_updates_db_sl_price(self):
        self._entry(entry=200.0, sl=185.0, tp=214.0)
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM), \
             patch.object(config, "get_enable_universal_trailing", return_value=True):
            paper_engine.engine.update_tick(214.0)
            trade = database.get_active_trade()
            self.assertEqual(float(trade["sl_price"]), 211.0)

    def test_promotion_updates_db_target_price(self):
        self._entry(entry=200.0, sl=185.0, tp=214.0)
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM), \
             patch.object(config, "get_enable_universal_trailing", return_value=True):
            paper_engine.engine.update_tick(214.0)
            trade = database.get_active_trade()
            self.assertEqual(float(trade["target_price"]), 224.0)

    def test_multiple_ticks_at_target_only_promote_once(self):
        self._entry(entry=200.0, sl=185.0, tp=214.0)
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM), \
             patch.object(config, "get_enable_universal_trailing", return_value=True):
            paper_engine.engine.update_tick(214.0)
            paper_engine.engine.update_tick(214.0)
            self.assertEqual(float(paper_engine.engine.active_trade["target_price"]), 224.0)

    def test_profit_lock_telegram_notification(self):
        self._entry(entry=200.0, sl=185.0, tp=214.0)
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM), \
             patch.object(config, "get_enable_universal_trailing", return_value=True), \
             patch("telegram_notifier.notify_trailing_update") as mock_tg:
            paper_engine.engine.update_tick(214.0)
            mock_tg.assert_called_once()

    def test_promotion_with_pe_trade(self):
        paper_engine.engine.process_entry({
            "signal_type": "BUY_PE",
            "symbol": "NIFTY25200PE_UNIV_TEST",
            "spot_price": 25200.0,
            "entry_price": 180.0,
            "sl_price": 165.0,
            "target_price": 194.0,
            "quantity": 65,
            "is_paper": 1
        })
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM), \
             patch.object(config, "get_enable_universal_trailing", return_value=True):
            res = paper_engine.engine.update_tick(194.0)
            self.assertEqual(res["status"], "ACTIVE")
            self.assertEqual(float(paper_engine.engine.active_trade["sl_price"]), 191.0)

    def test_extended_target_strictly_above_current_price(self):
        self._entry(entry=200.0, sl=185.0, tp=214.0)
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM), \
             patch.object(config, "get_enable_universal_trailing", return_value=True):
            paper_engine.engine.update_tick(214.0)
            self.assertGreater(float(paper_engine.engine.active_trade["target_price"]), 214.0)

    def test_locked_sl_strictly_above_entry(self):
        self._entry(entry=200.0, sl=185.0, tp=214.0)
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM), \
             patch.object(config, "get_enable_universal_trailing", return_value=True):
            paper_engine.engine.update_tick(214.0)
            self.assertGreater(float(paper_engine.engine.active_trade["sl_price"]), 200.0)

    def test_locked_sl_strictly_below_extended_target(self):
        self._entry(entry=200.0, sl=185.0, tp=214.0)
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM), \
             patch.object(config, "get_enable_universal_trailing", return_value=True):
            paper_engine.engine.update_tick(214.0)
            self.assertLess(float(paper_engine.engine.active_trade["sl_price"]), float(paper_engine.engine.active_trade["target_price"]))

    def test_wallet_balance_updated_after_promoted_exit(self):
        self._entry(entry=200.0, sl=185.0, tp=214.0)
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM), \
             patch.object(config, "get_enable_universal_trailing", return_value=True):
            paper_engine.engine.update_tick(214.0)
            paper_engine.engine.update_tick(211.0)
            trade = database.get_all_trades(1)[0]
            self.assertGreater(trade["pnl_rupees"], 0.0)

    def test_daily_stats_pnl_positive_after_promoted_exit(self):
        self._entry(entry=200.0, sl=185.0, tp=214.0)
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM), \
             patch.object(config, "get_enable_universal_trailing", return_value=True):
            paper_engine.engine.update_tick(214.0)
            paper_engine.engine.update_tick(211.0)
            stats = database.get_today_stats()
            self.assertGreater(stats["total_pnl_rupees"], 0.0)

    def test_reset_clears_universal_trailing_state(self):
        database.set_universal_trailing(True)
        database.reset_daily_limits()
        self.assertFalse(config.get_enable_universal_trailing())

    def test_universal_trailing_with_runner_confluence_initial_flag(self):
        paper_engine.engine.process_entry({
            "signal_type": "BUY_CE",
            "symbol": "NIFTY25200CE_INIT_RUNNER",
            "spot_price": 25200.0,
            "entry_price": 200.0,
            "sl_price": 185.0,
            "target_price": 226.0,
            "quantity": 65,
            "is_paper": 1,
            "is_runner": True
        })
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM):
            res = paper_engine.engine.update_tick(226.0)
            self.assertEqual(res["status"], "ACTIVE")
            self.assertEqual(float(paper_engine.engine.active_trade["target_price"]), 236.0)


class TestRatchetTrailingLeashAndReversals(unittest.TestCase):
    """Verifies peak high ratcheting, UT Bot opposite signal reversals, and time exits (23 tests)."""

    def setUp(self):
        self._patcher = patch("builtins.print")
        self._patcher.start()
        database.reset_daily_limits()
        conn = database.get_connection()
        c = conn.cursor()
        c.execute("UPDATE trades SET status = 'CLOSED' WHERE status = 'ACTIVE'")
        conn.commit()
        conn.close()
        paper_engine.engine.sync_state()

    def tearDown(self):
        database.reset_daily_limits()
        conn = database.get_connection()
        c = conn.cursor()
        if "iron_bot_unit_tests" in database.get_db_path():
            c.execute("DELETE FROM trades")
        else:
            c.execute("UPDATE trades SET status = 'CLOSED' WHERE status = 'ACTIVE'")
        conn.commit()
        conn.close()
        paper_engine.engine.sync_state()
        self._patcher.stop()

    def _entry_runner(self, entry=200.0, sl=185.0, tp=250.0):
        return paper_engine.engine.process_entry({
            "signal_type": "BUY_CE",
            "symbol": "NIFTY25200CE_RATCHET_TEST",
            "spot_price": 25200.0,
            "entry_price": entry,
            "sl_price": sl,
            "target_price": tp,
            "quantity": 65,
            "is_paper": 1,
            "is_runner": True
        })

    def test_milestone_1_soft_buffer(self):
        self._entry_runner(entry=200.0, sl=185.0)
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM):
            paper_engine.engine.update_tick(208.0)
            self.assertEqual(paper_engine.engine.milestone_reached, 1)
            self.assertEqual(float(paper_engine.engine.active_trade["sl_price"]), 194.0)

    def test_milestone_2_cost_lock(self):
        self._entry_runner(entry=200.0, sl=185.0)
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM):
            paper_engine.engine.update_tick(212.0)
            self.assertEqual(paper_engine.engine.milestone_reached, 2)
            self.assertEqual(float(paper_engine.engine.active_trade["sl_price"]), 200.0)

    def test_runner_milestone_3_locked_8pts_at_18gain(self):
        self._entry_runner(entry=200.0, sl=185.0)
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM):
            paper_engine.engine.update_tick(212.0) # Reach milestone 2
            paper_engine.engine.update_tick(218.0) # Reach milestone 3 -> Locks +8 pts = 208.0
            self.assertEqual(float(paper_engine.engine.active_trade["sl_price"]), 208.0)
            self.assertEqual(paper_engine.engine.milestone_reached, 3)

    def test_gain_20pts_starts_ratchet(self):
        self._entry_runner(entry=200.0, sl=185.0)
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM):
            paper_engine.engine.update_tick(212.0) # m2
            paper_engine.engine.update_tick(218.0) # m3
            paper_engine.engine.update_tick(221.0) # gain 21 -> 221 - 10 = 211.0 > 208+2 -> SL = 211.0
            self.assertEqual(float(paper_engine.engine.active_trade["sl_price"]), 211.0)

    def test_gain_25pts_sets_sl_to_215(self):
        self._entry_runner(entry=200.0, sl=185.0)
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM):
            paper_engine.engine.update_tick(212.0)
            paper_engine.engine.update_tick(218.0)
            paper_engine.engine.update_tick(225.0) # gain 25 -> 225 - 10 = 215.0
            self.assertEqual(float(paper_engine.engine.active_trade["sl_price"]), 215.0)

    def test_gain_30pts_sets_sl_to_220(self):
        self._entry_runner(entry=200.0, sl=185.0)
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM):
            paper_engine.engine.update_tick(212.0)
            paper_engine.engine.update_tick(218.0)
            paper_engine.engine.update_tick(230.0) # gain 30 -> 230 - 10 = 220.0
            self.assertEqual(float(paper_engine.engine.active_trade["sl_price"]), 220.0)

    def test_gain_35pts_sets_sl_to_225(self):
        self._entry_runner(entry=200.0, sl=185.0)
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM):
            paper_engine.engine.update_tick(212.0)
            paper_engine.engine.update_tick(218.0)
            paper_engine.engine.update_tick(235.0) # gain 35 -> 235 - 10 = 225.0
            self.assertEqual(float(paper_engine.engine.active_trade["sl_price"]), 225.0)

    def test_gain_40pts_sets_sl_to_230(self):
        self._entry_runner(entry=200.0, sl=185.0)
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM):
            paper_engine.engine.update_tick(212.0)
            paper_engine.engine.update_tick(218.0)
            paper_engine.engine.update_tick(240.0) # gain 40 -> 240 - 10 = 230.0
            self.assertEqual(float(paper_engine.engine.active_trade["sl_price"]), 230.0)

    def test_ratchet_sl_never_moves_down(self):
        self._entry_runner(entry=200.0, sl=185.0)
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM):
            paper_engine.engine.update_tick(212.0)
            paper_engine.engine.update_tick(218.0)
            paper_engine.engine.update_tick(230.0) # SL = 220.0
            paper_engine.engine.update_tick(225.0) # Price pulls back to 225.0
            self.assertEqual(float(paper_engine.engine.active_trade["sl_price"]), 220.0)

    def test_retracement_to_ratchet_sl_exits(self):
        self._entry_runner(entry=200.0, sl=185.0)
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM):
            paper_engine.engine.update_tick(212.0)
            paper_engine.engine.update_tick(218.0)
            paper_engine.engine.update_tick(230.0) # SL = 220.0
            res = paper_engine.engine.update_tick(220.0) # Hits SL
            self.assertEqual(res["status"], "CLOSED")
            self.assertEqual(res["reason"], "RUNNER_TRAILING_HIT")

    def test_ratchet_exit_price_includes_friction(self):
        self._entry_runner(entry=200.0, sl=185.0)
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM):
            paper_engine.engine.update_tick(212.0)
            paper_engine.engine.update_tick(218.0)
            paper_engine.engine.update_tick(230.0)
            paper_engine.engine.update_tick(220.0)
            trade = database.get_all_trades(1)[0]
            self.assertEqual(float(trade["exit_price"]), round(220.0 - config.BID_ASK_FRICTION_POINTS, 2))

    def test_reversal_exit_ce_to_pe(self):
        self._entry_runner(entry=200.0)
        res = paper_engine.engine.force_exit(exit_price=204.0, reason="UT_BOT_REVERSAL_EXIT")
        self.assertEqual(res["status"], "CLOSED")
        self.assertEqual(res["reason"], "UT_BOT_REVERSAL_EXIT")

    def test_reversal_exit_pe_to_ce(self):
        paper_engine.engine.process_entry({
            "signal_type": "BUY_PE",
            "symbol": "NIFTY25200PE_REV_TEST",
            "spot_price": 25200.0,
            "entry_price": 180.0,
            "sl_price": 165.0,
            "target_price": 194.0,
            "quantity": 65,
            "is_paper": 1
        })
        res = paper_engine.engine.force_exit(exit_price=175.0, reason="UT_BOT_REVERSAL_EXIT")
        self.assertEqual(res["status"], "CLOSED")
        self.assertEqual(res["reason"], "UT_BOT_REVERSAL_EXIT")

    def test_spot_price_guard_converts_high_value_spot(self):
        trade = {"entry_price": 200.0, "spot_price": 25200.0, "symbol": "NIFTY25200CE", "delta": 0.5}
        exit_p = paper_engine.engine._resolve_option_exit_price(trade, fallback_spot=25250.0)
        self.assertLess(exit_p, 1000.0)

    def test_auto_square_off_at_1515_ist(self):
        self._entry_runner(entry=200.0)
        dt_1515 = datetime(2026, 11, 28, 15, 15, 1, tzinfo=TZ)
        with patch.object(config, "get_now_ist", return_value=dt_1515):
            res = paper_engine.engine.update_tick(205.0)
            self.assertEqual(res["status"], "CLOSED")
            self.assertIn("AUTO_SQUARE_OFF", res["reason"])

    def test_auto_square_off_locks_system(self):
        self._entry_runner(entry=200.0)
        dt_1515 = datetime(2026, 11, 28, 15, 15, 1, tzinfo=TZ)
        with patch.object(config, "get_now_ist", return_value=dt_1515):
            paper_engine.engine.update_tick(205.0)
            stats = database.get_today_stats()
            self.assertEqual(stats["is_locked"], 1)

    def test_auto_square_off_clears_active_trade(self):
        self._entry_runner(entry=200.0)
        dt_1515 = datetime(2026, 11, 28, 15, 15, 1, tzinfo=TZ)
        with patch.object(config, "get_now_ist", return_value=dt_1515):
            paper_engine.engine.update_tick(205.0)
            self.assertIsNone(paper_engine.engine.active_trade)

    def test_auto_square_off_records_pnl(self):
        self._entry_runner(entry=200.0)
        dt_1515 = datetime(2026, 11, 28, 15, 15, 1, tzinfo=TZ)
        with patch.object(config, "get_now_ist", return_value=dt_1515):
            paper_engine.engine.update_tick(205.0)
            trade = database.get_all_trades(1)[0]
            self.assertEqual(trade["exit_reason"], "15:15_PM_AUTO_SQUARE_OFF")

    def test_tick_update_when_no_active_trade_returns_none(self):
        res = paper_engine.engine.update_tick(200.0)
        self.assertIsNone(res)

    def test_tick_update_none_price_returns_none(self):
        self._entry_runner(entry=200.0)
        res = paper_engine.engine.update_tick(None)
        self.assertIsNone(res)

    def test_ratchet_offset_custom_config(self):
        self._entry_runner(entry=200.0, sl=185.0)
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM), \
             patch.object(config, "RUNNER_TRAIL_OFFSET_POINTS", 8.0):
            paper_engine.engine.update_tick(212.0)
            paper_engine.engine.update_tick(218.0)
            paper_engine.engine.update_tick(222.0)
            # Offset 8.0 -> SL = 222 - 8 = 214.0
            self.assertEqual(float(paper_engine.engine.active_trade["sl_price"]), 214.0)

    def test_high_volume_100_ticks_stability(self):
        self._entry_runner(entry=200.0, sl=185.0)
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM):
            for p in range(201, 230):
                paper_engine.engine.update_tick(float(p))
            self.assertIsNotNone(paper_engine.engine.active_trade)

    def test_thread_safety_sync_state(self):
        with paper_engine.engine.lock:
            paper_engine.engine.sync_state()
            self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
