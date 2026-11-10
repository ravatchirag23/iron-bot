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
        
        telegram_notifier.notify_entry(
            symbol=trade_params["symbol"],
            signal_type=trade_params["signal_type"],
            entry_price=trade_params["entry_price"],
            sl_price=trade_params["sl_price"],
            target_price=trade_params["target_price"],
            quantity=trade_params["quantity"],
            oi_advisory=oi_advisory
        )
        return trade_id

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

        # 1. Target Hit
        if current_price >= target:
            pnl_pts = round(target - entry, 2)
            pnl_rupees = round(pnl_pts * qty, 2)
            database.record_trade_exit(trade_id, target, "TARGET_HIT", pnl_pts, pnl_rupees)
            database.lock_system_today("Target hit (+20 pts). Daily profit locked!")
            telegram_notifier.notify_exit(symbol, target, "TARGET_HIT", pnl_pts, pnl_rupees)
            self.active_trade = None
            return {"status": "CLOSED", "reason": "TARGET_HIT", "pnl_rupees": pnl_rupees}

        # 2. Stop-Loss Hit
        if current_price <= sl:
            pnl_pts = round(sl - entry, 2)
            pnl_rupees = round(pnl_pts * qty, 2)
            reason = "TRAILING_SL_HIT" if sl >= entry else "MICRO_SL_HIT"
            database.record_trade_exit(trade_id, sl, reason, pnl_pts, pnl_rupees)
            database.lock_system_today(f"{reason} reached. Strict 1-trade shutdown!")
            telegram_notifier.notify_exit(symbol, sl, reason, pnl_pts, pnl_rupees)
            self.active_trade = None
            return {"status": "CLOSED", "reason": reason, "pnl_rupees": pnl_rupees}

        # 3. Tiered Milestone Trailing Protection (Rule 9)
        # Milestone 1: At +12 pts -> Move SL to Cost
        if gain >= config.MILESTONE_1_TRIGGER and self.milestone_reached < 1:
            new_sl = entry
            self._update_sl(trade_id, new_sl)
            self.milestone_reached = 1
            telegram_notifier.notify_trailing_update(symbol, "+12 pts (Cost Lock)", new_sl, 0.0)

        # Milestone 2: At +22 pts -> Lock +10 pts
        elif gain >= config.MILESTONE_2_TRIGGER and self.milestone_reached < 2:
            new_sl = entry + 10.0
            self._update_sl(trade_id, new_sl)
            self.milestone_reached = 2
            telegram_notifier.notify_trailing_update(symbol, "+22 pts (Lock +10 pts)", new_sl, 10.0 * qty)

        # Milestone 3: At +35 pts -> Lock +22 pts
        elif gain >= config.MILESTONE_3_TRIGGER and self.milestone_reached < 3:
            new_sl = entry + 22.0
            self._update_sl(trade_id, new_sl)
            self.milestone_reached = 3
            telegram_notifier.notify_trailing_update(symbol, "+35 pts (Lock +22 pts)", new_sl, 22.0 * qty)

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
        database.lock_system_today(f"Trade closed ({reason})")
        telegram_notifier.notify_exit(trade["symbol"], exit_price, reason, pnl_pts, pnl_rupees)
        self.active_trade = None
        return {"status": "CLOSED", "reason": reason, "pnl_rupees": pnl_rupees}

# Global singleton engine
engine = PaperTradingEngine()
