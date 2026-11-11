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
    def __init__(self, key_val: float = 2.0, atr_period: int = 1):
        self.key_val = key_val
        self.atr_period = atr_period
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
        Computes UT Bot Trailing Stop & Detects fresh signals on the latest closed candle.
        Returns: (current_pos, latest_spot, new_signal_dict or None)
        """
        if len(candles_3m) < 5:
            return 0, 0.0, None

        # 1. True Range Calculation
        tr_list = []
        for i in range(len(candles_3m)):
            c = candles_3m[i]
            if i == 0:
                tr = c["high"] - c["low"]
            else:
                prev_c = candles_3m[i-1]["close"]
                tr = max(c["high"] - c["low"], abs(c["high"] - prev_c), abs(c["low"] - prev_c))
            tr_list.append(tr)

        # 2. UT Bot Trailing Stop State Machine
        stop = 0.0
        pos = 0
        latest_c3 = candles_3m[-1]
        candle_timestamp = latest_c3["time"]

        for i in range(len(candles_3m)):
            c = candles_3m[i]
            src = c["close"]
            tr = tr_list[i]
            n_loss = self.key_val * tr

            if i == 0:
                stop = src - n_loss
                continue

            prev_stop = stop
            if src > prev_stop:
                stop = max(src - n_loss, prev_stop)
            else:
                stop = min(src + n_loss, prev_stop)

            prev_pos = pos
            if src > stop:
                pos = 1
            elif src < stop:
                pos = -1

            # Detect flip on the very latest completed candle
            if i == len(candles_3m) - 1 and candle_timestamp != self.last_candle_time:
                self.last_candle_time = candle_timestamp
                
                # Check for position flip
                if pos != self.last_position and self.last_position != 0:
                    sig_type = "BUY_CE" if pos == 1 else "BUY_PE"
                    logger.info(f"⚡ [NATIVE UT BOT FLIP DETECTED] Signal={sig_type} Spot={src:.2f} CandleTime={candle_timestamp}")
                    
                    self.last_position = pos
                    return pos, src, {
                        "signal": sig_type,
                        "spot": src,
                        "candle_low": c["low"] if pos == 1 else c["high"],
                        "candle_range": round(c["high"] - c["low"], 2),
                        "timestamp": candle_timestamp
                    }
                
                self.last_position = pos

        latest_spot = latest_c3["close"]
        return pos, latest_spot, None

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
