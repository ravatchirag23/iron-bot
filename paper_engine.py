"""
Paper Trading Engine for Iron Bot
Simulates order fills, tracks active position, moves trailing SL, and manages exits.
100% Virtual Ledger (₹0 Real Money at Risk).
"""

from typing import Dict, Any, Optional
import config
import database
import telegram_notifier

class PaperTradingEngine:
    def __init__(self):
        self.active_trade = database.get_active_trade()
        self.milestone_reached = 0

    def sync_state(self):
        self.active_trade = database.get_active_trade()

    def process_entry(self, trade_params: Dict[str, Any], oi_advisory: str = "") -> int:
        """Executes a virtual trade entry and sends Telegram notification."""
        trade_id = database.record_trade_entry(trade_params)
        self.active_trade = database.get_active_trade()
        self.milestone_reached = 0

        # Broker Gateway Hook (Audited with ₹0 risk Canary Interception)
        try:
            import dhan_connector
            trade_params["trade_id"] = trade_id
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

        gain = current_price - entry
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
                return {"status": "CLOSED", "reason": "45MIN_THETA_STAGNATION_EXIT", "pnl_rupees": pnl_rupees}
        except Exception:
            pass

        # 1. Target Hit (Selling at Bid - Spread Friction)
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
            return {"status": "CLOSED", "reason": "TARGET_HIT", "pnl_rupees": pnl_rupees}

        # 2. Stop-Loss Hit (Selling at Bid - Spread Friction)
        if current_price <= sl:
            bid_exit = round(sl - config.BID_ASK_FRICTION_POINTS, 2)
            pnl_pts = round(bid_exit - entry, 2)
            pnl_rupees = round(pnl_pts * qty, 2)
            if sl > entry:
                reason = "RUNNER_TRAILING_HIT" if is_runner else "TRAILING_SL_HIT"
            else:
                reason = "MICRO_SL_HIT"
            database.record_trade_exit(trade_id, bid_exit, reason, pnl_pts, pnl_rupees)
            self._check_and_lock_daily_limits(reason)
            telegram_notifier.notify_exit(symbol, bid_exit, reason, pnl_pts, pnl_rupees)
            self.active_trade = None
            return {"status": "CLOSED", "reason": reason, "pnl_rupees": pnl_rupees}

        # 3. Tiered Milestone Trailing Protection (Rule 9 & Dynamic Greeks Model)
        initial_sl_dist = max(5.0, abs(entry - float(sl)))
        m1_trigger = round(initial_sl_dist * 1.0, 1)  # 1:1 RR Cost Lock (e.g. +14 pts on ATM, +7.6 pts on OTM)
        m2_trigger = round(initial_sl_dist * 1.5, 1)  # 1:1.5 RR Profit Lock
        m3_trigger = round(initial_sl_dist * 2.2, 1)  # 1:2.2 RR Big Breakout Lock

        # Milestone 1: At 1:1 RR gain -> Move SL to Cost
        if gain >= m1_trigger and self.milestone_reached < 1:
            new_sl = entry
            self._update_sl(trade_id, new_sl)
            self.milestone_reached = 1
            tag = f"+{gain:.1f} pts (Cost Lock - Runner Active)" if is_runner else f"+{gain:.1f} pts (Cost Lock @ ₹{entry:.2f})"
            telegram_notifier.notify_trailing_update(symbol, tag, new_sl, 0.0)

        # Milestone 2: At 1.5x RR gain -> Lock profit
        elif gain >= m2_trigger and self.milestone_reached < 2:
            locked_pts = round(initial_sl_dist * 0.75, 1)
            new_sl = round(entry + locked_pts, 2)
            self._update_sl(trade_id, new_sl)
            self.milestone_reached = 2
            tag = f"🏃 RUNNER: Target Approaching! Locked +{locked_pts} pts profit!" if is_runner else f"+{gain:.1f} pts (Lock +{locked_pts} pts @ ₹{new_sl:.2f})"
            telegram_notifier.notify_trailing_update(symbol, tag, new_sl, locked_pts * qty)

        # Milestone 3: At 2.2x RR gain -> Lock major profit
        elif gain >= m3_trigger and self.milestone_reached < 3:
            locked_pts = round(initial_sl_dist * 1.5, 1)
            new_sl = round(entry + locked_pts, 2)
            self._update_sl(trade_id, new_sl)
            self.milestone_reached = 3
            tag = f"🚀 RUNNER: Giant Move! Locked +{locked_pts} pts profit!" if is_runner else f"+{gain:.1f} pts (Lock +{locked_pts} pts @ ₹{new_sl:.2f})"
            telegram_notifier.notify_trailing_update(symbol, tag, new_sl, locked_pts * qty)

        # Milestone 4+: Infinite Ratchet Trailing for Confluence Trend Runners
        elif is_runner and gain > 35.0:
            offset = getattr(config, "RUNNER_TRAIL_OFFSET_POINTS", 15.0)
            dynamic_sl = round(current_price - offset, 2)
            if dynamic_sl > (sl + 2.0): # Step up whenever price moves 2+ pts higher
                self._update_sl(trade_id, dynamic_sl)
                telegram_notifier.notify_trailing_update(
                    symbol, 
                    f"🌊 RUNNER SURFING: +{gain:.1f} pts! Trailing SL raised", 
                    dynamic_sl, 
                    (dynamic_sl - entry) * qty
                )

        return {"status": "ACTIVE", "current_price": current_price, "sl": sl, "gain": gain}

    def _update_sl(self, trade_id: int, new_sl: float):
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE trades SET sl_price = ? WHERE id = ?", (new_sl, trade_id))
        conn.commit()
        conn.close()

    def force_exit(self, exit_price: float, reason: str = "MANUAL_EXIT") -> Dict[str, Any]:
        """Force exits active trade."""
        self.sync_state()
        if not self.active_trade:
            return {"status": "NO_ACTIVE_TRADE"}
            
        trade = self.active_trade
        entry = float(trade["entry_price"])
        qty = int(trade["quantity"])
        pnl_pts = round(exit_price - entry, 2)
        pnl_rupees = round(pnl_pts * qty, 2)
        
        database.record_trade_exit(trade["id"], exit_price, reason, pnl_pts, pnl_rupees)
        self._check_and_lock_daily_limits(reason)
        telegram_notifier.notify_exit(trade["symbol"], exit_price, reason, pnl_pts, pnl_rupees)
        self.active_trade = None
        return {"status": "CLOSED", "reason": reason, "pnl_rupees": pnl_rupees}

# Global singleton engine
engine = PaperTradingEngine()
