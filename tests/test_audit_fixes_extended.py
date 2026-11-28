"""
Extended regression tests covering Combined Super-Patch fixes:
- Scanner today-only IST day_open, NO_LIVE_PREMIUM retry, current_spot reversal
- Word-boundary _PUT_RE / _CALL_RE, PremiumUnavailableError (no phantom 220/0.6 entries), stale/sim cache safety
- Non-self-debouncing SIGNAL_RAW vs SIGNAL_ACCEPTED, unique active trade DB index, date-accurate exit & fresh lock_system_today
- Monitor tick live spot check, 3-tick glitch persistence, live-mode feed-down pause, 15:15 square-off
- Multi-step target extension, non-starving sequential milestones, restart milestone recovery, DECEMBER symbol parsing
- HTTP auth (X-Iron-Secret), no CORS wildcard, 413 payload limit, EXIT safety, external is_test stripping, /health & /handshake
- GIFT Nifty % change vs spot-basis bug, dashboard >50 trade ledger aggregation & feed_ok, dotenv loader
"""

import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import dashboard
import database
import macro_radar
import native_scanner
import option_chain_shield
import paper_engine
import risk_manager
import webhook_server


class IsolatedAuditTestCase(unittest.TestCase):
    """Provides a clean isolated SQLite DB and resets module caches for each test."""

    def setUp(self):
        self._orig_db_env = os.environ.get("IRON_BOT_DB_PATH")
        self._orig_test_mode = os.environ.get("IRON_BOT_TEST_MODE")
        self._orig_secret_cfg = getattr(config, "WEBHOOK_SECRET", "")
        self._orig_secret_env = os.environ.get("IRON_BOT_SECRET")
        self._orig_sim_flag = getattr(config, "ALLOW_SIMULATED_ENTRY", False)

        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["IRON_BOT_DB_PATH"] = os.path.join(self._tmpdir.name, "audit_ext.db")
        database.init_db()
        os.environ["IRON_BOT_TEST_MODE"] = "1"
        config.WEBHOOK_SECRET = ""
        config.ALLOW_SIMULATED_ENTRY = False

        paper_engine.engine.active_trade = None
        paper_engine.engine.milestone_reached = 0
        paper_engine.engine.initial_sl_dist = 0.0
        paper_engine.engine.sync_state()

        option_chain_shield._cached_data = None
        option_chain_shield._last_fetch_time = 0.0
        macro_radar._cached_macro = None
        macro_radar._last_macro_time = 0.0
        webhook_server._glitch_state["trade_id"] = None
        webhook_server._glitch_state["count"] = 0
        webhook_server._feed_down_alert_state["ts"] = 0.0

    def tearDown(self):
        if self._orig_db_env is None:
            os.environ.pop("IRON_BOT_DB_PATH", None)
        else:
            os.environ["IRON_BOT_DB_PATH"] = self._orig_db_env
        config.WEBHOOK_SECRET = self._orig_secret_cfg
        config.ALLOW_SIMULATED_ENTRY = self._orig_sim_flag
        if self._orig_test_mode is None:
            os.environ.pop("IRON_BOT_TEST_MODE", None)
        else:
            os.environ["IRON_BOT_TEST_MODE"] = self._orig_test_mode
        if self._orig_secret_env is None:
            os.environ.pop("IRON_BOT_SECRET", None)
        else:
            os.environ["IRON_BOT_SECRET"] = self._orig_secret_env
        option_chain_shield._cached_data = None
        option_chain_shield._last_fetch_time = 0.0
        macro_radar._cached_macro = None
        macro_radar._last_macro_time = 0.0
        webhook_server._glitch_state["trade_id"] = None
        webhook_server._glitch_state["count"] = 0
        paper_engine.engine.active_trade = None
        paper_engine.engine.milestone_reached = 0
        paper_engine.engine.initial_sl_dist = 0.0
        paper_engine.engine.sync_state()
        self._tmpdir.cleanup()

    def _new_trade(self, entry=200.0, sl=190.0, target=214.0, runner=0, symbol="NIFTY25000CE", spot=25000.0):
        tid = database.record_trade_entry({
            "signal_type": "BUY_CE",
            "symbol": symbol,
            "spot_price": spot,
            "entry_price": entry,
            "sl_price": sl,
            "initial_sl_price": sl,
            "target_price": target,
            "quantity": 65,
            "is_runner": runner,
        })
        paper_engine.engine.milestone_reached = 0
        paper_engine.engine.sync_state()
        return tid


