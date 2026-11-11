"""
Autonomous Native Market Scanner for Iron Bot
Calculates UT Bot Dual-Trigger & ATR Trailing Stop directly in pure Python.
Eliminates 100% dependency on external 3rd-party charting tools (GoCharting / TradingView).
Runs continuously on Render Cloud, detects 3m candle close flips, and auto-executes trades.
"""

import time
import logging
import urllib.request
import json
import ssl
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Dict, Any, List, Optional, Tuple

import config
import database
import risk_manager
import paper_engine
import telegram_notifier
import option_chain_shield

logger = logging.getLogger("IronBot.NativeScanner")

_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

class NativeMarketScanner:
    def __init__(self, sens: float = 1.5, atr_len: int = 2, ema_len: int = 20):
        # Exact parameters matching ut-bot-clean.lipi in GoCharting
        self.sens = sens
        self.atr_len = atr_len
        self.ema_len = ema_len
        self.last_candle_time = 0
        self.last_position = 0 # 1 = Long/CE, -1 = Short/PE
        self.is_running = False

    def fetch_intraday_candles(self) -> List[Dict[str, float]]:
        """
        Fetches 1m candles for NIFTY 50 (^NSEI) and aggregates into exact 3-minute bars.
        Zero external library dependencies.
        """
        url = "https://query1.finance.yahoo.com/v8/finance/chart/%5ENSEI?interval=1m&range=1d"
        req = urllib.request.Request(url, headers=HEADERS)
        try:
            with urllib.request.urlopen(req, context=_ssl_ctx, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            res = data["chart"]["result"][0]
            times = res["timestamp"]
            q = res["indicators"]["quote"][0]

            candles_3m = []
            bucket = []

            for i in range(len(times)):
                t = times[i]
                o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
                if None in (o, h, l, c):
                    continue
                bucket.append({"time": t, "open": o, "high": h, "low": l, "close": c})
                if len(bucket) == 3:
                    c3 = {
                        "time": bucket[-1]["time"],
                        "open": bucket[0]["open"],
                        "high": max(b["high"] for b in bucket),
                        "low": min(b["low"] for b in bucket),
                        "close": bucket[-1]["close"]
                    }
                    candles_3m.append(c3)
                    bucket = []

            return candles_3m
        except Exception as e:
            logger.warning(f"[SCANNER] Failed to fetch live candles: {e}")
            return []

    def evaluate_ut_bot(self, candles_3m: List[Dict[str, float]]) -> Tuple[int, float, Optional[Dict[str, Any]]]:
        """
        Exact 1:1 port of 'ut-bot-clean.lipi' from GoCharting:
        - 20 EMA Momentum Filter
        - ATR(2) with 1.5 Sensitivity Multiplier
        - Bullet-Proof Dual-Trigger (CrossUp + TrendBull OR EmaBreakUp + AboveTrail)
        Returns: (current_pos, latest_spot, new_signal_dict or None)
        """
        if len(candles_3m) < self.ema_len + 2:
            return 0, 0.0, None

        # 1. 20 EMA Calculation
        closes = [c["close"] for c in candles_3m]
        ema = [closes[0]]
        alpha = 2.0 / (self.ema_len + 1.0)
        for c in closes[1:]:
            ema.append(alpha * c + (1.0 - alpha) * ema[-1])

        # 2. True Range & ATR(2)
        tr_list = []
        for i in range(len(candles_3m)):
            c = candles_3m[i]
            if i == 0:
                tr = c["high"] - c["low"]
            else:
                prev_c = candles_3m[i-1]["close"]
                tr = max(c["high"] - c["low"], abs(c["high"] - prev_c), abs(c["low"] - prev_c))
            tr_list.append(tr)

        atr = []
        for i in range(len(tr_list)):
            if i < self.atr_len - 1:
                atr.append(sum(tr_list[:i+1]) / (i + 1))
            else:
                atr.append(sum(tr_list[i-self.atr_len+1:i+1]) / float(self.atr_len))

        # 3. UT Bot Single Unified Trail (Exact Lipi Logic)
        trail = []
        for i in range(len(candles_3m)):
            c = candles_3m[i]
            close = c["close"]
            n_loss = self.sens * atr[i]

            if i == 0:
                trail.append(close - n_loss)
                continue

            prev_trail = trail[-1]
            prev_close = candles_3m[i-1]["close"]

            if close > prev_trail and prev_close > prev_trail:
                new_trail = max(prev_trail, close - n_loss)
            elif close < prev_trail and prev_close < prev_trail:
                new_trail = min(prev_trail, close + n_loss)
            else:
                new_trail = close - n_loss if close > prev_trail else close + n_loss
            trail.append(new_trail)

        # 4. Bullet-Proof Dual-Trigger State Machine
        pos_state = 0
        latest_c3 = candles_3m[-1]
        candle_timestamp = latest_c3["time"]
        new_signal = None

        for i in range(1, len(candles_3m)):
            close = candles_3m[i]["close"]
            prev_close = candles_3m[i-1]["close"]
            t = trail[i]
            prev_t = trail[i-1]
            e = ema[i]
            prev_e = ema[i-1]

            trend_bull = close > e
            trend_bear = close < e

            cross_up = (close > t) and (prev_close <= prev_t)
            cross_dn = (close < t) and (prev_close >= prev_t)
            ema_break_up = (close > e) and (prev_close <= prev_e)
            ema_break_dn = (close < e) and (prev_close >= prev_e)

            buy_trigger = (cross_up and trend_bull) or (ema_break_up and (close > t))
            sell_trigger = (cross_dn and trend_bear) or (ema_break_dn and (close < t))

            buy_signal = buy_trigger and pos_state <= 0
            sell_signal = sell_trigger and pos_state >= 0

            if buy_signal:
                pos_state = 1
                if i == len(candles_3m) - 1 and candle_timestamp != self.last_candle_time:
                    self.last_candle_time = candle_timestamp
                    new_signal = {
                        "signal": "BUY_CE",
                        "spot": close,
                        "candle_low": candles_3m[i]["low"],
                        "candle_range": round(candles_3m[i]["high"] - candles_3m[i]["low"], 2),
                        "timestamp": candle_timestamp
                    }
            elif sell_signal:
                pos_state = -1
                if i == len(candles_3m) - 1 and candle_timestamp != self.last_candle_time:
                    self.last_candle_time = candle_timestamp
                    new_signal = {
                        "signal": "BUY_PE",
                        "spot": close,
                        "candle_low": candles_3m[i]["high"],
                        "candle_range": round(candles_3m[i]["high"] - candles_3m[i]["low"], 2),
                        "timestamp": candle_timestamp
                    }

        self.last_position = pos_state
        latest_spot = latest_c3["close"]
        return pos_state, latest_spot, new_signal

    def scan_and_execute_cycle(self):
        """Single autonomous scan cycle."""
        candles = self.fetch_intraday_candles()
        if not candles:
            return

        pos, spot, signal_data = self.evaluate_ut_bot(candles)
        if not signal_data:
            return

        # An authentic UT Bot flip occurred!
        sig_type = signal_data["signal"]
        logger.info(f"🎯 [AUTONOMOUS ENGINE TRIGGER] Processing native {sig_type} alert...")

        # 1. Check if it's an EXIT command for an existing trade
        active = database.get_active_trade()
        if active:
            active_is_ce = "CE" in active["symbol"]
            signal_is_pe = "PE" in sig_type
            if (active_is_ce and signal_is_pe) or (not active_is_ce and not signal_is_pe):
                logger.info(f"Opposite signal {sig_type} detected for active position #{active['id']}. Forcing exit!")
                paper_engine.engine.force_exit(spot, "UT_BOT_REVERSAL_EXIT")
                return

        # 2. 14 Iron Armor Rules Validation
        is_approved, reason = risk_manager.validate_signal_armor(signal_data)
        if not is_approved:
            logger.warning(f"[SCANNER] Signal {sig_type} rejected by Iron Armor: {reason}")
            telegram_notifier.notify_rejection(reason, signal_data)
            return

        # 3. Calculate Trade Parameters
        trade_params = risk_manager.calculate_trade_parameters(signal_data)

        # 4. Loosely Coupled Confluence Advisory
        oi_advisory = ""
        try:
            strike = risk_manager.get_atm_strike(trade_params["spot_price"], "BUY" in trade_params["signal_type"])
            oi_eval = option_chain_shield.evaluate_signal_confluence(
                trade_params["signal_type"], 
                trade_params["spot_price"], 
                strike
            )
            oi_advisory = oi_eval.get("advisory", "")
        except Exception as e:
            logger.warning(f"Option shield advisory bypassed safely: {e}")

        # 5. Execute via Paper Engine & Telegram Notification
        trade_id = paper_engine.engine.process_entry(trade_params, oi_advisory=oi_advisory)
        logger.info(f"✅ [AUTONOMOUS TRADE EXECUTED] Trade #{trade_id} {trade_params['symbol']} @ ₹{trade_params['entry_price']}")

    def start_scanner_thread(self):
        """Runs the native market scanner in a dedicated background daemon thread."""
        import threading
        if self.is_running:
            return
        self.is_running = True

        def loop():
            logger.info("🤖 Autonomous Native Market Scanner thread activated (100% In-House Engine).")
            while True:
                try:
                    now = config.get_now_ist()
                    # Only scan during Indian market hours
                    if 9 <= now.hour <= 15:
                        self.scan_and_execute_cycle()
                except Exception as e:
                    logger.warning(f"[SCANNER LOOP ERROR] {e}")
                time.sleep(15) # Poll every 15 seconds

        t = threading.Thread(target=loop, daemon=True)
        t.start()

# Global singleton scanner
scanner = NativeMarketScanner()
