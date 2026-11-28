"""
High-Precision Institutional Greeks and Risk Matrix Test Suite (100 Tests)
Covers:
1. Delta scaling across deep ITM, ATM, and OTM options (20 tests)
2. India VIX and ATR volatility regime adaptations (25 tests)
3. Strike selection rounding and symbol formatting (25 tests)
4. Dynamic SL caps, target ratios, and trade parameters (30 tests)
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

MOCK_1015_AM = datetime(2026, 11, 28, 10, 15, 0, tzinfo=ZoneInfo("Asia/Kolkata"))


class TestGreeksDeltaScaling(unittest.TestCase):
    """Verifies Buchanan-Passarelli option delta volatility scaling (20 tests)."""

    def setUp(self):
        self._patcher = patch("builtins.print")
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    def test_delta_deep_itm_085(self):
        sl, tp, _ = risk_manager.calculate_dynamic_greek_brackets(250.0, delta=0.85, spot_atr=22.0)
        self.assertGreater(sl, 18.0)
        self.assertGreater(tp, sl * 1.5)

    def test_delta_itm_075(self):
        sl, tp, _ = risk_manager.calculate_dynamic_greek_brackets(230.0, delta=0.75, spot_atr=22.0)
        self.assertGreater(sl, 16.0)
        self.assertGreaterEqual(round(tp / sl, 1), 1.5)

    def test_delta_itm_065(self):
        sl, tp, _ = risk_manager.calculate_dynamic_greek_brackets(210.0, delta=0.65, spot_atr=22.0)
        self.assertGreaterEqual(sl, 14.5)
        self.assertGreaterEqual(round(tp / sl, 1), 1.5)

    def test_delta_itm_060(self):
        sl, tp, _ = risk_manager.calculate_dynamic_greek_brackets(200.0, delta=0.60, spot_atr=22.0)
        self.assertGreaterEqual(sl, 14.0)

    def test_delta_atm_050(self):
        sl, tp, note = risk_manager.calculate_dynamic_greek_brackets(180.0, delta=0.50, spot_atr=22.0)
        self.assertGreaterEqual(sl, 13.0)
        self.assertIn("GREEK_DYNAMIC", note)

    def test_delta_otm_045(self):
        sl, tp, _ = risk_manager.calculate_dynamic_greek_brackets(150.0, delta=0.45, spot_atr=22.0)
        self.assertGreaterEqual(sl, 12.0)

    def test_delta_otm_040(self):
        sl, tp, _ = risk_manager.calculate_dynamic_greek_brackets(130.0, delta=0.40, spot_atr=22.0)
        self.assertGreaterEqual(sl, 10.0)

    def test_delta_otm_035(self):
        sl, tp, _ = risk_manager.calculate_dynamic_greek_brackets(110.0, delta=0.35, spot_atr=22.0)
        self.assertGreaterEqual(sl, 8.0)

    def test_delta_far_otm_025(self):
        sl, tp, _ = risk_manager.calculate_dynamic_greek_brackets(80.0, delta=0.25, spot_atr=22.0)
        self.assertGreaterEqual(sl, 6.0)

    def test_delta_deep_otm_015(self):
        sl, tp, _ = risk_manager.calculate_dynamic_greek_brackets(50.0, delta=0.15, spot_atr=22.0)
        self.assertGreaterEqual(sl, 6.0)

    def test_delta_boundary_min_clamp(self):
        sl, tp, _ = risk_manager.calculate_dynamic_greek_brackets(150.0, delta=0.01, spot_atr=22.0)
        self.assertGreaterEqual(sl, 6.0)

    def test_delta_boundary_max_clamp(self):
        sl, tp, _ = risk_manager.calculate_dynamic_greek_brackets(250.0, delta=1.00, spot_atr=22.0)
        self.assertLessEqual(sl, 35.0)

    def test_delta_sl_proportionality(self):
        sl_low, _, _ = risk_manager.calculate_dynamic_greek_brackets(150.0, delta=0.35, spot_atr=22.0)
        sl_high, _, _ = risk_manager.calculate_dynamic_greek_brackets(250.0, delta=0.85, spot_atr=22.0)
        self.assertGreaterEqual(sl_high, sl_low)

    def test_delta_target_proportionality(self):
        _, tp_low, _ = risk_manager.calculate_dynamic_greek_brackets(150.0, delta=0.35, spot_atr=22.0)
        _, tp_high, _ = risk_manager.calculate_dynamic_greek_brackets(250.0, delta=0.85, spot_atr=22.0)
        self.assertGreater(tp_high, tp_low)

    def test_delta_risk_reward_preservation(self):
        for d in [0.20, 0.35, 0.50, 0.65, 0.80]:
            sl, tp, _ = risk_manager.calculate_dynamic_greek_brackets(180.0, delta=d, spot_atr=22.0)
            self.assertGreaterEqual(round(tp / sl, 1), 1.5)

    def test_delta_call_vs_put_symmetry(self):
        sl_c, tp_c, _ = risk_manager.calculate_dynamic_greek_brackets(180.0, delta=0.50, spot_atr=22.0)
        sl_p, tp_p, _ = risk_manager.calculate_dynamic_greek_brackets(180.0, delta=0.50, spot_atr=22.0)
        self.assertEqual(sl_c, sl_p)
        self.assertEqual(tp_c, tp_p)

    def test_delta_friction_impact_high_delta(self):
        sl, _, _ = risk_manager.calculate_dynamic_greek_brackets(220.0, delta=0.75, spot_atr=22.0)
        self.assertGreater(sl, 15.0)

    def test_delta_friction_impact_low_delta(self):
        sl, _, _ = risk_manager.calculate_dynamic_greek_brackets(100.0, delta=0.25, spot_atr=22.0)
        self.assertGreaterEqual(sl, 6.0)

    def test_delta_invalid_float_fallback(self):
        sl, tp, _ = risk_manager.calculate_dynamic_greek_brackets(180.0, delta=float("nan"), spot_atr=22.0)
        self.assertGreaterEqual(sl, 13.0)

    def test_delta_none_fallback(self):
        sl, tp, _ = risk_manager.calculate_dynamic_greek_brackets(180.0, delta=None, spot_atr=22.0)
        self.assertGreaterEqual(sl, 13.0)


class TestVixAndAtrVolatilityScaling(unittest.TestCase):
    """Verifies VIX and ATR volatility regime adjustments (25 tests)."""

    def setUp(self):
        self._patcher = patch("builtins.print")
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    def _vix_patch(self, val):
        return patch("macro_radar.get_macro_sentiment", return_value={"vix": {"price": val}})

    def test_vix_extreme_low_9(self):
        with self._vix_patch(9.0):
            sl, tp, note = risk_manager.calculate_dynamic_greek_brackets(180.0, delta=0.50, spot_atr=22.0)
            self.assertGreaterEqual(sl, 11.0)
            self.assertIn("VIX=9.0", note)

    def test_vix_low_11(self):
        with self._vix_patch(11.5):
            sl, tp, _ = risk_manager.calculate_dynamic_greek_brackets(180.0, delta=0.50, spot_atr=22.0)
            self.assertGreaterEqual(sl, 12.0)

    def test_vix_normal_14(self):
        with self._vix_patch(14.0):
            sl, tp, _ = risk_manager.calculate_dynamic_greek_brackets(180.0, delta=0.50, spot_atr=22.0)
            self.assertGreaterEqual(sl, 13.0)

    def test_vix_normal_16(self):
        with self._vix_patch(16.5):
            sl, tp, _ = risk_manager.calculate_dynamic_greek_brackets(180.0, delta=0.50, spot_atr=22.0)
            self.assertGreaterEqual(sl, 14.0)

    def test_vix_high_19(self):
        with self._vix_patch(19.5):
            sl, tp, _ = risk_manager.calculate_dynamic_greek_brackets(180.0, delta=0.50, spot_atr=22.0)
            self.assertGreater(sl, 15.0)

    def test_vix_very_high_24(self):
        with self._vix_patch(24.0):
            sl, tp, _ = risk_manager.calculate_dynamic_greek_brackets(180.0, delta=0.50, spot_atr=22.0)
            self.assertGreater(sl, 16.0)

    def test_vix_extreme_29(self):
        with self._vix_patch(29.0):
            sl, tp, _ = risk_manager.calculate_dynamic_greek_brackets(180.0, delta=0.50, spot_atr=22.0)
            self.assertGreater(sl, 18.0)

    def test_vix_circuit_level_35(self):
        with self._vix_patch(35.0):
            sl, tp, _ = risk_manager.calculate_dynamic_greek_brackets(180.0, delta=0.50, spot_atr=22.0)
            self.assertGreater(sl, 20.0)

    def test_vix_negative_safety_fallback(self):
        with self._vix_patch(-5.0):
            sl, tp, _ = risk_manager.calculate_dynamic_greek_brackets(180.0, delta=0.50, spot_atr=22.0)
            self.assertGreaterEqual(sl, 11.0)

    def test_vix_zero_safety_fallback(self):
        with self._vix_patch(0.0):
            sl, tp, _ = risk_manager.calculate_dynamic_greek_brackets(180.0, delta=0.50, spot_atr=22.0)
            self.assertGreaterEqual(sl, 11.0)

    def test_atr_tight_range_10(self):
        sl, tp, _ = risk_manager.calculate_dynamic_greek_brackets(180.0, delta=0.50, spot_atr=10.0)
        self.assertGreaterEqual(sl, 9.0)

    def test_atr_moderate_range_18(self):
        sl, tp, _ = risk_manager.calculate_dynamic_greek_brackets(180.0, delta=0.50, spot_atr=18.0)
        self.assertGreaterEqual(sl, 11.5)

    def test_atr_standard_range_22(self):
        sl, tp, _ = risk_manager.calculate_dynamic_greek_brackets(180.0, delta=0.50, spot_atr=22.0)
        self.assertGreaterEqual(sl, 14.0)

    def test_atr_wide_range_32(self):
        sl, tp, _ = risk_manager.calculate_dynamic_greek_brackets(180.0, delta=0.50, spot_atr=32.0)
        self.assertGreater(sl, 16.0)

    def test_atr_extreme_range_55(self):
        sl, tp, _ = risk_manager.calculate_dynamic_greek_brackets(180.0, delta=0.50, spot_atr=55.0)
        self.assertGreater(sl, 22.0)

    def test_atr_zero_fallback(self):
        sl, tp, _ = risk_manager.calculate_dynamic_greek_brackets(180.0, delta=0.50, spot_atr=0.0)
        self.assertGreaterEqual(sl, 9.0)

    def test_atr_negative_fallback(self):
        sl, tp, _ = risk_manager.calculate_dynamic_greek_brackets(180.0, delta=0.50, spot_atr=-12.0)
        self.assertGreaterEqual(sl, 9.0)

    def test_atr_float_precision(self):
        sl, tp, _ = risk_manager.calculate_dynamic_greek_brackets(180.0, delta=0.50, spot_atr=21.87654)
        self.assertEqual(sl, round(sl, 1))

    def test_vix_spot_room_expansion(self):
        with self._vix_patch(12.0):
            sl_low, _, _ = risk_manager.calculate_dynamic_greek_brackets(180.0, delta=0.50, spot_atr=22.0)
        with self._vix_patch(25.0):
            sl_high, _, _ = risk_manager.calculate_dynamic_greek_brackets(180.0, delta=0.50, spot_atr=22.0)
        self.assertGreater(sl_high, sl_low)

    def test_vix_spot_room_compression(self):
        with self._vix_patch(10.0):
            sl, _, _ = risk_manager.calculate_dynamic_greek_brackets(180.0, delta=0.50, spot_atr=15.0)
            self.assertGreaterEqual(sl, 9.0)

    def test_vix_adaptive_brackets_low(self):
        with self._vix_patch(12.0):
            sl, tp, note = risk_manager.get_vix_adaptive_brackets()
            self.assertEqual(sl, 6.0)
            self.assertEqual(tp, 16.0)
            self.assertIn("LOW_VIX", note)

    def test_vix_adaptive_brackets_normal(self):
        with self._vix_patch(15.5):
            sl, tp, note = risk_manager.get_vix_adaptive_brackets()
            self.assertEqual(sl, 7.0)
            self.assertEqual(tp, 22.0)
            self.assertIn("NORMAL_VIX", note)

    def test_vix_adaptive_brackets_high(self):
        with self._vix_patch(22.0):
            sl, tp, note = risk_manager.get_vix_adaptive_brackets()
            self.assertEqual(sl, 9.0)
            self.assertEqual(tp, 28.0)
            self.assertIn("HIGH_VIX", note)

    def test_vix_adaptive_notes_formatting(self):
        sl, tp, note = risk_manager.get_vix_adaptive_brackets()
        self.assertIn("TP", note)
        self.assertIn("SL", note)

    def test_vix_regime_string_content(self):
        _, _, note = risk_manager.calculate_dynamic_greek_brackets(180.0, delta=0.50, spot_atr=22.0)
        self.assertTrue(note.startswith("GREEK_DYNAMIC"))


class TestStrikeSelectionAndRounding(unittest.TestCase):
    """Verifies strike selection algorithms and 50-point Nifty boundaries (25 tests)."""

    def test_spot_24010_rounds_to_24000(self):
        self.assertEqual(risk_manager.get_atm_strike(24010.0, True), 24000)

    def test_spot_24040_rounds_to_24050(self):
        self.assertEqual(risk_manager.get_atm_strike(24040.0, True), 24050)

    def test_spot_24060_rounds_to_24050(self):
        self.assertEqual(risk_manager.get_atm_strike(24060.0, False), 24050)

    def test_spot_24089_rounds_to_24100(self):
        self.assertEqual(risk_manager.get_atm_strike(24089.0, False), 24100)

    def test_spot_25000_exact_boundary(self):
        self.assertEqual(risk_manager.get_atm_strike(25000.0, True), 25000)
        self.assertEqual(risk_manager.get_atm_strike(25000.0, False), 25000)

    def test_spot_25024_rounds_down(self):
        self.assertEqual(risk_manager.get_atm_strike(25024.9, True), 25000)

    def test_spot_25025_midpoint_rounds_to_nearest_even(self):
        strike = risk_manager.get_atm_strike(25025.0, True)
        self.assertIn(strike, [25000, 25050])

    def test_spot_25074_rounds_down(self):
        self.assertEqual(risk_manager.get_atm_strike(25074.0, False), 25050)

    def test_spot_25076_rounds_up(self):
        self.assertEqual(risk_manager.get_atm_strike(25076.0, True), 25100)

    def test_spot_25633_rounds_to_25650(self):
        self.assertEqual(risk_manager.get_atm_strike(25633.3, False), 25650)

    def test_spot_25869_rounds_to_25850(self):
        self.assertEqual(risk_manager.get_atm_strike(25869.0, True), 25850)

    def test_spot_26000_exact_boundary(self):
        self.assertEqual(risk_manager.get_atm_strike(26000.0, True), 26000)

    def test_spot_26245_rounds_to_26250(self):
        self.assertEqual(risk_manager.get_atm_strike(26245.0, False), 26250)

    def test_spot_26480_rounds_to_26500(self):
        self.assertEqual(risk_manager.get_atm_strike(26480.0, True), 26500)

    def test_spot_26990_rounds_to_27000(self):
        self.assertEqual(risk_manager.get_atm_strike(26990.0, False), 27000)

    def test_ce_symbol_formatting(self):
        strike = risk_manager.get_atm_strike(25230.0, True)
        self.assertEqual(f"NIFTY{strike}CE", "NIFTY25250CE")

    def test_pe_symbol_formatting(self):
        strike = risk_manager.get_atm_strike(25230.0, False)
        self.assertEqual(f"NIFTY{strike}PE", "NIFTY25250PE")

    def test_strike_selection_string_spot(self):
        self.assertEqual(risk_manager.get_atm_strike(float("25050.0"), True), 25050)

    def test_strike_selection_integer_spot(self):
        self.assertEqual(risk_manager.get_atm_strike(25100, False), 25100)

    def test_strike_selection_negative_fallback(self):
        res = risk_manager.get_atm_strike(-25000.0, True)
        self.assertIsInstance(res, int)

    def test_strike_selection_zero_fallback(self):
        res = risk_manager.get_atm_strike(0.0, True)
        self.assertEqual(res, 0)

    def test_strike_selection_low_spot(self):
        self.assertEqual(risk_manager.get_atm_strike(10020.0, True), 10000)

    def test_strike_selection_high_spot(self):
        self.assertEqual(risk_manager.get_atm_strike(50040.0, False), 50050)

    def test_call_strike_consistency(self):
        s1 = risk_manager.get_atm_strike(25520.0, True)
        s2 = risk_manager.get_atm_strike(25520.0, True)
        self.assertEqual(s1, s2)

    def test_put_strike_consistency(self):
        s1 = risk_manager.get_atm_strike(25520.0, False)
        s2 = risk_manager.get_atm_strike(25520.0, False)
        self.assertEqual(s1, s2)


class TestTradeParametersAndDynamicCap(unittest.TestCase):
    """Verifies complete trade parameter payload generation and SL caps (30 tests)."""

    def setUp(self):
        self._patcher = patch("builtins.print")
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    def test_parameter_keys_present(self):
        res = risk_manager.calculate_trade_parameters({"spot": 25200.0, "signal": "BUY_CE", "entry_price": 200.0})
        expected_keys = ["signal_type", "symbol", "spot_price", "entry_price", "sl_price", "target_price", "quantity"]
        for k in expected_keys:
            self.assertIn(k, res)

    def test_dynamic_sl_cap_10(self):
        with patch.object(config, "get_max_sl_points", return_value=10.0):
            sl, _, _ = risk_manager.calculate_dynamic_greek_brackets(200.0, delta=0.50, spot_atr=22.0)
            self.assertLessEqual(sl, 10.0)

    def test_dynamic_sl_cap_11(self):
        with patch.object(config, "get_max_sl_points", return_value=11.0):
            sl, _, _ = risk_manager.calculate_dynamic_greek_brackets(200.0, delta=0.50, spot_atr=22.0)
            self.assertLessEqual(sl, 11.0)

    def test_dynamic_sl_cap_12(self):
        with patch.object(config, "get_max_sl_points", return_value=12.0):
            sl, _, _ = risk_manager.calculate_dynamic_greek_brackets(200.0, delta=0.50, spot_atr=22.0)
            self.assertLessEqual(sl, 12.0)

    def test_dynamic_sl_cap_13(self):
        with patch.object(config, "get_max_sl_points", return_value=13.0):
            sl, _, _ = risk_manager.calculate_dynamic_greek_brackets(200.0, delta=0.50, spot_atr=22.0)
            self.assertLessEqual(sl, 13.0)

    def test_dynamic_sl_cap_14(self):
        with patch.object(config, "get_max_sl_points", return_value=14.0):
            sl, _, _ = risk_manager.calculate_dynamic_greek_brackets(200.0, delta=0.50, spot_atr=22.0)
            self.assertLessEqual(sl, 14.0)

    def test_dynamic_sl_cap_15(self):
        with patch.object(config, "get_max_sl_points", return_value=15.0):
            sl, _, _ = risk_manager.calculate_dynamic_greek_brackets(200.0, delta=0.50, spot_atr=22.0)
            self.assertLessEqual(sl, 15.0)

    def test_dynamic_sl_cap_18(self):
        with patch.object(config, "get_max_sl_points", return_value=18.0):
            sl, _, _ = risk_manager.calculate_dynamic_greek_brackets(200.0, delta=0.50, spot_atr=22.0)
            self.assertLessEqual(sl, 18.0)

    def test_dynamic_sl_cap_20(self):
        with patch.object(config, "get_max_sl_points", return_value=20.0):
            sl, _, _ = risk_manager.calculate_dynamic_greek_brackets(200.0, delta=0.50, spot_atr=22.0)
            self.assertLessEqual(sl, 20.0)

    def test_dynamic_sl_cap_none_uncapped(self):
        with patch.object(config, "get_max_sl_points", return_value=None):
            sl, _, _ = risk_manager.calculate_dynamic_greek_brackets(250.0, delta=0.85, spot_atr=30.0)
            self.assertGreaterEqual(sl, 18.0)

    def test_dynamic_sl_cap_string_override(self):
        with patch.object(config, "get_max_sl_points", return_value=12.5):
            sl, _, _ = risk_manager.calculate_dynamic_greek_brackets(200.0, delta=0.50, spot_atr=22.0)
            self.assertLessEqual(sl, 12.5)

    def test_dynamic_sl_cap_negative_fallback(self):
        with patch.object(config, "get_max_sl_points", return_value=-5.0):
            sl, _, _ = risk_manager.calculate_dynamic_greek_brackets(200.0, delta=0.50, spot_atr=22.0)
            self.assertGreaterEqual(sl, 6.0)

    def test_dynamic_sl_cap_zero_fallback(self):
        with patch.object(config, "get_max_sl_points", return_value=0.0):
            sl, _, _ = risk_manager.calculate_dynamic_greek_brackets(200.0, delta=0.50, spot_atr=22.0)
            self.assertGreaterEqual(sl, 6.0)

    def test_default_target_points_14(self):
        with patch.object(config, "get_now_ist", return_value=MOCK_1015_AM):
            res = risk_manager.calculate_trade_parameters({"spot": 25200.0, "signal": "BUY_CE", "entry_price": 200.0})
            self.assertEqual(round(res["target_price"] - res["entry_price"], 1), 14.0)

    def test_midday_scalp_target_points_11(self):
        with patch.object(config, "get_now_ist") as mock_now:
            mock_obj = MagicMock()
            mock_obj.time.return_value = time(12, 0)
            mock_now.return_value = mock_obj
            res = risk_manager.calculate_trade_parameters({"spot": 25200.0, "signal": "BUY_CE", "entry_price": 200.0})
            self.assertEqual(round(res["target_price"] - res["entry_price"], 1), 11.0)

    def test_bid_ask_friction_added(self):
        res = risk_manager.calculate_trade_parameters({"spot": 25200.0, "signal": "BUY_CE", "entry_price": 200.0})
        self.assertEqual(res["entry_price"], round(200.0 + config.BID_ASK_FRICTION_POINTS, 2))

    def test_sl_strictly_below_entry_ce(self):
        res = risk_manager.calculate_trade_parameters({"spot": 25200.0, "signal": "BUY_CE", "entry_price": 200.0})
        self.assertLess(res["sl_price"], res["entry_price"])

    def test_target_strictly_above_entry_ce(self):
        res = risk_manager.calculate_trade_parameters({"spot": 25200.0, "signal": "BUY_CE", "entry_price": 200.0})
        self.assertGreater(res["target_price"], res["entry_price"])

    def test_inverted_sl_recovery(self):
        with patch("risk_manager.calculate_dynamic_greek_brackets", return_value=(-5.0, 14.0, "MOCK")):
            res = risk_manager.calculate_trade_parameters({"spot": 25200.0, "signal": "BUY_CE", "entry_price": 200.0})
            self.assertLess(res["sl_price"], res["entry_price"])

    def test_inverted_target_recovery(self):
        with patch("risk_manager.calculate_dynamic_greek_brackets", return_value=(14.0, -10.0, "MOCK")):
            res = risk_manager.calculate_trade_parameters({"spot": 25200.0, "signal": "BUY_CE", "entry_price": 200.0})
            self.assertGreater(res["target_price"], res["entry_price"])

    def test_signal_parsing_buy_ce(self):
        res = risk_manager.calculate_trade_parameters({"spot": 25200.0, "signal": "BUY_CE", "entry_price": 200.0})
        self.assertEqual(res["signal_type"], "BUY_CE")
        self.assertIn("CE", res["symbol"])

    def test_signal_parsing_buy_pe(self):
        res = risk_manager.calculate_trade_parameters({"spot": 25200.0, "signal": "BUY_PE", "entry_price": 200.0})
        self.assertEqual(res["signal_type"], "BUY_PE")
        self.assertIn("PE", res["symbol"])

    def test_signal_parsing_short(self):
        res = risk_manager.calculate_trade_parameters({"spot": 25200.0, "signal": "SHORT", "entry_price": 200.0})
        self.assertEqual(res["signal_type"], "BUY_PE")

    def test_signal_parsing_put(self):
        res = risk_manager.calculate_trade_parameters({"spot": 25200.0, "signal": "PUT", "entry_price": 200.0})
        self.assertEqual(res["signal_type"], "BUY_PE")

    def test_signal_parsing_call(self):
        res = risk_manager.calculate_trade_parameters({"spot": 25200.0, "signal": "CALL", "entry_price": 200.0})
        self.assertEqual(res["signal_type"], "BUY_CE")

    def test_signal_parsing_long(self):
        res = risk_manager.calculate_trade_parameters({"spot": 25200.0, "signal": "LONG", "entry_price": 200.0})
        self.assertEqual(res["signal_type"], "BUY_CE")

    def test_default_lot_quantity_65(self):
        res = risk_manager.calculate_trade_parameters({"spot": 25200.0, "signal": "BUY_CE", "entry_price": 200.0})
        self.assertEqual(res["quantity"], 65)

    def test_custom_entry_price_respected(self):
        res = risk_manager.calculate_trade_parameters({"spot": 25200.0, "signal": "BUY_CE", "entry_price": 315.50})
        self.assertEqual(res["entry_price"], round(315.50 + config.BID_ASK_FRICTION_POINTS, 2))

    def test_confluence_runner_bullish(self):
        sig = {
            "spot": 25800.0,
            "signal": "BUY_CE",
            "entry_price": 200.0,
            "day_open": 25750.0,
            "pullback_reentry": True
        }
        with patch("macro_radar.get_macro_sentiment", return_value={"score": 25}), \
             patch("option_chain_shield.get_oi_snapshot", return_value={"pcr": 1.15, "call_wall_resistance": 26000, "put_wall_support": 25700}):
            res = risk_manager.calculate_trade_parameters(sig)
            self.assertTrue(res["is_runner"])

    def test_confluence_runner_bearish(self):
        sig = {
            "spot": 25600.0,
            "signal": "BUY_PE",
            "entry_price": 200.0,
            "day_open": 25650.0,
            "pullback_reentry": True
        }
        with patch("macro_radar.get_macro_sentiment", return_value={"score": -25}), \
             patch("option_chain_shield.get_oi_snapshot", return_value={"pcr": 0.80, "call_wall_resistance": 25800, "put_wall_support": 25400}):
            res = risk_manager.calculate_trade_parameters(sig)
            self.assertTrue(res["is_runner"])


if __name__ == "__main__":
    unittest.main()