class TestScannerAndDirectionExtended(IsolatedAuditTestCase):
    def test_uses_todays_first_candle_not_5day_old(self):
        today = config.get_today_ist()
        ist = config.IST_TIMEZONE
        old = int(datetime.strptime("2020-01-01 09:15", "%Y-%m-%d %H:%M").replace(tzinfo=ist).timestamp())
        t0 = int(datetime.strptime(f"{today} 09:15", "%Y-%m-%d %H:%M").replace(tzinfo=ist).timestamp())
        candles = [
            {"time": old, "open": 24000.0, "high": 24010.0, "low": 23990.0, "close": 24005.0},
            {"time": t0, "open": 25123.0, "high": 25140.0, "low": 25110.0, "close": 25135.0},
            {"time": t0 + 300, "open": 25135.0, "high": 25150.0, "low": 25120.0, "close": 25145.0},
        ]
        self.assertEqual(native_scanner._todays_open(candles), 25123.0)

    def test_unknown_day_open_returns_zero_so_shield_skips(self):
        self.assertEqual(native_scanner._todays_open([{"time": 1577850300, "open": 24000.0}]), 0.0)
        self.assertEqual(native_scanner._todays_open([]), 0.0)

    def test_put_regex_avoids_open_spot_supertrend_false_matches(self):
        for ce_text in ("OPEN_LONG", "SPOT_BREAKOUT", "SUPERTREND_BUY", "BUY_CE", "CALL"):
            self.assertFalse(risk_manager._is_put_signal(ce_text), ce_text)
        for pe_text in ("BUY_PE", "NIFTY25200PE", "SHORT", "PUT", "SELL PUT"):
            self.assertTrue(risk_manager._is_put_signal(pe_text), pe_text)

    def test_scanner_rewinds_timestamp_on_no_live_premium_for_retry(self):
        scanner = native_scanner.NativeMarketScanner()
        scanner.last_candle_time = 100
        sample_sig = {
            "signal": "BUY_CE",
            "spot": 25200.0,
            "day_open": 25180.0,
            "candle_open": 25190.0,
            "candle_high": 25205.0,
            "candle_low": 25185.0,
            "candle_close": 25200.0,
            "candle_range": 20.0,
            "timestamp": 200,
            "is_reversal": True,
        }

        def fake_eval(_candles):
            scanner.last_candle_time = 200
            return 1, 25200.0, dict(sample_sig)

        with patch.object(scanner, "fetch_intraday_candles", return_value=[{"time": 200, "open": 1, "high": 2, "low": 1, "close": 2}] * 25), \
             patch.object(scanner, "evaluate_ut_bot", side_effect=fake_eval), \
             patch("risk_manager.validate_signal_armor", return_value=(False, "NO_LIVE_PREMIUM: Simulated fallback")), \
             patch("telegram_notifier.notify_rejection") as mock_notify:
            scanner._last_feed_alert_ts = 0.0
            scanner.scan_and_execute_cycle()
            self.assertEqual(scanner.last_candle_time, 100)
            self.assertEqual(mock_notify.call_count, 1)
            scanner.scan_and_execute_cycle()
            self.assertEqual(mock_notify.call_count, 1)

    def test_scanner_reversal_passes_current_spot_to_force_exit(self):
        self._new_trade(entry=200.0, sl=185.0, target=214.0, symbol="NIFTY25200CE", spot=25200.0)
        scanner = native_scanner.NativeMarketScanner()
        rev_sig = {
            "signal": "BUY_PE",
            "spot": 25120.0,
            "day_open": 25200.0,
            "candle_open": 25140.0,
            "candle_high": 25145.0,
            "candle_low": 25115.0,
            "candle_close": 25120.0,
            "candle_range": 30.0,
            "timestamp": 300,
            "is_reversal": True,
        }
        with patch.object(scanner, "fetch_intraday_candles", return_value=[{"time": 300, "open": 1, "high": 2, "low": 1, "close": 2}] * 25), \
             patch.object(scanner, "evaluate_ut_bot", return_value=(-1, 25120.0, rev_sig)), \
             patch.object(paper_engine.engine, "force_exit", wraps=paper_engine.engine.force_exit) as spy_exit, \
             patch("risk_manager.validate_signal_armor", return_value=(False, "Rejected after reversal")):
            scanner.scan_and_execute_cycle()
            spy_exit.assert_called_once()
            self.assertEqual(spy_exit.call_args.kwargs.get("current_spot"), 25120.0)


