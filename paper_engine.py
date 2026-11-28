"""
Paper Trading Engine for Iron Bot
Simulates order fills, tracks active position, moves trailing SL, and manages exits.
100% Virtual Ledger (₹0 Real Money at Risk).
"""

import threading
import logging
import re
import sqlite3
from typing import Dict, Any, Optional
from datetime import datetime
import config
import database
import telegram_notifier

logger = logging.getLogger("IronBot.PaperEngine")


def _derive_milestone(trade: Optional[Dict[str, Any]]) -> int:
    """Reconstructs highest reached trailing milestone from DB trade state after restart."""
    if not trade:
        return 0
    entry = float(trade.get("entry_price") or 0.0)
    sl = float(trade.get("sl_price") or 0.0)
    if entry <= 0.0 or sl <= 0.0:
        return 0
    soft_buffer = float(getattr(config, "SOFT_SL_BUFFER_POINTS", 6.0))
    if bool(trade.get("is_runner", 0)) and sl >= round(entry + 8.0, 2) - 1e-6:
        return 3
    if sl >= entry - 1e-6:
        return 2
    init_sl = float(trade.get("initial_sl_price") or 0.0)
    if sl >= round(entry - soft_buffer, 2) - 1e-6 and (init_sl <= 0.0 or sl > init_sl):
        return 1
    return 0


