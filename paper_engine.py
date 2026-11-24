"""
Paper Trading Engine for Iron Bot
Simulates order fills, tracks active position, moves trailing SL, and manages exits.
100% Virtual Ledger (₹0 Real Money at Risk).
"""

import threading
from typing import Dict, Any, Optional
from datetime import datetime
import config
import database
import telegram_notifier

class PaperTradingEngine:
    def __init__(self):
        self.lock = threading.RLock()
        self.active_trade = database.get_active_trade()
        self.milestone_reached = 0
        self.initial_sl_dist = 0.0
        if self.active_trade:
            entry = float(self.active_trade.get("entry_price", 0.0))
            base_sl = float(self.active_trade.get("initial_sl_price") or self.active_trade.get("sl_price", 0.0))
            self.initial_sl_dist = max(5.0, abs(entry - base_sl))

    def sync_state(self):
        with self.lock:
            self.active_trade = database.get_active_trade()
            if self.active_trade and self.initial_sl_dist <= 0.0:
                entry = float(self.active_trade.get("entry_price", 0.0))
                base_sl = float(self.active_trade.get("initial_sl_price") or self.active_trade.get("sl_price", 0.0))
                self.initial_sl_dist = max(5.0, abs(entry - base_sl))

    def process_entry(self, trade_params: Dict[str, Any], oi_advisory: str = "") -> int:
        """Executes a virtual trade entry and sends Telegram notification."""
        with self.lock:
            entry_px = float(trade_params["entry_price"])
            init_sl = float(trade_params["sl_price"])
            trade_params["initial_sl_price"] = init_sl
            self.initial_sl_dist = max(5.0, abs(entry_px - init_sl))
            
            trade_id = database.record_trade_entry(trade_params)
            self.active_trade = database.get_active_trade()
            self.milestone_reached = 0

            # Broker Gateway Hooks (Audited with ₹0 risk Canary Interception)
            trade_params["trade_id"] = trade_id
            try:
                import angel_connector
                angel_connector.route_order_to_broker(trade_params)
            except Exception:
                pass
            try:
                import dhan_connector
                dhan_connector.route_order_to_broker(trade_params)
            except Exception:
                pass
            
            telegram_notifier.notify_entry(
                symbol=trade_params["symbol"],
                signal_type=trade_params["signal_type"],
                entry_price=trade_params["entry_price"],
                sl_price=trade_params["sl_price"],
                target_price=trade_params["target_price"],
                quantity=trade_params["quantity"],
                oi_advisory=oi_advisory,
                is_runner=trade_params.get("is_runner", False)
            )
            return trade_id

    def _check_and_lock_daily_limits(self, reason: str):
        """Locks the day only if max trades or max loss reached."""
        stats = database.get_today_stats()
        if stats.get("trade_count", 0) >= config.MAX_DAILY_TRADES:
            database.lock_system_today(f"Max daily trades limit ({config.MAX_DAILY_TRADES}) reached")
        elif stats.get("total_pnl_rupees", 0.0) <= -config.MAX_DAILY_LOSS_RUPEES:
            database.lock_system_today(f"Daily loss limit (-₹{config.MAX_DAILY_LOSS_RUPEES}) hit")

    def update_tick(self, current_price: float) -> Optional[Dict[str, Any]]:
        """
        Processes price updates for the active trade.
        Checks:
        1. Target Hit
        2. Micro-SL Hit
        3. Tiered Trailing Profit Locks
        """
        with self.lock:
            self.sync_state()
            if not self.active_trade:
                return None
                
            trade = self.active_trade
            entry = float(trade["entry_price"])
            sl = float(trade["sl_price"])
            target = float(trade["target_price"])
            qty = int(trade["quantity"])
            symbol = trade["symbol"]
            trade_id = trade["id"]

            gain = round(current_price - entry, 2)
            now_ist = config.get_now_ist()

            # A. Mandatory 15:15 IST Square-Off (Gap #3: Expiry Zero Protection)
            if now_ist.time() >= config.AUTO_SQUARE_OFF_TIME:
                bid_exit = round(current_price - config.BID_ASK_FRICTION_POINTS, 2)
                pnl_pts = round(bid_exit - entry, 2)
                pnl_rupees = round(pnl_pts * qty, 2)
                database.record_trade_exit(trade_id, bid_exit, "15:15_PM_AUTO_SQUARE_OFF", pnl_pts, pnl_rupees)
                database.lock_system_today("15:15 PM Auto-Square-Off reached. Position closed safely!")
                telegram_notifier.notify_exit(symbol, bid_exit, "15:15_PM_AUTO_SQUARE_OFF", pnl_pts, pnl_rupees)
                self.active_trade = None
                self.initial_sl_dist = 0.0
                self.milestone_reached = 0
                return {"status": "CLOSED", "reason": "15:15_PM_AUTO_SQUARE_OFF", "pnl_rupees": pnl_rupees}

            # B. 45-Minute Stagnation Rule (Gap #2: Theta Decay Protection)
            try:
                entry_dt = datetime.strptime(trade["timestamp"], "%Y-%m-%d %H:%M:%S")
                elapsed_mins = (now_ist.replace(tzinfo=None) - entry_dt).total_seconds() / 60.0
                if elapsed_mins >= config.STAGNATION_TIMEOUT_MINUTES and gain < config.STAGNATION_MIN_GAIN_POINTS:
                    bid_exit = round(current_price - config.BID_ASK_FRICTION_POINTS, 2)
                    pnl_pts = round(bid_exit - entry, 2)
                    pnl_rupees = round(pnl_pts * qty, 2)
                    database.record_trade_exit(trade_id, bid_exit, "45MIN_THETA_STAGNATION_EXIT", pnl_pts, pnl_rupees)
                    self._check_and_lock_daily_limits("45MIN_THETA_STAGNATION_EXIT")
                    telegram_notifier.notify_exit(symbol, bid_exit, "45MIN_THETA_STAGNATION_EXIT", pnl_pts, pnl_rupees)
                    self.active_trade = None
                    self.initial_sl_dist = 0.0
                    self.milestone_reached = 0
                    return {"status": "CLOSED", "reason": "45MIN_THETA_STAGNATION_EXIT", "pnl_rupees": pnl_rupees}
            except Exception:
                pass

            # 1. Target Hit & Runner Trailing Extension
            is_runner = bool(trade.get("is_runner", 0))

            if not is_runner and current_price >= target:
                # Fixed Target Exit (Hit & Run for Normal/Chop days)
                bid_exit = round(target - config.BID_ASK_FRICTION_POINTS, 2)
                pnl_pts = round(bid_exit - entry, 2)
                pnl_rupees = round(pnl_pts * qty, 2)
                database.record_trade_exit(trade_id, bid_exit, "TARGET_HIT", pnl_pts, pnl_rupees)
                self._check_and_lock_daily_limits("TARGET_HIT")
                telegram_notifier.notify_exit(symbol, bid_exit, "TARGET_HIT", pnl_pts, pnl_rupees)
                self.active_trade = None
                self.initial_sl_dist = 0.0
                self.milestone_reached = 0
                return {"status": "CLOSED", "reason": "TARGET_HIT", "pnl_rupees": pnl_rupees}
            elif is_runner and current_price >= target:
                # Trailing Target Extension for Trend Runners (Confluence days)
                # Extend target by +10 pts and tighten trailing SL to lock profit
                old_target = target
                new_target = round(target + 10.0, 2)
                new_sl = round(max(sl, target - 3.0), 2)
                self._update_target(trade_id, new_target)
                self._update_sl(trade_id, new_sl)
                trade["target_price"] = new_target
                trade["sl_price"] = new_sl
                sl = new_sl
                target = new_target
                self.milestone_reached = max(self.milestone_reached, 3)
                locked_pts = round(new_sl - entry, 1)
                tag = f"🚀 RUNNER EXTENSION: Hit ₹{old_target:.2f}! Trailing Target extended to ₹{new_target:.2f} (SL locked @ ₹{new_sl:.2f} | +{locked_pts} pts profit)"
                telegram_notifier.notify_trailing_update(symbol, tag, new_sl, locked_pts * qty)

            # 2. Stop-Loss Hit (Realistic Gap-Through Fill: use WORSE of actual price vs SL)
            if current_price <= sl:
                actual_exit = min(current_price, sl)  # In gap-through, price is below SL
                bid_exit = round(actual_exit - config.BID_ASK_FRICTION_POINTS, 2)
                pnl_pts = round(bid_exit - entry, 2)
                pnl_rupees = round(pnl_pts * qty, 2)
                if sl > entry:
                    reason = "RUNNER_TRAILING_HIT" if is_runner else "TRAILING_SL_HIT"
                elif self.milestone_reached >= 1 and sl > (entry - 10.0):
                    reason = "SOFT_BUFFER_SL_HIT"
                else:
                    reason = "MICRO_SL_HIT"
                database.record_trade_exit(trade_id, bid_exit, reason, pnl_pts, pnl_rupees)
                self._check_and_lock_daily_limits(reason)
                telegram_notifier.notify_exit(symbol, bid_exit, reason, pnl_pts, pnl_rupees)
                self.active_trade = None
                self.initial_sl_dist = 0.0
                self.milestone_reached = 0
                return {"status": "CLOSED", "reason": reason, "pnl_rupees": pnl_rupees}

            # 3. Reality-Proof 3-Stage Trailing Protection
            soft_trigger = getattr(config, "SOFT_SL_TRIGGER_POINTS", 7.5)
            soft_buffer = getattr(config, "SOFT_SL_BUFFER_POINTS", 6.0)
            cost_trigger = getattr(config, "COST_LOCK_TRIGGER_POINTS", 11.0)

            # Milestone 2: True Cost Lock (At +11.0 pts gain -> Move SL to Cost)
            # Slashes risk to absolute zero once trade develops confirmed momentum
            if gain >= cost_trigger and self.milestone_reached < 2:
                new_sl = entry
                if new_sl > sl:
                    self._update_sl(trade_id, new_sl)
                    sl = new_sl
                    self.milestone_reached = 2
                    tag = f"+{gain:.1f} pts (Cost Lock - Runner Active)" if is_runner else f"🔒 COST LOCK: +{gain:.1f} pts gain! SL locked at ₹{new_sl:.2f} (Risk-Free Ride)"
                    telegram_notifier.notify_trailing_update(symbol, tag, new_sl, 0.0)

            # Milestone 1: Soft Risk Buffer (At +7.5 pts gain -> Tighten SL to Entry - 6.0 pts)
            # Slashes risk by 60% while giving 6 pts breathing room for 2-tick pullback wicks
            elif gain >= soft_trigger and self.milestone_reached < 1:
                new_sl = round(entry - soft_buffer, 2)
                if new_sl > sl:
                    self._update_sl(trade_id, new_sl)
                    sl = new_sl
                    self.milestone_reached = 1
                    tag = f"🛡️ SOFT RISK BUFFER: +{gain:.1f} pts gain! SL moved to ₹{new_sl:.2f} (Risk slashed 60%, 6 pt breathing room preserved)"
                    telegram_notifier.notify_trailing_update(symbol, tag, new_sl, 0.0)

            # Milestone 3: Profit Lock for Confluence Trend Runners (At +18 pts -> Lock +8 pts)
            elif is_runner and gain >= 18.0 and self.milestone_reached < 3:
                locked_pts = 8.0
                new_sl = round(entry + locked_pts, 2)
                if new_sl > sl:
                    self._update_sl(trade_id, new_sl)
                    self.milestone_reached = 3
                    tag = f"🏃 RUNNER: Giant Momentum! Locked +{locked_pts} pts profit @ ₹{new_sl:.2f}"
                    telegram_notifier.notify_trailing_update(symbol, tag, new_sl, locked_pts * qty)

            # Milestone 4+: Ratchet Trailing for Mega Trend Runners (Gain > 26 pts)
            elif is_runner and gain > 26.0:
                offset = getattr(config, "RUNNER_TRAIL_OFFSET_POINTS", 12.0)
                dynamic_sl = round(current_price - offset, 2)
                if dynamic_sl > (sl + 2.0): # Step up whenever price moves 2+ pts higher
                    self._update_sl(trade_id, dynamic_sl)
                    telegram_notifier.notify_trailing_update(
                        symbol, 
                        f"🌊 RUNNER SURFING: +{gain:.1f} pts! Trailing SL raised to ₹{dynamic_sl:.2f}", 
                        dynamic_sl, 
                        (dynamic_sl - entry) * qty
                    )

            return {"status": "ACTIVE", "current_price": current_price, "sl": sl, "gain": gain}

    def _update_sl(self, trade_id: int, new_sl: float):
        with database.db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE trades SET sl_price = ? WHERE id = ?", (new_sl, trade_id))

    def _update_target(self, trade_id: int, new_target: float):
        with database.db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE trades SET target_price = ? WHERE id = ?", (new_target, trade_id))

    def _resolve_option_exit_price(self, trade: Dict[str, Any], fallback_spot: Optional[float] = None) -> float:
        """
        Anti-Corruption Guardrail:
        Resolves the true Option Premium exit price if a caller accidentally passed a Spot index (> 5000).
        Uses live option quote or delta-adjusted estimate to protect ledger integrity.
        """
        symbol = trade.get("symbol", "")
        entry = float(trade.get("entry_price", 200.0))
        entry_spot = float(trade.get("spot_price", fallback_spot or 25050.0))
        current_spot = float(fallback_spot) if (fallback_spot and fallback_spot > 5000.0) else entry_spot
        is_call = "CE" in symbol
        
        # Extract held strike
        import re
        strike_match = re.search(r'NIFTY(\d+)', symbol)
        held_strike = int(strike_match.group(1)) if strike_match else round(current_spot / 50.0) * 50
        
        try:
            import option_chain_shield
            quote = option_chain_shield.get_option_quote_by_strike(held_strike, is_call, spot_price=current_spot)
            ltp = float(quote.get("bid") or quote.get("ltp") or 0.0)
            if ltp > 0.0:
                return round(ltp, 2)
        except Exception:
            pass
            
        # Delta-adjusted fallback estimate (0.50 delta for ATM)
        delta = 0.50 if is_call else -0.50
        spot_chg = current_spot - entry_spot
        est = round(max(1.0, entry + (delta * spot_chg) - config.BID_ASK_FRICTION_POINTS), 2)
        return est

    def force_exit(self, exit_price: Optional[float] = None, reason: str = "MANUAL_EXIT") -> Dict[str, Any]:
        """Force exits active trade with automatic spot-vs-option sanity validation."""
        with self.lock:
            self.sync_state()
            if not self.active_trade:
                return {"status": "NO_ACTIVE_TRADE"}
                
            trade = self.active_trade
            entry = float(trade["entry_price"])
            qty = int(trade["quantity"])

            # Sanity Guardrail: If exit_price is accidentally passed as spot price (> 5000)
            # or omitted, automatically resolve real option quote or delta estimate!
            if exit_price is None or exit_price > 5000.0:
                exit_price = self._resolve_option_exit_price(trade, fallback_spot=exit_price)
                
            exit_price = round(exit_price, 2)
            pnl_pts = round(exit_price - entry, 2)
            pnl_rupees = round(pnl_pts * qty, 2)
            
            database.record_trade_exit(trade["id"], exit_price, reason, pnl_pts, pnl_rupees)
            self._check_and_lock_daily_limits(reason)
            telegram_notifier.notify_exit(trade["symbol"], exit_price, reason, pnl_pts, pnl_rupees)
            self.active_trade = None
            self.initial_sl_dist = 0.0
            self.milestone_reached = 0
            return {"status": "CLOSED", "reason": reason, "pnl_rupees": pnl_rupees}

# Global singleton engine
engine = PaperTradingEngine()