class TestPremiumAndCacheSafetyExtended(IsolatedAuditTestCase):
    def test_simulated_chain_gives_real_quote_and_spot_field(self):
        q = option_chain_shield.get_real_option_quote(25050.0, True)
        self.assertGreater(q["ltp"], 20.0)
        self.assertTrue(q["is_simulated"])
        self.assertEqual(q["spot"], 25050.0)

    def test_prod_rejects_simulated_or_stale_feed_for_entry(self):
        os.environ["IRON_BOT_TEST_MODE"] = "0"
        sim = option_chain_shield.get_simulated_live_oi_structure(25050.0)
        sim["spot"] = 25050.0
        option_chain_shield._cached_data = sim
        option_chain_shield._last_fetch_time = 1e18
        with self.assertRaises(risk_manager.PremiumUnavailableError):
            risk_manager.calculate_trade_parameters({"signal": "BUY_CE", "spot": 25050.0})

        ok, why = risk_manager.validate_signal_armor({"signal": "BUY_CE", "spot": 25050.0, "candle_range": 15.0})
        self.assertFalse(ok)
        self.assertIn("NO_LIVE_PREMIUM", why)

    def test_simulated_cache_refreshes_on_spot_drift_gt_25(self):
        os.environ["IRON_BOT_TEST_MODE"] = "0"
        with patch("option_chain_shield.fetch_live_nse_option_chain", return_value=None):
            s1 = option_chain_shield.get_oi_snapshot(25000.0)
            self.assertEqual(s1["spot"], 25000.0)
            s2 = option_chain_shield.get_oi_snapshot(25100.0)
            self.assertEqual(s2["spot"], 25100.0)

    def test_premium_bounds_enforced_blocks_0_6_and_600(self):
        now_mkt = datetime(2026, 11, 30, 10, 30, tzinfo=config.IST_TIMEZONE)
        with patch("config.get_now_ist", return_value=now_mkt):
            ok_low, why_low = risk_manager.validate_signal_armor({
                "signal": "BUY_CE", "spot": 25050.0, "entry_price": 0.6, "candle_range": 15.0, "is_test": False
            })
            self.assertFalse(ok_low)
            self.assertIn("sweet-spot bounds", why_low)

            ok_high, why_high = risk_manager.validate_signal_armor({
                "signal": "BUY_CE", "spot": 25050.0, "entry_price": 600.0, "candle_range": 15.0, "is_test": False
            })
            self.assertFalse(ok_high)
            self.assertIn("sweet-spot bounds", why_high)


class TestDatabaseAndDebounceExtended(IsolatedAuditTestCase):
    def test_raw_log_does_not_self_debounce_accepted_does(self):
        database.log_event("SIGNAL_RAW_BUY_CE", "{}")
        self.assertFalse(database.is_duplicate_alert("BUY_CE", 60))
        database.log_event("SIGNAL_ACCEPTED_BUY_CE", "{}")
        self.assertTrue(database.is_duplicate_alert("BUY_CE", 60))

    def test_lock_system_today_creates_row_if_missing(self):
        database.lock_system_today("Manual test lock")
        stats = database.get_today_stats()
        self.assertEqual(stats["is_locked"], 1)
        self.assertEqual(stats["lock_reason"], "Manual test lock")

    def test_record_trade_exit_updates_trades_own_date(self):
        with patch("config.get_today_ist", return_value="2026-11-27"):
            tid = self._new_trade(entry=200.0, sl=186.0, target=214.0)
        with patch("config.get_today_ist", return_value="2026-11-28"):
            database.record_trade_exit(tid, 214.0, "TARGET_HIT", 14.0, 910.0)
            stats_today = database.get_today_stats()
            self.assertEqual(stats_today["total_pnl_rupees"], 0.0)
        with patch("config.get_today_ist", return_value="2026-11-27"):
            stats_prev = database.get_today_stats()
            self.assertEqual(stats_prev["total_pnl_rupees"], 910.0)