class PaperTradingEngine:
    def __init__(self):
        self.lock = threading.RLock()
        self.active_trade = database.get_active_trade()
        self.milestone_reached = _derive_milestone(self.active_trade)
        self.initial_sl_dist = 0.0
        if self.active_trade:
            entry = float(self.active_trade.get("entry_price", 0.0))
            base_sl = float(self.active_trade.get("initial_sl_price") or self.active_trade.get("sl_price", 0.0))
            self.initial_sl_dist = max(5.0, abs(entry - base_sl))

    def sync_state(self):
        with self.lock:
            prev_id = self.active_trade.get("id") if self.active_trade else None
            self.active_trade = database.get_active_trade()
            if not self.active_trade:
                self.initial_sl_dist = 0.0
                self.milestone_reached = 0
                return
            new_id = self.active_trade.get("id")
            if new_id != prev_id or self.initial_sl_dist <= 0.0:
                entry = float(self.active_trade.get("entry_price", 0.0))
                base_sl = float(self.active_trade.get("initial_sl_price") or self.active_trade.get("sl_price", 0.0))
                self.initial_sl_dist = max(5.0, abs(entry - base_sl))
            if new_id != prev_id or self.milestone_reached == 0:
                self.milestone_reached = max(self.milestone_reached, _derive_milestone(self.active_trade))

    def process_entry(self, trade_params: Dict[str, Any], oi_advisory: str = "") -> int:
        """Executes a virtual trade entry atomically and sends Telegram notification."""
        with self.lock:
            entry_px = float(trade_params["entry_price"])
            init_sl = float(trade_params["sl_price"])
            trade_params["initial_sl_price"] = init_sl
            self.initial_sl_dist = max(5.0, abs(entry_px - init_sl))

            try:
                trade_id = database.record_trade_entry(trade_params)
            except sqlite3.IntegrityError as e:
                logger.warning(f"Blocked duplicate concurrent trade entry: {e}")
                self.sync_state()
                return -1

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
        max_trades = getattr(config, "get_max_daily_trades", lambda: config.MAX_DAILY_TRADES)()
        max_loss = getattr(config, "get_max_daily_loss", lambda: config.MAX_DAILY_LOSS_RUPEES)()
        if stats.get("trade_count", 0) >= max_trades:
            database.lock_system_today(f"Max daily trades limit ({max_trades}) reached")
        elif stats.get("total_pnl_rupees", 0.0) <= -max_loss:
            database.lock_system_today(f"Daily loss limit (-₹{max_loss}) hit")

    def update_tick(self, current_price: float) -> Optional[Dict[str, Any]]:
        """
        Processes price updates for the active trade.
        Checks:
        1. Target Hit
        2. Micro-SL Hit
        3. Tiered Trailing Profit Locks
        """
        with self.lock:
            # Reject garbage, zero, or negative ticks from disconnected brokers/glitched websockets
            if current_price is None or current_price <= 0.0:
                logger.warning(f"Rejected invalid/zero tick price: {current_price}")
                return None

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

            # 1. Target Hit & Runner Trailing Extension (supports multi-step gap ratchets)
            is_runner = bool(trade.get("is_runner", 0))
            universal_trailing = getattr(config, "get_enable_universal_trailing", lambda: getattr(config, "ENABLE_UNIVERSAL_TRAILING", False))()

            if current_price >= target:
                if is_runner or universal_trailing:
                    first_old_target = target
                    steps = 0
                    while current_price >= target and steps < 20:
                        new_target = round(target + 10.0, 2)
                        new_sl = round(max(sl, target - 3.0), 2)
                        sl = new_sl
                        target = new_target
                        steps += 1
                    self._update_target(trade_id, target)
                    self._update_sl(trade_id, sl)
                    if not is_runner and universal_trailing:
                        with database.db_session() as conn:
                            cursor = conn.cursor()
                            cursor.execute("UPDATE trades SET is_runner = 1 WHERE id = ?", (trade_id,))
                        trade["is_runner"] = 1
                        is_runner = True
                    trade["target_price"] = target
                    trade["sl_price"] = sl
                    self.milestone_reached = max(self.milestone_reached, 3)
                    locked_pts = round(sl - entry, 1)
                    prefix = "🚀 UNIVERSAL TRAIL" if universal_trailing and steps == 1 else "🚀 RUNNER EXTENSION"
                    tag = f"{prefix}: Hit ₹{first_old_target:.2f}! Trailing Target extended to ₹{target:.2f} (SL locked @ ₹{sl:.2f} | +{locked_pts} pts profit)"
                    telegram_notifier.notify_trailing_update(symbol, tag, sl, locked_pts * qty)
                else:
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

            # 2. Stop-Loss Hit (Realistic Gap-Through Fill: clamped to realistic market slippage)
            if current_price <= sl:
                # Sanity Guardrail: Maximum realistic market slippage on Stop-Loss is 15.0 pts.
                # Allows realistic market slippage (3-5 pts) while blocking upstream API glitch crashes (e.g. 80-pt phantom drops)
                max_slip = getattr(config, "MAX_SLIPPAGE_POINTS", 15.0)
                clamped_price = max(sl - max_slip, current_price)
                if current_price < (sl - max_slip):
                    logger.warning(
                        f"SL gap slippage clamped for {symbol}: tick ₹{current_price:.2f} -> ₹{clamped_price:.2f} (SL=₹{sl:.2f}, max_slip={max_slip})"
                    )
                actual_exit = min(clamped_price, sl)
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

            # 3. Sequential Non-Starving Trailing Protection (Milestones 1 -> 2 -> 3 -> 4)
            soft_trigger = getattr(config, "SOFT_SL_TRIGGER_POINTS", 7.5)
            soft_buffer = getattr(config, "SOFT_SL_BUFFER_POINTS", 6.0)
            cost_trigger = getattr(config, "COST_LOCK_TRIGGER_POINTS", 11.0)

            # Milestone 1: Soft Risk Buffer (At +7.5 pts gain -> Tighten SL to Entry - 6.0 pts)
            if gain >= soft_trigger and self.milestone_reached < 1:
                self.milestone_reached = 1
                new_sl = round(entry - soft_buffer, 2)
                if gain < cost_trigger and new_sl > sl:
                    self._update_sl(trade_id, new_sl)
                    trade["sl_price"] = new_sl
                    sl = new_sl
                    tag = f"🛡️ SOFT RISK BUFFER: +{gain:.1f} pts gain! SL moved to ₹{new_sl:.2f} (Risk slashed 60%, 6 pt breathing room preserved)"
                    telegram_notifier.notify_trailing_update(symbol, tag, new_sl, 0.0)

            # Milestone 2: True Cost Lock (At +11.0 pts gain -> Move SL to Cost)
            if gain >= cost_trigger and self.milestone_reached < 2:
                self.milestone_reached = 2
                new_sl = entry
                if new_sl > sl and not (is_runner and gain >= 18.0):
                    self._update_sl(trade_id, new_sl)
                    trade["sl_price"] = new_sl
                    sl = new_sl
                    tag = f"+{gain:.1f} pts (Cost Lock - Runner Active)" if is_runner else f"🔒 COST LOCK: +{gain:.1f} pts gain! SL locked at ₹{new_sl:.2f} (Risk-Free Ride)"
                    telegram_notifier.notify_trailing_update(symbol, tag, new_sl, 0.0)

            # Milestone 3: Profit Lock for Confluence Trend Runners (At +18 pts -> Lock +8 pts)
            if is_runner and gain >= 18.0 and self.milestone_reached < 3:
                self.milestone_reached = 3
                locked_pts = 8.0
                new_sl = round(entry + locked_pts, 2)
                if new_sl > sl:
                    self._update_sl(trade_id, new_sl)
                    trade["sl_price"] = new_sl
                    sl = new_sl
                    tag = f"🏃 RUNNER: Giant Momentum! Locked +{locked_pts} pts profit @ ₹{new_sl:.2f}"
                    telegram_notifier.notify_trailing_update(symbol, tag, new_sl, locked_pts * qty)

            # Milestone 4+: Ratchet Trailing for Mega Trend Runners (Gain >= 20.0 pts)
            if is_runner and gain >= 20.0:
                offset = getattr(config, "RUNNER_TRAIL_OFFSET_POINTS", 10.0)
                dynamic_sl = round(current_price - offset, 2)
                if dynamic_sl > (sl + 2.0): # Step up whenever price moves 2+ pts higher
                    self._update_sl(trade_id, dynamic_sl)
                    trade["sl_price"] = dynamic_sl
                    sl = dynamic_sl
                    locked_gain = round(dynamic_sl - entry, 1)
                    telegram_notifier.notify_trailing_update(
                        symbol,
                        f"🌊 RUNNER SURFING: +{gain:.1f} pts! Trailing SL raised to ₹{dynamic_sl:.2f} (Locking +{locked_gain} pts)",
                        dynamic_sl,
                        locked_gain * qty
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
        is_call = not bool(re.search(r"\d+PE|(?:^|[^A-Z])(PE|PUT)(?:$|[^A-Z])", symbol.upper()))
        
        # Extract held strike
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

    def force_exit(self, exit_price: Optional[float] = None, reason: str = "MANUAL_EXIT", current_spot: Optional[float] = None) -> Dict[str, Any]:
        """Force exits active trade with automatic spot-vs-option sanity validation."""
        with self.lock:
            self.sync_state()
            if not self.active_trade:
                return {"status": "NO_ACTIVE_TRADE"}
                
            trade = self.active_trade
            entry = float(trade["entry_price"])
            qty = int(trade["quantity"])

            # Sanity Guardrail: If exit_price is accidentally passed as spot price (> 5000),
            # non-positive (<= 0), or omitted, automatically resolve real option quote or delta estimate!
            if exit_price is None or exit_price > 5000.0 or exit_price <= 0.0:
                fallback_spot = current_spot if current_spot is not None else exit_price
                exit_price = self._resolve_option_exit_price(trade, fallback_spot=fallback_spot)
                
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
