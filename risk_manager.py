"""
Risk Manager & 13 Iron Armor Rules Engine
Validates every incoming webhook before any order can be processed.
"""

from datetime import datetime, time
from typing import Tuple, Dict, Any, Optional
import config
import database

def get_atm_strike(spot_price: float, is_call: bool) -> int:
    """Rounds Nifty spot to nearest 50-strike ATM."""
    base_strike = round(spot_price / 50.0) * 50
    return int(base_strike)

def validate_signal_armor(signal_data: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Runs all 13 Iron Armor Rules.
    Returns (True, "APPROVED") or (False, "REJECTION_REASON").
    """
    now = config.get_now_ist()
    current_time = now.time()
    
    # 1. System Lock Check
    stats = database.get_today_stats()
    if stats.get("is_locked", 0) == 1:
        return False, f"System is LOCKED for today ({stats.get('lock_reason', 'Limit reached')})"
        
    # 2. Daily Trade Count Limit (Rule 1)
    if stats.get("trade_count", 0) >= config.MAX_DAILY_TRADES:
        database.lock_system_today(f"Max daily trades limit ({config.MAX_DAILY_TRADES}) reached")
        return False, f"Max daily trade limit ({config.MAX_DAILY_TRADES}) reached. Zero overtrading!"
        
    # 3. Daily Loss Limit (Rule 2)
    if stats.get("total_pnl_rupees", 0.0) <= -config.MAX_DAILY_LOSS_RUPEES:
        database.lock_system_today(f"Daily loss limit (-₹{config.MAX_DAILY_LOSS_RUPEES}) hit")
        return False, f"Daily max loss (-₹{config.MAX_DAILY_LOSS_RUPEES}) hit. Capital protection active!"

    # 4. Active Trade Check (No overlapping / duplicate trades)
    active = database.get_active_trade()
    if active:
        return False, f"Already in active trade #{active['id']} on {active['symbol']}. No concurrent entries!"

    # 5. Market Hours Check (Rule 3)
    # Allows bypass if testing flag provided
    is_test = signal_data.get("is_test", False)
    if not is_test:
        if current_time < config.MARKET_START_TIME:
            return False, f"Market hours check: Too early ({current_time.strftime('%H:%M')}). Wait till 09:30 AM!"
            
        if current_time > config.MARKET_END_TIME:
            return False, f"Market hours check: Too late ({current_time.strftime('%H:%M')}). No entry after 14:45 PM!"

        # 6. European Open Blackout Window (Rule 5: 13:25 to 13:45)
        if config.EURO_BLACKOUT_START <= current_time <= config.EURO_BLACKOUT_END:
            return False, "European Market Open Whiplash Window (13:25-13:45). Entry blocked to avoid trap!"

    # 7. Debounce / Duplicate Webhook Check (Rule 6)
    signal_type = signal_data.get("signal", "").upper()
    if not is_test and database.is_duplicate_alert(signal_type, config.DEBOUNCE_WINDOW_SECONDS):
        return False, f"Duplicate signal detected within {config.DEBOUNCE_WINDOW_SECONDS}s window. Debounce active!"

    # 8. Exhaustion Giant Candle Filter (Rule 10)
    candle_range = signal_data.get("candle_range", 0.0)
    if candle_range > config.MAX_CANDLE_RANGE_POINTS:
        return False, f"Exhaustion candle detected ({candle_range:.1f} pts > {config.MAX_CANDLE_RANGE_POINTS} pts max). Do not buy top!"

    return True, "APPROVED"

def calculate_trade_parameters(signal_data: Dict[str, Any]) -> Dict[str, Any]:
    """Calculates ATM Strike, Micro-SL, Target, and Quantity."""
    spot = float(signal_data.get("spot", 25050.0))
    raw_signal = signal_data.get("signal", "BUY_CE").upper()
    
    is_call = "CE" in raw_signal or "BUY" in raw_signal
    strike = get_atm_strike(spot, is_call)
    symbol = f"NIFTY{strike}{'CE' if is_call else 'PE'}"
    
    # Premium calculation: Fetches real live option tick LTP from NSE feed
    entry_premium = None
    if "entry_price" in signal_data:
        entry_premium = float(signal_data["entry_price"])
    else:
        try:
            import option_chain_shield
            quote = option_chain_shield.get_real_option_quote(spot, is_call)
            if quote.get("ltp", 0.0) > 0.0:
                entry_premium = float(quote["ltp"])
                strike = int(quote["strike"])
                expiry_str = quote.get("expiry", "")
                symbol = f"NIFTY{strike}{'CE' if is_call else 'PE'} ({expiry_str})"
        except Exception:
            entry_premium = None

    if not entry_premium:
        entry_premium = config.DEFAULT_ASSUMED_PREMIUM
    
    # Micro-SL: Uses candle_low if available and reasonable, else fixed micro-SL
    candle_low = signal_data.get("candle_low")
    if candle_low and (entry_premium - float(candle_low)) <= 10.0 and (entry_premium - float(candle_low)) >= 4.0:
        sl_price = round(float(candle_low), 2)
    else:
        sl_price = round(entry_premium - config.DEFAULT_MICRO_SL_POINTS, 2)
        
    target_price = round(entry_premium + config.DEFAULT_TARGET_POINTS, 2)
    
    return {
        "signal_type": "BUY_CE" if is_call else "SELL_PE",
        "symbol": symbol,
        "spot_price": spot,
        "entry_price": entry_premium,
        "sl_price": sl_price,
        "target_price": target_price,
        "quantity": config.DEFAULT_LOT_SIZE
    }