class TestMonitorAndEngineExtended(IsolatedAuditTestCase):
    def _q(self, **kw):
        base = {
            "ltp": 226.0, "ask": 226.6, "bid": 225.4, "delta": 0.5,
            "spot": 25040.0, "is_simulated": False, "is_stale": False, "age_seconds": 1.0,
        }
        base.update(kw)
        return base

    def test_runner_not_blinded_by_frozen_entry_spot(self):
        self._new_trade(entry=200.0, sl=190.0, target=214.0, runner=1, spot=25000.0)
        now = datetime(2026, 11, 30, 11, 0, tzinfo=config.IST_TIMEZONE)
        with patch("option_chain_shield.get_option_quote_by_strike", return_value=self._q(ltp=227.0, spot=25040.0)):
            res = webhook_server.monitor_tick(now_ist=now)
            self.assertEqual(res["status"], "ACTIVE")
            self.assertGreater(database.get_active_trade()["target_price"], 214.0)

    def test_true_glitch_deferred_twice_then_accepted_on_3rd_tick(self):
        self._new_trade(entry=200.0, sl=190.0, target=214.0, spot=25000.0)
        now = datetime(2026, 11, 30, 11, 0, tzinfo=config.IST_TIMEZONE)
        with patch("option_chain_shield.get_option_quote_by_strike", return_value=self._q(ltp=120.0, spot=25001.0)):
            r1 = webhook_server.monitor_tick(now_ist=now)
            r2 = webhook_server.monitor_tick(now_ist=now)
            self.assertEqual(r1["status"], "GLITCH_DEFERRED")
            self.assertEqual(r2["status"], "GLITCH_DEFERRED")
            r3 = webhook_server.monitor_tick(now_ist=now)
            self.assertEqual(r3["status"], "CLOSED")

    def test_stale_sim_or_error_feed_pauses_monitor_in_prod(self):
        self._new_trade(entry=200.0, sl=190.0, target=214.0)
        now = datetime(2026, 11, 30, 11, 0, tzinfo=config.IST_TIMEZONE)
        os.environ["IRON_BOT_TEST_MODE"] = "0"
        for bad in ({"is_stale": True}, {"is_simulated": True}, {"error": "missing"}):
            with patch("option_chain_shield.get_option_quote_by_strike", return_value=self._q(ltp=100.0, **bad)):
                res = webhook_server.monitor_tick(now_ist=now)
                self.assertEqual(res["status"], "FEED_DOWN_SKIPPED")
                self.assertIsNotNone(database.get_active_trade())

    def test_square_off_triggers_at_1515(self):
        self._new_trade()
        now = datetime(2026, 11, 30, 15, 16, tzinfo=config.IST_TIMEZONE)
        with patch("option_chain_shield.get_option_quote_by_strike", return_value=self._q(ltp=201.0, bid=200.5)):
            res = webhook_server.monitor_tick(now_ist=now)
            self.assertEqual(res["status"], "CLOSED")
            self.assertEqual(res["reason"], "15:15_PM_AUTO_SQUARE_OFF")
            self.assertIsNone(database.get_active_trade())

    def test_restart_keeps_runner_ratchet_alive(self):
        tid = self._new_trade(entry=200.0, sl=190.0, target=214.0, runner=1)
        paper_engine.engine._update_sl(tid, 208.0)
        e2 = paper_engine.PaperTradingEngine()
        self.assertEqual(e2.milestone_reached, 3)
        now = datetime(2026, 11, 30, 11, 0, tzinfo=config.IST_TIMEZONE)
        with patch("config.get_now_ist", return_value=now):
            e2.update_tick(222.0)
            self.assertGreaterEqual(database.get_active_trade()["sl_price"], 211.0)
            e2.update_tick(228.0)
            self.assertGreaterEqual(database.get_active_trade()["sl_price"], 217.0)

    def test_multistep_target_extension_and_non_starving_milestones(self):
        self._new_trade(entry=200.0, sl=186.0, target=250.0, runner=1)
        now = datetime(2026, 11, 30, 11, 0, tzinfo=config.IST_TIMEZONE)
        with patch("config.get_now_ist", return_value=now):
            paper_engine.engine.update_tick(225.0)
            self.assertEqual(paper_engine.engine.milestone_reached, 3)
            self.assertEqual(database.get_active_trade()["sl_price"], 215.0)

        # Close previous trade before opening next trade (partial unique index allows 1 ACTIVE row)
        paper_engine.engine.force_exit(225.0, "DONE")
        self._new_trade(entry=200.0, sl=186.0, target=214.0, runner=1, symbol="NIFTY25100CE")
        with patch("config.get_now_ist", return_value=now):
            paper_engine.engine.update_tick(236.0)
            tr = database.get_active_trade()
            self.assertGreater(tr["target_price"], 236.0)
            self.assertEqual(tr["sl_price"], 231.0)

    def test_only_one_active_trade_possible_via_unique_index(self):
        self._new_trade()
        tid = paper_engine.engine.process_entry({
            "signal_type": "BUY_CE", "symbol": "NIFTY25100CE", "spot_price": 25100.0,
            "entry_price": 210.0, "sl_price": 200.0, "target_price": 224.0, "quantity": 65,
        })
        self.assertEqual(tid, -1)
        with database.db_session() as conn:
            n = conn.execute("SELECT COUNT(*) FROM trades WHERE status='ACTIVE'").fetchone()[0]
        self.assertEqual(n, 1)

    def test_december_pe_symbol_not_parsed_as_ce_on_exit(self):
        tid = self._new_trade(entry=200.0, sl=186.0, target=214.0, symbol="NIFTY25200PE (24 DECEMBER)", spot=25200.0)
        trade = database.get_active_trade()
        with patch("option_chain_shield.get_option_quote_by_strike", return_value={"ltp": 210.0, "bid": 209.4}) as mock_q:
            px = paper_engine.engine._resolve_option_exit_price(trade, fallback_spot=25150.0)
            self.assertEqual(px, 209.4)
            self.assertEqual(mock_q.call_args.args[1], False)  # is_call MUST be False for PE


