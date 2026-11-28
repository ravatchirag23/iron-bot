"""
Autonomous Native Market Scanner for Iron Bot
Calculates UT Bot Dual-Trigger & ATR Trailing Stop directly in pure Python.
Eliminates 100% dependency on external 3rd-party charting tools (GoCharting / TradingView).
Runs continuously on Render Cloud, detects 3m candle close flips, and auto-executes trades.
"""

import time
import logging
import os
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
if os.environ.get("IRON_BOT_INSECURE_SSL") == "1":
    _ssl_ctx.check_hostname = False
    _ssl_ctx.verify_mode = ssl.CERT_NONE


def _todays_open(candles_5m: List[Dict[str, Any]]) -> float:
    """Returns the open of the first 5m candle belonging to today's IST session, or 0.0 if absent."""
    if not candles_5m:
        return 0.0
    today_str = config.get_today_ist()
    for c in candles_5m:
        ts = c.get("time")
        if ts is None:
            continue
        if datetime.fromtimestamp(int(ts), config.IST_TIMEZONE).strftime("%Y-%m-%d") == today_str:
            return float(c["open"])
    return 0.0

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

class NativeMarketScanner:
    def __init__(
        self, 
        a_buy: float = 1.5, 
        c_buy: int = 10, 
        a_sell: float = 1.5, 
        c_sell: int = 10, 
        ema_len: int = 20, 
        use_ema_filter: bool = True
    ):
        # 100% Exact parameters matching ut-bot-options-ready.lipi in GoCharting
        self.a_buy = a_buy
        self.c_buy = c_buy
        self.a_sell = a_sell
        self.c_sell = c_sell
        self.ema_len = ema_len
        self.use_ema_filter = use_ema_filter
        self.last_candle_time = 0
        self.last_pullback_time = 0
        self.last_position = 0 # 1 = Long/CE, -1 = Short/PE
        self.is_running = False
        self._last_feed_alert_ts = 0.0

    def fetch_intraday_candles(self) -> List[Dict[str, float]]:
        """
        Fetches official 5m candles for NIFTY 50 (^NSEI) matching GoCharting 5m chart 1:1.
        Fetches a 5-day rolling window to guarantee warm EMA(20) and ATR(10) buffers starting
        at 09:15 AM every session, eliminating morning cold-start blindness.
        Zero external library dependencies.
        """
        url = "https://query1.finance.yahoo.com/v8/finance/chart/%5ENSEI?interval=5m&range=5d"
        req = urllib.request.Request(url, headers=HEADERS)
        try:
            with urllib.request.urlopen(req, context=_ssl_ctx, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            chart_res = (data.get("chart") or {}).get("result") or [{}]
            res = chart_res[0] if chart_res else {}
            times = res.get("timestamp")
            if not times:
                logger.warning("[SCANNER] No timestamp data returned by chart API")
                return []
            quotes_list = ((res.get("indicators") or {}).get("quote") or [{}])
            q = quotes_list[0] if quotes_list else {}
            opens = q.get("open") or []
            highs = q.get("high") or []
            lows = q.get("low") or []
            closes = q.get("close") or []

            candles = []
            for i in range(len(times)):
                t = times[i]
                o, h, l, c = opens[i], highs[i], lows[i], closes[i]
                if None in (o, h, l, c):
                    continue
                # If synthetic unclosed intra-bar tick (t % 300 != 0), update current candle live tick
                if t % 300 != 0:
                    if candles:
                        candles[-1]["close"] = round(float(c), 2)
                        candles[-1]["high"] = max(candles[-1]["high"], round(float(c), 2))
                        candles[-1]["low"] = min(candles[-1]["low"], round(float(c), 2))
                    continue
                candles.append({
                    "time": int(t),
                    "open": round(float(o), 2),
                    "high": round(float(h), 2),
                    "low": round(float(l), 2),
                    "close": round(float(c), 2)
                })

            return candles
        except Exception as e:
            logger.warning(f"[SCANNER] Failed to fetch live 5m candles: {e}")
            return []

    def evaluate_ut_bot(self, candles_5m: List[Dict[str, float]]) -> Tuple[int, float, Optional[Dict[str, Any]]]:
        """
        100% EXACT mathematical port of 'ut-bot-options-ready.lipi' from GoCharting:
        - a_buy = 1.5, c_buy = 10 (ATR Period 10, Multiplier 1.5)
        - a_sell = 1.5, c_sell = 10 (ATR Period 10, Multiplier 1.5)
        - 20 EMA Momentum Trend Filter (talib.ema)
        - Symmetrical Dual Independent Trails: trail_buy & trail_sell
        - Direct crossover logic:
            above_buy_cross = (close > trail_buy) and (close[1] <= trail_buy[1])
            below_sell_cross = (close < trail_sell) and (close[1] >= trail_sell[1])
        - Filtered confirmed alerts:
            buy_signal_confirmed = buy_signal_raw and posState <= 0
            sell_signal_confirmed = sell_signal_raw and posState >= 0
        Returns: (current_pos, latest_spot, new_signal_dict or None)
        """
        if len(candles_5m) < max(self.ema_len, self.c_buy, self.c_sell) + 2:
            return 0, 0.0, None

        n = len(candles_5m)

        # 1. 20 EMA Calculation (talib.ema)
        alpha = 2.0 / (self.ema_len + 1.0)
        ema = []
        for i in range(n):
            if i == 0:
                ema.append(candles_5m[0]["close"])
            else:
                ema.append(alpha * candles_5m[i]["close"] + (1.0 - alpha) * ema[-1])

        # 2. True Range & Welles Wilder ATR(10) (talib.atr)
        tr = []
        for i in range(n):
            c = candles_5m[i]
            if i == 0:
                tr.append(c["high"] - c["low"])
            else:
                prev_c = candles_5m[i-1]["close"]
                tr.append(max(c["high"] - c["low"], abs(c["high"] - prev_c), abs(c["low"] - prev_c)))

        atr_buy = []
        for i in range(n):
            if i == 0:
                atr_buy.append(tr[0])
            elif i < self.c_buy:
                atr_buy.append((atr_buy[-1] * i + tr[i]) / (i + 1))
            else:
                atr_buy.append((atr_buy[-1] * (self.c_buy - 1) + tr[i]) / float(self.c_buy))

        atr_sell = []
        for i in range(n):
            if i == 0:
                atr_sell.append(tr[0])
            elif i < self.c_sell:
                atr_sell.append((atr_sell[-1] * i + tr[i]) / (i + 1))
            else:
                atr_sell.append((atr_sell[-1] * (self.c_sell - 1) + tr[i]) / float(self.c_sell))

        # 3. UT Bot Dual Independent Trails (Exact GoCharting Lipi logic)
        trail_buy = [None] * n
        trail_sell = [None] * n

        for i in range(n):
            close = candles_5m[i]["close"]

            # Buy Trail
            n_loss_buy = self.a_buy * atr_buy[i]
            if i == 0 or trail_buy[i-1] is None:
                trail_buy[i] = close - n_loss_buy
            else:
                prev_tb = trail_buy[i-1]
                prev_close = candles_5m[i-1]["close"]
                if close > prev_tb and prev_close > prev_tb:
                    trail_buy[i] = max(prev_tb, close - n_loss_buy)
                elif close < prev_tb and prev_close < prev_tb:
                    trail_buy[i] = min(prev_tb, close + n_loss_buy)
                else:
                    trail_buy[i] = close - n_loss_buy if close > prev_tb else close + n_loss_buy

            # Sell Trail
            n_loss_sell = self.a_sell * atr_sell[i]
            if i == 0 or trail_sell[i-1] is None:
                trail_sell[i] = close + n_loss_sell
            else:
                prev_ts = trail_sell[i-1]
                prev_close = candles_5m[i-1]["close"]
                if close > prev_ts and prev_close > prev_ts:
                    trail_sell[i] = max(prev_ts, close - n_loss_sell)
                elif close < prev_ts and prev_close < prev_ts:
                    trail_sell[i] = min(prev_ts, close + n_loss_sell)
                else:
                    trail_sell[i] = close - n_loss_sell if close > prev_ts else close + n_loss_sell

        # 4. Reliable Direct Crossover Logic + Trend Filter + State Machine
        pos_state = 0
        new_signal = None

        # Confirmed Bar Close Execution (barstate.isconfirmed):
        # Prevents intra-bar repainting and mid-candle wick fakeouts.
        # A candle is confirmed when its 300-second window has completed.
        now_ts = time.time()
        eval_idx = n - 1 if (now_ts >= candles_5m[-1]["time"] + 290) else (n - 2 if n >= 2 else n - 1)
        eval_candle = candles_5m[eval_idx]
        candle_timestamp = eval_candle["time"]
        day_open = _todays_open(candles_5m)

        for i in range(1, n):
            close = candles_5m[i]["close"]
            prev_close = candles_5m[i-1]["close"]

            tb = trail_buy[i]
            prev_tb = trail_buy[i-1]
            ts = trail_sell[i]
            prev_ts = trail_sell[i-1]
            e = ema[i]
            prev_e = ema[i-1]

            trend_bull = not self.use_ema_filter or close > e
            trend_bear = not self.use_ema_filter or close < e

            above_buy_cross = (tb is not None and prev_tb is not None and close > tb and prev_close <= prev_tb)
            below_sell_cross = (ts is not None and prev_ts is not None and close < ts and prev_close >= prev_ts)

            ema_break_up = (e is not None and prev_e is not None and close > e and prev_close <= prev_e)
            ema_break_dn = (e is not None and prev_e is not None and close < e and prev_close >= prev_e)

            # Bullet-proof dual trigger (100% Exact match to ut-bot-clean.lipi):
            # 1. Trail crossover confirmed by Bull EMA trend, OR
            # 2. Bull EMA breakout while already riding above trailing stop
            buy_signal_raw = (above_buy_cross and trend_bull) or (self.use_ema_filter and ema_break_up and (tb is not None and close > tb))
            sell_signal_raw = (below_sell_cross and trend_bear) or (self.use_ema_filter and ema_break_dn and (ts is not None and close < ts))

            buy_signal_confirmed = buy_signal_raw and pos_state <= 0
            sell_signal_confirmed = sell_signal_raw and pos_state >= 0

            # Emit signal ONLY on confirmed closed bar!
            # The execution engine checks if already in trade to prevent duplicate entries
            if buy_signal_raw:
                pos_state = 1
                if i == eval_idx and candle_timestamp != self.last_candle_time:
                    self.last_candle_time = candle_timestamp
                    new_signal = {
                        "signal": "BUY_CE",
                        "spot": close,
                        "day_open": day_open,
                        "candle_open": candles_5m[i]["open"],
                        "candle_high": candles_5m[i]["high"],
                        "candle_low": candles_5m[i]["low"],
                        "candle_close": close,
                        "candle_range": round(candles_5m[i]["high"] - candles_5m[i]["low"], 2),
                        "timestamp": candle_timestamp,
                        "is_reversal": buy_signal_confirmed
                    }
            elif sell_signal_raw:
                pos_state = -1
                if i == eval_idx and candle_timestamp != self.last_candle_time:
                    self.last_candle_time = candle_timestamp
                    new_signal = {
                        "signal": "BUY_PE",
                        "spot": close,
                        "day_open": day_open,
                        "candle_open": candles_5m[i]["open"],
                        "candle_high": candles_5m[i]["high"],
                        "candle_low": candles_5m[i]["low"],
                        "candle_close": close,
                        "candle_range": round(candles_5m[i]["high"] - candles_5m[i]["low"], 2),
                        "timestamp": candle_timestamp,
                        "is_reversal": sell_signal_confirmed
                    }

        # 5. Trend Pullback Re-entry Engine (Preserves Trade Frequency in Strong Trends)
        # In established trends where UT Bot pos_state remains locked, allows high-probability
        # re-entries when price pulls back to the 20 EMA and confirms a bounce.
        if new_signal is None and eval_idx >= 2:
            c_curr = candles_5m[eval_idx]
            c_prev = candles_5m[eval_idx - 1]
            e_curr = ema[eval_idx]
            e_prev = ema[eval_idx - 1]
            tb_curr = trail_buy[eval_idx]
            ts_curr = trail_sell[eval_idx]
            
            # Check Bullish Pullback Bounce:
            if pos_state == 1 and tb_curr is not None and c_curr["close"] > tb_curr:
                pulled_to_ema = (c_prev["low"] <= e_prev + 12.0)
                bounced_green = (c_curr["close"] > c_curr["open"]) and (c_curr["close"] > e_curr)
                if pulled_to_ema and bounced_green and candle_timestamp != getattr(self, "last_pullback_time", 0) and candle_timestamp != self.last_candle_time:
                    self.last_pullback_time = candle_timestamp
                    new_signal = {
                        "signal": "BUY_CE",
                        "spot": c_curr["close"],
                        "day_open": day_open,
                        "candle_open": c_curr["open"],
                        "candle_high": c_curr["high"],
                        "candle_low": c_curr["low"],
                        "candle_close": c_curr["close"],
                        "candle_range": round(c_curr["high"] - c_curr["low"], 2),
                        "timestamp": candle_timestamp,
                        "is_reversal": False,
                        "pullback_reentry": True
                    }
                    logger.info(f"⚡ [TREND PULLBACK] Bullish 20 EMA bounce re-entry detected @ {c_curr['close']}")

            # Check Bearish Pullback Bounce:
            elif pos_state == -1 and ts_curr is not None and c_curr["close"] < ts_curr:
                pulled_to_ema = (c_prev["high"] >= e_prev - 12.0)
                rejected_red = (c_curr["close"] < c_curr["open"]) and (c_curr["close"] < e_curr)
                if pulled_to_ema and rejected_red and candle_timestamp != getattr(self, "last_pullback_time", 0) and candle_timestamp != self.last_candle_time:
                    self.last_pullback_time = candle_timestamp
                    new_signal = {
                        "signal": "BUY_PE",
                        "spot": c_curr["close"],
                        "day_open": day_open,
                        "candle_open": c_curr["open"],
                        "candle_high": c_curr["high"],
                        "candle_low": c_curr["low"],
                        "candle_close": c_curr["close"],
                        "candle_range": round(c_curr["high"] - c_curr["low"], 2),
                        "timestamp": candle_timestamp,
                        "is_reversal": False,
                        "pullback_reentry": True
                    }
                    logger.info(f"⚡ [TREND PULLBACK] Bearish 20 EMA rejection re-entry detected @ {c_curr['close']}")

        self.last_position = pos_state
        latest_spot = candles_5m[-1]["close"]
        return pos_state, latest_spot, new_signal

    def scan_and_execute_cycle(self):
        """Single autonomous scan cycle with atomic execution lock and feed-down retry."""
        candles = self.fetch_intraday_candles()
        if not candles:
            return

        prev_candle_time = self.last_candle_time
        prev_pullback_time = getattr(self, "last_pullback_time", 0)
        pos, spot, signal_data = self.evaluate_ut_bot(candles)
        if not signal_data:
            return

        # An authentic UT Bot flip occurred!
        sig_type = signal_data["signal"]
        logger.info(f"🎯 [AUTONOMOUS ENGINE TRIGGER] Processing native {sig_type} alert...")

        with paper_engine.engine.lock:
            # 1. Check if it's an EXIT command for an existing opposite trade
            active = database.get_active_trade()
            if active:
                active_is_ce = not risk_manager._is_put_signal(active["symbol"])
                signal_is_pe = risk_manager._is_put_signal(sig_type)
                if (active_is_ce and signal_is_pe) or (not active_is_ce and not signal_is_pe):
                    logger.info(f"Opposite signal {sig_type} detected for active position #{active['id']}. Forcing reversal exit at market option quote!")
                    paper_engine.engine.force_exit(exit_price=None, reason="UT_BOT_REVERSAL_EXIT", current_spot=spot)
                    # Position is now closed in DB, proceed to enter opposite direction!
                else:
                    logger.info(f"Already in active trade #{active['id']} on {active['symbol']}. Ignoring duplicate {sig_type}.")
                    return

            # 2. 14 Iron Armor Rules Validation
            is_approved, reason = risk_manager.validate_signal_armor(signal_data)
            if not is_approved:
                if "NO_LIVE_PREMIUM" in reason:
                    # Feed down: rewind bar dedup timestamps so next 15s cycle can retry once feed recovers
                    self.last_candle_time = prev_candle_time
                    self.last_pullback_time = prev_pullback_time
                    logger.warning(f"[SCANNER] Live feed unavailable for {sig_type}, will retry: {reason}")
                    now_ts = time.time()
                    if (now_ts - getattr(self, "_last_feed_alert_ts", 0.0)) >= 300.0:
                        self._last_feed_alert_ts = now_ts
                        telegram_notifier.notify_rejection(reason, signal_data)
                    return
                logger.warning(f"[SCANNER] Signal {sig_type} rejected by Iron Armor: {reason}")
                telegram_notifier.notify_rejection(reason, signal_data)
                return

            # 3. Calculate Trade Parameters
            try:
                trade_params = risk_manager.calculate_trade_parameters(signal_data)
            except risk_manager.PremiumUnavailableError as e:
                self.last_candle_time = prev_candle_time
                self.last_pullback_time = prev_pullback_time
                logger.warning(f"[SCANNER] Live feed lost before fill for {sig_type}, will retry: {e}")
                now_ts = time.time()
                if (now_ts - getattr(self, "_last_feed_alert_ts", 0.0)) >= 300.0:
                    self._last_feed_alert_ts = now_ts
                    telegram_notifier.notify_rejection(f"NO_LIVE_PREMIUM: {e}", signal_data)
                return

            # 4. Loosely Coupled Confluence Advisory
            oi_advisory = ""
            try:
                is_call = not risk_manager._is_put_signal(trade_params["signal_type"])
                strike = risk_manager.get_atm_strike(trade_params["spot_price"], is_call)
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
            if trade_id <= 0:
                logger.warning(f"[SCANNER] Skipped duplicate entry attempt for {sig_type} (trade_id={trade_id})")
                return
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