class TestHttpSecurityAndMacroDashboardExtended(IsolatedAuditTestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = webhook_server.ThreadingHTTPServer(("127.0.0.1", 0), webhook_server.WebhookHandler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        super().setUp()
        config.WEBHOOK_SECRET = "s3cret"

    def _req(self, method, path, body=None, secret="s3cret"):
        headers = {"Content-Type": "application/json"}
        if secret is not None:
            headers["X-Iron-Secret"] = secret
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read()), dict(resp.headers)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read()), dict(e.headers)

    def test_exit_without_active_trade_opens_nothing(self):
        s, j, _ = self._req("POST", "/webhook", {"signal": "EXIT"})
        self.assertEqual(s, 200)
        self.assertEqual(j["status"], "NO_ACTIVE_TRADE")
        self.assertIsNone(database.get_active_trade())

    def test_manual_exit_uses_live_quote_not_entry_price(self):
        self._new_trade(entry=200.0)
        with patch("option_chain_shield.get_option_quote_by_strike", return_value={"ltp": 212.0, "bid": 211.5, "ask": 212.5, "is_simulated": False}):
            s, j, _ = self._req("POST", "/webhook", {"signal": "EXIT"})
            self.assertEqual(s, 200)
            self.assertEqual(j["status"], "EXIT_EXECUTED")
            self.assertGreater(j["details"]["pnl_rupees"], 0.0)

    def test_secret_enforcement_and_no_cors_wildcard_and_413(self):
        self.assertEqual(self._req("POST", "/webhook", {"signal": "BUY_CE"}, secret="wrong")[0], 401)
        self.assertEqual(self._req("POST", "/reset_wallet", {}, secret=None)[0], 401)
        self.assertEqual(self._req("POST", "/simulate", {"signal": "BUY_CE"}, secret="")[0], 401)

        s, j, hdrs = self._req("GET", "/health", secret=None)
        self.assertEqual(s, 200)
        self.assertNotIn("Access-Control-Allow-Origin", hdrs)
        self.assertIn("db_ok", j)
        self.assertIn("monitor_alive", j)

        big = json.dumps({"signal": "BUY_CE", "pad": "x" * 70000}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/webhook",
            data=big,
            headers={"Content-Type": "application/json", "X-Iron-Secret": "s3cret"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 413)

    def test_external_is_test_ignored_and_garbage_rejected(self):
        after_hours = datetime(2026, 11, 30, 20, 0, tzinfo=config.IST_TIMEZONE)
        with patch("config.get_now_ist", return_value=after_hours):
            s1, j1, _ = self._req("POST", "/webhook", {
                "signal": "BUY_CE", "spot": 25050.0, "entry_price": 200.0, "is_test": True, "candle_range": 15.0
            })
            self.assertEqual(s1, 400)
            self.assertIn("Market hours", j1["reason"])

        s2, j2, _ = self._req("POST", "/webhook", {"signal": "OPEN PRICE UPDATE"})
        self.assertEqual(s2, 400)
        self.assertIn("Unrecognized", j2["reason"])

    def test_handshake_includes_angel_and_dhan(self):
        with patch("dhan_connector.run_8am_handshake", return_value={"connected": False, "broker": "dhan"}), \
             patch("angel_connector.run_8am_handshake", return_value={"connected": False, "status": "DISCONNECTED"}):
            s, j, _ = self._req("GET", "/handshake", secret=None)
            self.assertEqual(s, 200)
            self.assertIn("dhan", j)
            self.assertIn("angel", j)

    def test_gift_nifty_basis_premium_does_not_inflate_macro_score(self):
        os.environ["IRON_BOT_TEST_MODE"] = "0"
        with patch("macro_radar.fetch_gift_nifty", return_value=(25150.0, 0.0)), \
             patch("macro_radar.fetch_yahoo_quote", side_effect=[
                 (44000.0, 0.0), (19000.0, 0.0), (75.0, 0.0), (84.0, 0.0), (17.0, 0.0), (25000.0, 0.0)
             ]):
            macro = macro_radar.get_macro_sentiment(force=True)
            self.assertEqual(macro["score"], 0)
            self.assertTrue(any("Flat" in f for f in macro["factors"]))

    def test_macro_test_mode_has_change_and_change_pct(self):
        os.environ["IRON_BOT_TEST_MODE"] = "1"
        m = macro_radar.get_macro_sentiment(force=True)
        for key in ("gift_nifty", "dow", "nasdaq", "crude", "usdinr", "vix", "nifty"):
            self.assertIn("change", m[key])
            self.assertIn("change_pct", m[key])

    def test_dashboard_full_ledger_over_50_trades_and_feed_ok(self):
        for i in range(55):
            tid = self._new_trade(symbol=f"NIFTY25000CE_{i}")
            database.record_trade_exit(tid, 205.0, "TARGET_HIT", 5.0, 325.0)
        self._new_trade(symbol="NIFTY25000CE_ACTIVE", entry=200.0)
        state = dashboard.get_live_dashboard_state()
        self.assertEqual(state["ledger_summary"]["total_trades"], 55)
        self.assertIn("feed_ok", state["active_trade"])

    def test_dotenv_loader_populates_missing_without_overriding(self):
        env_file = os.path.join(self._tmpdir.name, ".env")
        with open(env_file, "w", encoding="utf-8") as fh:
            fh.write("# comment\nTEST_DOTENV_A='alpha'\nTEST_DOTENV_B=beta\n")
        os.environ["TEST_DOTENV_B"] = "existing"
        os.environ.pop("TEST_DOTENV_A", None)
        try:
            config._load_dotenv(env_file)
            self.assertEqual(os.environ.get("TEST_DOTENV_A"), "alpha")
            self.assertEqual(os.environ.get("TEST_DOTENV_B"), "existing")
        finally:
            os.environ.pop("TEST_DOTENV_A", None)
            os.environ.pop("TEST_DOTENV_B", None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
